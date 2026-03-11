"""Built-in technical analysis functions (ta.*) for Pine Script v5."""

from __future__ import annotations

from typing import Any

from ..series import Series, is_na, na_value


def _unwrap(v: Any) -> Any:
    if isinstance(v, Series):
        return v.current
    return v


def _get_history(source: Any, length: int) -> list[float]:
    """Extract the last `length` values from a Series."""
    if isinstance(source, Series):
        return [source[i] for i in range(length - 1, -1, -1)]
    return [_unwrap(source)]


class TAState:
    """Holds stateful computation context for TA indicators across bars."""

    def __init__(self):
        self._ema_state: dict[int, float] = {}
        self._rsi_state: dict[int, dict] = {}

    def reset(self):
        self._ema_state.clear()
        self._rsi_state.clear()


_state = TAState()


def get_ta_state() -> TAState:
    return _state


def ta_sma(source: Any, length: Any) -> float:
    length = int(_unwrap(length))
    if not isinstance(source, Series) or len(source) < length:
        return na_value()
    vals = [source[i] for i in range(length)]
    valid = [v for v in vals if not is_na(v)]
    if len(valid) < length:
        return na_value()
    return sum(valid) / length


def ta_ema(source: Any, length: Any) -> float:
    length = int(_unwrap(length))
    if not isinstance(source, Series):
        return na_value()

    k = 2.0 / (length + 1)
    state_key = id(source) ^ (length * 31)

    current = source.current
    if is_na(current):
        return _state._ema_state.get(state_key, na_value())

    if state_key not in _state._ema_state:
        if len(source) >= length:
            vals = [source[i] for i in range(length)]
            valid = [v for v in vals if not is_na(v)]
            if len(valid) == length:
                _state._ema_state[state_key] = sum(valid) / length
                return _state._ema_state[state_key]
        return na_value()

    prev = _state._ema_state[state_key]
    result = current * k + prev * (1 - k)
    _state._ema_state[state_key] = result
    return result


def ta_rma(source: Any, length: Any) -> float:
    """Rolling Moving Average (Wilder's smoothing), used internally by RSI."""
    length = int(_unwrap(length))
    if not isinstance(source, Series):
        return na_value()

    alpha = 1.0 / length
    state_key = id(source) ^ (length * 37)

    current = source.current
    if is_na(current):
        return _state._ema_state.get(state_key, na_value())

    if state_key not in _state._ema_state:
        if len(source) >= length:
            vals = [source[i] for i in range(length)]
            valid = [v for v in vals if not is_na(v)]
            if len(valid) == length:
                _state._ema_state[state_key] = sum(valid) / length
                return _state._ema_state[state_key]
        return na_value()

    prev = _state._ema_state[state_key]
    result = alpha * current + (1 - alpha) * prev
    _state._ema_state[state_key] = result
    return result


def ta_rsi(source: Any, length: Any) -> float:
    length = int(_unwrap(length))
    if not isinstance(source, Series) or len(source) < 2:
        return na_value()

    state_key = id(source) ^ (length * 41)

    change = source[0] - source[1] if not is_na(source[0]) and not is_na(source[1]) else na_value()
    if is_na(change):
        return na_value()

    gain = max(change, 0.0)
    loss = max(-change, 0.0)

    if state_key not in _state._rsi_state:
        if len(source) < length + 1:
            return na_value()
        gains, losses = [], []
        for i in range(length):
            c = source[i] - source[i + 1]
            if is_na(c):
                return na_value()
            gains.append(max(c, 0.0))
            losses.append(max(-c, 0.0))
        avg_gain = sum(gains) / length
        avg_loss = sum(losses) / length
        _state._rsi_state[state_key] = {"avg_gain": avg_gain, "avg_loss": avg_loss}
    else:
        prev = _state._rsi_state[state_key]
        avg_gain = (prev["avg_gain"] * (length - 1) + gain) / length
        avg_loss = (prev["avg_loss"] * (length - 1) + loss) / length
        _state._rsi_state[state_key] = {"avg_gain": avg_gain, "avg_loss": avg_loss}

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def ta_crossover(a: Any, b: Any) -> bool:
    if not isinstance(a, Series) or not isinstance(b, Series):
        return False
    if len(a) < 2 or len(b) < 2:
        return False
    a0, a1 = a[0], a[1]
    b0, b1 = b[0], b[1]
    if any(is_na(v) for v in (a0, a1, b0, b1)):
        return False
    return a0 > b0 and a1 <= b1


