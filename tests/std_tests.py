"""Standard-library + import/linking tests. Executed by run_tests.py (shares its
`check`, `Interpreter`, `EX`, `SAMPLES` globals) so the suite stays one command."""
import io as _io
import os
import tempfile as _tempfile

from proviso.cli import _analyze as _cli_analyze
from proviso.interp import ProvisoExit
from proviso.loader import load as _load, LoadError, STD_DIR, std_modules
from proviso import lsp as _lsp
from proviso.stddoc import list_modules, module_doc

print("standard library:")


def std_analyze(src, path=None):
    return _cli_analyze(src, path)


def std_run(src, path=None, stdin=None):
    module, diags, _w = std_analyze(src, path)
    assert not diags, [d.title for d in diags]
    it = Interpreter(module)  # noqa: F821  (provided by run_tests.py)
    if stdin is not None:
        it.stdin = _io.StringIO(stdin)
    return it.run("main"), it.output


def _text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# every library module is a clean program on its own (no hard errors)
for _f in sorted(os.listdir(STD_DIR)):
    if _f.endswith(".pvo"):
        _p = os.path.join(STD_DIR, _f)
        _m, _ds, _ws = std_analyze(_text(_p), _p)
        check(f"std/{_f} checks clean", _ds == [],  # noqa: F821
              [(d.file, d.line, d.title) for d in _ds])
check("std module list", "std.string" in std_modules() and "std.io" in std_modules(),  # noqa: F821
      std_modules())

# the tour sample exercises string/ctype/math/array/list/option/errno/io/stdlib
_tp = os.path.join(SAMPLES, "stdlib_tour.pvo")  # noqa: F821
_r, _o = std_run(_text(_tp), _tp)
check("stdlib tour output", _o == [  # noqa: F821
    "alice / bob / carol", "BOB", "000042", "83", "2 + 3 = 5",
    "gcd=12 lcm=12 isqrt=31", "2^20=1048576 20!=2432902008176640000",
    "[-7, -3, 1, 4, 5, 8]", "[8, 5, 4, 1, -3, -7]", "[25, 64, 1, 16]", "8", "3",
    "Some(20)", "None", "[30, 20, 10]", "str_to_int failed: Invalid argument", "5"], _o)
check("library gradual points are not shown to the importer",  # noqa: F821
      std_analyze(_text(_tp), _tp)[2] == [])

_prog = r'''import std;
fn main() -> Int ! {IO} {
  print(str_find("hello world", "o w"));
  print(str_rfind("abcabc", "bc"));
  print(str_count("aaaa", "aa"));
  print(str_replace("1.2.3", ".", "::"));
  print(str_reverse(""));
  print(str_starts_with("proviso", "pro") && str_ends_with("proviso", "iso"));
  print(str_cmp("abc", "abd"));
  print(str_eq_nocase("HeLLo", "hello"));
  print(strlist_len(str_split("abc", "")));
  print(str_is_int("+12") && !str_is_int("-") && !str_is_int(""));
  print(is_alpha(ord("q")) && is_digit(ord("7")) && is_space(ord("\t")));
  print(chr(to_upper(ord("z"))));
  print(div_trunc(0 - 7, 2) * 10 + rem_trunc(0 - 7, 2));
  print(div_floor(0 - 7, 2));
  print(sign(0 - 9) + sign(0) + sign(4));
  print(digits(0 - 12345));
  print(is_prime(1) || is_prime(91));
  print(pow_mod(2, 100, 1000000007));
  print(arr_index_of([4, 5, 6], 6) + arr_count([1, 1, 2], 1));
  print(arr_max_or([], 0 - 1) + arr_min_or([3, 9, 2], 0));
  print(arr_eq(arr_range(0, 3), [0, 1, 2]) && arr_is_sorted(arr_sort([3, 1, 2])));
  print(arr_bsearch([1, 3, 5, 7], 4));
  print(arr_to_str(arr_set(arr_make(3, 0), 1, 9)));
  print(list_sum(list_from_array([1, 2, 3])) + list_len(Nil()));
  print(opt_unwrap_or(opt_map(Some(4), fn(x: Int) -> Int { x * x }), 0));
  print(res_unwrap_or(Err(22), 7) + res_error(Err(2)));
  print(strerror(ENOENT()));
  print(atoi("x") + atoi("12"));
  print(format("{}-{}-{}", [1, 2]));
  print(format_strs("{} and {}", SCons("tea", SCons("cake", SNil()))));
  0
}
'''
_r, _o = std_run(_prog)
check("std functions compute the right values", _o == [  # noqa: F821
    "4", "4", "2", "1::2::3", "", "true", "-1", "true", "3", "true", "true", "Z",
    "-31", "-4", "0", "5", "false", "976371285", "4", "1", "true", "-1", "[0, 9, 0]",
    "6", "16", "9", "No such file or directory", "12", "1-2-{}", "tea and cake"], _o)

