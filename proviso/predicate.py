"""The refinement-predicate solver -- the shared engine behind all three of Proviso's axes.

Refinements are predicates over a single implicit "value" variable, drawn from the
decidable fragment of linear integer arithmetic with comparisons and boolean
connectives, e.g.  {n | n > 0 && n < 10}.

The solver does two jobs, and crucially *both produce counterexamples*:

  - implies(P, Q):  is every value satisfying P also satisfying Q?
                    If not, returns a concrete witness x where P(x) and not Q(x).
  - witness(P):     find any value satisfying P (used to detect impossible refinements).

The counterexample is what lets the compiler speak in the dialogue style the design
calls for: "these two constraints conflict; here is a value that proves it."
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Set, List

NEG_INF = float("-inf")
POS_INF = float("inf")


class Pred:
    """Base class for refinement predicates over the implicit value variable."""


@dataclass(frozen=True)
class PTrue(Pred):
    def __str__(self) -> str:
        return "true"


@dataclass(frozen=True)
class PFalse(Pred):
    def __str__(self) -> str:
        return "false"


@dataclass(frozen=True)
class PCmp(Pred):
    op: str  # one of:  <  <=  >  >=  ==  !=
    const: int

    def __str__(self) -> str:
        return f"value {self.op} {self.const}"


@dataclass(frozen=True)
class PAnd(Pred):
    a: Pred
    b: Pred

    def __str__(self) -> str:
        return f"({self.a} && {self.b})"


@dataclass(frozen=True)
class POr(Pred):
    a: Pred
    b: Pred

    def __str__(self) -> str:
        return f"({self.a} || {self.b})"


@dataclass(frozen=True)
class PNot(Pred):
    a: Pred

    def __str__(self) -> str:
        return f"!({self.a})"


def render(pred: Pred, var: str) -> str:
    """Pretty-print a predicate using the bound variable's display name."""
    return str(pred).replace("value", var)


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #
_CMP = {
    "<": lambda x, c: x < c,
    "<=": lambda x, c: x <= c,
    ">": lambda x, c: x > c,
    ">=": lambda x, c: x >= c,
    "==": lambda x, c: x == c,
    "!=": lambda x, c: x != c,
}


def eval_pred(p: Pred, x: int) -> bool:
    if isinstance(p, PTrue):
        return True
    if isinstance(p, PFalse):
        return False
    if isinstance(p, PCmp):
        return _CMP[p.op](x, p.const)
    if isinstance(p, PAnd):
        return eval_pred(p.a, x) and eval_pred(p.b, x)
    if isinstance(p, POr):
        return eval_pred(p.a, x) or eval_pred(p.b, x)
    if isinstance(p, PNot):
        return not eval_pred(p.a, x)
    raise TypeError(f"unknown predicate node: {p!r}")


def _constants(p: Pred, out: Set[int]) -> None:
    if isinstance(p, PCmp):
        out.add(p.const)
    elif isinstance(p, (PAnd, POr)):
        _constants(p.a, out)
        _constants(p.b, out)
    elif isinstance(p, PNot):
        _constants(p.a, out)


def _candidate_points(preds: List[Pred]) -> List[int]:
    """Interesting test points.

    For predicates that are boolean combinations of single-variable comparisons,
    the solution set only changes shape at the constants that appear in them.  So
    sampling every constant and its neighbours is complete for this fragment: if a
    counterexample exists at all, one exists among these points.
    """
    cs: Set[int] = set()
    for p in preds:
        _constants(p, cs)
    pts: Set[int] = {0, 1, -1, 2, -2, 10**9, -(10**9)}
    for c in cs:
        pts.update({c - 1, c, c + 1})
    return sorted(pts)


def implies(p: Pred, q: Pred) -> Optional[int]:
    """Return None if P => Q holds; otherwise a counterexample x (P(x) & not Q(x))."""
    for x in _candidate_points([p, q]):
        if eval_pred(p, x) and not eval_pred(q, x):
            return x
    return None


def witness(p: Pred) -> Optional[int]:
    """Return a value satisfying P, or None if P looks unsatisfiable."""
    for x in _candidate_points([p]):
        if eval_pred(p, x):
            return x
    return None


def equivalent(p: Pred, q: Pred) -> bool:
    return implies(p, q) is None and implies(q, p) is None


# --------------------------------------------------------------------------- #
# Interval abstraction -- used to *infer* refinements through arithmetic.
# Returns the tightest [lo, hi] hull of the solution set (conservative on Or/Not).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Interval:
    lo: float
    hi: float

    @property
    def empty(self) -> bool:
        return self.lo > self.hi

    @property
    def whole(self) -> bool:
        return self.lo == NEG_INF and self.hi == POS_INF


def interval_of(p: Pred) -> Interval:
    if isinstance(p, PTrue):
        return Interval(NEG_INF, POS_INF)
    if isinstance(p, PFalse):
        return Interval(POS_INF, NEG_INF)  # empty
    if isinstance(p, PCmp):
        c = p.const
        return {
            "<": Interval(NEG_INF, c - 1),
            "<=": Interval(NEG_INF, c),
            ">": Interval(c + 1, POS_INF),
            ">=": Interval(c, POS_INF),
            "==": Interval(c, c),
            "!=": Interval(NEG_INF, POS_INF),  # hull of a punctured line
        }[p.op]
    if isinstance(p, PAnd):
        a, b = interval_of(p.a), interval_of(p.b)
        return Interval(max(a.lo, b.lo), min(a.hi, b.hi))
    if isinstance(p, POr):
        a, b = interval_of(p.a), interval_of(p.b)
        return Interval(min(a.lo, b.lo), max(a.hi, b.hi))
    # Negation and anything else: give up precisely, keep it sound as a hull.
    return Interval(NEG_INF, POS_INF)


def pred_of_interval(iv: Interval) -> Pred:
    if iv.empty:
        return PFalse()
    if iv.whole:
        return PTrue()
    parts: List[Pred] = []
    if iv.lo != NEG_INF:
        parts.append(PCmp(">=", int(iv.lo)))
    if iv.hi != POS_INF:
        parts.append(PCmp("<=", int(iv.hi)))
    if not parts:
        return PTrue()
    out = parts[0]
    for p in parts[1:]:
        out = PAnd(out, p)
    return out


def _shift(iv: Interval, k: int) -> Interval:
    lo = iv.lo + k if iv.lo != NEG_INF else NEG_INF
    hi = iv.hi + k if iv.hi != POS_INF else POS_INF
    return Interval(lo, hi)


def interval_add(a: Interval, b: Interval) -> Interval:
    lo = NEG_INF if (a.lo == NEG_INF or b.lo == NEG_INF) else a.lo + b.lo
    hi = POS_INF if (a.hi == POS_INF or b.hi == POS_INF) else a.hi + b.hi
    return Interval(lo, hi)


def interval_sub(a: Interval, b: Interval) -> Interval:
    return interval_add(a, Interval(-b.hi, -b.lo))


def interval_mul(a: Interval, b: Interval) -> Interval:
    # Only attempt the common case of non-negative operands precisely;
    # otherwise fall back to the whole line (sound).
    if a.lo >= 0 and b.lo >= 0 and a.lo != NEG_INF and b.lo != NEG_INF:
        lo = a.lo * b.lo
        hi = POS_INF if (a.hi == POS_INF or b.hi == POS_INF) else a.hi * b.hi
        return Interval(lo, hi)
    return Interval(NEG_INF, POS_INF)
