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
from ..builtins.strategy import get_strategy_context
from ..builtins.ta import get_ta_state, register_ohlcv

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

    def _init_interpreter(self):
        """Parse the script and set up the interpreter."""
        source = Path(self.config.script_path).read_text()
        tokens = Lexer(source).tokenize()
        self._script_ast = Parser(tokens).parse()

        interp = Interpreter()
        self._broker = Broker(initial_capital=10000.0, fill_on="close")

        math_funcs.register(interp)
        ta_module.register(interp)
        input_funcs.register(interp)
        strategy_module.register(interp)

        ctx = get_strategy_context()
        ctx.set_broker(self._broker)
        get_ta_state().reset()

        interp.env.define("open", self._open_s)
        interp.env.define("high", self._high_s)
        interp.env.define("low", self._low_s)
        interp.env.define("close", self._close_s)
        interp.env.define("volume", self._volume_s)
        interp.env.define("hl2", self._hl2_s)
        interp.env.define("hlc3", self._hlc3_s)
        interp.env.define("ohlc4", self._ohlc4_s)
        interp.env.define("bar_index", self._bar_index_s)

        register_ohlcv(interp, self._high_s, self._low_s, self._close_s)

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

        ctx = get_strategy_context()
        ctx.bar_index = self._bar_count

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
        from metaapi_cloud_sdk import MetaApi

        cfg = self.config
        mode_str = "LIVE" if cfg.is_live else "DRY RUN"

        print("=" * 60, flush=True)
        print(f"  PineForge Live Trading Bridge ({mode_str})", flush=True)
        print("=" * 60, flush=True)
        print(f"  Script:    {Path(cfg.script_path).name}", flush=True)
        print(f"  Symbol:    {cfg.symbol}", flush=True)
        print(f"  Timeframe: {cfg.timeframe}", flush=True)
        print(f"  Lot size:  {cfg.lot_size}", flush=True)
        print(f"  Poll:      every {cfg.poll_interval_seconds}s", flush=True)
        if not cfg.is_live:
            print(flush=True)
            print("  ** DRY RUN — no real orders will be placed **", flush=True)
            print("  ** Add --live flag to enable real trading **", flush=True)
        print("=" * 60, flush=True)
        print(flush=True)

        print("Connecting to MetaAPI...", flush=True)
        api = MetaApi(token=cfg.metaapi_token)
        account = await api.metatrader_account_api.get_account(cfg.metaapi_account_id)

        print(f"Account state: {account.state}, connection: {account.connection_status}", flush=True)

        if account.state not in ("DEPLOYING", "DEPLOYED"):
            try:
                print("Deploying MT5 account...", flush=True)
                await account.deploy()
            except Exception as e:
                print(f"Deploy note: {e}", flush=True)
                print("Continuing — account may already be provisioned...", flush=True)

        print("Waiting for MT5 connection...", flush=True)
        try:
            await account.wait_connected(timeout_in_seconds=60)
        except Exception as e:
            print(f"Connection timeout: {e}", flush=True)
            print("Retrying with account reload...", flush=True)
            account = await api.metatrader_account_api.get_account(cfg.metaapi_account_id)
            print(f"Account state: {account.state}, connection: {account.connection_status}", flush=True)
            await account.wait_connected(timeout_in_seconds=120)

        connection = account.get_rpc_connection()
        await connection.connect()
        await connection.wait_synchronized(timeout_in_seconds=120)
        print("Connected to MT5 account.\n", flush=True)

        executor = Executor(connection, cfg.symbol, cfg.is_live)

        acct_info = await executor.get_account_info()
        if acct_info:
            balance = acct_info.get("balance", 0)
            print(f"Account balance: {acct_info.get('currency', 'USD')} {balance:.2f}", flush=True)
            self.risk.reset_daily(balance)
        print(flush=True)

        self._init_interpreter()

        print(f"Fetching {cfg.lookback_bars} historical bars for warmup...", flush=True)
        bars = await fetch_candles(account, cfg.symbol, cfg.timeframe, cfg.lookback_bars)
        if len(bars) < 10:
            print(f"Error: only got {len(bars)} bars. Check symbol/timeframe.", file=sys.stderr, flush=True)
            return

        for bar in bars[:-1]:
            self._feed_bar(bar)
        self._last_bar_time = get_latest_closed_bar_time(bars)
        self._broker.pending_orders.clear()
        self._pending_signal = None

        print(f"Warmup complete: {self._bar_count} bars loaded.", flush=True)
        print(f"Listening for new {cfg.timeframe} bars on {cfg.symbol}...", flush=True)
        print(f"  Execution mode: NEXT BAR OPEN (signal queued, executed on next bar)\n", flush=True)

        self._setup_signal_handlers()
        self._start_time = datetime.now(timezone.utc)
        self._last_heartbeat = self._start_time

        while not self._shutdown:
            try:
                await self._poll_cycle(account, executor, cfg)
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error("Error in poll cycle: %s", e, exc_info=True)
                print(f"  [ERROR] {e}", flush=True)

            self._poll_count += 1
            self._print_heartbeat_if_due()
            await asyncio.sleep(cfg.poll_interval_seconds)

        print("\nShutting down...", flush=True)
        await connection.close()
        print("Disconnected. Goodbye.", flush=True)

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
        print(f"[{ts}] HEARTBEAT | uptime: {hours}h {minutes}m | polls: {self._poll_count} | "
              f"bars processed: {self._bar_count}", flush=True)

    async def _poll_cycle(self, account, executor: Executor, cfg: LiveConfig):
        """One iteration of the main loop.

        Uses "next open" execution: when a new bar arrives, first execute the
        signal from the *previous* bar (at the current bar's opening price),
        then compute the new signal and save it for the next bar.  This matches
        the default backtest fill mode (fill_on="next_open") and avoids
        same-bar stop-outs that kill profitability.
        """
        bars = await fetch_candles(account, cfg.symbol, cfg.timeframe, cfg.lookback_bars)
        if not bars:
            return

        if not detect_new_bar(bars, self._last_bar_time):
            return

        new_bar_time = get_latest_closed_bar_time(bars)
        latest_closed = bars[-2]  # most recent fully closed bar

        self._last_bar_time = new_bar_time
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] New bar: O={latest_closed['open']:.2f} H={latest_closed['high']:.2f} "
              f"L={latest_closed['low']:.2f} C={latest_closed['close']:.2f}", flush=True)

        # --- Step 1: Execute the PREVIOUS bar's signal at this bar's open ---
        if self._pending_signal is not None:
            print(f"  Executing queued signal: {self._pending_signal.upper()}", flush=True)
            await self._execute_signal(self._pending_signal, executor, cfg)
            self._pending_signal = None

        # --- Step 2: Feed this bar and compute the NEW signal (saved for next bar) ---
        self._feed_bar(latest_closed)
        signal = self._detect_signal()

        if signal is None:
            print("  No new signal.", flush=True)
        else:
            print(f"  Signal queued for next bar: {signal.upper()}", flush=True)
            self._pending_signal = signal

    async def _execute_signal(self, signal: str, executor: Executor, cfg: LiveConfig):
        """Place orders for a previously queued signal."""
        positions = await executor.get_positions()
        has_position = len(positions) > 0

        if signal == "entry_long":
            if has_position:
                pos_type = positions[0].get("type", "")
                if pos_type == "POSITION_TYPE_SELL":
                    print("  Flipping: closing SHORT -> opening LONG", flush=True)
                    await executor.close_all()
                else:
                    print("  Already LONG, skipping entry.", flush=True)
                    return

            allowed, reason = self.risk.check_can_trade(0)
            if not allowed:
                print(f"  Risk blocked: {reason}", flush=True)
                return

            result = await executor.open_buy(cfg.lot_size)
            if result:
                self.risk.record_trade_opened()

        elif signal == "entry_short":
            if has_position:
                pos_type = positions[0].get("type", "")
                if pos_type == "POSITION_TYPE_BUY":
                    print("  Flipping: closing LONG -> opening SHORT", flush=True)
                    await executor.close_all()
                else:
                    print("  Already SHORT, skipping entry.", flush=True)
                    return

            allowed, reason = self.risk.check_can_trade(0)
            if not allowed:
                print(f"  Risk blocked: {reason}", flush=True)
                return

            result = await executor.open_sell(cfg.lot_size)
            if result:
                self.risk.record_trade_opened()

        elif signal == "close":
            if not has_position and cfg.is_live:
                print("  No position to close.", flush=True)
                return
            await executor.close_all()

    def _setup_signal_handlers(self):
        """Handle Ctrl+C gracefully."""
        def _handler(signum, frame):
            print("\n\nReceived shutdown signal...", flush=True)
            self._shutdown = True
        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)


async def run_live(config: LiveConfig):
    """Entry point for the live trading bridge."""
    bridge = LiveBridge(config)
    await bridge.run()
