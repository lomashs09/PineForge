"""FastAPI application factory and startup/shutdown hooks."""

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .database import async_session, engine
from .routers import accounts, admin, auth, billing, bots, dashboard, payments, scripts
from .services.bot_manager import BotManager
from .services.log_cleanup import log_cleanup_loop
from .services.script_service import seed_system_scripts

logger = logging.getLogger(__name__)

_app_start_time: float = 0.0


async def _startup_tasks():
    """Run startup tasks in the background so the server starts immediately."""
    try:
        async with async_session() as db:
            await seed_system_scripts(db)
    except Exception as e:
        logger.warning("Failed to seed system scripts: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # Initialize BotManager immediately
    bot_manager = BotManager(
        session_factory=async_session,
        metaapi_token=settings.METAAPI_TOKEN,
        mt5_backend=settings.MT5_BACKEND,
        mt5_bridge_url=settings.MT5_BRIDGE_URL,
    )
    app.state.bot_manager = bot_manager

    global _app_start_time
    _app_start_time = time.time()

    # Run slow startup tasks (seeding, bot restart, log cleanup) in background
    import asyncio
    asyncio.create_task(_startup_tasks())
    asyncio.create_task(bot_manager.restart_crashed_bots())
    asyncio.create_task(log_cleanup_loop(async_session))

    yield

    # Shutdown: stop all bots
    await bot_manager.shutdown_all()
    await engine.dispose()


from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import Response

app = FastAPI(
    title="PineForge Cloud",
    description="Multi-tenant trading bot platform API",
    version="0.1.0",
    lifespan=lifespan,
    redirect_slashes=False,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Routers
app.include_router(auth.router)
app.include_router(scripts.router)
app.include_router(accounts.router)
app.include_router(billing.router)
app.include_router(bots.router)
app.include_router(dashboard.router)
app.include_router(payments.router)
app.include_router(admin.router)


@app.options("/{rest_of_path:path}")
async def preflight_handler(rest_of_path: str, request: Request):
    """Catch-all for OPTIONS preflight requests that don't match a route."""
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        },
    )


@app.get("/health")
async def health():
    from sqlalchemy import text

    db_ok = False
    try:
        async with async_session() as db:
            await db.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        pass

    settings = get_settings()
    running_bots = 0
    if settings.MT5_BACKEND != "direct":
        try:
            running_bots = len(app.state.bot_manager._running_bots)
        except Exception:
            pass

    uptime = int(time.time() - _app_start_time) if _app_start_time else 0

    return {
        "status": "ok" if db_ok else "degraded",
        "db_ok": db_ok,
        "running_bots": running_bots,
        "uptime_seconds": uptime,
        "version": "0.2.0",
    }
