"""FastAPI application factory and startup/shutdown hooks."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .database import async_session, engine
from .routers import accounts, admin, auth, bots, dashboard, scripts
from .services.bot_manager import BotManager
from .services.script_service import seed_system_scripts

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # Startup: seed system scripts
    async with async_session() as db:
        await seed_system_scripts(db)

    # Initialize BotManager
    bot_manager = BotManager(
        session_factory=async_session,
        metaapi_token=settings.METAAPI_TOKEN,
    )
    app.state.bot_manager = bot_manager

    # Restart bots that were running before shutdown
    await bot_manager.restart_crashed_bots()

    yield

    # Shutdown: stop all bots
    await bot_manager.shutdown_all()
    await engine.dispose()


app = FastAPI(
    title="PineForge Cloud",
    description="Multi-tenant trading bot platform API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth.router)
app.include_router(scripts.router)
app.include_router(accounts.router)
app.include_router(bots.router)
app.include_router(dashboard.router)
app.include_router(admin.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
