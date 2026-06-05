"""Hand-written lexer for Proviso."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

KEYWORDS = {
    "fn", "let", "linear", "if", "else", "true", "false",
    "handle", "catch", "return", "type",
}

# Multi-character operators must be tried before single-character ones.
_SYMBOLS = [
    "->", "==", "!=", "<=", ">=", "&&", "||",
    "(", ")", "{", "}", "[", "]", ",", ";", ":", "|", "!",
    "+", "-", "*", "/", "%", "<", ">", "=", ".",
]


@dataclass
class Token:
    kind: str   # 'int' | 'ident' | 'kw' | 'sym' | 'eof'
    value: str
    line: int
    col: int


class LexError(Exception):
    def __init__(self, message: str, line: int, col: int):
        super().__init__(message)
        self.message = message
        self.line = line
        self.col = col


def tokenize(src: str) -> List[Token]:
    toks: List[Token] = []
    i = 0
    line = 1
    col = 1
    n = len(src)

    def advance(k: int = 1):
        nonlocal i, col
        i += k
        col += k

    while i < n:
        c = src[i]
        if c == "\n":
            i += 1
            line += 1
            col = 1
            continue
        if c in " \t\r":
            advance()
            continue
        if c == "#":  # line comment
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c.isdigit():
            start = i
            start_col = col
            while i < n and src[i].isdigit():
                advance()
            toks.append(Token("int", src[start:i], line, start_col))
            continue
        if c.isalpha() or c == "_":
            start = i
            start_col = col
            while i < n and (src[i].isalnum() or src[i] == "_"):
                advance()
            word = src[start:i]
            kind = "kw" if word in KEYWORDS else "ident"
            toks.append(Token(kind, word, line, start_col))
            continue
        matched = False
        for sym in _SYMBOLS:
            if src.startswith(sym, i):
                toks.append(Token("sym", sym, line, col))
                advance(len(sym))
                matched = True
                break
        if not matched:
            raise LexError(f"unexpected character {c!r}", line, col)

    toks.append(Token("eof", "", line, col))
    return toks
