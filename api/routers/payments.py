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
from ..models.transaction import Transaction
from ..models.user import User
from ..services.transaction_service import record_transaction

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
    amount: float
    currency: str = "INR"  # INR (via Stripe domestic) or USD


@router.post("/add-funds")
async def add_funds(
    body: AddFundsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    stripe.api_key = settings.STRIPE_SECRET_KEY

    currency = body.currency.upper()
    if currency not in ("INR", "USD"):
        raise HTTPException(status_code=400, detail="Currency must be INR or USD")
    if body.amount < 1:
        raise HTTPException(status_code=400, detail=f"Minimum top-up is {'₹' if currency == 'INR' else '$'}1")
    max_amount = 100000 if currency == "INR" else 1000
    if body.amount > max_amount:
        raise HTTPException(status_code=400, detail=f"Maximum top-up is {'₹1,00,000' if currency == 'INR' else '$1,000'}")

    # Compute USD credit amount (wallet balance is always USD)
    if currency == "INR":
        rate = await _get_inr_to_usd_rate()
        usd_credit = round(body.amount * rate, 4)
    else:
        usd_credit = round(body.amount, 4)

    # Reuse existing Stripe customer or create one
    customer_id = current_user.stripe_customer_id
    if not customer_id:
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

    amount_smallest = int(body.amount * 100)
    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="payment",
        line_items=[{
            "price_data": {
                "currency": currency.lower(),
                "unit_amount": amount_smallest,
                "product_data": {
                    "name": f"PineForge Balance Top-Up (${usd_credit:.2f})",
                },
            },
            "quantity": 1,
        }],
        success_url=f"{settings.FRONTEND_URL}/billing?funded=1",
        cancel_url=f"{settings.FRONTEND_URL}/billing",
        metadata={
            "user_id": str(current_user.id),
            "type": "add_funds",
            "amount": str(body.amount),
            "currency": currency,
            "usd_credit": str(usd_credit),
        },
    )

    # Record pending transaction (balance unchanged, just audit trail)
    currency_sym = '₹' if currency == 'INR' else '$'
    await record_transaction(
        db, current_user, "deposit_pending", 0,
        f"Stripe checkout started: {currency_sym}{body.amount:.2f} → ${usd_credit:.4f}",
        reference_id=session.id,
    )
    await db.flush()

    return {"checkout_url": session.url, "usd_credit": usd_credit}


# ── Razorpay Add Funds ───────────────────────────────────────────


class RazorpayOrderRequest(BaseModel):
    amount: float
    currency: str = "INR"  # INR or USD


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

    currency = body.currency.upper()
    if currency not in ("INR", "USD"):
        raise HTTPException(status_code=400, detail="Currency must be INR or USD")

    if body.amount < 1:
        raise HTTPException(status_code=400, detail=f"Minimum top-up is {'₹' if currency == 'INR' else '$'}1")
    max_amount = 100000 if currency == "INR" else 1000
    if body.amount > max_amount:
        raise HTTPException(status_code=400, detail=f"Maximum top-up is {'₹1,00,000' if currency == 'INR' else '$1,000'}")

    import razorpay
    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

    amount_smallest = int(body.amount * 100)  # paise for INR, cents for USD
    order = client.order.create({
        "amount": amount_smallest,
        "currency": currency,
        "receipt": f"pf_{int(datetime.now(timezone.utc).timestamp())}",
        "notes": {
            "user_id": str(current_user.id),
            "type": "add_funds",
            "amount": str(body.amount),
            "currency": currency,
        },
    })

    return {
        "order_id": order["id"],
        "amount": amount_smallest,
        "currency": currency,
        "key_id": settings.RAZORPAY_KEY_ID,
        "user_email": current_user.email,
        "user_name": current_user.full_name,
    }


class RazorpayVerifyRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    amount: float  # paid amount in original currency
    currency: str = "INR"  # INR or USD