# effect polymorphism flows through the library
_pure = ("import std.array;\nfn dbl(x: Int) -> Int { x * 2 }\n"
         "fn main() -> Int ! {} { arr_sum(arr_map([1, 2, 3], dbl)) }\n")
check("arr_map with a pure fn is pure (no effect leak)",  # noqa: F821
      std_analyze(_pure)[1] == [], [d.code for d in std_analyze(_pure)[1]])
_leaky = ("import std.array;\nfn loud(x: Int) -> Int ! {IO} { print(x); x }\n"
          "fn main() -> Int ! {} { arr_sum(arr_map([1, 2, 3], loud)) }\n")
check("arr_map with an IO fn leaks IO",  # noqa: F821
      "effect-leak" in [d.code for d in std_analyze(_leaky)[1]],
      [d.code for d in std_analyze(_leaky)[1]])
_exc = 'import std.string;\nfn main() -> Int ! {IO} { str_to_int("5") }\n'
check("str_to_int carries Exc (unhandled -> effect-leak)",  # noqa: F821
      [d.code for d in std_analyze(_exc)[1]] == ["effect-leak"],
      [d.code for d in std_analyze(_exc)[1]])

# library preconditions are checked at the caller's site
_ex16 = os.path.join(EX, "16_stdlib_contracts.pvo")  # noqa: F821
_m, _ds, _ws = std_analyze(_text(_ex16), _ex16)
check("16: three caller-side conflicts (pow, typestate, exit)",  # noqa: F821
      sorted(d.code for d in _ds) == ["refine-conflict", "refine-conflict", "typestate"]
      and all(d.file is None for d in _ds), [(d.code, d.file, d.line) for d in _ds])

# stdin
_rd = ('import std.io;\nfn main() -> Int ! {IO, Exc} {\n'
       '  let a = read_int(); let b = read_int(); puts(read_line_or("<eof>"));\n'
       '  let c = handle { read_line() } catch (e) { to_str(e) }; puts(c); a + b }\n')
_got = std_run(_rd, stdin="20\r\n22\n")
check("read_int / read_line / EOF -> ENODATA", _got == (42, ["<eof>", "61"]), _got)  # noqa: F821

_w = 'import std.io;\nfn main() -> Int ! {IO} { put("a"); printf("{}", [1]); print(2); 0 }\n'
check("put/printf join with the next line", std_run(_w)[1] == ["a12"], std_run(_w)[1])  # noqa: F821

_ex = "import std.stdlib;\nfn main() -> Int ! {Exit} { exit(3); 0 }\n"
try:
    std_run(_ex)
    _code = None
except ProvisoExit as _x:
    _code = _x.code
check("exit(3) unwinds with status 3", _code == 3, _code)  # noqa: F821

# files (run in a temp dir), via the typestate Writer
_fp = os.path.join(SAMPLES, "files.pvo")  # noqa: F821
_cwd = os.getcwd()
with _tempfile.TemporaryDirectory() as _td:
    os.chdir(_td)
    try:
        _r, _o = std_run(_text(_fp), _fp)
        _left = os.listdir(_td)
    finally:
        os.chdir(_cwd)
check("files sample: write/append/read/remove", (_r, _o) == (3, [  # noqa: F821
    "3 lines", "third line", "No such file or directory"]) and _left == [], (_r, _o, _left))

