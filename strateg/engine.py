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
from .builtins.ta import get_ta_state


class Engine:
    """Runs a Pine Script strategy against OHLCV data."""

    def __init__(
        self,
        initial_capital: float = 10000.0,
        commission: float = 0.0,
        slippage: float = 0.0,
        fill_on: str = "next_open",
    ):
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        self.fill_on = fill_on

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

        interp.load_script(ast)

        total_bars = len(data)

        for i in range(total_bars):
            bar = data[i]

            open_s.push(bar["open"])
            high_s.push(bar["high"])
            low_s.push(bar["low"])
            close_s.push(bar["close"])
            volume_s.push(bar.get("volume", 0))
            hl2_s.push((bar["high"] + bar["low"]) / 2)
            hlc3_s.push((bar["high"] + bar["low"] + bar["close"]) / 3)
            ohlc4_s.push((bar["open"] + bar["high"] + bar["low"] + bar["close"]) / 4)
            bar_index_s.push(i)

            ctx.bar_index = i
            interp.execute_bar(i)

            if i > 0 or self.fill_on == "close":
                broker.process_orders(
                    bar_index=i,
                    open_price=bar["open"],
                    high_price=bar["high"],
                    low_price=bar["low"],
                    close_price=bar["close"],
                    date=bar.get("date"),
                )
            else:
                broker._update_equity(bar["close"])

        if broker.position is not None:
            last_bar = data[total_bars - 1]
            broker._close_position(last_bar["close"], total_bars - 1, last_bar.get("date"))
            broker.equity_curve[-1] = broker.equity

        return compute_results(broker, self.initial_capital, ctx.title)
