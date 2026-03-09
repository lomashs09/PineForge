"""Recursive descent parser for Pine Script v5."""

from __future__ import annotations

from .tokens import Token, TokenType
from . import ast_nodes as ast


class ParseError(Exception):
    def __init__(self, message: str, token: Token):
        super().__init__(f"Parse error at L{token.line}:{token.col}: {message}")
        self.token = token


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    # -- Helpers --

    def _current(self) -> Token:
        return self.tokens[self.pos]

    def _peek(self, offset: int = 0) -> Token:
        idx = self.pos + offset
        if idx < len(self.tokens):
            return self.tokens[idx]
        return self.tokens[-1]  # EOF

    def _at(self, *types: TokenType) -> bool:
        return self._current().type in types

    def _eat(self, ttype: TokenType) -> Token:
        tok = self._current()
        if tok.type != ttype:
            raise ParseError(f"Expected {ttype.name}, got {tok.type.name} ({tok.value!r})", tok)
        self.pos += 1
        return tok

    def _match(self, *types: TokenType) -> Token | None:
        if self._current().type in types:
            tok = self._current()
            self.pos += 1
            return tok
        return None

    def _skip_newlines(self):
        while self._at(TokenType.NEWLINE):
            self.pos += 1

    # -- Entry point --

    def parse(self) -> ast.Script:
        script = ast.Script()
        self._skip_newlines()

        if self._at(TokenType.VERSION_DIRECTIVE):
            tok = self._eat(TokenType.VERSION_DIRECTIVE)
            script.version = tok.value
            self._skip_newlines()

        while not self._at(TokenType.EOF):
            stmt = self._parse_statement()
            if stmt is not None:
                script.statements.append(stmt)
            self._skip_newlines()

        return script

    # -- Statements --

    def _parse_statement(self) -> ast.Node | None:
        self._skip_newlines()
        if self._at(TokenType.EOF):
            return None

        tok = self._current()

        if tok.type == TokenType.VAR or tok.type == TokenType.VARIP:
            return self._parse_var_declaration()

        if tok.type == TokenType.IF:
            return self._parse_if()

        if tok.type == TokenType.FOR:
            return self._parse_for()

        if tok.type == TokenType.WHILE:
            return self._parse_while()

        if tok.type == TokenType.IDENTIFIER:
            return self._parse_identifier_statement()

        expr = self._parse_expression()
        self._match(TokenType.NEWLINE)
        return ast.ExpressionStatement(expr=expr, line=tok.line, col=tok.col)

    def _parse_var_declaration(self) -> ast.Assignment:
        tok = self._current()
        is_var = tok.type == TokenType.VAR
        is_varip = tok.type == TokenType.VARIP
        self.pos += 1  # consume var/varip

        name_tok = self._eat(TokenType.IDENTIFIER)
        self._eat(TokenType.ASSIGN)
        value = self._parse_expression()
        self._match(TokenType.NEWLINE)
        return ast.Assignment(
            name=name_tok.value, value=value,
            is_var=is_var, is_varip=is_varip,
            line=tok.line, col=tok.col,
        )

    def _parse_identifier_statement(self) -> ast.Node:
        tok = self._current()
        name_tok = self._eat(TokenType.IDENTIFIER)

        # Function definition: name(params) =>
        if self._at(TokenType.LPAREN) and self._is_function_def():
            return self._parse_function_def(name_tok)

        # Assignment: name = expr
        if self._at(TokenType.ASSIGN):
            self._eat(TokenType.ASSIGN)
            value = self._parse_expression()
            self._match(TokenType.NEWLINE)
            return ast.Assignment(name=name_tok.value, value=value, line=tok.line, col=tok.col)

        # Reassignment: name := expr
        if self._at(TokenType.REASSIGN):
            self._eat(TokenType.REASSIGN)
            value = self._parse_expression()
            self._match(TokenType.NEWLINE)
            return ast.Reassignment(name=name_tok.value, value=value, line=tok.line, col=tok.col)

        # Augmented assignment: name += expr, etc.
        if self._at(TokenType.PLUS_ASSIGN, TokenType.MINUS_ASSIGN,
                     TokenType.STAR_ASSIGN, TokenType.SLASH_ASSIGN,
                     TokenType.PERCENT_ASSIGN):
            op_tok = self._current()
            self.pos += 1
            value = self._parse_expression()
            self._match(TokenType.NEWLINE)
            return ast.AugmentedAssignment(
                name=name_tok.value, op=op_tok.value,
                value=value, line=tok.line, col=tok.col,
            )

        # Otherwise it's an expression starting with this identifier — rewind and parse
        self.pos -= 1
        expr = self._parse_expression()
        self._match(TokenType.NEWLINE)
        return ast.ExpressionStatement(expr=expr, line=tok.line, col=tok.col)

    def _is_function_def(self) -> bool:
        """Lookahead to check if this is `name(params) =>` (function definition)."""
        saved = self.pos
        depth = 0
        try:
            while self.pos < len(self.tokens):
                t = self._current()
                if t.type == TokenType.LPAREN:
                    depth += 1
                elif t.type == TokenType.RPAREN:
                    depth -= 1
                    if depth == 0:
                        self.pos += 1
                        result = self._at(TokenType.ARROW)
                        return result
                elif t.type in (TokenType.NEWLINE, TokenType.EOF):
                    return False
                self.pos += 1
            return False
        finally:
            self.pos = saved

    def _parse_function_def(self, name_tok: Token) -> ast.FunctionDef:
        self._eat(TokenType.LPAREN)
        params: list[str] = []
        defaults: dict[str, ast.Node] = {}
        while not self._at(TokenType.RPAREN):
            p = self._eat(TokenType.IDENTIFIER)
            params.append(p.value)
            if self._match(TokenType.ASSIGN):
                defaults[p.value] = self._parse_expression()
            if not self._match(TokenType.COMMA):
                break
        self._eat(TokenType.RPAREN)
        self._eat(TokenType.ARROW)

        body = self._parse_block_or_inline()
        return ast.FunctionDef(
            name=name_tok.value, params=params, defaults=defaults,
            body=body, line=name_tok.line, col=name_tok.col,
        )

    def _parse_if(self) -> ast.IfStatement:
        tok = self._eat(TokenType.IF)
        condition = self._parse_expression()
        body = self._parse_block()

        elseif_clauses: list[tuple[ast.Node, list[ast.Node]]] = []
        else_body: list[ast.Node] | None = None

        while True:
            self._skip_newlines()
            if self._at(TokenType.ELSE):
                self._eat(TokenType.ELSE)
                if self._at(TokenType.IF):
                    self._eat(TokenType.IF)
                    ei_cond = self._parse_expression()
                    ei_body = self._parse_block()
                    elseif_clauses.append((ei_cond, ei_body))
                else:
                    else_body = self._parse_block()
                    break
            else:
                break

        return ast.IfStatement(
            condition=condition, body=body,
            elseif_clauses=elseif_clauses, else_body=else_body,
            line=tok.line, col=tok.col,
        )

    def _parse_for(self) -> ast.Node:
        tok = self._eat(TokenType.FOR)
        var_tok = self._eat(TokenType.IDENTIFIER)

        if self._at(TokenType.IN):
            self._eat(TokenType.IN)
            iterable = self._parse_expression()
            body = self._parse_block()
            return ast.ForInStatement(
                var_name=var_tok.value, iterable=iterable,
                body=body, line=tok.line, col=tok.col,
            )

        self._eat(TokenType.ASSIGN)
        start = self._parse_expression()
        self._eat(TokenType.TO)
        end = self._parse_expression()
        step = None
        if self._match(TokenType.BY):
            step = self._parse_expression()
        body = self._parse_block()
        return ast.ForStatement(
            var_name=var_tok.value, start=start, end=end,
            step=step, body=body, line=tok.line, col=tok.col,
        )

    def _parse_while(self) -> ast.WhileStatement:
        tok = self._eat(TokenType.WHILE)
        condition = self._parse_expression()
        body = self._parse_block()
        return ast.WhileStatement(
            condition=condition, body=body,
            line=tok.line, col=tok.col,
        )

    # -- Blocks --

    def _parse_block(self) -> list[ast.Node]:
        """Parse an indented block after a control structure."""
        self._match(TokenType.NEWLINE)
        self._skip_newlines()
        self._eat(TokenType.INDENT)
        stmts: list[ast.Node] = []
        while not self._at(TokenType.DEDENT, TokenType.EOF):
            stmt = self._parse_statement()
            if stmt is not None:
                stmts.append(stmt)
            self._skip_newlines()
        self._match(TokenType.DEDENT)
        return stmts

    def _parse_block_or_inline(self) -> list[ast.Node]:
        """Parse either an indented block or a single inline expression."""
        if self._at(TokenType.NEWLINE):
            return self._parse_block()
        expr = self._parse_expression()
        self._match(TokenType.NEWLINE)
        return [ast.ExpressionStatement(expr=expr, line=expr.line, col=expr.col)]

    # -- Expressions (precedence climbing) --

    def _parse_expression(self) -> ast.Node:
        return self._parse_ternary()

    def _parse_ternary(self) -> ast.Node:
        expr = self._parse_or()
        if self._match(TokenType.QUESTION):
            true_expr = self._parse_expression()
            self._eat(TokenType.COLON)
            false_expr = self._parse_expression()
            return ast.TernaryOp(
                condition=expr, true_expr=true_expr,
                false_expr=false_expr, line=expr.line, col=expr.col,
            )
        return expr

    def _parse_or(self) -> ast.Node:
        left = self._parse_and()
        while self._at(TokenType.OR):
            op = self._eat(TokenType.OR)
            right = self._parse_and()
            left = ast.BinaryOp(op="or", left=left, right=right, line=op.line, col=op.col)
        return left

    def _parse_and(self) -> ast.Node:
        left = self._parse_not()
        while self._at(TokenType.AND):
            op = self._eat(TokenType.AND)
            right = self._parse_not()
            left = ast.BinaryOp(op="and", left=left, right=right, line=op.line, col=op.col)
        return left

    def _parse_not(self) -> ast.Node:
        if self._at(TokenType.NOT):
            op = self._eat(TokenType.NOT)
            operand = self._parse_not()
            return ast.UnaryOp(op="not", operand=operand, line=op.line, col=op.col)
        return self._parse_comparison()

    def _parse_comparison(self) -> ast.Node:
        left = self._parse_addition()
        comp_types = {
            TokenType.EQ: "==", TokenType.NEQ: "!=",
            TokenType.LT: "<", TokenType.GT: ">",
            TokenType.LTE: "<=", TokenType.GTE: ">=",
        }
        while self._current().type in comp_types:
            op_tok = self._current()
            op_str = comp_types[op_tok.type]
            self.pos += 1
            right = self._parse_addition()
            left = ast.BinaryOp(op=op_str, left=left, right=right, line=op_tok.line, col=op_tok.col)
        return left

    def _parse_addition(self) -> ast.Node:
        left = self._parse_multiplication()
        while self._at(TokenType.PLUS, TokenType.MINUS):
            op_tok = self._current()
            self.pos += 1
            right = self._parse_multiplication()
            left = ast.BinaryOp(op=op_tok.value, left=left, right=right, line=op_tok.line, col=op_tok.col)
        return left

    def _parse_multiplication(self) -> ast.Node:
        left = self._parse_unary()
        while self._at(TokenType.STAR, TokenType.SLASH, TokenType.PERCENT):
            op_tok = self._current()
            self.pos += 1
            right = self._parse_unary()
            left = ast.BinaryOp(op=op_tok.value, left=left, right=right, line=op_tok.line, col=op_tok.col)
        return left

    def _parse_unary(self) -> ast.Node:
        if self._at(TokenType.MINUS):
            op = self._eat(TokenType.MINUS)
            operand = self._parse_unary()
            return ast.UnaryOp(op="-", operand=operand, line=op.line, col=op.col)
        if self._at(TokenType.PLUS):
            self._eat(TokenType.PLUS)
            return self._parse_unary()
        return self._parse_postfix()

    def _parse_postfix(self) -> ast.Node:
        expr = self._parse_primary()

        while True:
            if self._at(TokenType.LBRACKET):
                self._eat(TokenType.LBRACKET)
                offset = self._parse_expression()
                self._eat(TokenType.RBRACKET)
                expr = ast.HistoryRef(expr=expr, offset=offset, line=expr.line, col=expr.col)
            elif self._at(TokenType.DOT):
                self._eat(TokenType.DOT)
                member = self._eat(TokenType.IDENTIFIER)
                expr = ast.MemberAccess(object=expr, member=member.value, line=expr.line, col=expr.col)
            elif self._at(TokenType.LPAREN) and isinstance(expr, (ast.Identifier, ast.MemberAccess)):
                expr = self._parse_call(expr)
            else:
                break

        return expr

    def _parse_call(self, callee: ast.Node) -> ast.FunctionCall:
        self._eat(TokenType.LPAREN)
        args: list[ast.Node] = []
        kwargs: dict[str, ast.Node] = {}

        while not self._at(TokenType.RPAREN, TokenType.EOF):
            # Check for named arg: identifier = expr
            if (self._at(TokenType.IDENTIFIER) and
                    self._peek(1).type == TokenType.ASSIGN):
                key_tok = self._eat(TokenType.IDENTIFIER)
                self._eat(TokenType.ASSIGN)
                val = self._parse_expression()
                kwargs[key_tok.value] = val
            else:
                args.append(self._parse_expression())

            if not self._match(TokenType.COMMA):
                break

        self._eat(TokenType.RPAREN)

        name = self._resolve_callee_name(callee)
        return ast.FunctionCall(
            name=name, args=args, kwargs=kwargs,
            line=callee.line, col=callee.col,
        )

    def _resolve_callee_name(self, node: ast.Node) -> str:
        if isinstance(node, ast.Identifier):
            return node.name
        if isinstance(node, ast.MemberAccess):
            parent = self._resolve_callee_name(node.object)
            return f"{parent}.{node.member}"
        return "<unknown>"

    def _parse_primary(self) -> ast.Node:
        tok = self._current()

        if tok.type == TokenType.INT:
            self.pos += 1
            return ast.NumberLiteral(value=tok.value, line=tok.line, col=tok.col)

        if tok.type == TokenType.FLOAT:
            self.pos += 1
            return ast.NumberLiteral(value=tok.value, line=tok.line, col=tok.col)

        if tok.type == TokenType.STRING:
            self.pos += 1
            return ast.StringLiteral(value=tok.value, line=tok.line, col=tok.col)

        if tok.type == TokenType.BOOL_TRUE:
            self.pos += 1
            return ast.BoolLiteral(value=True, line=tok.line, col=tok.col)

        if tok.type == TokenType.BOOL_FALSE:
            self.pos += 1
            return ast.BoolLiteral(value=False, line=tok.line, col=tok.col)

        if tok.type == TokenType.NA:
            self.pos += 1
            return ast.NaLiteral(line=tok.line, col=tok.col)

        if tok.type == TokenType.IDENTIFIER:
            self.pos += 1
            return ast.Identifier(name=tok.value, line=tok.line, col=tok.col)

        if tok.type == TokenType.LPAREN:
            self._eat(TokenType.LPAREN)
            expr = self._parse_expression()
            self._eat(TokenType.RPAREN)
            return expr

        if tok.type == TokenType.IF:
            return self._parse_if()

        raise ParseError(f"Unexpected token {tok.type.name} ({tok.value!r})", tok)
