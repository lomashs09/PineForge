"""Async SQLAlchemy engine, session factory, and base model."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


def asyncpg_connect_args(database_url: str) -> dict:
    """Neon (and other cloud Postgres) requires TLS; asyncpg needs explicit ssl for many URLs."""
    if "neon.tech" in database_url:
        return {"ssl": True}
    return {}


settings = get_settings()

_engine_kwargs = {"echo": settings.APP_ENV == "development"}
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
        except Exception:
            await session.rollback()
            raise
