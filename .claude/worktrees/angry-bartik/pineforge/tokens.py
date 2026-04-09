"""Token types for the Pine Script v5 lexer."""

from enum import Enum, auto
from dataclasses import dataclass
from typing import Any


class TokenType(Enum):
    # Literals
    INT = auto()
    FLOAT = auto()
    STRING = auto()
    BOOL_TRUE = auto()
    BOOL_FALSE = auto()
    NA = auto()

    # Identifiers & keywords
    IDENTIFIER = auto()
    IF = auto()
    ELSE = auto()
    FOR = auto()
    WHILE = auto()
    VAR = auto()
    VARIP = auto()
    IMPORT = auto()
    EXPORT = auto()
    SWITCH = auto()
    TYPE = auto()
    AND = auto()
    OR = auto()
    NOT = auto()
    IN = auto()
    TO = auto()
    BY = auto()

    # Operators
    PLUS = auto()          # +
    MINUS = auto()         # -
    STAR = auto()          # *
    SLASH = auto()         # /
    PERCENT = auto()       # %
    ASSIGN = auto()        # =
    REASSIGN = auto()      # :=
    EQ = auto()            # ==
    NEQ = auto()           # !=
    LT = auto()            # <
    GT = auto()            # >
    LTE = auto()           # <=
    GTE = auto()           # >=
    ARROW = auto()         # =>
    QUESTION = auto()      # ?
    COLON = auto()         # :
    PLUS_ASSIGN = auto()   # +=
    MINUS_ASSIGN = auto()  # -=
    STAR_ASSIGN = auto()   # *=
    SLASH_ASSIGN = auto()  # /=
    PERCENT_ASSIGN = auto()  # %=

    # Punctuation
    LPAREN = auto()        # (
    RPAREN = auto()        # )
    LBRACKET = auto()      # [
    RBRACKET = auto()      # ]
    COMMA = auto()         # ,
    DOT = auto()           # .

    # Structure
    NEWLINE = auto()
    INDENT = auto()
    DEDENT = auto()
    EOF = auto()

    # Special
    VERSION_DIRECTIVE = auto()  # //@version=5
    COMMENT = auto()


KEYWORDS = {
    "if": TokenType.IF,
    "else": TokenType.ELSE,
    "for": TokenType.FOR,
    "while": TokenType.WHILE,
    "var": TokenType.VAR,
    "varip": TokenType.VARIP,
    "import": TokenType.IMPORT,
    "export": TokenType.EXPORT,
    "switch": TokenType.SWITCH,
    "type": TokenType.TYPE,
    "and": TokenType.AND,
    "or": TokenType.OR,
    "not": TokenType.NOT,
    "true": TokenType.BOOL_TRUE,
    "false": TokenType.BOOL_FALSE,
    "na": TokenType.NA,
    "in": TokenType.IN,
    "to": TokenType.TO,
    "by": TokenType.BY,
}


@dataclass
class Token:
    type: TokenType
    value: Any
    line: int
    col: int

    def __repr__(self) -> str:
        return f"Token({self.type.name}, {self.value!r}, L{self.line}:{self.col})"
