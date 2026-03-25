"""Backtesting engine — orchestrates bar-by-bar execution."""

from __future__ import annotations

from typing import Any

from .lexer import Lexer
from .parser import Parser
from .interpreter import Interpreter
from .series import Series, na_value
from .environment import Environment
from .broker import Broker
from .data import DataFeed
from .results import BacktestResult, compute_results
from .builtins import math_funcs, ta as ta_module, input_funcs, strategy as strategy_module
from .builtins.strategy import get_strategy_context
from .builtins.ta import get_ta_state, register_ohlcv


class Engine:
    """Runs a Pine Script strategy against OHLCV data."""

    def __init__(
        self,
        initial_capital: float = 10000.0,
        commission: float = 0.0,
        slippage: float = 0.0,
        fill_on: str = "next_open",
        interval: str = "1d",
    ):
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        self.fill_on = fill_on
        self.interval = interval  # IMP 2: used for Sharpe annualization

    def run(self, script_source: str, data: DataFeed, input_overrides: dict[str, Any] | None = None) -> BacktestResult:
        tokens = Lexer(script_source).tokenize()
        ast = Parser(tokens).parse()

        interp = Interpreter()
        broker = Broker(
            initial_capital=self.initial_capital,
            commission=self.commission,
            slippage=self.slippage,
            fill_on=self.fill_on,
        )

        math_funcs.register(interp)
        ta_module.register(interp)
        input_funcs.register(interp)
        strategy_module.register(interp)

        if input_overrides:
            input_funcs.get_input_store().set_overrides(input_overrides)

        ctx = get_strategy_context()
        ctx.reset()          # BUG 10: clear state from any previous run
        ctx.set_broker(broker)
        ta_state = get_ta_state()
        ta_state.reset()

        open_s = Series()
        high_s = Series()
        low_s = Series()
        close_s = Series()
        volume_s = Series()
        hl2_s = Series()
        hlc3_s = Series()
        ohlc4_s = Series()

        interp.env.define("open", open_s)
        interp.env.define("high", high_s)
        interp.env.define("low", low_s)
        interp.env.define("close", close_s)
        interp.env.define("volume", volume_s)
        interp.env.define("hl2", hl2_s)
        interp.env.define("hlc3", hlc3_s)
        interp.env.define("ohlc4", ohlc4_s)

        bar_index_s = Series()
        interp.env.define("bar_index", bar_index_s)

        register_ohlcv(interp, high_s, low_s, close_s)

        interp.load_script(ast)

        total_bars = len(data)

        for i in range(total_bars):
            bar = data[i]

            # STEP 1: Push current bar data.
            open_s.push(bar["open"])
            high_s.push(bar["high"])
            low_s.push(bar["low"])
            close_s.push(bar["close"])
            volume_s.push(bar.get("volume", 0))
            hl2_s.push((bar["high"] + bar["low"]) / 2)
            hlc3_s.push((bar["high"] + bar["low"] + bar["close"]) / 3)
            ohlc4_s.push((bar["open"] + bar["high"] + bar["low"] + bar["close"]) / 4)
            bar_index_s.push(i)

            # IMP 1+4: expose strategy built-in variables so scripts can read them
            interp.env.define("strategy.position_size", broker.position_size)
            interp.env.define("strategy.opentrades", 1 if broker.position else 0)
            interp.env.define("strategy.closedtrades", len(broker.closed_trades))

            if self.fill_on == "next_open":
                # Process PREVIOUS bar's orders at THIS bar's open (correct TV semantics).
                # Persistent exit orders (stop/limit) are also evaluated against this bar.
                if i > 0:
                    broker.process_orders(
                        bar_index=i,
                        open_price=bar["open"],
                        high_price=bar["high"],
                        low_price=bar["low"],
                        close_price=bar["close"],
                        date=bar.get("date"),
                    )
                else:
                    # Bar 0: no orders to process yet, just record equity snapshot.
                    broker._update_equity(bar["close"])

                ctx.bar_index = i
                interp.execute_bar(i)

                # BUG 5: on bar 0, strategy() has just been called.  Apply script-level
                # commission/slippage/qty defaults to the broker only when the engine
                # was created with its own defaults (0.0) so CLI args take precedence.
                if i == 0:
                    _apply_strategy_defaults_to_broker(ctx, broker, self)

            else:
                # "close" mode: script runs first, then orders fill on same bar's close.
                ctx.bar_index = i
                interp.execute_bar(i)

                if i == 0:
                    _apply_strategy_defaults_to_broker(ctx, broker, self)

                broker.process_orders(
                    bar_index=i,
                    open_price=bar["open"],
                    high_price=bar["high"],
                    low_price=bar["low"],
                    close_price=bar["close"],
                    date=bar.get("date"),
                )

        if broker.position is not None:
            last_bar = data[total_bars - 1]
            pos = broker.position
            raw = last_bar["close"]
            # BUG 6: apply slippage when force-closing at end of backtest
            adj = (raw - broker.slippage) if pos.direction == "long" else (raw + broker.slippage)
            broker._close_position(adj, total_bars - 1, last_bar.get("date"))
            if broker.equity_curve:
                broker.equity_curve[-1] = broker.equity

        return compute_results(broker, self.initial_capital, ctx.title, interval=self.interval)


def _apply_strategy_defaults_to_broker(ctx, broker, engine: "Engine") -> None:
    """BUG 5: Apply strategy() declaration settings to broker as fallback defaults.

    CLI/Engine args override script settings.  Only applied when engine was
    constructed with the default value (0.0 / 0.0) for that setting.
    """
    # commission_value in Pine Script is a percentage (e.g. 0.1 = 0.1%).
    if engine.commission == 0.0 and ctx.commission_value > 0.0:
        broker.commission = ctx.commission_value / 100.0

    # slippage in Pine Script is in price ticks/points (integer).
    if engine.slippage == 0.0 and ctx.slippage > 0:
        broker.slippage = float(ctx.slippage)

    # default_qty_value used by strategy.entry when qty not specified.
    # This is already read from ctx by strategy_entry(), so no broker change needed.
