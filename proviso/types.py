"""Semantic types -- what the checker actually reasons about.

A refinement is either a predicate (a *paid-for* guarantee) or UNKNOWN, written `?`,
which is the gradual element: an unrefined `Int` is `Int{v | ?}`.  Unknown refinements
are *consistent* with everything and defer to a runtime check, which is exactly how Proviso
lets you not pay for a proof until you want to.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import predicate as P
from .predicate import Pred, render


@dataclass
class Refinement:
    var: str
    pred: Optional[Pred]  # None  =>  ? (gradual / unknown)

    @property
    def unknown(self) -> bool:
        return self.pred is None

    @staticmethod
    def gradual(var: str = "v") -> "Refinement":
        return Refinement(var, None)

    @staticmethod
    def exactly(value: int) -> "Refinement":
        return Refinement("v", P.PCmp("==", value))

    def __str__(self) -> str:
        if self.unknown:
            return "?"
        return render(self.pred, self.var)


class Type:
    pass


@dataclass
class BaseType(Type):
    name: str             # Int | Bool | Unit
    refine: Refinement = field(default_factory=Refinement.gradual)

    def __str__(self) -> str:
        if self.name != "Int":
            return self.name
        if self.refine.unknown:
            return "Int"
        return f"Int{{{self.refine.var} | {render(self.refine.pred, self.refine.var)}}}"


@dataclass
class Effect:
    name: str
    refine: Optional[Refinement] = None

    def __str__(self) -> str:
        if self.refine is None or self.refine.unknown:
            return self.name
        return f"{self.name}{{{self.refine.var} | {render(self.refine.pred, self.refine.var)}}}"


@dataclass
class FnType(Type):
    params: List[Tuple[str, Type, bool]]  # (name, type, linear)
    ret: Type
    effects: List[Effect] = field(default_factory=list)

    def __str__(self) -> str:
        ps = ", ".join(str(t) for _, t, _ in self.params)
        eff = ""
        if self.effects:
            eff = " ! {" + ", ".join(str(e) for e in self.effects) + "}"
        return f"({ps}) -> {self.ret}{eff}"


def INT(pred: Optional[Pred] = None, var: str = "v") -> BaseType:
    return BaseType("Int", Refinement(var, pred) if pred is not None else Refinement.gradual(var))


BOOL = BaseType("Bool", Refinement.gradual("b"))
UNIT = BaseType("Unit", Refinement.gradual("u"))
ARRAY = BaseType("Array", Refinement.gradual("a"))  # array of Int (monomorphic in v0.1)
FUNC = BaseType("Fn", Refinement.gradual("f"))      # first-class function value (gradual)
STR = BaseType("Str", Refinement.gradual("s"))      # string


@dataclass
class DataType(Type):
    """A user-defined sum type (enum). Records are single-variant enums."""
    name: str

    def __str__(self) -> str:
        return self.name


def effects_str(effects: List[Effect]) -> str:
    return "{" + ", ".join(str(e) for e in effects) + "}" if effects else "{}"
