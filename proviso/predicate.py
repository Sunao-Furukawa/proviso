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

# --------------------------------------------------------------------------- #
# Solver backend selection.
#
# The whole point of the `implies(P, Q) -> counterexample | None` interface is that
# it can be backed by anything that decides the logic and produces a witness.  If the
# `z3-solver` package is importable we use Z3 (sound, complete, and far beyond the
# single-variable fragment the bundled sampler can decide); otherwise we fall back to
# the pure-Python sampler so the language still runs with zero dependencies.
# Set PROVISO_SOLVER=sampler to force the fallback (used in tests).
# --------------------------------------------------------------------------- #
import os as _os

try:
    import z3 as _z3  # type: ignore
    _HAVE_Z3 = True
except Exception:  # pragma: no cover - depends on environment
    _z3 = None
    _HAVE_Z3 = False


def _use_z3() -> bool:
    return _HAVE_Z3 and _os.environ.get("PROVISO_SOLVER", "").lower() != "sampler"


def solver_backend() -> str:
    """Name of the active backend: 'z3' or 'sampler'."""
    return "z3" if _use_z3() else "sampler"


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


# --------------------------------------------------------------------------- #
# Terms -- the building blocks of *dependent* refinements (#5).  A refinement may
# now relate the value to other in-scope variables and to the `len(x)` measure,
# e.g.  Int{k | k >= 0 && k < len(xs)}  or  Int{h | h >= lo}.
# --------------------------------------------------------------------------- #
class Term:
    pass


@dataclass(frozen=True)
class TVal(Term):
    def __str__(self) -> str:
        return "value"


@dataclass(frozen=True)
class TInt(Term):
    k: int

    def __str__(self) -> str:
        return str(self.k)


@dataclass(frozen=True)
class TVar(Term):
    name: str

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class TLen(Term):
    name: str

    def __str__(self) -> str:
        return f"len({self.name})"


@dataclass(frozen=True)
class TArith(Term):
    op: str  # + - *
    a: Term
    b: Term

    def __str__(self) -> str:
        return f"({self.a} {self.op} {self.b})"


@dataclass(frozen=True)
class PRel(Pred):
    """A comparison between two terms (the general, possibly-dependent atom)."""
    op: str
    lhs: Term
    rhs: Term

    def __str__(self) -> str:
        return f"{self.lhs} {self.op} {self.rhs}"


def is_dependent(p: Pred) -> bool:
    """True if the predicate mentions any variable other than the bound value."""
    return bool(free_names(p))


def free_names(p: Pred) -> set:
    out: set = set()
    _free_names(p, out)
    return out


def _free_names(p: Pred, out: set) -> None:
    if isinstance(p, PRel):
        _term_names(p.lhs, out)
        _term_names(p.rhs, out)
    elif isinstance(p, (PAnd, POr)):
        _free_names(p.a, out)
        _free_names(p.b, out)
    elif isinstance(p, PNot):
        _free_names(p.a, out)


def _term_names(t: Term, out: set) -> None:
    if isinstance(t, (TVar, TLen)):
        out.add(t.name)
    elif isinstance(t, TArith):
        _term_names(t.a, out)
        _term_names(t.b, out)


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


def eval_term(t: Term, x: int, env: dict) -> int:
    if isinstance(t, TVal):
        return x
    if isinstance(t, TInt):
        return t.k
    if isinstance(t, TVar):
        return int(env[t.name])
    if isinstance(t, TLen):
        return len(env[t.name])
    if isinstance(t, TArith):
        a, b = eval_term(t.a, x, env), eval_term(t.b, x, env)
        return {"+": a + b, "-": a - b, "*": a * b}[t.op]
    raise TypeError(f"unknown term node: {t!r}")


def eval_pred(p: Pred, x: int, env: dict = None) -> bool:
    if isinstance(p, PTrue):
        return True
    if isinstance(p, PFalse):
        return False
    if isinstance(p, PCmp):
        return _CMP[p.op](x, p.const)
    if isinstance(p, PRel):
        env = env or {}
        return _CMP[p.op](eval_term(p.lhs, x, env), eval_term(p.rhs, x, env))
    if isinstance(p, PAnd):
        return eval_pred(p.a, x, env) and eval_pred(p.b, x, env)
    if isinstance(p, POr):
        return eval_pred(p.a, x, env) or eval_pred(p.b, x, env)
    if isinstance(p, PNot):
        return not eval_pred(p.a, x, env)
    raise TypeError(f"unknown predicate node: {p!r}")


# --- substitution / renaming (used to instantiate dependent refinements) ----- #
def subst_value(p: Pred, term: Term) -> Pred:
    """Replace the bound value (TVal / the implicit value of PCmp) with `term`."""
    if isinstance(p, PCmp):
        return PRel(p.op, term, TInt(p.const))
    if isinstance(p, PRel):
        return PRel(p.op, _subst_value_t(p.lhs, term), _subst_value_t(p.rhs, term))
    if isinstance(p, PAnd):
        return PAnd(subst_value(p.a, term), subst_value(p.b, term))
    if isinstance(p, POr):
        return POr(subst_value(p.a, term), subst_value(p.b, term))
    if isinstance(p, PNot):
        return PNot(subst_value(p.a, term))
    return p


