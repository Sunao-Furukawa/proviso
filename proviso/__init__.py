"""Proviso -- a language whose type system you can pay for gradually.

Public surface:
    proviso.parse(src)            -> Module
    proviso.check(module, src)    -> (diagnostics, warnings)
    proviso.check_ownership(...)  -> diagnostics
    proviso.run_module(module)    -> value
    proviso.load(src, path)       -> (Module, SourceMap)   # resolves `import`s
"""
from .parser import parse, ParseError
from .lexer import LexError
from .checker import check
from .ownership import check_ownership
from .typestate import check_typestate
from .interp import run_module, ProvisoRuntimeError, ProvisoThrow
from .loader import load, LoadError

__all__ = [
    "parse", "ParseError", "LexError",
    "check", "check_ownership", "check_typestate",
    "run_module", "ProvisoRuntimeError", "ProvisoThrow",
    "load", "LoadError",
]

__version__ = "1.0.0"
