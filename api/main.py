"""FastAPI application factory and startup/shutdown hooks."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from .config import get_settings

# Initialize Sentry before importing FastAPI/Starlette so auto-instrumentation patches them.
_settings = get_settings()
if _settings.SENTRY_DSN:
    import sentry_sdk

    def _sentry_before_send(event, hint):
        """Drop events that are user-facing bot output, not platform errors.

        - bot.<uuid> loggers carry the user's bot console output (captured via
          BotPrintCapture). Those go to the bot_logs table and the user's bot
          dashboard — they're not platform issues to be paged on.
        - asyncio.TimeoutError from pineforge.live.* is a recoverable broker
          latency event, already demoted to WARNING; this is a defense-in-depth
          guard in case any other path emits it at ERROR.
        """
        logger_name = event.get("logger") or ""
        if logger_name.startswith("bot."):
            return None
        exc_info = hint.get("exc_info") if hint else None
        if exc_info and exc_info[0] is asyncio.TimeoutError and logger_name.startswith("pineforge.live"):
            return None
        return event

    sentry_sdk.init(
        dsn=_settings.SENTRY_DSN,
        environment=_settings.APP_ENV,
        release=_settings.SENTRY_RELEASE or None,
        traces_sample_rate=_settings.SENTRY_TRACES_SAMPLE_RATE,
        profiles_sample_rate=_settings.SENTRY_PROFILES_SAMPLE_RATE,
        send_default_pii=True,
        before_send=_sentry_before_send,
    )

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from .middleware.rate_limit import RateLimitMiddleware
from .middleware.request_context import RequestContextMiddleware
from .database import async_session, engine
from .routers import (
    accounts,
    admin,
    auth,
    billing,
    bots,
    dashboard,
    newsletter,
    payments,
    public_stats,
    scripts,
)
from .services.bot_manager import BotManager
from .services.log_cleanup import log_cleanup_loop
from .services.script_service import seed_system_scripts
from .services.bot_health_check import bot_health_check_loop
from .services.usage_billing import usage_billing_loop
from .utils.log_context import configure_logging

configure_logging()
logger = logging.getLogger(__name__)


async def _startup_tasks():
    """Run startup tasks in the background so the server starts immediately."""
    try:
        async with async_session() as db:
            await seed_system_scripts(db)
    except Exception as e:
        logger.warning("Failed to seed system scripts: %s", e)


def _fire_and_forget(coro, name: str):
    """Create a background task with error logging so exceptions are never silently lost."""
    async def _wrapper():
        try:
            await coro
        except asyncio.CancelledError:
            logger.info("Background task '%s' cancelled", name)
        except Exception:
            logger.exception("Background task '%s' failed", name)
    return asyncio.create_task(_wrapper(), name=name)


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
    app.state.start_time = time.time()

    # Run slow startup tasks (seeding, bot restart, log cleanup) in background
    bg_tasks = [
        _fire_and_forget(_startup_tasks(), "seed_scripts"),
        _fire_and_forget(bot_manager.restart_crashed_bots(), "restart_bots"),
        _fire_and_forget(log_cleanup_loop(async_session), "log_cleanup"),
        _fire_and_forget(usage_billing_loop(async_session, bot_manager), "usage_billing"),
        _fire_and_forget(bot_health_check_loop(async_session), "bot_health_check"),
    ]

    yield

    # Shutdown: cancel background tasks, stop all bots
    for t in bg_tasks:
        t.cancel()
    await bot_manager.shutdown_all()
    await engine.dispose()


settings = get_settings()

app = FastAPI(
    title="PineForge Cloud",
    description="Multi-tenant trading bot platform API",
    version="0.2.0",
    lifespan=lifespan,
    redirect_slashes=False,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=bool(settings.CORS_ORIGINS),  # Only allow credentials with explicit origins
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["X-Request-Id"],
)

# Rate limiting on auth endpoints (login, register, resend-verification)
app.add_middleware(RateLimitMiddleware)

# Request context (X-Request-Id, access log, Sentry scope) — added LAST so it
# wraps everything and every inner middleware/log line carries the request id.
app.add_middleware(RequestContextMiddleware)


# Request body size limit (1MB) to prevent DoS via huge payloads
MAX_BODY_SIZE = 1_048_576  # 1 MB


@app.middleware("http")
async def limit_request_body(request: Request, call_next):
    """Reject requests with bodies larger than MAX_BODY_SIZE."""
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_BODY_SIZE:
        return JSONResponse(
            status_code=413,
            content={"detail": "Request body too large. Maximum size is 1MB."},
        )
    return await call_next(request)


# Routers
app.include_router(auth.router)
app.include_router(scripts.router)
app.include_router(accounts.router)
app.include_router(billing.router)
app.include_router(bots.router)
app.include_router(dashboard.router)
app.include_router(payments.router)
app.include_router(newsletter.router)
app.include_router(public_stats.router)
app.include_router(admin.router)


if settings.APP_ENV != "production":
    @app.get("/sentry-debug")
    async def trigger_error():
        division_by_zero = 1 / 0


@app.get("/health")
async def health():
    from sqlalchemy import text

    db_ok = False
    try:
        async with async_session() as db:
            await db.execute(text("SELECT 1"))
            db_ok = True
    except Exception as e:
        logger.warning("Health check DB probe failed: %s", e)

    running_bots = 0
    try:
        bot_manager = app.state.bot_manager
        running_bots = bot_manager.running_bot_count
    except Exception:
        pass

    start_time = getattr(app.state, "start_time", 0)
    uptime = int(time.time() - start_time) if start_time else 0

    return {
        "status": "ok" if db_ok else "degraded",
        "db_ok": db_ok,
        "running_bots": running_bots,
        "uptime_seconds": uptime,
        "version": "0.2.0",
    }
