"""Bot Manager singleton — manages asyncio tasks for LiveBridge instances."""

import asyncio
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from ..models.bot import Bot
from ..models.broker_account import BrokerAccount
from ..utils import metrics

# Lazy import to avoid circular deps — used in _run_bot_wrapper
_market_hours = None
def _get_market_hours():
    global _market_hours
    if _market_hours is None:
        from pineforge.live.market_hours import is_market_likely_closed, get_sleep_duration_for_closed_market
        _market_hours = (is_market_likely_closed, get_sleep_duration_for_closed_market)
    return _market_hours
from ..models.script import Script
from ..utils.bot_logger import BotDatabaseHandler, BotPrintCapture

logger = logging.getLogger(__name__)


def _streaming_listener_enabled() -> bool:
    """Read USE_STREAMING_TRADE_LISTENER each call so a config change in
    .env takes effect on the next bot start without restarting the API.
    Defaults to True — set to '0' / 'false' to fall back to parsed-print only.
    """
    val = os.getenv("USE_STREAMING_TRADE_LISTENER", "1").strip().lower()
    return val in ("1", "true", "yes", "on")


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
        self._start_locks: Dict[uuid.UUID, asyncio.Lock] = {}
        self._shutting_down = False  # Set during app shutdown to skip status updates
        # Phase 3: parallel streaming connections for trade-event listening.
        # Keyed by bot_id, value is the StreamingMetaApiConnectionInstance —
        # held here for the bot's lifetime so the SDK doesn't gc it.
        self._bot_streaming_connections: Dict[uuid.UUID, object] = {}

    @property
    def running_bot_count(self) -> int:
        """Public accessor for the number of currently running bots."""
        return len(self._running_bots)

    async def start_bot(self, bot_id: uuid.UUID, _is_restart: bool = False) -> None:
        """Load bot config from DB and start it as an asyncio task."""
        # Per-bot lock prevents concurrent start of the same bot
        if bot_id not in self._start_locks:
            self._start_locks[bot_id] = asyncio.Lock()

        async with self._start_locks[bot_id]:
            await self._start_bot_inner(bot_id, _is_restart)

    async def _start_bot_inner(self, bot_id: uuid.UUID, _is_restart: bool = False) -> None:
        """Internal bot start logic, called under per-bot lock."""
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
                magic_number=bot.magic_number or 0,
                mt5_backend=self._mt5_backend,
                mt5_bridge_url=self._mt5_bridge_url,
            )

            bridge = LiveBridge(config)
            bridge._register_signals = False  # Don't register OS signal handlers

            # Phase 3: spin up a parallel MetaAPI streaming connection for
            # live trade events. The bridge keeps using its RPC connection
            # for orders (lower latency, simpler request/response model);
            # this streaming side-channel only listens for deal events so
            # bot_trades is updated the moment the broker confirms a fill.
            #
            # Behind a feature flag (USE_STREAMING_TRADE_LISTENER, default
            # on) so we can fall back to parsed-print without a redeploy.
            # Only valid for live + metaapi backends.
            if (
                bot.is_live
                and self._mt5_backend == "metaapi"
                and _streaming_listener_enabled()
                and account.metaapi_account_id
            ):
                try:
                    from .trade_listener import BotTradeListener
                    listener = BotTradeListener(
                        bot_id=bot_id,
                        broker_account_id=account.id,
                        magic_number=bot.magic_number or 0,
                        session_factory=self._session_factory,
                    )
                    # Cold-connecting a streaming connection takes 30-60s.
                    # Don't block start_bot on it — fire-and-forget; the
                    # parsed-print path covers any trades during that window.
                    asyncio.create_task(
                        self._attach_streaming_listener(
                            bot_id, listener, account.metaapi_account_id
                        ),
                        name=f"streaming-listener-{bot_id}",
                    )
                except Exception:
                    logger.exception(
                        "Failed to schedule streaming listener for bot %s — "
                        "falling back to parsed-print only", bot_id,
                    )

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
        """Wrapper that runs the bridge with automatic restart on failure.

        When the bridge exits due to connection drops or transient errors,
        it automatically restarts (up to 10 times) with a fresh bridge.
        Only user-initiated stops and permanent errors exit the loop.
        """
        from pineforge.live.bridge import LiveBridge

        capture = BotPrintCapture(bot_logger)

        def _bot_print(*args, **kwargs):
            """Per-bot print replacement that routes to the bot's own logger."""
            msg = " ".join(str(a) for a in args)
            capture.write(msg + "\n")

        max_retries = 50  # Bots should survive extended outages (market close, broker maintenance)
        retry_count = 0
        user_stopped = False

        try:
            # Update status to running
            async with self._session_factory() as db:
                result = await db.execute(select(Bot).where(Bot.id == bot_id))
                bot = result.scalar_one_or_none()
                if bot:
                    bot.status = "running"
                    await db.commit()
                    metrics.count("bot.started", 1, attributes={"bot_id": str(bot_id)})

            while retry_count <= max_retries and not user_stopped and not self._shutting_down:
                # Inject per-bot print
                bridge._print_fn = _bot_print

                try:
                    await bridge.run()

                    if bridge._shutdown:
                        # User clicked Stop — clean exit
                        user_stopped = True
                        break
                    else:
                        # Check if market is closed — don't count as retry
                        is_closed, reason = _get_market_hours()[0](bridge.config.symbol)
                        if is_closed:
                            sleep_time = _get_market_hours()[1](bridge.config.symbol)
                            _bot_print(f"  Bridge exited during market closure ({reason}). Waiting {sleep_time}s...")
                            logger.info("Bot %s exited during market closure — not counting as retry", bot_id)
                            await asyncio.sleep(sleep_time)
                        else:
                            # Genuine unexpected exit during market hours
                            retry_count += 1
                            logger.info("Bot %s exited unexpectedly (retry %s/%s) — restarting in 10s", bot_id, retry_count, max_retries)
                            _bot_print(f"  [WARN] Bot exited unexpectedly — restarting ({retry_count}/{max_retries})...")
                            await asyncio.sleep(10)

                        # Create fresh bridge with same config
                        old_bridge = bridge
                        bridge = LiveBridge(bridge.config)
                        bridge._register_signals = False
                        self._bot_bridges[bot_id] = bridge
                        del old_bridge  # Help GC

                except asyncio.CancelledError:
                    if self._shutting_down:
                        raise  # App is shutting down — propagate
                    if bridge._shutdown:
                        # User clicked Stop on the dashboard. stop_bot()
                        # sets bridge._shutdown=True, then cancels this
                        # task as a fallback if bridge.run() doesn't
                        # exit within 30s. Without this branch, the
                        # generic retry below would catch the cancel,
                        # replace the bridge with a fresh one (and lose
                        # _shutdown=True), and the bot would keep
                        # running forever — exactly the "Stop doesn't
                        # work" symptom.
                        user_stopped = True
                        break
                    # Otherwise: connection drop / transient cancel — retry.
                    # Check if market is closed — don't count as retry
                    is_closed, reason = _get_market_hours()[0](bridge.config.symbol)
                    if is_closed:
                        sleep_time = _get_market_hours()[1](bridge.config.symbol)
                        _bot_print(f"  Connection cancelled during market closure ({reason}). Waiting {sleep_time}s...")
                        await asyncio.sleep(sleep_time)
                    else:
                        retry_count += 1
                        logger.warning("Bot %s received CancelledError (retry %s/%s) — restarting in 15s",
                                       bot_id, retry_count, max_retries)
                        _bot_print(f"  [WARN] Connection cancelled — restarting ({retry_count}/{max_retries})...")
                        await asyncio.sleep(15)

                    old_bridge = bridge
                    bridge = LiveBridge(bridge.config)
                    bridge._register_signals = False
                    self._bot_bridges[bot_id] = bridge
                    del old_bridge
                except (SyntaxError, NameError, AttributeError) as e:
                    # Permanent errors — don't retry
                    logger.error("Bot %s permanent error: %s", bot_id, e)
                    async with self._session_factory() as db:
                        result = await db.execute(select(Bot).where(Bot.id == bot_id))
                        b = result.scalar_one_or_none()
                        if b:
                            b.status = "error"
                            b.error_message = str(e)[:500]
                            b.stopped_at = datetime.now(timezone.utc)
                            await db.commit()
                    metrics.count("bot.stopped", 1, attributes={"reason": "script_error"})
                    return
                except Exception as e:
                    err_str = str(e).lower()
                    is_permanent = any(x in err_str for x in ["script", "parse", "syntax", "not found", "not accessible"])

                    if is_permanent:
                        logger.error("Bot %s permanent error: %s", bot_id, e)
                        async with self._session_factory() as db:
                            result = await db.execute(select(Bot).where(Bot.id == bot_id))
                            b = result.scalar_one_or_none()
                            if b:
                                b.status = "error"
                                b.error_message = str(e)[:500]
                                b.stopped_at = datetime.now(timezone.utc)
                                await db.commit()
                        metrics.count("bot.stopped", 1, attributes={"reason": "permanent_error"})
                        return
                    else:
                        is_closed, _ = _get_market_hours()[0](bridge.config.symbol)
                        if not is_closed:
                            retry_count += 1
                        sleep_time = _get_market_hours()[1](bridge.config.symbol) if is_closed else 15
                        logger.error("Bot %s crashed: %s (retry %s/%s, market_closed=%s) — restarting in %ds",
                                     bot_id, e, retry_count, max_retries, is_closed, sleep_time)
                        await asyncio.sleep(sleep_time)
                        # Create fresh bridge with same config
                        old_bridge = bridge
                        bridge = LiveBridge(bridge.config)
                        bridge._register_signals = False
                        self._bot_bridges[bot_id] = bridge
                        del old_bridge  # Help GC

            # Exited loop
            if user_stopped:
                async with self._session_factory() as db:
                    result = await db.execute(select(Bot).where(Bot.id == bot_id))
                    bot = result.scalar_one_or_none()
                    if bot:
                        bot.status = "stopped"
                        bot.stopped_at = datetime.now(timezone.utc)
                        await db.commit()
                metrics.count("bot.stopped", 1, attributes={"reason": "user"})
            elif retry_count > max_retries:
                logger.error("Bot %s exhausted %s retries — setting error", bot_id, max_retries)
                async with self._session_factory() as db:
                    result = await db.execute(select(Bot).where(Bot.id == bot_id))
                    bot = result.scalar_one_or_none()
                    if bot:
                        bot.status = "error"
                        bot.error_message = f"Connection lost after {retry_count} reconnect attempts. Click Start to retry."
                        bot.stopped_at = datetime.now(timezone.utc)
                        await db.commit()
                metrics.count("bot.stopped", 1, attributes={"reason": "max_retries"})

        except asyncio.CancelledError:
            if not self._shutting_down:
                async with self._session_factory() as db:
                    result = await db.execute(select(Bot).where(Bot.id == bot_id))
                    bot = result.scalar_one_or_none()
                    if bot:
                        bot.status = "stopped"
                        bot.stopped_at = datetime.now(timezone.utc)
                        await db.commit()

        finally:
            await db_handler.stop()
            bot_logger.removeHandler(db_handler)
            # Tear down the streaming listener connection if one was attached.
            await self._close_streaming_connection(bot_id)
            # Undeploy the MetaAPI account. Pre-one-bot-per-account this
            # was deferred to avoid the $0.13 redeploy fee + 30-60s cold
            # start when sibling bots might restart. Now each account
            # has at most one bot — once that bot is gone, no one is
            # using the account, and keeping it DEPLOYED keeps charging
            # the user the hourly hosting fee. Skipped on app shutdown
            # so the next process boot can resume cleanly.
            if not self._shutting_down:
                await self._undeploy_account(bot_id)
            self._running_bots.pop(bot_id, None)
            self._bot_bridges.pop(bot_id, None)
            self._bot_loggers.pop(bot_id, None)
            self._bot_account_ids.pop(bot_id, None)
            self._start_locks.pop(bot_id, None)

    async def _attach_streaming_listener(
        self,
        bot_id: uuid.UUID,
        listener,
        metaapi_account_id: str,
    ) -> None:
        """Open a streaming connection and attach the trade listener.

        Background task — must not raise into the caller. Cold-connect
        time on MetaAPI is 30-60s; the parsed-print path covers trades
        during that window. Once attached, this connection stays open
        until the bot stops.
        """
        from metaapi_cloud_sdk import MetaApi

        async def _do_attach():
            api = MetaApi(token=self._metaapi_token)
            account = await api.metatrader_account_api.get_account(metaapi_account_id)
            if account.state not in ("DEPLOYED", "DEPLOYING"):
                logger.info(
                    "Streaming listener: deploying account %s for bot %s",
                    metaapi_account_id, bot_id,
                )
                await account.deploy()
            # Cold deploy + broker connection can take 90s+; pass an
            # explicit generous timeout so wait_connected doesn't fire
            # its 60s default before the account finishes coming up.
            await account.wait_connected(timeout_in_seconds=180)

            streaming_conn = account.get_streaming_connection()
            streaming_conn.add_synchronization_listener(listener)
            await streaming_conn.connect()
            # The streaming connection's wait_synchronized takes a
            # SynchronizationOptions dict (timeoutInSeconds key), NOT
            # the timeout_in_seconds kwarg the RPC variant accepts.
            # Passing the kwarg raises TypeError: "got an unexpected
            # keyword argument 'timeout_in_seconds'" — the source of
            # PYTHON-FASTAPI-C in Sentry.
            await streaming_conn.wait_synchronized({"timeoutInSeconds": 180})
            return streaming_conn

        try:
            streaming_conn = await self._with_retries(
                _do_attach,
                label=f"streaming_listener_attach:{metaapi_account_id}",
                max_attempts=3,
            )
        except asyncio.CancelledError:
            raise

        if streaming_conn is None:
            # Retries exhausted. Don't surface as ERROR to Sentry — the
            # parsed-print fallback path still records trades into
            # bot_trades, so the bot keeps working. Log as warning so
            # it shows up in journals but doesn't page anyone.
            logger.warning(
                "Streaming listener attach gave up for bot %s after retries — "
                "bot continues on parsed-print fallback (DB inserts via "
                "BotPrintCapture). MetaAPI account state may have been slow "
                "to come up; reconciliation loop will close any orphan trades.",
                bot_id,
            )
            metrics.count(
                "bot.trade.listener_error",
                1,
                attributes={"hook": "attach", "outcome": "fallback_to_parsed_print"},
            )
            return

        self._bot_streaming_connections[bot_id] = streaming_conn
        logger.info(
            "Streaming trade listener attached for bot %s (magic=%s)",
            bot_id, listener.magic_number,
        )
        metrics.count(
            "bot.trade.listener_attached",
            1,
            attributes={"bot_id": str(bot_id)},
        )

    async def _close_streaming_connection(self, bot_id: uuid.UUID) -> None:
        """Close the parallel streaming connection for a bot, if any."""
        conn = self._bot_streaming_connections.pop(bot_id, None)
        if conn is None:
            return
        try:
            await conn.close()
            logger.info("Streaming listener connection closed for bot %s", bot_id)
        except Exception:
            logger.warning(
                "Failed to close streaming listener connection for bot %s",
                bot_id, exc_info=True,
            )

    async def _with_retries(self, op_factory, *, label: str, max_attempts: int = 3) -> Optional[object]:
        """Run an awaitable factory with exponential backoff.

        Used for MetaAPI deploy/undeploy/close calls that can fail
        transiently (network blip, partial sync, broker rate limit).
        Never swallows CancelledError — only retries on Exception.

        op_factory must be a no-arg callable that returns a fresh
        coroutine each call (so we can re-await after a failure).

        Returns the operation's result, or None after exhausting retries.
        """
        last_exc: Optional[BaseException] = None
        for attempt in range(max_attempts):
            try:
                return await op_factory()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last_exc = e
                wait = 2 ** attempt  # 1s, 2s, 4s
                logger.warning(
                    "%s attempt %d/%d failed: %s — retry in %ds",
                    label, attempt + 1, max_attempts, e, wait,
                )
                try:
                    await asyncio.sleep(wait)
                except asyncio.CancelledError:
                    raise
        logger.error("%s failed after %d attempts: %s", label, max_attempts, last_exc)
        metrics.count(
            "metaapi.op_failed",
            1,
            attributes={"op": label.split(":", 1)[0], "attempts": max_attempts},
        )
        return None

    async def _undeploy_account(self, bot_id: uuid.UUID) -> None:
        """Undeploy the MetaAPI account so it stops consuming resources.

        With the one-bot-per-broker-account constraint, a stopped bot
        always leaves its account fully orphaned, so undeploy is the
        right move on every stop. Wrapped in _with_retries so transient
        MetaAPI errors don't strand a deployed account (which would
        keep racking up Account Hosting charges).
        """
        metaapi_account_id = self._bot_account_ids.get(bot_id)
        if not metaapi_account_id or not self._metaapi_token:
            return

        # Defensive check: in case a future change relaxes the
        # one-bot-per-account constraint, don't undeploy if siblings
        # are still running on the same account.
        other_uses = any(
            aid == metaapi_account_id
            for bid, aid in self._bot_account_ids.items()
            if bid != bot_id and bid in self._running_bots
        )
        if other_uses:
            logger.info(
                "Skipping undeploy for %s — other bots still using it",
                metaapi_account_id,
            )
            return

        from metaapi_cloud_sdk import MetaApi

        async def _do_undeploy():
            api = MetaApi(token=self._metaapi_token)
            account = await api.metatrader_account_api.get_account(metaapi_account_id)
            if account.state in ("DEPLOYING", "DEPLOYED"):
                await account.undeploy()
                logger.info("Undeployed MetaAPI account %s", metaapi_account_id)
                metrics.count(
                    "metaapi.account_undeployed",
                    1,
                    attributes={"account": metaapi_account_id},
                )
            else:
                logger.info(
                    "Account %s already in state %s — skipping undeploy",
                    metaapi_account_id, account.state,
                )
            return account.state

        await self._with_retries(
            _do_undeploy,
            label=f"undeploy_account:{metaapi_account_id}",
            max_attempts=3,
        )

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
                    logger.info("Closed %s positions for bot %s (pnl: $%.2f)", len(positions), bot_id, pnl)
        except Exception as e:
            logger.error("Failed to close positions for %s: %s", bot_id, e)

        # Signal graceful shutdown
        bridge._shutdown = True

        # Wait up to 30 seconds for graceful exit
        try:
            await asyncio.wait_for(task, timeout=30)
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
        logger.info("Checking for bots to auto-restart...")

        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(Bot)
                    .options(selectinload(Bot.broker_account))
                    .where(Bot.status.in_(["running", "starting"]))
                )
                bots = result.scalars().all()
        except Exception as e:
            logger.error("Failed to query bots: %s", e)
            return

        if not bots:
            logger.info("No bots need restarting.")
            return

        logger.info("Found %s bots to restart", len(bots))

        for bot in bots:
            if bot.id in self._running_bots:
                logger.info("Bot %s already running in memory, skipping", bot.name)
                continue

            # Demo bots are seeded fixtures with synthetic metaapi_account_ids
            # (prefix "demo-") used for product walkthroughs / video recording.
            # They have no real MetaAPI account — calling start_bot would 404
            # on deploy, hit the "not found" permanent-error branch in
            # _run_bot_wrapper, and stamp bot.error_message. Leave them be:
            # bot_status_reconcile already exempts them from the orphan check.
            account = bot.broker_account
            if account and (account.metaapi_account_id or "").startswith("demo-"):
                logger.info("Bot %s is a demo bot, skipping auto-restart", bot.name)
                continue

            success = False
            for attempt in range(3):
                try:
                    logger.info("Restarting bot %s — attempt %s/3", bot.name, attempt + 1)
                    await self.start_bot(bot.id, _is_restart=True)
                    logger.info("Bot %s restarted successfully", bot.name)
                    success = True
                    break
                except Exception as e:
                    logger.error("Restart attempt %s failed for %s: %s", attempt + 1, bot.name, e)
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
        # Close any lingering streaming listener connections so MetaAPI
        # doesn't keep them alive across the restart.
        for bot_id in list(self._bot_streaming_connections.keys()):
            await self._close_streaming_connection(bot_id)
        self._running_bots.clear()
        self._bot_bridges.clear()
        logger.info("All bot tasks stopped (status kept as 'running' for auto-restart)")
