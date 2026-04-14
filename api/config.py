"""Application configuration loaded from environment variables."""

import logging
import warnings
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

_INSECURE_DEFAULTS = {"change-me-in-production", "changeme", "secret", ""}


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://pineforge:password@localhost:5432/pineforge"

    # Database pool tuning (configurable via env)
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_RECYCLE: int = 180

    # JWT
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # Encryption key for MT5 passwords (separate from JWT secret)
    ENCRYPTION_KEY: str = ""

    # MT5 backend: "metaapi" (cloud) or "bridge" (self-hosted) or "direct"
    MT5_BACKEND: str = "metaapi"

    # MetaAPI settings (when MT5_BACKEND=metaapi)
    METAAPI_TOKEN: str = ""

    # Self-hosted bridge settings (when MT5_BACKEND=bridge)
    MT5_BRIDGE_URL: str = ""  # e.g. "http://mt5bridge:5555"

    # Auth
    AUTH_DISABLED: bool = False  # Set True to bypass JWT auth (dev only)

    # Stripe
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PRICE_STARTER_MONTHLY: str = ""
    STRIPE_PRICE_STARTER_ANNUAL: str = ""
    STRIPE_PRICE_PRO_MONTHLY: str = ""
    STRIPE_PRICE_PRO_ANNUAL: str = ""
    STRIPE_PRICE_EXPERT_MONTHLY: str = ""
    STRIPE_PRICE_EXPERT_ANNUAL: str = ""

    # Razorpay
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""

    # Data providers
    TWELVEDATA_API_KEY: str = ""  # Free tier: 800 credits/day, 1+ year intraday history

    # Email (Resend)
    RESEND_API_KEY: str = ""
    EMAIL_FROM: str = "PineForge <noreply@getpineforge.com>"
    FRONTEND_URL: str = "http://localhost:5173"

    # CORS
    CORS_ORIGINS: str = ""  # Comma-separated allowed origins; empty = FRONTEND_URL only

    # App
    APP_ENV: str = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @model_validator(mode="after")
    def _validate_security(self) -> "Settings":
        """Warn or reject insecure configurations at startup."""
        is_prod = self.APP_ENV == "production"

        # JWT secret must be strong in production
        if self.JWT_SECRET_KEY in _INSECURE_DEFAULTS or len(self.JWT_SECRET_KEY) < 32:
            if is_prod:
                raise ValueError(
                    "JWT_SECRET_KEY is insecure. Set a strong random secret (>= 32 chars) in production."
                )
            warnings.warn(
                "JWT_SECRET_KEY is using an insecure default. Set a strong secret via env var.",
                stacklevel=2,
            )

        # AUTH_DISABLED must never be on in production
        if self.AUTH_DISABLED and is_prod:
            raise ValueError("AUTH_DISABLED=true is not allowed in production.")

        # Auto-generate encryption key from JWT secret if not set (with warning)
        if not self.ENCRYPTION_KEY:
            self.ENCRYPTION_KEY = self.JWT_SECRET_KEY
            if is_prod:
                warnings.warn(
                    "ENCRYPTION_KEY not set — falling back to JWT_SECRET_KEY. "
                    "Set a separate ENCRYPTION_KEY for better security.",
                    stacklevel=2,
                )

        return self

    @property
    def cors_allowed_origins(self) -> list[str]:
        """Parse CORS_ORIGINS into a list, falling back to FRONTEND_URL."""
        if self.CORS_ORIGINS:
            return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]
        if self.APP_ENV == "development":
            return ["*"]
        return [self.FRONTEND_URL] if self.FRONTEND_URL else []


@lru_cache
def get_settings() -> Settings:
    return Settings()
