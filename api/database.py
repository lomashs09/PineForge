"""Async SQLAlchemy engine, session factory, and base model."""

import logging
from collections.abc import AsyncGenerator

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings

logger = logging.getLogger(__name__)


def asyncpg_connect_args(database_url: str) -> dict:
    """Neon (and other cloud Postgres) requires TLS; asyncpg needs explicit ssl for many URLs."""
    if "neon.tech" in database_url:
        return {"ssl": True}
    return {}


settings = get_settings()

_engine_kwargs = {
    "echo": settings.APP_ENV == "development",
    # Neon serverless drops idle connections after ~5 minutes.
    # These settings prevent "connection is closed" errors:
    "pool_recycle": settings.DB_POOL_RECYCLE,
    "pool_pre_ping": True,
    "pool_size": settings.DB_POOL_SIZE,
    "max_overflow": settings.DB_MAX_OVERFLOW,
}
_ca = asyncpg_connect_args(settings.DATABASE_URL)
if _ca:
    _engine_kwargs["connect_args"] = _ca

engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except SQLAlchemyError as e:
            logger.error("Database error, rolling back: %s", e)
            await session.rollback()
            raise
        except Exception:
            await session.rollback()
            raise
