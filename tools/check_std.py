"""Check every std module as a program of its own; fail if any has a hard error.

    python tools/check_std.py        # summary
    python tools/check_std.py -v     # also list the library's internal gradual points
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from proviso import check, check_ownership, check_typestate  # noqa: E402
from proviso.loader import STD_DIR, load  # noqa: E402

bad = 0
for f in sorted(os.listdir(STD_DIR)):
    if not f.endswith(".pvo"):
        continue
    path = os.path.join(STD_DIR, f)
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    module, smap = load(src, path)
    diags, warns = check(module, smap)
    diags += check_ownership(module, smap) + check_typestate(module, smap)
    mine = [w for w in warns if smap.is_main(w.line)]
    print(f"{f}: {len(diags)} errors, {len(mine)} gradual points")
    for d in diags:
        fl, ln = smap.locate(d.line)
        print(f"   ERR {fl or f}:{ln} [{d.code}] {d.title}")
        bad += 1
    if "-v" in sys.argv:
        for w in mine:
            print(f"   gradual {w.line}: {w.message[:120]}")
sys.exit(1 if bad else 0)
