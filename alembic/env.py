"""Alembic async migration environment."""

import asyncio
import os
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

load_dotenv()

from api.database import Base, asyncpg_connect_args  # noqa: E402

config = context.config

if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option(
        "sqlalchemy.url",
        os.getenv("DATABASE_URL", "postgresql+asyncpg://pineforge:password@localhost:5432/pineforge"),
    )

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import all models so Alembic sees them
from api.models import *  # noqa: F401, F403

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    url = config.get_main_option("sqlalchemy.url")
    _kw = {"poolclass": pool.NullPool}
    _ca = asyncpg_connect_args(url)
    if _ca:
        _kw["connect_args"] = _ca
    connectable = create_async_engine(url, **_kw)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