# --- loader / linking --------------------------------------------------------- #
print("imports / linking:")
with _tempfile.TemporaryDirectory() as _td:
    _a = os.path.join(_td, "a.pvo")
    _b = os.path.join(_td, "b.pvo")
    with open(_a, "w") as f:
        f.write('import "b.pvo";\nfn fa() -> Int { fb() + 1 }\n')
    with open(_b, "w") as f:
        f.write('import "a.pvo";\nimport std.math;\nfn fb() -> Int { max(1, 2) }\n')
    _main = 'import "a.pvo";\nimport "b.pvo";\nimport std.math;\nfn main() -> Int { fa() }\n'
    _mp = os.path.join(_td, "main.pvo")
    check("relative imports, cycles and repeats link once",  # noqa: F821
          std_run(_main, _mp)[0] == 3, std_run(_main, _mp))
    with open(_b, "w") as f:
        f.write('fn fb() -> Int { let x: Int{n | n > 0} = 0; x }\n')
    _m, _ds, _ws = std_analyze(_main, _mp)
    check("error inside an imported file names that file",  # noqa: F821
          [(d.file, d.line) for d in _ds] == [("b.pvo", 1)], [(d.file, d.line) for d in _ds])


def _load_err(src):
    try:
        _load(src)
    except LoadError as e:
        return e
    return None


_e = _load_err("import std.math;\nfn max(a: Int) -> Int { a }\nfn main() -> Int { 0 }\n")
check("duplicate definition is a link error naming both sites",  # noqa: F821
      _e is not None and "max" in _e.message and "std/math.pvo" in _e.message and _e.line == 2,
      _e and _e.message)
_e = _load_err("import std.nope;\n")
check("unknown std module -> import error listing modules",  # noqa: F821
      _e is not None and "std.string" in _e.message, _e and _e.message)
_e = _load_err("import foo.bar;\n")
check("non-std dotted import is rejected", _e is not None and _e.line == 1,  # noqa: F821
      _e and _e.message)
check("`import std;` links the whole library",  # noqa: F821
      len(_load("import std;\nfn main() -> Int { 0 }\n")[0].decls) > 150)
check("programs without imports are unchanged",  # noqa: F821
      len(_load("fn main() -> Int { 0 }\n")[1].segments) == 1)

# LSP sees through imports
_ls = 'import std.string;\nfn main() -> Int { str_len("abc") }\n'
check("lsp: imported names resolve (no diagnostics)",  # noqa: F821
      _lsp.compute_diagnostics(_ls) == [], _lsp.compute_diagnostics(_ls))
_loc = _lsp.definition_location(_ls, 1, 21)
check("lsp: go-to-definition jumps into std/string.pvo",  # noqa: F821
      _loc is not None and _loc[0].endswith("string.pvo")
      and _loc[1]["start"]["character"] == 3, _loc)
check("lsp: bad import -> one import diagnostic",  # noqa: F821
      [d["code"] for d in _lsp.compute_diagnostics("import std.zzz;\n")] == ["import"])

# proviso doc
check("doc lists modules", "std.ctype" in list_modules())  # noqa: F821
_doc = module_doc("std.io")
check("doc shows signatures + docs, hides private helpers",  # noqa: F821
      "fn read_file(path: Str) -> Str ! {FS, Exc}" in _doc and "_io_chomp" not in _doc
      and "getline" in _doc, _doc[:300])

# --- interval inference through `/` and `%` ----------------------------------- #
print("interval inference for / and %:")
from proviso import predicate as _P  # noqa: E402


def _iv(lo, hi):
    return _P.Interval(_P.NEG_INF if lo is None else lo, _P.POS_INF if hi is None else hi)


def _in(iv, v):
    return iv.lo <= v <= iv.hi


