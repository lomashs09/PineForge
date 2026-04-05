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
        self._macd_line_series: dict[int, "Series"] = {}  # BUG 1: MACD signal
        self._atr_state: dict[int, float] | None = None

    def reset(self):
        self._ema_state.clear()
        self._rsi_state.clear()
        self._macd_line_series.clear()
        if self._atr_state is not None:
            self._atr_state.clear()


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


def _series_key(source: "Series", multiplier: int) -> int:
    """Stable state key using Series._id — avoids id() memory-address collisions (BUG 11)."""
    return getattr(source, "_id", id(source)) * 10007 + multiplier


def ta_ema(source: Any, length: Any) -> float:
    length = int(_unwrap(length))
    if not isinstance(source, Series):
        return na_value()

    k = 2.0 / (length + 1)
    state_key = _series_key(source, length * 31)

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
    state_key = _series_key(source, length * 37)

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

    state_key = _series_key(source, length * 41)

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


def _cross_values(v: Any) -> tuple[Any, Any]:
    """Return (current, prev) for crossover checks — supports Series or scalar (BUG 3)."""
    if isinstance(v, Series):
        return v[0], v[1]
    scalar = _unwrap(v)
    return scalar, scalar  # constant level: current == prev


def ta_crossover(a: Any, b: Any) -> bool:
    """a crossed over b: a[1] <= b[1] and a[0] > b[0].
    Supports Series and scalar values (e.g. ta.crossover(rsi, 70)).
    """
    if not isinstance(a, Series) and not isinstance(b, Series):
        return False
    if isinstance(a, Series) and len(a) < 2:
        return False
    if isinstance(b, Series) and len(b) < 2:
        return False
    a0, a1 = _cross_values(a)
    b0, b1 = _cross_values(b)
    if any(is_na(v) for v in (a0, a1, b0, b1)):
        return False
    return a0 > b0 and a1 <= b1


def ta_crossunder(a: Any, b: Any) -> bool:
    """a crossed under b: a[1] >= b[1] and a[0] < b[0].
    Supports Series and scalar values (e.g. ta.crossunder(rsi, 30)).
    """
    if not isinstance(a, Series) and not isinstance(b, Series):
        return False
    if isinstance(a, Series) and len(a) < 2:
        return False
    if isinstance(b, Series) and len(b) < 2:
        return False
    a0, a1 = _cross_values(a)
    b0, b1 = _cross_values(b)
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
    if len(valid) < 2:  # sample stdev requires at least 2 points
        return na_value()
    mean = sum(valid) / len(valid)
    # TradingView uses sample standard deviation (Bessel's correction ÷N-1) — BUG 4
    variance = sum((v - mean) ** 2 for v in valid) / (len(valid) - 1)
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
    """Returns (macd_line, signal, histogram) as a tuple.

    BUG 1 fix: signal is EMA(macd_line, siglen); histogram = macd_line - signal.
    A persistent Series is maintained per (source, fastlen, slowlen) so the signal
    EMA accumulates correctly across bars.
    """
    fl = int(_unwrap(fastlen))
    sl = int(_unwrap(slowlen))
    sg = int(_unwrap(siglen))

    fast = ta_ema(source, fl)
    slow = ta_ema(source, sl)
    if is_na(fast) or is_na(slow):
        return na_value(), na_value(), na_value()
    macd_line = fast - slow

    # Maintain a per-call Series for the MACD line so signal EMA accumulates.
    src_id = getattr(source, "_id", id(source))
    state_key = src_id * 100003 + fl * 1009 + sl
    if state_key not in _state._macd_line_series:
        _state._macd_line_series[state_key] = Series()
    ml_series = _state._macd_line_series[state_key]
    ml_series.push(macd_line)

    signal = ta_ema(ml_series, sg)
    histogram = (macd_line - signal) if not is_na(signal) else na_value()
    return macd_line, signal, histogram