@router.post("/razorpay/verify")
async def razorpay_verify_payment(
    body: RazorpayVerifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Verify Razorpay payment and credit user balance (always in USD)."""
    settings = get_settings()
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=500, detail="Razorpay not configured")

    import razorpay
    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

    try:
        client.utility.verify_payment_signature({
            "razorpay_order_id": body.razorpay_order_id,
            "razorpay_payment_id": body.razorpay_payment_id,
            "razorpay_signature": body.razorpay_signature,
        })
    except razorpay.errors.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Payment verification failed")

    # Convert to USD if paid in INR (wallet balance is always in USD)
    currency = body.currency.upper()
    if currency == "INR":
        rate = await _get_inr_to_usd_rate()
        usd_credited = round(body.amount * rate, 4)
    else:
        usd_credited = round(body.amount, 4)

    current_user.balance = round((current_user.balance or 0) + usd_credited, 4)
    await record_transaction(
        db, current_user, "deposit", usd_credited,
        f"Razorpay {'₹' if currency == 'INR' else '$'}{body.amount:.2f} → ${usd_credited:.4f}",
        reference_id=body.razorpay_payment_id,
    )
    await db.flush()

    logger.info("Razorpay: Paid %s%.2f → credited $%.4f to %s (balance: $%.2f) [payment=%s]",
                '₹' if currency == 'INR' else '$', body.amount, usd_credited,
                current_user.email, current_user.balance, body.razorpay_payment_id)

    return {
        "success": True,
        "balance": current_user.balance,
        "credited_usd": usd_credited,
        "paid_amount": body.amount,
        "paid_currency": currency,
        "payment_id": body.razorpay_payment_id,
    }


# ── FX Rate ──────────────────────────────────────────────────────

_fx_cache = {"usd_per_inr": None, "fetched_at": 0}


async def _get_inr_to_usd_rate() -> float:
    """Get current INR→USD conversion rate, cached for 1 hour."""
    import time
    import httpx
    now = time.time()
    if _fx_cache["usd_per_inr"] and (now - _fx_cache["fetched_at"]) < 3600:
        return _fx_cache["usd_per_inr"]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://open.er-api.com/v6/latest/INR")
            resp.raise_for_status()
            data = resp.json()
            rate = float(data["rates"]["USD"])
            _fx_cache["usd_per_inr"] = rate
            _fx_cache["fetched_at"] = now
            return rate
    except Exception as e:
        logger.warning("FX rate fetch failed: %s — using fallback", e)
        return _fx_cache["usd_per_inr"] or 0.012  # ~83 INR per USD fallback


@router.get("/fx-rate")
async def get_fx_rate(current_user: User = Depends(get_current_user)):
    """Get current INR→USD conversion rate."""
    rate = await _get_inr_to_usd_rate()
    return {
        "inr_to_usd": rate,
        "usd_to_inr": round(1 / rate, 2) if rate else 0,
        "source": "open.er-api.com",
    }


# ── PayPal Add Funds ─────────────────────────────────────────────


def _paypal_base_url():
    settings = get_settings()
    return "https://api-m.sandbox.paypal.com" if settings.PAYPAL_MODE == "sandbox" else "https://api-m.paypal.com"


async def _paypal_get_token():
    """Get OAuth2 access token from PayPal."""
    settings = get_settings()
    if not settings.PAYPAL_CLIENT_ID or not settings.PAYPAL_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="PayPal not configured")

    import httpx
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{_paypal_base_url()}/v1/oauth2/token",
            auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials"},
        )
    resp.raise_for_status()
    return resp.json()["access_token"]


class PayPalOrderRequest(BaseModel):
    amount: float  # USD amount


@router.post("/paypal/create-order")
async def paypal_create_order(
    body: PayPalOrderRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a PayPal order for adding funds (USD only)."""
    if body.amount < 1:
        raise HTTPException(status_code=400, detail="Minimum top-up is $1.00")
    if body.amount > 1000:
        raise HTTPException(status_code=400, detail="Maximum top-up is $1,000.00")

    import httpx
    token = await _paypal_get_token()

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{_paypal_base_url()}/v2/checkout/orders",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "intent": "CAPTURE",
                "purchase_units": [{
                    "reference_id": f"pf_{int(datetime.now(timezone.utc).timestamp())}",
                    "description": "PineForge Balance Top-Up",
                    "amount": {
                        "currency_code": "USD",
                        "value": f"{body.amount:.2f}",
                    },
                    "custom_id": str(current_user.id),
                }],
            },
        )

    if resp.status_code >= 400:
        logger.error("PayPal create order failed: %s", resp.text)
        raise HTTPException(status_code=400, detail=f"PayPal error: {resp.text[:200]}")

    order = resp.json()

    # Record pending transaction
    await record_transaction(
        db, current_user, "deposit_pending", 0,
        f"PayPal checkout started: ${body.amount:.2f}",
        reference_id=order["id"],
    )
    await db.flush()

    return {
        "order_id": order["id"],
        "client_id": get_settings().PAYPAL_CLIENT_ID,
    }


class PayPalCaptureRequest(BaseModel):
    order_id: str
    amount: float  # Expected USD amount


