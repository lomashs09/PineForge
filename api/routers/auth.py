"""Authentication routes — register, login, refresh, profile, email verification."""

import html
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db
from ..middleware.auth import get_current_user
from ..models.user import User
from ..schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    ResendVerificationRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserResponse,
    VerifyEmailRequest,
)
from ..services.auth_service import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from ..services.email_service import EmailRateLimited, generate_verification_token, send_verification_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_MIN_PASSWORD_LENGTH = 8


def _normalize_email(email: str) -> str:
    """Lowercase and strip whitespace from email for consistent lookup."""
    return email.strip().lower()


def _validate_password_strength(password: str) -> None:
    """Enforce minimum password requirements."""
    if len(password) < _MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {_MIN_PASSWORD_LENGTH} characters.",
        )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    _validate_password_strength(body.password)

    normalized_email = _normalize_email(body.email)
    result = await db.execute(select(User).where(User.email == normalized_email))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="Email already registered")

    token = generate_verification_token()
    user = User(
        email=normalized_email,
        hashed_password=hash_password(body.password),
        full_name=html.escape(body.full_name.strip()),
        email_verification_token=token,
    )
    try:
        db.add(user)
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    await db.refresh(user)

    try:
        send_verification_email(normalized_email, body.full_name, token)
    except EmailRateLimited:
        pass  # User just registered — don't block registration over rate limit
    except Exception as e:
        logger.error("Failed to send verification email to %s: %s", normalized_email, e)

    logger.info("New user registered: %s", normalized_email)
    return user


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    normalized_email = _normalize_email(body.email)
    result = await db.execute(select(User).where(User.email == normalized_email))
    user = result.scalar_one_or_none()

    # Always verify password hash to prevent timing attacks.
    # If user doesn't exist, verify against a dummy hash so the response
    # time is consistent regardless of whether the email exists.
    _dummy_hash = "$2b$12$dummyhashfortimingattak000000000000000000000000000000"
    password_ok = verify_password(body.password, user.hashed_password if user else _dummy_hash)

    if user is None or not password_ok:
        logger.warning("Failed login attempt for: %s", body.email)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    if not user.is_email_verified:
        raise HTTPException(status_code=403, detail="Email not verified. Please check your inbox.")

    settings = get_settings()
    token_data = {"sub": str(user.id), "email": user.email, "is_admin": user.is_admin}

    logger.info("User logged in: %s", normalized_email)
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    try:
        payload = decode_token(body.refresh_token)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid refresh token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or disabled")

    settings = get_settings()
    token_data = {"sub": str(user.id), "email": user.email, "is_admin": user.is_admin}

    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/limits")
async def get_limits(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the user's current usage vs limits."""
    from sqlalchemy import func
    from ..models.bot import Bot
    from ..models.broker_account import BrokerAccount

    bot_count = (await db.execute(
        select(func.count(Bot.id)).where(Bot.user_id == current_user.id)
    )).scalar() or 0

    account_count = (await db.execute(
        select(func.count(BrokerAccount.id)).where(
            BrokerAccount.user_id == current_user.id,
            BrokerAccount.is_active.is_(True),
        )
    )).scalar() or 0

    max_accounts = 99 if current_user.is_admin else 1

    return {
        "plan": current_user.plan or "free",
        "is_admin": current_user.is_admin,
        "balance": current_user.balance or 0.0,
        "bots": {"used": bot_count, "max": current_user.max_bots},
        "accounts": {"used": account_count, "max": max_accounts},
    }


@router.patch("/me", response_model=UserResponse)
async def update_me(
    body: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.password is not None:
        _validate_password_strength(body.password)
        if body.current_password is None:
            raise HTTPException(status_code=400, detail="Current password required to change password")
        if not verify_password(body.current_password, current_user.hashed_password):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        current_user.hashed_password = hash_password(body.password)

    if body.full_name is not None:
        current_user.full_name = html.escape(body.full_name.strip())

    await db.flush()
    await db.refresh(current_user)
    return current_user


@router.post("/verify-email")
async def verify_email(body: VerifyEmailRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User).where(User.email_verification_token == body.token)
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")

    # Check if token is expired (24 hour window)
    token_age = datetime.now(timezone.utc) - (user.updated_at or user.created_at)
    if token_age.total_seconds() > 86400:  # 24 hours
        raise HTTPException(status_code=400, detail="Verification token has expired. Please request a new one.")

    if user.is_email_verified:
        return {"message": "Email verified successfully"}

    user.is_email_verified = True
    user.email_verification_token = None
    await db.flush()

    logger.info("Email verified: %s", user.email)
    return {"message": "Email verified successfully"}


@router.post("/resend-verification")
async def resend_verification(
    body: ResendVerificationRequest, db: AsyncSession = Depends(get_db)
):
    normalized_email = _normalize_email(body.email)
    result = await db.execute(select(User).where(User.email == normalized_email))
    user = result.scalar_one_or_none()

    # Always return success to avoid leaking whether an email exists
    if user is None or user.is_email_verified:
        return {"message": "If that email is registered, a verification link has been sent."}

    token = generate_verification_token()
    user.email_verification_token = token
    await db.flush()

    try:
        send_verification_email(user.email, user.full_name, token)
    except EmailRateLimited as e:
        raise HTTPException(
            status_code=429,
            detail=f"Please wait {e.retry_after} seconds before requesting another email.",
        )
    except Exception as e:
        logger.error("Failed to send verification email to %s: %s", user.email, e)

    return {"message": "If that email is registered, a verification link has been sent."}
