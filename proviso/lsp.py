"""A minimal Language Server for Proviso (#10) -- pure Python, no dependencies.

Speaks the Language Server Protocol over stdio (JSON-RPC with `Content-Length`
framing) so any LSP-capable editor gets Proviso's dialogue diagnostics live, plus
hover showing the (effect-inferred) signature of the enclosing function.

The interesting design point: the diagnostics an editor shows are *the same dialogue
objects* the CLI renders -- counterexample, the two framed choices, the lot -- merely
re-encoded as LSP `Diagnostic`s. The whole language is built around that artifact, so
the editor integration is a re-presentation, not a second implementation.

Run it with:  python -m proviso lsp

The protocol-handling core (`compute_diagnostics`, `hover_at`, `LspServer.handle`) is
plain data-in/data-out so it can be unit-tested without real stdio.
"""
from __future__ import annotations

import json
import sys
from typing import Dict, List, Optional

from .parser import parse, ParseError
from .lexer import LexError, tokenize
from .checker import Checker
from .ownership import check_ownership
from .typestate import check_typestate
from . import nodes as N
from .loader import load, LoadError, LINE_STRIDE

# LSP DiagnosticSeverity
_ERROR = 1
_WARNING = 2


# --------------------------------------------------------------------------- #
# Diagnostics: run the full analysis and re-encode each Diagnostic/Warning as LSP.
# --------------------------------------------------------------------------- #
def _line_range(text: str, line1: int, col1: Optional[int] = None) -> dict:
    """An LSP range (0-based) for 1-based `line1`. With no column, span the trimmed
    line; with a column, a one-token-ish span starting there."""
    lines = text.splitlines()
    idx = line1 - 1
    if idx < 0 or idx >= len(lines):
        return {"start": {"line": max(0, idx), "character": 0},
                "end": {"line": max(0, idx), "character": 0}}
    src = lines[idx]
    if col1 is not None:
        start = max(0, col1 - 1)
        return {"start": {"line": idx, "character": start},
                "end": {"line": idx, "character": max(start + 1, len(src))}}
    lead = len(src) - len(src.lstrip())
    return {"start": {"line": idx, "character": lead},
            "end": {"line": idx, "character": len(src)}}


def _diag_message(d) -> str:
    """Flatten a Proviso Diagnostic into the dialogue text an editor will surface."""
    parts = [d.title, "", f"required: {d.expected}", f"known:    {d.found}", "",
             f"why: {d.why}"]
    if d.counterexample:
        parts.append(f"counterexample: {d.counterexample}")
    if d.choices:
        parts.append("")
        parts.append("two ways forward:")
        for i, ch in enumerate(d.choices):
            parts.append(f"  ({chr(ord('A') + i)}) {ch.label}")
            parts.append(f"      {ch.explanation}")
            parts.append(f"      edit: {ch.edit}")
    if d.note:
        parts.append("")
        parts.append(f"note: {d.note}")
    return "\n".join(parts)


def _uri_to_path(uri: str) -> Optional[str]:
    """`file:///C:/x/y.pvo` -> a filesystem path (None for non-file URIs)."""
    if not uri.startswith("file:"):
        return None
    from urllib.parse import urlparse, unquote
    from urllib.request import url2pathname
    return url2pathname(unquote(urlparse(uri).path))


def _path_to_uri(path: str) -> str:
    import pathlib
    return pathlib.Path(path).resolve().as_uri()


def _linked(text: str, path: Optional[str]):
    """(module, source-for-the-passes, SourceMap) for a document, imports linked in."""
    module, smap = load(text, path)
    source = smap if len(smap.segments) > 1 else text
    return module, source, smap


def compute_diagnostics(text: str, path: Optional[str] = None) -> List[dict]:
    """Analyze source and return LSP Diagnostic dicts (errors + gradual warnings).

    `import`s are resolved relative to `path`. A hard error *inside* an imported
    module is reported on the importing line (naming the file); gradual points inside
    the library are the library's business and are not shown."""
    try:
        module, source, smap = _linked(text, path)
    except (ParseError, LexError, LoadError) as e:
        line = e.line if e.line < LINE_STRIDE else 1
        return [{
            "range": _line_range(text, line, getattr(e, "col", None)),
            "severity": _ERROR, "source": "proviso",
            "code": "import" if isinstance(e, LoadError) else "parse",
            "message": e.message,
        }]
    chk = Checker(module, source)
    diags, warns = chk.run()
    diags = diags + check_ownership(module, source) + check_typestate(module, source)
    warns = [w for w in warns if smap.is_main(w.line)]
    for d in diags:
        if not smap.is_main(d.line):
            f, loc = smap.locate(d.line)
            d.title = f"in imported {f}:{loc}: {d.title}"
            d.line = smap.root_line(d.line)

    out: List[dict] = []
    for d in diags:
        out.append({
            "range": _line_range(text, d.line),
            "severity": _ERROR, "source": "proviso", "code": d.code,
            "message": _diag_message(d),
        })
    for w in warns:
        out.append({
            "range": _line_range(text, w.line),
            "severity": _WARNING, "source": "proviso", "code": w.code,
            "message": f"gradual point: {w.message}",
        })
    return out


