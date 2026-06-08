# The Proviso Programming Manual

**Version 1.0.0**

*(日本語版: [MANUAL.ja.md](MANUAL.ja.md))*

Proviso is an experimental programming language built around one idea: **correctness you
can pay for gradually**. A type's refinement is either a *predicate* you have proven, or
`?` (*unknown*) — and the two live side by side. Where you write `?`, the language inserts
a runtime check and lets the program run; where you write a predicate, the same call site
becomes statically *proven* or statically *rejected*. You decide, per call site, how much
proof to buy.

When the type checker does reject something, it never just says "type error." It states
what was **required**, what is **known**, **why** they conflict, a concrete
**counterexample**, and **two framed choices** — loosen the strict side, or strengthen the
weak side — each with a suggested edit. The diagnostic is the centrepiece of the design.

This manual is both a tutorial and a reference for the v1.0.0 language.

---

## Table of contents

1. [Getting started](#1-getting-started)
2. [A tour of the language](#2-a-tour-of-the-language)
3. [Lexical structure](#3-lexical-structure)
4. [Types](#4-types)
5. [Refinements and the solver](#5-refinements-and-the-solver)
6. [Expressions and operators](#6-expressions-and-operators)
7. [Statements, bindings, and blocks](#7-statements-bindings-and-blocks)
8. [Functions](#8-functions)
9. [Control flow: `if` and `match`](#9-control-flow-if-and-match)
10. [User-defined types: `enum`](#10-user-defined-types-enum)
11. [Effects](#11-effects)
12. [Algebraic effects and handlers](#12-algebraic-effects-and-handlers)
13. [Exceptions](#13-exceptions)
14. [Ownership: linear resources](#14-ownership-linear-resources)
15. [Typestate](#15-typestate)
16. [Gradual contracts: erasure and blame](#16-gradual-contracts-erasure-and-blame)
17. [Reading a diagnostic](#17-reading-a-diagnostic)
18. [Built-in functions](#18-built-in-functions)
19. [Tooling: CLI, solver, editor](#19-tooling-cli-solver-editor)
20. [Bounded scope and known limitations](#20-bounded-scope-and-known-limitations)
21. [Cheat sheet](#21-cheat-sheet)
22. [Appendix A: grammar](#appendix-a-grammar)
23. [Appendix B: version history](#appendix-b-version-history)

---

## 1. Getting started

Proviso is implemented in pure Python (no required dependencies). Source files use the
`.pvo` extension. From the project folder:

```sh
python -m proviso run   samples/factorial.pvo     # type-check, then run
python -m proviso check examples/03_conflict.pvo  # type-check only; print the dialogue
python -m proviso lsp                             # language server over stdio
python tests/run_tests.py                         # the test suite (128 tests)
```

- **`run`** type-checks the program and *refuses to run* if there are hard errors. Gradual
  points (`?`) are not errors — they become runtime checks. It then calls `main`.
- **`check`** runs every static analysis (types, effects, ownership, typestate) and prints
  the diagnostics, but does not execute anything.
- A program that you intend to `run` must define `fn main() -> ... { ... }`.

An optional SMT backend (Z3) is used automatically if `z3-solver` is importable; otherwise
a bundled pure-Python sampler decides the single-variable fragment. Set
`PROVISO_SOLVER=sampler` to force the fallback. See [§19](#19-tooling-cli-solver-editor).

---

## 2. A tour of the language

**The prototype end.** Everything is a plain `Int`; nothing is proven; it just runs.

```proviso
fn inc(x: Int) -> Int { x + 1 }

fn main() -> Int ! {IO} {
  print(inc(41));   # 42
  inc(41)
}
```

**Buying a proof.** Add a refinement and the call site is now checked statically.

```proviso
fn sqrt_floor(x: Int{n | n > 0}) -> Int{r | r >= 0} { abs(x) }
```

`sqrt_floor(5)` is *proven* (no runtime cost). `sqrt_floor(0)` is a *hard error* with the
counterexample `0`. `sqrt_floor(some_plain_int)` is a *gradual point*: a runtime check is
inserted, and if it fails the blame points back at this call.

**The three axes, together.** Refinements, effects on the signature, and ownership all run
through the *same* refinement solver and the *same* dialogue diagnostics. The rest of this
manual takes them one at a time.

---

## 3. Lexical structure

- **Comments** start with `#` and run to end of line. There are no block comments.
- **Identifiers** match `[A-Za-z_][A-Za-z0-9_]*`. By convention, constructor and protocol
  state names are `UpperCamel`; everything else is `lower_snake`. In a `match` pattern an
  uppercase-initial name is a *constructor* and a lowercase-initial name is a *binder*.
- **Integer literals** are decimal digits, e.g. `42`. A negative literal is written `-42`
  (unary minus applied to `42`).
- **String literals** are double-quoted with escapes `\n`, `\t`, `\"`, `\\`, e.g.
  `"a\tb"`. They may not span lines.
- **Booleans**: `true`, `false`.
- **Keywords**: `fn let linear if else true false handle catch return type enum match
  perform with protocol`.
- **Operators / punctuation**: `-> => == != <= >= && || ( ) { } [ ] , ; : | ! + - * / % <
  > = . @`.

Whitespace is insignificant except as a token separator.

---

## 4. Types

The base types are:

| Type        | Values                          | Notes |
|-------------|---------------------------------|-------|
| `Int`       | machine integers                | carries a refinement (see §5) |
| `Bool`      | `true`, `false`                 | |
| `Unit`      | `()`                            | result of statements like `print` |
| `Str`       | `"..."`                         | `+` concatenates, `==` compares |
| `Array`     | `[1, 2, 3]`                     | arrays of `Int` (monomorphic in v1) |
| `Fn`        | functions / continuations       | the *gradual* function type |
| `Fn(T, …) -> T ! {E}` | functions               | a *precise* function type (see §8) |
| *enum name* | constructor values              | user-defined sum types (see §10) |

A plain `Int` is **gradual**: its refinement is `?` (unknown), so it is consistent with any
requirement and any requirement placed on it is deferred to a runtime check.

### Type aliases

`type Name = <type>` names a (possibly refined) type for reuse. Aliases are resolved by both
the checker and the interpreter, so runtime contracts and diagnostics pass through them.

```proviso
type Nat = Int{n | n >= 0}
type Percent = Int{p | p >= 0 && p <= 100}

fn clamp_high(x: Nat) -> Percent {
  if x <= 100 { x } else { 100 }
}
```

---

## 5. Refinements and the solver

A refinement constrains a base type by a predicate over an implicit *value variable* you
name:

```
Int{n | n > 0}              # the positive integers
Int{p | p >= 0 && p <= 100} # 0..100
Int{k | k != 0}             # non-zero
```

### Predicate grammar

A predicate is a boolean combination of **relations** between **terms**:

- boolean: `&&`, `||`, `!(...)`, and the literals `true` / `false`;
- relations: `<  <=  >  >=  ==  !=`;
- terms: integer literals, the value variable, other in-scope names (see *dependent*
  below), arithmetic `+ - *`, and the **measures** below.

### Measures

A *measure* is an integer-valued function usable inside a refinement:

- `len(a)` — the length of array `a` (the structural measure; proven `>= 0`).
- `abs(t)` — absolute value.
- `min(a, b)`, `max(a, b)` — minimum / maximum.

```proviso
fn small_step(d: Int{n | abs(n) <= 3}) -> Int { d }
fn in_range(lo: Int, hi: Int,
            x: Int{v | v >= min(lo, hi) && v <= max(lo, hi)}) -> Int { x }
```

`abs`, `min`, `max` live in linear integer arithmetic, so an obligation that mentions them
is proven or refuted *exactly*, just like any other refinement.

### Dependent refinements

A refinement may mention **other parameters** and `len`, so one argument's type can depend
on another:

```proviso
fn between(lo: Int, hi: Int{h | h >= lo},
           x: Int{v | v >= lo && v <= hi}) -> Int { x }
```

At each call site the formal names are substituted by the actual arguments and the
obligation is discharged: **proven** → no runtime check; **provably impossible** → a hard
error with a counterexample; **otherwise** → a deferred runtime check.

### How a refinement obligation is decided

Whenever a value of type `S` is used where `T` is required:

1. If `T`'s refinement is `?` → accepted (anything satisfies the unknown).
2. If `S`'s refinement is `?` → a **gradual point**: a runtime check is inserted.
3. If both are predicates → the solver checks `S ⟹ T`:
   - holds → **proven**, the check is *erased* (no runtime cost);
   - fails → a **hard error** carrying the counterexample `x` where `S(x)` but not `T(x)`.

The same engine produces the counterexample that drives the dialogue (see §17).

---

## 6. Expressions and operators

Proviso is expression-oriented: `if`, `match`, `handle`, and blocks all produce values.

Operators, from **lowest to highest** precedence:

| Precedence | Operators            | Associativity | Notes |
|-----------:|----------------------|---------------|-------|
| 1 | `\|\|`                       | left  | short-circuits |
| 2 | `&&`                       | left  | short-circuits |
| 3 | `== != < <= > >=`          | left  | comparisons |
| 4 | `+ -`                      | left  | `+` is Int add **or** `Str` concat |
| 5 | `* / %`                    | left  | `/` is floor division; `/ %` by `0` is a runtime error |
| 6 | unary `-`, `!`             | prefix | negation, logical not |
| 7 | call `f(...)`, index `a[i]` | postfix | |

Notes:

- **Calls are on names only.** `f(x)` requires `f` to be an identifier (a top-level
  function, a constructor, a built-in, or a binding holding a function/continuation). To
  call a lambda, bind it first: `let g = fn(x: Int) -> Int { x }; g(3)`.
- **Indexing** `a[i]` carries the obligation `0 <= i < len(a)` (see §5, §16).
- There are **no loops**; iteration is by recursion (the evaluator is trampolined, so deep
  recursion does not overflow the stack).

---

## 7. Statements, bindings, and blocks

A **block** `{ ... }` is a sequence of statements optionally ending in a *result
expression* (no trailing `;`); the result is the block's value. With no result expression a
block has type `Unit`.

```proviso
fn f(x: Int) -> Int {
  let y = x + 1;     # statement (note the ;)
  print(y);          # statement
  y * 2              # result expression (no ;) -> the block's value
}
```

**Bindings** introduce a name:

```proviso
let x = expr;            # infer the type from expr
let x: Int{n|n>0} = e;   # annotate (the value is checked against the annotation)
let linear conn = open();# a linear (owned) resource — see §14
```

A later `let` of the same name **shadows** the earlier one.

---

## 8. Functions

```proviso
fn name(p1: T1, p2: T2) -> Ret ! {Effects} {
  body
}
```

- The return type (`-> Ret`) and effect row (`! {Effects}`) are **optional**.
- Omitting `-> Ret` makes the result `Unit`.
- Omitting `! {…}` means the effect row is **inferred** from the body (see §11).
- Parameters may be refined and may be `linear` (see §14).

### First-class functions and precise types

Functions are values. A bare function name used as a value is a reference; an anonymous
function is a lambda:

```proviso
fn twice(f: Fn, x: Int) -> Int { f(f(x)) }   # gradual function param

fn main() -> Int ! {IO} {
  let g = fn(n: Int) -> Int { n * 10 };       # a lambda value
  print(twice(g, 3));                          # 300
  twice(g, 1)
}
```

A **precise** function type `Fn(T, …) -> T ! {E}` constrains the argument/result/effects. A
function passed where a precise type is expected is checked by **subtyping**:

- **contravariant** parameters — the function must accept at least what callers will pass;
- **covariant** result — the function must return at most what callers expect;
- the function's effects must be a **subset** of the expected row (an effect *variable*
  absorbs any effect — see §11).

Refinement clashes in either position are reported with the usual counterexample dialogue.
Gradual on either side (a `?`, a bare `Fn` value) is accepted silently.

```proviso
# `run_nonneg` only ever feeds its callee a non-negative Int, so a callee that
# accepts ANY Int is fine (contravariant parameter):
fn run_nonneg(f: Fn(Int{n | n >= 0}) -> Int, x: Int{n | n >= 0}) -> Int { f(x) }
fn double(n: Int) -> Int { n + n }
```

---

## 9. Control flow: `if` and `match`

### `if`

`if` is an expression. The `else` branch is optional (without it the type is `Unit`); use
`else if` to chain.

```proviso
fn sign(x: Int) -> Int {
  if x > 0 { 1 } else if x < 0 { 0 - 1 } else { 0 }
}
```

**Occurrence typing.** A guard refines the value inside the branch. Guards like
`if len(xs) > 0` or `if i < len(xs)` record a fact the solver uses, so a guarded array
access is *proven* in range (no runtime check):

```proviso
fn first(xs: Array) -> Int {
  if len(xs) > 0 { xs[0] } else { 0 - 1 }   # xs[0] proven: 0 < len(xs)
}
```

### `match`

`match` deconstructs `enum` values. Patterns nest, and exhaustiveness is checked at the top
level (add arms or a `_` wildcard).

```proviso
match shape {
  Circle(r)   => 3 * r * r,
  Rect(w, h)  => w * h
}
```

Patterns are: `_` (wildcard), a lowercase binder (`x`), a literal (`0`, `true`, `"s"`), or
a constructor with sub-patterns (`Cons(h, Cons(h2, rest))`).

---

## 10. User-defined types: `enum`

```proviso
enum Shape {
  Circle(Int),
  Rect(Int, Int)
}
```

- Each variant is a **constructor** called like a function: `Circle(10)`, `Rect(4, 5)`.
- A single-variant enum is effectively a **record**: `enum Handle { Handle(Int) }`.
- Deconstruct with `match`. Non-exhaustive matches are reported as a dialogue.

```proviso
fn area(s: Shape) -> Int {
  match s {
    Circle(r)  => 3 * r * r,
    Rect(w, h) => w * h
  }
}
```

---

## 11. Effects

A function's effect row is written after `!`:

```proviso
fn fetch() -> Int ! {Net} { http_get(2) }
fn log_it(x: Int) -> Unit ! {IO} { print(x) }
```

Effect labels in Proviso: `IO` (printing), `Net` (network), `Exc` (exceptions), and any
operation name you `perform` (see §12).

- **Inference.** A function without a `!` row has its effects inferred from its body (a
  fixpoint, so mutual recursion works) and exported to callers.
- **Contract.** Writing `! {…}` (including the empty `! {}`) is an *enforced* contract: the
  inferred effects must be a subset of the declared ones, or you get an **`effect-leak`**
  diagnostic.
- **Refined effects.** An effect may carry a refinement on the operation's argument — e.g.
  `http_get`'s "at most 3 retries" is `Net{r | r <= 3}`, checked by the same solver.
- **Effect-variable polymorphism.** A *lowercase* effect-row name is a variable. A
  higher-order function can declare `! e` and have it instantiated at each call from the
  actual function argument's effects:

```proviso
fn apply(f: Fn(Int) -> Int ! e, x: Int) -> Int ! e { f(x) }
# apply(inc, …)   instantiates e := {}    (inc is pure)
# apply(shout, …) instantiates e := {IO}  (shout prints)
```

---

## 12. Algebraic effects and handlers

You can define your own effects with `perform` and handle them with `handle … with`.

```proviso
perform Op(arg)                      # invoke effect operation Op; its name is an effect label

handle <body> with {
  Op(x, k) => <clause>,              # x = the operation argument; k = the resumption
  return(v) => <clause>             # v = the body's normal result (the delimiter)
}
```

The resumption `k` is a **first-class, multi-shot continuation**: calling `k(rv)` resumes
the suspended computation at the `perform` site with value `rv`, and you may call it any
number of times — each run independent.

```proviso
fn main() -> Int ! {IO} {
  let total = handle {
    let a = perform Choose(0);
    let b = perform Choose(0);
    a + b
  } with {
    Choose(x, k) => k(0) + k(10),    # resume twice: enumerates {0,10} × {0,10}
    return(v) => v
  };
  print(total);                       # 40
  total
}
```

Handlers compose without restriction:

- **Nesting** — handlers nest; an inner handler's clause may `perform` an effect handled by
  an outer one.
- **Across function calls** — an effect performed inside a *called function* is caught by
  the caller's handler.
- **Escaping continuations** — a captured `k` is an ordinary value: a handler can return it,
  you can bind it, and you can invoke it later, *outside* the `handle` that produced it. The
  resumption has a precise `Fn(Int) -> answer` type, so calling it is a statically-typed call.

```proviso
fn main() -> Int ! {IO} {
  let resume = handle {
    let x = perform Pause(0);
    x + x
  } with { Pause(p, k) => k, return(v) => v };   # yield the continuation itself
  print(resume(10));                              # 20
  resume(21)                                      # 42  (multi-shot, after the handle returned)
}
```

A clause that does **not** resume `k` is *abortive*: its value becomes the handler's value
(this is how you model early exit). The `return(v)` clause is the delimiter applied to the
body's normal result and to each resumption's result.

---

## 13. Exceptions

`Exc` is a built-in effect with dedicated sugar. Raise with `throw(code)` and discharge with
`handle … catch (e) { … }`, where `e` binds the thrown code.

```proviso
fn checked_div(a: Int, b: Int) -> Int ! {Exc} {
  if b == 0 { throw(1) } else { a / b }
}

fn main() -> Int ! {IO} {
  let ok  = handle { checked_div(20, 4) } catch (e) { 0 - 1 };   # 5
  let bad = handle { checked_div(7, 0) }  catch (e) { 0 - 1 };   # -1 (caught)
  print(ok); print(bad);
  ok
}
```

Once handled, `Exc` no longer appears in the enclosing function's effect row.

---

## 14. Ownership: linear resources

Ownership is treated as an effect. A `linear` binding owns a resource; any plain use of it
performs a *Move* that consumes it. `borrow(x)` and `clone(x)` read without consuming.

```proviso
fn send(c: Int) -> Int ! {Net} { http_get(1) }

fn main() -> Int ! {Net} {
  let linear conn = http_get(0);
  send(conn);
  send(conn)        # error: use of `conn` after it was moved
}
```

Use-after-move is reported with the **path** (where it was moved → where you touched the
corpse) and the two standard ways out: **borrow** at the first site, or **clone** to get a
second owned value. The analysis follows straight-line code, `if`, and `match` (branches
are merged).

---

## 15. Typestate

Typestate carries a resource's *state* in its type. A `protocol` names the states a resource
moves through; an operation annotates the state it **requires** and **produces** with
`@State` on a carrier type.

```proviso
enum Handle { Handle(Int) }
protocol File { Closed, Open }

fn make()  -> Handle @ Closed            { Handle(0) }
fn open(f:  Handle @ Closed) -> Handle @ Open   { f }
fn read(f:  Handle @ Open)   -> Handle @ Open   { f }
fn close(f: Handle @ Open)   -> Handle @ Closed { f }

fn main() -> Int ! {IO} {
  let linear f = make();     # File @ Closed
  let linear f = open(f);    # Closed -> Open
  let linear f = read(f);    # Open   -> Open
  let linear f = close(f);   # Open   -> Closed
  print(7); 7
}
```

The checker tracks each binding's state — seeded from parameters and each operation's result
state, threaded through `let` re-binds, merged across `if`/`match` — and rejects a call made
in a provably-wrong state. The diagnostic names the required and current states, where the
resource entered its current state, and offers two choices: **ADVANCE** it to the required
state (via the transition operation), or **STAY** and use an operation valid in the current
state.

State is **gradual**: a binding whose state cannot be determined (an un-annotated parameter,
or one merged from disagreeing branches) is accepted silently. `@State` is **erased at
runtime** — operations are ordinary functions, so typestate programs still run. Pair it with
`linear` so the underlying value cannot be reused after a transition.

---

## 16. Gradual contracts: erasure and blame

This is where the central idea becomes operational. For every refinement obligation the
checker decides, per call site and per array index, whether it is:

- **proven** → the runtime check is **erased** (it costs nothing), or
- **gradual** → a runtime check is inserted, or
- **impossible** → a hard error (the program will not run).

So *the proof you buy statically is exactly the runtime cost you remove*. A program with no
annotations checks everything at runtime; adding refinements erases the checks you have
proven.

When a gradual check **fails** at runtime, the error carries a **blame** note naming the
unproven call site:

```
runtime contract failed: value 0 for `b` of `safe_div` violates {n | n != 0}
  [blame: the call at line 13 was not statically proven, so this contract is checked here]
```

If a program is run without the checker, the interpreter conservatively checks *everything*
(the sound fallback).

---

## 17. Reading a diagnostic

Every rejection has the same shape. Example (`examples/03_conflict.pvo`):

```
conflict[refine-conflict]: argument `x` of `sqrt_floor` cannot be proven
  at line 15
   |
   | let s = sqrt_floor(count);
   |

  required  Int{n | n > 0}
  known     Int{c | c >= 0}

  why  the source guarantees only (c >= 0); the target requires (n > 0).
       These do not agree on every value.
  counterexample  a value of 0 satisfies the source but breaks the requirement

  two ways forward -- your call:
  (A) LOOSEN the requirement
      edit: Int{n | n >= 0}   (or drop the refinement entirely to go gradual)
  (B) STRENGTHEN the source
      edit: if c > 0 { ... }   -- inside the guard the value is proven Int{n | n > 0}
```

How to read it:

- **required / known** — the constraint demanded vs. the one actually guaranteed.
- **why / counterexample** — a concrete value where the two disagree.
- **(A) LOOSEN** — weaken the strict side to accept what is known.
- **(B) STRENGTHEN** — reinforce the weak side so the requirement holds (often by adding a
  guard, after which occurrence typing proves it).

Diagnostic codes you may see: `refine-conflict`, `bounds`, `effect-leak`, `moved`,
`typestate`, `non-exhaustive`, `type`, `arity`, `unbound`. Gradual points are reported as
non-fatal `gradual[cast]` warnings.

---

## 18. Built-in functions

| Built-in | Signature | Effect | Notes |
|----------|-----------|--------|-------|
| `print(x)` | `Int -> Unit` (polymorphic over base types) | `IO` | prints `x` |
| `throw(code)` | `Int -> Unit` | `Exc` | raise; catch with `handle … catch` |
| `abs(x)` | `Int -> Int{r | r >= 0}` | — | absolute value (proven non-negative) |
| `len(a)` | `Array -> Int{r | r >= 0}` | — | array length (also a measure) |
| `to_str(n)` | `Int -> Str` | — | render an `Int` as a string |
| `http_get(r)` | `Int{r | 0 <= r <= 3} -> Int` | `Net{r | r <= 3}` | simulated request; the retry budget is a refinement |
| `borrow(x)` | `Int -> Int` | — | read a `linear` value without consuming it |
| `clone(x)` | `Int -> Int` | — | make a second owned copy of a `linear` value |

`print` is intentionally polymorphic: it accepts any base type (`Int`, `Bool`, `Str`, …).

---

## 19. Tooling: CLI, solver, editor

### CLI

```sh
proviso check <file.pvo>   # static analysis only (types, effects, ownership, typestate)
proviso run   <file.pvo>   # check, then run main (refuses to run on hard errors)
proviso lsp                # language server over stdio
```

(Invoke as `python -m proviso …` from the project folder.)

### Solver backends

The refinement engine is backend-agnostic. If the `z3-solver` package is importable, Proviso
uses **Z3** (sound and complete, well beyond the single-variable fragment); otherwise it
falls back to a **pure-Python sampler** that is complete for the single-variable comparison
fragment. Force the fallback with `PROVISO_SOLVER=sampler`. Dependent obligations
(refinements mentioning other names, `len`, or `abs`/`min`/`max`) require Z3; without it they
gracefully degrade to gradual runtime checks.

### Editor integration (LSP)

`proviso lsp` is a dependency-free Language Server speaking LSP over stdio (JSON-RPC with
`Content-Length` framing). It supports **incremental** document sync (range-based edits) and
publishes the *same* dialogue diagnostics the CLI renders (errors and gradual-point warnings),
answers `textDocument/hover` with the enclosing function's effect-inferred signature, and
supports **`textDocument/definition`** — go-to-definition for functions, type aliases, enums,
constructors, and protocols. Point any LSP-capable editor at the `proviso lsp` command for
`.pvo` files. A ready-to-use **Visual
Studio Code** client is in [`editors/vscode/`](editors/vscode/) — see its README for setup
(`pip install -e .`, then run the extension and open a `.pvo` file).

---

## 20. Bounded scope and known limitations

Proviso is a focused prototype. Deliberately out of scope in v1.0.0:

- Arrays are monomorphic (`Array` of `Int`); the only structural measure is `len`.
- An array's length is tracked statically only for **literals** and through **guards**;
  length passed through a function parameter is gradual.
- Function-argument subtyping is precise for refinements/effects but **single-level** for
  effect-variable instantiation; the name-based effect-inference pass does not substitute
  effect variables.
- **Typestate** is enforced both statically and, for the gradual cases the static pass
  cannot follow, at **runtime** (a `Resource` carries its state; a wrong-state operation
  raises). `@State` is erased from the type; the state rides on the value.
- Constructor-field runtime contracts are not enforced.
- `match` exhaustiveness recurses into nested patterns and reports a concrete missing case;
  redundant/unreachable arms are not yet flagged.
- Calls are on names only; you cannot immediately call a lambda literal or an indexed
  element — bind it first.
- The LSP supports incremental sync and go-to-definition; find-references and rename are not
  implemented.
- An escaped continuation has a precise `Fn(Int) -> answer` arrow; a continuation that escapes
  through a non-function join (a clause that returns `k` itself) still degrades to gradual.

These boundaries are where the prototype stops, not where the design does.

---

## 21. Cheat sheet

```proviso
# types & refinements
Int   Bool   Unit   Str   Array
Int{n | n > 0}                 Int{k | k >= 0 && k < len(xs)}
abs(n)   min(a, b)   max(a, b)   len(a)
type Nat = Int{n | n >= 0}

# functions & effects
fn f(x: Int) -> Int ! {IO} { ... }     # declared effect row
fn g(x: Int) -> Int { ... }            # effects inferred
fn h(f: Fn(Int) -> Int ! e, x: Int) -> Int ! e { f(x) }   # effect-polymorphic HOF
let lam = fn(x: Int) -> Int { x + 1 };  lam(41)

# bindings & control
let y = e;   let y: T = e;   let linear r = e;
if c { ... } else if c2 { ... } else { ... }
match v { Ctor(a, b) => ..., 0 => ..., _ => ... }

# data
enum Shape { Circle(Int), Rect(Int, Int) }      Circle(10)
[1, 2, 3]    a[i]    len(a)
"hi " + name    to_str(42)

# algebraic effects
perform Op(arg)
handle <body> with { Op(x, k) => k(rv), return(v) => v }
handle <body> catch (e) { ... }                  # exceptions

# ownership & typestate
let linear conn = open();   borrow(conn);   clone(conn)
protocol File { Closed, Open }
fn open(f: Handle @ Closed) -> Handle @ Open { f }
```

---

## Appendix A: grammar

Informal EBNF (lexical details omitted):

```
module     := decl*
decl       := fn_decl | type_alias | enum_decl | protocol_decl

fn_decl    := 'fn' IDENT '(' params? ')' ('->' type)? ('!' eff_row)? block
params     := param (',' param)*
param      := 'linear'? IDENT ':' type

type_alias   := 'type' IDENT '=' type ';'?
enum_decl    := 'enum' IDENT '{' variant (',' variant)* '}'
variant      := IDENT ('(' type (',' type)* ')')?
protocol_decl:= 'protocol' IDENT '{' IDENT (',' IDENT)* '}'

type       := fn_type | IDENT refinement? ('@' IDENT)?
fn_type    := 'Fn' '(' (type (',' type)*)? ')' '->' type ('!' eff_row)?
refinement := '{' IDENT '|' pred '}'
eff_row    := '{' (effect (',' effect)*)? '}' | effect
effect     := IDENT refinement?

block      := '{' stmt* expr? '}'
stmt       := 'let' 'linear'? IDENT (':' type)? '=' expr ';'  |  expr ';'

expr       := if | match | handle | logic
if         := 'if' expr block ('else' (if | block))?
match      := 'match' expr '{' arm (',' arm)* '}'
arm        := pattern '=>' expr
pattern    := '_' | IDENT | INT | 'true' | 'false' | STR
            | IDENT '(' (pattern (',' pattern)*)? ')'      # Upper-initial = constructor
handle     := 'handle' block 'with' '{' clause (',' clause)* '}'
            | 'handle' block 'catch' '(' IDENT ')' block
clause     := IDENT '(' IDENT ',' IDENT ')' '=>' expr      # operation clause
            | 'return' '(' IDENT ')' '=>' expr             # delimiter clause

logic      := and ('||' and)*
and        := cmp ('&&' cmp)*
cmp        := add (CMP add)?
add        := mul (('+'|'-') mul)*
mul        := unary (('*'|'/'|'%') unary)*
unary      := ('-'|'!') unary | postfix
postfix    := primary ( '(' args? ')' | '[' expr ']' )*
primary    := INT | STR | 'true' | 'false' | IDENT | perform | lambda
            | '(' expr ')' | '[' args? ']' | block
perform    := 'perform' IDENT '(' expr ')'
lambda     := 'fn' '(' params? ')' ('->' type)? block
```

---

## Appendix B: version history

**v1.0.0** — feature-complete prototype. All ten roadmap items are implemented:

1. nested / cross-handler algebraic effects + escaping continuations
2. precise function-argument subtyping (contravariant params, covariant result, effect
   subset)
3. arithmetic measures `abs` / `min` / `max`
4. static array-length tracking (literals + guards)
5. dependent refinements (other names + `len`), with len-guard occurrence typing (#5b)
6. user-defined types (`enum` + `match`, nested patterns, exhaustiveness)
7. arrays
8. contract erasure + blame
9. typestate (`protocol` + `@State`)
10. a stdio language server

Plus, beyond the original baseline: a Z3/sampler solver backend, type aliases, effect
inference, strings, first-class functions with multi-shot effect handlers, a trampolined
evaluator, **nested-pattern exhaustiveness** (Maranget's algorithm, with a concrete missing-
case witness), **runtime typestate enforcement** (a `Resource` carries its state through the
gradual regions the static pass can't follow), **LSP incremental sync and go-to-definition**,
and **precise (non-gradual) types for escaped continuations**. The test suite is 157
dependency-free tests.
