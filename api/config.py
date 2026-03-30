"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://pineforge:password@localhost:5432/pineforge"

    # JWT
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # MT5 backend: "metaapi" (cloud) or "bridge" (self-hosted)
    MT5_BACKEND: str = "metaapi"

    # MetaAPI settings (when MT5_BACKEND=metaapi)
    METAAPI_TOKEN: str = ""

    # Self-hosted bridge settings (when MT5_BACKEND=bridge)
    MT5_BRIDGE_URL: str = ""  # e.g. "http://mt5bridge:5555"

    # Auth
    AUTH_DISABLED: bool = False  # Set True to bypass JWT auth (dev only)

    # App
    APP_ENV: str = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
