# Proviso

An experimental programming language built around one idea: **correctness you can pay for
gradually**. The type system is meant to be a *dialogue partner*, not an adversary — every
rejection is a counterexample plus two framed choices.

Formerly codenamed **Veil**; renamed to Proviso (a *proviso* = a conditional stipulation,
which is exactly what a refinement type is). A second, original prototype copy still exists
at `C:\Users\sadie\hsp37_claudecode\veil`; this folder is the canonical one.

## The three axes

1. **Gradual dependent (refinement) types.** A type's refinement is either a predicate
   (`Int{n | n > 0}`) or `?` (unknown). Plain `Int` is `?`: consistent with everything,
   deferred to a runtime check. Add a refinement and the same call site becomes statically
   proven or statically rejected. Pay for proof when you choose to.
2. **Effects on the signature.** `fn f() -> Int ! {Net}` declares the effect row after `!`.
   Inferred effects must be a subset of declared ones (else `effect-leak`). A `Net`
   operation's "max 3 retries" safety property is a *refinement on its argument*, checked by
   the same solver — effects integrated with dependent types.
3. **Ownership as an effect.** `linear` bindings own a resource; using one performs a `Move`
   (consume); `borrow`/`clone` read without consuming. Use-after-move is explained with the
   move→reuse path, and offers borrow vs clone.

The thing the author cares about most is the **diagnostics**: `required` / `known` / `why` /
`counterexample` / two choices (LOOSEN the strict side vs STRENGTHEN the weak side), with
concrete suggested edits. See `examples/03_conflict.pvo`.

## Layout

```
proviso/        the implementation (Python, no deps)
  predicate.py    refinement solver: implies(P,Q) returns a counterexample — shared engine
  lexer.py parser.py nodes.py       front end
  types.py                          semantic types, refinements, effect rows
  checker.py                        gradual dependent type + effect checker -> Diagnostics
  ownership.py                      linear-use (ownership-as-effect) checker -> Diagnostics
  diagnostics.py                    the dialogue renderer
  interp.py                         tree-walking interpreter; refinements double as runtime contracts
  cli.py __main__.py                `proviso check|run`
examples/   one .pvo per axis, incl. the showcase conflict and the gradual seam
samples/    runnable programs (factorial, gcd, exceptions, relu, retry_budget, resource)
tests/run_tests.py   16 dependency-free tests
```

## How to run (from this folder)

```powershell
python -m proviso run   samples\factorial.pvo      # type-checks, then runs
python -m proviso check examples\03_conflict.pvo   # see the dialogue-style diagnostic
python tests\run_tests.py                           # 16 tests
```

`run` type-checks first and refuses to run on hard errors; gradual points (`?`) are deferred
to runtime checks instead. Source files use the `.pvo` extension.

## Bounded scope (deliberate, documented in README.md)

Single-bound-variable refinements over linear integer arithmetic (no SMT / full Π-types
yet — the `implies`-returns-counterexample interface is built to swap in Z3 later);
`Exc`-only resumable handlers; simple linear-use ownership; only `Int`/`Bool`/`Unit`.

Natural next steps: real SMT backend + cross-argument dependency; multi-shot effect
handlers; user-defined types; strings.
