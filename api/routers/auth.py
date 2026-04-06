"""Authentication routes — register, login, refresh, profile, email verification."""

from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError
from sqlalchemy import select
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

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="Email already registered")

    token = generate_verification_token()
    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
        email_verification_token=token,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    try:
        send_verification_email(body.email, body.full_name, token)
    except EmailRateLimited:
        pass  # User just registered — don't block registration over rate limit

    return user


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    if not user.is_email_verified:
        raise HTTPException(status_code=403, detail="Email not verified. Please check your inbox.")

    settings = get_settings()
    token_data = {"sub": str(user.id), "email": user.email, "is_admin": user.is_admin}

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
            BrokerAccount.is_active == True,
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
        if body.current_password is None:
            raise HTTPException(status_code=400, detail="Current password required to change password")
        if not verify_password(body.current_password, current_user.hashed_password):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        current_user.hashed_password = hash_password(body.password)

    if body.full_name is not None:
        current_user.full_name = body.full_name

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

    if user.is_email_verified:
        return {"message": "Email already verified"}

    user.is_email_verified = True
    user.email_verification_token = None
    await db.flush()

    return {"message": "Email verified successfully"}


@router.post("/resend-verification")
async def resend_verification(
    body: ResendVerificationRequest, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(User).where(User.email == body.email))
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

    return {"message": "If that email is registered, a verification link has been sent."}
