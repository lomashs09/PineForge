"""PineForge Bot Worker — runs trading bots on a Windows machine with MT5.

This is a standalone process that:
1. Connects to the Neon DB (same as the API)
2. Polls for bots with status "start_requested" or "stop_requested"
3. Starts/stops LiveBridge instances with direct MT5 access
4. Writes logs and trades back to DB

Run: python -m worker.main
"""

import asyncio
import io
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from dotenv import load_dotenv
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

# Add parent dir to path so we can import pineforge
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()

from worker.config import WorkerConfig
from worker.executor import DirectExecutor
from worker import mt5_direct as mt5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("worker")

# Import models (they share the same DB)
from api.models.bot import Bot
from api.models.bot_log import BotLog
from api.models.bot_trade import BotTrade
from api.models.broker_account import BrokerAccount
from api.models.script import Script


class BotWorker:
    """Manages bot lifecycle on a Windows machine with direct MT5 access."""

    def __init__(self, config: WorkerConfig, session_factory: async_sessionmaker):
        self.config = config
        self.session_factory = session_factory
        self._running: Dict[str, asyncio.Task] = {}  # bot_id str → task
        self._accounts_logged_in: set = set()  # MT5 login numbers already authenticated

    async def run(self):
        """Main loop: poll DB for bot commands."""
        logger.info("Worker %s starting (poll=%ds, max_bots=%d)",
                     self.config.worker_id, self.config.poll_interval, self.config.max_bots)

        # Initialize MT5 terminal
        if not await mt5.initialize():
            logger.error("Failed to initialize MT5 terminal. Is it running?")
            return

        logger.info("MT5 terminal connected")

        # Restart bots that were running before worker restart
        await self._restart_running_bots()

        # Poll loop
        while True:
            try:
                await self._poll_commands()
            except Exception as e:
                logger.error("Poll error: %s", e, exc_info=True)

            await asyncio.sleep(self.config.poll_interval)

    async def _poll_commands(self):
        """Check DB for start/stop requests."""
        async with self.session_factory() as db:
            # Find bots requesting start
            result = await db.execute(
                select(Bot)
                .options(selectinload(Bot.broker_account), selectinload(Bot.script))
                .where(Bot.status == "start_requested")
            )
            bots_to_start = result.scalars().all()
            if bots_to_start:
                logger.info("Found %d bots to start", len(bots_to_start))
            for bot in bots_to_start:
                if str(bot.id) not in self._running:
                    await self._start_bot(bot, db)

            # Find bots requesting stop
            result = await db.execute(
                select(Bot).where(Bot.status == "stop_requested")
            )
            bots_to_stop = result.scalars().all()
            for bot in bots_to_stop:
                await self._stop_bot(bot, db)

            await db.commit()

    async def _start_bot(self, bot: Bot, db: AsyncSession):
        """Start a bot."""
        if len(self._running) >= self.config.max_bots:
            bot.status = "error"
            bot.error_message = f"Worker at capacity ({self.config.max_bots} bots)"
            return

        logger.info("Starting bot %s (%s) — %s %s", bot.id, bot.name, bot.symbol, bot.timeframe)

        account = bot.broker_account
        script = bot.script

        # Login to MT5 account if not already
        login_key = f"{account.mt5_login}@{account.mt5_server}"
        if login_key not in self._accounts_logged_in:
            # We need the MT5 password — but we don't store it.
            # The terminal should already be logged in via the GUI.
            # Just verify the connection is good.
            if not await mt5.is_connected():
                bot.status = "error"
                bot.error_message = "MT5 terminal not connected. Please login via MT5 GUI."
                return
            self._accounts_logged_in.add(login_key)

        # Update status
        bot.status = "running"
        bot.started_at = datetime.now(timezone.utc)
        bot.error_message = None

        # Create and start the bot task
        task = asyncio.create_task(
            self._run_bot(str(bot.id), bot.name, script.source,
                          bot.symbol, bot.timeframe, float(bot.lot_size),
                          bot.is_live, bot.poll_interval_seconds,
                          bot.lookback_bars, str(account.id))
        )
        self._running[str(bot.id)] = task

    async def _stop_bot(self, bot: Bot, db: AsyncSession):
        """Stop a bot."""
        bot_id = str(bot.id)
        task = self._running.get(bot_id)

        if task:
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=10)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self._running.pop(bot_id, None)

        bot.status = "stopped"
        bot.stopped_at = datetime.now(timezone.utc)
        bot.error_message = None
        logger.info("Stopped bot %s (%s)", bot.id, bot.name)

    async def _run_bot(self, bot_id: str, name: str, script_source: str,
                       symbol: str, timeframe: str, lot_size: float,
                       is_live: bool, poll_seconds: int, lookback: int,
                       broker_account_id: str):
        """Run a single bot using LiveBridge with direct MT5 executor."""
        from pineforge.live.bridge import LiveBridge
        from pineforge.live.config import LiveConfig

        logger.info("Bot %s running: %s %s %s", name, symbol, timeframe,
                     "LIVE" if is_live else "DRY")

        config = LiveConfig(
            symbol=symbol,
            timeframe=timeframe,
            lot_size=lot_size,
            is_live=is_live,
            poll_interval_seconds=poll_seconds,
            lookback_bars=lookback,
            script_source=script_source,
            mt5_backend="direct",  # New backend type for direct MT5
        )

        bridge = LiveBridge(config)
        bridge._register_signals = False

        # Override the executor creation — use DirectExecutor
        bridge._direct_executor_cls = DirectExecutor

        try:
            await bridge.run()
        except asyncio.CancelledError:
            logger.info("Bot %s cancelled", name)
        except Exception as e:
            logger.error("Bot %s crashed: %s", name, e, exc_info=True)
            # Update status in DB
            try:
                async with self.session_factory() as db:
                    result = await db.execute(select(Bot).where(Bot.id == bot_id))
                    bot = result.scalar_one_or_none()
                    if bot:
                        bot.status = "error"
                        bot.error_message = str(e)[:500]
                        bot.stopped_at = datetime.now(timezone.utc)
                        await db.commit()
            except Exception:
                pass
        finally:
            self._running.pop(bot_id, None)
            # Update stopped status
            try:
                async with self.session_factory() as db:
                    result = await db.execute(select(Bot).where(Bot.id == bot_id))
                    bot = result.scalar_one_or_none()
                    if bot and bot.status == "running":
                        bot.status = "stopped"
                        bot.stopped_at = datetime.now(timezone.utc)
                        await db.commit()
            except Exception:
                pass

    async def _restart_running_bots(self):
        """On startup, restart bots that were running before."""
        async with self.session_factory() as db:
            result = await db.execute(
                select(Bot)
                .options(selectinload(Bot.broker_account), selectinload(Bot.script))
                .where(Bot.status.in_(["running", "starting", "start_requested"]))
            )
            bots = result.scalars().all()

            if bots:
                logger.info("Restarting %d bots from previous session", len(bots))
                for bot in bots:
                    bot.status = "start_requested"
                await db.commit()


async def main():
    config = WorkerConfig.from_env()

    if not config.database_url:
        logger.error("DATABASE_URL not set")
        return

    # Create DB engine with Neon-friendly settings
    engine_kwargs = {
        "pool_recycle": 180,
        "pool_pre_ping": True,
        "pool_size": 5,
        "max_overflow": 10,
    }
    if "neon.tech" in config.database_url:
        engine_kwargs["connect_args"] = {"ssl": True}

    engine = create_async_engine(config.database_url, **engine_kwargs)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    worker = BotWorker(config, session_factory)

    try:
        await worker.run()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