def register(interpreter, ctx=None) -> None:
    if ctx is not None:
        # Create closure-wrapped functions that use ctx.ta instead of _state
        st = ctx.ta

        def _ema(source: Any, length: Any) -> float:
            length = int(_unwrap(length))
            if not isinstance(source, Series):
                return na_value()
            k = 2.0 / (length + 1)
            state_key = _series_key(source, length * 31)
            current = source.current
            if is_na(current):
                return st._ema_state.get(state_key, na_value())
            if state_key not in st._ema_state:
                if len(source) >= length:
                    vals = [source[i] for i in range(length)]
                    valid = [v for v in vals if not is_na(v)]
                    if len(valid) == length:
                        st._ema_state[state_key] = sum(valid) / length
                        return st._ema_state[state_key]
                return na_value()
            prev = st._ema_state[state_key]
            result = current * k + prev * (1 - k)
            st._ema_state[state_key] = result
            return result

        def _rma(source: Any, length: Any) -> float:
            length = int(_unwrap(length))
            if not isinstance(source, Series):
                return na_value()
            alpha = 1.0 / length
            state_key = _series_key(source, length * 37)
            current = source.current
            if is_na(current):
                return st._ema_state.get(state_key, na_value())
            if state_key not in st._ema_state:
                if len(source) >= length:
                    vals = [source[i] for i in range(length)]
                    valid = [v for v in vals if not is_na(v)]
                    if len(valid) == length:
                        st._ema_state[state_key] = sum(valid) / length
                        return st._ema_state[state_key]
                return na_value()
            prev = st._ema_state[state_key]
            result = alpha * current + (1 - alpha) * prev
            st._ema_state[state_key] = result
            return result

        def _rsi(source: Any, length: Any) -> float:
            length = int(_unwrap(length))
            if not isinstance(source, Series) or len(source) < 2:
                return na_value()
            state_key = _series_key(source, length * 41)
            change = source[0] - source[1] if not is_na(source[0]) and not is_na(source[1]) else na_value()
            if is_na(change):
                return na_value()
            gain = max(change, 0.0)
            loss = max(-change, 0.0)
            if state_key not in st._rsi_state:
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
                st._rsi_state[state_key] = {"avg_gain": avg_gain, "avg_loss": avg_loss}
            else:
                prev = st._rsi_state[state_key]
                avg_gain = (prev["avg_gain"] * (length - 1) + gain) / length
                avg_loss = (prev["avg_loss"] * (length - 1) + loss) / length
                st._rsi_state[state_key] = {"avg_gain": avg_gain, "avg_loss": avg_loss}
            if avg_loss == 0:
                return 100.0
            rs = avg_gain / avg_loss
            return 100.0 - (100.0 / (1.0 + rs))

        def _macd(source: Any, fastlen: Any = 12, slowlen: Any = 26, siglen: Any = 9):
            fl = int(_unwrap(fastlen))
            sl = int(_unwrap(slowlen))
            sg = int(_unwrap(siglen))
            fast = _ema(source, fl)
            slow = _ema(source, sl)
            if is_na(fast) or is_na(slow):
                return na_value(), na_value(), na_value()
            macd_line = fast - slow
            src_id = getattr(source, "_id", id(source))
            state_key = src_id * 100003 + fl * 1009 + sl
            if state_key not in st._macd_line_series:
                st._macd_line_series[state_key] = Series()
            ml_series = st._macd_line_series[state_key]
            ml_series.push(macd_line)
            signal = _ema(ml_series, sg)
            histogram = (macd_line - signal) if not is_na(signal) else na_value()
            return macd_line, signal, histogram

        funcs = {
            "ta.sma": ta_sma,
            "ta.ema": _ema,
            "ta.rma": _rma,
            "ta.rsi": _rsi,
            "ta.crossover": ta_crossover,
            "ta.crossunder": ta_crossunder,
            "ta.highest": ta_highest,
            "ta.lowest": ta_lowest,
            "ta.change": ta_change,
            "ta.stdev": ta_stdev,
            "ta.macd": _macd,
        }
    else:
        # Legacy path: use module-level _state global
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


def register_ohlcv(interpreter, high_s: Series, low_s: Series, close_s: Series,
                    ctx=None) -> None:
    """Register indicators that depend on OHLCV series (ta.tr, ta.atr)."""

    ta_st = ctx.ta if ctx is not None else _state
    ta_st._atr_state = {}

    def _ta_tr_wrapper() -> float:
        return ta_tr(high_s, low_s, close_s)

    def _ta_atr_wrapper(length: Any) -> float:
        length = int(_unwrap(length))
        tr_val = ta_tr(high_s, low_s, close_s)
        if is_na(tr_val):
            return na_value()

        atr_state = ta_st._atr_state
        alpha = 1.0 / length
        state_key = length

        if state_key not in atr_state:
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
            atr_state[state_key] = sum(tr_vals) / length
            return atr_state[state_key]

        prev = atr_state[state_key]
        result = alpha * tr_val + (1 - alpha) * prev
        atr_state[state_key] = result
        return result

    interpreter.register_builtin("ta.tr", _ta_tr_wrapper)
    interpreter.register_builtin("ta.atr", _ta_atr_wrapper)