@router.post("/paypal/capture")
async def paypal_capture(
    body: PayPalCaptureRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Capture an approved PayPal order and credit user balance."""
    import httpx
    token = await _paypal_get_token()

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{_paypal_base_url()}/v2/checkout/orders/{body.order_id}/capture",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={},
        )

    if resp.status_code >= 400:
        logger.error("PayPal capture failed: %s", resp.text)
        await _mark_paypal_failed(db, current_user, body.order_id, f"Capture HTTP {resp.status_code}")
        raise HTTPException(status_code=400, detail=f"PayPal capture failed: {resp.text[:200]}")

    capture = resp.json()
    if capture.get("status") != "COMPLETED":
        await _mark_paypal_failed(db, current_user, body.order_id, f"Status: {capture.get('status')}")
        raise HTTPException(status_code=400, detail=f"Payment not completed: {capture.get('status')}")

    # Verify amount matches
    pu = capture.get("purchase_units", [{}])[0]
    captures = pu.get("payments", {}).get("captures", [{}])
    captured_amount = float(captures[0].get("amount", {}).get("value", 0))
    captured_currency = captures[0].get("amount", {}).get("currency_code", "")

    if captured_currency != "USD" or abs(captured_amount - body.amount) > 0.01:
        await _mark_paypal_failed(db, current_user, body.order_id, "Amount mismatch")
        raise HTTPException(status_code=400, detail="Payment amount mismatch")

    # Mark pending as completed
    pending = (await db.execute(
        select(Transaction).where(
            Transaction.reference_id == body.order_id,
            Transaction.type == "deposit_pending",
        )
    )).scalar_one_or_none()
    if pending:
        pending.type = "deposit_completed"
        pending.description = f"PayPal payment successful: ${captured_amount:.2f}"

    # Credit balance
    current_user.balance = round((current_user.balance or 0) + captured_amount, 4)
    await record_transaction(
        db, current_user, "deposit", captured_amount,
        f"PayPal ${captured_amount:.2f}",
        reference_id=body.order_id,
    )
    await db.flush()

    logger.info("PayPal: Added $%.2f to %s balance (new: $%.2f) [order=%s]",
                captured_amount, current_user.email, current_user.balance, body.order_id)

    return {
        "success": True,
        "balance": current_user.balance,
        "order_id": body.order_id,
    }


async def _mark_paypal_failed(db: AsyncSession, user: User, order_id: str, reason: str):
    """Mark a pending PayPal transaction as failed."""
    pending = (await db.execute(
        select(Transaction).where(
            Transaction.reference_id == order_id,
            Transaction.type == "deposit_pending",
        )
    )).scalar_one_or_none()
    if pending:
        pending.type = "deposit_failed"
        pending.description += f" — {reason}"
    else:
        await record_transaction(
            db, user, "deposit_failed", 0,
            f"PayPal capture failed: {reason}",
            reference_id=order_id,
        )
    await db.flush()


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
    # Stripe SDK returns StripeObject, not plain dict — convert so .get() works
    if hasattr(data, "to_dict"):
        data = data.to_dict()

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

    elif event_type == "checkout.session.expired":
        await _handle_checkout_expired(data, db)

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
    paid_currency = metadata.get("currency", "USD").upper()
    usd_credit_raw = metadata.get("usd_credit", amount_raw)
    session_id = session.get("id", "")

    try:
        paid_amount = float(amount_raw)
        usd_credit = float(usd_credit_raw)
    except (TypeError, ValueError):
        logger.error("Webhook add-funds: invalid amount '%s' in session %s", amount_raw, session_id)
        return

    if not user_id or usd_credit <= 0 or usd_credit > 10_000:
        logger.warning("Webhook add-funds: invalid params user_id=%s usd_credit=%s", user_id, usd_credit)
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

    user.balance = round((user.balance or 0) + usd_credit, 4)
    currency_sym = '₹' if paid_currency == 'INR' else '$'

    # Mark any pending transaction as completed, then record the deposit
    pending = (await db.execute(
        select(Transaction).where(
            Transaction.reference_id == session_id,
            Transaction.type == "deposit_pending",
        )
    )).scalar_one_or_none()
    if pending:
        pending.type = "deposit_completed"
        pending.description = f"Stripe payment successful: {currency_sym}{paid_amount:.2f}"

    await record_transaction(
        db, user, "deposit", usd_credit,
        f"Stripe {currency_sym}{paid_amount:.2f} → ${usd_credit:.4f}",
        reference_id=session_id,
    )
    logger.info("Added $%.4f to %s balance (paid %s%.2f, new: $%.2f) [session=%s]",
                usd_credit, user.email, currency_sym,
                paid_amount, user.balance, session_id)
    await db.commit()


async def _handle_checkout_expired(session: dict, db: AsyncSession) -> None:
    """Handle expired/abandoned Stripe checkout sessions."""
    session_id = session.get("id", "")
    metadata = session.get("metadata", {})

    if metadata.get("type") != "add_funds":
        return

    # Mark the pending transaction as failed
    pending = (await db.execute(
        select(Transaction).where(
            Transaction.reference_id == session_id,
            Transaction.type == "deposit_pending",
        )
    )).scalar_one_or_none()

    if pending:
        pending.type = "deposit_failed"
        pending.description += " — expired/abandoned"
        logger.info("Stripe checkout expired: session=%s user_id=%s", session_id, metadata.get("user_id"))
        await db.commit()
    else:
        # No pending record (e.g. created before this feature) — create a failed record
        user_id = metadata.get("user_id")
        if user_id:
            import uuid as _uuid
            try:
                uid = _uuid.UUID(user_id)
            except (ValueError, AttributeError):
                return
            result = await db.execute(select(User).where(User.id == uid))
            user = result.scalar_one_or_none()
            if user:
                amount_raw = metadata.get("amount", "0")
                currency = metadata.get("currency", "USD").upper()
                currency_sym = '₹' if currency == 'INR' else '$'
                await record_transaction(
                    db, user, "deposit_failed", 0,
                    f"Stripe checkout expired: {currency_sym}{amount_raw}",
                    reference_id=session_id,
                )
                await db.commit()
