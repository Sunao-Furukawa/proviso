# Proviso プログラミングマニュアル (v0.1)

Proviso は「**正しさを段階的に支払える**」ことを中心に設計された実験的プログラミング言語です。
型注釈を足さなければ動的言語のように緩く動き、足すほど静的に強く検証されます。そして型システムは
**敵ではなく対話相手**として、すべての矛盾を「反例＋2つの選択肢」の形で提示します。

このマニュアルは v0.1 プロトタイプの**実際の挙動**に対応しています。仕様上のスコープ外（未実装）の
事項は §13 にまとめてあります。

---

## 目次

1. [はじめに（3本柱）](#1-はじめに3本柱)
2. [インストールと実行](#2-インストールと実行)
3. [プログラムの構造](#3-プログラムの構造)
4. [字句要素](#4-字句要素)
5. [型](#5-型)
6. [精緻化（refinement）と述語](#6-精緻化refinementと述語)
7. [関数](#7-関数)
8. [式と文](#8-式と文)
9. [効果（effects）](#9-効果effects)
10. [例外](#10-例外)
11. [所有権（ownership）](#11-所有権ownership)
12. [漸進的型付けと実行時契約](#12-漸進的型付けと実行時契約)
13. [組み込み関数](#13-組み込み関数)
14. [診断メッセージの読み方](#14-診断メッセージの読み方)
15. [文法リファレンス（EBNF）](#15-文法リファレンスebnf)
16. [スコープと制限](#16-スコープと制限)
17. [クイックリファレンス](#17-クイックリファレンス)

---

## 1. はじめに（3本柱）

Proviso は3つの軸を1つの仕組み（**反例を返す精緻化ソルバ**）の上で統合します。

1. **漸進的な依存（精緻化）型** — 型に述語を付けられる。`Int{n | n > 0}`。注釈を足すほど静的検証が強くなる。
2. **効果を型に乗せる** — 関数が行う副作用（IO/例外/ネットワーク等）をシグネチャに書く。`-> Int ! {Net}`。
3. **所有権を効果として扱う** — `linear` な資源の move/borrow を、説明可能な形（経路つき）で検査する。

---

## 2. インストールと実行

必要なもの: Python 3.10 以上（コア機能は標準ライブラリのみ。**必須の外部依存なし**）。

**任意:** `z3-solver` を入れると精緻化ソルバが自動的に Z3 バックエンドに切り替わります（無ければ
内蔵のサンプラにフォールバック。§6・§16 参照）。`pip install --user z3-solver`。
`PROVISO_SOLVER=sampler` を設定すると常にサンプラを使います。

```sh
# プロジェクトのルート（C:\Users\sadie\proviso）から
python -m proviso check <file>.pvo    # 型チェックのみ
python -m proviso run   <file>.pvo    # チェック → 問題なければ実行
python tests\run_tests.py             # テスト一式
```

- ソースファイルの拡張子は **`.pvo`**。
- `run` は**まず型チェック**し、確定エラー（hard error）があれば実行を拒否します。
- `?`（未精緻化）由来の保留点（gradual point）だけが残る場合は、実行時チェックを挿入して実行します。
- 終了コード: 成功 `0`、チェック失敗 `1`、構文エラー `2`。

実行の入口は **`main` 関数**です（引数なしで呼ばれ、戻り値が `=> main returned ...` として表示されます）。

---

## 3. プログラムの構造

プログラムは関数定義（`fn`）の並びです。トップレベルに置けるのは関数だけで、グローバル変数や文は
書けません。

```proviso
fn double(x: Int) -> Int {
  x * 2
}

fn main() -> Int ! {IO} {
  print(double(21));   # 42
  0
}
```

---

## 4. 字句要素

- **コメント**: `#` から行末まで。
- **識別子**: 英字または`_`で始まり、英数字と`_`が続く。例 `conn`, `safe_div`, `x2`。
- **整数リテラル**: 10進の符号なし数字列（負数は単項 `-` で表す）。例 `0`, `42`。**整数のみ**（浮動小数点なし）。
- **真偽リテラル**: `true`, `false`。
- **予約語**: `fn let linear if else true false handle catch return`
  （`return` は予約のみで未実装。ブロックの値は末尾の式で表します。§8 参照）。
- **記号/演算子**:
  `-> == != <= >= && || ( ) { } , ; : | ! + - * / % < > = .`

---

## 5. 型

基本型は3つです。

| 型 | 説明 | 精緻化 |
|---|---|---|
| `Int` | 整数 | 付けられる（`Int{n | ...}`） |
| `Bool` | 真偽値 | 付けられない（常に gradual） |
| `Unit` | 値なし。ブロックが末尾式を持たないときの値。表示は `()` | － |
| `Array` | 整数の配列（v0.1 は要素 Int 固定）。リテラル `[1, 2, 3]`、長さ `len(a)`、添字 `a[i]` | － |
| `Fn` | 第一級関数値（ラムダや捕捉した継続）。v0.1 では引数/効果を細分しない gradual な関数型 | － |

- **`Int`**（精緻化なし）は内部的に「未知の述語 `?`」を持つ**漸進的（gradual）**な型です。あらゆる要求と
  両立し、必要なら実行時チェックに先送りされます（§12）。
- **`Int{n | 述語}`** は「述語を満たすことが保証された整数」。`n` は値を指す**束縛変数**（名前は任意）。
  例: `Int{n | n > 0}`, `Int{k | k >= 0 && k <= 100}`。
- 戻り型を省略した関数（`fn f() { ... }`）は `Unit` を返します。

### 型エイリアス

トップレベルで `type 名前 = 型` と書くと、精緻化型に名前を付けて再利用できます（末尾の `;` は任意）。

```proviso
type Nat = Int{n | n >= 0}
type Percent = Int{p | p >= 0 && p <= 100}

fn clamp_high(x: Nat) -> Percent { ... }
```

エイリアスは使用箇所で展開されます。**実行時契約も診断もエイリアス越しに保たれます**——`Nat` の引数に
負値を渡せば、ちゃんと反例つきの `refine-conflict`（静的）や実行時チェック（gradual 経由）になります。
v0.1 ではエイリアスは「裸の名前」として使ってください（`Nat{...}` のような追加精緻化は未対応）。

### ユーザー定義型（enum）と match

`enum` で直和型（タグ付きヴァリアント）を定義します。単一ヴァリアントの enum は実質レコードです。

```proviso
enum Shape {
  Circle(Int),
  Rect(Int, Int)
}
```

- **構築**: コンストラクタを関数のように呼びます — `Circle(10)`, `Rect(4, 5)`。
- **分解**: `match` 式でヴァリアントごとにフィールドを束縛します。

```proviso
fn area(s: Shape) -> Int {
  match s {
    Circle(r)  => 3 * r * r,
    Rect(w, h) => w * h
  }
}
```

- **網羅性**: チェッカは全ヴァリアントが網羅されているか検査します。漏れがあれば `non-exhaustive`
  診断（不足アーム or ワイルドカード `_` の2択）を出します。
- パターンは1段（`Ctor(変数...)` か `_`）。ネストパターン・`.field` 直接アクセスは未対応（分解は match で）。

精緻化型の構文は `BaseType { 束縛変数 | 述語 }` です。述語は**束縛変数と整数定数だけ**から成る、
線形整数算術の決定可能な断片です。

**述語に書けるもの**

- 比較: `n < k`, `n <= k`, `n > k`, `n >= k`, `n == k`, `n != k`
  （`k < n` のように定数を左に書いてもよい）
- 論理結合: `&&`（かつ）, `||`（または）, `!`（否定）, 括弧 `( )`
- 真偽定数: `true`, `false`

```proviso
Int{n | n > 0}                 # 正
Int{n | n >= 0 && n <= 3}       # 0..3
Int{n | n != 0}                 # 非ゼロ
Int{x | x < 10 || x > 20}       # 10未満 または 20超
```

**依存精緻化（#5）**: 述語は束縛変数だけでなく、**同じスコープの他の変数**や配列長 `len(x)` も参照できます
（`+ - *` の線形算術も可）。これにより真の依存型が書けます。

```proviso
fn between(lo: Int, hi: Int{h | h >= lo}, x: Int{v | v >= lo && v <= hi}) -> Int { x }
fn get_at(xs: Array, i: Int{k | k >= 0 && k < len(xs)}) -> Int { xs[i] }
```

呼び出し時、形式引数は実引数（変数なら名前、リテラルなら定数）に置換され、SMT バックエンドが義務を判定します。
**証明できれば実行時チェックなし／反証（恒偽）なら反例つきの確定エラー／それ以外は実行時チェック（gradual）**、
という3段階です。実行時には実際の引数値で述語を評価して契約を検査します。

**意味論（重要）**: 精緻化どうしの適合は**含意（implication）**で判定されます。「ソースの述語 P」が
「要求の述語 Q」を**すべての値で**含意するか（P ⟹ Q）。含意が成り立たなければ、ソルバは**反例**
（P を満たすが Q を破る具体的な整数）を返し、それが診断に表示されます（§14）。

リテラルや算術からも精緻化が**推論**されます。

- 整数リテラル `5` の型は `Int{v | v == 5}`（シングルトン）。
- `+ - *` は区間（interval）を伝播して結果の精緻化を推論します（例: `abs(x)`（`>=0`）に `+1` 等）。
- `/` と `%` の結果は安全側に倒して gradual（`?`）になります。

---

## 7. 関数

```proviso
fn 名前(引数, ...) -> 戻り型 ! {効果, ...} {
  本体ブロック
}
```

- `-> 戻り型` は省略可（省略時は `Unit`）。
- `! {効果, ...}` は省略可。**省略すると効果は本体から推論されます**（§9）。明示的に書いた場合のみ
  「契約」として検査されます（`! {}` は「純粋である」という約束＝守らないと `effect-leak`）。効果が1つなら
  `! Net` のように波括弧を省略可。
- 引数は `名前: 型`。先頭に `linear` を付けると**所有権つき資源**になります（§11）。

```proviso
fn safe_div(a: Int, b: Int{n | n != 0}) -> Int {
  a / b
}

fn greet(msg: Int) -> Int ! {IO} {
  print(msg);
  msg
}
```

- **再帰**が可能です（自分自身や相互に呼び出せる）。
- 呼び出せるのは**名前付き関数のみ**（第一級の関数値・ラムダはありません）。呼び出しは識別子に対してのみ:
  `f(args)`。
- 関数本体の最後の式が**戻り値**になります（§8）。`return` 文はありません。

### 第一級関数（ラムダ）

関数は値です。**ラムダ** `fn(引数...) -> 戻り型 { 本体 }` を式として書け、変数に束縛・他の関数へ渡せます。
関数型の引数は `Fn`（gradual な関数型）で受けます。

```proviso
fn twice(f: Fn, x: Int) -> Int { f(f(x)) }

fn main() -> Int ! {IO} {
  let g = fn(n: Int) -> Int { n * 10 };
  print(twice(g, 3))    # g(g(3)) = 300
}
```

`Fn` 値の呼び出しは結果・効果ともに gradual に扱われます（効果多相の簡易版）。

---

## 8. 式と文

Proviso は式指向です。`if` やブロックも値を持ちます。

### ブロック

```proviso
{
  文;          # let 文、または 式文（末尾に ;）
  文;
  末尾の式      # ; を付けない。これがブロックの値
}
```

末尾の式を省略するとブロックの値は `Unit` です。

### let 文

```proviso
let x = 式;                 # 型は推論
let y: Int{n | n > 0} = 式;  # 型注釈つき（注釈と式が適合するか検査される）
let linear conn = 式;        # 所有権つき資源（§11）
```

### if 式

```proviso
if 条件 { ... } else { ... }
if 条件 { ... } else if 別条件 { ... } else { ... }
if 条件 { ... }                 # else 省略時、値は Unit
```

- 条件は `Bool`。
- `if x > 0 { ... }` のように条件が `変数 比較 定数`（や `&&` 連結）の場合、**then 枝の中で変数が絞り込まれます**
  （occurrence typing）。これにより「ガードを足して要求を満たす」修正（§14 の選択肢B）が実際に効きます。

### 演算子と優先順位（低い→高い）

| 段階 | 演算子 | 結合 | 結果型 |
|---|---|---|---|
| 1 | `\|\|` | 左 | Bool |
| 2 | `&&` | 左 | Bool |
| 3 | `< <= > >= == !=` | **非結合**（連鎖不可） | Bool |
| 4 | `+ -` | 左 | Int |
| 5 | `* / %` | 左 | Int |
| 6 | 単項 `- !` | － | Int / Bool |
| 7 | 関数呼び出し `f(...)` | － | 関数の戻り型 |

- 比較は**連鎖できません**（`a < b < c` は不可。`a < b && b < c` と書く）。
- 整数除算 `/` は切り捨て（floor）。`0` で割ると実行時エラー（精緻化 `Int{n | n != 0}` で静的に防げる）。

---

## 9. 効果（effects）

関数が実行時に起こす副作用を、戻り型のうしろに `!` で書きます。これを**効果行（effect row）**と呼びます。

```proviso
fn fetch() -> Int ! {Net} { http_get(2) }
fn log(x: Int) -> Int ! {IO} { print(x); x }
fn job()  -> Int ! {IO, Net} { ... }   # 複数
```

**組み込みの効果**

| 効果 | 意味 | 発生源 |
|---|---|---|
| `IO` | 入出力 | `print` |
| `Exc` | 例外を投げる | `throw` |
| `Net` | ネットワーク | `http_get` |

**効果推論（`!` を省略した場合）**: 効果行を**書かなかった**関数は、本体から効果が**推論**され、それが
その関数の効果行になります（相互再帰も不動点で扱う）。これは「足さなくても動く」という gradual の発想を
効果にも適用したもので、**省略しても `effect-leak` にはなりません**。推論結果は呼び出し側にも伝わるので、
**効果を明示宣言した呼び出し側**はそれを勘定に入れる必要があります（さもなければ呼び出し側が leak）。

**検査ルール（`!` を明示した場合）**: 関数本体が実際に起こす効果（呼び出し先の効果の合併）は、宣言した
効果行の**部分集合**でなければなりません。

- 本体の効果 ⊄ 宣言 → `effect-leak`（効果の隠蔽）エラー。`! {}` は「純粋」という明示的な約束。
- 宣言を多めに書く（使っていない効果を宣言）のは許されます。
- `handle ... catch`（§10）は本体から `Exc` 効果を**取り除き**ます。

> **依存型との統合**: `http_get` の「リトライは最大3回」という安全性は、効果 `Net` に乗せつつ、
> その**引数の精緻化** `retries: Int{r | r >= 0 && r <= 3}` として表現されます。つまり効果の安全条件が、
> `n > 0` などと**同じソルバ**で検査されます。

### 代数的効果と multi-shot ハンドラ

任意の演算を `perform Op(引数)` で発生させ、`handle <body> with { ... }` で処理します。演算名
（`Op`）はそのまま効果ラベルになり、`perform Op` は効果 `Op` を持ちます。`handle...with` は処理した
演算を本体の効果から**取り除き**ます。

```proviso
fn main() -> Int ! {IO} {
  let total = handle {
    let a = perform Choose(0);
    let b = perform Choose(0);
    a + b
  } with {
    Choose(x, k) => k(0) + k(10),   # k は捕捉した継続。複数回呼べる = multi-shot
    return(v) => v
  };
  print(total);    # 40 （{0,10}×{0,10} の総和）
  total
}
```

- ハンドラ節 `Op(x, k) => ...`: `x` は演算の引数、`k` は **multi-shot な継続**（第一級の `Fn` 値）。
  `k(v)` は「`perform` の続き」をハンドラ境界まで実行し、その結果値を返します。**`k` は何度でも呼べ**、
  各回が独立に走ります（`k(0) + k(10)` は2回 resume）。0回呼べば例外的な打ち切り、1回なら通常の継続。
- `return(v) => ...`: 本体が正常終了したときの値 `v` を変換します（省略時は恒等）。
- 実装は継続渡し（CPS）評価器で、これにより**完全な multi-shot** を実現しています。

---

## 10. 例外

`throw` で投げ、`handle ... catch` で捕捉します。例外コードは整数です。

```proviso
fn checked_div(a: Int, b: Int) -> Int ! {Exc} {
  if b == 0 {
    throw(1)        # Exc 効果
  } else {
    a / b
  }
}

fn main() -> Int ! {IO} {
  let ok  = handle { checked_div(20, 4) } catch (e) { -1 };  # 5
  let bad = handle { checked_div(7, 0) }  catch (e) { -1 };  # -1（捕捉）
  print(ok); print(bad);
  ok
}
```

- `handle { 本体 } catch (名前) { ハンドラ }`。本体で `throw` されると、`名前` に投げられたコード（Int）が
  束縛され、ハンドラが評価されます。
- `handle` は本体の `Exc` 効果を**discharge（解消）**します。上の `main` は `Exc` を宣言せずに済みます。
- 値: `handle` 式全体の型は、本体とハンドラの型の合併です。

---

## 11. 所有権（ownership）

`linear` を付けた束縛は**所有権つき資源**（1度しか消費できない値）を表します。Proviso では所有権を
**効果**として捉えます。

- 資源を**普通に使う**（関数へ値渡しする等）＝ `Move` 効果＝**消費**。以後その名前は無効。
- `borrow(x)` / `clone(x)` は**消費せずに読む**（`Borrow`）。
- 消費後に再び使うと `moved`（use-after-move）エラー。診断は move 地点→再使用地点の**経路**を示します。

```proviso
fn main() -> Int ! {Net} {
  let linear conn = http_get(0);
  send(borrow(conn));   # 読むだけ → move しない
  send(conn)            # ここで初めて move（以後 conn は使えない）
}
```

修正の定石は2つ（診断が提示します）:

- **BORROW**: 先の使用が読むだけなら `borrow(conn)` にして所有権を残す。
- **CLONE**: 両方が所有権を必要とするなら `let conn2 = clone(conn);` で独立コピーを作る。

> 注: 現在のモデルは「linear値の素の使用＝move、`borrow`/`clone`＝読み取り」という単純化です。直線的コード
> と `if`（両枝の move を合併）を扱います（§16）。

---

## 12. 漸進的型付けと実行時契約

Proviso の中心思想です。**精緻化された引数は、実行時にも必ず検査されます**（実行時契約）。静的証明は
「その検査が絶対に失敗しないと信頼できる」という意味を持ちます。

3つの状態があります。

| ソース側 | 要求側 | 結果 |
|---|---|---|
| 精緻化あり（支払い済み） | 精緻化あり | **静的判定**。含意が成り立てば OK、破れれば**確定エラー**（反例つき） |
| `?`（未精緻化 `Int`） | 精緻化あり | **gradual point**: 実行時チェックを挿入（`gradual[cast]` 注記、エラーではない） |
| 何でも | `?`（要求が緩い） | 無条件 OK |

```proviso
fn need_positive(x: Int{n | n > 0}) -> Int { x }

fn from_input(raw: Int) -> Int ! {IO} {   # raw は ? （未払い）
  let checked = need_positive(raw);        # gradual[cast]: 実行時に n>0 を検査
  print(checked); checked
}
```

- `python -m proviso check` → `OK: ... (1 gradual point will be checked at runtime)`。
- `run` で `from_input(7)` なら `7 > 0` を実行時に確認して通過。`from_input(0)` なら**実行時に検査が発火**してエラー
  （「先送りした請求書が来る」）。
- 「今すぐ支払う」には `raw: Int{r | r > 0}` のように注釈を足す。すると静的に証明され、gradual point は消えます。

この連続性こそが「`Int` のままなら Python 的に緩く、`Int{...}` を足せば Idris 的に厳密に」を注釈1つで
行き来できる、という設計の核です。

---

## 13. 組み込み関数

| 関数 | シグネチャ（概念） | 効果 | 説明 |
|---|---|---|---|
| `print(x)` | 任意の基本型 → `Unit` | `IO` | 値を1行出力。`Int`/`Bool`/`Unit` を受ける |
| `throw(code)` | `Int -> Unit` | `Exc` | 例外を投げる |
| `abs(x)` | `Int -> Int{v | v >= 0}` | なし | 絶対値（戻り値が非負であることが証明される） |
| `http_get(retries)` | `Int{r | r >= 0 && r <= 3} -> Int{v | v >= 0}` | `Net` | ネットワーク取得をシミュレート。`200` を返す |
| `borrow(x)` | `Int -> Int` | なし | 所有権を消費せず読む（§11） |
| `clone(x)` | `Int -> Int` | なし | 独立コピーを得る（§11） |
| `len(a)` | `Array -> Int{v | v >= 0}` | なし | 配列長。依存精緻化の measure（`len(a)`）として参照可（§6） |

> `print` は v0.1 では基本型に対して多相的に振る舞います（型チェッカが特別扱い）。

---

## 14. 診断メッセージの読み方

すべての拒否は同じ形をしています（型システムを対話相手にするための統一フォーマット）。

```
conflict[種類]: 見出し
  at line N
   | （該当ソース行）

  required  要求された制約
  known     実際に分かっている保証
  why       なぜ食い違うかの説明
  counterexample  反例（値、または実行経路）

  two ways forward -- your call:
  (A) ...（ゆるめる / 借用する 等）   edit: 具体的な修正
  (B) ...（強める / 複製する 等）     edit: 具体的な修正

  note  補足（多くは「これは支払い済みの確定エラー」など）
```

**診断の種類**

| code | 意味 | 典型的な2択 |
|---|---|---|
| `refine-conflict` | 精緻化の含意が成り立たない（依存精緻化を含む） | (A) 要求をゆるめる / (B) ソースを強める（ガード等） |
| `effect-leak` | 本体の効果を型が隠している | (A) 効果を宣言する / (B) handle で解消・操作を除去 |
| `moved` | move 済みの linear 値を使用 | (A) borrow / (B) clone |
| `bounds` | 配列添字が範囲内だと証明できない（恒偽） | (A) ガードする / (B) 添字を精緻化（依存型） |
| `non-exhaustive` | `match` が一部のヴァリアントを網羅していない | (A) 不足アームを追加 / (B) ワイルドカード `_` を追加 |
| `type` | 基本型の不一致（Int と Bool 等） | － |
| `arity` | 引数の個数違い | － |
| `unbound` | 未定義の名前・関数 | － |

**注記（warning）**

- `gradual[cast]` — エラーではなく、実行時チェックを挿入した保留点（§12）。

**まとめ行**

- `OK: all obligations discharged (M gradual points ...)` — 確定エラーなし。M個は実行時検査。
- `FAIL: N unresolved obligations (M gradual points ...)` — 確定エラーN個。`run` は実行を拒否。

---

## 15. 文法リファレンス（EBNF）

```
module    := (type_alias | enum_decl | fn_decl)*
type_alias:= 'type' IDENT '=' type ';'?
enum_decl := 'enum' IDENT '{' variant (',' variant)* ','? '}'
variant   := IDENT ('(' type (',' type)* ')')?
fn_decl   := 'fn' IDENT '(' params? ')' ('->' type)? ('!' eff_row)? block
params    := param (',' param)*
param     := 'linear'? IDENT ':' type

type      := IDENT ('{' IDENT '|' pred '}')?        # IDENT は Int/Bool/Unit またはエイリアス名
eff_row   := '{' effect (',' effect)* '}' | effect
effect    := IDENT ('{' IDENT '|' pred '}')?

block     := '{' stmt* expr? '}'
stmt      := 'let' 'linear'? IDENT (':' type)? '=' expr ';'
           | expr ';'

expr      := if | handle | match | logic_or
if        := 'if' expr block ('else' (if | block))?
handle    := 'handle' block ('catch' '(' IDENT ')' block | 'with' '{' wclause (',' wclause)* ','? '}')
wclause   := IDENT '(' IDENT ',' IDENT ')' '=>' expr   |   'return' '(' IDENT ')' '=>' expr
match     := 'match' expr '{' arm (',' arm)* ','? '}'
arm       := (IDENT ('(' IDENT (',' IDENT)* ')')? | '_') '=>' expr
# primaries also include:  lambda := 'fn' '(' params? ')' ('->' type)? block
#                          perform := 'perform' IDENT '(' expr ')'
logic_or  := logic_and ('||' logic_and)*
logic_and := comparison ('&&' comparison)*
comparison:= additive (CMP additive)?               # 連鎖なし
additive  := multiplicative (('+'|'-') multiplicative)*
multiplicative := unary (('*'|'/'|'%') unary)*
unary     := ('-'|'!') unary | postfix
postfix   := primary (('(' args? ')') | ('[' expr ']'))*   # 呼び出しは IDENT のみ / 添字
primary   := INT | 'true' | 'false' | IDENT | '(' expr ')' | block
           | '[' (expr (',' expr)*)? ']'                   # 配列リテラル
args      := expr (',' expr)*

# 精緻化述語（依存精緻化: 束縛変数・他の変数・len(x)・線形算術を含む）
pred      := pred_and ('||' pred_and)*
pred_and  := pred_atom ('&&' pred_atom)*
pred_atom := '!' pred_atom | '(' pred ')' | 'true' | 'false' | term CMP term
term      := factor (('+'|'-') factor)*
factor    := tatom ('*' tatom)*
tatom     := INT | IDENT | 'len' '(' IDENT ')'            # IDENT: 束縛変数=値, それ以外=他変数
CMP       := '<' | '<=' | '>' | '>=' | '==' | '!='
```

---

## 16. スコープと制限

v0.1 で**意図的に未対応**の事項（いずれも既知の拡張で、設計上の欠陥ではありません）。

- **精緻化ソルバ**: `z3-solver` があれば自動的に Z3（健全・完全、より広い論理を判定）を使い、無ければ
  内蔵サンプラ（単一束縛変数×比較の断片に対して反例探索が完全）にフォールバックします。`implies` が反例を
  返すインターフェイスはバックエンド非依存に設計されています。
- **依存精緻化**は動くが、measure は `len` のみ。配列の長さは静的に追跡しないので、`let` 束縛した配列への
  リテラル添字は（証明されず）実行時チェックになります。`len(x)` ガードの occurrence typing も未対応。
- 代数的効果ハンドラは **multi-shot 完全対応**（CPS 評価器）。ただし第一級関数型は gradual な `Fn`
  （`Fn(Int)->Int ! e` のような細分や効果変数は未対応）なので、**効果多相は gradual 近似**です。
- 型エイリアスは「裸の名前」のみ（`Nat{...}` のような追加精緻化や、エイリアスのジェネリクスは未対応）。
- 所有権は単純な linear-use 解析（直線コード＋`if`/`match`）。リージョン/ライフタイム推論なし。
- 基本型は `Int`/`Bool`/`Unit`/`Array`（要素 Int）/`Fn` ＋ユーザー定義 `enum`。ジェネリクス・文字列・
  浮動小数点なし。`enum` のパターンは1段（ネスト不可）、コンストラクタフィールドの実行時契約は未強制。
- 評価器は CPS のため Python フレームを深く積みます。非常に深い再帰は（引き上げた）再帰上限に当たり得ます。
- `return` 文なし（ブロック末尾式が値）。

> #1-#7 はすべて実装済み（テスト48件）。次の候補: `Fn(T)->T ! e` の精密な関数型＋効果変数多相、
> measure 追加、配列長の静的追跡、ネストパターン、文字列、深い再帰向けのトランポリン化。

---

## 17. クイックリファレンス

```proviso
# 型エイリアス
type Nat = Int{n | n >= 0}

# 関数・型・効果（! を省略すると効果は推論される）
fn name(x: Nat, linear r: Int) -> Int{n | n >= 0} ! {IO, Net} { ... }

# 精緻化型
Int                         # gradual（?）
Int{n | n > 0}              # 正
Int{n | n >= 0 && n <= 3}    # 範囲

# 文
let x = e;
let y: Int{n | n != 0} = e;
let linear h = e;
e;                          # 式文

# 式
if c { a } else { b }
handle { e } catch (err) { h }
a + b   a - b   a * b   a / b   a % b
a < b   a <= b  a == b  a != b
p && q  p || q  !p   -n

# 組み込み
print(x)  throw(code)  abs(x)  http_get(retries)  borrow(x)  clone(x)

# 実行
python -m proviso check file.pvo
python -m proviso run   file.pvo
```

最小プログラム:

```proviso
fn main() -> Int ! {IO} {
  print(42);
  0
}
```

---

*Proviso v0.1 — リポジトリ: https://github.com/Sunao-Furukawa/proviso ／ 設計の全体像は `README.md` を参照。*
