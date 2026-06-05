"""Recursive-descent parser for Proviso.

Grammar (informal):

  module    := fn_decl*
  fn_decl   := 'fn' IDENT '(' params? ')' ('->' type)? ('!' eff_row)? block
  param     := 'linear'? IDENT ':' type
  type      := IDENT ('{' IDENT '|' pred '}')?
  eff_row   := '{' effect (',' effect)* '}' | effect
  effect    := IDENT ('{' IDENT '|' pred '}')?
  block     := '{' (stmt)* expr? '}'
  stmt      := 'let' 'linear'? IDENT (':' type)? '=' expr ';'  |  expr ';'
  expr      := if | handle | logic
  pred      := comparison/boolean expression over the bound variable
"""
from __future__ import annotations

from typing import List, Optional

from .lexer import Token, tokenize
from . import nodes as N
from . import predicate as P


class ParseError(Exception):
    def __init__(self, message: str, line: int, col: int):
        super().__init__(message)
        self.message = message
        self.line = line
        self.col = col


_CMP_OPS = {"<", "<=", ">", ">=", "==", "!="}
_FLIP = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "==": "==", "!=": "!="}


class Parser:
    def __init__(self, toks: List[Token]):
        self.toks = toks
        self.pos = 0

    # --- token helpers ----------------------------------------------------- #
    @property
    def cur(self) -> Token:
        return self.toks[self.pos]

    def at(self, kind: str, value: Optional[str] = None) -> bool:
        t = self.cur
        return t.kind == kind and (value is None or t.value == value)

    def eat(self, kind: str, value: Optional[str] = None) -> Token:
        t = self.cur
        if not self.at(kind, value):
            want = value if value is not None else kind
            raise ParseError(f"expected {want!r}, found {t.value!r}", t.line, t.col)
        self.pos += 1
        return t

    def accept(self, kind: str, value: Optional[str] = None) -> Optional[Token]:
        if self.at(kind, value):
            return self.eat(kind, value)
        return None

    def peek(self, k: int) -> Token:
        return self.toks[min(self.pos + k, len(self.toks) - 1)]

    def _at_refine_brace(self) -> bool:
        # A refinement `{ ident | ... }` is distinguished from a block `{ ... }`
        # by the `ident |` that no expression-statement can begin with.
        return (
            self.at("sym", "{")
            and self.peek(1).kind == "ident"
            and self.peek(2).kind == "sym"
            and self.peek(2).value == "|"
        )

    # --- entry ------------------------------------------------------------- #
    def parse_module(self) -> N.Module:
        decls = []
        aliases = []
        enums = []
        while not self.at("eof"):
            if self.at("kw", "type"):
                aliases.append(self.parse_alias())
            elif self.at("kw", "enum"):
                enums.append(self.parse_enum())
            else:
                decls.append(self.parse_fn())
        return N.Module(decls, aliases, enums)

    def parse_enum(self) -> N.EnumDecl:
        kw = self.eat("kw", "enum")
        name = self.eat("ident").value
        self.eat("sym", "{")
        variants = [self.parse_variant()]
        while self.accept("sym", ","):
            if self.at("sym", "}"):
                break
            variants.append(self.parse_variant())
        self.eat("sym", "}")
        return N.EnumDecl(name, variants, kw.line)

    def parse_variant(self) -> N.Variant:
        t = self.eat("ident")
        fields = []
        if self.accept("sym", "("):
            if not self.at("sym", ")"):
                fields.append(self.parse_type())
                while self.accept("sym", ","):
                    fields.append(self.parse_type())
            self.eat("sym", ")")
        return N.Variant(t.value, fields, t.line)

    # --- declarations ------------------------------------------------------ #
    def parse_alias(self) -> N.TypeAlias:
        kw = self.eat("kw", "type")
        name = self.eat("ident").value
        self.eat("sym", "=")
        ty = self.parse_type()
        self.accept("sym", ";")  # optional terminator
        return N.TypeAlias(name, ty, kw.line)

    def parse_fn(self) -> N.FnDecl:
        kw = self.eat("kw", "fn")
        name = self.eat("ident").value
        self.eat("sym", "(")
        params = []
        if not self.at("sym", ")"):
            params.append(self.parse_param())
            while self.accept("sym", ","):
                params.append(self.parse_param())
        self.eat("sym", ")")
        ret = None
        if self.accept("sym", "->"):
            ret = self.parse_type()
        effects = []
        effects_declared = False
        if self.accept("sym", "!"):
            effects_declared = True
            effects = self.parse_effect_row()
        body = self.parse_block()
        return N.FnDecl(name, params, ret, effects, body, kw.line,
                        effects_declared=effects_declared)

    def parse_param(self) -> N.Param:
        linear = self.accept("kw", "linear") is not None
        t = self.eat("ident")
        self.eat("sym", ":")
        ty = self.parse_type()
        return N.Param(t.value, ty, linear, t.line)

    def parse_type(self) -> N.TypeExpr:
        t = self.eat("ident")
        refine = None
        if self._at_refine_brace():
            self.eat("sym", "{")
            refine = self.parse_refine()
            self.eat("sym", "}")
        return N.TypeExpr(t.value, refine, t.line)

    def parse_refine(self) -> N.RefineExpr:
        var = self.eat("ident").value
        self.eat("sym", "|")
        pred = self.parse_pred(var)
        return N.RefineExpr(var, pred)

    def parse_effect_row(self) -> List[N.EffectExpr]:
        effects = []
        if self.accept("sym", "{"):
            if not self.at("sym", "}"):
                effects.append(self.parse_effect())
                while self.accept("sym", ","):
                    effects.append(self.parse_effect())
            self.eat("sym", "}")
        else:
            effects.append(self.parse_effect())
        return effects

    def parse_effect(self) -> N.EffectExpr:
        t = self.eat("ident")
        refine = None
        if self._at_refine_brace():
            self.eat("sym", "{")
            refine = self.parse_refine()
            self.eat("sym", "}")
        return N.EffectExpr(t.value, refine, t.line)

    # --- refinement predicates -------------------------------------------- #
    def parse_pred(self, var: str) -> P.Pred:
        return self._pred_or(var)

    def _pred_or(self, var: str) -> P.Pred:
        left = self._pred_and(var)
        while self.accept("sym", "||"):
            left = P.POr(left, self._pred_and(var))
        return left

    def _pred_and(self, var: str) -> P.Pred:
        left = self._pred_atom(var)
        while self.accept("sym", "&&"):
            left = P.PAnd(left, self._pred_atom(var))
        return left

    def _pred_atom(self, var: str) -> P.Pred:
        if self.accept("sym", "!"):
            return P.PNot(self._pred_atom(var))
        if self.accept("sym", "("):
            inner = self._pred_or(var)
            self.eat("sym", ")")
            return inner
        if self.at("kw", "true"):
            self.eat("kw", "true")
            return P.PTrue()
        if self.at("kw", "false"):
            self.eat("kw", "false")
            return P.PFalse()
        # relation: term CMP term  (terms may be dependent: other vars, len(x), arith)
        lhs = self._term(var)
        op = self._eat_cmp()
        rhs = self._term(var)
        return self._mk_rel(op, lhs, rhs)

    def _mk_rel(self, op: str, lhs: "P.Term", rhs: "P.Term") -> P.Pred:
        # normalise the common single-variable cases to PCmp so the bundled
        # sampler / interval inference keep working unchanged
        if isinstance(lhs, P.TVal) and isinstance(rhs, P.TInt):
            return P.PCmp(op, rhs.k)
        if isinstance(lhs, P.TInt) and isinstance(rhs, P.TVal):
            return P.PCmp(_FLIP[op], lhs.k)
        return P.PRel(op, lhs, rhs)

    def _term(self, var: str) -> "P.Term":
        left = self._factor(var)
        while self.cur.kind == "sym" and self.cur.value in ("+", "-"):
            op = self.eat("sym").value
            left = P.TArith(op, left, self._factor(var))
        return left

    def _factor(self, var: str) -> "P.Term":
        left = self._term_atom(var)
        while self.cur.kind == "sym" and self.cur.value == "*":
            self.eat("sym", "*")
            left = P.TArith("*", left, self._term_atom(var))
        return left

    def _term_atom(self, var: str) -> "P.Term":
        if self.at("int"):
            return P.TInt(int(self.eat("int").value))
        if self.at("ident"):
            name = self.eat("ident").value
            if name == "len" and self.at("sym", "("):
                self.eat("sym", "(")
                arg = self.eat("ident").value
                self.eat("sym", ")")
                return P.TLen(arg)
            return P.TVal() if name == var else P.TVar(name)
        t = self.cur
        raise ParseError(f"malformed refinement term near {t.value!r}", t.line, t.col)

    def _eat_cmp(self) -> str:
        t = self.cur
        if t.kind == "sym" and t.value in _CMP_OPS:
            self.pos += 1
            return t.value
        raise ParseError(f"expected comparison operator, found {t.value!r}", t.line, t.col)

    # --- statements / blocks ---------------------------------------------- #
    def parse_block(self) -> N.Block:
        open_t = self.eat("sym", "{")
        stmts: List[object] = []
        result: Optional[N.Expr] = None
        while not self.at("sym", "}"):
            if self.at("kw", "let"):
                stmts.append(self.parse_let())
                continue
            expr = self.parse_expr()
            if self.accept("sym", ";"):
                stmts.append(N.ExprStmt(expr, expr.line))
            else:
                result = expr
                break
        self.eat("sym", "}")
        return N.Block(stmts, result, open_t.line)

    def parse_let(self) -> N.LetStmt:
        kw = self.eat("kw", "let")
        linear = self.accept("kw", "linear") is not None
        name = self.eat("ident").value
        ty = None
        if self.accept("sym", ":"):
            ty = self.parse_type()
        self.eat("sym", "=")
        value = self.parse_expr()
        self.eat("sym", ";")
        return N.LetStmt(name, ty, value, linear, kw.line)

    # --- expressions ------------------------------------------------------- #
    def parse_expr(self) -> N.Expr:
        if self.at("kw", "if"):
            return self.parse_if()
        if self.at("kw", "handle"):
            return self.parse_handle()
        if self.at("kw", "match"):
            return self.parse_match()
        return self._logic_or()

    def parse_match(self) -> N.Match:
        kw = self.eat("kw", "match")
        scrutinee = self.parse_expr()
        self.eat("sym", "{")
        arms = [self.parse_arm()]
        while self.accept("sym", ","):
            if self.at("sym", "}"):
                break
            arms.append(self.parse_arm())
        self.eat("sym", "}")
        return N.Match(scrutinee, arms, kw.line)

    def parse_arm(self) -> N.MatchArm:
        t = self.eat("ident")
        ctor = None if t.value == "_" else t.value
        binders = []
        if ctor is not None and self.accept("sym", "("):
            if not self.at("sym", ")"):
                binders.append(self.eat("ident").value)
                while self.accept("sym", ","):
                    binders.append(self.eat("ident").value)
            self.eat("sym", ")")
        self.eat("sym", "=>")
        body = self.parse_expr()
        return N.MatchArm(ctor, binders, body, t.line)

    def parse_if(self) -> N.If:
        kw = self.eat("kw", "if")
        cond = self.parse_expr()
        then = self.parse_block()
        els = None
        if self.accept("kw", "else"):
            els = self.parse_if() if self.at("kw", "if") else self.parse_block()
            if isinstance(els, N.If):
                els = N.Block([], els, els.line)
        return N.If(cond, then, els, kw.line)

    def parse_handle(self) -> N.Handle:
        kw = self.eat("kw", "handle")
        body = self.parse_block()
        self.eat("kw", "catch")
        self.eat("sym", "(")
        binder = self.eat("ident").value
        self.eat("sym", ")")
        handler = self.parse_block()
        return N.Handle(body, binder, handler, kw.line)

    def _logic_or(self) -> N.Expr:
        left = self._logic_and()
        while self.at("sym", "||"):
            op = self.eat("sym").value
            left = N.BinOp(op, left, self._logic_and(), left.line)
        return left

    def _logic_and(self) -> N.Expr:
        left = self._comparison()
        while self.at("sym", "&&"):
            op = self.eat("sym").value
            left = N.BinOp(op, left, self._comparison(), left.line)
        return left

    def _comparison(self) -> N.Expr:
        left = self._additive()
        if self.cur.kind == "sym" and self.cur.value in _CMP_OPS:
            op = self.eat("sym").value
            right = self._additive()
            return N.BinOp(op, left, right, left.line)
        return left

    def _additive(self) -> N.Expr:
        left = self._multiplicative()
        while self.cur.kind == "sym" and self.cur.value in ("+", "-"):
            op = self.eat("sym").value
            left = N.BinOp(op, left, self._multiplicative(), left.line)
        return left

    def _multiplicative(self) -> N.Expr:
        left = self._unary()
        while self.cur.kind == "sym" and self.cur.value in ("*", "/", "%"):
            op = self.eat("sym").value
            left = N.BinOp(op, left, self._unary(), left.line)
        return left

    def _unary(self) -> N.Expr:
        if self.cur.kind == "sym" and self.cur.value in ("-", "!"):
            op = self.eat("sym")
            return N.UnOp(op.value, self._unary(), op.line)
        return self._postfix()

    def _postfix(self) -> N.Expr:
        expr = self._primary()
        while self.at("sym", "(") or self.at("sym", "["):
            if self.at("sym", "["):
                lb = self.eat("sym", "[")
                idx = self.parse_expr()
                self.eat("sym", "]")
                expr = N.Index(expr, idx, lb.line)
                continue
            # call: only on a bare identifier in v1
            if not isinstance(expr, N.Var):
                t = self.cur
                raise ParseError("only named functions are callable", t.line, t.col)
            self.eat("sym", "(")
            args = []
            if not self.at("sym", ")"):
                args.append(self.parse_expr())
                while self.accept("sym", ","):
                    args.append(self.parse_expr())
            self.eat("sym", ")")
            expr = N.Call(expr.name, args, expr.line)
        return expr

    def _primary(self) -> N.Expr:
        t = self.cur
        if t.kind == "int":
            self.eat("int")
            return N.IntLit(int(t.value), t.line)
        if self.at("kw", "true"):
            self.eat("kw", "true")
            return N.BoolLit(True, t.line)
        if self.at("kw", "false"):
            self.eat("kw", "false")
            return N.BoolLit(False, t.line)
        if t.kind == "ident":
            self.eat("ident")
            return N.Var(t.value, t.line)
        if self.at("sym", "("):
            self.eat("sym", "(")
            inner = self.parse_expr()
            self.eat("sym", ")")
            return inner
        if self.at("sym", "["):
            lb = self.eat("sym", "[")
            elems = []
            if not self.at("sym", "]"):
                elems.append(self.parse_expr())
                while self.accept("sym", ","):
                    elems.append(self.parse_expr())
            self.eat("sym", "]")
            return N.ArrayLit(elems, lb.line)
        if self.at("sym", "{"):
            return self.parse_block()
        raise ParseError(f"unexpected {t.value!r}", t.line, t.col)


def parse(src: str) -> N.Module:
    return Parser(tokenize(src)).parse_module()
