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
  typestate.py                      protocol/typestate state-tracking checker -> Diagnostics (#9)
  diagnostics.py                    the dialogue renderer
  interp.py                         tree-walking interpreter; refinements double as runtime contracts
  lsp.py                            stdio Language Server: diagnostics + hover + go-to-def (#10)
  cli.py __main__.py                `proviso check|run|lsp`
examples/   one .pvo per axis, incl. the showcase conflict and the gradual seam
samples/    runnable programs (factorial, gcd, exceptions, relu, retry_budget, resource)
tests/run_tests.py   157 dependency-free tests
```

## How to run (from this folder)

```powershell
python -m proviso run   samples\factorial.pvo      # type-checks, then runs
python -m proviso check examples\03_conflict.pvo   # see the dialogue-style diagnostic
python -m proviso lsp                               # language server over stdio (#10)
python tests\run_tests.py                           # 120 tests
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
  terms live in `predicate.py` (TVal/TInt/TVar/TLen/TArith/TMeasure, PRel). Call sites
  substitute formal→actual terms and discharge via `z3_discharge`/`z3_consistent`: proven →
  no runtime check; provably impossible → hard `refine-conflict`/`bounds` error with a
  counterexample; otherwise → gradual runtime check. Runtime contracts evaluate the predicate
  with the full arg environment. See `examples/07_dependent.pvo`.
- **#3 arithmetic measures**: besides the structural `len`, refinements may use the integer
  measures `abs(t)`, `min(a, b)`, `max(a, b)` (`Int{n | abs(n) <= 3}`,
  `Int{v | v >= min(lo, hi) && v <= max(lo, hi)}`). They are total and live in linear integer
  arithmetic, so the same `z3_discharge` engine proves / refutes them and erasure (#8) applies;
  the bundled sampler decides the single-value (`abs(value)…`) fragment too. `MEASURE_ARITY`
  in `predicate.py` is the registry (TMeasure node); `_z3_measure` encodes them
  (`abs`→`If(t>=0,t,-t)`, `min`/`max`→`If`). A negated int literal `-k` now carries the
  singleton `{value == -k}` so `abs` proves on negative literals. See `samples/measures.pvo`.
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
- **#1 nested / cross-handler effects + escaping continuations [DONE]**: the interpreter
  was already fully general (nested handlers, effects crossing function-call boundaries,
  multi-shot resumptions, and continuations that *escape* their handler -- the captured
  `k` closes over its whole delimited context, and `below = h[:i]` in `_perform` is
  exactly the handler's install-time stack, so resumptions re-install the right handlers).
  The only gap was the **checker**: calling a captured continuation that had escaped (bound
  in a `let`, returned, pulled from a `match`) and whose type a join had made gradual was
  rejected as "unknown function". Fix: in `_infer_call`, calling *any in-scope binding*
  whose type is not a precise arrow is a **gradual call** (thread arg effects, defer the
  rest to the runtime) -- the gradual thesis applied to first-class continuations. A
  genuinely unbound name is still a hard error. See `samples/continuations.pvo`,
  `examples/13_continuations.pvo`.

## Bounded scope (deliberate, documented in README.md)

Dependent refinements work; the measures are `len` (structural) plus the arithmetic
`abs`/`min`/`max` (#3). **len-guard occurrence typing (#5b)
is implemented**: a guard like `if len(xs) > 0` / `if i < len(xs)` records a path fact
(self.assumptions in the checker) so the guarded array access is statically proven (no
runtime check). **Literal array length is tracked statically (#4)**: an array literal's type carries
`len(self) == N` (BaseType("Array", Refinement)), surfaced by _assumptions as `len(a) == N`,
so in-range literal indexing is proven and out-of-range is a hard `bounds` error. Array
length passed through a function parameter is still gradual. Linear-use ownership; base
types `Int`/`Bool`/`Unit`/`Array(Int)`/`Fn`/`Str` plus user `enum`s. Constructor-field
runtime contracts not enforced; function-arg subtyping is gradual.

All seven requested features (#1-#7) are implemented. Plus: **strings** (`Str` type,
`"..."` literals with escapes, `+` concat, `==`, `to_str(n)`) and **nested patterns**
(match patterns are now a recursive `Pattern` AST: PatWild/PatVar/PatLit/PatCtor;
uppercase-initial idents are constructors, lowercase are binders; literals `0`/`true`/`"x"`
allowed; exhaustiveness recurses into nested patterns -- see the dedicated section below).

Plus **precise function types** `Fn(T, ...) -> T ! {effects}` (ArrowType, FnTypeExpr) with
**effect-variable polymorphism**: lowercase effect-row names are variables; a HOF's `! e` is
instantiated at each call from the actual function argument's effects (substitution in
_infer_call). Bare top-level fn names are function references (ArrowType / interp Closure).

**#2 precise function-arg subtyping [DONE]**: when a parameter has a precise `ArrowType`, the
function passed is matched by *subtyping* (`_check_arrow_sub` in checker.py): arity, then
**contravariant** parameters (expected param `<:` actual param) and a **covariant** result
(actual ret `<:` expected ret), recursing through nested arrows; the actual function's
effects must be a subset of the expected row, except an effect *variable* there absorbs any.
Refinement leaves reuse the `implies`/`_refine_conflict` dialogue (counterexample + LOOSEN/
STRENGTHEN). Gradual on either side (a `?` refinement, a bare `Fn` value, a dependent
predicate with no call-site context) is accepted silently -- we cannot wrap a closure to
insert a runtime check, so only provable conflicts/clashes are hard errors. A negated int
literal `-k` carries `{value == -k}`. See `samples/fn_subtype.pvo`, `examples/11_fn_subtype.pvo`.
Bounded: single-level effect-var instantiation; the name-based effect-inference pass (#3) does
not substitute vars; no runtime contract wrapping of function arguments.

Plus **trampolining**: the CPS evaluator returns `_Thunk`s driven by `_drive` (a loop), so
deep recursion (e.g. sumto(100000), count(300000)) stays flat instead of overflowing the
Python C stack. Handler boundaries / resumptions use nested `_drive` calls (one frame per
active handler/resume, not per recursion level). Suite is 63 tests.

**#9 typestate [DONE]**: `protocol Name { StateA, StateB }` (new top-level decl) names the
states a resource moves through; an operation annotates the state it requires/produces with
`@State` on a (real, runnable) carrier type -- `fn open(f: Handle @ Closed) -> Handle @ Open`.
`typestate.py` (a pass like ownership.py, wired into cli `_analyze` and exported as
`check_typestate`) walks each body tracking every binding's state (seeded from params and each
op's result state, threaded through `let` re-binds, merged across `if`/`match`); a call on a
provably-wrong state is a `typestate` dialogue (required/known state, the line it entered that
state, and two choices: ADVANCE via the transition op, or STAY and use an op valid in the
current state). State is gradual: an un-annotated / branch-merged-divergent binding is unknown
and accepted silently. A state name maps back to its protocol (`state_proto`), so any carrier
type works. `@State` is erased from the *type* (TypeExpr.state; `@` token; `protocol` keyword);
the main checker ignores `.state` (protocol carrier types resolve as ordinary/opaque types).
See `samples/typestate.pvo`, `examples/12_typestate.pvo`.

**Typestate runtime enforcement [DONE]**: the static pass proves what it can see; for what it
leaves *gradual* (a resource laundered through an un-annotated region), the runtime is the
backstop. `typestate.operation_signatures(module)` lowers each decl's `@State` to a
`RuntimeOpSig` (per-param required `(proto,state)`, the result's produced `(proto,state)`, a
`has_state` gate). The interpreter carries each protocol value as a `Resource(value, proto,
state)`: at a protocol-operation call, `call_user` checks every `@State` argument's state
(`_ts_enter` -> `ProvisoStateError` on a wrong-state use) and unwraps it to the bare carrier
for the body, then re-tags the result with the produced state (`_ts_wrap`). `Resource` is
transparent to ordinary operations via `_carrier` (arith/index/match/print/builtins see
through it); plain functions (no `@State`) are untouched, so state rides on the *value* and
survives the gradual region the static pass can't follow. See `examples/15_typestate_runtime.pvo`.

**#10 LSP [DONE]**: `proviso lsp` runs a pure-Python (no deps) Language Server over stdio
(`lsp.py`). JSON-RPC `Content-Length` framing (`read_message`/`write_message`); lifecycle
(initialize/initialized/shutdown/exit); **incremental document sync** (sync kind 2: range-based
`didChange` edits spliced by `apply_change`/`_offset`, with a full-replace fallback when a
change carries no `range`); `textDocument/publishDiagnostics` re-encodes the *same*
Diagnostic/Warning objects the CLI renders (`compute_diagnostics` -> LSP dicts, severity
Error/Warning, the dialogue in the message); `textDocument/hover` (`hover_at`) shows the
enclosing function's effect-inferred signature; **`textDocument/definition`** (`definition_at`
+ `_symbols`) jumps from any use of a function / type-alias / enum / constructor / protocol
name to its declaration (token-under-cursor located via the lexer, declaration line via the
AST). The core (`compute_diagnostics`, `hover_at`, `definition_at`, `LspServer.handle` -> list
of outgoing messages) is data-in/data-out for unit testing without real stdio.

**#8 contract erasure + blame [DONE]**: the checker decides per call-site/index whether a
refinement obligation is proven (erased) or gradual (checked); it annotates the AST
(`Call.runtime_checks` = set of arg indices to check; `Index.needs_check` bool). The
interpreter enforces only those (others cost nothing at runtime); `interp.checks_performed`
counts checks actually run. A failing gradual check raises ProvisoCastError with a blame
note naming the unproven call site (line). If a node has no annotation (program run without
the checker), the interpreter checks everything (sound fallback). Tests `run_src`/
`run_counting` run the checker first so erasure is active. See `samples/erasure.pvo`.

**Nested-pattern exhaustiveness [DONE]**: exhaustiveness now recurses *into* nested patterns
(Maranget's usefulness algorithm), not just the top-level constructor set. `checker._missing`
asks whether the all-wildcard vector is still useful against the arm matrix; it specializes
(`_specialize`) by each constructor of a *complete* column and defaults (`_default`) on an
incomplete/open one, threading the per-column semantic types (enum field types, `Bool`, or an
open `Int`/`Str` domain via `_signature_of`). When a gap exists it reconstructs a concrete
witness -- e.g. `Cons(_, Cons(_, _))`, the missing case nested one level down -- surfaced in
the `non-exhaustive` dialogue. A wildcard/binder arm still closes a column; literal patterns
(`Wrap(0)`) leave an open domain. See `examples/14_nested_match.pvo`, `samples/list.pvo`.

**Precise escaped-continuation types [DONE]**: a captured resumption `k` is now typed as a
*precise* `Fn(Int) -> answer` (ArrowType) rather than the gradual `Fn` marker. In
`_infer_handle_with` the delimited *answer type* is inferred first (the `return` clause's body
type, or the handled body's type), and each op-clause binds `k` to `ArrowType([Int], answer)`.
So calling a continuation -- including one that has *escaped* its handler (let-bound, returned,
pulled from a match) and is invoked later -- is a statically-typed call (`k(v) : answer`),
not a deferred gradual one. A genuinely unbound name is still a hard error; a clause that
returns `k` itself and joins it with a non-function answer degrades to gradual (unavoidable).
See `examples/13_continuations.pvo`, `samples/continuations.pvo`.

Roadmap (all DONE): #5b len-guard -> #4 array-length -> #3 more measures (abs/min/max) ->
#8 erasure+blame -> #2 precise function-arg subtyping -> #9 typestate -> #10 LSP ->
#1 nested cross-handler effects + escaping continuations -> nested-pattern exhaustiveness ->
typestate runtime enforcement -> LSP incremental sync + go-to-definition -> precise
(non-gradual) escaped-continuation types. Remaining ideas: redundant/unreachable-arm
detection; typestate runtime enforcement for branch-divergent merges; LSP find-references
and rename; precise types for continuations that escape through a non-function join.
