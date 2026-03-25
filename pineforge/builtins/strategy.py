"""Built-in strategy functions for Pine Script v5.

These functions interface with the Broker to place orders, manage positions, etc.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..broker import Broker


class StrategyContext:
    """Holds the strategy declaration settings and a reference to the broker."""

    def __init__(self):
        self.title: str = "Strategy"
        self.overlay: bool = True
        self.initial_capital: float = 10000.0
        self.default_qty_type: str = "fixed"
        self.default_qty_value: float = 1.0
        self.commission_type: str = "percent"
        self.commission_value: float = 0.0
        self.slippage: int = 0
        self.currency: str = "USD"
        self.broker: Broker | None = None
        self.bar_index: int = 0

    def set_broker(self, broker: Broker) -> None:
        self.broker = broker

    def reset(self) -> None:
        """Reset to defaults for a fresh backtest run (BUG 10: prevent state leak)."""
        self.title = "Strategy"
        self.overlay = True
        self.initial_capital = 10000.0
        self.default_qty_type = "fixed"
        self.default_qty_value = 1.0
        self.commission_type = "percent"
        self.commission_value = 0.0
        self.slippage = 0
        self.currency = "USD"
        self.broker = None
        self.bar_index = 0


_ctx = StrategyContext()


def get_strategy_context() -> StrategyContext:
    return _ctx


def strategy_declare(title: Any = "Strategy", **kwargs) -> None:
    _ctx.title = str(title)
    _ctx.overlay = kwargs.get("overlay", True)
    if "initial_capital" in kwargs:
        _ctx.initial_capital = float(kwargs["initial_capital"])
    if "default_qty_type" in kwargs:
        _ctx.default_qty_type = str(kwargs["default_qty_type"])
    if "default_qty_value" in kwargs:
        _ctx.default_qty_value = float(kwargs["default_qty_value"])
    if "commission_type" in kwargs:
        _ctx.commission_type = str(kwargs["commission_type"])
    if "commission_value" in kwargs:
        _ctx.commission_value = float(kwargs["commission_value"])
    if "slippage" in kwargs:
        _ctx.slippage = int(kwargs["slippage"])


def strategy_entry(id: Any, direction: Any, qty: Any = None, **_kwargs) -> None:
    if _ctx.broker is None:
        return
    from ..series import Series, is_na
    if isinstance(qty, Series):
        qty = qty.current
    q = float(qty) if qty is not None and not is_na(qty) else _ctx.default_qty_value
    _ctx.broker.submit_entry(str(id), str(direction), q, _ctx.bar_index)


def strategy_close(id: Any, **_kwargs) -> None:
    if _ctx.broker is None:
        return
    _ctx.broker.submit_close(str(id), _ctx.bar_index)


def strategy_close_all(**_kwargs) -> None:
    if _ctx.broker is None:
        return
    _ctx.broker.submit_close_all(_ctx.bar_index)


def strategy_exit(id: Any, from_entry: Any = None, **kwargs) -> None:
    if _ctx.broker is None:
        return
    from ..series import is_na
    _unwrap = lambda v: v if not is_na(v) else None
    _ctx.broker.submit_exit(
        str(id),
        from_entry=str(from_entry) if from_entry else None,
        stop=_unwrap(kwargs.get("stop")),
        limit=_unwrap(kwargs.get("limit")),
        bar_index=_ctx.bar_index,
    )


def strategy_order(id: Any, direction: Any, qty: Any = None, **_kwargs) -> None:
    if _ctx.broker is None:
        return
    from ..series import is_na
    q = float(qty) if qty is not None and not is_na(qty) else _ctx.default_qty_value
    _ctx.broker.submit_entry(str(id), str(direction), q, _ctx.bar_index)


def register(interpreter) -> None:
    funcs = {
        "strategy": strategy_declare,
        "strategy.entry": strategy_entry,
        "strategy.close": strategy_close,
        "strategy.close_all": strategy_close_all,
        "strategy.exit": strategy_exit,
        "strategy.order": strategy_order,
    }
    for name, fn in funcs.items():
        interpreter.register_builtin(name, fn)

    interpreter.env.define("strategy.long", "long")
    interpreter.env.define("strategy.short", "short")
