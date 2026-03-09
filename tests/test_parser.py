"""Tests for the Pine Script v5 parser."""

import pytest
from strateg.lexer import Lexer
from strateg.parser import Parser, ParseError
from strateg import ast_nodes as ast


def parse(source: str) -> ast.Script:
    tokens = Lexer(source).tokenize()
    return Parser(tokens).parse()


class TestVersionAndStrategy:
    def test_version_directive(self):
        script = parse("//@version=5")
        assert script.version == 5

    def test_strategy_declaration(self):
        script = parse('//@version=5\nstrategy("Test", overlay=true)')
        assert len(script.statements) == 1
        stmt = script.statements[0]
        assert isinstance(stmt, ast.ExpressionStatement)
        assert isinstance(stmt.expr, ast.FunctionCall)
        assert stmt.expr.name == "strategy"


class TestAssignments:
    def test_simple_assignment(self):
        script = parse("x = 42")
        assert len(script.statements) == 1
        stmt = script.statements[0]
        assert isinstance(stmt, ast.Assignment)
        assert stmt.name == "x"
        assert isinstance(stmt.value, ast.NumberLiteral)
        assert stmt.value.value == 42

    def test_var_declaration(self):
        script = parse("var count = 0")
        stmt = script.statements[0]
        assert isinstance(stmt, ast.Assignment)
        assert stmt.is_var is True
        assert stmt.name == "count"

    def test_reassignment(self):
        script = parse("x := 10")
        stmt = script.statements[0]
        assert isinstance(stmt, ast.Reassignment)
        assert stmt.name == "x"


class TestExpressions:
    def test_binary_ops(self):
        script = parse("x = 1 + 2 * 3")
        stmt = script.statements[0]
        assert isinstance(stmt, ast.Assignment)
        assert isinstance(stmt.value, ast.BinaryOp)
        assert stmt.value.op == "+"
        assert isinstance(stmt.value.right, ast.BinaryOp)
        assert stmt.value.right.op == "*"

    def test_comparison(self):
        script = parse("x = a > b")
        stmt = script.statements[0]
        assert isinstance(stmt.value, ast.BinaryOp)
        assert stmt.value.op == ">"

    def test_ternary(self):
        script = parse("x = a ? b : c")
        stmt = script.statements[0]
        assert isinstance(stmt.value, ast.TernaryOp)

    def test_unary_minus(self):
        script = parse("x = -5")
        stmt = script.statements[0]
        assert isinstance(stmt.value, ast.UnaryOp)
        assert stmt.value.op == "-"

    def test_history_ref(self):
        script = parse("x = close[1]")
        stmt = script.statements[0]
        assert isinstance(stmt.value, ast.HistoryRef)
        assert isinstance(stmt.value.offset, ast.NumberLiteral)
        assert stmt.value.offset.value == 1

    def test_member_access_call(self):
        script = parse("x = ta.sma(close, 14)")
        stmt = script.statements[0]
        assert isinstance(stmt.value, ast.FunctionCall)
        assert stmt.value.name == "ta.sma"
        assert len(stmt.value.args) == 2

    def test_named_args(self):
        script = parse('x = input.int(14, title="Length")')
        stmt = script.statements[0]
        call = stmt.value
        assert isinstance(call, ast.FunctionCall)
        assert len(call.args) == 1
        assert "title" in call.kwargs

    def test_logical_ops(self):
        script = parse("x = a and b or not c")
        stmt = script.statements[0]
        assert isinstance(stmt.value, ast.BinaryOp)
        assert stmt.value.op == "or"

    def test_na_literal(self):
        script = parse("x = na")
        stmt = script.statements[0]
        assert isinstance(stmt.value, ast.NaLiteral)

    def test_bool_literals(self):
        script = parse("x = true")
        stmt = script.statements[0]
        assert isinstance(stmt.value, ast.BoolLiteral)
        assert stmt.value.value is True


class TestControlFlow:
    def test_if_statement(self):
        source = "if x > 0\n    y = 1"
        script = parse(source)
        stmt = script.statements[0]
        assert isinstance(stmt, ast.IfStatement)
        assert len(stmt.body) == 1

    def test_if_else(self):
        source = "if x > 0\n    y = 1\nelse\n    y = 2"
        script = parse(source)
        stmt = script.statements[0]
        assert isinstance(stmt, ast.IfStatement)
        assert stmt.else_body is not None
        assert len(stmt.else_body) == 1

    def test_for_to(self):
        source = "for i = 0 to 10\n    x = i"
        script = parse(source)
        stmt = script.statements[0]
        assert isinstance(stmt, ast.ForStatement)
        assert stmt.var_name == "i"

    def test_while(self):
        source = "while x > 0\n    x := x - 1"
        script = parse(source)
        stmt = script.statements[0]
        assert isinstance(stmt, ast.WhileStatement)


class TestFunctionDef:
    def test_inline_function(self):
        source = "f(x, y) => x + y"
        script = parse(source)
        stmt = script.statements[0]
        assert isinstance(stmt, ast.FunctionDef)
        assert stmt.name == "f"
        assert stmt.params == ["x", "y"]
        assert len(stmt.body) == 1


class TestFullStrategy:
    def test_sma_crossover(self):
        source = """//@version=5
strategy("SMA Crossover", overlay=true)

length_fast = input.int(10, "Fast Length")
length_slow = input.int(30, "Slow Length")

fast_sma = ta.sma(close, length_fast)
slow_sma = ta.sma(close, length_slow)

if ta.crossover(fast_sma, slow_sma)
    strategy.entry("Long", strategy.long)

if ta.crossunder(fast_sma, slow_sma)
    strategy.close("Long")
"""
        script = parse(source)
        assert script.version == 5
        assert len(script.statements) == 7  # strategy decl + 4 assigns + 2 ifs
