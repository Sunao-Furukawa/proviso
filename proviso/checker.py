"""The gradual dependent type + effect checker.

This is where the three axes meet:

  1. Gradual dependent types: a plain `Int` carries an UNKNOWN refinement (`?`) and is
     *consistent* with any requirement -- it just defers to a runtime check (a warning,
     not an error).  Add `Int{n | n > 0}` and the same call site is now statically
     proven or statically rejected.  You pay for correctness when you choose to.

  2. Effects in the signature: every expression's inferred effect row must be a subset
     of what the function declares.  A `Net{r | r <= 3}` effect's "max 3 retries"
     property is enforced by the *same* refinement engine, on the operation's argument --
     that is the promised integration of effects with dependent types.

  3. (Ownership lives in ownership.py -- it is the third effect-like discipline.)

Every rejection is emitted as a `Diagnostic` carrying a counterexample and two framed
choices, never a bare "type error".
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from . import nodes as N
from . import predicate as P
from . import types as T
from .diagnostics import Diagnostic, Choice, Warning
from .types import BaseType, FnType, Effect, Refinement


Env = Dict[str, T.Type]


def builtin_signatures() -> Dict[str, FnType]:
    """The standard prelude -- each builtin shows off one part of the design."""
    return {
        # IO
        "print": FnType([("x", T.INT(), False)], T.UNIT, [Effect("IO")]),
        # Exceptions
        "throw": FnType([("code", T.INT(), False)], T.UNIT, [Effect("Exc")]),
        # Pure refinement inference: abs is *proven* to return a non-negative Int.
        "abs": FnType([("x", T.INT(), False)],
                      T.INT(P.PCmp(">=", 0)), []),
        # A network operation whose safety property -- "at most 3 retries" -- is a
        # dependent refinement on its argument, while `Net` rides in the effect row.
        "http_get": FnType(
            [("retries", T.INT(P.PAnd(P.PCmp(">=", 0), P.PCmp("<=", 3)), "r"), False)],
            T.INT(P.PCmp(">=", 0)),
            [Effect("Net", Refinement("r", P.PAnd(P.PCmp(">=", 0), P.PCmp("<=", 3))))],
        ),
        # Ownership primitives: read a linear value without consuming it. The
        # ownership pass treats these specially; to the type checker they are just
        # pure identity functions over a value.
        "borrow": FnType([("x", T.INT(), False)], T.INT(), []),
        "clone": FnType([("x", T.INT(), False)], T.INT(), []),
        # Array length -- a *measure*, proven non-negative. Refinements may mention
        # `len(a)` (see dependent refinements), which is how bounds checks are stated.
        "len": FnType([("a", T.ARRAY, False)], T.INT(P.PCmp(">=", 0)), []),
        # Render an Int as a string (for building messages).
        "to_str": FnType([("n", T.INT(), False)], T.STR, []),
    }


class Checker:
    def __init__(self, module: N.Module, source: str):
        self.module = module
        self.src_lines = source.splitlines()
        self.diags: List[Diagnostic] = []
        self.warnings: List[Warning] = []
        self.fns: Dict[str, FnType] = {}
        self.fn_decls: Dict[str, N.FnDecl] = {}
        self.builtins = builtin_signatures()
        self.aliases: Dict[str, N.TypeExpr] = {
            a.name: a.type for a in getattr(module, "aliases", [])
        }
        # user-defined sum types (#6): enum registry + constructor signatures
        self.enums: Dict[str, N.EnumDecl] = {e.name: e for e in getattr(module, "enums", [])}
        self.enum_ctors: Dict[str, List[str]] = {}
        self.ctors: Dict[str, FnType] = {}
        self.ctor_enum: Dict[str, str] = {}
        self.assumptions: list = []  # path facts true in the current branch (#5)
        for e in self.enums.values():
            self.enum_ctors[e.name] = [v.name for v in e.variants]
            for v in e.variants:
                fields = [(f"f{i}", self._sem_type(ft), False)
                          for i, ft in enumerate(v.fields)]
                self.ctors[v.name] = FnType(fields, T.DataType(e.name), [])
                self.ctor_enum[v.name] = e.name

    # --- public ----------------------------------------------------------- #
    def run(self) -> Tuple[List[Diagnostic], List[Warning]]:
        for d in self.module.decls:
            self.fns[d.name] = self._fn_type(d)
            self.fn_decls[d.name] = d
        # Effect inference: functions WITHOUT a declared `!` row export the effects
        # inferred from their body (a fixpoint, so mutual recursion is handled). This
        # is the gradual story applied to effects -- omit the row and it is computed.
        self._infer_effect_rows()
        for d in self.module.decls:
            self._check_fn(d)
        return self.diags, self.warnings

    def _infer_effect_rows(self) -> None:
        est: Dict[str, set] = {}
        for d in self.module.decls:
            if d.effects_declared:
                est[d.name] = {e.name for e in self.fns[d.name].effects}
            else:
                est[d.name] = set()
        changed = True
        while changed:
            changed = False
            for d in self.module.decls:
                if d.effects_declared:
                    continue
                s = self._collect_effects(d.body, est)
                if s != est[d.name]:
                    est[d.name] = s
                    changed = True
        for d in self.module.decls:
            if not d.effects_declared:
                self.fns[d.name].effects = [Effect(n) for n in sorted(est[d.name])]

    def _callee_effects(self, name: str, est: Dict[str, set]) -> set:
        if name in est:
            return est[name]
        b = self.builtins.get(name)
        return {e.name for e in b.effects} if b else set()

    def _collect_effects(self, e, est: Dict[str, set]) -> set:
        if isinstance(e, N.Block):
            s = set()
            for st in e.stmts:
                if isinstance(st, N.LetStmt):
                    s |= self._collect_effects(st.value, est)
                elif isinstance(st, N.ExprStmt):
                    s |= self._collect_effects(st.expr, est)
            if e.result is not None:
                s |= self._collect_effects(e.result, est)
            return s
        if isinstance(e, N.Call):
            s = set()
            for a in e.args:
                s |= self._collect_effects(a, est)
            return s | self._callee_effects(e.fn, est)
        if isinstance(e, N.BinOp):
            return self._collect_effects(e.left, est) | self._collect_effects(e.right, est)
        if isinstance(e, N.UnOp):
            return self._collect_effects(e.operand, est)
        if isinstance(e, N.If):
            s = self._collect_effects(e.cond, est) | self._collect_effects(e.then, est)
            if e.els is not None:
                s |= self._collect_effects(e.els, est)
            return s
        if isinstance(e, N.Handle):
            body = self._collect_effects(e.body, est) - {"Exc"}  # handle discharges Exc
            return body | self._collect_effects(e.handler, est)
        if isinstance(e, N.ArrayLit):
            s = set()
            for el in e.elements:
                s |= self._collect_effects(el, est)
            return s
        if isinstance(e, N.Index):
            return self._collect_effects(e.arr, est) | self._collect_effects(e.idx, est)
        if isinstance(e, N.Match):
            s = self._collect_effects(e.scrutinee, est)
            for arm in e.arms:
                s |= self._collect_effects(arm.body, est)
            return s
        if isinstance(e, N.Perform):
            return self._collect_effects(e.arg, est) | {e.op}
        if isinstance(e, N.HandleWith):
            handled = {c.op for c in e.clauses}
            s = self._collect_effects(e.body, est) - handled
            for c in e.clauses:
                s |= self._collect_effects(c.body, est)
            if e.ret is not None:
                s |= self._collect_effects(e.ret.body, est)
            return s
        # a lambda's body effects are latent (they occur when it is called)
        return set()

    def lookup(self, name: str) -> Optional[FnType]:
        return self.fns.get(name) or self.builtins.get(name) or self.ctors.get(name)

    def src_line(self, line: int) -> Optional[str]:
        if 1 <= line <= len(self.src_lines):
            return self.src_lines[line - 1]
        return None

    # --- type-expression -> semantic type --------------------------------- #
    def _resolve_alias(self, te: N.TypeExpr) -> N.TypeExpr:
        seen = set()
        while te.name in self.aliases and te.name not in seen:
            seen.add(te.name)
            te = self.aliases[te.name]
        return te

    def _sem_type(self, te: Optional[N.TypeExpr]) -> T.Type:
        if te is None:
            return T.UNIT
        if isinstance(te, N.FnTypeExpr):
            return T.ArrowType(
                [self._sem_type(p) for p in te.params],
                self._sem_type(te.ret),
                [Effect(ef.name) for ef in te.effects],
            )
        te = self._resolve_alias(te)
        if te.name == "Bool":
            return T.BOOL
        if te.name == "Unit":
            return T.UNIT
        if te.name == "Int":
            if te.refine is None:
                return T.INT()  # gradual
            return T.INT(te.refine.pred, te.refine.var)
        if te.name == "Array":
            return T.ARRAY
        if te.name == "Fn":
            return T.FUNC
        if te.name == "Str":
            return T.STR
        if te.name in self.enums:
            return T.DataType(te.name)
        # Unknown base type name; treat as opaque gradual Int-ish for v1.
        return BaseType(te.name, Refinement.gradual())

    def _fn_type(self, d: N.FnDecl) -> FnType:
        params = [(p.name, self._sem_type(p.type), p.linear) for p in d.params]
        ret = self._sem_type(d.ret)
        effects = [Effect(e.name,
                          Refinement(e.refine.var, e.refine.pred) if e.refine else None)
                   for e in d.effects]
        return FnType(params, ret, effects)

    # --- function check --------------------------------------------------- #
    def _check_fn(self, d: N.FnDecl) -> None:
        sig = self.fns[d.name]
        env: Env = {name: ty for (name, ty, _) in sig.params}
        self.assumptions = []  # path facts known in the current branch (len-guards etc.)
        body_ty, body_effects = self._infer(d.body, env)

        # return-type obligation
        self._check_assign(body_ty, sig.ret, d.body.line,
                           context=f"return value of `{d.name}`")

        # effect obligation: inferred subset-of declared.
        # Only enforced when the `!` row was actually written; an omitted row is
        # inferred (see _infer_effect_rows), so by construction it cannot leak.
        if d.effects_declared:
            declared = {e.name for e in sig.effects}
            for eff_name, line in body_effects.items():
                if eff_name not in declared:
                    self._effect_leak(d, eff_name, line)

    # --- inference -------------------------------------------------------- #
    def _infer(self, e: N.Expr, env: Env) -> Tuple[T.Type, Dict[str, int]]:
        if isinstance(e, N.IntLit):
            return T.INT(P.PCmp("==", e.value)), {}
        if isinstance(e, N.BoolLit):
            return T.BOOL, {}
        if isinstance(e, N.StrLit):
            return T.STR, {}
        if isinstance(e, N.Var):
            ty = env.get(e.name)
            if ty is None:
                # a bare top-level function name used as a value -> a function reference
                fn = self.fns.get(e.name)
                if fn is not None:
                    return T.ArrowType([t for _n, t, _l in fn.params], fn.ret,
                                       list(fn.effects)), {}
                self.diags.append(Diagnostic(
                    code="unbound", title=f"unknown name `{e.name}`",
                    line=e.line, expected="a value in scope", found="nothing",
                    why=f"`{e.name}` is not a parameter or let-binding here.",
                    source_line=self.src_line(e.line),
                ))
                return T.INT(), {}
            return ty, {}
        if isinstance(e, N.UnOp):
            ty, eff = self._infer(e.operand, env)
            if e.op == "!":
                return T.BOOL, eff
            if isinstance(e.operand, N.IntLit):  # `-k` is the singleton {value == -k}
                return T.INT(P.PCmp("==", -e.operand.value)), eff
            return T.INT(), eff  # other negation: drop to gradual (sound)
        if isinstance(e, N.BinOp):
            return self._infer_binop(e, env)
        if isinstance(e, N.Call):
            return self._infer_call(e, env)
        if isinstance(e, N.If):
            return self._infer_if(e, env)
        if isinstance(e, N.Handle):
            return self._infer_handle(e, env)
        if isinstance(e, N.Block):
            return self._infer_block(e, env)
        if isinstance(e, N.ArrayLit):
            eff: Dict[str, int] = {}
            for el in e.elements:
                _, ee = self._infer(el, env)
                eff.update(ee)
            # track the literal's length statically: len(self) == N (#4)
            n = len(e.elements)
            return BaseType("Array", Refinement("n", P.PCmp("==", n))), eff
        if isinstance(e, N.Index):
            return self._infer_index(e, env)
        if isinstance(e, N.Match):
            return self._infer_match(e, env)
        if isinstance(e, N.Lambda):
            # a function value: infer its precise arrow type (params -> ret ! effects).
            # The effects are latent here -- they ride in the type, performed on call.
            penv = dict(env)
            params = []
            for p in e.params:
                pt = self._sem_type(p.type)
                penv[p.name] = pt
                params.append(pt)
            bt, beff = self._infer(e.body, penv)
            ret = self._sem_type(e.ret) if e.ret is not None else bt
            if e.ret is not None:
                self._check_assign(bt, ret, e.line, "lambda result")
            return T.ArrowType(params, ret, [Effect(n) for n in beff]), {}
        if isinstance(e, N.Perform):
            _at, ae = self._infer(e.arg, env)
            # the value a `perform` yields is whatever the handler resumes with: gradual
            return T.INT(), {**ae, e.op: e.line}
        if isinstance(e, N.HandleWith):
            return self._infer_handle_with(e, env)
        raise TypeError(f"cannot infer {e!r}")

    def _infer_handle_with(self, e: N.HandleWith, env: Env) -> Tuple[T.Type, Dict[str, int]]:
        bt, be = self._infer(e.body, env)
        handled = {c.op for c in e.clauses}
        eff = {k: v for k, v in be.items() if k not in handled}  # discharge handled ops
        results = []
        for c in e.clauses:
            cenv = dict(env)
            cenv[c.param] = T.INT()    # operation argument (gradual)
            cenv[c.kbinder] = T.FUNC   # the multi-shot resumption, a first-class function
            ct, ce = self._infer(c.body, cenv)
            eff.update(ce)
            results.append(ct)
        if e.ret is not None:
            renv = dict(env)
            renv[e.ret.binder] = bt
            rt, re = self._infer(e.ret.body, renv)
            eff.update(re)
            results.append(rt)
        else:
            results.append(bt)
        result = results[0]
        for r in results[1:]:
            result = self._join(result, r)
        return result, eff

    def _infer_match(self, e: N.Match, env: Env) -> Tuple[T.Type, Dict[str, int]]:
        st, eff = self._infer(e.scrutinee, env)
        enum_name = st.name if isinstance(st, T.DataType) else None
        if enum_name is None or enum_name not in self.enums:
            self.diags.append(Diagnostic(
                code="type", title="match on a non-enum value",
                line=e.line, expected="a value of an `enum` type", found=str(st),
                why="`match` deconstructs user-defined sum types only.",
                source_line=self.src_line(e.line),
            ))
            return T.UNIT, eff
        covered: set = set()
        has_wild = False
        result_ty: Optional[T.Type] = None
        for arm in e.arms:
            arm_env = dict(env)
            self._check_pattern(arm.pattern, st, arm_env)
            p = arm.pattern
            if isinstance(p, (N.PatWild, N.PatVar)):
                has_wild = True
            elif isinstance(p, N.PatCtor):
                covered.add(p.name)
            bt, be = self._infer(arm.body, arm_env)
            eff.update(be)
            result_ty = bt if result_ty is None else self._join(result_ty, bt)
        if not has_wild:
            missing = [c for c in self.enum_ctors.get(enum_name, []) if c not in covered]
            if missing:
                self._non_exhaustive(e, enum_name, missing)
        return (result_ty or T.UNIT), eff

    def _check_pattern(self, pat: N.Pattern, ty: T.Type, env: Env) -> None:
        """Type-check a (possibly nested) pattern against `ty`, binding variables."""
        if isinstance(pat, N.PatWild):
            return
        if isinstance(pat, N.PatVar):
            env[pat.name] = ty
            return
        if isinstance(pat, N.PatLit):
            want = {"int": "Int", "bool": "Bool", "str": "Str"}[pat.kind]
            if not (isinstance(ty, BaseType) and ty.name == want):
                self.diags.append(Diagnostic(
                    code="type", title=f"{pat.kind} literal pattern against {ty}",
                    line=pat.line, expected=str(ty), found=f"a {pat.kind} literal",
                    why="a literal pattern must match the scrutinee's base type.",
                    source_line=self.src_line(pat.line),
                ))
            return
        if isinstance(pat, N.PatCtor):
            sig = self.ctors.get(pat.name)
            enum_name = ty.name if isinstance(ty, T.DataType) else None
            if sig is None or self.ctor_enum.get(pat.name) != enum_name:
                self.diags.append(Diagnostic(
                    code="type", title=f"`{pat.name}` is not a constructor of {ty}",
                    line=pat.line, expected=f"a constructor of {ty}", found=f"`{pat.name}`",
                    why="constructor patterns must match the scrutinee's enum.",
                    source_line=self.src_line(pat.line),
                ))
                return
            if len(pat.args) != len(sig.params):
                self.diags.append(Diagnostic(
                    code="arity", title=f"`{pat.name}` takes {len(sig.params)} field(s)",
                    line=pat.line, expected=f"{len(sig.params)} sub-pattern(s)",
                    found=f"{len(pat.args)}",
                    why="a constructor pattern must match its arity.",
                    source_line=self.src_line(pat.line),
                ))
                return
            for sub, (_n, fty, _l) in zip(pat.args, sig.params):
                self._check_pattern(sub, fty, env)

    def _non_exhaustive(self, e: N.Match, enum_name: str, missing: list) -> None:
        ms = ", ".join(missing)
        self.diags.append(Diagnostic(
            code="non-exhaustive",
            title=f"match on {enum_name} does not cover every case",
            line=e.line,
            expected=f"arms for all variants of {enum_name}",
            found=f"missing: {ms}",
            why=(f"{enum_name} has variants not handled here; at runtime such a value "
                 f"would have no matching arm."),
            counterexample=f"a value built with {missing[0]}(...) matches no arm",
            choices=[
                Choice(
                    label="ADD the missing arms (handle each case)",
                    explanation="Cover every variant so the match is total and the "
                                "compiler can trust it.",
                    edit=f"add arms: {'; '.join(m + '(...) => ...' for m in missing)}",
                ),
                Choice(
                    label="ADD a wildcard `_` arm (catch-all)",
                    explanation="Handle the rest uniformly if they share behaviour.",
                    edit="_ => ...",
                ),
            ],
            source_line=self.src_line(e.line),
        ))

    def _infer_index(self, e: N.Index, env: Env) -> Tuple[T.Type, Dict[str, int]]:
        at, ae = self._infer(e.arr, env)
        it, ie = self._infer(e.idx, env)
        eff = {**ae, **ie}
        if not (isinstance(at, BaseType) and at.name == "Array"):
            self.diags.append(Diagnostic(
                code="type", title="indexing a non-array",
                line=e.line, expected="Array", found=str(at),
                why="the `[ ]` index operator applies to arrays only.",
                source_line=self.src_line(e.line),
            ))
            return T.INT(), eff
        # The bounds obligation 0 <= idx < len(arr) is a *dependent* refinement.
        # #8: record whether the access needs a runtime bounds check (vs. proven/erased).
        e.needs_check = self._check_index_bounds(e, it, env)
        return T.INT(), eff

    def _infer_binop(self, e: N.BinOp, env: Env) -> Tuple[T.Type, Dict[str, int]]:
        lt, le = self._infer(e.left, env)
        rt, re = self._infer(e.right, env)
        eff = {**le, **re}
        if e.op in ("&&", "||"):
            return T.BOOL, eff
        if e.op in ("<", "<=", ">", ">=", "==", "!="):
            return T.BOOL, eff
        # string concatenation
        if e.op == "+" and isinstance(lt, BaseType) and lt.name == "Str" \
                and isinstance(rt, BaseType) and rt.name == "Str":
            return T.STR, eff
        # arithmetic -- propagate intervals so refinements survive computation
        if e.op in ("+", "-", "*"):
            ai = self._interval(lt)
            bi = self._interval(rt)
            if ai is not None and bi is not None:
                if e.op == "+":
                    res = P.interval_add(ai, bi)
                elif e.op == "-":
                    res = P.interval_sub(ai, bi)
                else:
                    res = P.interval_mul(ai, bi)
                pred = P.pred_of_interval(res)
                if isinstance(pred, P.PTrue):
                    return T.INT(), eff
                return T.INT(pred), eff
        return T.INT(), eff  # / and % stay gradual

    def _interval(self, ty: T.Type) -> Optional[P.Interval]:
        if isinstance(ty, BaseType) and ty.name == "Int":
            if ty.refine.unknown:
                return P.Interval(P.NEG_INF, P.POS_INF)
            return P.interval_of(ty.refine.pred)
        return None

    def _infer_call(self, e: N.Call, env: Env) -> Tuple[T.Type, Dict[str, int]]:
        # calling a first-class function value (a `Fn`-typed binding, incl. a captured
        # resumption `k`). We don't know its result/effects statically -> gradual, and
        # its effects are *polymorphic*: they ride with the value, not this call site.
        callee_ty = env.get(e.fn)
        if isinstance(callee_ty, T.ArrowType):
            eff: Dict[str, int] = {}
            for a in e.args:
                _at, ae = self._infer(a, env)
                eff.update(ae)
            # calling a function value performs its whole effect row, including any
            # effect variable (that variable is what makes the caller polymorphic)
            for ef in callee_ty.effects:
                eff[ef.name] = e.line
            return callee_ty.ret, eff
        if callee_ty is not None:
            # `e.fn` names an in-scope binding whose type is not a precise arrow: a
            # gradual `Fn` value, or -- crucially for algebraic effects -- a captured
            # resumption `k` that has *escaped* its handler (stored in a let, returned,
            # or pulled out of a match) and whose type a join has made gradual. We
            # cannot see its arrow statically, so the call is gradual: thread the
            # argument effects and defer the rest to the runtime. This is what lets the
            # nested / cross-handler continuation machinery (fully supported by the
            # interpreter) also type-check, rather than tripping "unknown function".
            eff = {}
            for a in e.args:
                _at, ae = self._infer(a, env)
                eff.update(ae)
            return T.INT(), eff
        sig = self.lookup(e.fn)
        eff = {}
        if sig is None:
            self.diags.append(Diagnostic(
                code="unbound", title=f"unknown function `{e.fn}`",
                line=e.line, expected="a declared or builtin function",
                found=f"`{e.fn}`", why="no such function is in scope.",
                source_line=self.src_line(e.line),
            ))
            return T.INT(), eff

        # arity
        if len(e.args) != len(sig.params):
            self.diags.append(Diagnostic(
                code="arity", title=f"`{e.fn}` expects {len(sig.params)} argument(s)",
                line=e.line, expected=f"{len(sig.params)} argument(s)",
                found=f"{len(e.args)} given",
                why="argument count does not match the signature.",
                source_line=self.src_line(e.line),
            ))

        # formal-parameter -> actual-argument *term* map, used to instantiate
        # dependent refinements like `i: Int{k | k < len(xs)}` or `hi: Int{h | h >= lo}`.
        term_map = {}
        for a, (pn, _t, _l) in zip(e.args, sig.params):
            t = self._arg_term(a)
            if t is not None:
                term_map[pn] = t
        arg_types = []
        needs_check = set()  # arg indices that need a runtime contract check (gradual)
        for i, (arg, (pname, pty, _lin)) in enumerate(zip(e.args, sig.params)):
            at, ae = self._infer(arg, env)
            arg_types.append(at)
            eff.update(ae)
            # `print` is intentionally polymorphic over base types in v1.
            if e.fn == "print":
                continue
            if self._check_arg(at, pty, arg, e.fn, pname, env, term_map):
                needs_check.add(i)
        e.runtime_checks = needs_check  # #8: only these args are checked at runtime

        # effect-variable polymorphism: bind the variables in arrow-typed parameters
        # from the concrete effects of the actual function arguments.
        subst: Dict[str, set] = {}
        for (pname, pty, _l), at in zip(sig.params, arg_types):
            if isinstance(pty, T.ArrowType) and isinstance(at, T.ArrowType):
                argnames = {ef.name for ef in at.effects}
                for ef in pty.effects:
                    if T.is_effect_var(ef.name):
                        subst.setdefault(ef.name, set()).update(argnames)
        for ef in sig.effects:
            if T.is_effect_var(ef.name):
                for nm in subst.get(ef.name, set()):
                    eff[nm] = e.line
            else:
                eff[ef.name] = e.line
        return sig.ret, eff

    def _infer_if(self, e: N.If, env: Env) -> Tuple[T.Type, Dict[str, int]]:
        ct, ce = self._infer(e.cond, env)
        saved = self.assumptions
        # then-branch: refine variables (occurrence typing) and add guard facts
        # (e.g. `len(xs) > 0`) so dependent obligations inside can be discharged.
        then_env = self._refine_env(e.cond, env, negate=False)
        self.assumptions = saved + self._guard_facts(e.cond, negate=False)
        tt, te = self._infer(e.then, then_env)
        self.assumptions = saved
        eff = {**ce, **te}
        if e.els is None:
            return T.UNIT, eff
        else_env = self._refine_env(e.cond, env, negate=True)
        self.assumptions = saved + self._guard_facts(e.cond, negate=True)
        et, ee = self._infer(e.els, else_env)
        self.assumptions = saved
        eff.update(ee)
        return self._join(tt, et), eff

    _NEG_CMP = {"<": ">=", "<=": ">", ">": "<=", ">=": "<", "==": "!=", "!=": "=="}

    def _expr_to_term(self, e: N.Expr):
        """Best-effort lowering of an expression to a refinement term (or None)."""
        if isinstance(e, N.IntLit):
            return P.TInt(e.value)
        if isinstance(e, N.UnOp) and e.op == "-" and isinstance(e.operand, N.IntLit):
            return P.TInt(-e.operand.value)
        if isinstance(e, N.Var):
            return P.TVar(e.name)
        if (isinstance(e, N.Call) and e.fn == "len" and len(e.args) == 1
                and isinstance(e.args[0], N.Var)):
            return P.TLen(e.args[0].name)
        if (isinstance(e, N.Call) and e.fn in P.MEASURE_ARITY
                and len(e.args) == P.MEASURE_ARITY[e.fn]):
            ts = [self._expr_to_term(a) for a in e.args]
            if all(t is not None for t in ts):
                return P.TMeasure(e.fn, tuple(ts))
        if isinstance(e, N.BinOp) and e.op in ("+", "-", "*"):
            lt, rt = self._expr_to_term(e.left), self._expr_to_term(e.right)
            if lt is not None and rt is not None:
                return P.TArith(e.op, lt, rt)
        return None

    def _guard_facts(self, cond: N.Expr, negate: bool) -> list:
        """Closed predicates a guard makes true in its branch (e.g. `len(xs) > 0`)."""
        if isinstance(cond, N.BinOp) and cond.op == "&&" and not negate:
            return self._guard_facts(cond.left, False) + self._guard_facts(cond.right, False)
        if isinstance(cond, N.BinOp) and cond.op == "||" and negate:
            return self._guard_facts(cond.left, True) + self._guard_facts(cond.right, True)
        if isinstance(cond, N.BinOp) and cond.op in ("<", "<=", ">", ">=", "==", "!="):
            lt, rt = self._expr_to_term(cond.left), self._expr_to_term(cond.right)
            if lt is not None and rt is not None:
                op = self._NEG_CMP[cond.op] if negate else cond.op
                return [P.PRel(op, lt, rt)]
        return []

    def _infer_handle(self, e: N.Handle, env: Env) -> Tuple[T.Type, Dict[str, int]]:
        bt, be = self._infer(e.body, env)
        # the binder receives the thrown code (an Int)
        henv = dict(env)
        henv[e.binder] = T.INT()
        ht, he = self._infer(e.handler, henv)
        # `handle ... catch` discharges the Exc effect of the body
        be = {k: v for k, v in be.items() if k != "Exc"}
        eff = {**be, **he}
        return self._join(bt, ht), eff

    def _infer_block(self, e: N.Block, env: Env) -> Tuple[T.Type, Dict[str, int]]:
        local = dict(env)
        eff: Dict[str, int] = {}
        for st in e.stmts:
            if isinstance(st, N.LetStmt):
                vt, ve = self._infer(st.value, local)
                eff.update(ve)
                if st.type is not None:
                    declared = self._sem_type(st.type)
                    self._check_assign(vt, declared, st.line,
                                       context=f"binding `{st.name}`")
                    local[st.name] = declared
                else:
                    local[st.name] = vt
            elif isinstance(st, N.ExprStmt):
                _, se = self._infer(st.expr, local)
                eff.update(se)
        if e.result is not None:
            rt, re = self._infer(e.result, local)
            eff.update(re)
            return rt, eff
        return T.UNIT, eff

    # --- subtyping / consistency, the gradual core ------------------------ #
    def _check_assign(self, src: T.Type, dst: T.Type, line: int, context: str) -> None:
        if isinstance(dst, T.DataType):
            if not (isinstance(src, T.DataType) and src.name == dst.name):
                self.diags.append(Diagnostic(
                    code="type", title=f"{context}: expected {dst.name}, found {src}",
                    line=line, expected=str(dst), found=str(src),
                    why="enum types differ.", source_line=self.src_line(line),
                ))
            return
        # base-name mismatch is a hard, non-gradual error
        if isinstance(src, BaseType) and isinstance(dst, BaseType):
            if src.name != dst.name:
                self.diags.append(Diagnostic(
                    code="type", title=f"{context}: expected {dst.name}, found {src.name}",
                    line=line, expected=str(dst), found=str(src),
                    why="base types differ; no refinement can reconcile them.",
                    source_line=self.src_line(line),
                ))
                return
            self._check_refine(src, dst, line, context, param_name=None, arg=None)

    def _check_arg(self, arg_ty: T.Type, param_ty: T.Type, arg: N.Expr,
                   fn: str, pname: str, env: Env, term_map: dict) -> bool:
        """Returns True if this argument needs a runtime contract check (gradual)."""
        if isinstance(param_ty, T.ArrowType):
            if isinstance(arg_ty, T.ArrowType):
                # #2 precise function-arg subtyping: arg_ty <: param_ty, with
                # contravariant parameters and a covariant result.
                self._check_arrow_sub(arg_ty, param_ty, arg, fn, pname)
                return False
            if isinstance(arg_ty, BaseType) and arg_ty.name == "Fn":
                return False  # a gradual `Fn` value -> accepted (cannot wrap at runtime)
            self.diags.append(Diagnostic(
                code="type",
                title=f"argument `{pname}` of `{fn}`: expected a function, found {arg_ty}",
                line=arg.line, expected=str(param_ty), found=str(arg_ty),
                why="a function-typed parameter needs a function value.",
                source_line=self.src_line(arg.line),
            ))
            return False
        if isinstance(param_ty, T.DataType):
            if not (isinstance(arg_ty, T.DataType) and arg_ty.name == param_ty.name):
                self.diags.append(Diagnostic(
                    code="type",
                    title=f"argument `{pname}` of `{fn}`: expected {param_ty.name}, "
                          f"found {arg_ty}",
                    line=arg.line, expected=str(param_ty), found=str(arg_ty),
                    why="enum types differ.", source_line=self.src_line(arg.line),
                ))
            return False
        if isinstance(arg_ty, BaseType) and isinstance(param_ty, BaseType):
            if arg_ty.name != param_ty.name:
                self.diags.append(Diagnostic(
                    code="type",
                    title=f"argument `{pname}` of `{fn}`: expected {param_ty.name}, "
                          f"found {arg_ty.name}",
                    line=arg.line, expected=str(param_ty), found=str(arg_ty),
                    why="base types differ.",
                    source_line=self.src_line(arg.line),
                ))
                return False
            # dependent requirement (mentions other names / len) -> SMT-discharge it
            if (param_ty.name == "Int" and not param_ty.refine.unknown
                    and P.is_dependent(param_ty.refine.pred)):
                return self._check_dependent(arg_ty, param_ty, arg, env, term_map,
                                             context=f"argument `{pname}` of `{fn}`",
                                             pname=pname)
            return self._check_refine(arg_ty, param_ty, arg.line,
                                      context=f"argument `{pname}` of `{fn}`",
                                      param_name=pname, arg=arg)
        return False

    # --- #2 precise function-arg subtyping -------------------------------- #
    def _check_arrow_sub(self, src: T.ArrowType, dst: T.ArrowType, arg: N.Expr,
                         fn: str, pname: str) -> None:
        """Check `src <: dst` for function types: a value of arrow type `src` may be
        used where `dst` is expected iff its parameters are *contravariant* (it accepts
        at least what callers will pass) and its result is *covariant* (it returns at
        most what callers expect). Effects must be a subset of the expected row."""
        where = f"function `{pname}` (argument of `{fn}`)"
        if len(src.params) != len(dst.params):
            self.diags.append(Diagnostic(
                code="arity",
                title=f"argument `{pname}` of `{fn}`: function takes "
                      f"{len(src.params)} parameter(s), but {len(dst.params)} expected",
                line=arg.line, expected=str(dst), found=str(src),
                why="a function argument must have the arity its parameter declares.",
                source_line=self.src_line(arg.line),
            ))
            return
        # parameters: contravariant -> expected param <: actual param
        for i, (dp, sp) in enumerate(zip(dst.params, src.params)):
            self._check_sub(dp, sp, arg, f"parameter {i + 1} of {where}")
        # result: covariant -> actual result <: expected result
        self._check_sub(src.ret, dst.ret, arg, f"result of {where}")
        self._check_arrow_effects(src, dst, arg, fn, pname)

    def _check_sub(self, src: T.Type, dst: T.Type, arg: N.Expr, role: str) -> None:
        """`src <: dst` for a type in argument/result position. Gradual on either side
        is accepted silently (the `?` is consistent with everything); only a provable
        refinement conflict, a base-type clash, or an arity mismatch is reported."""
        if isinstance(src, T.ArrowType) and isinstance(dst, T.ArrowType):
            self._check_arrow_sub(src, dst, arg, role, role)  # nested, variance recurses
            return
        if isinstance(src, T.DataType) or isinstance(dst, T.DataType):
            if not (isinstance(src, T.DataType) and isinstance(dst, T.DataType)
                    and src.name == dst.name):
                self._sub_type_clash(src, dst, arg, role)
            return
        if isinstance(src, BaseType) and isinstance(dst, BaseType):
            if src.name != dst.name:
                self._sub_type_clash(src, dst, arg, role)
                return
            # refinement subtyping: a `?` on either side defers (accepted); a dependent
            # predicate has no call-site context here -> accepted; otherwise decide it.
            if dst.refine.unknown or src.refine.unknown:
                return
            if P.is_dependent(dst.refine.pred) or P.is_dependent(src.refine.pred):
                return
            cx = P.implies(src.refine.pred, dst.refine.pred)
            if cx is not None:
                self.diags.append(self._refine_conflict(
                    src, dst, arg.line, role, cx, param_name=None, arg=arg))
            return
        # mixed kinds (e.g. function vs base) -> a clash
        if type(src) is not type(dst):
            self._sub_type_clash(src, dst, arg, role)

    def _sub_type_clash(self, src: T.Type, dst: T.Type, arg: N.Expr, role: str) -> None:
        self.diags.append(Diagnostic(
            code="type",
            title=f"{role}: expected {dst}, found {src}",
            line=arg.line, expected=str(dst), found=str(src),
            why="the function argument's type does not match the expected signature.",
            source_line=self.src_line(arg.line),
        ))

    def _check_arrow_effects(self, src: T.ArrowType, dst: T.ArrowType, arg: N.Expr,
                             fn: str, pname: str) -> None:
        """The argument function may perform only effects the expected row allows. An
        effect *variable* in the expected row is polymorphic and absorbs any effect."""
        if any(T.is_effect_var(ef.name) for ef in dst.effects):
            return
        allowed = {ef.name for ef in dst.effects}
        leaked = sorted({ef.name for ef in src.effects
                         if not T.is_effect_var(ef.name) and ef.name not in allowed})
        if leaked:
            self.diags.append(Diagnostic(
                code="effect-leak",
                title=f"argument `{pname}` of `{fn}`: function performs "
                      f"{T.effects_str([Effect(n) for n in leaked])} not allowed by the "
                      f"expected row {T.effects_str(dst.effects)}",
                line=arg.line, expected=str(dst), found=str(src),
                why="a function argument may perform only effects its parameter declares.",
                source_line=self.src_line(arg.line),
            ))

    # --- dependent-refinement machinery (#5) ------------------------------ #
    def _arg_term(self, e: N.Expr):
        if isinstance(e, N.Var):
            return P.TVar(e.name)
        if isinstance(e, N.IntLit):
            return P.TInt(e.value)
        if isinstance(e, N.UnOp) and e.op == "-" and isinstance(e.operand, N.IntLit):
            return P.TInt(-e.operand.value)
        if (isinstance(e, N.Call) and e.fn in P.MEASURE_ARITY
                and len(e.args) == P.MEASURE_ARITY[e.fn]):
            ts = [self._arg_term(a) for a in e.args]
            if all(t is not None for t in ts):
                return P.TMeasure(e.fn, tuple(ts))
        return None  # complex argument -> cannot name it symbolically

    def _assumptions(self, env: Env) -> list:
        """Everything currently known: each in-scope refined Int, plus branch facts."""
        out = []
        for name, ty in env.items():
            if isinstance(ty, BaseType) and ty.name == "Int" and not ty.refine.unknown:
                out.append(P.subst_value(ty.refine.pred, P.TVar(name)))
            elif isinstance(ty, BaseType) and ty.name == "Array" and not ty.refine.unknown:
                # statically-known array length becomes the fact `len(name) == N` (#4)
                out.append(P.subst_value(ty.refine.pred, P.TLen(name)))
        out.extend(self.assumptions)  # path facts from enclosing guards (#5)
        return out

    def _gradual(self, line: int, context: str, what: str) -> None:
        self.warnings.append(Warning(
            code="cast", line=line,
            message=(f"{context} requires {what}, which cannot be proven statically here "
                     f"-- inserting a runtime check (guard it, or refine the inputs to "
                     f"prove it now)."),
        ))

    def _discharge(self, goal, assumptions, line, context, what, make_conflict) -> bool:
        """Returns True if a runtime check is needed (gradual), False if it can be erased.

        Proven -> erase (False); provably impossible -> hard error (False, won't run);
        otherwise -> deferred runtime check (True)."""
        try:
            model = P.z3_discharge(assumptions, goal)
        except P.NotDecidable:
            self._gradual(line, context, what)
            return True
        if model is None:
            return False  # statically proven -> no runtime check (erased)
        try:
            possible = P.z3_consistent(assumptions + [goal])
        except P.NotDecidable:
            possible = True
        if possible:
            self._gradual(line, context, what)  # might hold at runtime -> check there
            return True
        self.diags.append(make_conflict(model))  # provably impossible -> hard error
        return False

    def _model_str(self, model: dict) -> str:
        parts = [f"{k} = {v}" for k, v in sorted(model.items())]
        return ", ".join(parts) if parts else "(some inputs)"

    def _check_dependent(self, arg_ty: BaseType, param_ty: BaseType, arg: N.Expr,
                         env: Env, term_map: dict, context: str, pname: str) -> bool:
        argterm = self._arg_term(arg)
        req = P.render(param_ty.refine.pred, param_ty.refine.var)
        if argterm is None:
            self._gradual(arg.line, context, f"{{{param_ty.refine.var} | {req}}}")
            return True
        goal = P.subst_value(P.subst_names(param_ty.refine.pred, term_map), argterm)
        assumptions = self._assumptions(env)
        if not arg_ty.refine.unknown:
            assumptions.append(P.subst_value(arg_ty.refine.pred, argterm))

        def make_conflict(model):
            return Diagnostic(
                code="refine-conflict",
                title=f"{context} cannot be proven",
                line=arg.line,
                expected=str(param_ty),
                found=str(arg_ty),
                why=(f"the requirement ({req}) does not hold for the value supplied, "
                     f"given what is known about the other inputs."),
                counterexample=(f"fails when {self._model_str(model)}" if model
                                else f"the instantiated requirement `{goal}` is false"),
                choices=[
                    Choice(
                        label="LOOSEN the requirement",
                        explanation=(f"Weaken `{pname}`'s refinement to admit this case "
                                     f"(downstream code must then handle it)."),
                        edit=f"relax the `{{{param_ty.refine.var} | {req}}}` on `{pname}`",
                    ),
                    Choice(
                        label="STRENGTHEN the source",
                        explanation=(f"Establish ({req}) before the call -- e.g. guard "
                                     f"with `if {req} {{ ... }}` or refine the producer."),
                        edit=f"if {req} {{ ... }}   -- inside, the call is proven safe",
                    ),
                ],
                source_line=self.src_line(arg.line),
                note="a dependent (paid-for) obligation, discharged by the SMT backend.",
            )

        return self._discharge(goal, assumptions, arg.line, context,
                               f"{{{param_ty.refine.var} | {req}}}", make_conflict)

    def _check_index_bounds(self, e: N.Index, idx_ty: T.Type, env: Env) -> bool:
        if not isinstance(e.arr, N.Var):
            self._gradual(e.line, "array index", "0 <= i < len(array)")
            return True
        idxterm = self._arg_term(e.idx)
        if idxterm is None:
            self._gradual(e.line, "array index", "0 <= i < len(array)")
            return True
        arrname = e.arr.name
        goal = P.PAnd(P.PRel(">=", idxterm, P.TInt(0)),
                      P.PRel("<", idxterm, P.TLen(arrname)))
        assumptions = self._assumptions(env)
        if isinstance(idx_ty, BaseType) and idx_ty.name == "Int" and not idx_ty.refine.unknown:
            assumptions.append(P.subst_value(idx_ty.refine.pred, idxterm))

        def make_conflict(model):
            return Diagnostic(
                code="bounds",
                title=f"index may be out of bounds for `{arrname}`",
                line=e.line,
                expected=f"0 <= index < len({arrname})",
                found="an index not provably in range",
                why=(f"reading `{arrname}[i]` requires 0 <= i < len({arrname}); that does "
                     f"not hold for all permitted inputs."),
                counterexample=(f"out of bounds when {self._model_str(model)}" if model
                                else f"the access `{goal}` cannot hold"),
                choices=[
                    Choice(
                        label="GUARD the index",
                        explanation="Check the bound before indexing; inside the guard "
                                    "the access is proven safe.",
                        edit=f"if i < len({arrname}) {{ {arrname}[i] }} else {{ ... }}",
                    ),
                    Choice(
                        label="REFINE the index parameter",
                        explanation="Demand the bound in the signature so callers must "
                                    "prove it (dependent refinement).",
                        edit=f"i: Int{{k | k >= 0 && k < len({arrname})}}",
                    ),
                ],
                source_line=self.src_line(e.line),
                note="bounds are a dependent refinement; proven safe accesses cost nothing "
                     "at runtime, unproven ones are checked.",
            )

        return self._discharge(goal, assumptions, e.line, "array index",
                               f"0 <= i < len({arrname})", make_conflict)

    def _check_refine(self, src: BaseType, dst: BaseType, line: int, context: str,
                      param_name: Optional[str], arg: Optional[N.Expr]) -> bool:
        if dst.refine.unknown:
            return False  # requirement is `?` -- accepts anything, nothing to check
        if P.is_dependent(dst.refine.pred):
            # dependent return/binding obligation without call-site substitution
            # context -> defer to a runtime check rather than mis-judging it
            self._gradual(line, context, f"{{{dst.refine.var} | "
                          f"{P.render(dst.refine.pred, dst.refine.var)}}}")
            return True
        if src.refine.unknown:
            # gradual point: known requirement, unknown source -> runtime check
            self.warnings.append(Warning(
                code="cast", line=line,
                message=(f"{context} needs {dst}, but the value is an unrefined "
                         f"Int -- inserting a runtime check (pay later, or annotate to "
                         f"prove it now)."),
            ))
            return True
        # both sides fully refined -> decide it
        cx = P.implies(src.refine.pred, dst.refine.pred)
        if cx is None:
            return False  # statically proven -> erase the runtime check
        self.diags.append(self._refine_conflict(src, dst, line, context, cx,
                                                param_name, arg))
        return False  # hard conflict -> won't run, so no runtime check

    def _refine_conflict(self, src: BaseType, dst: BaseType, line: int, context: str,
                         cx: int, param_name: Optional[str],
                         arg: Optional[N.Expr]) -> Diagnostic:
        req_pred = P.render(dst.refine.pred, dst.refine.var)
        known_pred = P.render(src.refine.pred, src.refine.var)
        src_text = self.src_line(line)

        # The two framed choices.
        choices: List[Choice] = []
        target = f"`{param_name}`" if param_name else "the target"
        # (A) LOOSEN the requirement to admit what is actually known.
        loosened = P.render(src.refine.pred, dst.refine.var)
        choices.append(Choice(
            label="LOOSEN the requirement",
            explanation=(f"Accept what the source already guarantees. {target} would "
                         f"then admit values like {cx}; downstream code must be ready "
                         f"for them."),
            edit=f"Int{{{dst.refine.var} | {loosened}}}   "
                 f"(or drop the refinement entirely to go gradual)",
        ))
        # (B) STRENGTHEN the source so the requirement is met before this point.
        guard_var = src.refine.var
        choices.append(Choice(
            label="STRENGTHEN the source",
            explanation=(f"Guarantee {req_pred} before reaching here, e.g. guard the "
                         f"value or refine its producer. The counterexample {cx} is "
                         f"then excluded by construction."),
            edit=f"if {P.render(dst.refine.pred, guard_var)} {{ ... }}   "
                 f"-- inside the guard the value is proven {dst}",
        ))

        return Diagnostic(
            code="refine-conflict",
            title=f"{context} cannot be proven",
            line=line,
            expected=str(dst),
            found=str(src),
            why=(f"the source guarantees only ({known_pred}); the target requires "
                 f"({req_pred}). These do not agree on every value."),
            counterexample=(f"a value of {cx} satisfies the source but breaks the "
                            f"requirement ({req_pred} is false at {cx})"),
            choices=choices,
            source_line=src_text,
            note="this is a fully-refined (paid-for) obligation, so it is a hard error, "
                 "not a deferred runtime check.",
        )

    def _effect_leak(self, d: N.FnDecl, eff_name: str, line: int) -> None:
        declared = T.effects_str([Effect(e.name) for e in self.fns[d.name].effects])
        with_eff = sorted({e.name for e in self.fns[d.name].effects} | {eff_name})
        choices = [
            Choice(
                label="DECLARE the effect (make the cost visible)",
                explanation=(f"`{d.name}` really does perform `{eff_name}`. Surfacing it "
                             f"in the signature is honest and lets callers account for "
                             f"it."),
                edit=f"fn {d.name}(...) ! {{{', '.join(with_eff)}}} {{ ... }}",
            ),
            Choice(
                label="HANDLE or REMOVE the effect (keep the type pure)",
                explanation=(f"Discharge `{eff_name}` locally (e.g. `handle ... catch` "
                             f"for Exc) or drop the operation, so it never escapes "
                             f"`{d.name}`'s type."),
                edit=f"handle {{ ... }} catch (e) {{ ... }}   "
                     f"-- or remove the `{eff_name}` operation",
            ),
        ]
        self.diags.append(Diagnostic(
            code="effect-leak",
            title=f"`{d.name}` performs `{eff_name}` but its type hides it",
            line=line,
            expected=f"declared effects {declared}",
            found=f"`{eff_name}` performed in the body",
            why=(f"an effect is a property of the type. `{eff_name}` happens at runtime "
                 f"but the signature claims {declared}, so callers cannot see or "
                 f"control it."),
            choices=choices,
            source_line=self.src_line(line),
        ))

    # --- helpers ---------------------------------------------------------- #
    def _join(self, a: T.Type, b: T.Type) -> T.Type:
        if isinstance(a, T.DataType) and isinstance(b, T.DataType) and a.name == b.name:
            return a
        if isinstance(a, BaseType) and isinstance(b, BaseType) and a.name == b.name:
            if a.name == "Int":
                if a.refine.unknown or b.refine.unknown:
                    return T.INT()
                # the join is the union of guarantees (Or)
                return T.INT(P.POr(a.refine.pred, b.refine.pred), a.refine.var)
            return a
        return T.INT()  # disjoint -> gradual

    def _refine_env(self, cond: N.Expr, env: Env, negate: bool) -> Env:
        """Occurrence typing: a guard like `if x > 0` refines x inside the branch."""
        out = dict(env)
        self._apply_guard(cond, out, negate)
        return out

    def _apply_guard(self, cond: N.Expr, env: Env, negate: bool) -> None:
        if isinstance(cond, N.BinOp) and cond.op == "&&" and not negate:
            self._apply_guard(cond.left, env, negate)
            self._apply_guard(cond.right, env, negate)
            return
        if isinstance(cond, N.BinOp) and cond.op in ("<", "<=", ">", ">=", "==", "!="):
            var = name = None
            const = None
            op = cond.op
            if isinstance(cond.left, N.Var) and isinstance(cond.right, N.IntLit):
                name, const = cond.left.name, cond.right.value
            elif isinstance(cond.left, N.IntLit) and isinstance(cond.right, N.Var):
                name, const = cond.right.name, cond.left.value
                op = {"<": ">", "<=": ">=", ">": "<", ">=": "<=",
                      "==": "==", "!=": "!="}[op]
            if name is None:
                return
            if negate:
                op = {"<": ">=", "<=": ">", ">": "<=", ">=": "<",
                      "==": "!=", "!=": "=="}[op]
            cur = env.get(name)
            if not isinstance(cur, BaseType) or cur.name != "Int":
                return
            new = P.PCmp(op, const)
            pred = new if cur.refine.unknown else P.PAnd(cur.refine.pred, new)
            var = cur.refine.var if not cur.refine.unknown else name
            env[name] = T.INT(pred, var)


def check(module: N.Module, source: str) -> Tuple[List[Diagnostic], List[Warning]]:
    return Checker(module, source).run()
