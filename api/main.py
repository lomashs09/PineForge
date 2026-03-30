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
    )
    app.state.bot_manager = bot_manager

    # Run slow startup tasks (seeding, bot restart) in background
    import asyncio
    asyncio.create_task(_startup_tasks())
    asyncio.create_task(bot_manager.restart_crashed_bots())

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
app.include_router(bots.router)
app.include_router(dashboard.router)
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
    return {"status": "ok"}
