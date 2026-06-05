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
    interp = Interpreter(proviso.parse(src))
    return interp.run(entry), interp.output


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

_oob = ("fn g(xs: Array, i: Int{k | k >= 0 && k < len(xs)}) -> Int { xs[i] }\n"
        "fn main() -> Int { let a = [1, 2]; g(a, 5) }\n")
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

print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
