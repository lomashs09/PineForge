"""Pine Script Series type — the core data structure.

In Pine Script, every value is implicitly a time series indexed by bar.
`close` is a series, `ta.sma(close, 14)` is a series, even `2 + 3` becomes
a series (a constant series). The `[n]` operator accesses historical values.
"""

from __future__ import annotations

import math
from typing import Any


_NA = float("nan")

# Monotonic counter gives each Series a stable unique ID (BUG 11).
# Using id() is fragile because CPython reuses memory addresses.
_series_counter: int = 0

# Maximum data points kept per Series. When exceeded, oldest 10% is trimmed.
MAX_SERIES_DATA = 50_000


def na_value() -> float:
    return _NA


def is_na(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, float) and math.isnan(val):
        return True
    return False


class Series:
    """A time-indexed series of values with history access via `[n]`."""

    __slots__ = ("_data", "_id")

    def __init__(self, initial: Any = None, ctx: Any = None):
        if ctx is not None:
            self._id: int = ctx.next_series_id()
        else:
            global _series_counter
            _series_counter += 1
            self._id: int = _series_counter
        self._data: list[Any] = []
        if initial is not None:
            self._data.append(initial)

    def push(self, value: Any) -> None:
        """Append a new bar's value."""
        self._data.append(value)
        if len(self._data) > MAX_SERIES_DATA:
            # Trim oldest 10% to avoid repeated trimming
            trim = MAX_SERIES_DATA // 10
            self._data = self._data[trim:]

    def set_current(self, value: Any) -> None:
        """Set the current (latest) bar's value."""
        if self._data:
            self._data[-1] = value
        else:
            self._data.append(value)

    @property
    def current(self) -> Any:
        """The current bar's value."""
        if not self._data:
            return na_value()
        return self._data[-1]

    def __getitem__(self, offset: int) -> Any:
        """History access: series[0] is current, series[1] is previous bar, etc."""
        idx = len(self._data) - 1 - offset
        if idx < 0 or idx >= len(self._data):
            return na_value()
        return self._data[idx]

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        if len(self._data) <= 5:
            return f"Series({self._data})"
        return f"Series([...{len(self._data)} bars], current={self.current})"

    def all_values(self) -> list[Any]:
        return list(self._data)
