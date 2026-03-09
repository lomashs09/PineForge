"""AST node definitions for Pine Script v5."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Node:
    line: int = 0
    col: int = 0


# -- Top-level --

@dataclass
class Script(Node):
    version: int = 5
    statements: list[Node] = field(default_factory=list)


@dataclass
class VersionDirective(Node):
    version: int = 5


# -- Literals --

@dataclass
class NumberLiteral(Node):
    value: int | float = 0


@dataclass
class StringLiteral(Node):
    value: str = ""


@dataclass
class BoolLiteral(Node):
    value: bool = True


@dataclass
class NaLiteral(Node):
    pass


@dataclass
class Identifier(Node):
    name: str = ""


@dataclass
class ColorLiteral(Node):
    value: str = ""


# -- Expressions --

@dataclass
class BinaryOp(Node):
    op: str = ""
    left: Node = field(default_factory=Node)
    right: Node = field(default_factory=Node)


@dataclass
class UnaryOp(Node):
    op: str = ""
    operand: Node = field(default_factory=Node)


@dataclass
class TernaryOp(Node):
    condition: Node = field(default_factory=Node)
    true_expr: Node = field(default_factory=Node)
    false_expr: Node = field(default_factory=Node)


@dataclass
class HistoryRef(Node):
    expr: Node = field(default_factory=Node)
    offset: Node = field(default_factory=Node)


@dataclass
class FunctionCall(Node):
    name: str = ""
    args: list[Node] = field(default_factory=list)
    kwargs: dict[str, Node] = field(default_factory=dict)


@dataclass
class MemberAccess(Node):
    object: Node = field(default_factory=Node)
    member: str = ""


# -- Statements --

@dataclass
class Assignment(Node):
    name: str = ""
    value: Node = field(default_factory=Node)
    is_var: bool = False
    is_varip: bool = False
    type_annotation: str | None = None


@dataclass
class Reassignment(Node):
    name: str = ""
    value: Node = field(default_factory=Node)


@dataclass
class AugmentedAssignment(Node):
    name: str = ""
    op: str = ""
    value: Node = field(default_factory=Node)


@dataclass
class IfStatement(Node):
    condition: Node = field(default_factory=Node)
    body: list[Node] = field(default_factory=list)
    elseif_clauses: list[tuple[Node, list[Node]]] = field(default_factory=list)
    else_body: list[Node] | None = None


@dataclass
class ForStatement(Node):
    var_name: str = ""
    start: Node = field(default_factory=Node)
    end: Node = field(default_factory=Node)
    step: Node | None = None
    body: list[Node] = field(default_factory=list)


@dataclass
class ForInStatement(Node):
    var_name: str = ""
    iterable: Node = field(default_factory=Node)
    body: list[Node] = field(default_factory=list)


@dataclass
class WhileStatement(Node):
    condition: Node = field(default_factory=Node)
    body: list[Node] = field(default_factory=list)


@dataclass
class FunctionDef(Node):
    name: str = ""
    params: list[str] = field(default_factory=list)
    defaults: dict[str, Node] = field(default_factory=dict)
    body: list[Node] = field(default_factory=list)


@dataclass
class ExpressionStatement(Node):
    """Wraps a standalone expression used as a statement (e.g. bare function call)."""
    expr: Node = field(default_factory=Node)
