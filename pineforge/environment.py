"""Variable scope / symbol table for Pine Script execution."""

from __future__ import annotations

from typing import Any


class Environment:
    """Scoped symbol table with support for nested scopes."""

    def __init__(self, parent: Environment | None = None):
        self.parent = parent
        self._bindings: dict[str, Any] = {}
        self._var_declarations: set[str] = set()

    def define(self, name: str, value: Any, is_var: bool = False) -> None:
        self._bindings[name] = value
        if is_var:
            self._var_declarations.add(name)

    def is_var(self, name: str) -> bool:
        if name in self._var_declarations:
            return True
        if self.parent:
            return self.parent.is_var(name)
        return False

    def get(self, name: str) -> Any:
        if name in self._bindings:
            return self._bindings[name]
        if self.parent:
            return self.parent.get(name)
        raise NameError(f"Undefined variable: {name!r}")

    def has(self, name: str) -> bool:
        if name in self._bindings:
            return True
        if self.parent:
            return self.parent.has(name)
        return False

    def set(self, name: str, value: Any) -> None:
        """Set an existing variable (walks up scopes)."""
        if name in self._bindings:
            self._bindings[name] = value
            return
        if self.parent:
            self.parent.set(name, value)
            return
        raise NameError(f"Undefined variable: {name!r}")

    def child(self) -> Environment:
        return Environment(parent=self)
