"""Built-in input functions for Pine Script v5.

Input functions return their default values. CLI overrides can be added later.
"""

from __future__ import annotations

from typing import Any

from ..series import na_value


class InputStore:
    """Collects input declarations for potential override."""

    def __init__(self):
        self.inputs: dict[str, Any] = {}
        self.overrides: dict[str, Any] = {}

    def set_overrides(self, overrides: dict[str, Any]) -> None:
        self.overrides = overrides

    def reset(self) -> None:
        self.inputs.clear()
        self.overrides.clear()

    def resolve(self, title: str | None, defval: Any) -> Any:
        if title and title in self.overrides:
            return self.overrides[title]
        return defval


_store = InputStore()


def get_input_store() -> InputStore:
    return _store


def input_int(defval: Any = 0, title: Any = None, **_kwargs) -> int:
    from ..series import is_na
    val = _store.resolve(title, defval)
    if is_na(val):
        return 0
    return int(val)


def input_float(defval: Any = 0.0, title: Any = None, **_kwargs) -> float:
    from ..series import is_na
    val = _store.resolve(title, defval)
    if is_na(val):
        return 0.0
    return float(val)


def input_bool(defval: Any = False, title: Any = None, **_kwargs) -> bool:
    val = _store.resolve(title, defval)
    return bool(val)


def input_string(defval: Any = "", title: Any = None, **_kwargs) -> str:
    val = _store.resolve(title, defval)
    return str(val)


def input_source(defval: Any = None, title: Any = None, **_kwargs) -> Any:
    """Source inputs (close, open, etc.) — return defval as-is."""
    return _store.resolve(title, defval)


def input_generic(defval: Any = None, title: Any = None, **_kwargs) -> Any:
    """Generic input() call."""
    if isinstance(defval, bool):
        return input_bool(defval, title)
    if isinstance(defval, int):
        return input_int(defval, title)
    if isinstance(defval, float):
        return input_float(defval, title)
    if isinstance(defval, str):
        return input_string(defval, title)
    return _store.resolve(title, defval)


def register(interpreter, ctx=None) -> None:
    if ctx is not None:
        store = ctx.inputs

        def _int(defval: Any = 0, title: Any = None, **_kwargs) -> int:
            from ..series import is_na
            val = store.resolve(title, defval)
            if is_na(val):
                return 0
            return int(val)

        def _float(defval: Any = 0.0, title: Any = None, **_kwargs) -> float:
            from ..series import is_na
            val = store.resolve(title, defval)
            if is_na(val):
                return 0.0
            return float(val)

        def _bool(defval: Any = False, title: Any = None, **_kwargs) -> bool:
            return bool(store.resolve(title, defval))

        def _string(defval: Any = "", title: Any = None, **_kwargs) -> str:
            return str(store.resolve(title, defval))

        def _source(defval: Any = None, title: Any = None, **_kwargs) -> Any:
            return store.resolve(title, defval)

        def _generic(defval: Any = None, title: Any = None, **_kwargs) -> Any:
            if isinstance(defval, bool):
                return _bool(defval, title)
            if isinstance(defval, int):
                return _int(defval, title)
            if isinstance(defval, float):
                return _float(defval, title)
            if isinstance(defval, str):
                return _string(defval, title)
            return store.resolve(title, defval)

        funcs = {
            "input": _generic,
            "input.int": _int,
            "input.float": _float,
            "input.bool": _bool,
            "input.string": _string,
            "input.source": _source,
        }
    else:
        funcs = {
            "input": input_generic,
            "input.int": input_int,
            "input.float": input_float,
            "input.bool": input_bool,
            "input.string": input_string,
            "input.source": input_source,
        }
    for name, fn in funcs.items():
        interpreter.register_builtin(name, fn)
