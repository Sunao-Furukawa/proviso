# Proviso プログラミングマニュアル

**バージョン 1.0.0**

*(English version: [MANUAL.md](MANUAL.md))*

Proviso は、ただ一つの考え方を中心に設計された実験的プログラミング言語です——**正しさを段階
的に支払える(correctness you can pay for gradually)**。型のリファインメント(精緻化)は、
あなたが証明済みの*述語*か、あるいは `?`(*未知*)のいずれかであり、両者は共存します。`?` と
書いた箇所には言語が実行時チェックを挿入してプログラムを動かし、述語を書いた箇所では同じ呼び
出し位置が静的に*証明される*か*却下される*かのどちらかになります。**呼び出し位置ごとに、どれ
だけ証明を買うか**をあなたが決めます。

そして型検査が何かを却下するとき、単に「型エラー」とは言いません。**required(要求された制約)**
**known(実際に分かっている制約)**、なぜ両者が**矛盾するのか(why)**、具体的な**反例
(counterexample)**、そして**2つの選択肢(two choices)**——厳しい側を緩めるか、弱い側を強め
るか——を、それぞれ具体的な編集案つきで提示します。この対話的診断こそが設計の中心です。

このマニュアルは v1.0.0 言語のチュートリアル兼リファレンスです。

---

## 目次