def ta_crossunder(a: Any, b: Any) -> bool:
    if not isinstance(a, Series) or not isinstance(b, Series):
        return False
    if len(a) < 2 or len(b) < 2:
        return False
    a0, a1 = a[0], a[1]
    b0, b1 = b[0], b[1]
    if any(is_na(v) for v in (a0, a1, b0, b1)):
        return False
    return a0 < b0 and a1 >= b1


def ta_highest(source: Any, length: Any) -> float:
    length = int(_unwrap(length))
    if not isinstance(source, Series) or len(source) < length:
        return na_value()
    vals = [source[i] for i in range(length)]
    valid = [v for v in vals if not is_na(v)]
    return max(valid) if valid else na_value()


def ta_lowest(source: Any, length: Any) -> float:
    length = int(_unwrap(length))
    if not isinstance(source, Series) or len(source) < length:
        return na_value()
    vals = [source[i] for i in range(length)]
    valid = [v for v in vals if not is_na(v)]
    return min(valid) if valid else na_value()


def ta_atr(length: Any) -> float:
    """ATR requires high, low, close series — injected at registration time."""
    raise NotImplementedError("ta.atr requires engine context; use the wrapper")


def ta_change(source: Any, length: Any = 1) -> float:
    length = int(_unwrap(length))
    if not isinstance(source, Series) or len(source) <= length:
        return na_value()
    curr = source[0]
    prev = source[length]
    if is_na(curr) or is_na(prev):
        return na_value()
    return curr - prev


def ta_stdev(source: Any, length: Any) -> float:
    length = int(_unwrap(length))
    if not isinstance(source, Series) or len(source) < length:
        return na_value()
    vals = [source[i] for i in range(length)]
    valid = [v for v in vals if not is_na(v)]
    if len(valid) < length:
        return na_value()
    mean = sum(valid) / len(valid)
    variance = sum((v - mean) ** 2 for v in valid) / len(valid)
    return variance ** 0.5


def ta_tr(high_s: Series, low_s: Series, close_s: Series) -> float:
    """True Range."""
    if len(close_s) < 2:
        h, l = high_s[0], low_s[0]
        if is_na(h) or is_na(l):
            return na_value()
        return h - l
    h = high_s[0]
    l = low_s[0]
    pc = close_s[1]
    if any(is_na(v) for v in (h, l, pc)):
        return na_value()
    return max(h - l, abs(h - pc), abs(l - pc))


def ta_macd(source: Any, fastlen: Any = 12, slowlen: Any = 26, siglen: Any = 9):
    """Returns (macd_line, signal, histogram) as a tuple."""
    fast = ta_ema(source, fastlen)
    slow = ta_ema(source, slowlen)
    if is_na(fast) or is_na(slow):
        return na_value(), na_value(), na_value()
    macd_line = fast - slow
    return macd_line, na_value(), na_value()


def register(interpreter) -> None:
    funcs = {
        "ta.sma": ta_sma,
        "ta.ema": ta_ema,
        "ta.rma": ta_rma,
        "ta.rsi": ta_rsi,
        "ta.crossover": ta_crossover,
        "ta.crossunder": ta_crossunder,
        "ta.highest": ta_highest,
        "ta.lowest": ta_lowest,
        "ta.change": ta_change,
        "ta.stdev": ta_stdev,
    }
    for name, fn in funcs.items():
        interpreter.register_builtin(name, fn)


def register_ohlcv(interpreter, high_s: Series, low_s: Series, close_s: Series) -> None:
    """Register indicators that depend on OHLCV series (ta.tr, ta.atr)."""

    _atr_state: dict[int, float] = {}

    def _ta_tr_wrapper() -> float:
        return ta_tr(high_s, low_s, close_s)

    def _ta_atr_wrapper(length: Any) -> float:
        length = int(_unwrap(length))
        tr_val = ta_tr(high_s, low_s, close_s)
        if is_na(tr_val):
            return na_value()

        alpha = 1.0 / length
        state_key = length

        if state_key not in _atr_state:
            if len(close_s) < length + 1:
                return na_value()
            tr_vals = []
            for i in range(length):
                h, l = high_s[i], low_s[i]
                if len(close_s) > i + 1:
                    pc = close_s[i + 1]
                else:
                    pc = na_value()
                if any(is_na(v) for v in (h, l, pc)):
                    return na_value()
                tr_vals.append(max(h - l, abs(h - pc), abs(l - pc)))
            _atr_state[state_key] = sum(tr_vals) / length
            return _atr_state[state_key]

        prev = _atr_state[state_key]
        result = alpha * tr_val + (1 - alpha) * prev
        _atr_state[state_key] = result
        return result

    interpreter.register_builtin("ta.tr", _ta_tr_wrapper)
    interpreter.register_builtin("ta.atr", _ta_atr_wrapper)
