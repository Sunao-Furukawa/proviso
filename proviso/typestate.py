"""Typestate -- a resource's *state* tracked through its type (#9).

The third axis already treats ownership as an effect; typestate is its natural
companion. A `protocol` names the states a resource moves through:

    protocol File { Closed, Open }

Operations annotate the state they *require* and *produce* with `@State` on their
protocol-typed parameters and result:

    fn open(f: File @ Closed) -> File @ Open { ... }   # Closed -> Open
    fn read(f: File @ Open)   -> File @ Open { ... }   # Open   -> Open
    fn close(f: File @ Open)  -> File @ Closed { ... } # Open   -> Closed

This pass walks each function body tracking, for every binding, the protocol state
it currently holds (seeded from parameters and from each operation's result state,
threaded through `let` re-bindings and merged across `if`/`match`). Calling an
operation on a resource that is in the wrong state is rejected with the usual
dialogue: required / known state, the point the resource entered its current state,
and two ways forward -- ADVANCE it to the required state, or use an operation that
is valid in the state it is already in.

State is *gradual*: a binding whose state cannot be determined (an un-annotated
parameter, or a value merged from disagreeing branches) is left unknown, and a call
on an unknown-state resource is accepted silently. Only a provably-wrong state is an
error. `@State` is erased at runtime -- operations are ordinary functions.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from . import nodes as N
from .diagnostics import Diagnostic, Choice

# A binding's tracked state: (protocol, state-or-None, line-it-entered-that-state).
Cell = Tuple[str, Optional[str], int]


class _OpSig:
    """The typestate signature of a user function: which params require a protocol
    state, and what protocol/state the result produces."""

    def __init__(self, name: str):
        self.name = name
        self.params: List[Optional[Tuple[str, str]]] = []  # per param: (proto, state)|None
        self.ret: Optional[Tuple[str, str]] = None         # (proto, state)|None


class TypestateChecker:
    def __init__(self, module: N.Module, source: str):
        self.module = module
        self.src_lines = source.splitlines()
        self.diags: List[Diagnostic] = []
        self.protocols: Dict[str, List[str]] = {
            p.name: list(p.states) for p in getattr(module, "protocols", [])
        }
        # a state name maps back to the protocol that declares it, so an operation can
        # annotate any carrier type (`Handle @ Open`, `Int @ Open`) and we still know
        # which protocol the state belongs to.
        self.state_proto: Dict[str, str] = {}
        for p in getattr(module, "protocols", []):
            for s in p.states:
                self.state_proto[s] = p.name
        self.sigs: Dict[str, _OpSig] = {}
        for d in module.decls:
            self.sigs[d.name] = self._signature(d)
        # transition table: (proto, from_state) -> list of (op, to_state)
        self.transitions: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}
        # operations valid in a state: (proto, state) -> list of op names
        self.valid_in: Dict[Tuple[str, str], List[str]] = {}
        for d in module.decls:
            sig = self.sigs[d.name]
            subj = self._subject(sig)  # the first protocol param, if any
            if subj is None:
                continue
            proto, frm = subj
            self.valid_in.setdefault((proto, frm), []).append(d.name)
            if sig.ret is not None and sig.ret[0] == proto and sig.ret[1] != frm:
                self.transitions.setdefault((proto, frm), []).append((d.name, sig.ret[1]))

    # --- signatures ------------------------------------------------------- #
    def _cell_of(self, te: Optional[N.TypeExpr]) -> Optional[Tuple[str, str]]:
        if not isinstance(te, N.TypeExpr) or te.state is None:
            return None
        proto = self.state_proto.get(te.state)
        if proto is None:
            return None  # `@State` names no declared protocol state -> ignore
        return (proto, te.state)

    def _signature(self, d: N.FnDecl) -> _OpSig:
        sig = _OpSig(d.name)
        for p in d.params:
            sig.params.append(self._cell_of(p.type))
        sig.ret = self._cell_of(d.ret)
        return sig

    def _subject(self, sig: _OpSig) -> Optional[Tuple[str, str]]:
        for p in sig.params:
            if p is not None:
                return p
        return None

    def src_line(self, line: int) -> Optional[str]:
        if 1 <= line <= len(self.src_lines):
            return self.src_lines[line - 1]
        return None

    # --- entry ------------------------------------------------------------ #
    def run(self) -> List[Diagnostic]:
        if not self.protocols:
            return self.diags  # nothing to check
        for d in self.module.decls:
            self._check_fn(d)
        return self.diags

    def _check_fn(self, d: N.FnDecl) -> None:
        self.state: Dict[str, Cell] = {}
        for p in d.params:
            c = self._cell_of(p.type)
            if c is not None:
                self.state[p.name] = (c[0], c[1], d.line)
        self._block(d.body)

    # --- traversal -------------------------------------------------------- #
    def _block(self, b: N.Block) -> None:
        for st in b.stmts:
            if isinstance(st, N.LetStmt):
                self._expr(st.value)
                self.state[st.name] = self._result_state(st.value, st.line)
            elif isinstance(st, N.ExprStmt):
                self._expr(st.expr)
        if b.result is not None:
            self._expr(b.result)

    def _result_state(self, e: N.Expr, line: int) -> Optional[Cell]:
        """The protocol state a value expression yields, if statically known."""
        if isinstance(e, N.Call) and e.fn in self.sigs:
            ret = self.sigs[e.fn].ret
            if ret is not None:
                return (ret[0], ret[1], line)
        if isinstance(e, N.Var):
            return self.state.get(e.name)
        return None

    def _expr(self, e: N.Expr) -> None:
        if isinstance(e, N.Call):
            for a in e.args:
                self._expr(a)
            self._check_call(e)
        elif isinstance(e, N.BinOp):
            self._expr(e.left)
            self._expr(e.right)
        elif isinstance(e, N.UnOp):
            self._expr(e.operand)
        elif isinstance(e, N.Block):
            self._block(e)
        elif isinstance(e, N.If):
            self._expr(e.cond)
            pre = dict(self.state)
            self._block(e.then)
            then_env = dict(self.state)
            self.state = dict(pre)
            if e.els is not None:
                self._block(e.els)
            self.state = self._merge(then_env, dict(self.state))
        elif isinstance(e, N.ArrayLit):
            for el in e.elements:
                self._expr(el)
        elif isinstance(e, N.Index):
            self._expr(e.arr)
            self._expr(e.idx)
        elif isinstance(e, N.Match):
            self._expr(e.scrutinee)
            pre = dict(self.state)
            merged: Optional[Dict[str, Cell]] = None
            for arm in e.arms:
                self.state = dict(pre)
                self._expr(arm.body)
                merged = dict(self.state) if merged is None \
                    else self._merge(merged, dict(self.state))
            if merged is not None:
                self.state = merged
        elif isinstance(e, N.Handle):
            self._block(e.body)
            self._block(e.handler)
        elif isinstance(e, N.HandleWith):
            self._block(e.body)
            for c in e.clauses:
                self._expr(c.body)
            if e.ret is not None:
                self._expr(e.ret.body)
        elif isinstance(e, N.Perform):
            self._expr(e.arg)
        elif isinstance(e, N.Lambda):
            self._block(e.body)
        # literals / vars: nothing to do

    def _merge(self, a: Dict[str, Cell], b: Dict[str, Cell]) -> Dict[str, Cell]:
        """Branch join: a binding keeps its state only where both branches agree;
        otherwise its state becomes unknown (so no later call is wrongly rejected)."""
        out: Dict[str, Cell] = {}
        for k in set(a) | set(b):
            ca, cb = a.get(k), b.get(k)
            if ca is not None and cb is not None and ca[0] == cb[0] and ca[1] == cb[1]:
                out[k] = ca
            elif ca is not None and cb is not None and ca[0] == cb[0]:
                out[k] = (ca[0], None, ca[2])  # same protocol, divergent state -> unknown
            elif ca is not None:
                out[k] = ca
            elif cb is not None:
                out[k] = cb
        return out

    # --- the rule --------------------------------------------------------- #
    def _check_call(self, e: N.Call) -> None:
        sig = self.sigs.get(e.fn)
        if sig is None:
            return
        for i, req in enumerate(sig.params):
            if req is None or i >= len(e.args):
                continue
            proto, need = req
            arg = e.args[i]
            cur = self._arg_state(arg)
            if cur is None or cur[0] != proto or cur[1] is None:
                continue  # state unknown / different protocol -> gradual, accept
            if cur[1] != need:
                self.diags.append(self._wrong_state(e, arg, proto, cur, need))

    def _arg_state(self, arg: N.Expr) -> Optional[Cell]:
        if isinstance(arg, N.Var):
            return self.state.get(arg.name)
        return self._result_state(arg, arg.line)

    def _name_of(self, arg: N.Expr) -> str:
        return arg.name if isinstance(arg, N.Var) else "the resource"

    def _wrong_state(self, e: N.Call, arg: N.Expr, proto: str,
                     cur: Cell, need: str) -> Diagnostic:
        who = f"`{self._name_of(arg)}`"
        cur_state, since = cur[1], cur[2]
        choices: List[Choice] = []
        # (A) ADVANCE: a single operation that moves cur_state -> need, if one exists.
        advance = [op for (op, to) in self.transitions.get((proto, cur_state), [])
                   if to == need]
        if advance:
            choices.append(Choice(
                label=f"ADVANCE {who} to `{need}` first",
                explanation=(f"`{advance[0]}` takes a {proto} from `{cur_state}` to "
                             f"`{need}`. Re-bind {who} through it before this call."),
                edit=f"let {self._name_of(arg)} = {advance[0]}({self._name_of(arg)});"
                     f"   -- now {who} is `{need}`",
            ))
        else:
            choices.append(Choice(
                label=f"ADVANCE {who} to `{need}` first",
                explanation=(f"Reach state `{need}` by applying the operations that lead "
                             f"there before this call; `{e.fn}` is only valid once {who} "
                             f"is `{need}`."),
                edit=f"-- transition {who} from `{cur_state}` to `{need}` first",
            ))
        # (B) STAY: an operation that is valid in the state the resource is already in.
        here = [op for op in self.valid_in.get((proto, cur_state), []) if op != e.fn]
        if here:
            choices.append(Choice(
                label=f"STAY in `{cur_state}` -- use a valid operation",
                explanation=(f"In state `{cur_state}` you may call: "
                             f"{', '.join(here)}. Pick one of these instead of `{e.fn}`."),
                edit=f"{here[0]}({self._name_of(arg)})   -- valid while {who} is `{cur_state}`",
            ))
        else:
            choices.append(Choice(
                label=f"STAY in `{cur_state}`",
                explanation=(f"No operation is declared for a {proto} in state "
                             f"`{cur_state}`; this resource may be at the end of its "
                             f"protocol. Drop the call or revise the protocol."),
                edit=f"-- nothing consumes a {proto} @ {cur_state}",
            ))
        return Diagnostic(
            code="typestate",
            title=f"`{e.fn}` cannot be called on {who} in state `{cur_state}`",
            line=e.line,
            expected=f"{proto} @ {need}",
            found=f"{proto} @ {cur_state}",
            why=(f"`{e.fn}` requires its {proto} argument to be in state `{need}`, but "
                 f"{who} is in state `{cur_state}` here."),
            counterexample=(f"trace: {who} entered state `{cur_state}` at line {since} -> "
                            f"line {e.line} calls `{e.fn}`, which needs `{need}`"),
            choices=choices,
            source_line=self.src_line(e.line),
            note="typestate is the ownership discipline carried one step further: the "
                 "resource's state rides in its type and every operation declares the "
                 "transition it performs.",
        )


def check_typestate(module: N.Module, source: str) -> List[Diagnostic]:
    return TypestateChecker(module, source).run()


# --- runtime enforcement (#9, dynamic side) -------------------------------- #
class RuntimeOpSig:
    """The per-operation typestate contract the interpreter enforces at runtime:
    which parameters require a `(protocol, state)`, and which one the result produces.
    `has_state` is the cheap gate -- functions with no `@State` are plain functions."""
    __slots__ = ("params", "ret", "has_state")

    def __init__(self, params, ret):
        self.params = params              # list of (proto, state) | None, per param
        self.ret = ret                    # (proto, state) | None
        self.has_state = ret is not None or any(p is not None for p in params)


def operation_signatures(module: N.Module) -> Dict[str, RuntimeOpSig]:
    """Lower each declaration's `@State` annotations to runtime typestate signatures,
    so the interpreter can carry a resource's state and check it where it is consumed.
    Shares the state->protocol resolution with the static pass."""
    state_proto: Dict[str, str] = {}
    for p in getattr(module, "protocols", []):
        for s in p.states:
            state_proto[s] = p.name

    def cell(te: Optional[N.TypeExpr]) -> Optional[Tuple[str, str]]:
        if not isinstance(te, N.TypeExpr) or te.state is None:
            return None
        proto = state_proto.get(te.state)
        return (proto, te.state) if proto is not None else None

    out: Dict[str, RuntimeOpSig] = {}
    for d in module.decls:
        out[d.name] = RuntimeOpSig([cell(p.type) for p in d.params], cell(d.ret))
    return out
