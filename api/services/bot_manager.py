"""Bot Manager singleton — manages asyncio tasks for LiveBridge instances."""

import asyncio
import logging
import sys
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from ..models.bot import Bot
from ..models.broker_account import BrokerAccount
from ..models.script import Script
from ..utils.bot_logger import BotDatabaseHandler, BotPrintCapture

logger = logging.getLogger(__name__)


class BotManager:
    """Manages running bot asyncio tasks. One instance per FastAPI app."""

    def __init__(self, session_factory: async_sessionmaker, metaapi_token: str):
        self._session_factory = session_factory
        self._metaapi_token = metaapi_token
        self._running_bots: Dict[uuid.UUID, asyncio.Task] = {}
        self._bot_bridges: Dict[uuid.UUID, object] = {}  # LiveBridge instances
        self._bot_loggers: Dict[uuid.UUID, BotDatabaseHandler] = {}

    async def start_bot(self, bot_id: uuid.UUID) -> None:
        """Load bot config from DB and start it as an asyncio task."""
        if bot_id in self._running_bots:
            raise RuntimeError(f"Bot {bot_id} is already running")

        from pineforge.live.bridge import LiveBridge
        from pineforge.live.config import LiveConfig

        async with self._session_factory() as db:
            result = await db.execute(
                select(Bot)
                .options(selectinload(Bot.broker_account), selectinload(Bot.script))
                .where(Bot.id == bot_id)
            )
            bot = result.scalar_one_or_none()
            if bot is None:
                raise ValueError(f"Bot {bot_id} not found")
            if bot.status in ("running", "starting"):
                raise RuntimeError(f"Bot {bot_id} is already {bot.status}")

            account = bot.broker_account
            script = bot.script

            config = LiveConfig(
                metaapi_token=self._metaapi_token,
                metaapi_account_id=account.metaapi_account_id,
                symbol=bot.symbol,
                timeframe=bot.timeframe,
                lot_size=float(bot.lot_size),
                max_lot_size=float(bot.max_lot_size),
                risk_per_trade_pct=1.0,
                max_daily_loss_pct=float(bot.max_daily_loss_pct),
                max_open_positions=bot.max_open_positions,
                cooldown_seconds=bot.cooldown_seconds,
                is_live=bot.is_live,
                poll_interval_seconds=bot.poll_interval_seconds,
                lookback_bars=bot.lookback_bars,
                script_source=script.source,
            )

            bridge = LiveBridge(config)
            bridge._register_signals = False  # Don't register OS signal handlers

            # Set up dedicated logger
            bot_logger = logging.getLogger(f"bot.{bot_id}")
            bot_logger.setLevel(logging.DEBUG)
            db_handler = BotDatabaseHandler(bot_id, self._session_factory)
            bot_logger.addHandler(db_handler)
            db_handler.start()

            # Update status
            bot.status = "starting"
            bot.started_at = datetime.now(timezone.utc)
            bot.error_message = None
            await db.commit()

        # Store references
        self._bot_bridges[bot_id] = bridge
        self._bot_loggers[bot_id] = db_handler

        # Create the asyncio task
        task = asyncio.create_task(self._run_bot_wrapper(bot_id, bridge, bot_logger, db_handler))
        self._running_bots[bot_id] = task

    async def _run_bot_wrapper(
        self,
        bot_id: uuid.UUID,
        bridge,
        bot_logger: logging.Logger,
        db_handler: BotDatabaseHandler,
    ):
        """Wrapper that runs the bridge and handles errors/cleanup."""
        original_stdout = sys.stdout
        capture = BotPrintCapture(bot_logger)

        try:
            # Redirect stdout for this coroutine's print() calls
            sys.stdout = capture

            # Update status to running
            async with self._session_factory() as db:
                result = await db.execute(select(Bot).where(Bot.id == bot_id))
                bot = result.scalar_one_or_none()
                if bot:
                    bot.status = "running"
                    await db.commit()

            await bridge.run()

            # Clean exit
            async with self._session_factory() as db:
                result = await db.execute(select(Bot).where(Bot.id == bot_id))
                bot = result.scalar_one_or_none()
                if bot:
                    bot.status = "stopped"
                    bot.stopped_at = datetime.now(timezone.utc)
                    await db.commit()

        except asyncio.CancelledError:
            async with self._session_factory() as db:
                result = await db.execute(select(Bot).where(Bot.id == bot_id))
                bot = result.scalar_one_or_none()
                if bot:
                    bot.status = "stopped"
                    bot.stopped_at = datetime.now(timezone.utc)
                    await db.commit()

        except Exception as e:
            logger.error("Bot %s crashed: %s", bot_id, e, exc_info=True)
            async with self._session_factory() as db:
                result = await db.execute(select(Bot).where(Bot.id == bot_id))
                bot = result.scalar_one_or_none()
                if bot:
                    bot.status = "error"
                    bot.error_message = str(e)
                    bot.stopped_at = datetime.now(timezone.utc)
                    await db.commit()

        finally:
            sys.stdout = original_stdout
            await db_handler.stop()
            bot_logger.removeHandler(db_handler)
            self._running_bots.pop(bot_id, None)
            self._bot_bridges.pop(bot_id, None)
            self._bot_loggers.pop(bot_id, None)

    async def stop_bot(self, bot_id: uuid.UUID) -> None:
        """Gracefully stop a running bot."""
        bridge = self._bot_bridges.get(bot_id)
        task = self._running_bots.get(bot_id)

        if bridge is None or task is None:
            # Not running in memory, just update DB
            async with self._session_factory() as db:
                result = await db.execute(select(Bot).where(Bot.id == bot_id))
                bot = result.scalar_one_or_none()
                if bot and bot.status in ("running", "starting"):
                    bot.status = "stopped"
                    bot.stopped_at = datetime.now(timezone.utc)
                    await db.commit()
            return

        # Signal graceful shutdown
        bridge._shutdown = True

        # Wait up to 30 seconds for graceful exit
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=30)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            pass

    def get_status(self, bot_id: uuid.UUID) -> Optional[dict]:
        """Get live status from the in-memory bridge instance."""
        bridge = self._bot_bridges.get(bot_id)
        if bridge is None:
            return None

        uptime_seconds = 0
        if bridge._start_time:
            uptime_seconds = int((datetime.now(timezone.utc) - bridge._start_time).total_seconds())

        return {
            "running": bot_id in self._running_bots,
            "uptime_seconds": uptime_seconds,
            "bars_processed": bridge._bar_count,
            "polls": bridge._poll_count,
            "last_signal": bridge._pending_signal,
        }

    def is_running(self, bot_id: uuid.UUID) -> bool:
        return bot_id in self._running_bots

    async def restart_crashed_bots(self) -> None:
        """On startup, restart bots that were running before server shutdown."""
        async with self._session_factory() as db:
            result = await db.execute(
                select(Bot).where(Bot.status.in_(["running", "starting"]))
            )
            bots = result.scalars().all()

        for bot in bots:
            try:
                logger.info("Restarting bot %s (%s)", bot.id, bot.name)
                await self.start_bot(bot.id)
            except Exception as e:
                logger.error("Failed to restart bot %s: %s", bot.id, e)
                async with self._session_factory() as db:
                    result = await db.execute(select(Bot).where(Bot.id == bot.id))
                    b = result.scalar_one_or_none()
                    if b:
                        b.status = "error"
                        b.error_message = f"Failed to restart: {e}"
                        await db.commit()

    async def shutdown_all(self) -> None:
        """Stop all running bots (called on app shutdown)."""
        bot_ids = list(self._running_bots.keys())
        for bot_id in bot_ids:
            try:
                await self.stop_bot(bot_id)
            except Exception as e:
                logger.error("Error stopping bot %s during shutdown: %s", bot_id, e)
