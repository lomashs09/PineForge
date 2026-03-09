"""Tests for the Pine Script v5 lexer."""

import pytest
from pineforge.lexer import Lexer, LexerError
from pineforge.tokens import TokenType


def tokenize(source: str) -> list:
    return Lexer(source).tokenize()


def token_types(source: str) -> list[TokenType]:
    return [t.type for t in tokenize(source) if t.type != TokenType.EOF]


class TestBasicTokens:
    def test_integer(self):
        tokens = tokenize("42")
        assert tokens[0].type == TokenType.INT
        assert tokens[0].value == 42

    def test_float(self):
        tokens = tokenize("3.14")
        assert tokens[0].type == TokenType.FLOAT
        assert tokens[0].value == 3.14

    def test_string_double_quotes(self):
        tokens = tokenize('"hello"')
        assert tokens[0].type == TokenType.STRING
        assert tokens[0].value == "hello"

    def test_string_single_quotes(self):
        tokens = tokenize("'world'")
        assert tokens[0].type == TokenType.STRING
        assert tokens[0].value == "world"

    def test_identifiers(self):
        tokens = tokenize("foo bar_baz _x")
        idents = [t for t in tokens if t.type == TokenType.IDENTIFIER]
        assert [t.value for t in idents] == ["foo", "bar_baz", "_x"]

    def test_keywords(self):
        assert tokenize("if")[0].type == TokenType.IF
        assert tokenize("else")[0].type == TokenType.ELSE
        assert tokenize("for")[0].type == TokenType.FOR
        assert tokenize("while")[0].type == TokenType.WHILE
        assert tokenize("var")[0].type == TokenType.VAR
        assert tokenize("true")[0].type == TokenType.BOOL_TRUE
        assert tokenize("false")[0].type == TokenType.BOOL_FALSE
        assert tokenize("na")[0].type == TokenType.NA
        assert tokenize("and")[0].type == TokenType.AND
        assert tokenize("or")[0].type == TokenType.OR
        assert tokenize("not")[0].type == TokenType.NOT


class TestOperators:
    def test_two_char_operators(self):
        assert tokenize(":=")[0].type == TokenType.REASSIGN
        assert tokenize("==")[0].type == TokenType.EQ
        assert tokenize("!=")[0].type == TokenType.NEQ
        assert tokenize("<=")[0].type == TokenType.LTE
        assert tokenize(">=")[0].type == TokenType.GTE
        assert tokenize("=>")[0].type == TokenType.ARROW

    def test_single_char_operators(self):
        assert tokenize("+")[0].type == TokenType.PLUS
        assert tokenize("-")[0].type == TokenType.MINUS
        assert tokenize("*")[0].type == TokenType.STAR
        assert tokenize("/")[0].type == TokenType.SLASH
        assert tokenize("=")[0].type == TokenType.ASSIGN
        assert tokenize("<")[0].type == TokenType.LT
        assert tokenize(">")[0].type == TokenType.GT


class TestComments:
    def test_line_comment_skipped(self):
        types = token_types("x = 1 // this is a comment\ny = 2")
        assert TokenType.COMMENT not in types

    def test_version_directive(self):
        tokens = tokenize("//@version=5")
        assert tokens[0].type == TokenType.VERSION_DIRECTIVE
        assert tokens[0].value == 5


class TestIndentation:
    def test_indent_dedent(self):
        source = "if x\n    y = 1\nz = 2"
        types = token_types(source)
        assert TokenType.INDENT in types
        assert TokenType.DEDENT in types

    def test_nested_indent(self):
        source = "if a\n    if b\n        x = 1\ny = 2"
        types = token_types(source)
        assert types.count(TokenType.INDENT) == 2
        assert types.count(TokenType.DEDENT) == 2


class TestFullExpression:
    def test_assignment(self):
        types = token_types("x = ta.sma(close, 14)")
        assert TokenType.IDENTIFIER in types
        assert TokenType.ASSIGN in types
        assert TokenType.DOT in types
        assert TokenType.LPAREN in types
        assert TokenType.INT in types
        assert TokenType.RPAREN in types

    def test_version_and_strategy(self):
        source = '//@version=5\nstrategy("Test", overlay=true)'
        tokens = tokenize(source)
        assert tokens[0].type == TokenType.VERSION_DIRECTIVE
        assert tokens[0].value == 5


class TestEdgeCases:
    def test_empty_source(self):
        tokens = tokenize("")
        assert tokens[-1].type == TokenType.EOF

    def test_unterminated_string(self):
        with pytest.raises(LexerError):
            tokenize('"hello')

    def test_unexpected_char(self):
        with pytest.raises(LexerError):
            tokenize("@invalid")

    def test_number_with_underscore(self):
        tokens = tokenize("1_000_000")
        assert tokens[0].type == TokenType.INT
        assert tokens[0].value == 1000000

    def test_line_continuation(self):
        source = "x = 1 +\\\n2"
        types = token_types(source)
        assert TokenType.NEWLINE not in [t for i, t in enumerate(types)
                                          if i > 0 and types[i-1] == TokenType.PLUS]
