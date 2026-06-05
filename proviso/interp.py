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


UNIT = None


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
        return self.call_user(entry, [])

    # --- calls ------------------------------------------------------------ #
    def call_user(self, name: str, args: List):
        decl = self.fns[name]
        env: Dict[str, object] = {}
        # bind all params first so dependent contracts can reference siblings/len
        for (pname, _pty), val in zip(self.fn_params[name], args):
            env[pname] = val
        for (pname, pty), val in zip(self.fn_params[name], args):
            self._enforce(pname, pty, val, env)
        return self.eval_block(decl.body, env)

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

    # --- evaluation ------------------------------------------------------- #
    def eval_block(self, b: N.Block, env: Dict[str, object]):
        local = dict(env)
        for st in b.stmts:
            if isinstance(st, N.LetStmt):
                local[st.name] = self.eval(st.value, local)
            elif isinstance(st, N.ExprStmt):
                self.eval(st.expr, local)
        if b.result is not None:
            return self.eval(b.result, local)
        return UNIT

    def eval(self, e: N.Expr, env: Dict[str, object]):
        if isinstance(e, N.IntLit):
            return e.value
        if isinstance(e, N.BoolLit):
            return e.value
        if isinstance(e, N.Var):
            if e.name in env:
                return env[e.name]
            raise ProvisoRuntimeError(f"unbound variable `{e.name}`")
        if isinstance(e, N.UnOp):
            v = self.eval(e.operand, env)
            return (-v) if e.op == "-" else (not v)
        if isinstance(e, N.BinOp):
            return self._binop(e, env)
        if isinstance(e, N.Call):
            return self._call(e, env)
        if isinstance(e, N.If):
            if self.eval(e.cond, env):
                return self.eval_block(e.then, env)
            if e.els is not None:
                return self.eval_block(e.els, env)
            return UNIT
        if isinstance(e, N.Handle):
            try:
                return self.eval_block(e.body, env)
            except ProvisoThrow as t:
                henv = dict(env)
                henv[e.binder] = t.code
                return self.eval_block(e.handler, henv)
        if isinstance(e, N.Block):
            return self.eval_block(e, env)
        if isinstance(e, N.ArrayLit):
            return [self.eval(el, env) for el in e.elements]
        if isinstance(e, N.Index):
            arr = self.eval(e.arr, env)
            idx = self.eval(e.idx, env)
            if not isinstance(arr, list):
                raise ProvisoRuntimeError(f"cannot index a non-array at line {e.line}")
            if idx < 0 or idx >= len(arr):
                raise ProvisoRuntimeError(
                    f"index {idx} out of bounds at line {e.line} (array length "
                    f"{len(arr)}) -- a refinement Int{{k | k >= 0 && k < len(a)}} would "
                    f"have caught this statically"
                )
            return arr[idx]
        raise ProvisoRuntimeError(f"cannot evaluate {e!r}")

    def _binop(self, e: N.BinOp, env):
        if e.op == "&&":
            return bool(self.eval(e.left, env)) and bool(self.eval(e.right, env))
        if e.op == "||":
            return bool(self.eval(e.left, env)) or bool(self.eval(e.right, env))
        a = self.eval(e.left, env)
        b = self.eval(e.right, env)
        ops = {
            "+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b,
            "<": lambda: a < b, "<=": lambda: a <= b,
            ">": lambda: a > b, ">=": lambda: a >= b,
            "==": lambda: a == b, "!=": lambda: a != b,
        }
        if e.op in ("/", "%"):
            if b == 0:
                raise ProvisoRuntimeError(
                    f"division by zero at line {e.line} -- a refinement like "
                    f"Int{{n | n != 0}} on the divisor would have caught this statically"
                )
            return a // b if e.op == "/" else a % b
        return ops[e.op]()

    def _call(self, e: N.Call, env):
        args = [self.eval(a, env) for a in e.args]
        if e.fn in self.fns:
            return self.call_user(e.fn, args)
        if e.fn in self.builtins:
            return self.call_builtin(e.fn, args)
        raise ProvisoRuntimeError(f"unknown function `{e.fn}`")


def _show(v) -> str:
    if v is True:
        return "true"
    if v is False:
        return "false"
    if v is None:
        return "()"
    if isinstance(v, list):
        return "[" + ", ".join(_show(x) for x in v) + "]"
    return str(v)


def run_module(module: N.Module, entry: str = "main"):
    return Interpreter(module).run(entry)