def _subst_value_t(t: Term, term: Term) -> Term:
    if isinstance(t, TVal):
        return term
    if isinstance(t, TArith):
        return TArith(t.op, _subst_value_t(t.a, term), _subst_value_t(t.b, term))
    return t


def rename(p: Pred, mapping: dict) -> Pred:
    """Rename free variable names (formal params -> actual argument names)."""
    if isinstance(p, PCmp):
        return p
    if isinstance(p, PRel):
        return PRel(p.op, _rename_t(p.lhs, mapping), _rename_t(p.rhs, mapping))
    if isinstance(p, PAnd):
        return PAnd(rename(p.a, mapping), rename(p.b, mapping))
    if isinstance(p, POr):
        return POr(rename(p.a, mapping), rename(p.b, mapping))
    if isinstance(p, PNot):
        return PNot(rename(p.a, mapping))
    return p


def _rename_t(t: Term, mapping: dict) -> Term:
    if isinstance(t, TVar):
        return TVar(mapping.get(t.name, t.name))
    if isinstance(t, TLen):
        return TLen(mapping.get(t.name, t.name))
    if isinstance(t, TArith):
        return TArith(t.op, _rename_t(t.a, mapping), _rename_t(t.b, mapping))
    return t


def subst_names(p: Pred, term_map: dict) -> Pred:
    """Substitute free variable names with arbitrary terms (formal params -> actual args).

    `term_map` maps a name to a Term. A bare variable use becomes that term; a `len(name)`
    use resolves to `len(actual)` when the actual is itself a variable, else is left free.
    """
    if isinstance(p, PCmp):
        return p
    if isinstance(p, PRel):
        return PRel(p.op, _subst_names_t(p.lhs, term_map), _subst_names_t(p.rhs, term_map))
    if isinstance(p, PAnd):
        return PAnd(subst_names(p.a, term_map), subst_names(p.b, term_map))
    if isinstance(p, POr):
        return POr(subst_names(p.a, term_map), subst_names(p.b, term_map))
    if isinstance(p, PNot):
        return PNot(subst_names(p.a, term_map))
    return p


def _subst_names_t(t: Term, term_map: dict) -> Term:
    if isinstance(t, TVar):
        return term_map.get(t.name, t)
    if isinstance(t, TLen):
        r = term_map.get(t.name)
        if isinstance(r, TVar):
            return TLen(r.name)
        return t
    if isinstance(t, TArith):
        return TArith(t.op, _subst_names_t(t.a, term_map), _subst_names_t(t.b, term_map))
    return t


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


# --- pure-Python fallback (complete for the single-variable comparison fragment) -- #
def _sampler_implies(p: Pred, q: Pred) -> Optional[int]:
    for x in _candidate_points([p, q]):
        if eval_pred(p, x) and not eval_pred(q, x):
            return x
    return None


def _sampler_witness(p: Pred) -> Optional[int]:
    for x in _candidate_points([p]):
        if eval_pred(p, x):
            return x
    return None


# --- Z3 backend -------------------------------------------------------------- #
def _to_z3(p: Pred, v):
    if isinstance(p, PTrue):
        return _z3.BoolVal(True)
    if isinstance(p, PFalse):
        return _z3.BoolVal(False)
    if isinstance(p, PCmp):
        c = p.const
        return {
            "<": v < c, "<=": v <= c, ">": v > c, ">=": v >= c,
            "==": v == c, "!=": v != c,
        }[p.op]
    if isinstance(p, PAnd):
        return _z3.And(_to_z3(p.a, v), _to_z3(p.b, v))
    if isinstance(p, POr):
        return _z3.Or(_to_z3(p.a, v), _to_z3(p.b, v))
    if isinstance(p, PNot):
        return _z3.Not(_to_z3(p.a, v))
    raise TypeError(f"unknown predicate node: {p!r}")


def _z3_model_value(solver, v) -> int:
    m = solver.model()
    val = m[v]
    return val.as_long() if val is not None else 0


def _z3_implies(p: Pred, q: Pred) -> Optional[int]:
    v = _z3.Int("v")
    s = _z3.Solver()
    s.add(_z3.And(_to_z3(p, v), _z3.Not(_to_z3(q, v))))  # P & not Q -> a counterexample
    if s.check() == _z3.sat:
        return _z3_model_value(s, v)
    return None


def _z3_witness(p: Pred) -> Optional[int]:
    v = _z3.Int("v")
    s = _z3.Solver()
    s.add(_to_z3(p, v))
    if s.check() == _z3.sat:
        return _z3_model_value(s, v)
    return None


