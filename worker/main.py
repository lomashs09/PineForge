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
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Union

from dotenv import load_dotenv
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

# Add parent dir to path so we can import pineforge
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()

from worker.config import WorkerConfig
from worker.executor import DirectExecutor
from worker.account_manager import AccountManager

# Bot logging (reuse API's logger infrastructure)
from api.utils.bot_logger import BotDatabaseHandler, BotPrintCapture

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
    """Manages bot lifecycle on a Windows machine with direct MT5 access.

    Supports multiple broker accounts — each gets its own MT5 terminal instance.
    """

    def __init__(self, config: WorkerConfig, session_factory: async_sessionmaker,
                 jwt_secret: str = ""):
        self.config = config
        self.session_factory = session_factory
        self.jwt_secret = jwt_secret
        # In-process mode: asyncio.Task, subprocess mode: asyncio.subprocess.Process
        self._running: Dict[str, Union[asyncio.Task, asyncio.subprocess.Process]] = {}
        self._bot_loggers: Dict[str, BotDatabaseHandler] = {}
        self._bot_retries: Dict[str, int] = {}  # bot_id -> crash count
        self._account_mgr = AccountManager()
        self._shutdown_event = asyncio.Event()

    async def run(self):
        """Main loop: poll DB for bot commands."""
        logger.info("Worker %s starting (poll=%ds, max_bots=%d)",
                     self.config.worker_id, self.config.poll_interval, self.config.max_bots)
        logger.info("Multi-account mode — each broker account gets its own MT5 terminal")
        if self.config.use_subprocess:
            logger.info("Subprocess isolation ENABLED — each bot runs in its own process")

        # Restart bots that were running before worker restart
        await self._restart_running_bots()

        # Poll loop
        poll_count = 0
        while not self._shutdown_event.is_set():
            try:
                await self._poll_commands()
                poll_count += 1
                if poll_count % 12 == 1:  # Log every ~60s
                    logger.info("Polling... (running bots: %d)", len(self._running))
            except Exception as e:
                logger.error("Poll error: %s", e, exc_info=True)

            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=self.config.poll_interval)
                break  # Shutdown requested
            except asyncio.TimeoutError:
                pass  # Normal timeout, continue polling

        await self._graceful_shutdown()

    async def _poll_commands(self):
        """Check DB for start/stop requests and monitor subprocess health."""
        # Check subprocess health first
        if self.config.use_subprocess:
            await self._check_subprocess_health()

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

        # Decrypt MT5 password
        mt5_password = ""
        if account.mt5_password_encrypted and self.jwt_secret:
            from api.utils.crypto import decrypt_password
            try:
                mt5_password = decrypt_password(account.mt5_password_encrypted, self.jwt_secret)
            except Exception as e:
                logger.error("Failed to decrypt password for %s: %s", account.mt5_login, e)
                bot.status = "error"
                bot.error_message = f"Failed to decrypt MT5 password: {e}"
                return

        if not mt5_password:
            logger.error("No MT5 password stored for account %s (encrypted=%s, jwt_secret=%s)",
                         account.mt5_login, bool(account.mt5_password_encrypted), bool(self.jwt_secret))
            bot.status = "error"
            bot.error_message = "MT5 password not stored. Please reconnect your broker account."
            return

        logger.info("Password decrypted OK for %s@%s", account.mt5_login, account.mt5_server)

        # Ensure MT5 terminal is running for this account
        try:
            instance = await self._account_mgr.ensure_account_ready(
                account.mt5_login, mt5_password, account.mt5_server
            )
            terminal_path = str(instance.terminal_path)
            logger.info("MT5 terminal ready at %s", terminal_path)
        except Exception as e:
            logger.error("Failed to start MT5 for %s@%s: %s", account.mt5_login, account.mt5_server, e, exc_info=True)
            bot.status = "error"
            bot.error_message = f"Failed to start MT5 for {account.mt5_login}@{account.mt5_server}: {e}"
            return

        # Update status
        bot.status = "running"
        bot.started_at = datetime.now(timezone.utc)
        bot.error_message = None

        bot_id_str = str(bot.id)

        if self.config.use_subprocess:
            # Subprocess mode: spawn bot_runner as separate process
            runner_config = json.dumps({
                "bot_id": bot_id_str,
                "name": bot.name,
                "script_source": script.source,
                "symbol": bot.symbol,
                "timeframe": bot.timeframe,
                "lot_size": float(bot.lot_size),
                "is_live": bot.is_live,
                "poll_interval_seconds": bot.poll_interval_seconds,
                "lookback_bars": bot.lookback_bars,
                "broker_account_id": str(account.id),
                "terminal_path": terminal_path,
                "database_url": self.config.database_url,
                "jwt_secret": self.jwt_secret,
            })
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "worker.bot_runner", runner_config,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(Path(__file__).resolve().parent.parent),
            )
            self._running[bot_id_str] = proc
            # Start log reader task for subprocess stdout
            asyncio.create_task(self._read_subprocess_output(bot_id_str, bot.name, proc))
            logger.info("Started bot %s as subprocess (pid=%d)", bot.name, proc.pid)
        else:
            # In-process mode: asyncio task
            task = asyncio.create_task(
                self._run_bot(bot_id_str, bot.name, script.source,
                              bot.symbol, bot.timeframe, float(bot.lot_size),
                              bot.is_live, bot.poll_interval_seconds,
                              bot.lookback_bars, str(account.id), terminal_path)
            )
            self._running[bot_id_str] = task

    async def _stop_bot(self, bot: Bot, db: AsyncSession):
        """Stop a bot (supports both task and subprocess modes)."""
        bot_id = str(bot.id)
        running = self._running.get(bot_id)

        if running is not None:
            if isinstance(running, asyncio.subprocess.Process):
                # Subprocess mode: send SIGTERM, wait, then SIGKILL
                try:
                    running.terminate()
                    try:
                        await asyncio.wait_for(running.wait(), timeout=10)
                    except asyncio.TimeoutError:
                        running.kill()
                        await running.wait()
                except ProcessLookupError:
                    pass  # Already dead
            else:
                # In-process task mode
                running.cancel()
                try:
                    await asyncio.wait_for(running, timeout=10)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
            self._running.pop(bot_id, None)

        self._bot_retries.pop(bot_id, None)
        bot.status = "stopped"
        bot.stopped_at = datetime.now(timezone.utc)
        bot.error_message = None
        logger.info("Stopped bot %s (%s)", bot.id, bot.name)

    async def _run_bot(self, bot_id: str, name: str, script_source: str,
                       symbol: str, timeframe: str, lot_size: float,
                       is_live: bool, poll_seconds: int, lookback: int,
                       broker_account_id: str, terminal_path: str = ""):
        """Run a single bot using LiveBridge with direct MT5 executor."""
        import uuid as _uuid
        from pineforge.live.bridge import LiveBridge
        from pineforge.live.config import LiveConfig

        logger.info("Bot %s running: %s %s %s (terminal: %s)", name, symbol, timeframe,
                     "LIVE" if is_live else "DRY", terminal_path)

        config = LiveConfig(
            symbol=symbol,
            timeframe=timeframe,
            lot_size=lot_size,
            is_live=is_live,
            poll_interval_seconds=poll_seconds,
            lookback_bars=lookback,
            script_source=script_source,
            mt5_backend="direct",
        )

        bridge = LiveBridge(config)
        bridge._register_signals = False

        # Create executor bound to the specific MT5 terminal for this account
        bridge._direct_executor_cls = lambda sym, live: DirectExecutor(sym, live, terminal_path)
        bridge._terminal_path = terminal_path

        # Set up database logger for this bot
        bot_uuid = _uuid.UUID(bot_id)
        acct_uuid = _uuid.UUID(broker_account_id) if broker_account_id else None
        bot_logger = logging.getLogger(f"bot.{bot_id}")
        bot_logger.setLevel(logging.INFO)
        db_handler = BotDatabaseHandler(bot_uuid, self.session_factory, broker_account_id=acct_uuid)
        bot_logger.addHandler(db_handler)
        db_handler.start()
        self._bot_loggers[bot_id] = db_handler

        # Capture bridge's print() output and route to DB logger
        capture = BotPrintCapture(bot_logger)
        original_stdout = sys.stdout

        try:
            sys.stdout = capture
            await bridge.run()
        except asyncio.CancelledError:
            logger.info("Bot %s cancelled", name)
        except Exception as e:
            logger.error("Bot %s crashed: %s", name, e, exc_info=True)
            bot_logger.error("Bot crashed: %s", str(e)[:500])
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
            sys.stdout = original_stdout
            self._running.pop(bot_id, None)
            # Stop the DB logger
            try:
                await db_handler.stop()
            except Exception:
                pass
            bot_logger.removeHandler(db_handler)
            self._bot_loggers.pop(bot_id, None)
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

    async def _read_subprocess_output(self, bot_id: str, name: str, proc: asyncio.subprocess.Process):
        """Read stdout/stderr from a bot subprocess and log it."""
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                logger.info("[bot:%s] %s", name, line.decode().rstrip())
        except Exception:
            pass
        # Also drain stderr
        try:
            stderr = await proc.stderr.read()
            if stderr:
                for line in stderr.decode().splitlines():
                    logger.error("[bot:%s] %s", name, line)
        except Exception:
            pass

    async def _check_subprocess_health(self):
        """Check if any bot subprocesses have died unexpectedly."""
        dead = []
        for bot_id, proc in list(self._running.items()):
            if not isinstance(proc, asyncio.subprocess.Process):
                continue
            if proc.returncode is not None:
                dead.append((bot_id, proc.returncode))

        for bot_id, returncode in dead:
            self._running.pop(bot_id, None)
            retries = self._bot_retries.get(bot_id, 0)

            if returncode != 0 and retries < self.config.max_retries:
                # Auto-restart: set back to start_requested
                self._bot_retries[bot_id] = retries + 1
                logger.warning("Bot %s subprocess died (code=%d), retry %d/%d — requesting restart",
                               bot_id, returncode, retries + 1, self.config.max_retries)
                try:
                    async with self.session_factory() as db:
                        result = await db.execute(select(Bot).where(Bot.id == bot_id))
                        bot = result.scalar_one_or_none()
                        if bot and bot.status == "running":
                            bot.status = "start_requested"
                            await db.commit()
                except Exception as e:
                    logger.error("Failed to request restart for %s: %s", bot_id, e)
            else:
                # Max retries exceeded or clean exit
                logger.info("Bot %s subprocess exited (code=%d)", bot_id, returncode)
                try:
                    async with self.session_factory() as db:
                        result = await db.execute(select(Bot).where(Bot.id == bot_id))
                        bot = result.scalar_one_or_none()
                        if bot and bot.status == "running":
                            if returncode != 0:
                                bot.status = "error"
                                bot.error_message = f"Process crashed (exit code {returncode}) after {self.config.max_retries} retries"
                            else:
                                bot.status = "stopped"
                            bot.stopped_at = datetime.now(timezone.utc)
                            await db.commit()
                except Exception as e:
                    logger.error("Failed to update status for %s: %s", bot_id, e)
                self._bot_retries.pop(bot_id, None)

    async def _graceful_shutdown(self):
        """Stop all running bots and update their status in DB."""
        logger.info("Graceful shutdown: stopping %d bots", len(self._running))
        for bot_id, running in list(self._running.items()):
            if isinstance(running, asyncio.subprocess.Process):
                try:
                    running.terminate()
                    try:
                        await asyncio.wait_for(running.wait(), timeout=10)
                    except asyncio.TimeoutError:
                        running.kill()
                        await running.wait()
                except ProcessLookupError:
                    pass
            else:
                running.cancel()
                try:
                    await asyncio.wait_for(running, timeout=10)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

        # Mark all running/starting bots as stopped
        try:
            async with self.session_factory() as db:
                result = await db.execute(
                    select(Bot).where(Bot.status.in_(["running", "starting", "start_requested"]))
                )
                bots = result.scalars().all()
                for bot in bots:
                    bot.status = "stopped"
                    bot.stopped_at = datetime.now(timezone.utc)
                await db.commit()
                logger.info("Marked %d bots as stopped", len(bots))
        except Exception as e:
            logger.error("Failed to update bot statuses on shutdown: %s", e)

        await self._account_mgr.shutdown_all()
        logger.info("Shutdown complete")

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
        import ssl as _ssl
        ssl_ctx = _ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = _ssl.CERT_NONE
        engine_kwargs["connect_args"] = {"ssl": ssl_ctx}

    engine = create_async_engine(config.database_url, **engine_kwargs)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Test DB connection
    try:
        async with session_factory() as db:
            result = await db.execute(select(Bot))
            all_bots = result.scalars().all()
            logger.info("DB connection OK — found %d bots total", len(all_bots))
            for b in all_bots:
                logger.info("  Bot: %s status=%s", b.name, b.status)
    except Exception as e:
        logger.error("DB connection FAILED: %s", e)
        return

    jwt_secret = os.getenv("JWT_SECRET_KEY", "")
    worker = BotWorker(config, session_factory, jwt_secret=jwt_secret)

    # Register signal handlers for graceful shutdown
    import signal as _signal

    def _on_shutdown(signum, frame):
        logger.info("Received signal %s — initiating graceful shutdown", signum)
        worker._shutdown_event.set()

    _signal.signal(_signal.SIGINT, _on_shutdown)
    _signal.signal(_signal.SIGTERM, _on_shutdown)

    try:
        await worker.run()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
