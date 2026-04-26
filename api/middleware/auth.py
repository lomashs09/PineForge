"""JWT authentication dependencies for FastAPI."""

from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db
from ..models.user import User
from ..services.auth_service import decode_token
from ..utils.log_context import user_id_var

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

DEV_USER_EMAIL = "dev@pineforge.local"


async def _get_or_create_dev_user(db: AsyncSession) -> User:
    """Return a default dev user, creating one if it doesn't exist."""
    result = await db.execute(select(User).where(User.email == DEV_USER_EMAIL))
    user = result.scalar_one_or_none()
    if user is None:
        from ..services.auth_service import hash_password

        user = User(
            email=DEV_USER_EMAIL,
            hashed_password=hash_password("dev"),
            full_name="Dev User",
            is_admin=True,
            max_bots=99,
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
    return user


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    settings = get_settings()

    # Bypass auth in dev mode
    if settings.AUTH_DISABLED:
        user = await _get_or_create_dev_user(db)
        _bind_user_to_request_scope(user)
        return user

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if token is None:
        raise credentials_exception

    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise credentials_exception
        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == UUID(user_id)))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise credentials_exception
    _bind_user_to_request_scope(user)
    return user


def _bind_user_to_request_scope(user: User) -> None:
    """Attach user identity to the per-request log + Sentry scope."""
    user_id_var.set(str(user.id))
    try:
        import sentry_sdk
        sentry_sdk.set_user({"id": str(user.id), "username": user.email})
    except Exception:
        pass


async def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user
