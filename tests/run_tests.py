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

print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
