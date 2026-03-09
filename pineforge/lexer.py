"""Lexer for Pine Script v5 — tokenizes source into a stream of tokens."""

from __future__ import annotations

from .tokens import Token, TokenType, KEYWORDS


class LexerError(Exception):
    def __init__(self, message: str, line: int, col: int):
        super().__init__(f"Lexer error at L{line}:{col}: {message}")
        self.line = line
        self.col = col


class Lexer:
    def __init__(self, source: str):
        self.source = source
        self.pos = 0
        self.line = 1
        self.col = 1
        self.tokens: list[Token] = []
        self._indent_stack: list[int] = [0]
        self._at_line_start = True

    def tokenize(self) -> list[Token]:
        while self.pos < len(self.source):
            if self._at_line_start:
                self._handle_indentation()
                self._at_line_start = False

            ch = self._peek()

            if ch == "\n":
                self._emit_newline()
                continue

            if ch in " \t":
                self._advance()
                continue

            if ch == "/" and self._peek(1) == "/":
                self._handle_comment()
                continue

            if ch == "\\" and self._peek(1) == "\n":
                self._advance()
                self._advance()
                self._at_line_start = False
                continue

            if ch in "0123456789" or (ch == "." and self._peek(1) in "0123456789"):
                self._read_number()
                continue

            if ch == '"' or ch == "'":
                self._read_string(ch)
                continue

            if ch.isalpha() or ch == "_":
                self._read_identifier()
                continue

            self._read_operator_or_punct()

        # Close remaining indents
        while len(self._indent_stack) > 1:
            self._indent_stack.pop()
            self.tokens.append(Token(TokenType.DEDENT, "", self.line, self.col))

        self.tokens.append(Token(TokenType.EOF, "", self.line, self.col))
        return self.tokens

    # -- Character helpers --

    def _peek(self, offset: int = 0) -> str:
        idx = self.pos + offset
        if idx < len(self.source):
            return self.source[idx]
        return "\0"

    def _advance(self) -> str:
        ch = self.source[self.pos]
        self.pos += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    # -- Indentation --

    def _handle_indentation(self):
        indent = 0
        while self.pos < len(self.source) and self.source[self.pos] in " \t":
            if self.source[self.pos] == "\t":
                indent += 4
            else:
                indent += 1
            self.pos += 1
            self.col += 1

        if self.pos < len(self.source) and self.source[self.pos] == "\n":
            return
        if self.pos < len(self.source) and self.source[self.pos] == "/" and self.pos + 1 < len(self.source) and self.source[self.pos + 1] == "/":
            pass  # comment lines don't affect indent

        current = self._indent_stack[-1]
        if indent > current:
            self._indent_stack.append(indent)
            self.tokens.append(Token(TokenType.INDENT, indent, self.line, 1))
        else:
            while indent < self._indent_stack[-1]:
                self._indent_stack.pop()
                self.tokens.append(Token(TokenType.DEDENT, "", self.line, 1))
            if indent != self._indent_stack[-1]:
                raise LexerError("Inconsistent indentation", self.line, self.col)

    # -- Newline --

    def _emit_newline(self):
        line = self.line
        col = self.col
        self._advance()
        if self.tokens and self.tokens[-1].type not in (
            TokenType.NEWLINE, TokenType.INDENT, TokenType.DEDENT
        ):
            self.tokens.append(Token(TokenType.NEWLINE, "\\n", line, col))
        self._at_line_start = True

    # -- Comments --

    def _handle_comment(self):
        start_col = self.col
        text = ""
        self._advance()  # first /
        self._advance()  # second /

        if self._peek() == "@":
            directive = ""
            while self.pos < len(self.source) and self._peek() != "\n":
                directive += self._advance()
            directive = directive.strip()
            if directive.startswith("@version="):
                version = directive[len("@version="):]
                self.tokens.append(Token(TokenType.VERSION_DIRECTIVE, int(version), self.line, start_col))
                return

        while self.pos < len(self.source) and self._peek() != "\n":
            text += self._advance()

    # -- Numbers --

    def _read_number(self):
        start_col = self.col
        num_str = ""
        is_float = False

        while self.pos < len(self.source) and (self._peek() in "0123456789._"):
            ch = self._peek()
            if ch == ".":
                if is_float:
                    break
                is_float = True
            if ch == "_":
                self._advance()
                continue
            num_str += self._advance()

        if is_float:
            self.tokens.append(Token(TokenType.FLOAT, float(num_str), self.line, start_col))
        else:
            self.tokens.append(Token(TokenType.INT, int(num_str), self.line, start_col))

    # -- Strings --

    def _read_string(self, quote: str):
        start_col = self.col
        self._advance()  # opening quote
        value = ""
        while self.pos < len(self.source):
            ch = self._peek()
            if ch == "\\":
                self._advance()
                esc = self._advance()
                escape_map = {"n": "\n", "t": "\t", "\\": "\\", "'": "'", '"': '"'}
                value += escape_map.get(esc, esc)
                continue
            if ch == quote:
                self._advance()
                self.tokens.append(Token(TokenType.STRING, value, self.line, start_col))
                return
            if ch == "\n":
                raise LexerError("Unterminated string literal", self.line, self.col)
            value += self._advance()
        raise LexerError("Unterminated string literal", self.line, self.col)

    # -- Identifiers & keywords --

    def _read_identifier(self):
        start_col = self.col
        ident = ""
        while self.pos < len(self.source) and (self._peek().isalnum() or self._peek() == "_"):
            ident += self._advance()

        ttype = KEYWORDS.get(ident, TokenType.IDENTIFIER)
        self.tokens.append(Token(ttype, ident, self.line, start_col))

    # -- Operators & punctuation --

    def _read_operator_or_punct(self):
        ch = self._peek()
        start_col = self.col

        two_char = ch + self._peek(1)
        two_char_ops = {
            ":=": TokenType.REASSIGN,
            "==": TokenType.EQ,
            "!=": TokenType.NEQ,
            "<=": TokenType.LTE,
            ">=": TokenType.GTE,
            "=>": TokenType.ARROW,
            "+=": TokenType.PLUS_ASSIGN,
            "-=": TokenType.MINUS_ASSIGN,
            "*=": TokenType.STAR_ASSIGN,
            "/=": TokenType.SLASH_ASSIGN,
            "%=": TokenType.PERCENT_ASSIGN,
        }

        if two_char in two_char_ops:
            self._advance()
            self._advance()
            self.tokens.append(Token(two_char_ops[two_char], two_char, self.line, start_col))
            return

        single_char_ops = {
            "+": TokenType.PLUS,
            "-": TokenType.MINUS,
            "*": TokenType.STAR,
            "/": TokenType.SLASH,
            "%": TokenType.PERCENT,
            "=": TokenType.ASSIGN,
            "<": TokenType.LT,
            ">": TokenType.GT,
            "?": TokenType.QUESTION,
            ":": TokenType.COLON,
            "(": TokenType.LPAREN,
            ")": TokenType.RPAREN,
            "[": TokenType.LBRACKET,
            "]": TokenType.RBRACKET,
            ",": TokenType.COMMA,
            ".": TokenType.DOT,
        }

        if ch in single_char_ops:
            self._advance()
            self.tokens.append(Token(single_char_ops[ch], ch, self.line, start_col))
            return

        raise LexerError(f"Unexpected character: {ch!r}", self.line, self.col)