# --------------------------------------------------------------------------- #
# Hover: the signature of the function enclosing the cursor.
# --------------------------------------------------------------------------- #
def hover_at(text: str, line0: int, _char0: int,
             path: Optional[str] = None) -> Optional[dict]:
    """Hover for a 0-based position: the enclosing function's inferred signature."""
    try:
        module, source, smap = _linked(text, path)
    except (ParseError, LexError, LoadError):
        try:
            module, source, smap = parse(text), text, None
        except (ParseError, LexError):
            return None
    line1 = line0 + 1
    decls = sorted((d for d in module.decls if d.line < LINE_STRIDE), key=lambda d: d.line)
    enclosing = None
    for d in decls:
        if d.line <= line1:
            enclosing = d
        else:
            break
    if enclosing is None:
        return None
    chk = Checker(module, source)
    chk.run()  # populates inferred effect rows
    ft = chk.fns.get(enclosing.name)
    if ft is None:
        return None
    sig = f"fn {enclosing.name}{ft}"
    value = f"```proviso\n{sig}\n```"
    return {"contents": {"kind": "markdown", "value": value}}


# --------------------------------------------------------------------------- #
# Go-to-definition: resolve the name under the cursor to its declaration.
# --------------------------------------------------------------------------- #
def _symbols(module: N.Module) -> Dict[str, int]:
    """Every declared name -> the 1-based line it is declared on: functions, type
    aliases, enums and their constructors, and protocols."""
    syms: Dict[str, int] = {}
    for d in module.decls:
        syms[d.name] = d.line
    for a in getattr(module, "aliases", []):
        syms[a.name] = a.line
    for e in getattr(module, "enums", []):
        syms[e.name] = e.line
        for v in e.variants:
            syms.setdefault(v.name, v.line)  # a constructor jumps to its variant
    for p in getattr(module, "protocols", []):
        syms[p.name] = p.line  # states are not standalone declarations
    return syms


def _name_range(text: str, line1: int, name: str) -> Optional[dict]:
    """An LSP range (0-based) covering `name` on 1-based `line1` (its declaration)."""
    lines = text.splitlines()
    idx = line1 - 1
    if idx < 0 or idx >= len(lines):
        return None
    src = lines[idx]
    col = src.find(name)
    if col < 0:
        return {"start": {"line": idx, "character": 0},
                "end": {"line": idx, "character": len(src)}}
    return {"start": {"line": idx, "character": col},
            "end": {"line": idx, "character": col + len(name)}}


def definition_location(text: str, line0: int, char0: int,
                        path: Optional[str] = None) -> Optional[tuple]:
    """Like definition_at, but also follows names into imported files (e.g. a call
    to `str_split` jumps into std/string.pvo). Returns (path-or-None, range); a None
    path means "this document"."""
    rng = definition_at(text, line0, char0)
    if rng is not None:
        return None, rng
    try:
        toks = tokenize(text)
        module, _src, smap = _linked(text, path)
    except (ParseError, LexError, LoadError):
        return None
    line1, col1 = line0 + 1, char0 + 1
    name = next((t.value for t in toks if t.kind == "ident" and t.line == line1
                 and t.col <= col1 <= t.col + len(t.value)), None)
    decl_line = _symbols(module).get(name) if name else None
    if decl_line is None or smap.is_main(decl_line):
        return None
    seg = smap.segment(decl_line)
    seg_text = "\n".join(seg.lines)
    return seg.path, _name_range(seg_text, decl_line - seg.base, name)


def definition_at(text: str, line0: int, char0: int) -> Optional[dict]:
    """The declaration range for the identifier at a 0-based position, or None. Used by
    `textDocument/definition` -- click a call, an enum, a constructor or a protocol name
    and jump to where it is declared."""
    try:
        toks = tokenize(text)
    except LexError:
        return None
    line1, col1 = line0 + 1, char0 + 1
    name = None
    for t in toks:
        if (t.kind == "ident" and t.line == line1
                and t.col <= col1 <= t.col + len(t.value)):
            name = t.value
            break
    if name is None:
        return None
    try:
        module = parse(text)
    except (ParseError, LexError):
        return None
    decl_line = _symbols(module).get(name)
    if decl_line is None:
        return None
    return _name_range(text, decl_line, name)


# --------------------------------------------------------------------------- #
# Incremental document sync: apply range-based edits to the stored text.
# --------------------------------------------------------------------------- #
def _offset(text: str, line0: int, char0: int) -> int:
    """Absolute index into `text` for a 0-based (line, character) position."""
    lines = text.split("\n")
    off = sum(len(lines[i]) + 1 for i in range(min(line0, len(lines))))
    return off + char0


