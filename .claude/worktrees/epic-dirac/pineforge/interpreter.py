"""Tree-walking interpreter for Pine Script v5 AST."""

from __future__ import annotations

from typing import Any, Callable

from . import ast_nodes as ast
from .series import Series, is_na, na_value
from .environment import Environment


class RuntimeError_(Exception):
    def __init__(self, message: str, node: ast.Node | None = None):
        loc = f" at L{node.line}:{node.col}" if node else ""
        super().__init__(f"Runtime error{loc}: {message}")
        self.node = node


class Interpreter:
    """Evaluates a Pine Script AST bar-by-bar.

    The interpreter is designed to be called once per bar by the engine.
    It maintains state across bars via the environment (for `var` declarations)
    and series objects.
    """

    def __init__(self):
        self.env = Environment()
        self.builtins: dict[str, Callable] = {}
        self.bar_index: int = 0
        self._initialized_vars: set[str] = set()
        self._script: ast.Script | None = None

    def register_builtin(self, name: str, func: Callable) -> None:
        self.builtins[name] = func

    def load_script(self, script: ast.Script) -> None:
        self._script = script

    def execute_bar(self, bar_index: int) -> None:
        """Execute the full script for a single bar."""
        if self._script is None:
            raise RuntimeError_("No script loaded")
        self.bar_index = bar_index
        for stmt in self._script.statements:
            self._exec(stmt)

    def _exec(self, node: ast.Node) -> Any:
        method_name = f"_exec_{type(node).__name__}"
        method = getattr(self, method_name, None)
        if method is None:
            raise RuntimeError_(f"No handler for {type(node).__name__}", node)
        return method(node)

    # -- Statements --

    def _exec_ExpressionStatement(self, node: ast.ExpressionStatement) -> Any:
        return self._eval(node.expr)

    def _exec_Assignment(self, node: ast.Assignment) -> Any:
        if node.is_var or node.is_varip:
            if node.name in self._initialized_vars:
                return self.env.get(node.name)
            value = self._eval(node.value)
            series = self._to_series(value)
            self.env.define(node.name, series, is_var=True)
            self._initialized_vars.add(node.name)
            return series
        else:
            value = self._eval(node.value)
            if self.env.has(node.name) and isinstance(self.env.get(node.name), Series):
                s = self.env.get(node.name)
                s.push(self._unwrap(value))
                return s
            series = self._to_series(value)
            self.env.define(node.name, series)
            return series

    def _exec_Reassignment(self, node: ast.Reassignment) -> Any:
        value = self._eval(node.value)
        raw = self._unwrap(value)
        if self.env.has(node.name):
            target = self.env.get(node.name)
            if isinstance(target, Series):
                target.set_current(raw)
                return target
        self.env.set(node.name, raw)
        return raw

    def _exec_AugmentedAssignment(self, node: ast.AugmentedAssignment) -> Any:
        current = self._unwrap(self._eval_name(node.name))
        rhs = self._unwrap(self._eval(node.value))
        ops = {
            "+=": lambda a, b: a + b,
            "-=": lambda a, b: a - b,
            "*=": lambda a, b: a * b,
            "/=": lambda a, b: a / b if b != 0 else na_value(),
            "%=": lambda a, b: a % b if b != 0 else na_value(),
        }
        result = ops[node.op](current, rhs)
        if self.env.has(node.name):
            target = self.env.get(node.name)
            if isinstance(target, Series):
                target.set_current(result)
                return target
        self.env.set(node.name, result)
        return result

    def _exec_IfStatement(self, node: ast.IfStatement) -> Any:
        cond = self._unwrap(self._eval(node.condition))
        if cond and not is_na(cond):
            return self._exec_block(node.body)

        for ei_cond, ei_body in node.elseif_clauses:
            c = self._unwrap(self._eval(ei_cond))
            if c and not is_na(c):
                return self._exec_block(ei_body)

        if node.else_body:
            return self._exec_block(node.else_body)
        return na_value()

    def _exec_ForStatement(self, node: ast.ForStatement) -> Any:
        start = int(self._unwrap(self._eval(node.start)))
        end = int(self._unwrap(self._eval(node.end)))
        step = int(self._unwrap(self._eval(node.step))) if node.step else 1

        result = na_value()
        i = start
        while (step > 0 and i <= end) or (step < 0 and i >= end):
            self.env.define(node.var_name, i)
            result = self._exec_block(node.body)
            i += step
        return result

    def _exec_ForInStatement(self, node: ast.ForInStatement) -> Any:
        iterable = self._unwrap(self._eval(node.iterable))
        result = na_value()
        if hasattr(iterable, "__iter__"):
            for item in iterable:
                self.env.define(node.var_name, item)
                result = self._exec_block(node.body)
        return result

    def _exec_WhileStatement(self, node: ast.WhileStatement) -> Any:
        result = na_value()
        limit = 10000
        count = 0
        while count < limit:
            cond = self._unwrap(self._eval(node.condition))
            if not cond or is_na(cond):
                break
            result = self._exec_block(node.body)
            count += 1
        return result

    def _exec_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.env.define(node.name, node)

    def _exec_block(self, stmts: list[ast.Node]) -> Any:
        result = na_value()
        for stmt in stmts:
            result = self._exec(stmt)
        return result

    # -- Expressions --

    def _eval(self, node: ast.Node) -> Any:
        method_name = f"_eval_{type(node).__name__}"
        method = getattr(self, method_name, None)
        if method is None:
            return self._exec(node)
        return method(node)

    def _eval_NumberLiteral(self, node: ast.NumberLiteral) -> int | float:
        return node.value

    def _eval_StringLiteral(self, node: ast.StringLiteral) -> str:
        return node.value

    def _eval_BoolLiteral(self, node: ast.BoolLiteral) -> bool:
        return node.value

    def _eval_NaLiteral(self, _node: ast.NaLiteral) -> float:
        return na_value()

    def _eval_Identifier(self, node: ast.Identifier) -> Any:
        return self._eval_name(node.name)

    def _eval_name(self, name: str) -> Any:
        if self.env.has(name):
            return self.env.get(name)
        if name in self.builtins:
            return self.builtins[name]
        raise RuntimeError_(f"Undefined: {name!r}")

    def _eval_BinaryOp(self, node: ast.BinaryOp) -> Any:
        left = self._unwrap(self._eval(node.left))
        right = self._unwrap(self._eval(node.right))

        if is_na(left) or is_na(right):
            if node.op in ("==", "!="):
                pass  # allow comparison with na
            else:
                return na_value()

        ops: dict[str, Callable] = {
            "+": lambda a, b: a + b,
            "-": lambda a, b: a - b,
            "*": lambda a, b: a * b,
            "/": lambda a, b: a / b if b != 0 else na_value(),
            "%": lambda a, b: a % b if b != 0 else na_value(),
            "==": lambda a, b: (is_na(a) and is_na(b)) or a == b,
            "!=": lambda a, b: not ((is_na(a) and is_na(b)) or a == b),
            "<": lambda a, b: a < b,
            ">": lambda a, b: a > b,
            "<=": lambda a, b: a <= b,
            ">=": lambda a, b: a >= b,
            "and": lambda a, b: bool(a) and bool(b),
            "or": lambda a, b: bool(a) or bool(b),
        }
        op_fn = ops.get(node.op)
        if op_fn is None:
            raise RuntimeError_(f"Unknown operator: {node.op}", node)
        return op_fn(left, right)

    def _eval_UnaryOp(self, node: ast.UnaryOp) -> Any:
        operand = self._unwrap(self._eval(node.operand))
        if node.op == "-":
            return -operand if not is_na(operand) else na_value()
        if node.op == "not":
            return not operand if not is_na(operand) else na_value()
        raise RuntimeError_(f"Unknown unary operator: {node.op}", node)

    def _eval_TernaryOp(self, node: ast.TernaryOp) -> Any:
        cond = self._unwrap(self._eval(node.condition))
        if cond and not is_na(cond):
            return self._eval(node.true_expr)
        return self._eval(node.false_expr)

    def _eval_HistoryRef(self, node: ast.HistoryRef) -> Any:
        series = self._eval(node.expr)
        offset = int(self._unwrap(self._eval(node.offset)))
        if isinstance(series, Series):
            return series[offset]
        return na_value()

    def _eval_MemberAccess(self, node: ast.MemberAccess) -> Any:
        full_name = self._build_dotted_name(node)
        if full_name and self.env.has(full_name):
            return self.env.get(full_name)
        if full_name and full_name in self.builtins:
            return self.builtins[full_name]
        obj = self._eval(node.object)
        if isinstance(obj, dict):
            return obj.get(node.member, na_value())
        raise RuntimeError_(f"Cannot access member '{node.member}'", node)

    def _build_dotted_name(self, node: ast.Node) -> str | None:
        if isinstance(node, ast.Identifier):
            return node.name
        if isinstance(node, ast.MemberAccess):
            parent = self._build_dotted_name(node.object)
            if parent:
                return f"{parent}.{node.member}"
        return None

    def _eval_FunctionCall(self, node: ast.FunctionCall) -> Any:
        args = [self._eval(a) for a in node.args]
        kwargs = {k: self._eval(v) for k, v in node.kwargs.items()}

        if node.name in self.builtins:
            fn = self.builtins[node.name]
            return fn(*args, **kwargs)

        if self.env.has(node.name):
            fn_def = self.env.get(node.name)
            if isinstance(fn_def, ast.FunctionDef):
                return self._call_user_function(fn_def, args, kwargs)
            if callable(fn_def):
                return fn_def(*args, **kwargs)

        raise RuntimeError_(f"Undefined function: {node.name!r}", node)

    def _call_user_function(self, fn: ast.FunctionDef, args: list, kwargs: dict) -> Any:
        child_env = self.env.child()
        saved_env = self.env
        self.env = child_env

        for i, param in enumerate(fn.params):
            if i < len(args):
                child_env.define(param, args[i])
            elif param in kwargs:
                child_env.define(param, kwargs[param])
            elif param in fn.defaults:
                child_env.define(param, self._eval(fn.defaults[param]))
            else:
                child_env.define(param, na_value())

        result = na_value()
        for stmt in fn.body:
            result = self._exec(stmt)

        self.env = saved_env
        return result

    # -- Helpers --

    def _unwrap(self, value: Any) -> Any:
        """Unwrap a Series to its current scalar value."""
        if isinstance(value, Series):
            return value.current
        return value

    def _to_series(self, value: Any) -> Series:
        if isinstance(value, Series):
            return value
        s = Series()
        s.push(self._unwrap(value))
        return s
