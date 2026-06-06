"""Abstract syntax tree for Proviso.

Nodes carry a source location (line) so diagnostics can point precisely.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set

from .predicate import Pred


# --- Type expressions (surface syntax for types) --------------------------- #
@dataclass
class RefineExpr:
    var: str          # the bound variable name, e.g. n in {n | n > 0}
    pred: Pred


@dataclass
class TypeExpr:
    name: str                              # Int, Bool, Unit
    refine: Optional[RefineExpr] = None    # None => gradual (plain `Int`)
    line: int = 0
    state: Optional[str] = None            # typestate (#9): `File @ Open` -> state "Open"


@dataclass
class EffectExpr:
    name: str
    refine: Optional[RefineExpr] = None
    line: int = 0


@dataclass
class FnTypeExpr:
    """Surface syntax for a precise function type: Fn(T, ...) -> T ! {effects}."""
    params: List[TypeExpr]
    ret: TypeExpr
    effects: List[EffectExpr] = field(default_factory=list)
    line: int = 0


# --- Declarations ---------------------------------------------------------- #
@dataclass
class Param:
    name: str
    type: TypeExpr
    linear: bool = False
    line: int = 0


@dataclass
class TypeAlias:
    """`type Name = <type>` — a reusable name for a (possibly refined) type."""
    name: str
    type: TypeExpr
    line: int = 0


@dataclass
class FnDecl:
    name: str
    params: List[Param]
    ret: Optional[TypeExpr]
    effects: List[EffectExpr]
    body: "Block"
    line: int = 0
    effects_declared: bool = False  # was a `!` effect row written? (vs. inferred)


@dataclass
class Variant:
    name: str
    fields: List[TypeExpr]
    line: int = 0


@dataclass
class EnumDecl:
    name: str
    variants: List[Variant]
    line: int = 0


@dataclass
class ProtocolDecl:
    """A typestate protocol (#9): the named states a resource of this type moves
    through, e.g. `protocol File { Closed, Open }`. Operations declare the state they
    require / produce via `@State` annotations on their protocol-typed params/returns."""
    name: str
    states: List[str]
    line: int = 0


@dataclass
class Module:
    decls: List[FnDecl]
    aliases: List[TypeAlias] = field(default_factory=list)
    enums: List[EnumDecl] = field(default_factory=list)
    protocols: List[ProtocolDecl] = field(default_factory=list)


# --- Statements ------------------------------------------------------------ #
@dataclass
class LetStmt:
    name: str
    type: Optional[TypeExpr]
    value: "Expr"
    linear: bool = False
    line: int = 0


@dataclass
class ExprStmt:
    expr: "Expr"
    line: int = 0


# --- Expressions ----------------------------------------------------------- #
class Expr:
    line: int = 0


@dataclass
class IntLit(Expr):
    value: int
    line: int = 0


@dataclass
class BoolLit(Expr):
    value: bool
    line: int = 0


@dataclass
class StrLit(Expr):
    value: str
    line: int = 0


@dataclass
class Var(Expr):
    name: str
    line: int = 0


@dataclass
class BinOp(Expr):
    op: str
    left: Expr
    right: Expr
    line: int = 0


@dataclass
class UnOp(Expr):
    op: str
    operand: Expr
    line: int = 0


@dataclass
class Call(Expr):
    fn: str
    args: List[Expr]
    line: int = 0
    # #8 contract erasure: arg indices whose refinement the checker could NOT prove,
    # so they need a runtime contract check. The checker fills this in; the rest are
    # erased (cost nothing at runtime). `None` => checker never ran => check all args
    # (the sound fallback).
    runtime_checks: Optional[Set[int]] = None


@dataclass
class ArrayLit(Expr):
    elements: List[Expr]
    line: int = 0


@dataclass
class Index(Expr):
    arr: Expr
    idx: Expr
    line: int = 0
    # #8 contract erasure: whether the 0 <= i < len(arr) bounds obligation needs a
    # runtime check. The checker sets it False when the access is statically proven
    # in range. Defaults True so an unchecked program stays sound.
    needs_check: bool = True


@dataclass
class If(Expr):
    cond: Expr
    then: "Block"
    els: Optional["Block"]
    line: int = 0


@dataclass
class Handle(Expr):
    """handle <body> catch (name) <handler>  -- first-class discharge of the Exc effect."""
    body: "Block"
    binder: str
    handler: "Block"
    line: int = 0


@dataclass
class Lambda(Expr):
    """An anonymous first-class function value: fn(params) -> ret { body }."""
    params: List[Param]
    ret: Optional[TypeExpr]
    body: "Block"
    line: int = 0


@dataclass
class Perform(Expr):
    """perform Op(arg) -- invoke an algebraic effect operation."""
    op: str
    arg: Expr
    line: int = 0


@dataclass
class OpClause:
    op: str          # operation name handled
    param: str       # binds the operation's argument
    kbinder: str     # binds the (multi-shot) resumption continuation
    body: "Expr"
    line: int = 0


@dataclass
class RetClause:
    binder: str
    body: "Expr"
    line: int = 0


@dataclass
class HandleWith(Expr):
    """handle <body> with { Op(x, k) => ..., return(v) => ... } -- algebraic effects."""
    body: "Block"
    clauses: List[OpClause]
    ret: Optional[RetClause] = None
    line: int = 0


# --- patterns (for match) -------------------------------------------------- #
class Pattern:
    line: int = 0


@dataclass
class PatWild(Pattern):
    line: int = 0


@dataclass
class PatVar(Pattern):
    name: str = ""
    line: int = 0


@dataclass
class PatLit(Pattern):
    kind: str = "int"   # "int" | "bool" | "str"
    value: object = None
    line: int = 0


@dataclass
class PatCtor(Pattern):
    name: str = ""
    args: List["Pattern"] = field(default_factory=list)
    line: int = 0


@dataclass
class MatchArm:
    pattern: Pattern
    body: Expr = None
    line: int = 0


@dataclass
class Match(Expr):
    scrutinee: Expr = None
    arms: List[MatchArm] = field(default_factory=list)
    line: int = 0


@dataclass
class Block(Expr):
    stmts: List[object] = field(default_factory=list)  # LetStmt | ExprStmt
    result: Optional[Expr] = None  # trailing expression (the block's value)
    line: int = 0
