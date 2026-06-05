# Proviso

> A language whose type system you can **pay for gradually** — and that argues with
> you as a collaborator, not an adversary.

Proviso is a working prototype of the design you sketched: a small language built around
"correctness you can pay for in stages." It is a real lexer → parser → gradual dependent
type & effect checker → tree-walking interpreter, plus the part that matters most — a
diagnostics layer that turns every conflict into a **counterexample and two choices**.

This is a *prototype*, not the final research language. It deliberately implements a
decidable, runnable core of each idea so you can see the three axes interact today. See
[Scope & honesty](#scope--honesty) for exactly where the lines are.

```
proviso/
  predicate.py    the refinement solver (implication + counterexamples) — shared engine
  lexer.py        tokenizer
  nodes.py        AST
  parser.py       recursive-descent parser (incl. the refinement sub-grammar)
  types.py        semantic types, refinements (a pred, or ? for gradual), effect rows
  checker.py      gradual dependent type + effect checker  → emits Diagnostics
  ownership.py    ownership-as-effect (linear use) checker  → emits Diagnostics
  diagnostics.py  the dialogue: required/known/why/counterexample/two-choices
  interp.py       interpreter; refinements double as runtime contracts
  cli.py          `proviso check` / `proviso run`
examples/         one file per axis, including the showcase conflict
tests/run_tests.py
```

## Run it

```sh
python -m proviso run   examples/01_gradual.pvo      # prototype stage — just runs
python -m proviso run   examples/02_refine.pvo       # production stage — proven statically
python -m proviso check examples/03_conflict.pvo     # the showcase: a conflict as a dialogue
python -m proviso check examples/04_effects.pvo      # effect leak + dependent retry bound
python -m proviso check examples/05_ownership.pvo    # use-after-move, explained with a path
python -m proviso run   examples/06_gradual_cast.pvo # gradual seam: deferred runtime check
python tests/run_tests.py
```

## The one idea: a refinement is a guarantee you may or may not have paid for

A type carries a refinement that is **either a predicate or `?` (unknown)**:

| You write        | Meaning                          | Stage                       |
|------------------|----------------------------------|-----------------------------|
| `Int`            | `Int{v | ?}` — unknown           | prototype: no proof owed    |
| `Int{n | n > 0}` | a paid-for guarantee             | production: statically proven or rejected |

- `?` is **consistent with everything**: passing an unrefined `Int` where `{n | n > 0}`
  is required is *not* an error. The checker says `gradual[cast]` and inserts a **runtime
  check**. You move along the spectrum by adding annotations — never by rewriting.
- Two fully-refined types are checked by **implication** over linear integer arithmetic.
  If `P ⇒ Q` fails, the solver returns a concrete **counterexample**, and that is what
  every diagnostic is built around.

`examples/06` shows the seam: it type-checks with one gradual note, runs fine on `7`, and
the deferred check *fires* on `0` — the bill comes due exactly where you chose to defer it.

## The three axes

### 1. Gradual dependent types
The same call site is unchecked, statically proven, or statically rejected depending only
on how much you annotated. `examples/02` proves `magnitude` returns `Int{m | m >= 0}` by
flowing the refinement on `abs` through the body. `examples/01` is the same shapes with no
refinements at all — and it just runs.

### 2. Effects on the type — integrated with dependent types
A function's inferred effect row must be a subset of what it declares. Hiding `IO`
(`examples/04`) is an `effect-leak`. The integration with dependent types is concrete:
`http_get`'s "**at most 3 retries**" safety property is a *refinement on its argument*
(`retries: Int{r | r >= 0 && r <= 3}`) while `Net` rides in the effect row. Asking for 5
retries is caught by the **same solver** that catches `n > 0` violations — one engine,
not two.

### 3. Ownership, reinterpreted as an effect
A `linear` binding owns a resource; using it performs a `Move` effect that consumes it;
`borrow(x)`/`clone(x)` read without consuming. The rejection in `examples/05` does not just
say "value used after move" — it prints the **path** (`line 13 moves conn → line 14 touches
conn → no value remains`) and offers borrow-vs-clone.

## The thing you care about most: the compiler as a conversation partner

Every rejection — refinement conflict, effect leak, use-after-move — is the *same* shape:

```
conflict[refine-conflict]: argument `x` of `sqrt_floor` cannot be proven
  required  Int{n | n > 0}
  known     Int{c | c >= 0}
  why  the source guarantees only (c >= 0); the target requires (n > 0).
  counterexample  a value of 0 satisfies the source but breaks the requirement

  two ways forward -- your call:
  (A) LOOSEN the requirement
      edit: Int{n | n >= 0}   (or drop the refinement to go gradual)
  (B) STRENGTHEN the source
      edit: if c > 0 { ... }  -- inside the guard the value is proven Int{n | n > 0}
```

The type system is presented as a constraint *negotiation*: here are the two constraints,
here is the value where they disagree, and here are the two directions you can move —
weaken the strict side, or reinforce the weak side. These suggestions are **real**: applying
(B) (a `if count > 0` guard) makes `examples/03` type-check, because occurrence typing
refines the variable inside the branch. The dialogue closes the loop.

## Language reference (v0.1)

```
fn name(p1: T, linear p2: T, ...) -> T ! {Eff, Eff{r | pred}} { block }

T      := Int | Bool | Unit | Int{ v | pred }
pred   := comparison (n op k) combined with && || ! and parentheses, over the bound var
block  := { (let [linear] x [: T] = e;  |  e;)* eTrailing? }
e      := literal | x | e binop e | f(args) | if e {..} else {..}
        | handle {..} catch (x) {..}        # discharges the Exc effect
builtins: print (IO), throw (Exc), abs (pure, proven >=0),
          http_get (Net, retries<=3), borrow, clone
```

## Scope & honesty

What is **real** and runs: gradual refinements with runtime contracts; an implication
solver with counterexamples (**Z3-backed when `z3-solver` is installed**, with a pure-Python
fallback); interval-based refinement inference through `+ - *`; occurrence typing through
guards; **type aliases** (`type Nat = Int{n | n >= 0}`); an effect row with subset checking,
**effect inference for omitted rows**, and a dependent effect parameter; ownership as a
linear-use analysis; algebraic-style `handle/catch` for exceptions; the full dialogue
diagnostics.

What is deliberately **bounded** in this prototype (each is a known, non-trivial extension,
not an oversight):

- The refinement solver is **Z3 when available, else a sampler** that is complete for the
  single-variable comparison fragment but is not a general decision procedure. Either way,
  **surface refinements still range over a single bound variable** vs. integer constants;
  full Π-types / cross-argument dependency (`b: Int{m | m < a}`) and measure-referencing
  predicates (`len(xs)`) are the natural next step. The `implies`-returns-counterexample
  interface is backend-agnostic by design.
- **Effects** are labels with optional refinement parameters; first-class, fully resumable
  effect handlers (multi-shot continuations) are modeled only for `Exc` here.
- **Ownership** uses a simple "any use moves; borrow/clone read" model over straight-line
  code and `if`; no region/lifetime inference yet.
- Only `Int`/`Bool`/`Unit`; no user types, generics, or strings.

The point of the prototype is to make the *interactions* between the three axes — and the
diagnostic experience — concrete and runnable, so the design can be felt rather than
argued about.
