"""Standalone bot process — runs a single bot in its own process for crash isolation.

Usage: python -m worker.bot_runner <json_config>

The supervisor (worker/main.py) spawns one of these per bot. If this process
crashes (segfault in MT5 DLL, infinite loop), only this bot is affected.

Config JSON schema:
{
    "bot_id": "uuid",
    "name": "My Bot",
    "script_source": "...",
    "symbol": "XAUUSDm",
    "timeframe": "1h",
    "lot_size": 0.01,
    "is_live": false,
    "poll_interval_seconds": 60,
    "lookback_bars": 200,
    "broker_account_id": "uuid",
    "terminal_path": "C:\\MT5\\Acc_123\\terminal64.exe",
    "database_url": "postgresql+asyncpg://...",
    "jwt_secret": ""
}
"""

import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add parent dir to path so we can import pineforge
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("bot_runner")

_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    logger.info("Received signal %s — shutting down", signum)
    _shutdown = True


async def run_bot(config: dict):
    """Run a single bot until shutdown or crash."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy import select

    from pineforge.live.bridge import LiveBridge
    from pineforge.live.config import LiveConfig
    from worker.executor import DirectExecutor
    from api.models.bot import Bot

    bot_id = config["bot_id"]
    name = config["name"]
    terminal_path = config.get("terminal_path", "")
    database_url = config["database_url"]

    logger.info("Bot runner starting: %s (%s %s %s)",
                name, config["symbol"], config["timeframe"],
                "LIVE" if config["is_live"] else "DRY")

    # Create DB engine
    engine_kwargs = {
        "pool_recycle": 180,
        "pool_pre_ping": True,
        "pool_size": 2,
        "max_overflow": 3,
    }
    if "neon.tech" in database_url:
        import ssl as _ssl
        ssl_ctx = _ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = _ssl.CERT_NONE
        engine_kwargs["connect_args"] = {"ssl": ssl_ctx}

    db_engine = create_async_engine(database_url, **engine_kwargs)
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    # Update status to running
    try:
        async with session_factory() as db:
            result = await db.execute(select(Bot).where(Bot.id == bot_id))
            bot = result.scalar_one_or_none()
            if bot:
                bot.status = "running"
                bot.started_at = datetime.now(timezone.utc)
                bot.error_message = None
                await db.commit()
    except Exception as e:
        logger.error("Failed to update bot status: %s", e)

    # Create LiveBridge
    live_config = LiveConfig(
        symbol=config["symbol"],
        timeframe=config["timeframe"],
        lot_size=config["lot_size"],
        is_live=config["is_live"],
        poll_interval_seconds=config["poll_interval_seconds"],
        lookback_bars=config["lookback_bars"],
        script_source=config["script_source"],
        mt5_backend="direct",
    )

    bridge = LiveBridge(live_config)
    bridge._register_signals = False
    bridge._direct_executor_cls = lambda sym, live: DirectExecutor(sym, live, terminal_path)
    bridge._terminal_path = terminal_path

    # Run the bridge — check _shutdown flag periodically
    exit_code = 0
    error_msg = None
    try:
        # Use a task so we can cancel on shutdown
        task = asyncio.create_task(bridge.run())

        while not task.done():
            if _shutdown:
                bridge._shutdown = True
                try:
                    await asyncio.wait_for(task, timeout=15)
                except asyncio.TimeoutError:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                break
            await asyncio.sleep(1)

        if task.done() and task.exception():
            raise task.exception()

    except asyncio.CancelledError:
        logger.info("Bot %s cancelled", name)
    except Exception as e:
        logger.error("Bot %s crashed: %s", name, e, exc_info=True)
        error_msg = str(e)[:500]
        exit_code = 1
    finally:
        # Update final status in DB
        try:
            async with session_factory() as db:
                result = await db.execute(select(Bot).where(Bot.id == bot_id))
                bot = result.scalar_one_or_none()
                if bot:
                    if error_msg:
                        bot.status = "error"
                        bot.error_message = error_msg
                    elif bot.status == "running":
                        bot.status = "stopped"
                    bot.stopped_at = datetime.now(timezone.utc)
                    await db.commit()
        except Exception as e:
            logger.error("Failed to update final bot status: %s", e)

        await db_engine.dispose()
        logger.info("Bot runner exiting (code=%d)", exit_code)

    return exit_code


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m worker.bot_runner '<json_config>'", file=sys.stderr)
        sys.exit(1)

    config = json.loads(sys.argv[1])

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    exit_code = asyncio.run(run_bot(config))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
