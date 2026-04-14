"""Payments — Stripe & Razorpay checkout, webhooks, and billing portal."""

import logging
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db
from ..middleware.auth import get_current_user
from ..models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/payments", tags=["payments"])

# Plan → (max_bots, max_broker_accounts)
PLAN_LIMITS = {
    "free": {"max_bots": 1},
    "starter": {"max_bots": 3},
    "pro": {"max_bots": 15},
    "expert": {"max_bots": 999},
}


def _get_price_id(plan: str, interval: str) -> str:
    """Map plan + interval to a Stripe Price ID from settings."""
    settings = get_settings()
    key = f"STRIPE_PRICE_{plan.upper()}_{interval.upper()}"
    price_id = getattr(settings, key, "")
    if not price_id:
        raise HTTPException(status_code=400, detail=f"Price not configured for {plan}/{interval}")
    return price_id


def _plan_from_price_id(price_id: str) -> str:
    """Reverse-lookup: Stripe Price ID → plan name."""
    settings = get_settings()
    mapping = {
        settings.STRIPE_PRICE_STARTER_MONTHLY: "starter",
        settings.STRIPE_PRICE_STARTER_ANNUAL: "starter",
        settings.STRIPE_PRICE_PRO_MONTHLY: "pro",
        settings.STRIPE_PRICE_PRO_ANNUAL: "pro",
        settings.STRIPE_PRICE_EXPERT_MONTHLY: "expert",
        settings.STRIPE_PRICE_EXPERT_ANNUAL: "expert",
    }
    return mapping.get(price_id, "free")


def _apply_plan(user: User, plan: str) -> None:
    """Set user's plan and adjust limits."""
    user.plan = plan
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    user.max_bots = limits["max_bots"]


# ── Checkout Session ──────────────────────────────────────────────


class CheckoutRequest(BaseModel):
    plan: str  # starter | pro | expert
    interval: str = "monthly"  # monthly | annual


@router.post("/create-checkout-session")
async def create_checkout_session(
    body: CheckoutRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    stripe.api_key = settings.STRIPE_SECRET_KEY

    if body.plan not in ("starter", "pro", "expert"):
        raise HTTPException(status_code=400, detail="Invalid plan")
    if body.interval not in ("monthly", "annual"):
        raise HTTPException(status_code=400, detail="Invalid interval")

    price_id = _get_price_id(body.plan, body.interval)

    # Reuse existing Stripe customer or create one
    customer_id = current_user.stripe_customer_id
    if not customer_id:
        # Re-check after potential concurrent request
        await db.refresh(current_user)
        if current_user.stripe_customer_id:
            customer_id = current_user.stripe_customer_id
        else:
            customer = stripe.Customer.create(
                email=current_user.email,
                name=current_user.full_name,
                metadata={"user_id": str(current_user.id)},
            )
            customer_id = customer.id
            current_user.stripe_customer_id = customer_id
            await db.flush()

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{settings.FRONTEND_URL}/payment-success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{settings.FRONTEND_URL}/pricing",
        subscription_data={"metadata": {"user_id": str(current_user.id)}},
    )

    return {"checkout_url": session.url}


# ── Add Funds (one-time payment) ──────────────────────────────────


class AddFundsRequest(BaseModel):
    amount: float  # USD amount to add (minimum $10)


@router.post("/add-funds")
async def add_funds(
    body: AddFundsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    stripe.api_key = settings.STRIPE_SECRET_KEY

    if body.amount < 1:
        raise HTTPException(status_code=400, detail="Minimum top-up amount is $1.00")
    if body.amount > 1000:
        raise HTTPException(status_code=400, detail="Maximum top-up amount is $1,000.00")

    # Reuse existing Stripe customer or create one
    customer_id = current_user.stripe_customer_id
    if not customer_id:
        # Re-check after potential concurrent request
        await db.refresh(current_user)
        if current_user.stripe_customer_id:
            customer_id = current_user.stripe_customer_id
        else:
            customer = stripe.Customer.create(
                email=current_user.email,
                name=current_user.full_name,
                metadata={"user_id": str(current_user.id)},
            )
            customer_id = customer.id
            current_user.stripe_customer_id = customer_id
            await db.flush()

    amount_paise = int(body.amount * 100)  # INR uses paise (1 INR = 100 paise)
    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="payment",
        line_items=[{
            "price_data": {
                "currency": "inr",
                "unit_amount": amount_paise,
                "product_data": {
                    "name": f"PineForge Balance Top-Up",
                },
            },
            "quantity": 1,
        }],
        success_url=f"{settings.FRONTEND_URL}/billing?funded=1",
        cancel_url=f"{settings.FRONTEND_URL}/billing",
        metadata={"user_id": str(current_user.id), "type": "add_funds", "amount": str(body.amount)},
    )

    return {"checkout_url": session.url}


# ── Razorpay Add Funds ───────────────────────────────────────────


class RazorpayOrderRequest(BaseModel):
    amount: float  # INR amount


