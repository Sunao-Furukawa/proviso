"""Diagnostics -- the part the design cares about most.

The compiler is meant to be a conversation partner, not an adversary.  Every
diagnostic therefore has the same shape:

    - what was expected, and what was found
    - *why* they conflict, with a concrete counterexample
    - two ways forward, framed as a choice the human gets to make:
        (A) LOOSEN  -- weaken the stricter side
        (B) STRENGTHEN -- reinforce the weaker side

This module is pure presentation: the checker builds these objects, and this
renders them.  Keeping it separate is deliberate -- the dialogue is a first-class
artifact, not string-formatting scattered through the type checker.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


# ANSI styling, with a graceful monochrome fallback.
class _Style:
    enabled = True

    def _w(self, code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if self.enabled else s

    def bold(self, s): return self._w("1", s)
    def dim(self, s): return self._w("2", s)
    def red(self, s): return self._w("31", s)
    def green(self, s): return self._w("32", s)
    def yellow(self, s): return self._w("33", s)
    def blue(self, s): return self._w("34", s)
    def magenta(self, s): return self._w("35", s)
    def cyan(self, s): return self._w("36", s)


style = _Style()


@dataclass
class Choice:
    label: str          # "LOOSEN the requirement" / "STRENGTHEN the source"
    explanation: str     # what this trade-off means
    edit: str            # a concrete suggested edit


@dataclass
class Diagnostic:
    code: str            # e.g. "refine-conflict"
    title: str
    line: int
    expected: str        # the constraint that was required
    found: str           # the constraint that was actually known
    why: str             # the human-readable reason
    counterexample: Optional[str] = None
    choices: List[Choice] = field(default_factory=list)
    source_line: Optional[str] = None  # the offending line of source, if known
    note: Optional[str] = None

    def render(self) -> str:
        S = style
        out: List[str] = []
        out.append(S.red(S.bold(f"conflict[{self.code}]")) + S.bold(f": {self.title}"))
        out.append(S.dim(f"  at line {self.line}"))
        if self.source_line is not None:
            out.append(S.dim("   |"))
            out.append(S.dim("   | ") + self.source_line.strip())
            out.append(S.dim("   |"))
        out.append("")
        out.append(f"  {S.cyan('required')}  {self.expected}")
        out.append(f"  {S.yellow('known')}     {self.found}")
        out.append("")
        out.append(f"  {S.bold('why')}  {self.why}")
        if self.counterexample is not None:
            out.append(
                f"  {S.magenta('counterexample')}  {self.counterexample}"
            )
        if self.choices:
            out.append("")
            out.append("  " + S.bold("two ways forward -- your call:"))
            for i, ch in enumerate(self.choices):
                tag = S.green(f"  ({chr(ord('A') + i)}) {ch.label}")
                out.append(tag)
                out.append(f"      {ch.explanation}")
                out.append(f"      {S.dim('edit:')} {S.bold(ch.edit)}")
        if self.note:
            out.append("")
            out.append(S.dim(f"  note: {self.note}"))
        return "\n".join(out)


@dataclass
class Warning:
    """A non-fatal note -- e.g. a gradual point where a runtime check was inserted."""
    code: str
    line: int
    message: str

    def render(self) -> str:
        S = style
        return (
            S.yellow(S.bold(f"gradual[{self.code}]"))
            + f" line {self.line}: {self.message}"
        )
