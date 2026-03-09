"""Built-in math functions for Pine Script v5."""

from __future__ import annotations

import math as _math
from typing import Any

from ..series import Series, is_na, na_value


def _unwrap(v: Any) -> Any:
    if isinstance(v, Series):
        return v.current
    return v


def pine_abs(x: Any) -> float:
    x = _unwrap(x)
    if is_na(x):
        return na_value()
    return abs(x)


def pine_max(a: Any, b: Any) -> float:
    a, b = _unwrap(a), _unwrap(b)
    if is_na(a):
        return b
    if is_na(b):
        return a
    return max(a, b)


def pine_min(a: Any, b: Any) -> float:
    a, b = _unwrap(a), _unwrap(b)
    if is_na(a):
        return b
    if is_na(b):
        return a
    return min(a, b)


def pine_round(x: Any, precision: Any = 0) -> float:
    x, precision = _unwrap(x), _unwrap(precision)
    if is_na(x):
        return na_value()
    return round(x, int(precision))


def pine_ceil(x: Any) -> float:
    x = _unwrap(x)
    if is_na(x):
        return na_value()
    return _math.ceil(x)


def pine_floor(x: Any) -> float:
    x = _unwrap(x)
    if is_na(x):
        return na_value()
    return _math.floor(x)


def pine_log(x: Any) -> float:
    x = _unwrap(x)
    if is_na(x) or x <= 0:
        return na_value()
    return _math.log(x)


def pine_log10(x: Any) -> float:
    x = _unwrap(x)
    if is_na(x) or x <= 0:
        return na_value()
    return _math.log10(x)


def pine_sqrt(x: Any) -> float:
    x = _unwrap(x)
    if is_na(x) or x < 0:
        return na_value()
    return _math.sqrt(x)


def pine_pow(base: Any, exp: Any) -> float:
    base, exp = _unwrap(base), _unwrap(exp)
    if is_na(base) or is_na(exp):
        return na_value()
    return base ** exp


def pine_sign(x: Any) -> int:
    x = _unwrap(x)
    if is_na(x):
        return na_value()
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def pine_avg(*args: Any) -> float:
    vals = [_unwrap(a) for a in args if not is_na(_unwrap(a))]
    if not vals:
        return na_value()
    return sum(vals) / len(vals)


def pine_sum(source: Any, length: Any) -> float:
    source = _unwrap(source)
    length = int(_unwrap(length))
    if isinstance(source, Series) and len(source) >= length:
        vals = [source[i] for i in range(length)]
        valid = [v for v in vals if not is_na(v)]
        return sum(valid) if valid else na_value()
    return source if not is_na(source) else na_value()


def pine_nz(x: Any, replacement: Any = 0) -> Any:
    x = _unwrap(x)
    if is_na(x):
        return _unwrap(replacement)
    return x


def pine_na_check(x: Any = None) -> Any:
    """Implements both na (constant) and na(x) (check function)."""
    if x is None:
        return na_value()
    return is_na(_unwrap(x))


def pine_fixnan(source: Any) -> float:
    source = _unwrap(source)
    if is_na(source):
        return na_value()
    return source


def register(interpreter) -> None:
    funcs = {
        "math.abs": pine_abs,
        "math.max": pine_max,
        "math.min": pine_min,
        "math.round": pine_round,
        "math.ceil": pine_ceil,
        "math.floor": pine_floor,
        "math.log": pine_log,
        "math.log10": pine_log10,
        "math.sqrt": pine_sqrt,
        "math.pow": pine_pow,
        "math.sign": pine_sign,
        "math.avg": pine_avg,
        "math.sum": pine_sum,
        "nz": pine_nz,
        "na": pine_na_check,
        "fixnan": pine_fixnan,
    }
    for name, fn in funcs.items():
        interpreter.register_builtin(name, fn)
