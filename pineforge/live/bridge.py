"""Live trading bridge — main loop that connects strategy signals to real execution."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..lexer import Lexer
from ..parser import Parser
from ..interpreter import Interpreter
from ..series import Series
from ..broker import Broker
from ..builtins import math_funcs, ta as ta_module, input_funcs, strategy as strategy_module
from ..builtins.ta import register_ohlcv
from ..execution_context import ExecutionContext

from .config import LiveConfig
from .feed import fetch_candles, detect_new_bar, get_latest_closed_bar_time
from .executor import Executor
from .risk import RiskManager

logger = logging.getLogger("pineforge.live.bridge")


class LiveBridge:
    """Runs a Pine Script strategy live, executing signals on a real broker."""

    def __init__(self, config: LiveConfig):
        self.config = config
        self.risk = RiskManager(
            risk_per_trade_pct=config.risk_per_trade_pct,
            max_daily_loss_pct=config.max_daily_loss_pct,
            max_open_positions=config.max_open_positions,
            cooldown_seconds=config.cooldown_seconds,
            max_lot_size=config.max_lot_size,
        )
        self._shutdown = False
        self._register_signals = True  # Set to False when running inside BotManager
        self._last_bar_time: str | None = None
        self._pending_signal: str | None = None
        self._interpreter: Interpreter | None = None
        self._broker: Broker | None = None
        self._script_ast = None
        self._start_time: datetime | None = None
        self._poll_count = 0
        self._last_heartbeat: datetime | None = None

        # Built-in series
        self._open_s = Series()
        self._high_s = Series()
        self._low_s = Series()
        self._close_s = Series()
        self._volume_s = Series()
        self._hl2_s = Series()
        self._hlc3_s = Series()
        self._ohlc4_s = Series()
        self._bar_index_s = Series()
        self._bar_count = 0
        self._connector = None  # Set when using bridge backend
        self._ectx: ExecutionContext | None = None
        self._print_fn = None  # Set by BotManager for per-bot output isolation

    def _print(self, *args, **kwargs):
        """Print that routes to per-bot logger when running under BotManager."""
        if self._print_fn:
            self._print_fn(*args)
        else:
            print(*args, flush=True, **kwargs)

    def _init_interpreter(self):
        """Parse the script and set up the interpreter."""
        if self.config.script_source:
            source = self.config.script_source
        else:
            source = Path(self.config.script_path).read_text()
        tokens = Lexer(source).tokenize()
        self._script_ast = Parser(tokens).parse()

        # Each live session gets its own ExecutionContext
        self._ectx = ExecutionContext()

        interp = Interpreter()
        self._broker = Broker(initial_capital=10000.0, fill_on="close")

        math_funcs.register(interp)
        ta_module.register(interp, ctx=self._ectx)
        input_funcs.register(interp, ctx=self._ectx)
        strategy_module.register(interp, ctx=self._ectx)

        self._ectx.strategy.set_broker(self._broker)

        interp.env.define("open", self._open_s)
        interp.env.define("high", self._high_s)
        interp.env.define("low", self._low_s)
        interp.env.define("close", self._close_s)
        interp.env.define("volume", self._volume_s)
        interp.env.define("hl2", self._hl2_s)
        interp.env.define("hlc3", self._hlc3_s)
        interp.env.define("ohlc4", self._ohlc4_s)
        interp.env.define("bar_index", self._bar_index_s)

        register_ohlcv(interp, self._high_s, self._low_s, self._close_s, ctx=self._ectx)

        interp.load_script(self._script_ast)
        self._interpreter = interp

    def _feed_bar(self, bar: dict[str, Any]):
        """Push one bar through the interpreter and detect signals."""
        o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
        v = bar.get("volume", 0)

        self._open_s.push(o)
        self._high_s.push(h)
        self._low_s.push(l)
        self._close_s.push(c)
        self._volume_s.push(v)
        self._hl2_s.push((h + l) / 2)
        self._hlc3_s.push((h + l + c) / 3)
        self._ohlc4_s.push((o + h + l + c) / 4)
        self._bar_index_s.push(self._bar_count)

        self._ectx.strategy.bar_index = self._bar_count

        self._broker.pending_orders.clear()
        self._interpreter.execute_bar(self._bar_count)
        self._bar_count += 1

    def _detect_signal(self) -> str | None:
        """Check what the strategy wants to do after the latest bar.

        Returns "entry_long", "entry_short", "close", or None.
        Entries take priority over closes because in Pine Script an entry in
        the opposite direction automatically closes the current position.
        """
        if not self._broker.pending_orders:
            return None

        has_close = False
        for order in self._broker.pending_orders:
            if order.action == "entry":
                return "entry_long" if order.direction == "long" else "entry_short"
            if order.action in ("close", "close_all"):
                has_close = True

        return "close" if has_close else None

    async def run(self):
        """Main live trading loop."""
        cfg = self.config
        mode_str = "LIVE" if cfg.is_live else "DRY RUN"

        self._print("=" * 60)
        self._print(f"  PineForge Live Trading Bridge ({mode_str})")
        self._print("=" * 60)
        script_label = Path(cfg.script_path).name if cfg.script_path else "API Script"
        self._print(f"  Script:    {script_label}")
        self._print(f"  Symbol:    {cfg.symbol}")
        self._print(f"  Timeframe: {cfg.timeframe}")
        self._print(f"  Lot size:  {cfg.lot_size}")
        self._print(f"  Poll:      every {cfg.poll_interval_seconds}s")
        self._print(f"  Backend:   {cfg.mt5_backend}")
        if not cfg.is_live:
            print(flush=True)
            self._print("  ** DRY RUN — no real orders will be placed **")
            self._print("  ** Add --live flag to enable real trading **")
        self._print("=" * 60)
        print(flush=True)

        # ── Connect to MT5 (MetaAPI or self-hosted bridge) ────────────
        account = None  # Only used by MetaAPI for candle fetching
        connection = None  # MetaAPI RPC connection
        connector = None  # Self-hosted bridge connector

        if cfg.mt5_backend == "direct":
            # Direct MT5 access — worker sets _direct_executor_cls
            self._print("Using direct MT5 terminal connection...")
            executor_cls = getattr(self, '_direct_executor_cls', None)
            if executor_cls is None:
                raise RuntimeError("mt5_backend=direct but no _direct_executor_cls set. Run via worker.")
            executor = executor_cls(cfg.symbol, cfg.is_live)

            acct_info = await executor.get_account_info()
            if acct_info:
                balance = acct_info.get("balance", 0)
                self._print(f"Account balance: {acct_info.get('currency', 'USD')} {balance:.2f}")
                self.risk.reset_daily(balance)
            print(flush=True)

        elif cfg.mt5_backend == "bridge":
            from .connector import create_connector
            from .connector_executor import ConnectorExecutor

            self._print(f"Connecting to MT5 bridge at {cfg.mt5_bridge_url}...")
            connector = create_connector(
                backend="bridge",
                bridge_url=cfg.mt5_bridge_url,
            )
            await connector.connect()
            self._print("Connected to MT5 via self-hosted bridge.\n")

            executor = ConnectorExecutor(connector, cfg.symbol, cfg.is_live)
        else:
            from metaapi_cloud_sdk import MetaApi

            self._print("Connecting to MetaAPI...")
            api = MetaApi(token=cfg.metaapi_token)
            account = await api.metatrader_account_api.get_account(cfg.metaapi_account_id)

            self._print(f"Account state: {account.state}, connection: {account.connection_status}")

            if account.state not in ("DEPLOYING", "DEPLOYED"):
                try:
                    self._print("Deploying MT5 account...")
                    await account.deploy()
                    self._print("Waiting for deploy to complete...")
                    await account.wait_deployed(timeout_in_seconds=120)
                except Exception as e:
                    self._print(f"Deploy note: {e}")
                    self._print("Continuing — account may already be provisioned...")

            self._print("Waiting for MT5 connection...")
            for attempt in range(3):
                try:
                    await account.wait_connected(timeout_in_seconds=120)
                    break
                except Exception as e:
                    self._print(f"Connection attempt {attempt+1}/3 timeout: {e}")
                    if attempt < 2:
                        self._print("Reloading account and retrying...")
                        account = await api.metatrader_account_api.get_account(cfg.metaapi_account_id)
                        self._print(f"Account state: {account.state}, connection: {account.connection_status}")
                        if account.state not in ("DEPLOYING", "DEPLOYED"):
                            await account.deploy()
                    else:
                        raise

            connection = account.get_rpc_connection()
            await connection.connect()
            await connection.wait_synchronized(timeout_in_seconds=120)
            self._print("Connected to MT5 account.\n")

            executor = Executor(connection, cfg.symbol, cfg.is_live)

        # Propagate per-bot print function to executor for output isolation
        if hasattr(executor, '_print_fn') and self._print_fn:
            executor._print_fn = self._print_fn

        acct_info = await executor.get_account_info()
        if acct_info:
            balance = acct_info.get("balance", 0)
            self._print(f"Account balance: {acct_info.get('currency', 'USD')} {balance:.2f}")
            self.risk.reset_daily(balance)

        self._init_interpreter()

        # ── Fetch warmup bars ─────────────────────────────────────────
        self._print(f"Fetching {cfg.lookback_bars} historical bars for warmup...")
        if cfg.mt5_backend == "direct":
            # Fetch candles via the executor's MT5 connection
            bars = await self._fetch_direct_candles(cfg.symbol, cfg.timeframe, cfg.lookback_bars)
        elif connector:
            raw = await connector.get_candles(cfg.symbol, cfg.timeframe, cfg.lookback_bars)
            bars = raw
        else:
            bars = await fetch_candles(account, cfg.symbol, cfg.timeframe, cfg.lookback_bars)
        if len(bars) < 10:
            self._print(f"Error: only got {len(bars)} bars. Check symbol/timeframe.", file=sys.stderr)
            return

        for bar in bars[:-1]:
            self._feed_bar(bar)
        self._last_bar_time = get_latest_closed_bar_time(bars)
        self._broker.pending_orders.clear()
        self._pending_signal = None

        self._print(f"Warmup complete: {self._bar_count} bars loaded.")
        self._print(f"Listening for new {cfg.timeframe} bars on {cfg.symbol}...")
        self._print(f"  Execution mode: NEXT BAR OPEN (signal queued, executed on next bar)\n")

        if self._register_signals:
            self._setup_signal_handlers()
        self._start_time = datetime.now(timezone.utc)
        self._last_heartbeat = self._start_time

        self._connector = connector  # Store for _poll_cycle candle fetching
        self._account = account  # Store for reconnection
        self._connection = connection
        self._executor = executor
        self._consecutive_errors = 0

        while not self._shutdown:
            try:
                await self._poll_cycle(self._account, self._executor, cfg)
                self._consecutive_errors = 0  # Reset on success
            except KeyboardInterrupt:
                break
            except Exception as e:
                self._consecutive_errors += 1
                logger.error("Error in poll cycle (%d consecutive): %s",
                             self._consecutive_errors, e, exc_info=True)
                self._print(f"  [ERROR] ({self._consecutive_errors}x) {e}")

                # After 3 consecutive errors, try to reconnect
                if self._consecutive_errors >= 3 and cfg.mt5_backend == "metaapi":
                    self._print("  Multiple failures — attempting reconnect...")
                    try:
                        await self._reconnect_metaapi(cfg)
                        self._consecutive_errors = 0
                        self._print("  Reconnected successfully!")
                    except Exception as re:
                        self._print(f"  Reconnect failed: {re}")
                        # Wait longer before next attempt
                        await asyncio.sleep(30)
                        continue

            self._poll_count += 1
            self._print_heartbeat_if_due()
            await asyncio.sleep(cfg.poll_interval_seconds)

        self._print("\nShutting down...")
        if connector:
            await connector.disconnect()
        elif self._connection:
            try:
                await self._connection.close()
            except Exception:
                pass
        self._print("Disconnected. Goodbye.")

    async def _reconnect_metaapi(self, cfg):
        """Reconnect to MetaAPI when the account gets undeployed/disconnected."""
        from metaapi_cloud_sdk import MetaApi

        # Close old connection
        if self._connection:
            try:
                await self._connection.close()
            except Exception:
                pass

        self._print("  Reconnecting to MetaAPI...")
        api = MetaApi(token=cfg.metaapi_token)
        account = await api.metatrader_account_api.get_account(cfg.metaapi_account_id)
        self._print(f"  Account state: {account.state}")

        # Redeploy if needed
        if account.state not in ("DEPLOYING", "DEPLOYED"):
            self._print("  Redeploying account...")
            await account.deploy()

        await account.wait_connected(timeout_in_seconds=120)

        connection = account.get_rpc_connection()
        await connection.connect()
        await connection.wait_synchronized(timeout_in_seconds=120)

        # Update stored references
        self._account = account
        self._connection = connection
        self._executor = Executor(connection, cfg.symbol, cfg.is_live)
        if self._print_fn:
            self._executor._print_fn = self._print_fn

        self._print("  Reconnected to MT5 account.")

    async def _fetch_direct_candles(self, symbol, timeframe, count):
        """Fetch candles using the direct MT5 terminal connection."""
        import asyncio
        from concurrent.futures import ThreadPoolExecutor
        from datetime import datetime as dt, timezone as tz

        terminal_path = getattr(self, '_terminal_path', '')

        def _get():
            import MetaTrader5 as _mt5
            if terminal_path:
                _mt5.initialize(path=terminal_path)
            tf_map = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
            tf = tf_map.get(timeframe)
            if tf is None:
                return []
            sym_info = _mt5.symbol_info(symbol)
            if sym_info and not sym_info.visible:
                _mt5.symbol_select(symbol, True)
            rates = _mt5.copy_rates_from_pos(symbol, tf, 0, count)
            if rates is None or len(rates) == 0:
                return []
            return [
                {"open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
                 "close": float(r[4]), "volume": int(r[5]),
                 "date": dt.fromtimestamp(r[0], tz=tz.utc).isoformat()}
                for r in rates
            ]

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _get)

    def _print_heartbeat_if_due(self):
        """Print a status line every hour so you can verify the server is alive."""
        now = datetime.now(timezone.utc)
        if (now - self._last_heartbeat).total_seconds() < 3600:
            return
        self._last_heartbeat = now
        uptime = now - self._start_time
        hours = int(uptime.total_seconds() // 3600)
        minutes = int((uptime.total_seconds() % 3600) // 60)
        ts = now.strftime("%Y-%m-%d %H:%M:%S UTC")
        self._print(f"[{ts}] HEARTBEAT | uptime: {hours}h {minutes}m | polls: {self._poll_count} | "
                    f"bars processed: {self._bar_count}")

    async def _poll_cycle(self, account, executor, cfg: LiveConfig):
        """One iteration of the main loop.

        Uses "next open" execution: when a new bar arrives, first execute the
        signal from the *previous* bar (at the current bar's opening price),
        then compute the new signal and save it for the next bar.  This matches
        the default backtest fill mode (fill_on="next_open") and avoids
        same-bar stop-outs that kill profitability.
        """
        if cfg.mt5_backend == "direct":
            bars = await self._fetch_direct_candles(cfg.symbol, cfg.timeframe, cfg.lookback_bars)
        elif self._connector:
            bars = await self._connector.get_candles(cfg.symbol, cfg.timeframe, cfg.lookback_bars)
        else:
            bars = await fetch_candles(account, cfg.symbol, cfg.timeframe, cfg.lookback_bars)
        if not bars:
            return

        if not detect_new_bar(bars, self._last_bar_time):
            return

        new_bar_time = get_latest_closed_bar_time(bars)
        latest_closed = bars[-2]  # most recent fully closed bar

        self._last_bar_time = new_bar_time
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self._print(f"[{now}] New bar: O={latest_closed['open']:.2f} H={latest_closed['high']:.2f} "
                    f"L={latest_closed['low']:.2f} C={latest_closed['close']:.2f}")

        # --- Step 1: Execute the PREVIOUS bar's signal at this bar's open ---
        if self._pending_signal is not None:
            self._print(f"  Executing queued signal: {self._pending_signal.upper()}")
            await self._execute_signal(self._pending_signal, executor, cfg)
            self._pending_signal = None

        # --- Step 2: Feed this bar and compute the NEW signal (saved for next bar) ---
        self._feed_bar(latest_closed)
        signal = self._detect_signal()

        if signal is None:
            self._print("  No new signal.")
        else:
            self._print(f"  Signal queued for next bar: {signal.upper()}")
            self._pending_signal = signal

    async def _execute_signal(self, signal: str, executor: Executor, cfg: LiveConfig):
        """Place orders for a previously queued signal."""
        positions = await executor.get_positions()
        has_position = len(positions) > 0

        if signal == "entry_long":
            if has_position:
                pos_type = positions[0].get("type", "")
                if pos_type == "POSITION_TYPE_SELL":
                    self._print("  Flipping: closing SHORT -> opening LONG")
                    await executor.close_all()
                else:
                    self._print("  Already LONG, skipping entry.")
                    return

            allowed, reason = self.risk.check_can_trade(0)
            if not allowed:
                self._print(f"  Risk blocked: {reason}")
                return

            result = await executor.open_buy(cfg.lot_size)
            if result:
                self.risk.record_trade_opened()

        elif signal == "entry_short":
            if has_position:
                pos_type = positions[0].get("type", "")
                if pos_type == "POSITION_TYPE_BUY":
                    self._print("  Flipping: closing LONG -> opening SHORT")
                    await executor.close_all()
                else:
                    self._print("  Already SHORT, skipping entry.")
                    return

            allowed, reason = self.risk.check_can_trade(0)
            if not allowed:
                self._print(f"  Risk blocked: {reason}")
                return

            result = await executor.open_sell(cfg.lot_size)
            if result:
                self.risk.record_trade_opened()

        elif signal == "close":
            if not has_position and cfg.is_live:
                self._print("  No position to close.")
                return
            await executor.close_all()

    def _setup_signal_handlers(self):
        """Handle Ctrl+C gracefully."""
        def _handler(signum, frame):
            self._print("\n\nReceived shutdown signal...")
            self._shutdown = True
        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)


async def run_live(config: LiveConfig):
    """Entry point for the live trading bridge."""
    bridge = LiveBridge(config)
    await bridge.run()