def apply_change(text: str, change: dict) -> str:
    """Apply one `contentChanges` entry. With a `range` it is an incremental edit
    (LSP sync kind 2); without one it is a full-document replacement (kind 1)."""
    rng = change.get("range")
    if rng is None:
        return change["text"]
    start = _offset(text, rng["start"]["line"], rng["start"]["character"])
    end = _offset(text, rng["end"]["line"], rng["end"]["character"])
    return text[:start] + change["text"] + text[end:]


# --------------------------------------------------------------------------- #
# The server: dispatch LSP messages to lists of outgoing messages.
# --------------------------------------------------------------------------- #
class LspServer:
    def __init__(self) -> None:
        self.docs: Dict[str, str] = {}     # uri -> text
        self.running = True

    def handle(self, msg: dict) -> List[dict]:
        """Process one client message; return any responses/notifications to send."""
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            return [self._respond(mid, {
                "capabilities": {
                    # 2 = incremental sync (range-based edits); falls back to full
                    "textDocumentSync": {"openClose": True, "change": 2, "save": True},
                    "hoverProvider": True,
                    "definitionProvider": True,
                },
                "serverInfo": {"name": "proviso-lsp", "version": "1.0.0"},
            })]
        if method == "shutdown":
            return [self._respond(mid, None)]
        if method == "exit":
            self.running = False
            return []
        if method in ("initialized", "$/setTrace", "textDocument/didSave"):
            uri = self._uri(msg)
            if method == "textDocument/didSave" and uri in self.docs:
                return [self._publish(uri)]
            return []
        if method == "textDocument/didOpen":
            doc = msg["params"]["textDocument"]
            self.docs[doc["uri"]] = doc["text"]
            return [self._publish(doc["uri"])]
        if method == "textDocument/didChange":
            uri = msg["params"]["textDocument"]["uri"]
            changes = msg["params"].get("contentChanges", [])
            text = self.docs.get(uri, "")
            for ch in changes:  # incremental edits applied in order (full replace if no range)
                text = apply_change(text, ch)
            self.docs[uri] = text
            return [self._publish(uri)]
        if method == "textDocument/didClose":
            uri = self._uri(msg)
            self.docs.pop(uri, None)
            return [self._notify("textDocument/publishDiagnostics",
                                 {"uri": uri, "diagnostics": []})]
        if method == "textDocument/hover":
            uri = self._uri(msg)
            pos = msg["params"]["position"]
            text = self.docs.get(uri, "")
            hov = hover_at(text, pos["line"], pos["character"], _uri_to_path(uri))
            return [self._respond(mid, hov)]
        if method == "textDocument/definition":
            uri = self._uri(msg)
            pos = msg["params"]["position"]
            text = self.docs.get(uri, "")
            loc = definition_location(text, pos["line"], pos["character"],
                                      _uri_to_path(uri))
            result = None
            if loc is not None:
                target, rng = loc
                result = {"uri": _path_to_uri(target) if target else uri, "range": rng}
            return [self._respond(mid, result)]
        if mid is not None:
            return [self._respond(mid, None)]  # unknown request -> empty result
        return []

    # --- message builders ------------------------------------------------- #
    def _publish(self, uri: str) -> dict:
        diags = compute_diagnostics(self.docs.get(uri, ""), _uri_to_path(uri))
        return self._notify("textDocument/publishDiagnostics",
                            {"uri": uri, "diagnostics": diags})

    @staticmethod
    def _uri(msg: dict) -> str:
        return msg.get("params", {}).get("textDocument", {}).get("uri", "")

    @staticmethod
    def _respond(mid, result) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    @staticmethod
    def _notify(method: str, params: dict) -> dict:
        return {"jsonrpc": "2.0", "method": method, "params": params}

    # --- stdio loop ------------------------------------------------------- #
    def serve(self, stdin=None, stdout=None) -> None:
        stdin = stdin or sys.stdin.buffer
        stdout = stdout or sys.stdout.buffer
        while self.running:
            msg = read_message(stdin)
            if msg is None:
                break
            for out in self.handle(msg):
                write_message(stdout, out)


# --------------------------------------------------------------------------- #
# JSON-RPC framing (Content-Length headers, LSP base protocol).
# --------------------------------------------------------------------------- #
def read_message(stream) -> Optional[dict]:
    headers: Dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return None  # EOF
        line = line.decode("ascii", "replace").strip()
        if line == "":
            break
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    length = int(headers.get("content-length", 0))
    body = stream.read(length) if length else b""
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def write_message(stream, msg: dict) -> None:
    body = json.dumps(msg).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    stream.write(header)
    stream.write(body)
    stream.flush()


def main() -> int:
    LspServer().serve()
    return 0
