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

    def __init__(self, session_factory: async_sessionmaker, metaapi_token: str,
                 mt5_backend: str = "metaapi", mt5_bridge_url: str = ""):
        self._session_factory = session_factory
        self._metaapi_token = metaapi_token
        self._mt5_backend = mt5_backend
        self._mt5_bridge_url = mt5_bridge_url
        self._running_bots: Dict[uuid.UUID, asyncio.Task] = {}
        self._bot_bridges: Dict[uuid.UUID, object] = {}  # LiveBridge instances
        self._bot_loggers: Dict[uuid.UUID, BotDatabaseHandler] = {}
        self._bot_account_ids: Dict[uuid.UUID, str] = {}  # bot_id → metaapi_account_id
        self._shutting_down = False  # Set during app shutdown to skip status updates

    async def start_bot(self, bot_id: uuid.UUID, _is_restart: bool = False) -> None:
        """Load bot config from DB and start it as an asyncio task."""
        if bot_id in self._running_bots:
            if not _is_restart:
                raise RuntimeError(f"Bot {bot_id} is already running")
            # During restart, clean up stale references from previous run
            self._running_bots.pop(bot_id, None)
            self._bot_bridges.pop(bot_id, None)
            self._bot_loggers.pop(bot_id, None)
            self._bot_account_ids.pop(bot_id, None)

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
            if bot.status in ("running", "starting") and not _is_restart:
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
                mt5_backend=self._mt5_backend,
                mt5_bridge_url=self._mt5_bridge_url,
            )

            bridge = LiveBridge(config)
            bridge._register_signals = False  # Don't register OS signal handlers

            # Set up dedicated logger
            bot_logger = logging.getLogger(f"bot.{bot_id}")
            bot_logger.setLevel(logging.DEBUG)
            db_handler = BotDatabaseHandler(bot_id, self._session_factory, broker_account_id=account.id)
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
        self._bot_account_ids[bot_id] = account.metaapi_account_id

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
        capture = BotPrintCapture(bot_logger)

        def _bot_print(*args, **kwargs):
            """Per-bot print replacement that routes to the bot's own logger."""
            msg = " ".join(str(a) for a in args)
            capture.write(msg + "\n")

        # Inject per-bot print into bridge so it doesn't use global sys.stdout
        bridge._print_fn = _bot_print

        try:

            # Update status to running
            async with self._session_factory() as db:
                result = await db.execute(select(Bot).where(Bot.id == bot_id))
                bot = result.scalar_one_or_none()
                if bot:
                    bot.status = "running"
                    await db.commit()

            await bridge.run()

            # Bridge exited — if _shutdown was set by user, it's a clean stop
            # Otherwise it's an unexpected exit (connection drop) — keep as running for auto-restart
            if bridge._shutdown:
                async with self._session_factory() as db:
                    result = await db.execute(select(Bot).where(Bot.id == bot_id))
                    bot = result.scalar_one_or_none()
                    if bot:
                        bot.status = "stopped"
                        bot.stopped_at = datetime.now(timezone.utc)
                        await db.commit()
            else:
                # Unexpected exit — mark as running so restart_crashed_bots picks it up
                print(f"[BotManager] Bot {bot_id} exited unexpectedly — will auto-restart", flush=True)

        except asyncio.CancelledError:
            # During server shutdown, keep status as "running" for auto-restart
            if not self._shutting_down:
                async with self._session_factory() as db:
                    result = await db.execute(select(Bot).where(Bot.id == bot_id))
                    bot = result.scalar_one_or_none()
                    if bot:
                        bot.status = "stopped"
                        bot.stopped_at = datetime.now(timezone.utc)
                        await db.commit()

        except Exception as e:
            logger.error("Bot %s crashed: %s", bot_id, e, exc_info=True)
            print(f"[BotManager] Bot {bot_id} crashed: {e} — keeping as running for auto-restart", flush=True)
            # Keep status as running so restart_crashed_bots picks it up
            # Only set error if it's a permanent failure (e.g. bad script)
            err_str = str(e).lower()
            is_permanent = any(x in err_str for x in ["script", "parse", "syntax", "not found", "not accessible"])
            if is_permanent:
                async with self._session_factory() as db:
                    result = await db.execute(select(Bot).where(Bot.id == bot_id))
                    bot = result.scalar_one_or_none()
                    if bot:
                        bot.status = "error"
                        bot.error_message = str(e)[:500]
                        bot.stopped_at = datetime.now(timezone.utc)
                        await db.commit()

        finally:
            await db_handler.stop()
            bot_logger.removeHandler(db_handler)
            # Keep account deployed — redeploying costs $0.13 and takes 30-60s.
            # Accounts only undeploy when user disconnects from Accounts page.
            self._running_bots.pop(bot_id, None)
            self._bot_bridges.pop(bot_id, None)
            self._bot_loggers.pop(bot_id, None)
            self._bot_account_ids.pop(bot_id, None)

    async def _undeploy_account(self, bot_id: uuid.UUID) -> None:
        """Undeploy the MetaAPI account so it stops consuming resources."""
        metaapi_account_id = self._bot_account_ids.get(bot_id)
        if not metaapi_account_id or not self._metaapi_token:
            return

        # Only undeploy if no OTHER running bot uses the same account
        other_uses = any(
            aid == metaapi_account_id
            for bid, aid in self._bot_account_ids.items()
            if bid != bot_id and bid in self._running_bots
        )
        if other_uses:
            logger.info("Skipping undeploy for %s — other bots still using it", metaapi_account_id)
            return

        try:
            from metaapi_cloud_sdk import MetaApi
            api = MetaApi(token=self._metaapi_token)
            account = await api.metatrader_account_api.get_account(metaapi_account_id)
            if account.state in ("DEPLOYING", "DEPLOYED"):
                await account.undeploy()
                logger.info("Undeployed MetaAPI account %s", metaapi_account_id)
        except Exception as e:
            logger.warning("Failed to undeploy account %s: %s", metaapi_account_id, e)

    async def stop_bot(self, bot_id: uuid.UUID) -> dict:
        """Gracefully stop a running bot and close all its open positions.

        Returns dict with positions_closed count and pnl.
        """
        bridge = self._bot_bridges.get(bot_id)
        task = self._running_bots.get(bot_id)
        close_result = {"positions_closed": 0, "pnl": 0.0}

        if bridge is None or task is None:
            # Not running in memory, just update DB
            async with self._session_factory() as db:
                result = await db.execute(select(Bot).where(Bot.id == bot_id))
                bot = result.scalar_one_or_none()
                if bot and bot.status in ("running", "starting", "error"):
                    bot.status = "stopped"
                    bot.error_message = None
                    bot.stopped_at = datetime.now(timezone.utc)
                    await db.commit()
            return close_result

        # Close all open positions for this bot's symbol before stopping
        try:
            executor = getattr(bridge, '_executor', None)
            if executor:
                positions = await executor.get_positions()
                if positions:
                    pnl = sum(p.get("profit", 0) or 0 for p in positions)
                    await executor.close_all()
                    close_result = {"positions_closed": len(positions), "pnl": round(pnl, 2)}
                    print(f"[BotManager] Closed {len(positions)} positions for bot {bot_id} (pnl: ${pnl:.2f})", flush=True)
        except Exception as e:
            print(f"[BotManager] Failed to close positions for {bot_id}: {e}", flush=True)

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

        return close_result

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
        """On startup, restart bots that were running before server shutdown.

        Retries up to 3 times with delays between bots to avoid overwhelming MetaAPI.
        """
        # Wait a bit for the server to fully start before reconnecting bots
        await asyncio.sleep(5)
        print("[BotManager] Checking for bots to auto-restart...", flush=True)

        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(Bot).where(Bot.status.in_(["running", "starting"]))
                )
                bots = result.scalars().all()
        except Exception as e:
            print(f"[BotManager] Failed to query bots: {e}", flush=True)
            return

        if not bots:
            print("[BotManager] No bots need restarting.", flush=True)
            return

        print(f"[BotManager] Found {len(bots)} bots to restart", flush=True)

        for bot in bots:
            if bot.id in self._running_bots:
                print(f"[BotManager] Bot {bot.name} already running in memory, skipping", flush=True)
                continue

            success = False
            for attempt in range(3):
                try:
                    print(f"[BotManager] Restarting bot {bot.name} — attempt {attempt + 1}/3", flush=True)
                    await self.start_bot(bot.id, _is_restart=True)
                    print(f"[BotManager] Bot {bot.name} restarted successfully", flush=True)
                    success = True
                    break
                except Exception as e:
                    print(f"[BotManager] Restart attempt {attempt + 1} failed for {bot.name}: {e}", flush=True)
                    if attempt < 2:
                        await asyncio.sleep(10)  # Wait before retry

            if not success:
                async with self._session_factory() as db:
                    result = await db.execute(select(Bot).where(Bot.id == bot.id))
                    b = result.scalar_one_or_none()
                    if b:
                        b.status = "error"
                        b.error_message = "Failed to auto-restart after deploy. Click Start to retry."
                        await db.commit()

            # Delay between bots to avoid MetaAPI rate limits
            await asyncio.sleep(5)

    async def shutdown_all(self) -> None:
        """Stop all running bot tasks on app shutdown.

        Keeps bot status as 'running' in DB so restart_crashed_bots()
        will auto-restart them when the server comes back up.
        """
        self._shutting_down = True
        for bot_id, task in list(self._running_bots.items()):
            try:
                bridge = self._bot_bridges.get(bot_id)
                if bridge:
                    bridge._shutdown = True
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=5)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
            except Exception as e:
                logger.error("Error stopping bot %s during shutdown: %s", bot_id, e)
        self._running_bots.clear()
        self._bot_bridges.clear()
        logger.info("All bot tasks stopped (status kept as 'running' for auto-restart)")
