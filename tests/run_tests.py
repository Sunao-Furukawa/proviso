"""Dependency-free test suite for Proviso.  Run:  python tests/run_tests.py"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import proviso
from proviso import predicate as P
from proviso.interp import Interpreter, ProvisoCastError

EX = os.path.join(ROOT, "examples")

_passed = 0
_failed = 0


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failed += 1
        print(f"  FAIL {name}  {detail}")


def analyze(path):
    src = open(path, encoding="utf-8-sig").read()
    module = proviso.parse(src)
    diags, warns = proviso.check(module, src)
    diags = diags + proviso.check_ownership(module, src)
    return module, diags, warns


def codes(diags):
    return sorted(d.code for d in diags)


# --- solver ---------------------------------------------------------------- #
print("solver:")
check("c>=0 does not imply n>0, cx=0",
      P.implies(P.PCmp(">=", 0), P.PCmp(">", 0)) == 0)
check("==4 implies !=0",
      P.implies(P.PCmp("==", 4), P.PCmp("!=", 0)) is None)
check("==5 violates [0,3], cx=5",
      P.implies(P.PCmp("==", 5),
                P.PAnd(P.PCmp(">=", 0), P.PCmp("<=", 3))) == 5)
check("n>0 implies n>=0",
      P.implies(P.PCmp(">", 0), P.PCmp(">=", 0)) is None)

# --- example diagnostics --------------------------------------------------- #
print("examples (static):")
_, d01, w01 = analyze(os.path.join(EX, "01_gradual.pvo"))
check("01 clean", d01 == [] and w01 == [], codes(d01))

_, d02, w02 = analyze(os.path.join(EX, "02_refine.pvo"))
check("02 clean (proven statically)", d02 == [] and w02 == [], codes(d02))

_, d03, _ = analyze(os.path.join(EX, "03_conflict.pvo"))
check("03 one refine-conflict", codes(d03) == ["refine-conflict"], codes(d03))
check("03 counterexample is 0",
      d03 and "0" in (d03[0].counterexample or ""), d03 and d03[0].counterexample)
check("03 offers two choices", d03 and len(d03[0].choices) == 2)

_, d04, _ = analyze(os.path.join(EX, "04_effects.pvo"))
check("04 effect-leak + refine-conflict",
      codes(d04) == ["effect-leak", "refine-conflict"], codes(d04))

_, d05, _ = analyze(os.path.join(EX, "05_ownership.pvo"))
check("05 use-after-move", codes(d05) == ["moved"], codes(d05))

_, d06, w06 = analyze(os.path.join(EX, "06_gradual_cast.pvo"))
check("06 no errors, one gradual point",
      d06 == [] and len(w06) == 1, (codes(d06), len(w06)))

# --- execution ------------------------------------------------------------- #
print("examples (runtime):")
def run(path, entry="main"):
    src = open(path, encoding="utf-8-sig").read()
    interp = Interpreter(proviso.parse(src))
    result = interp.run(entry)
    return result, interp.output

r01, o01 = run(os.path.join(EX, "01_gradual.pvo"))
check("01 runs -> 42 with output [42,30]", r01 == 42 and o01 == ["42", "30"], (r01, o01))

r02, o02 = run(os.path.join(EX, "02_refine.pvo"))
check("02 runs -> 5 with output [5,7]", r02 == 5 and o02 == ["5", "7"], (r02, o02))

r06, _ = run(os.path.join(EX, "06_gradual_cast.pvo"))
check("06 runs -> 7 (runtime check passes)", r06 == 7, r06)

# runtime cast fires on a bad value
bad = open(os.path.join(EX, "06_gradual_cast.pvo"), encoding="utf-8-sig").read()
bad = bad.replace("from_input(7)", "from_input(0)")
fired = False
try:
    Interpreter(proviso.parse(bad)).run("main")
except ProvisoCastError:
    fired = True
check("runtime refinement check fires on 0", fired)

SAMPLES = os.path.join(ROOT, "samples")


def analyze_src(src):
    module = proviso.parse(src)
    diags, warns = proviso.check(module, src)
    diags = diags + proviso.check_ownership(module, src)
    return diags, warns


def run_src(src, entry="main"):
    module = proviso.parse(src)
    proviso.check(module, src)  # annotate nodes so proven contracts are erased (#8)
    interp = Interpreter(module)
    return interp.run(entry), interp.output


def run_counting(src, entry="main"):
    """Run a program and report how many runtime contract checks actually executed."""
    module = proviso.parse(src)
    proviso.check(module, src)
    interp = Interpreter(module)
    interp.run(entry)
    return interp.checks_performed


# --- #1: SMT backend -------------------------------------------------------- #
print("#1 SMT backend:")
check("backend is z3 or sampler", P.solver_backend() in ("z3", "sampler"),
      P.solver_backend())
# sampler and (when present) z3 must agree on these implication decisions
_cases = [
    (P.PCmp(">=", 0), P.PCmp(">", 0), False),   # has counterexample
    (P.PCmp(">", 0), P.PCmp(">=", 0), True),    # holds
    (P.PAnd(P.PCmp(">=", 0), P.PCmp("<=", 3)), P.PCmp(">=", 0), True),
]
ok_parity = all((P._sampler_implies(a, b) is None) == holds for a, b, holds in _cases)
check("sampler decisions correct", ok_parity)
if P._HAVE_Z3:
    okz = all((P._z3_implies(a, b) is None) == holds for a, b, holds in _cases)
    check("z3 decisions agree with sampler", okz)
    check("z3 counterexample for c>=0 !=> n>0 is 0",
          P._z3_implies(P.PCmp(">=", 0), P.PCmp(">", 0)) == 0)

# --- #2: type aliases ------------------------------------------------------- #
print("#2 type aliases:")
d, w = analyze_src(open(os.path.join(SAMPLES, "alias.pvo"), encoding="utf-8-sig").read())
check("alias sample checks clean", d == [] and w == [], codes(d))
r, o = run_src(open(os.path.join(SAMPLES, "alias.pvo"), encoding="utf-8-sig").read())
check("alias sample runs -> 0 with [42,100]", r == 0 and o == ["42", "100"], (r, o))

_alias_conf = "type Pos = Int{n | n > 0}\nfn need(x: Pos) -> Int { x }\nfn main() -> Int { need(0) }\n"
d, _ = analyze_src(_alias_conf)
check("alias refinement still produces refine-conflict",
      codes(d) == ["refine-conflict"], codes(d))
check("alias conflict counterexample is 0",
      d and "0" in (d[0].counterexample or ""))

# aliased refinement is still a runtime contract (via a gradual source)
_alias_rt = ("type Pos = Int{n | n > 0}\n"
             "fn need(x: Pos) -> Int { x }\n"
             "fn thru(raw: Int) -> Int { need(raw) }\n"
             "fn main() -> Int { thru(0) }\n")
fired = False
try:
    run_src(_alias_rt)
except ProvisoCastError:
    fired = True
check("aliased refinement enforced at runtime", fired)

# --- #3: effect inference --------------------------------------------------- #
print("#3 effect inference:")
d, w = analyze_src(open(os.path.join(SAMPLES, "infer_effects.pvo"), encoding="utf-8-sig").read())
check("omitted effect row infers (no leak)", d == [], codes(d))
r, o = run_src(open(os.path.join(SAMPLES, "infer_effects.pvo"), encoding="utf-8-sig").read())
check("infer_effects runs -> 0", r == 0 and o[0] == "7", (r, o))

# inferred effects still propagate to a declared caller -> leak if unaccounted
_leak = ("fn audit(c: Int) { print(c); http_get(1); }\n"
         "fn main() -> Int ! {IO} { audit(0); 0 }\n")
d, _ = analyze_src(_leak)
check("inferred Net leaks through a caller declaring only {IO}",
      codes(d) == ["effect-leak"], codes(d))

# --- #5 + #7: dependent refinements & arrays ------------------------------- #
print("#5/#7 dependent refinements + arrays:")
_arr = open(os.path.join(SAMPLES, "arrays.pvo"), encoding="utf-8-sig").read()
d, w = analyze_src(_arr)
check("arrays sample: no hard errors", d == [], codes(d))
r, o = run_src(_arr)
check("arrays sample runs -> 30 with array printed",
      r == 30 and o[0] == "[10, 20, 30]" and o[1] == "3", (r, o))

_good = ("fn between(lo: Int, hi: Int{h | h >= lo}, x: Int{v | v >= lo && v <= hi}) -> Int { x }\n"
         "fn main() -> Int { between(1, 10, 5) }\n")
d, w = analyze_src(_good)
check("dependent precondition proven statically (between 1,10,5)",
      d == [] and w == [], (codes(d), len(w)))

d, _ = analyze_src(open(os.path.join(EX, "07_dependent.pvo"), encoding="utf-8-sig").read())
check("dependent violation -> refine-conflict(s)",
      "refine-conflict" in codes(d) and len(d) >= 1, codes(d))

_ga = "fn get_at(xs: Array, i: Int{k | k >= 0 && k < len(xs)}) -> Int { xs[i] }\n"
d, w = analyze_src(_ga)
check("in-bounds index proven inside get_at (no runtime check)",
      d == [] and w == [], (codes(d), len(w)))

d, _ = analyze_src("fn f(xs: Array) -> Int { xs[-1] }\n")
check("provably out-of-bounds -> hard bounds error", codes(d) == ["bounds"], codes(d))

# array reaches `g` through a parameter, so its length is gradual -> a runtime check
_oob = ("fn g(xs: Array, i: Int{k | k >= 0 && k < len(xs)}) -> Int { xs[i] }\n"
        "fn viaparam(xs: Array) -> Int { g(xs, 5) }\n"
        "fn main() -> Int { viaparam([1, 2]) }\n")
fired = False
try:
    run_src(_oob)
except ProvisoCastError:
    fired = True
check("runtime check catches an out-of-range index", fired)

# --- #6: user-defined types + pattern matching ----------------------------- #
print("#6 enums + match:")
_sh = open(os.path.join(SAMPLES, "shapes.pvo"), encoding="utf-8-sig").read()
d, w = analyze_src(_sh)
check("shapes sample checks clean (exhaustive)", d == [], codes(d))
r, o = run_src(_sh)
check("shapes runs -> 6 with output [300,20]",
      r == 6 and o == ["300", "20"], (r, o))

d, _ = analyze_src(open(os.path.join(EX, "08_match.pvo"), encoding="utf-8-sig").read())
check("missing arm -> non-exhaustive", codes(d) == ["non-exhaustive"], codes(d))

_wild = ("enum Shape { Circle(Int), Rect(Int, Int) }\n"
         "fn f(s: Shape) -> Int { match s { Circle(r) => r, _ => 0 } }\n"
         "fn main() -> Int { f(Rect(1, 2)) }\n")
d, _ = analyze_src(_wild)
check("wildcard arm makes match exhaustive", d == [], codes(d))
r, _ = run_src(_wild)
check("wildcard match runs -> 0", r == 0, r)

_mism = ("enum Shape { Circle(Int) }\nfn f(s: Shape) -> Int { 0 }\n"
         "fn main() -> Int { f(3) }\n")
d, _ = analyze_src(_mism)
check("passing Int where enum expected -> type error", codes(d) == ["type"], codes(d))

# --- #4: first-class functions + multi-shot effect handlers ----------------- #
print("#4 first-class functions + multi-shot handlers:")
_ms = open(os.path.join(SAMPLES, "multishot.pvo"), encoding="utf-8-sig").read()
d, w = analyze_src(_ms)
check("multishot sample checks clean (effect discharged)", d == [], codes(d))
r, o = run_src(_ms)
check("multi-shot: handler resumes twice -> 40", r == 40 and o == ["40"], (r, o))

# resuming twice over `x + 1`: k(0)+k(100) = 1 + 101 = 102
_ms2 = ("fn main() -> Int {\n"
        "  handle { perform Flip(0) + 1 } with { Flip(x, k) => k(0) + k(100), return(v) => v }\n"
        "}\n")
r, _ = run_src(_ms2)
check("multi-shot value: k(0)+k(100) over x+1 = 102", r == 102, r)

_hof = open(os.path.join(SAMPLES, "hof.pvo"), encoding="utf-8-sig").read()
d, w = analyze_src(_hof)
check("hof sample checks clean", d == [], codes(d))
r, o = run_src(_hof)
check("hof: twice(g,1) = 100, prints 300", r == 100 and o == ["300"], (r, o))

# single-shot resume (exception-like): handler ignores k, returns a default
_once = ("fn main() -> Int {\n"
         "  handle { let x = perform Fail(0); x + 1 } with { Fail(e, k) => 0 - 1, return(v) => v }\n"
         "}\n")
r, _ = run_src(_once)
check("handler may ignore k (0-resumption) -> -1", r == -1, r)

# a performed-but-undeclared/unhandled effect leaks (explicit `!` row)
d, _ = analyze_src(open(os.path.join(EX, "09_effect_leak.pvo"), encoding="utf-8-sig").read())
check("performed effect that escapes -> effect-leak", codes(d) == ["effect-leak"], codes(d))

# --- strings ---------------------------------------------------------------- #
print("strings:")
_str = open(os.path.join(SAMPLES, "strings.pvo"), encoding="utf-8-sig").read()
d, w = analyze_src(_str)
check("strings sample checks clean", d == [], codes(d))
r, o = run_src(_str)
check("strings run -> [Hello, Proviso!, answer = 42]",
      r == 0 and o == ["Hello, Proviso!", "answer = 42"], (r, o))
_eq = ('fn f(a: Str, b: Str) -> Bool { a == b }\n'
       'fn main() -> Int { if f("x", "x") { 1 } else { 0 } }\n')
r, _ = run_src(_eq)
check("string equality works", r == 1, r)

# --- nested patterns -------------------------------------------------------- #
print("nested patterns:")
_lst = open(os.path.join(SAMPLES, "list.pvo"), encoding="utf-8-sig").read()
d, w = analyze_src(_lst)
check("list sample checks clean (recursive enum)", d == [], codes(d))
r, o = run_src(_lst)
check("list: sum=60, second=20", r == 60 and o == ["60", "20"], (r, o))

_nest = ("enum L { Nil(), Cons(Int, L) }\n"
         "fn snd(xs: L) -> Int { match xs { Cons(a, Cons(b, r)) => b, _ => 0 - 1 } }\n"
         "fn main() -> Int { snd(Cons(1, Nil())) }\n")
r, _ = run_src(_nest)
check("nested pattern falls through to wildcard -> -1", r == -1, r)

_lit = ("enum W { Wrap(Int) }\n"
        "fn f(n: Int) -> Int { match Wrap(n) { Wrap(0) => 100, Wrap(x) => x } }\n"
        "fn main() -> Int { f(0) + f(7) }\n")
r, _ = run_src(_lit)
check("literal pattern: f(0)+f(7) = 107", r == 107, r)

# --- precise function types + effect-variable polymorphism ------------------ #
print("function types + effect polymorphism:")
_ep = open(os.path.join(SAMPLES, "effect_poly.pvo"), encoding="utf-8-sig").read()
d, w = analyze_src(_ep)
check("effect-poly sample checks clean", d == [], codes(d))
r, o = run_src(_ep)
check("effect-poly runs -> 42 (prints 42)", r == 42 and o == ["42"], (r, o))

# IO-performing arg instantiates e := {IO}; declaring `! {}` then leaks
_leak = ("fn apply(f: Fn(Int) -> Int ! e, x: Int) -> Int ! e { f(x) }\n"
         "fn shout(n: Int) -> Int ! {IO} { print(n); n }\n"
         "fn main() -> Int ! {} { apply(shout, 7) }\n")
d, _ = analyze_src(_leak)
check("effect var instantiated to {IO} -> leak under `! {}`",
      codes(d) == ["effect-leak"], codes(d))

# pure arg instantiates e := {}, so `! {}` is satisfied
_pure = ("fn apply(f: Fn(Int) -> Int ! e, x: Int) -> Int ! e { f(x) }\n"
         "fn inc(n: Int) -> Int { n + 1 }\n"
         "fn main() -> Int ! {} { apply(inc, 7) }\n")
d, _ = analyze_src(_pure)
check("effect var instantiated to {} -> no leak", d == [], codes(d))

# passing a non-function where a function is expected -> type error
_bad = ("fn apply(f: Fn(Int) -> Int ! e, x: Int) -> Int ! e { f(x) }\n"
        "fn main() -> Int { apply(5, 7) }\n")
d, _ = analyze_src(_bad)
check("non-function where Fn expected -> type error", "type" in codes(d), codes(d))

# --- trampolining (deep recursion) ------------------------------------------ #
print("trampolining:")
_deep = open(os.path.join(SAMPLES, "deep.pvo"), encoding="utf-8-sig").read()
d, w = analyze_src(_deep)
check("deep sample checks clean", d == [], codes(d))
r, o = run_src(_deep)
check("sumto(100000) = 5000050000 (no stack overflow)",
      r == 5000050000 and o == ["5000050000"], (r, o))

# tail-ish deep recursion well beyond the C stack limit
_cnt = ("fn count(n: Int) -> Int { if n == 0 { 0 } else { count(n - 1) } }\n"
        "fn main() -> Int { count(300000) }\n")
r, _ = run_src(_cnt)
check("count(300000) = 0 (trampolined)", r == 0, r)

# --- #5b: len-guard occurrence typing --------------------------------------- #
print("len-guard occurrence typing:")
_g = open(os.path.join(SAMPLES, "guard.pvo"), encoding="utf-8-sig").read()
d, w = analyze_src(_g)
check("guarded array access is PROVEN (no gradual points)",
      d == [] and w == [], (codes(d), len(w)))
r, o = run_src(_g)
check("guard sample runs -> -1 with [10, 30]",
      r == -1 and o == ["10", "30"], (r, o))

# without the guard, the same access is a runtime check (a gradual point)
_ung = ("fn head(xs: Array) -> Int { xs[0] }\nfn main() -> Int { head([1, 2]) }\n")
d, w = analyze_src(_ung)
check("unguarded index stays a gradual point", d == [] and len(w) == 1, (codes(d), len(w)))

# --- #4: static array-length tracking --------------------------------------- #
print("static array-length tracking:")
_al = open(os.path.join(SAMPLES, "array_len.pvo"), encoding="utf-8-sig").read()
d, w = analyze_src(_al)
check("known-length indexing is PROVEN (no gradual points)",
      d == [] and w == [], (codes(d), len(w)))
r, o = run_src(_al)
check("array_len runs -> 20 with [10, 30]", r == 20 and o == ["10", "30"], (r, o))

d, _ = analyze_src("fn main() -> Int { let a = [10, 20, 30]; a[5] }\n")
check("out-of-bounds literal index -> hard bounds error", codes(d) == ["bounds"], codes(d))

# a known-length array passed to a dependent get is also proven now
_kl = ("fn get_at(xs: Array, i: Int{k | k >= 0 && k < len(xs)}) -> Int { xs[i] }\n"
       "fn main() -> Int { let a = [1, 2, 3]; get_at(a, 2) }\n")
d, w = analyze_src(_kl)
check("dependent get on known-length array is proven", d == [] and w == [], (codes(d), len(w)))

# --- #8: contract erasure + blame ------------------------------------------- #
print("contract erasure + blame:")
# all contracts proven -> ZERO runtime checks performed (erased)
_proven = ("fn safe_div(a: Int, b: Int{n | n != 0}) -> Int { a / b }\n"
           "fn main() -> Int { safe_div(20, 4) + safe_div(30, 5) }\n")
check("proven contracts are erased (0 runtime checks)", run_counting(_proven) == 0,
      run_counting(_proven))

# a gradual argument is checked at runtime (count > 0)
_grad = ("fn safe_div(a: Int, b: Int{n | n != 0}) -> Int { a / b }\n"
         "fn thru(x: Int) -> Int { safe_div(100, x) }\n"
         "fn main() -> Int { thru(2) }\n")
check("gradual contract is checked at runtime (count == 1)", run_counting(_grad) == 1,
      run_counting(_grad))

# proven array index erased; the guard makes it free
_idx = ("fn main() -> Int { let a = [10, 20, 30]; a[0] + a[2] }\n")
check("proven array indexing is erased (0 checks)", run_counting(_idx) == 0,
      run_counting(_idx))

# the erasure sample runs, and only the one gradual call is checked
_er = open(os.path.join(SAMPLES, "erasure.pvo"), encoding="utf-8-sig").read()
d, _ = analyze_src(_er)
check("erasure sample checks clean", d == [], codes(d))
check("erasure sample: exactly 1 runtime check (the gradual call)",
      run_counting(_er) == 1, run_counting(_er))

# blame: a failing gradual check names the unproven call site
_blame = ("fn need(x: Int{n | n > 0}) -> Int { x }\n"
          "fn thru(y: Int) -> Int { need(y) }\n"
          "fn main() -> Int { thru(0) }\n")
msg = ""
try:
    run_src(_blame)
except ProvisoCastError as ex:
    msg = str(ex)
check("blame names the unproven call site", "blame" in msg and "line" in msg, msg)

# --- #3: arithmetic measures (abs / min / max) ------------------------------ #
print("arithmetic measures:")
# abs on the bound value: proven for an in-range literal, refuted for an out-of-range one
_a_ok = "fn s(d: Int{n | abs(n) <= 3}) -> Int { d }\nfn main() -> Int { s(-3) }\n"
d, w = analyze_src(_a_ok)
check("abs(-3) <= 3 is proven (erased)", d == [] and w == [], (codes(d), len(w)))
d, _ = analyze_src("fn s(d: Int{n | abs(n) <= 3}) -> Int { d }\n"
                   "fn main() -> Int { s(-5) }\n")
check("abs(-5) <= 3 -> hard refine-conflict", codes(d) == ["refine-conflict"], codes(d))

# min/max in a dependent refinement (mentions other params), order-independent
_mm = ("fn in_range(lo: Int, hi: Int, "
       "x: Int{v | v >= min(lo, hi) && v <= max(lo, hi)}) -> Int { x }\n")
d, w = analyze_src(_mm + "fn main() -> Int { in_range(8, 2, 5) }\n")
check("min/max range proven with reversed bounds", d == [] and w == [], (codes(d), len(w)))
d, _ = analyze_src(_mm + "fn main() -> Int { in_range(2, 8, 99) }\n")
check("out-of-[min,max] -> hard refine-conflict", codes(d) == ["refine-conflict"], codes(d))

# a measure obligation reached through a parameter is gradual -> a runtime check that blames
_mg = ("fn s(d: Int{n | abs(n) <= 3}) -> Int { d }\n"
       "fn thru(y: Int) -> Int { s(y) }\nfn main() -> Int { thru(2) }\n")
d, w = analyze_src(_mg)
check("abs through a parameter is a gradual point", d == [] and len(w) == 1, (codes(d), len(w)))
check("gradual abs check runs once and passes", run_counting(_mg) == 1, run_counting(_mg))
msg = ""
try:
    run_src("fn s(d: Int{n | abs(n) <= 3}) -> Int { d }\n"
            "fn thru(y: Int) -> Int { s(y) }\nfn main() -> Int { thru(9) }\n")
except ProvisoCastError as ex:
    msg = str(ex)
check("failing abs contract blames + names the measure",
      "blame" in msg and "abs(n) <= 3" in msg, msg)

# the measures sample type-checks clean and runs with zero runtime checks (all proven)
_ms = open(os.path.join(SAMPLES, "measures.pvo"), encoding="utf-8-sig").read()
d, w = analyze_src(_ms)
check("measures sample checks clean (no gradual points)", d == [] and w == [],
      (codes(d), len(w)))
r, o = run_src(_ms)
check("measures sample runs -> 2 with [5, 5, -3]", r == 2 and o == ["5", "5", "-3"], (r, o))
check("measures sample: 0 runtime checks (all erased)", run_counting(_ms) == 0,
      run_counting(_ms))

# malformed measure arity is a parse error
_bad = "fn s(d: Int{n | abs(n, n) <= 3}) -> Int { d }\nfn main() -> Int { s(0) }\n"
try:
    proviso.parse(_bad)
    check("wrong measure arity -> parse error", False, "no error")
except Exception as ex:
    check("wrong measure arity -> parse error", "abs" in str(ex), str(ex))

# --- #2: precise function-arg subtyping ------------------------------------- #
print("precise function-arg subtyping:")
_HO = "fn apply(f: Fn(Int{n|n>=0}) -> Int, x: Int{n|n>=0}) -> Int { f(x) }\n"
# contravariant params: a callee that accepts ANY Int is fine where >=0 is fed
d, _ = analyze_src(_HO + "fn g(n: Int) -> Int { n }\nfn main() -> Int { apply(g, 5) }\n")
check("contravariant param accepts a more permissive callee", d == [], codes(d))
# ...but a callee demanding more (n>0) than will be passed (n>=0) is rejected
d, _ = analyze_src(_HO + "fn pos(n: Int{m|m>0}) -> Int { n }\n"
                   "fn main() -> Int { apply(pos, 5) }\n")
check("contravariant param: stricter callee -> refine-conflict",
      codes(d) == ["refine-conflict"], codes(d))

_RC = "fn want_pos(f: Fn(Int) -> Int{r|r>0}, x: Int) -> Int { f(x) }\n"
# covariant result: a callee returning >=1 satisfies a promised >0
d, _ = analyze_src(_RC + "fn p(n: Int) -> Int{r|r>=1} { abs(n)+1 }\n"
                   "fn main() -> Int { want_pos(p, 5) }\n")
check("covariant result accepts a tighter return", d == [], codes(d))
# ...but a callee that may return 0 cannot satisfy a promised >0
d, _ = analyze_src(_RC + "fn q(n: Int) -> Int{r|r>=0} { abs(n) }\n"
                   "fn main() -> Int { want_pos(q, 5) }\n")
check("covariant result: looser return -> refine-conflict",
      codes(d) == ["refine-conflict"], codes(d))

# arity, base-type clash, and effect leak on a function argument
d, _ = analyze_src("fn ap(f: Fn(Int) -> Int, x: Int) -> Int { f(x) }\n"
                   "fn two(a: Int, b: Int) -> Int { a+b }\nfn main() -> Int { ap(two, 1) }\n")
check("function-arg arity mismatch -> arity error", codes(d) == ["arity"], codes(d))
d, _ = analyze_src("fn ap(f: Fn(Int) -> Int, x: Int) -> Int { f(x) }\n"
                   "fn nb(b: Bool) -> Int { 0 }\nfn main() -> Int { ap(nb, 1) }\n")
check("function-arg base-type clash -> type error", codes(d) == ["type"], codes(d))
d, _ = analyze_src("fn ap(f: Fn(Int) -> Int, x: Int) -> Int { f(x) }\n"
                   "fn sh(n: Int) -> Int ! {IO} { print(n); n }\n"
                   "fn main() -> Int ! {IO} { ap(sh, 1) }\n")
check("function-arg effect leak -> effect-leak", codes(d) == ["effect-leak"], codes(d))
# an effect *variable* in the expected row absorbs the argument's effects (no leak)
d, _ = analyze_src("fn ap(f: Fn(Int) -> Int ! e, x: Int) -> Int ! e { f(x) }\n"
                   "fn sh(n: Int) -> Int ! {IO} { print(n); n }\n"
                   "fn main() -> Int ! {IO} { ap(sh, 1) }\n")
check("effect-variable row absorbs an effectful callee", d == [], codes(d))

# the subtyping sample type-checks clean and runs
_fs = open(os.path.join(SAMPLES, "fn_subtype.pvo"), encoding="utf-8-sig").read()
d, w = analyze_src(_fs)
check("fn_subtype sample checks clean", d == [] and w == [], (codes(d), len(w)))
r, o = run_src(_fs)
check("fn_subtype sample runs -> 8 with [42, 6]", r == 8 and o == ["42", "6"], (r, o))

# --- #9: typestate ---------------------------------------------------------- #
print("typestate:")
def ts_codes(src):
    m = proviso.parse(src)
    return sorted(d.code for d in proviso.check_typestate(m, src))

_PROTO = ("enum H { H(Int) }\nprotocol File { Closed, Open }\n"
          "fn make() -> H @ Closed { H(0) }\n"
          "fn open(f: H @ Closed) -> H @ Open { f }\n"
          "fn read(f: H @ Open) -> H @ Open { f }\n"
          "fn close(f: H @ Open) -> H @ Closed { f }\n")
_ok = _PROTO + ("fn main() -> Int { let linear f = make(); let linear f = open(f); "
                "let linear f = read(f); let linear f = close(f); 0 }\n")
check("correct protocol sequence is clean", ts_codes(_ok) == [], ts_codes(_ok))

_rbo = _PROTO + ("fn main() -> Int { let linear f = make(); let linear f = read(f); 0 }\n")
check("read before open -> typestate error", ts_codes(_rbo) == ["typestate"], ts_codes(_rbo))

_dc = _PROTO + ("fn main() -> Int { let linear f = make(); let linear f = open(f); "
                "let linear f = close(f); let linear f = close(f); 0 }\n")
check("double close -> typestate error", ts_codes(_dc) == ["typestate"], ts_codes(_dc))

_ot = _PROTO + ("fn main() -> Int { let linear f = make(); let linear f = open(f); "
                "let linear f = open(f); 0 }\n")
check("open twice -> typestate error", ts_codes(_ot) == ["typestate"], ts_codes(_ot))

# an un-annotated (unknown-state) resource is gradual -> accepted silently
_grad = _PROTO + ("fn use_any(f: H) -> H { read(f) }\n"
                  "fn main() -> Int { 0 }\n")
check("unknown-state resource is gradual (no error)", ts_codes(_grad) == [], ts_codes(_grad))

# the diagnostic carries states, a trace, and the two ways forward
m = proviso.parse(_rbo)
dd = proviso.check_typestate(m, _rbo)
check("typestate diag: states + 2 choices + trace",
      dd and dd[0].expected == "File @ Open" and dd[0].found == "File @ Closed"
      and len(dd[0].choices) == 2 and "entered state" in (dd[0].counterexample or ""),
      dd and (dd[0].expected, dd[0].found, len(dd[0].choices)))

# the typestate sample checks clean (type + ownership + typestate) and runs
_tsf = open(os.path.join(SAMPLES, "typestate.pvo"), encoding="utf-8-sig").read()
d, w = analyze_src(_tsf)
check("typestate sample: type+ownership clean", d == [], codes(d))
check("typestate sample: no typestate violations", ts_codes(_tsf) == [], ts_codes(_tsf))
r, o = run_src(_tsf)
check("typestate sample runs -> 7", r == 7 and o == ["7"], (r, o))

# the showcase example has exactly one typestate violation
_ex12 = open(os.path.join(EX, "12_typestate.pvo"), encoding="utf-8-sig").read()
check("example 12 is one typestate violation", ts_codes(_ex12) == ["typestate"], ts_codes(_ex12))

# --- #10: language server (LSP) --------------------------------------------- #
print("language server:")
import io
from proviso import lsp

# a conflict surfaces as one Error (severity 1) diagnostic carrying the dialogue
_cf = "fn f(x: Int{n|n>0}) -> Int { x }\nfn main() -> Int { f(0) }\n"
ds = lsp.compute_diagnostics(_cf)
check("conflict -> one Error diagnostic", len(ds) == 1 and ds[0]["severity"] == 1
      and ds[0]["code"] == "refine-conflict", [(d["severity"], d["code"]) for d in ds])
check("diagnostic message carries the dialogue",
      "two ways forward" in ds[0]["message"] and "counterexample" in ds[0]["message"],
      ds[0]["message"][:40])
check("diagnostic range is on the failing line (0-based line 1)",
      ds[0]["range"]["start"]["line"] == 1, ds[0]["range"])

# a gradual point is a Warning (severity 2), not an error
_gp = ("fn f(x: Int{n|n>0}) -> Int { x }\nfn g(y: Int) -> Int { f(y) }\n"
       "fn main() -> Int { g(2) }\n")
check("gradual point -> Warning severity",
      [d["severity"] for d in lsp.compute_diagnostics(_gp)] == [2],
      lsp.compute_diagnostics(_gp))
# a clean program -> no diagnostics; a parse error -> exactly one
check("clean program -> no diagnostics", lsp.compute_diagnostics("fn main()->Int{0}\n") == [])
check("parse error -> one diagnostic", len(lsp.compute_diagnostics("fn f( {")) == 1)
# a typestate violation surfaces through the same channel
check("typestate violation surfaces in LSP diagnostics",
      any(d["code"] == "typestate" for d in lsp.compute_diagnostics(_rbo)),
      [d["code"] for d in lsp.compute_diagnostics(_rbo)])

# hover reports the enclosing function's (effect-inferred) signature
hv = lsp.hover_at("fn add(a: Int, b: Int) -> Int { a + b }\n"
                  "fn main() -> Int ! {IO} { print(add(1,2)); 0 }\n", 1, 4)
check("hover shows enclosing fn with inferred effects",
      hv is not None and "fn main" in hv["contents"]["value"] and "{IO}" in hv["contents"]["value"],
      hv)

# server dispatch: initialize advertises capabilities; didOpen publishes diagnostics
srv = lsp.LspServer()
init = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
caps = init[0]["result"]["capabilities"]
check("initialize advertises hover + full sync",
      caps["hoverProvider"] is True and caps["textDocumentSync"] == 1, caps)
opened = srv.handle({"jsonrpc": "2.0", "method": "textDocument/didOpen",
                     "params": {"textDocument": {"uri": "file:///t.pvo", "text": _cf}}})
check("didOpen publishes diagnostics",
      opened[0]["method"] == "textDocument/publishDiagnostics"
      and len(opened[0]["params"]["diagnostics"]) == 1, opened[0]["method"])
changed = srv.handle({"jsonrpc": "2.0", "method": "textDocument/didChange",
                      "params": {"textDocument": {"uri": "file:///t.pvo"},
                                 "contentChanges": [{"text": "fn main()->Int{0}\n"}]}})
check("didChange re-publishes (now clean)",
      changed[0]["params"]["diagnostics"] == [], changed[0]["params"]["diagnostics"])
srv.handle({"jsonrpc": "2.0", "method": "exit"})
check("exit stops the server loop", srv.running is False)

# JSON-RPC Content-Length framing round-trips
_buf = io.BytesIO()
lsp.write_message(_buf, {"jsonrpc": "2.0", "id": 9, "result": {"ok": True}})
_buf.seek(0)
check("framing round-trips", lsp.read_message(_buf) == {"jsonrpc": "2.0", "id": 9,
                                                        "result": {"ok": True}})

# --- #1: nested / cross-handler effects + escaping continuations ------------- #
print("nested cross-handler effects:")
# a continuation that ESCAPES its handler (yielded, bound, called later) type-checks
_esc = ("fn main() -> Int { let r = handle { let x = perform P(0); x + 1 } "
        "with { P(p, k) => k, return(v) => v }; r(5) }\n")
check("escaped continuation call type-checks", codes(analyze_src(_esc)[0]) == [],
      codes(analyze_src(_esc)[0]))
check("escaped continuation resumes (r(5) -> 6)", run_src(_esc)[0] == 6, run_src(_esc)[0])

# calling a genuinely unknown name is still a hard error
check("unknown name is still an error",
      codes(analyze_src("fn main() -> Int { nope(0) }\n")[0]) == ["unbound"],
      codes(analyze_src("fn main() -> Int { nope(0) }\n")[0]))

# nested, cross-handler, multi-shot, across a function-call boundary -- the example
_nest = open(os.path.join(EX, "13_continuations.pvo"), encoding="utf-8-sig").read()
check("nested cross-handler example type-checks", codes(analyze_src(_nest)[0]) == [],
      codes(analyze_src(_nest)[0]))
r, o = run_src(_nest)
check("nested cross-handler runs -> 114", r == 114 and o == ["114"], (r, o))

# multi-shot resumption that crosses a function-call boundary
_fb = ("fn worker(n: Int) -> Int { let r = perform Choose(0); n * r }\n"
       "fn main() -> Int { handle { worker(10) } "
       "with { Choose(x, k) => k(2) + k(3), return(v) => v } }\n")
check("multishot across a call boundary -> 50", run_src(_fb)[0] == 50, run_src(_fb)[0])

# the escaping-continuation sample type-checks and runs
_cs = open(os.path.join(SAMPLES, "continuations.pvo"), encoding="utf-8-sig").read()
check("continuations sample type-checks clean", codes(analyze_src(_cs)[0]) == [],
      codes(analyze_src(_cs)[0]))
r, o = run_src(_cs)
check("continuations sample runs -> 42 with [20]", r == 42 and o == ["20"], (r, o))

print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
