"""Ownership, reinterpreted as an effect.

Rust's borrow checker is powerful but its rejections are famously terse.  Proviso treats
"consume this value" as a `Move` effect and "read it without consuming" as a `Borrow`
effect, and -- most importantly -- when the discipline is violated it explains the *path*:
where the value was moved, and where you then tried to use the corpse.

A `linear` binding holds an owned resource.  Any plain use moves (consumes) it.
`borrow(x)` and `clone(x)` read without consuming.  Use-after-move is the error, and it
always comes with the two standard ways out: borrow, or clone.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import nodes as N
from .diagnostics import Diagnostic, Choice


class OwnershipChecker:
    def __init__(self, module: N.Module, source: str):
        self.module = module
        self.src_lines = source.splitlines()
        self.diags: List[Diagnostic] = []

    def src_line(self, line: int) -> Optional[str]:
        if 1 <= line <= len(self.src_lines):
            return self.src_lines[line - 1]
        return None

    def run(self) -> List[Diagnostic]:
        for d in self.module.decls:
            self._check_fn(d)
        return self.diags

    def _check_fn(self, d: N.FnDecl) -> None:
        self.linear = {p.name for p in d.params if p.linear}
        self.moved: Dict[str, int] = {}
        self._block(d.body)

    # --- traversal -------------------------------------------------------- #
    def _block(self, b: N.Block) -> None:
        for st in b.stmts:
            if isinstance(st, N.LetStmt):
                self._expr(st.value)
                if st.linear:
                    self.linear.add(st.name)
                    self.moved.pop(st.name, None)  # fresh, live binding
            elif isinstance(st, N.ExprStmt):
                self._expr(st.expr)
        if b.result is not None:
            self._expr(b.result)

    def _expr(self, e: N.Expr) -> None:
        if isinstance(e, N.Var):
            self._use(e.name, e.line, borrow=False)
        elif isinstance(e, N.Call):
            if e.fn in ("borrow", "clone") and len(e.args) == 1 and isinstance(e.args[0], N.Var):
                v = e.args[0]
                self._use(v.name, v.line, borrow=True)
            else:
                for a in e.args:
                    self._expr(a)
        elif isinstance(e, N.BinOp):
            self._expr(e.left)
            self._expr(e.right)
        elif isinstance(e, N.UnOp):
            self._expr(e.operand)
        elif isinstance(e, N.Block):
            self._block(e)
        elif isinstance(e, N.If):
            self._expr(e.cond)
            base = dict(self.moved)
            self._block(e.then)
            moved_then = dict(self.moved)
            self.moved = dict(base)
            if e.els is not None:
                self._block(e.els)
            moved_else = dict(self.moved)
            merged = dict(moved_then)
            for k, v in moved_else.items():
                merged.setdefault(k, v)
            self.moved = merged
        elif isinstance(e, N.Handle):
            self._block(e.body)
            self._block(e.handler)
        elif isinstance(e, N.ArrayLit):
            for el in e.elements:
                self._expr(el)
        elif isinstance(e, N.Index):
            self._expr(e.arr)
            self._expr(e.idx)
        elif isinstance(e, N.Match):
            self._expr(e.scrutinee)
            base = dict(self.moved)
            merged = None
            for arm in e.arms:
                self.moved = dict(base)
                self._expr(arm.body)
                if merged is None:
                    merged = dict(self.moved)
                else:
                    for k, v in self.moved.items():
                        merged.setdefault(k, v)
            if merged is not None:
                self.moved = merged
        # literals: nothing

    # --- the rule --------------------------------------------------------- #
    def _use(self, name: str, line: int, borrow: bool) -> None:
        if name not in self.linear:
            return
        if name in self.moved:
            self.diags.append(self._after_move(name, line, self.moved[name], borrow))
            return
        if not borrow:
            self.moved[name] = line

    def _after_move(self, name: str, use_line: int, move_line: int, borrow: bool) -> Diagnostic:
        action = "borrow" if borrow else "use"
        return Diagnostic(
            code="moved",
            title=f"{action} of `{name}` after it was moved",
            line=use_line,
            expected=f"`{name}` to still own a live resource",
            found=f"`{name}`, whose value was moved away at line {move_line}",
            why=(f"passing `{name}` by value performs a Move effect that consumes it. "
                 f"After line {move_line} the name no longer denotes a live resource, "
                 f"so there is nothing here at line {use_line} to {action}."),
            counterexample=(f"trace: line {move_line} moves `{name}` -> "
                            f"line {use_line} touches `{name}` -> no value remains"),
            choices=[
                Choice(
                    label="BORROW at the first site (share, don't consume)",
                    explanation=(f"If the earlier use only needs to read `{name}`, borrow "
                                 f"it so ownership stays here and the later use is still "
                                 f"valid."),
                    edit=f"borrow({name})   at line {move_line}",
                ),
                Choice(
                    label="CLONE to get a second owned value",
                    explanation=(f"If both sites genuinely need ownership, make an "
                                 f"independent copy for one of them."),
                    edit=f"let {name}2 = clone({name});   then use `{name}2` at line {use_line}",
                ),
            ],
            source_line=self.src_line(use_line),
            note="ownership is just the Move/Borrow effect; this is the same machinery "
                 "as the effect row, specialised to resources.",
        )


def check_ownership(module: N.Module, source: str) -> List[Diagnostic]:
    return OwnershipChecker(module, source).run()
