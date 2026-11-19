"""Module loader / linker: resolves `import` declarations into one program.

    import std.string;        # a bundled standard-library module (proviso/std/string.pvo)
    import "util.pvo";        # a file relative to the importing file

This plays the role a C toolchain gives the preprocessor + linker: every imported file
is parsed once (imports are deduplicated and cycle-safe), and its declarations are
linked into a single flat `Module` -- like C, Proviso has one global namespace, so a
name defined twice is a link error, reported with both locations.

Each file gets its own *line range* (file k starts at line k * LINE_STRIDE + 1), so the
existing passes need no notion of files: a diagnostic's line is mapped back to
(file, local line) through the `SourceMap`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import nodes as N
from .parser import parse

STD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "std")
LINE_STRIDE = 1_000_000


class LoadError(Exception):
    """An unresolvable import or a duplicate definition (a "link error")."""

    def __init__(self, message: str, line: int, col: int = 1, file: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.line = line
        self.col = col
        self.file = file


@dataclass
class Segment:
    base: int            # global line = base + local line
    path: Optional[str]  # absolute path (None for an in-memory main program)
    display: str         # what diagnostics print, e.g. "std/string.pvo"
    lines: List[str]
    root_line: int       # the main-file `import` line that (transitively) pulled it in


class _LinesView:
    """Sparse, list-like line table spanning every segment (see diagnostics.source_lines)."""

    def __init__(self, smap: "SourceMap"):
        self.smap = smap

    def __len__(self) -> int:
        last = self.smap.segments[-1]
        return last.base + len(last.lines)

    def __getitem__(self, idx: int) -> Optional[str]:
        return self.smap.line_text(idx + 1)


@dataclass
class SourceMap:
    segments: List[Segment] = field(default_factory=list)

    def lines_view(self) -> _LinesView:
        return _LinesView(self)

    def segment(self, line: int) -> Segment:
        return self.segments[line // LINE_STRIDE] if line // LINE_STRIDE < len(
            self.segments) else self.segments[0]

    def is_main(self, line: int) -> bool:
        return line < LINE_STRIDE

    def locate(self, line: int) -> Tuple[Optional[str], int]:
        """Global line -> (display name or None for the main file, local line)."""
        if self.is_main(line):
            return None, line
        seg = self.segment(line)
        return seg.display, line - seg.base

    def line_text(self, line: int) -> Optional[str]:
        seg = self.segment(line)
        local = line - seg.base
        if 1 <= local <= len(seg.lines):
            return seg.lines[local - 1]
        return None

    def root_line(self, line: int) -> int:
        return line if self.is_main(line) else self.segment(line).root_line


def _display(path: str) -> str:
    try:
        rel = os.path.relpath(path, STD_DIR)
        if not rel.startswith(".."):
            return "std/" + rel.replace(os.sep, "/")
    except ValueError:  # different drive on Windows
        pass
    return os.path.basename(path)


def resolve(imp: N.ImportDecl, importer: Optional[str]) -> str:
    if imp.is_path:
        base = os.path.dirname(importer) if importer else os.getcwd()
        path = os.path.normpath(os.path.join(base, imp.target))
        if not path.endswith(".pvo") and not os.path.exists(path):
            path += ".pvo"
        return path
    parts = imp.target.split(".")
    if parts == ["std"]:
        parts = ["std", "prelude"]  # `import std;` = the whole library
    if parts[0] != "std" or len(parts) < 2:
        raise LoadError(
            f"cannot resolve `import {imp.target}` -- library modules live under `std.` "
            f"(e.g. `import std.string;`); use `import \"file.pvo\";` for your own files",
            imp.line)
    return os.path.join(STD_DIR, *parts[1:]) + ".pvo"


def std_modules() -> List[str]:
    """The names of every bundled standard-library module (`std.<name>`)."""
    return sorted("std." + f[:-4] for f in os.listdir(STD_DIR) if f.endswith(".pvo"))


def load(src: str, path: Optional[str] = None) -> Tuple[N.Module, SourceMap]:
    """Parse `src` (the main program) and link in everything it imports."""
    smap = SourceMap()
    main_path = os.path.abspath(path) if path else None
    smap.segments.append(Segment(0, main_path, path or "<main>", src.splitlines(), 0))
    main = parse(src)
    if not main.imports:
        return main, smap

    order: List[N.Module] = []          # imported modules, dependencies first
    index: Dict[str, int] = {}          # abs path -> segment index (dedupe / cycles)

    def visit(mod: N.Module, importer: Optional[str], root: Optional[int]) -> None:
        for imp in mod.imports:
            target = resolve(imp, importer)
            key = os.path.normcase(os.path.abspath(target))
            if key in index:
                continue
            if not os.path.isfile(target):
                what = (f"no standard-library module `{imp.target}` (available: "
                        f"{', '.join(std_modules())})") if not imp.is_path else \
                       f"cannot find imported file `{imp.target}`"
                raise LoadError(what, imp.line)
            with open(target, "r", encoding="utf-8-sig") as f:
                text = f.read()
            k = len(smap.segments)
            index[key] = k
            rline = root if root is not None else imp.line
            smap.segments.append(Segment(k * LINE_STRIDE, target, _display(target),
                                         text.splitlines(), rline))
            sub = parse(text, first_line=k * LINE_STRIDE + 1)
            visit(sub, target, rline)
            order.append(sub)

    visit(main, main_path, None)
    return link(order + [main], smap), smap


def link(mods: List[N.Module], smap: SourceMap) -> N.Module:
    out = N.Module([], [], [], [], [])
    seen: Dict[str, Tuple[str, int]] = {}  # name -> (kind, global line)

    def claim(name: str, kind: str, line: int) -> None:
        if name in seen:
            okind, oline = seen[name]
            ofile, oloc = smap.locate(oline)
            nfile, nloc = smap.locate(line)
            raise LoadError(
                f"{kind} `{name}` is defined twice: at {nfile or 'line'}"
                f"{':' if nfile else ' '}{nloc} and at {ofile or 'line'}"
                f"{':' if ofile else ' '}{oloc} (Proviso, like C, links one flat "
                f"namespace -- rename one of them)",
                smap.root_line(line))
        seen[name] = (kind, line)

    for m in mods:
        for d in m.decls:
            claim(d.name, "function", d.line)
            out.decls.append(d)
        for a in m.aliases:
            claim(a.name, "type", a.line)
            out.aliases.append(a)
        for e in m.enums:
            claim(e.name, "type", e.line)
            out.enums.append(e)
            for v in e.variants:
                if v.name != e.name:  # a record's constructor shares its type's name
                    claim(v.name, "constructor", v.line)
        for p in m.protocols:
            out.protocols.append(p)
    return out