# brute-force soundness: every concrete x // y and x % y (Python floor semantics,
# which the interpreter uses) lies in the inferred interval
_bad = []
_spans = [(-9, -3), (-4, 5), (0, 7), (3, 12), (-6, -6), (0, 0)]
for (alo, ahi) in _spans + [(None, 4), (-3, None), (None, None)]:
    for (blo, bhi) in [(1, 1), (1, 4), (2, 5), (3, None), (1, None)]:
        dv = _P.interval_div(_iv(alo, ahi), _iv(blo, bhi))
        md = _P.interval_mod(_iv(alo, ahi), _iv(blo, bhi))
        xs = range(alo if alo is not None else -40, (ahi if ahi is not None else 40) + 1)
        ys = range(blo, (bhi if bhi is not None else 30) + 1)
        for x in xs:
            for y in ys:
                if not _in(dv, x // y) or not _in(md, x % y):
                    _bad.append(((alo, ahi), (blo, bhi), x, y))
check("interval_div / interval_mod are sound (brute force)", _bad == [], _bad[:5])  # noqa: F821
check("a divisor that may be <= 0 gives no information",  # noqa: F821
      _P.interval_div(_iv(0, 9), _iv(0, 3)).whole and _P.interval_mod(_iv(0, 9), _iv(-2, 3)).whole)
check("n / 2 with n >= 0 stays >= 0",  # noqa: F821
      _P.interval_div(_iv(0, None), _iv(2, 2)).lo == 0)
check("x % 10 lands in [0, 9]", (lambda r: (r.lo, r.hi))(  # noqa: F821
      _P.interval_mod(_iv(None, None), _iv(10, 10))) == (0, 9))

_halve = ("fn need(n: Int{k | k >= 0}) -> Int { n }\n"
          "fn f(n: Int{k | k >= 0}) -> Int { need(n / 2) + need(n % 3) }\n")
check("n / 2 and n % 3 discharge a `>= 0` precondition statically",  # noqa: F821
      std_analyze(_halve)[1:] == ([], []), std_analyze(_halve)[1:])
_neg = ("fn need(n: Int{k | k >= 0}) -> Int { n }\n"
        "fn f(n: Int{k | k >= 0}, d: Int) -> Int { need(n / d) }\n")
check("division by an unrefined Int stays a gradual point",  # noqa: F821
      len(std_analyze(_neg)[2]) == 1 and std_analyze(_neg)[1] == [], std_analyze(_neg)[1:])
_neg2 = ("fn need(n: Int{k | k >= 0}) -> Int { n }\n"
         "fn f() -> Int { need(0 - 7 / 2) }\n")
check("a provably negative quotient is still a hard conflict",  # noqa: F821
      [d.code for d in std_analyze(_neg2)[1]] == ["refine-conflict"],
      [d.code for d in std_analyze(_neg2)[1]])

# the rewritten helpers still compute the right answers
import math as _math  # noqa: E402
_sq = ("import std.math;\nfn go(n: Int{k | k >= 0}, hi: Int) -> Int ! {IO} {\n"
       "  print(isqrt(n)); if n < hi { go(n + 1, hi) } else { 0 } }\n"
       "fn main() -> Int ! {IO} { print(fib(0)); print(fib(1)); print(fib(90)); go(0, 300) }\n")
_r, _o = std_run(_sq)
check("isqrt(0..300) and fib match Python",  # noqa: F821
      _o[:3] == ["0", "1", "2880067194370816120"]
      and _o[3:] == [str(_math.isqrt(n)) for n in range(0, 301)], _o[:6])

if _P.solver_backend() == "z3":
    _gp = {}
    for _f in sorted(os.listdir(STD_DIR)):
        if _f.endswith(".pvo"):
            _p = os.path.join(STD_DIR, _f)
            _gp[_f] = len(std_analyze(_text(_p), _p)[2])
    check("with Z3, every std module is fully proven (0 runtime checks)",  # noqa: F821
          all(v == 0 for v in _gp.values()), {k: v for k, v in _gp.items() if v})
    _ix = ("fn f(a: Array, i: Int) -> Int {\n"
           "  if i >= 1 && i < len(a) { a[i - 1] + a[i] } else { 0 } }\n")
    check("a[i - 1] under `i >= 1 && i < len(a)` is proven",  # noqa: F821
          std_analyze(_ix)[1:] == ([], []), std_analyze(_ix)[1:])
    _ix2 = ("fn f(a: Array, i: Int) -> Int {\n"
            "  if i >= 0 && i < len(a) { a[i + 1] } else { 0 } }\n")
    check("a[i + 1] under `i < len(a)` may be in range -> gradual, not an error",  # noqa: F821
          std_analyze(_ix2)[1] == [] and len(std_analyze(_ix2)[2]) == 1, std_analyze(_ix2)[1:])
    _ix3 = "fn f(a: Array) -> Int { a[len(a)] }\n"
    check("a[len(a)] is a hard bounds error",  # noqa: F821
          [d.code for d in std_analyze(_ix3)[1]] == ["bounds"],
          [d.code for d in std_analyze(_ix3)[1]])
