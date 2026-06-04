"""Command-line driver:  proviso check <file>  |  proviso run <file>"""
from __future__ import annotations

import sys
from typing import List

from .parser import parse, ParseError
from .lexer import LexError
from .checker import check
from .ownership import check_ownership
from .interp import Interpreter, ProvisoRuntimeError, ProvisoThrow
from .diagnostics import style, Diagnostic, Warning


def _banner(text: str) -> str:
    return style.bold(style.blue(f"== {text} " + "=" * max(0, 56 - len(text))))


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8-sig") as f:  # tolerate a UTF-8 BOM
        return f.read()


def _analyze(src: str):
    module = parse(src)
    diags, warns = check(module, src)
    diags = diags + check_ownership(module, src)
    diags.sort(key=lambda d: d.line)
    warns.sort(key=lambda w: w.line)
    return module, diags, warns


def cmd_check(path: str) -> int:
    src = _read(path)
    try:
        _module, diags, warns = _analyze(src)
    except (ParseError, LexError) as e:
        print(style.red(style.bold("parse error")) + f" at line {e.line}: {e.message}")
        return 2

    print(_banner(f"checking {path}"))
    print()
    for w in warns:
        print(w.render())
        print()
    for d in diags:
        print(d.render())
        print()

    if diags:
        n = len(diags)
        print(style.red(style.bold(
            f"FAIL: {n} unresolved obligation{'s' if n != 1 else ''} "
            f"({len(warns)} gradual point{'s' if len(warns) != 1 else ''} deferred to runtime)"
        )))
        return 1
    print(style.green(style.bold(
        f"OK: all obligations discharged "
        f"({len(warns)} gradual point{'s' if len(warns) != 1 else ''} will be checked at runtime)"
    )))
    return 0


def cmd_run(path: str) -> int:
    src = _read(path)
    try:
        module, diags, warns = _analyze(src)
    except (ParseError, LexError) as e:
        print(style.red(style.bold("parse error")) + f" at line {e.line}: {e.message}")
        return 2

    if diags:
        print(_banner(f"checking {path}"))
        print()
        for d in diags:
            print(d.render())
            print()
        print(style.red(style.bold(
            "FAIL: refusing to run: unresolved obligations above. "
            "Resolve them, or relax the annotations to go gradual."
        )))
        return 1

    if warns:
        print(_banner("gradual points (checked at runtime)"))
        for w in warns:
            print("  " + w.render())
        print()

    print(_banner(f"running {path}"))
    interp = Interpreter(module)
    try:
        result = interp.run("main")
    except ProvisoThrow as t:
        print(style.red(f"uncaught throw: code {t.code}"))
        return 1
    except ProvisoRuntimeError as e:
        print(style.red(style.bold("runtime error: ")) + str(e))
        return 1
    print()
    print(style.green("=> main returned ") + style.bold(str(result)))
    return 0


def main(argv: List[str] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    style.enabled = sys.stdout.isatty()
    if len(argv) < 2 or argv[0] not in ("check", "run"):
        print("usage: proviso (check|run) <file.pvo>")
        return 64
    cmd, path = argv[0], argv[1]
    return cmd_check(path) if cmd == "check" else cmd_run(path)


if __name__ == "__main__":
    raise SystemExit(main())
