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

## Implemented extensions (since v0.1 baseline)

- **#1 SMT backend**: `predicate.py` uses Z3 when `z3-solver` is importable, else the
  pure-Python sampler. Backend-agnostic `implies`/`witness`; `PROVISO_SOLVER=sampler` forces
  the fallback. `solver_backend()` reports the active one.
- **#2 type aliases**: `type Name = <type>` (see `samples/alias.pvo`). Resolved in both the
  checker and the interpreter, so runtime contracts and diagnostics pass through aliases.
- **#3 effect inference**: a function that omits its `!` row has effects inferred from its
  body (fixpoint; mutual recursion ok) and exported to callers — no leak. Writing `! {...}`
  (incl. `! {}`) is still an enforced contract. See `samples/infer_effects.pvo`.
- **#5 dependent refinements**: refinement predicates may mention other in-scope variables
  and the `len(x)` measure (`hi: Int{h | h >= lo}`, `i: Int{k | k < len(xs)}`). Predicate
  terms live in `predicate.py` (TVal/TInt/TVar/TLen/TArith, PRel). Call sites substitute
  formal→actual terms and discharge via `z3_discharge`/`z3_consistent`: proven → no runtime
  check; provably impossible → hard `refine-conflict`/`bounds` error with a counterexample;
  otherwise → gradual runtime check. Runtime contracts evaluate the predicate with the full
  arg environment. See `examples/07_dependent.pvo`.
- **#7 arrays**: `Array` (of Int), literals `[1, 2, 3]`, `len(a)`, indexing `a[i]` with the
  `0 <= i < len(a)` obligation handled by #5 (proven / runtime-checked / hard error). See
  `samples/arrays.pvo`.
- **#6 user-defined types**: `enum Name { Ctor(T, ...), ... }` (single-variant = record).
  Constructors are called like functions; `match e { Ctor(a, b) => ..., _ => ... }`
  deconstructs. Exhaustiveness is checked and reported as a `non-exhaustive` dialogue
  (add arms / add wildcard). Runtime value is `interp.Data(ctor, fields)`. See
  `samples/shapes.pvo`, `examples/08_match.pvo`.
- **#4 first-class functions + multi-shot effect handlers**: the interpreter is now a
  **CPS evaluator** (`eval(e, env, k, h)`; `run_to_value` is the delimiter). Lambdas
  `fn(x: Int) -> Int { ... }` are `Closure` values, `Fn`-typed params, callable
  (`f(x)`). Algebraic effects: `perform Op(arg)` + `handle <body> with { Op(x, k) =>
  ..., return(v) => ... }`. The resumption `k` is a first-class `Continuation` and is
  **fully multi-shot** — `_apply` composes `k(rv)`'s delimited result back into the
  current continuation, so `k(0) + k(10)` resumes twice. Operation names are effect
  labels: `perform Op` adds effect `Op`; `handle...with` discharges handled ops.
  Effect polymorphism is gradual: calling a `Fn` value contributes no static effects.
  See `samples/multishot.pvo`, `samples/hof.pvo`, `examples/09_effect_leak.pvo`.

## Bounded scope (deliberate, documented in README.md)

Dependent refinements work but the only measure is `len`; array *length* is not tracked
statically (so a literal index on a `let`-bound array is runtime-checked, not proven);
no occurrence typing on `len(x)` guards yet. simple linear-use ownership; base types are
`Int`/`Bool`/`Unit`/`Array(Int)`/`Fn` plus user `enum`s; aliases are bare names only. Enum
match patterns are one level deep (`Ctor(vars)` / `_`); no nested patterns, no `.field`
access (use match), no runtime contract enforcement on constructor fields. First-class
function types are gradual (`Fn`, not `Fn(Int)->Int`), so effect polymorphism is gradual
rather than via explicit effect variables. The CPS evaluator nests Python frames, so very
deep recursion can hit the (raised) recursion limit.

All seven requested features (#1-#7) are implemented; suite is 48 tests. Possible next
steps: precise `Fn(T)->T ! e` types with effect-variable polymorphism; more measures;
static array-length tracking; len-guard occurrence typing; strings; nested patterns; a
trampolined evaluator for deep recursion.
