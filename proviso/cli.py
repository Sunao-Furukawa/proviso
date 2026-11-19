"""Command-line driver:  proviso check <file>  |  proviso run <file>"""
from __future__ import annotations

import sys
from typing import List

from .parser import parse, ParseError
from .lexer import LexError
from .checker import check
from .ownership import check_ownership
from .typestate import check_typestate
from .interp import Interpreter, ProvisoRuntimeError, ProvisoThrow, ProvisoExit
from .loader import load, LoadError
from .diagnostics import style, Diagnostic, Warning


def _banner(text: str) -> str:
    return style.bold(style.blue(f"== {text} " + "=" * max(0, 56 - len(text))))


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8-sig") as f:  # tolerate a UTF-8 BOM
        return f.read()


def _analyze(src: str, path: str = None):
    """Load (parse + link imports), then run every static pass over the linked program.

    Diagnostics inside an imported module are reported against that module's file;
    gradual points *inside the library* are its own business (they are runtime-checked
    there) and are not shown to the importing program."""
    module, smap = load(src, path)
    source = smap if module.imports or len(smap.segments) > 1 else src
    diags, warns = check(module, source)
    diags = diags + check_ownership(module, source) + check_typestate(module, source)
    for d in diags:
        d.file, d.line = smap.locate(d.line)
    warns = [w for w in warns if smap.is_main(w.line)]
    diags.sort(key=lambda d: (d.file is not None, d.file or "", d.line))
    warns.sort(key=lambda w: w.line)
    return module, diags, warns


_FRONT_END_ERRORS = (ParseError, LexError, LoadError)


def _front_end_error(e) -> None:
    kind = "import error" if isinstance(e, LoadError) else "parse error"
    print(style.red(style.bold(kind)) + f" at line {e.line}: {e.message}")


def cmd_check(path: str) -> int:
    src = _read(path)
    try:
        _module, diags, warns = _analyze(src, path)
    except _FRONT_END_ERRORS as e:
        _front_end_error(e)
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
        module, diags, warns = _analyze(src, path)
    except _FRONT_END_ERRORS as e:
        _front_end_error(e)
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
    except ProvisoExit as x:
        print()
        print(style.dim(f"=> exit({x.code})"))
        return x.code
    except ProvisoThrow as t:
        print(style.red(f"uncaught throw: code {t.code}"))
        return 1
    except ProvisoRuntimeError as e:
        print(style.red(style.bold("runtime error: ")) + str(e))
        return 1
    print()
    print(style.green("=> main returned ") + style.bold(str(result)))
    return 0


def cmd_doc(name: str = None) -> int:
    """`proviso doc` lists the std modules; `proviso doc std.string` prints a module's
    exported signatures (with inferred effect rows) and their doc comments."""
    from .stddoc import list_modules, module_doc
    try:
        print(module_doc(name) if name else list_modules())
    except LoadError as e:
        print(style.red(style.bold("doc error")) + f": {e.message}")
        return 2
    return 0


def main(argv: List[str] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    style.enabled = sys.stdout.isatty()
    if argv and argv[0] == "lsp":  # language server over stdio (#10)
        from .lsp import main as lsp_main
        return lsp_main()
    if argv and argv[0] == "doc":  # browse the standard library
        return cmd_doc(argv[1] if len(argv) > 1 else None)
    if len(argv) < 2 or argv[0] not in ("check", "run"):
        print("usage: proviso (check|run) <file.pvo> | proviso doc [std.module] | proviso lsp")
        return 64
    cmd, path = argv[0], argv[1]
    return cmd_check(path) if cmd == "check" else cmd_run(path)


if __name__ == "__main__":
    raise SystemExit(main())
