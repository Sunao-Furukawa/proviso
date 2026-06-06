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
from .lexer import LexError
from .checker import Checker
from .ownership import check_ownership
from .typestate import check_typestate

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


def compute_diagnostics(text: str) -> List[dict]:
    """Analyze source and return LSP Diagnostic dicts (errors + gradual warnings)."""
    try:
        module = parse(text)
    except (ParseError, LexError) as e:
        return [{
            "range": _line_range(text, e.line, getattr(e, "col", None)),
            "severity": _ERROR, "source": "proviso", "code": "parse",
            "message": e.message,
        }]
    chk = Checker(module, text)
    diags, warns = chk.run()
    diags = diags + check_ownership(module, text) + check_typestate(module, text)

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
def hover_at(text: str, line0: int, _char0: int) -> Optional[dict]:
    """Hover for a 0-based position: the enclosing function's inferred signature."""
    try:
        module = parse(text)
    except (ParseError, LexError):
        return None
    line1 = line0 + 1
    decls = sorted(module.decls, key=lambda d: d.line)
    enclosing = None
    for d in decls:
        if d.line <= line1:
            enclosing = d
        else:
            break
    if enclosing is None:
        return None
    chk = Checker(module, text)
    chk.run()  # populates inferred effect rows
    ft = chk.fns.get(enclosing.name)
    if ft is None:
        return None
    sig = f"fn {enclosing.name}{ft}"
    value = f"```proviso\n{sig}\n```"
    return {"contents": {"kind": "markdown", "value": value}}


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
                    "textDocumentSync": 1,   # 1 = full document sync
                    "hoverProvider": True,
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
            if changes:  # full sync: the last change carries the whole document
                self.docs[uri] = changes[-1]["text"]
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
            hov = hover_at(text, pos["line"], pos["character"])
            return [self._respond(mid, hov)]
        if mid is not None:
            return [self._respond(mid, None)]  # unknown request -> empty result
        return []

    # --- message builders ------------------------------------------------- #
    def _publish(self, uri: str) -> dict:
        diags = compute_diagnostics(self.docs.get(uri, ""))
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
