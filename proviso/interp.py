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


class _Thunk:
    """A deferred step. The CPS evaluator returns these instead of recursing, and the
    trampoline (`_drive`) runs them in a loop -- so deep recursion uses heap, not the
    Python call stack."""
    __slots__ = ("fn",)

    def __init__(self, fn):
        self.fn = fn


def _drive(x):
    while type(x) is _Thunk:
        x = x.fn()
    return x


def _ID(v):
    return v


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
        self.checks_performed = 0  # #8: counts runtime contract checks actually run

    def _sem_type(self, te: Optional[N.TypeExpr]) -> T.Type:
        # function types carry no runtime contract; treat as the gradual Fn marker
        if isinstance(te, N.FnTypeExpr):
            return T.FUNC
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
        return _drive(_Thunk(lambda: self.call_user(entry, [], _ID, [])))

    def run_to_value(self, e, env, h):
        """Run a delimited sub-computation to a value (the handler-boundary primitive)."""
        return _drive(self.eval(e, env, _ID, h))

    # --- calls (trampolined CPS: k = continuation, h = handler stack) ----- #
    # #8: `checks` is the set of parameter indices that need a runtime contract check
    # (the gradual ones the checker could not prove). None => check all refined params
    # (the sound fallback when the program was run without the checker). Proven
    # contracts are *erased* -- they cost nothing at runtime.
    def call_user(self, name, args, k, h, checks=None, blame=None):
        env: Dict[str, object] = {}
        for (pname, _pty), val in zip(self.fn_params[name], args):
            env[pname] = val
        for idx, ((pname, pty), val) in enumerate(zip(self.fn_params[name], args)):
            if checks is None or idx in checks:
                self._enforce(pname, pty, val, env, blame, name)
        body = self.fns[name].body
        return _Thunk(lambda: self.eval(body, env, k, h))

    def _enforce(self, pname, pty, val, env, blame=None, fn=None) -> None:
        if isinstance(pty, T.BaseType) and pty.name == "Int" and not pty.refine.unknown:
            self.checks_performed += 1
            if not P.eval_pred(pty.refine.pred, val, env):
                where = f" of `{fn}`" if fn else ""
                why = (f"  [blame: the call at line {blame} was not statically proven, "
                       f"so this contract is checked here]") if blame else ""
                raise ProvisoCastError(
                    f"runtime contract failed: value {val} for `{pname}`{where} violates "
                    f"{{{pty.refine.var} | {P.render(pty.refine.pred, pty.refine.var)}}}{why}"
                )

    def call_builtin(self, name: str, args: List, checks=None, blame=None):
        sig = self.builtins[name]
        benv = {pn: v for (pn, _t, _l), v in zip(sig.params, args)}
        for idx, ((pname, pty, _lin), val) in enumerate(zip(sig.params, args)):
            if checks is None or idx in checks:
                self._enforce(pname, pty, val, benv, blame, name)
        if name == "len":
            return len(args[0])
        if name == "to_str":
            return str(args[0])
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

    # --- trampolined CPS evaluation --------------------------------------- #
    # Every recursive step returns a `_Thunk` instead of calling directly, so the
    # driver loop (`_drive`) keeps the Python stack flat under deep recursion.
    def eval_block(self, b: N.Block, env, k, h):
        return self._eval_stmts(b, 0, dict(env), k, h)

    def _eval_stmts(self, b: N.Block, i: int, local, k, h):
        if i < len(b.stmts):
            st = b.stmts[i]
            if isinstance(st, N.LetStmt):
                return _Thunk(lambda: self.eval(
                    st.value, local,
                    lambda v: _Thunk(lambda: self._eval_stmts(
                        b, i + 1, {**local, st.name: v}, k, h)), h))
            return _Thunk(lambda: self.eval(
                st.expr, local,
                lambda _v: _Thunk(lambda: self._eval_stmts(b, i + 1, local, k, h)), h))
        if b.result is not None:
            return _Thunk(lambda: self.eval(b.result, local, k, h))
        return _Thunk(lambda: k(UNIT))

    def _eval_seq(self, items, i, acc, env, then, h):
        if i < len(items):
            return _Thunk(lambda: self.eval(
                items[i], env,
                lambda v: _Thunk(lambda: self._eval_seq(
                    items, i + 1, acc + [v], env, then, h)), h))
        return _Thunk(lambda: then(acc))

    def eval(self, e: N.Expr, env, k, h):
        if isinstance(e, N.IntLit):
            return _Thunk(lambda: k(e.value))
        if isinstance(e, N.BoolLit):
            return _Thunk(lambda: k(e.value))
        if isinstance(e, N.StrLit):
            return _Thunk(lambda: k(e.value))
        if isinstance(e, N.Var):
            if e.name in env:
                return _Thunk(lambda: k(env[e.name]))
            if e.name in self.fns:  # bare function name used as a value -> a closure
                return _Thunk(lambda: k(
                    Closure(self.fn_params[e.name], self.fns[e.name].body, {})))
            raise ProvisoRuntimeError(f"unbound variable `{e.name}`")
        if isinstance(e, N.UnOp):
            return _Thunk(lambda: self.eval(e.operand, env,
                lambda v: _Thunk(lambda: k((-v) if e.op == "-" else (not v))), h))
        if isinstance(e, N.BinOp):
            return self._eval_binop(e, env, k, h)
        if isinstance(e, N.Call):
            return _Thunk(lambda: self._eval_seq(e.args, 0, [], env,
                lambda vals: _Thunk(lambda: self._apply(e, vals, env, k, h)), h))
        if isinstance(e, N.If):
            return _Thunk(lambda: self.eval(e.cond, env, lambda c: (
                _Thunk(lambda: self.eval_block(e.then, env, k, h)) if c
                else (_Thunk(lambda: self.eval_block(e.els, env, k, h))
                      if e.els is not None else _Thunk(lambda: k(UNIT)))), h))
        if isinstance(e, N.Block):
            return self.eval_block(e, env, k, h)
        if isinstance(e, N.ArrayLit):
            return _Thunk(lambda: self._eval_seq(e.elements, 0, [], env,
                lambda vals: _Thunk(lambda: k(list(vals))), h))
        if isinstance(e, N.Index):
            do_check = getattr(e, "needs_check", True)  # #8: proven access -> erased
            return _Thunk(lambda: self.eval(e.arr, env,
                lambda a: _Thunk(lambda: self.eval(e.idx, env,
                    lambda i: _Thunk(lambda: k(self._index(a, i, e.line, do_check))), h)), h))
        if isinstance(e, N.Match):
            return _Thunk(lambda: self.eval(e.scrutinee, env,
                lambda s: _Thunk(lambda: self._match(e, s, env, k, h)), h))
        if isinstance(e, N.Handle):
            return self._handle_catch(e, env, k, h)
        if isinstance(e, N.Lambda):
            params = [(p.name, self._sem_type(p.type)) for p in e.params]
            return _Thunk(lambda: k(Closure(params, e.body, env)))
        if isinstance(e, N.Perform):
            return _Thunk(lambda: self.eval(e.arg, env,
                lambda av: _Thunk(lambda: self._perform(e.op, av, k, h)), h))
        if isinstance(e, N.HandleWith):
            return self._handle_with(e, env, k, h)
        raise ProvisoRuntimeError(f"cannot evaluate {e!r}")

    def _eval_binop(self, e: N.BinOp, env, k, h):
        if e.op == "&&":
            return _Thunk(lambda: self.eval(e.left, env, lambda l: (
                _Thunk(lambda: k(False)) if not l
                else _Thunk(lambda: self.eval(e.right, env,
                    lambda r: _Thunk(lambda: k(bool(r))), h))), h))
        if e.op == "||":
            return _Thunk(lambda: self.eval(e.left, env, lambda l: (
                _Thunk(lambda: k(True)) if l
                else _Thunk(lambda: self.eval(e.right, env,
                    lambda r: _Thunk(lambda: k(bool(r))), h))), h))
        return _Thunk(lambda: self.eval(e.left, env, lambda a: _Thunk(lambda: self.eval(
            e.right, env, lambda b: _Thunk(lambda: k(self._arith(e.op, a, b, e.line))), h)), h))

    def _arith(self, op, a, b, line):
        if op == "+":
            return a + b      # Int addition or Str concatenation
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op in ("/", "%"):
            if b == 0:
                raise ProvisoRuntimeError(
                    f"division by zero at line {line} -- a refinement like "
                    f"Int{{n | n != 0}} on the divisor would have caught this statically"
                )
            return a // b if op == "/" else a % b
        if op == "<":
            return a < b
        if op == "<=":
            return a <= b
        if op == ">":
            return a > b
        if op == ">=":
            return a >= b
        if op == "==":
            return a == b
        if op == "!=":
            return a != b
        raise ProvisoRuntimeError(f"unknown operator {op}")

    def _index(self, arr, idx, line, do_check=True):
        if not isinstance(arr, list):
            raise ProvisoRuntimeError(f"cannot index a non-array at line {line}")
        if do_check:  # #8: erased when the access was statically proven in range
            self.checks_performed += 1
            if idx < 0 or idx >= len(arr):
                raise ProvisoRuntimeError(
                    f"index {idx} out of bounds at line {line} (array length {len(arr)}) "
                    f"-- a refinement Int{{k | k >= 0 && k < len(a)}} would have caught this"
                )
        return arr[idx]

    def _match(self, e: N.Match, scrut, env, k, h):
        for arm in e.arms:
            arm_env = dict(env)
            if self._match_pat(arm.pattern, scrut, arm_env):
                body = arm.body
                return _Thunk(lambda: self.eval(body, arm_env, k, h))
        raise ProvisoRuntimeError(
            f"no matching arm for {getattr(scrut, 'ctor', scrut)} at line {e.line}")

    def _match_pat(self, pat, value, env) -> bool:
        if isinstance(pat, N.PatWild):
            return True
        if isinstance(pat, N.PatVar):
            env[pat.name] = value
            return True
        if isinstance(pat, N.PatLit):
            return value == pat.value
        if isinstance(pat, N.PatCtor):
            if not (isinstance(value, Data) and value.ctor == pat.name
                    and len(value.fields) == len(pat.args)):
                return False
            for sub, fv in zip(pat.args, value.fields):
                if not self._match_pat(sub, fv, env):
                    return False
            return True
        return False

    def _apply(self, call, vals, env, k, h):
        name = call.fn
        checks = getattr(call, "runtime_checks", None)  # None -> check all (sound fallback)
        blame = call.line
        callee = env.get(name)
        if isinstance(callee, Continuation):
            # resuming runs the captured continuation to a value (its own trampoline)
            # and feeds it into THIS context -- so `k(0) + k(10)` adds two resumed
            # results. Multi-shot: callable repeatedly, each run independent.
            val = _drive(callee.k(vals[0] if vals else UNIT))
            return _Thunk(lambda: k(val))
        if isinstance(callee, Closure):
            env2 = dict(callee.env)
            for (pn, _pty), a in zip(callee.params, vals):
                env2[pn] = a
            for (pn, pty), a in zip(callee.params, vals):  # first-class calls are gradual
                self._enforce(pn, pty, a, env2, blame, name)
            return _Thunk(lambda: self.eval(callee.body, env2, k, h))
        if name in self.ctors:
            return _Thunk(lambda: k(Data(name, vals)))
        if name in self.fns:
            return _Thunk(lambda: self.call_user(name, vals, k, h, checks, blame))
        if name in self.builtins:
            return _Thunk(lambda: k(self.call_builtin(name, vals, checks, blame)))
        raise ProvisoRuntimeError(f"unknown function `{name}`")

    # --- exceptions (Exc) ------------------------------------------------- #
    def _handle_catch(self, e: N.Handle, env, k, h):
        try:
            val = self.run_to_value(e.body, env, h)
        except ProvisoThrow as t:
            val = self.run_to_value(e.handler, {**env, e.binder: t.code}, h)
        return _Thunk(lambda: k(val))

    # --- algebraic effects with multi-shot resumption --------------------- #
    def _handle_with(self, e: N.HandleWith, env, k, h):
        ops = {c.op: (c.param, c.kbinder, c.body) for c in e.clauses}
        retc = e.ret
        frame = HandlerFrame(ops, retc, env)

        def delim(v):
            if retc is not None:
                return self.run_to_value(retc.body, {**env, retc.binder: v}, h)
            return v

        val = _drive(self.eval(e.body, env, delim, h + [frame]))
        return _Thunk(lambda: k(val))

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
