"""`proviso doc` -- browse the standard library from the terminal.

    proviso doc                 # every std module with its one-line summary
    proviso doc std.string      # a module's public API: signatures + `##` doc comments

Signatures are printed as written, plus the *inferred* effect row for functions that
omit theirs, so what you read is exactly the contract the checker enforces. Names
starting with `_` are library-private helpers and are not listed.
"""
from __future__ import annotations

import os
import re
from typing import List

from .checker import Checker
from .loader import STD_DIR, LoadError, load, std_modules


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8-sig") as f:
        return f.read()


def _module_path(name: str) -> str:
    parts = name.split(".")
    if parts == ["std"]:
        parts = ["std", "prelude"]
    if parts[0] != "std" or len(parts) != 2:
        raise LoadError(f"`{name}` is not a standard-library module "
                        f"(try one of: {', '.join(std_modules())})", 0)
    path = os.path.join(STD_DIR, parts[1] + ".pvo")
    if not os.path.isfile(path):
        raise LoadError(f"no module `{name}` (available: {', '.join(std_modules())})", 0)
    return path


def _summary(lines: List[str]) -> str:
    for ln in lines:
        s = ln.strip()
        if s.startswith("#") and not s.startswith("##"):
            text = s.lstrip("#").strip()
            if " -- " in text:
                return text.split(" -- ", 1)[1]
            if text:
                return text
    return ""


def list_modules() -> str:
    out = ["Proviso standard library  (import one with `import std.<name>;`, "
           "or everything with `import std;`)", ""]
    for name in std_modules():
        lines = _read(_module_path(name)).splitlines()
        out.append(f"  {name:<13} {_summary(lines)}")
    out.append("")
    out.append("`proviso doc std.<name>` shows a module's functions.")
    return "\n".join(out)


def module_doc(name: str) -> str:
    path = _module_path(name)
    src = _read(path)
    lines = src.splitlines()
    module, smap = load(src, path)
    chk = Checker(module, smap)
    chk.run()  # fills in inferred effect rows

    out: List[str] = []
    # the module's header comment block
    for ln in lines:
        s = ln.strip()
        if not s.startswith("#") or s.startswith("##"):
            break
        out.append(s.lstrip("#").lstrip() if s != "#" else "")
    imports = [i.target for i in module.imports]
    if imports:
        out.append("")
        out.append("imports: " + ", ".join(imports))

    def docs_above(line: int) -> List[str]:
        doc, i = [], line - 2
        while i >= 0 and lines[i].strip().startswith("##"):
            doc.insert(0, lines[i].strip()[2:].strip())
            i -= 1
        return doc

    def sig_text(line: int) -> str:
        text, i = "", line - 1
        while i < len(lines):
            text += " " + lines[i].strip()
            if "{" in lines[i] and text.count("(") <= text.count(")"):
                break
            i += 1
        text = " ".join(text.split())
        # the body brace is the first `{` that opens neither a refinement `{x | ...}`
        # nor an effect row `! {...}`
        for m in re.finditer(r"\{", text):
            j = m.start()
            if re.match(r"\{\s*\w+\s*\|", text[j:]) or text[:j].rstrip().endswith("!"):
                continue
            return text[:j].rstrip()
        return text

    local = [d for d in module.decls if smap.is_main(d.line)]
    types = [e for e in module.enums if smap.is_main(e.line)]
    protos = [p for p in module.protocols if smap.is_main(p.line)]
    if types or protos:
        out.append("")
        out.append("types:")
        for e in types:
            for doc in docs_above(e.line):
                out.append(f"  # {doc}")
            out.append("  " + lines[e.line - 1].strip())
        for p in protos:
            for doc in docs_above(p.line):
                out.append(f"  # {doc}")
            out.append("  " + lines[p.line - 1].strip())
    out.append("")
    out.append("functions:")
    for d in local:
        if d.name.startswith("_"):
            continue
        sig = sig_text(d.line)
        if not d.effects_declared:
            ft = chk.fns.get(d.name)
            if ft is not None and ft.effects:
                sig += "   # inferred ! {" + ", ".join(e.name for e in ft.effects) + "}"
        out.append("  " + sig)
        for doc in docs_above(d.line):
            out.append(f"      {doc}")
    return "\n".join(out)
