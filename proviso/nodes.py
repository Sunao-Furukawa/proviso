"""Abstract syntax tree for Proviso.

Nodes carry a source location (line) so diagnostics can point precisely.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

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


@dataclass
class EffectExpr:
    name: str
    refine: Optional[RefineExpr] = None
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
class Module:
    decls: List[FnDecl]
    aliases: List[TypeAlias] = field(default_factory=list)


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
class Block(Expr):
    stmts: List[object] = field(default_factory=list)  # LetStmt | ExprStmt
    result: Optional[Expr] = None  # trailing expression (the block's value)
    line: int = 0