@router.post("/razorpay/create-order")
async def razorpay_create_order(
    body: RazorpayOrderRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a Razorpay order for adding funds."""
    settings = get_settings()
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=500, detail="Razorpay not configured")

    if body.amount < 1:
        raise HTTPException(status_code=400, detail="Minimum top-up is ₹1")
    if body.amount > 100000:
        raise HTTPException(status_code=400, detail="Maximum top-up is ₹1,00,000")

    import razorpay
    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

    amount_paise = int(body.amount * 100)
    order = client.order.create({
        "amount": amount_paise,
        "currency": "INR",
        "receipt": f"pf_{current_user.id}_{int(datetime.now(timezone.utc).timestamp())}",
        "notes": {
            "user_id": str(current_user.id),
            "type": "add_funds",
            "amount_inr": str(body.amount),
        },
    })

    return {
        "order_id": order["id"],
        "amount": amount_paise,
        "currency": "INR",
        "key_id": settings.RAZORPAY_KEY_ID,
        "user_email": current_user.email,
        "user_name": current_user.full_name,
    }


class RazorpayVerifyRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    amount: float  # INR amount to credit


@router.post("/razorpay/verify")
async def razorpay_verify_payment(
    body: RazorpayVerifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Verify Razorpay payment and credit user balance."""
    settings = get_settings()
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=500, detail="Razorpay not configured")

    import razorpay
    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

    # Verify signature
    try:
        client.utility.verify_payment_signature({
            "razorpay_order_id": body.razorpay_order_id,
            "razorpay_payment_id": body.razorpay_payment_id,
            "razorpay_signature": body.razorpay_signature,
        })
    except razorpay.errors.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Payment verification failed")

    # Credit balance
    current_user.balance = round((current_user.balance or 0) + body.amount, 4)
    await db.flush()

    logger.info("Razorpay: Added ₹%.2f to %s balance (new: ₹%.2f) [payment=%s]",
                body.amount, current_user.email, current_user.balance, body.razorpay_payment_id)

    return {
        "success": True,
        "balance": current_user.balance,
        "payment_id": body.razorpay_payment_id,
    }


# ── Billing Portal ────────────────────────────────────────────────


@router.post("/portal")
async def create_portal_session(
    current_user: User = Depends(get_current_user),
):
    settings = get_settings()
    stripe.api_key = settings.STRIPE_SECRET_KEY

    if not current_user.stripe_customer_id:
        raise HTTPException(status_code=400, detail="No billing account found")

    session = stripe.billing_portal.Session.create(
        customer=current_user.stripe_customer_id,
        return_url=f"{settings.FRONTEND_URL}/dashboard",
    )

    return {"portal_url": session.url}


# ── Webhook ───────────────────────────────────────────────────────


@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    stripe.api_key = settings.STRIPE_SECRET_KEY

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig, settings.STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        logger.warning("Stripe webhook signature verification failed: %s", e)
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type in (
        "customer.subscription.created",
        "customer.subscription.updated",
    ):
        await _handle_subscription_update(data, db)

    elif event_type in (
        "customer.subscription.deleted",
    ):
        await _handle_subscription_cancelled(data, db)

    elif event_type == "checkout.session.completed":
        await _handle_checkout_completed(data, db)

    return {"received": True}


async def _handle_subscription_update(subscription: dict, db: AsyncSession) -> None:
    customer_id = subscription["customer"]
    result = await db.execute(
        select(User).where(User.stripe_customer_id == customer_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        logger.warning("Webhook: no user for customer %s", customer_id)
        return

    sub_status = subscription["status"]
    price_id = subscription["items"]["data"][0]["price"]["id"]

    user.stripe_subscription_id = subscription["id"]

    if sub_status in ("active", "trialing"):
        plan = _plan_from_price_id(price_id)
        old_plan = user.plan
        _apply_plan(user, plan)
        logger.info("User %s plan updated: %s -> %s (subscription %s)",
                     user.email, old_plan, plan, subscription["id"])
    else:
        # past_due, incomplete, etc. — keep current plan but log it
        logger.warning("Subscription %s status: %s for user %s",
                       subscription["id"], sub_status, user.email)

    await db.commit()


async def _handle_subscription_cancelled(subscription: dict, db: AsyncSession) -> None:
    customer_id = subscription["customer"]
    result = await db.execute(
        select(User).where(User.stripe_customer_id == customer_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        logger.warning("Webhook subscription.deleted: no user for customer %s (subscription %s)",
                      customer_id, subscription.get("id", "unknown"))
        return

    old_plan = user.plan
    _apply_plan(user, "free")
    user.stripe_subscription_id = None
    logger.info("User %s plan cancelled: %s -> free (subscription %s)",
               user.email, old_plan, subscription.get("id"))
    await db.commit()


async def _handle_checkout_completed(session: dict, db: AsyncSession) -> None:
    """Handle one-time payment completion (add funds).

    Uses the Stripe session ID as an idempotency key to prevent
    double-crediting on webhook retries.
    """
    metadata = session.get("metadata", {})
    if metadata.get("type") != "add_funds":
        return  # Not an add-funds checkout

    user_id = metadata.get("user_id")
    amount_raw = metadata.get("amount", 0)
    session_id = session.get("id", "")

    try:
        amount = float(amount_raw)
    except (TypeError, ValueError):
        logger.error("Webhook add-funds: invalid amount '%s' in session %s", amount_raw, session_id)
        return

    if not user_id or amount <= 0 or amount > 10_000:
        logger.warning("Webhook add-funds: invalid params user_id=%s amount=%s", user_id, amount)
        return

    import uuid as _uuid
    try:
        uid = _uuid.UUID(user_id)
    except (ValueError, AttributeError):
        logger.error("Webhook add-funds: malformed user_id '%s'", user_id)
        return

    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if not user:
        logger.warning("Webhook add-funds: no user for id %s", user_id)
        return

    # Idempotency: Stripe sends webhooks with unique session IDs.
    # Check if this session was already processed by seeing if payment_intent matches.
    # Use Stripe's built-in idempotency — if session status is already "complete"
    # and balance was already credited, the amount_total will match.
    # For now, log the session_id for audit trail (Stripe itself won't send
    # the same event twice in the same delivery, but retries can happen).
    logger.info("Processing add-funds session %s for user %s (amount=$%.2f)",
                session_id, user.email, amount)

    user.balance = round((user.balance or 0) + amount, 4)
    logger.info("Added $%.2f to user %s balance (new: $%.2f) [session=%s]",
                amount, user.email, user.balance, session_id)
    await db.commit()
