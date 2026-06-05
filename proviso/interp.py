"""Tree-walking interpreter for Proviso.

Refinements are *also* runtime contracts: every refined parameter is checked when a
function is called.  Static proof (a fully-refined, conflict-free obligation) is what
lets you trust the check will never fire -- but the check is always there, which is
exactly why an un-annotated prototype still runs safely (it just pays at runtime).

Effects are realised concretely: IO prints, Exc throws/handles, Net simulates a request.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import nodes as N
from . import predicate as P
from . import types as T
from .checker import builtin_signatures
from .diagnostics import style


class ProvisoThrow(Exception):
    def __init__(self, code: int):
        self.code = code


class ProvisoRuntimeError(Exception):
    pass


class ProvisoCastError(ProvisoRuntimeError):
    """A runtime refinement check failed -- the 'pay later' bill came due."""


import sys as _sys
_sys.setrecursionlimit(1_000_000)  # the CPS evaluator nests Python frames deeply

UNIT = None


class Data:
    """A runtime value of a user-defined enum: a constructor tag plus its fields."""
    __slots__ = ("ctor", "fields")

    def __init__(self, ctor: str, fields: list):
        self.ctor = ctor
        self.fields = fields


class Closure:
    """A first-class function value: parameters, body, and the captured environment."""
    __slots__ = ("params", "body", "env")

    def __init__(self, params, body, env):
        self.params = params      # list of (name, sem_type)
        self.body = body
        self.env = env


class Continuation:
    """A reified, *multi-shot* continuation captured by an effect operation.

    Calling it resumes the suspended computation with a value, up to the handler's
    delimiter -- and it may be called any number of times, each run independent.
    """
    __slots__ = ("k",)

    def __init__(self, k):
        self.k = k


class HandlerFrame:
    """A dynamically-scoped algebraic-effect handler installed by `handle ... with`."""
    __slots__ = ("ops", "ret", "env")

    def __init__(self, ops, ret, env):
        self.ops = ops    # opname -> (param, kbinder, body)
        self.ret = ret    # RetClause or None
        self.env = env


def _sem_type(te: Optional[N.TypeExpr]) -> T.Type:
    if te is None:
        return T.UNIT
    if te.name == "Bool":
        return T.BOOL
    if te.name == "Unit":
        return T.UNIT
    if te.name == "Int":
        if te.refine is None:
            return T.INT()
        return T.INT(te.refine.pred, te.refine.var)
    return T.BaseType(te.name, T.Refinement.gradual())


class Interpreter:
    def __init__(self, module: N.Module):
        self.module = module
        self.aliases = {a.name: a.type for a in getattr(module, "aliases", [])}
        self.fns: Dict[str, N.FnDecl] = {d.name: d for d in module.decls}
        self.fn_params: Dict[str, List] = {
            d.name: [(p.name, self._sem_type(p.type)) for p in d.params]
            for d in module.decls
        }
        self.builtins = builtin_signatures()
        self.ctors = {v.name for e in getattr(module, "enums", []) for v in e.variants}
        self.output: List[str] = []

    def _sem_type(self, te: Optional[N.TypeExpr]) -> T.Type:
        # resolve type aliases before lowering, so runtime contracts on aliased
        # refinement types (e.g. `type Nat = Int{n | n >= 0}`) are still enforced
        seen = set()
        while te is not None and te.name in self.aliases and te.name not in seen:
            seen.add(te.name)
            te = self.aliases[te.name]
        return _sem_type(te)

    # --- entry ------------------------------------------------------------ #
    def run(self, entry: str = "main"):
        if entry not in self.fns:
            raise ProvisoRuntimeError(f"no `{entry}` function to run")
        return self.call_user(entry, [], lambda v: v, [])

    def run_to_value(self, e, env, h):
        """Run a delimited sub-computation to a value (the handler-boundary primitive)."""
        return self.eval(e, env, lambda v: v, h)

    # --- calls (CPS: k = continuation, h = dynamic handler stack) --------- #
    def call_user(self, name, args, k, h):
        env: Dict[str, object] = {}
        for (pname, _pty), val in zip(self.fn_params[name], args):
            env[pname] = val
        for (pname, pty), val in zip(self.fn_params[name], args):
            self._enforce(pname, pty, val, env)
        return self.eval(self.fns[name].body, env, k, h)

    def _enforce(self, pname: str, pty: T.Type, val, env: Dict[str, object]) -> None:
        if isinstance(pty, T.BaseType) and pty.name == "Int" and not pty.refine.unknown:
            if not P.eval_pred(pty.refine.pred, val, env):
                raise ProvisoCastError(
                    f"runtime refinement check failed for `{pname}`: value {val} "
                    f"violates {{{pty.refine.var} | "
                    f"{P.render(pty.refine.pred, pty.refine.var)}}}"
                )

    def call_builtin(self, name: str, args: List):
        sig = self.builtins[name]
        # enforce refined params at the boundary, too
        benv = {pn: v for (pn, _t, _l), v in zip(sig.params, args)}
        for (pname, pty, _lin), val in zip(sig.params, args):
            self._enforce(pname, pty, val, benv)
        if name == "len":
            return len(args[0])
        if name == "print":
            line = str(_show(args[0]))
            self.output.append(line)
            print(line)
            return UNIT
        if name == "throw":
            raise ProvisoThrow(int(args[0]))
        if name == "abs":
            return abs(int(args[0]))
        if name == "http_get":
            retries = int(args[0])
            msg = f"[net] GET ok (budget: up to {retries} retr{'y' if retries==1 else 'ies'})"
            self.output.append(msg)
            print(msg)
            return 200
        if name in ("borrow", "clone"):
            return args[0]
        raise ProvisoRuntimeError(f"unknown builtin `{name}`")

    # --- CPS evaluation --------------------------------------------------- #
    def eval_block(self, b: N.Block, env, k, h):
        return self._eval_stmts(b, 0, dict(env), k, h)

    def _eval_stmts(self, b: N.Block, i: int, local, k, h):
        if i < len(b.stmts):
            st = b.stmts[i]
            if isinstance(st, N.LetStmt):
                return self.eval(
                    st.value, local,
                    lambda v: self._eval_stmts(b, i + 1, {**local, st.name: v}, k, h), h)
            return self.eval(
                st.expr, local, lambda _v: self._eval_stmts(b, i + 1, local, k, h), h)
        if b.result is not None:
            return self.eval(b.result, local, k, h)
        return k(UNIT)

    def _eval_seq(self, items, i, acc, env, then, h):
        if i < len(items):
            return self.eval(items[i], env,
                             lambda v: self._eval_seq(items, i + 1, acc + [v], env, then, h), h)
        return then(acc)

    def eval(self, e: N.Expr, env, k, h):
        if isinstance(e, N.IntLit):
            return k(e.value)
        if isinstance(e, N.BoolLit):
            return k(e.value)
        if isinstance(e, N.Var):
            if e.name in env:
                return k(env[e.name])
            raise ProvisoRuntimeError(f"unbound variable `{e.name}`")
        if isinstance(e, N.UnOp):
            return self.eval(e.operand, env,
                             lambda v: k((-v) if e.op == "-" else (not v)), h)
        if isinstance(e, N.BinOp):
            return self._eval_binop(e, env, k, h)
        if isinstance(e, N.Call):
            return self._eval_seq(e.args, 0, [], env,
                                  lambda vals: self._apply(e.fn, vals, env, k, h), h)
        if isinstance(e, N.If):
            return self.eval(e.cond, env, lambda c: (
                self.eval_block(e.then, env, k, h) if c
                else (self.eval_block(e.els, env, k, h) if e.els is not None else k(UNIT))
            ), h)
        if isinstance(e, N.Block):
            return self.eval_block(e, env, k, h)
        if isinstance(e, N.ArrayLit):
            return self._eval_seq(e.elements, 0, [], env, lambda vals: k(list(vals)), h)
        if isinstance(e, N.Index):
            return self.eval(e.arr, env, lambda a: self.eval(
                e.idx, env, lambda i: k(self._index(a, i, e.line)), h), h)
        if isinstance(e, N.Match):
            return self.eval(e.scrutinee, env, lambda s: self._match(e, s, env, k, h), h)
        if isinstance(e, N.Handle):
            return self._handle_catch(e, env, k, h)
        if isinstance(e, N.Lambda):
            params = [(p.name, self._sem_type(p.type)) for p in e.params]
            return k(Closure(params, e.body, env))
        if isinstance(e, N.Perform):
            return self.eval(e.arg, env, lambda av: self._perform(e.op, av, k, h), h)
        if isinstance(e, N.HandleWith):
            return self._handle_with(e, env, k, h)
        raise ProvisoRuntimeError(f"cannot evaluate {e!r}")

    def _eval_binop(self, e: N.BinOp, env, k, h):
        if e.op == "&&":
            return self.eval(e.left, env, lambda l: (
                k(False) if not l else self.eval(e.right, env, lambda r: k(bool(r)), h)), h)
        if e.op == "||":
            return self.eval(e.left, env, lambda l: (
                k(True) if l else self.eval(e.right, env, lambda r: k(bool(r)), h)), h)
        return self.eval(e.left, env, lambda a: self.eval(
            e.right, env, lambda b: k(self._arith(e.op, a, b, e.line)), h), h)

    def _arith(self, op, a, b, line):
        if op in ("/", "%"):
            if b == 0:
                raise ProvisoRuntimeError(
                    f"division by zero at line {line} -- a refinement like "
                    f"Int{{n | n != 0}} on the divisor would have caught this statically"
                )
            return a // b if op == "/" else a % b
        return {"+": a + b, "-": a - b, "*": a * b,
                "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b,
                "==": a == b, "!=": a != b}[op]

    def _index(self, arr, idx, line):
        if not isinstance(arr, list):
            raise ProvisoRuntimeError(f"cannot index a non-array at line {line}")
        if idx < 0 or idx >= len(arr):
            raise ProvisoRuntimeError(
                f"index {idx} out of bounds at line {line} (array length {len(arr)}) "
                f"-- a refinement Int{{k | k >= 0 && k < len(a)}} would have caught this"
            )
        return arr[idx]

    def _match(self, e: N.Match, scrut, env, k, h):
        for arm in e.arms:
            if arm.ctor is None or (isinstance(scrut, Data) and arm.ctor == scrut.ctor):
                arm_env = dict(env)
                if arm.ctor is not None:
                    for bn, fv in zip(arm.binders, scrut.fields):
                        arm_env[bn] = fv
                return self.eval(arm.body, arm_env, k, h)
        raise ProvisoRuntimeError(
            f"no matching arm for {getattr(scrut, 'ctor', scrut)} at line {e.line}")

    def _apply(self, name, vals, env, k, h):
        callee = env.get(name)
        if isinstance(callee, Continuation):
            # resuming yields a value (the delimited result) back into THIS context,
            # so the clause can use it -- e.g. `k(0) + k(10)`. Multi-shot: callable
            # repeatedly, each run independent.
            return k(callee.k(vals[0] if vals else UNIT))
        if isinstance(callee, Closure):
            env2 = dict(callee.env)
            for (pn, _pty), a in zip(callee.params, vals):
                env2[pn] = a
            for (pn, pty), a in zip(callee.params, vals):
                self._enforce(pn, pty, a, env2)
            return self.eval(callee.body, env2, k, h)
        if name in self.ctors:
            return k(Data(name, vals))
        if name in self.fns:
            return self.call_user(name, vals, k, h)
        if name in self.builtins:
            return k(self.call_builtin(name, vals))
        raise ProvisoRuntimeError(f"unknown function `{name}`")

    # --- exceptions (Exc) ------------------------------------------------- #
    def _handle_catch(self, e: N.Handle, env, k, h):
        try:
            val = self.run_to_value(e.body, env, h)
        except ProvisoThrow as t:
            val = self.run_to_value(e.handler, {**env, e.binder: t.code}, h)
        return k(val)

    # --- algebraic effects with multi-shot resumption --------------------- #
    def _handle_with(self, e: N.HandleWith, env, k, h):
        ops = {c.op: (c.param, c.kbinder, c.body) for c in e.clauses}
        retc = e.ret
        frame = HandlerFrame(ops, retc, env)

        def delim(v):
            if retc is not None:
                return self.run_to_value(retc.body, {**env, retc.binder: v}, h)
            return v

        val = self.eval(e.body, env, delim, h + [frame])
        return k(val)

    def _perform(self, op, av, k, h):
        for i in range(len(h) - 1, -1, -1):
            if op in h[i].ops:
                frame = h[i]
                below = h[:i]
                param, kbinder, cbody = frame.ops[op]
                # `k` is the continuation from this perform up to the handler's
                # delimiter; wrapping it as a Continuation makes it a multi-shot,
                # first-class resumption the clause can invoke any number of times.
                cenv = {**frame.env, param: av, kbinder: Continuation(k)}
                return self.run_to_value(cbody, cenv, below)
        raise ProvisoRuntimeError(f"unhandled effect operation `{op}`")


def _show(v) -> str:
    if v is True:
        return "true"
    if v is False:
        return "false"
    if v is None:
        return "()"
    if isinstance(v, list):
        return "[" + ", ".join(_show(x) for x in v) + "]"
    if isinstance(v, Data):
        if not v.fields:
            return v.ctor
        return v.ctor + "(" + ", ".join(_show(f) for f in v.fields) + ")"
    if isinstance(v, Closure):
        return "<fn>"
    if isinstance(v, Continuation):
        return "<continuation>"
    return str(v)


def run_module(module: N.Module, entry: str = "main"):
    return Interpreter(module).run(entry)
