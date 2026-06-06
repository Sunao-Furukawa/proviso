"""Proviso -- a language whose type system you can pay for gradually.

Public surface:
    proviso.parse(src)            -> Module
    proviso.check(module, src)    -> (diagnostics, warnings)
    proviso.check_ownership(...)  -> diagnostics
    proviso.run_module(module)    -> value
"""
from .parser import parse, ParseError
from .lexer import LexError
from .checker import check
from .ownership import check_ownership
from .typestate import check_typestate
from .interp import run_module, ProvisoRuntimeError, ProvisoThrow

__all__ = [
    "parse", "ParseError", "LexError",
    "check", "check_ownership", "check_typestate",
    "run_module", "ProvisoRuntimeError", "ProvisoThrow",
]

__version__ = "0.1.0"