class NotDecidable(Exception):
    """Raised when no SMT backend is available to decide a dependent obligation."""


def z3_discharge(assumptions: "list[Pred]", goal: Pred):
    """Decide  (AND assumptions) => goal  for *closed* predicates (no bound value left).

    Returns None when proven, or a dict {name: int} counterexample model when not.
    Raises NotDecidable if Z3 is unavailable.
    """
    if not _HAVE_Z3:
        raise NotDecidable()
    cache: dict = {}

    def zname(kind: str, nm: str):
        key = (kind, nm)
        if key not in cache:
            cache[key] = _z3.Int(("len_" + nm) if kind == "len" else nm)
        return cache[key]

    def tz(t: Term):
        if isinstance(t, TInt):
            return t.k
        if isinstance(t, TVal):
            return zname("var", "value")
        if isinstance(t, TVar):
            return zname("var", t.name)
        if isinstance(t, TLen):
            return zname("len", t.name)
        if isinstance(t, TArith):
            a, b = tz(t.a), tz(t.b)
            return {"+": a + b, "-": a - b, "*": a * b}[t.op]
        raise TypeError(t)

    def pz(p: Pred):
        if isinstance(p, PTrue):
            return _z3.BoolVal(True)
        if isinstance(p, PFalse):
            return _z3.BoolVal(False)
        if isinstance(p, PCmp):
            return _rel(zname("var", "value"), p.op, p.const)
        if isinstance(p, PRel):
            return _rel(tz(p.lhs), p.op, tz(p.rhs))
        if isinstance(p, PAnd):
            return _z3.And(pz(p.a), pz(p.b))
        if isinstance(p, POr):
            return _z3.Or(pz(p.a), pz(p.b))
        if isinstance(p, PNot):
            return _z3.Not(pz(p.a))
        raise TypeError(p)

    s = _z3.Solver()
    facts = [pz(a) for a in assumptions]
    neg_goal = _z3.Not(pz(goal))
    # every `len` measure is non-negative
    for (kind, nm), zv in list(cache.items()):
        if kind == "len":
            s.add(zv >= 0)
    for f in facts:
        s.add(f)
    s.add(neg_goal)
    if s.check() == _z3.sat:
        m = s.model()
        out = {}
        for (kind, nm), zv in cache.items():
            label = f"len({nm})" if kind == "len" else nm
            val = m[zv]
            out[label] = val.as_long() if val is not None else 0
        return out
    return None


def _rel(a, op: str, b):
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b,
            "==": a == b, "!=": a != b}[op]


def z3_consistent(preds: "list[Pred]") -> bool:
    """True if all `preds` (closed) can hold simultaneously. Raises if no Z3."""
    if not _HAVE_Z3:
        raise NotDecidable()
    cache: dict = {}

    def zname(kind, nm):
        key = (kind, nm)
        if key not in cache:
            cache[key] = _z3.Int(("len_" + nm) if kind == "len" else nm)
        return cache[key]

    def tz(t: Term):
        if isinstance(t, TInt):
            return t.k
        if isinstance(t, TVal):
            return zname("var", "value")
        if isinstance(t, TVar):
            return zname("var", t.name)
        if isinstance(t, TLen):
            return zname("len", t.name)
        if isinstance(t, TArith):
            a, b = tz(t.a), tz(t.b)
            return {"+": a + b, "-": a - b, "*": a * b}[t.op]
        raise TypeError(t)

    def pz(p: Pred):
        if isinstance(p, PTrue):
            return _z3.BoolVal(True)
        if isinstance(p, PFalse):
            return _z3.BoolVal(False)
        if isinstance(p, PCmp):
            return _rel(zname("var", "value"), p.op, p.const)
        if isinstance(p, PRel):
            return _rel(tz(p.lhs), p.op, tz(p.rhs))
        if isinstance(p, PAnd):
            return _z3.And(pz(p.a), pz(p.b))
        if isinstance(p, POr):
            return _z3.Or(pz(p.a), pz(p.b))
        if isinstance(p, PNot):
            return _z3.Not(pz(p.a))
        raise TypeError(p)

    s = _z3.Solver()
    body = [pz(p) for p in preds]
    for (kind, nm), zv in list(cache.items()):
        if kind == "len":
            s.add(zv >= 0)
    for b in body:
        s.add(b)
    return s.check() == _z3.sat


# --- public dispatchers ------------------------------------------------------ #
def implies(p: Pred, q: Pred) -> Optional[int]:
    """Return None if P => Q holds; otherwise a counterexample x (P(x) & not Q(x))."""
    return _z3_implies(p, q) if _use_z3() else _sampler_implies(p, q)


def witness(p: Pred) -> Optional[int]:
    """Return a value satisfying P, or None if P is unsatisfiable."""
    return _z3_witness(p) if _use_z3() else _sampler_witness(p)


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