1. [はじめに](#1-はじめに)
2. [言語ツアー](#2-言語ツアー)
3. [字句構造](#3-字句構造)
4. [型](#4-型)
5. [リファインメントとソルバ](#5-リファインメントとソルバ)
6. [式と演算子](#6-式と演算子)
7. [文・束縛・ブロック](#7-文束縛ブロック)
8. [関数](#8-関数)
9. [制御構文: `if` と `match`](#9-制御構文-if-と-match)
10. [ユーザー定義型: `enum`](#10-ユーザー定義型-enum)
11. [効果(エフェクト)](#11-効果エフェクト)
12. [代数的効果とハンドラ](#12-代数的効果とハンドラ)
13. [例外](#13-例外)
14. [所有権: 線形リソース](#14-所有権-線形リソース)
15. [タイプステート](#15-タイプステート)
16. [グラデュアル契約: 消去と blame](#16-グラデュアル契約-消去と-blame)
17. [診断の読み方](#17-診断の読み方)
18. [組み込み関数](#18-組み込み関数)
19. [ツール: CLI・ソルバ・エディタ](#19-ツール-cliソルバエディタ)
20. [スコープの境界と既知の制限](#20-スコープの境界と既知の制限)
21. [チートシート](#21-チートシート)
22. [付録A: 文法](#付録a-文法)
23. [付録B: バージョン履歴](#付録b-バージョン履歴)

---

## 1. はじめに

Proviso は純粋な Python で実装されています(必須の外部依存なし)。ソースファイルの拡張子は
`.pvo` です。プロジェクトフォルダから:

```sh
python -m proviso run   samples/factorial.pvo     # 型検査してから実行
python -m proviso check examples/03_conflict.pvo  # 型検査のみ。対話的診断を表示
python -m proviso lsp                             # stdio 上の言語サーバ
python tests/run_tests.py                         # テストスイート(128 テスト)
```

- **`run`** はプログラムを型検査し、ハードエラーがあれば*実行を拒否*します。グラデュアル点
  (`?`)はエラーではなく、実行時チェックになります。その後 `main` を呼び出します。
- **`check`** はすべての静的解析(型・効果・所有権・タイプステート)を実行して診断を表示します
  が、何も実行しません。
- `run` したいプログラムは `fn main() -> ... { ... }` を定義している必要があります。

オプションの SMT バックエンド(Z3)は、`z3-solver` がインポート可能なら自動的に使われます。
そうでなければ、単一変数の断片を判定する純粋 Python のサンプラにフォールバックします。
`PROVISO_SOLVER=sampler` でフォールバックを強制できます([§19](#19-ツール-cliソルバエディタ)参照)。

---

## 2. 言語ツアー

**プロトタイプの端。** すべてが素の `Int` で、何も証明されず、ただ動きます。

```proviso
fn inc(x: Int) -> Int { x + 1 }

fn main() -> Int ! {IO} {
  print(inc(41));   # 42
  inc(41)
}
```

**証明を買う。** リファインメントを加えると、その呼び出し位置が静的に検査されます。

```proviso
fn sqrt_floor(x: Int{n | n > 0}) -> Int{r | r >= 0} { abs(x) }
```

`sqrt_floor(5)` は*証明済み*(実行時コストなし)。`sqrt_floor(0)` は反例 `0` 付きの*ハードエラー*。
`sqrt_floor(素のInt)` は*グラデュアル点*で、実行時チェックが挿入され、失敗すれば blame がこの
呼び出しを指し示します。

**3つの軸を一度に。** リファインメント、署名上の効果、所有権はすべて*同じ*リファインメントソ
ルバと*同じ*対話的診断を通ります。このマニュアルの残りで一つずつ見ていきます。

---

## 3. 字句構造

- **コメント**は `#` で始まり行末まで。ブロックコメントはありません。
- **識別子**は `[A-Za-z_][A-Za-z0-9_]*`。慣習として、コンストラクタ名とプロトコルの状態名は
  `UpperCamel`、それ以外は `lower_snake`。`match` パターンでは、大文字始まりの名前は*コンスト
  ラクタ*、小文字始まりの名前は*束縛子(binder)*として扱われます。
- **整数リテラル**は十進数字、例 `42`。負のリテラルは `-42`(`42` への単項マイナス)と書きます。
- **文字列リテラル**はダブルクォートで、エスケープ `\n`・`\t`・`\"`・`\\` が使えます(例
  `"a\tb"`)。行をまたぐことはできません。
- **真偽値**: `true`、`false`。
- **キーワード**: `fn let linear if else true false handle catch return type enum match
  perform with protocol`。
- **演算子・区切り**: `-> => == != <= >= && || ( ) { } [ ] , ; : | ! + - * / % < > = . @`。

空白はトークンの区切りとしてのみ意味を持ちます。

---

## 4. 型

基本型は次のとおりです:

| 型          | 値                              | 備考 |
|-------------|---------------------------------|------|
| `Int`       | 整数                            | リファインメントを持つ(§5) |
| `Bool`      | `true`, `false`                 | |
| `Unit`      | `()`                            | `print` のような文の結果 |
| `Str`       | `"..."`                         | `+` で連結、`==` で比較 |
| `Array`     | `[1, 2, 3]`                     | `Int` の配列(v1 では単相) |
| `Fn`        | 関数 / 継続                     | *グラデュアルな*関数型 |
| `Fn(T, …) -> T ! {E}` | 関数                  | *精密な*関数型(§8) |
| *enum 名*   | コンストラクタ値                | ユーザー定義の直和型(§10) |

素の `Int` は**グラデュアル**です。そのリファインメントは `?`(未知)なので、あらゆる要求と整合
し、それに課されるあらゆる要求は実行時チェックへ先送りされます。

### 型エイリアス

`type Name = <型>` は(精緻化された)型に再利用可能な名前を付けます。エイリアスは型検査器と
インタプリタの両方で解決されるので、実行時契約も診断もエイリアスを通り抜けます。

```proviso
type Nat = Int{n | n >= 0}
type Percent = Int{p | p >= 0 && p <= 100}

fn clamp_high(x: Nat) -> Percent {
  if x <= 100 { x } else { 100 }
}
```

---

## 5. リファインメントとソルバ

リファインメントは、あなたが名付ける暗黙の*値変数*についての述語で基本型を制約します:

```
Int{n | n > 0}              # 正の整数
Int{p | p >= 0 && p <= 100} # 0..100
Int{k | k != 0}             # 非ゼロ
```

### 述語の文法

述語は、**項(term)**の間の**関係(relation)**のブール結合です:

- ブール: `&&`、`||`、`!(...)`、リテラル `true` / `false`;
- 関係: `<  <=  >  >=  ==  !=`;
- 項: 整数リテラル、値変数、他のスコープ内の名前(下記の*依存*を参照)、算術 `+ - *`、および
  下記の**measure(測度)**。

### Measure(測度)

*measure* はリファインメント内で使える整数値の関数です:

- `len(a)` — 配列 `a` の長さ(構造的 measure。`>= 0` が証明される)。
- `abs(t)` — 絶対値。
- `min(a, b)`、`max(a, b)` — 最小 / 最大。

```proviso
fn small_step(d: Int{n | abs(n) <= 3}) -> Int { d }
fn in_range(lo: Int, hi: Int,
            x: Int{v | v >= min(lo, hi) && v <= max(lo, hi)}) -> Int { x }
```

`abs`・`min`・`max` は線形整数算術に収まるので、それらを含む義務は他のリファインメントと同様に
*正確に*証明・反証されます。

### 依存リファインメント

リファインメントは**他のパラメータ**や `len` を参照できるので、ある引数の型が別の引数に依存
できます:

```proviso
fn between(lo: Int, hi: Int{h | h >= lo},
           x: Int{v | v >= lo && v <= hi}) -> Int { x }
```

各呼び出し位置で仮引数名が実引数で置換され、義務が解消されます: **証明済み** → 実行時チェック
なし、**証明不能(矛盾)** → 反例付きハードエラー、**それ以外** → 実行時チェックへ先送り。

### リファインメント義務がどう判定されるか

型 `S` の値が `T` の要求される場所で使われるたびに:

1. `T` のリファインメントが `?` → 受理(未知は何でも満たす)。
2. `S` のリファインメントが `?` → **グラデュアル点**: 実行時チェックを挿入。
3. 両方が述語 → ソルバが `S ⟹ T` を検査:
   - 成立 → **証明済み**。チェックは*消去*される(実行時コストなし);
   - 不成立 → **ハードエラー**。`S(x)` だが `T(x)` でない反例 `x` を提示。

同じエンジンが、対話を駆動する反例を生成します(§17)。

---

## 6. 式と演算子

Proviso は式指向です: `if`、`match`、`handle`、ブロックはすべて値を生みます。

演算子(**低い**優先順位から**高い**優先順位へ):

| 優先順位 | 演算子                | 結合性 | 備考 |
|---------:|-----------------------|--------|------|
| 1 | `\|\|`                       | 左    | 短絡 |
| 2 | `&&`                       | 左    | 短絡 |
| 3 | `== != < <= > >=`          | 左    | 比較 |
| 4 | `+ -`                      | 左    | `+` は Int 加算**または** `Str` 連結 |
| 5 | `* / %`                    | 左    | `/` は床除算。`/ %` の `0` 除算は実行時エラー |
| 6 | 単項 `-`, `!`             | 前置  | 符号反転、論理否定 |
| 7 | 呼び出し `f(...)`、添字 `a[i]` | 後置  | |

注意:

- **呼び出しは名前に対してのみ。** `f(x)` は `f` が識別子(トップレベル関数、コンストラクタ、
  組み込み、または関数/継続を保持する束縛)であることを要求します。ラムダを呼ぶにはまず束縛
  します: `let g = fn(x: Int) -> Int { x }; g(3)`。
- **添字** `a[i]` は義務 `0 <= i < len(a)` を伴います(§5・§16)。
- **ループはありません**。反復は再帰で行います(評価器はトランポリン化されているので、深い再帰
  でもスタックを溢れさせません)。

---

## 7. 文・束縛・ブロック

**ブロック** `{ ... }` は文の列で、末尾に*結果式*(末尾 `;` なし)を置けます。結果がブロックの
値になります。結果式がなければブロックの型は `Unit` です。

```proviso
fn f(x: Int) -> Int {
  let y = x + 1;     # 文(; に注意)
  print(y);          # 文
  y * 2              # 結果式(; なし)-> ブロックの値
}
```

**束縛**は名前を導入します:

```proviso
let x = expr;            # expr から型を推論
let x: Int{n|n>0} = e;   # 注釈(値は注釈に対して検査される)
let linear conn = open();# 線形(所有)リソース — §14 参照
```

同名の後続 `let` は先行の束縛を**シャドウ**します。

---

## 8. 関数

```proviso
fn name(p1: T1, p2: T2) -> Ret ! {Effects} {
  body
}
```

- 戻り型(`-> Ret`)と効果行(`! {Effects}`)は**省略可能**です。
- `-> Ret` を省略すると結果は `Unit`。
- `! {…}` を省略すると効果行は本体から**推論**されます(§11)。
- パラメータはリファインメント付き・`linear` 付きにできます(§14)。

### 一級関数と精密な型

関数は値です。値として使われた素の関数名は参照、無名関数はラムダです:

```proviso
fn twice(f: Fn, x: Int) -> Int { f(f(x)) }   # グラデュアルな関数パラメータ

fn main() -> Int ! {IO} {
  let g = fn(n: Int) -> Int { n * 10 };       # ラムダ値
  print(twice(g, 3));                          # 300
  twice(g, 1)
}
```

**精密な**関数型 `Fn(T, …) -> T ! {E}` は引数・結果・効果を制約します。精密な型が要求される
場所に渡された関数は**部分型(subtyping)**で検査されます:

- パラメータは**反変(contravariant)** — 関数は呼び出し側が渡すものすべてを少なくとも受理せねば
  ならない;
- 結果は**共変(covariant)** — 関数は呼び出し側が期待するもの以下を返さねばならない;
- 関数の効果は期待される行の**部分集合**でなければならない(効果*変数*は任意の効果を吸収する —
  §11)。

いずれの位置でもリファインメントの矛盾は通常の反例付き対話で報告されます。どちらかの側が
グラデュアル(`?`、素の `Fn` 値)なら黙って受理されます。

```proviso
# `run_nonneg` は呼び出す関数に非負の Int しか渡さないので、
# 任意の Int を受理する関数を渡してよい(パラメータ位置は反変):
fn run_nonneg(f: Fn(Int{n | n >= 0}) -> Int, x: Int{n | n >= 0}) -> Int { f(x) }
fn double(n: Int) -> Int { n + n }
```

---

## 9. 制御構文: `if` と `match`

### `if`

`if` は式です。`else` 節は省略可能(省略時は型 `Unit`)。`else if` で連結します。

```proviso
fn sign(x: Int) -> Int {
  if x > 0 { 1 } else if x < 0 { 0 - 1 } else { 0 }
}
```

**オカレンス型付け(occurrence typing)。** ガードはその分岐内で値を精緻化します。`if len(xs) > 0`
や `if i < len(xs)` のようなガードはソルバが使う事実を記録するので、ガードされた配列アクセスは
範囲内が*証明*されます(実行時チェックなし):

```proviso
fn first(xs: Array) -> Int {
  if len(xs) > 0 { xs[0] } else { 0 - 1 }   # xs[0] は証明済み: 0 < len(xs)
}
```

### `match`

`match` は `enum` 値を分解します。パターンはネスト可能で、網羅性は**ネストしたパターンの内側
まで**検査されます(Maranget のアルゴリズム)。漏れがあれば `Cons(_, Cons(_, _))` のような
具体的な未カバー例を提示します(アームを増やすか `_` ワイルドカードを追加)。

```proviso
match shape {
  Circle(r)   => 3 * r * r,
  Rect(w, h)  => w * h
}
```

パターンは: `_`(ワイルドカード)、小文字の束縛子(`x`)、リテラル(`0`・`true`・`"s"`)、または
サブパターン付きコンストラクタ(`Cons(h, Cons(h2, rest))`)です。

---

## 10. ユーザー定義型: `enum`

```proviso
enum Shape {
  Circle(Int),
  Rect(Int, Int)
}
```

- 各バリアントは**コンストラクタ**で、関数のように呼びます: `Circle(10)`、`Rect(4, 5)`。
- 単一バリアントの enum は実質**レコード**です: `enum Handle { Handle(Int) }`。
- `match` で分解します。網羅でない match は対話として報告されます。

```proviso
fn area(s: Shape) -> Int {
  match s {
    Circle(r)  => 3 * r * r,
    Rect(w, h) => w * h
  }
}
```

---

## 11. 効果(エフェクト)

関数の効果行は `!` の後に書きます:

```proviso
fn fetch() -> Int ! {Net} { http_get(2) }
fn log_it(x: Int) -> Unit ! {IO} { print(x) }
```

Proviso の効果ラベル: `IO`(出力)、`Net`(ネットワーク)、`Exc`(例外)、および `perform` する
任意の演算名(§12)。

- **推論。** `!` 行のない関数は本体から効果が推論され(不動点なので相互再帰も扱える)、呼び出し
  側へ公開されます。
- **契約。** `! {…}`(空の `! {}` を含む)を書くと*強制される*契約になります。推論された効果は
  宣言された効果の部分集合でなければならず、さもなくば **`effect-leak`** 診断が出ます。
- **精緻化された効果。** 効果は演算の引数についてのリファインメントを持てます——例えば
  `http_get` の「リトライは最大3回」は `Net{r | r <= 3}` で、同じソルバが検査します。
- **効果変数多相。** *小文字*の効果行名は変数です。高階関数は `! e` を宣言し、各呼び出しで実際
  の関数引数の効果から具体化できます:

```proviso
fn apply(f: Fn(Int) -> Int ! e, x: Int) -> Int ! e { f(x) }
# apply(inc, …)   は e := {}    に具体化(inc は純粋)
# apply(shout, …) は e := {IO}  に具体化(shout は出力する)
```

---

## 12. 代数的効果とハンドラ

`perform` で独自の効果を発生させ、`handle … with` でハンドルできます。

```proviso
perform Op(arg)                      # 効果演算 Op を発生。その名前は効果ラベルになる

handle <body> with {
  Op(x, k) => <節>,                  # x = 演算引数。k = 再開(resumption)
  return(v) => <節>                  # v = 本体の通常結果(デリミタ)
}
```

再開 `k` は**一級・マルチショットの継続**です。`k(rv)` を呼ぶと `perform` の地点から値 `rv` で
中断された計算を再開し、何回でも呼べます——各実行は独立です。

```proviso
fn main() -> Int ! {IO} {
  let total = handle {
    let a = perform Choose(0);
    let b = perform Choose(0);
    a + b
  } with {
    Choose(x, k) => k(0) + k(10),    # 二度再開: {0,10} × {0,10} を列挙
    return(v) => v
  };
  print(total);                       # 40
  total
}
```

ハンドラは制限なく合成できます:

- **ネスト** — ハンドラは入れ子にでき、内側のハンドラの節が外側で扱われる効果を `perform`
  できる。
- **関数呼び出しを越える** — 呼ばれた関数の中で発生した効果は、呼び出し側のハンドラで捕捉
  される。
- **エスケープする継続** — 捕捉された `k` は普通の値です。ハンドラがそれを返し、束縛し、後で
  ——それを生んだ `handle` の*外側*で——呼び出せます。この再開は精密な `Fn(Int) -> answer` 型
  を持つので、その呼び出しは(グラデュアルではなく)静的に型付けされた呼び出しです。

```proviso
fn main() -> Int ! {IO} {
  let resume = handle {
    let x = perform Pause(0);
    x + x
  } with { Pause(p, k) => k, return(v) => v };   # 継続そのものを返す
  print(resume(10));                              # 20
  resume(21)                                      # 42(マルチショット、handle が返った後)
}
```

`k` を再開**しない**節は*中断的(abortive)*です。その値がハンドラの値になります(早期脱出の
モデル化)。`return(v)` 節は、本体の通常結果と各再開の結果に適用されるデリミタです。

---

## 13. 例外

`Exc` は専用糖衣のある組み込み効果です。`throw(code)` で送出し、`handle … catch (e) { … }` で
解消します(`e` が送出されたコードを束縛)。

```proviso
fn checked_div(a: Int, b: Int) -> Int ! {Exc} {
  if b == 0 { throw(1) } else { a / b }
}

fn main() -> Int ! {IO} {
  let ok  = handle { checked_div(20, 4) } catch (e) { 0 - 1 };   # 5
  let bad = handle { checked_div(7, 0) }  catch (e) { 0 - 1 };   # -1(捕捉)
  print(ok); print(bad);
  ok
}
```

一度ハンドルされると、`Exc` は囲む関数の効果行から消えます。

---

## 14. 所有権: 線形リソース

所有権は効果として扱われます。`linear` 束縛はリソースを所有し、それを素に使うと*Move*(消費)
が発生します。`borrow(x)` と `clone(x)` は消費せずに読みます。

```proviso
fn send(c: Int) -> Int ! {Net} { http_get(1) }

fn main() -> Int ! {Net} {
  let linear conn = http_get(0);
  send(conn);
  send(conn)        # エラー: move された後の `conn` の使用
}
```

move 後使用は**経路**(どこで move されたか → どこで亡骸に触れたか)つきで報告され、標準的な
2つの逃げ道——最初の地点で **borrow** する、または **clone** して2つ目の所有値を得る——を提示
します。解析は直線コード・`if`・`match` を追います(分岐はマージされます)。

---

## 15. タイプステート

タイプステートはリソースの*状態*を型に乗せます。`protocol` はリソースが遷移する状態を名付け、
演算は**要求する**状態と**生成する**状態を運搬型上の `@State` で注釈します。

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

型検査器は各束縛の状態を追跡し——パラメータと各演算の結果状態で初期化、`let` 再束縛で伝播、
`if`/`match` でマージ——証明可能に誤った状態でなされた呼び出しを却下します。診断は要求状態と
現在状態、リソースが現在状態に入った位置を示し、2つの選択肢を提示します: **ADVANCE**(遷移
演算で要求状態へ進める)、または **STAY**(現在状態で有効な演算を使う)。

状態は静的には**グラデュアル**です。状態が確定できない束縛(注釈のないパラメータ、または分岐で
食い違ってマージされたもの)は静的には黙って受理されます。ただし静的パスが追えないグラデュアルな
ケースについては**実行時に強制**されます。実行時には各プロトコル値は自分の状態を運ぶ `Resource`
として表現され、誤った状態で演算を呼ぶと例外(`ProvisoStateError`)になります。`@State` は*型*から
は消去され、状態は*値*に乗ります。遷移後に元の値を再利用できないよう、`linear` と組み合わせて
ください。`examples/15_typestate_runtime.pvo` を参照。

---

## 16. グラデュアル契約: 消去と blame

ここで中心の考え方が実体化します。すべてのリファインメント義務について、型検査器は呼び出し位置
ごと・配列添字ごとに、それが次のどれかを判定します:

- **証明済み** → 実行時チェックは**消去**される(コストゼロ)、または
- **グラデュアル** → 実行時チェックが挿入される、または
- **不可能** → ハードエラー(プログラムは実行されない)。

つまり*静的に買った証明が、ちょうど取り除かれる実行時コスト*です。注釈のないプログラムは
すべてを実行時に検査し、リファインメントを足すほど証明できたチェックが消えていきます。

グラデュアルなチェックが実行時に**失敗**すると、エラーは証明されなかった呼び出し位置を指す
**blame** 注記を伴います:

```
runtime contract failed: value 0 for `b` of `safe_div` violates {n | n != 0}
  [blame: the call at line 13 was not statically proven, so this contract is checked here]
```

型検査器なしでプログラムを実行した場合、インタプリタは保守的に*すべて*を検査します(健全な
フォールバック)。

---

## 17. 診断の読み方

すべての却下は同じ形をしています。例(`examples/03_conflict.pvo`):

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

読み方:

- **required / known** — 要求された制約 vs 実際に保証されている制約。
- **why / counterexample** — 両者が食い違う具体的な値。
- **(A) LOOSEN** — 厳しい側を緩めて、分かっていることを受理する。
- **(B) STRENGTHEN** — 弱い側を強めて要求を満たす(多くはガード追加。その後オカレンス型付けが
  証明する)。

現れる診断コード: `refine-conflict`、`bounds`、`effect-leak`、`moved`、`typestate`、
`non-exhaustive`、`type`、`arity`、`unbound`。グラデュアル点は非致命的な `gradual[cast]` 警告
として報告されます。

---

## 18. 組み込み関数

| 組み込み | シグネチャ | 効果 | 備考 |
|----------|-----------|------|------|
| `print(x)` | `Int -> Unit`(基本型に多相) | `IO` | `x` を出力 |
| `throw(code)` | `Int -> Unit` | `Exc` | 送出。`handle … catch` で捕捉 |
| `abs(x)` | `Int -> Int{r | r >= 0}` | — | 絶対値(非負が証明される) |
| `len(a)` | `Array -> Int{r | r >= 0}` | — | 配列長(measure でもある) |
| `to_str(n)` | `Int -> Str` | — | `Int` を文字列に変換 |
| `http_get(r)` | `Int{r | 0 <= r <= 3} -> Int` | `Net{r | r <= 3}` | 模擬リクエスト。リトライ予算はリファインメント |
| `borrow(x)` | `Int -> Int` | — | `linear` 値を消費せず読む |
| `clone(x)` | `Int -> Int` | — | `linear` 値の2つ目の所有コピーを作る |

`print` は意図的に多相で、任意の基本型(`Int`・`Bool`・`Str` など)を受理します。

---

## 19. ツール: CLI・ソルバ・エディタ

### CLI

```sh
proviso check <file.pvo>   # 静的解析のみ(型・効果・所有権・タイプステート)
proviso run   <file.pvo>   # 検査してから main を実行(ハードエラーなら実行拒否)
proviso lsp                # stdio 上の言語サーバ
```

(プロジェクトフォルダから `python -m proviso …` として起動します。)

### ソルバのバックエンド

リファインメントエンジンはバックエンド非依存です。`z3-solver` パッケージがインポート可能なら
**Z3**(健全かつ完全で、単一変数の断片をはるかに超える)を使い、そうでなければ単一変数の比較
断片について完全な**純粋 Python サンプラ**にフォールバックします。`PROVISO_SOLVER=sampler` で
フォールバックを強制できます。依存義務(他の名前・`len`・`abs`/`min`/`max` を含むリファイン
メント)は Z3 を必要とし、無い場合はグラデュアルな実行時チェックへ穏やかに劣化します。

### エディタ連携(LSP)

`proviso lsp` は依存なしの言語サーバで、stdio 上で LSP(`Content-Length` フレーミングの
JSON-RPC)を話します。**差分(インクリメンタル)ドキュメント同期**(範囲ベースの編集)を
サポートし、CLI と*同じ*対話的診断(エラーとグラデュアル点の警告)を発行し、
`textDocument/hover` で囲む関数の効果推論済みシグネチャを返し、**`textDocument/definition`**
で**定義ジャンプ**(関数・型エイリアス・enum・コンストラクタ・protocol)に応答します。
`.pvo` ファイルに対して、LSP 対応エディタを `proviso lsp` コマンドに向けてください。
すぐ使える **Visual Studio Code** 用クライアントが [`editors/vscode/`](editors/vscode/) に
あります(セットアップはその README を参照: `pip install -e .` の後、拡張を起動して `.pvo`
ファイルを開く)。

---

## 20. スコープの境界と既知の制限

Proviso は焦点を絞ったプロトタイプです。v1.0.0 で意図的にスコープ外としたもの:

- 配列は単相(`Int` の `Array`)。構造的 measure は `len` のみ。
- 配列長が静的に追跡されるのは**リテラル**と**ガード**経由のみ。関数パラメータを通った長さは
  グラデュアル。
- 関数引数の部分型はリファインメント/効果については精密だが、効果変数の具体化は**単一段**。
  名前ベースの効果推論パスは効果変数を置換しない。
- **タイプステート**は静的に加え、静的パスが追えないグラデュアルなケースでは**実行時**にも
  強制される(`Resource` が状態を運び、誤った状態の演算は例外になる)。`@State` は型から消去
  され、状態は値に乗る。
- コンストラクタフィールドの実行時契約は強制されない。
- `match` の網羅性はネストしたパターンの内側まで検査し、具体的な未カバー例を提示する。冗長/
  到達不能なアームの検出は未実装。
- 呼び出しは名前に対してのみ。ラムダリテラルや添字要素を即座に呼ぶことはできない——まず束縛
  する。
- LSP は差分同期と定義ジャンプをサポートする。find-references と rename は未実装。
- エスケープした継続は精密な `Fn(Int) -> answer` 型を持つ。ただし非関数との join を通って
  エスケープする継続(節が `k` 自身を返す場合)はグラデュアルに劣化する。

これらの境界はプロトタイプが止まる場所であって、設計が止まる場所ではありません。

---

## 21. チートシート

```proviso
# 型とリファインメント
Int   Bool   Unit   Str   Array
Int{n | n > 0}                 Int{k | k >= 0 && k < len(xs)}
abs(n)   min(a, b)   max(a, b)   len(a)
type Nat = Int{n | n >= 0}

# 関数と効果
fn f(x: Int) -> Int ! {IO} { ... }     # 効果行を宣言
fn g(x: Int) -> Int { ... }            # 効果は推論
fn h(f: Fn(Int) -> Int ! e, x: Int) -> Int ! e { f(x) }   # 効果多相な高階関数
let lam = fn(x: Int) -> Int { x + 1 };  lam(41)

# 束縛と制御
let y = e;   let y: T = e;   let linear r = e;
if c { ... } else if c2 { ... } else { ... }
match v { Ctor(a, b) => ..., 0 => ..., _ => ... }

# データ
enum Shape { Circle(Int), Rect(Int, Int) }      Circle(10)
[1, 2, 3]    a[i]    len(a)
"hi " + name    to_str(42)

# 代数的効果
perform Op(arg)
handle <body> with { Op(x, k) => k(rv), return(v) => v }
handle <body> catch (e) { ... }                  # 例外

# 所有権とタイプステート
let linear conn = open();   borrow(conn);   clone(conn)
protocol File { Closed, Open }
fn open(f: Handle @ Closed) -> Handle @ Open { f }
```

---

## 付録A: 文法

非形式的 EBNF(字句の詳細は省略):

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
            | IDENT '(' (pattern (',' pattern)*)? ')'      # 大文字始まり = コンストラクタ
handle     := 'handle' block 'with' '{' clause (',' clause)* '}'
            | 'handle' block 'catch' '(' IDENT ')' block
clause     := IDENT '(' IDENT ',' IDENT ')' '=>' expr      # 演算節
            | 'return' '(' IDENT ')' '=>' expr             # デリミタ節

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

## 付録B: バージョン履歴

**v1.0.0** — 機能完成プロトタイプ。10 個のロードマップ項目すべてを実装:

1. ネスト/クロスハンドラの代数的効果 + エスケープする継続
2. 精密な関数引数の部分型(反変パラメータ、共変結果、効果部分集合)
3. 算術 measure `abs` / `min` / `max`
4. 配列長の静的追跡(リテラル + ガード)
5. 依存リファインメント(他の名前 + `len`)、len-ガードのオカレンス型付け(#5b)
6. ユーザー定義型(`enum` + `match`、ネストパターン、網羅性)
7. 配列
8. 契約消去 + blame
9. タイプステート(`protocol` + `@State`)
10. stdio 言語サーバ

加えて、当初のベースラインを超えて: Z3/サンプラのソルババックエンド、型エイリアス、効果推論、
文字列、マルチショット効果ハンドラ付きの一級関数、トランポリン化された評価器、**ネストパターン
の網羅性**(Maranget のアルゴリズム、具体的な未カバー例つき)、**実行時のタイプステート強制**
(`Resource` が状態を運び、静的パスが追えないグラデュアル領域を越えても強制される)、**LSP の
差分同期と定義ジャンプ**、**エスケープした継続の精密(非グラデュアル)型**。テストスイートは
157 個の依存なしテストです。
