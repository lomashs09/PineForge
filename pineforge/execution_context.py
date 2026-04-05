"""ExecutionContext — per-run state container that eliminates global singletons.

Each Engine.run() or LiveBridge session creates its own ExecutionContext,
allowing multiple backtests/bots to run concurrently without state corruption.
"""

from __future__ import annotations

from .builtins.strategy import StrategyContext
from .builtins.ta import TAState
from .builtins.input_funcs import InputStore


class ExecutionContext:
    """Holds all mutable state for one engine/bridge run."""

    def __init__(self):
        self.strategy = StrategyContext()
        self.ta = TAState()
        self.inputs = InputStore()
        self.series_counter: int = 0

    def next_series_id(self) -> int:
        self.series_counter += 1
        return self.series_counter
