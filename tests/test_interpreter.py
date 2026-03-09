"""Tests for the Pine Script v5 interpreter."""

import pytest
from strateg.lexer import Lexer
from strateg.parser import Parser
from strateg.interpreter import Interpreter
from strateg.series import Series, is_na, na_value
from strateg import ast_nodes as ast


def make_interpreter(source: str) -> Interpreter:
    tokens = Lexer(source).tokenize()
    script = Parser(tokens).parse()
    interp = Interpreter()
    interp.load_script(script)
    return interp


class TestBasicExpressions:
    def test_number_assignment(self):
        interp = make_interpreter("x = 42")
        interp.execute_bar(0)
        val = interp.env.get("x")
        assert isinstance(val, Series)
        assert val.current == 42

    def test_arithmetic(self):
        interp = make_interpreter("x = 2 + 3 * 4")
        interp.execute_bar(0)
        assert interp.env.get("x").current == 14

    def test_string_assignment(self):
        interp = make_interpreter('x = "hello"')
        interp.execute_bar(0)
        assert interp.env.get("x").current == "hello"

    def test_bool_assignment(self):
        interp = make_interpreter("x = true")
        interp.execute_bar(0)
        assert interp.env.get("x").current is True

    def test_na_assignment(self):
        interp = make_interpreter("x = na")
        interp.execute_bar(0)
        assert is_na(interp.env.get("x").current)

    def test_ternary(self):
        interp = make_interpreter("x = true ? 1 : 2")
        interp.execute_bar(0)
        assert interp.env.get("x").current == 1


class TestVarKeyword:
    def test_var_persists_across_bars(self):
        interp = make_interpreter("var count = 0")
        interp.execute_bar(0)
        assert interp.env.get("count").current == 0
        interp.execute_bar(1)
        assert interp.env.get("count").current == 0

    def test_regular_resets_each_bar(self):
        interp = make_interpreter("x = 42")
        interp.execute_bar(0)
        assert interp.env.get("x").current == 42
        interp.execute_bar(1)
        assert len(interp.env.get("x")) == 2


class TestControlFlow:
    def test_if_true(self):
        source = "x = 0\nif true\n    x := 1"
        interp = make_interpreter(source)
        interp.execute_bar(0)
        assert interp.env.get("x").current == 1

    def test_if_false(self):
        source = "x = 0\nif false\n    x := 1"
        interp = make_interpreter(source)
        interp.execute_bar(0)
        assert interp.env.get("x").current == 0

    def test_for_loop(self):
        source = "var total = 0\nfor i = 1 to 5\n    total := total + i"
        interp = make_interpreter(source)
        interp.execute_bar(0)
        assert interp.env.get("total").current == 15

    def test_while_loop(self):
        source = "var x = 10\nwhile x > 0\n    x := x - 1"
        interp = make_interpreter(source)
        interp.execute_bar(0)
        assert interp.env.get("x").current == 0


class TestUserFunctions:
    def test_simple_function(self):
        source = "add(a, b) => a + b\nx = add(3, 4)"
        interp = make_interpreter(source)
        interp.execute_bar(0)
        assert interp.env.get("x").current == 7


class TestBuiltins:
    def test_nz(self):
        from strateg.builtins import math_funcs
        interp = make_interpreter("x = nz(na, 5)")
        math_funcs.register(interp)
        interp.execute_bar(0)
        assert interp.env.get("x").current == 5

    def test_math_abs(self):
        from strateg.builtins import math_funcs
        interp = make_interpreter("x = math.abs(-10)")
        math_funcs.register(interp)
        interp.execute_bar(0)
        assert interp.env.get("x").current == 10

    def test_math_max(self):
        from strateg.builtins import math_funcs
        interp = make_interpreter("x = math.max(3, 7)")
        math_funcs.register(interp)
        interp.execute_bar(0)
        assert interp.env.get("x").current == 7


class TestSeries:
    def test_series_history(self):
        s = Series()
        s.push(10)
        s.push(20)
        s.push(30)
        assert s[0] == 30
        assert s[1] == 20
        assert s[2] == 10
        assert is_na(s[3])

    def test_series_current(self):
        s = Series()
        s.push(42)
        assert s.current == 42
