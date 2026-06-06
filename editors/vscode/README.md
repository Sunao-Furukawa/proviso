# Proviso for Visual Studio Code

A VS Code extension that gives you, for `.pvo` files:

- **syntax highlighting** — a TextMate grammar for keywords, types, refinements,
  constructors, effects, operators, strings, and comments;
- **live diagnostics** — the same dialogue (required / known / why / counterexample /
  two choices) the CLI prints, shown inline as you type, with gradual points as warnings;
- **hover** — the enclosing function's effect-inferred signature.

Highlighting works on its own; diagnostics and hover are powered by the Proviso language
server (`proviso lsp`).

VS Code has no built-in "just point at an LSP command" setting, so this small client
extension is the supported way to use `proviso lsp`.

## Prerequisites

- **Python 3.8+** with the Proviso package importable (see step 1).
- **VS Code 1.75+**.
- **Node.js + npm** (only to build/run the extension).
- *(optional)* `pip install z3-solver` for the faster SMT backend; without it the
  pure-Python sampler is used automatically.

## 1. Make `proviso lsp` runnable

Pick **one**:

- **Recommended — install the package** (then it works from any folder):
  ```sh
  cd <proviso repo root>
  pip install -e .            # or:  pip install -e ".[smt]"  to also get Z3
  ```
  Now `python -m proviso lsp` (and the `proviso` command) work anywhere.

- **Without installing** — leave the package in place and tell the extension where it
  is: set the `proviso.serverCwd` setting (see below) to the repo root, so
  `python -m proviso lsp` can `import proviso`.

Sanity check (it should sit waiting for LSP input; press Ctrl+C to exit):
```sh
python -m proviso lsp
```

## 2. Run the extension

### Quick try (Extension Development Host)

```sh
cd editors/vscode
npm install
code .            # open this folder in VS Code
```
Press **F5** ("Run Proviso Extension"). A second VS Code window opens — open any
`.pvo` file there and diagnostics/hover appear.

### Install it permanently

```sh
cd editors/vscode
npm install
npm install -g @vscode/vsce
vsce package                       # produces proviso-1.0.0.vsix
code --install-extension proviso-1.0.0.vsix
```

## 3. Settings

| Setting | Default | Meaning |
|---------|---------|---------|
| `proviso.pythonPath` | `python` | Interpreter used to launch `python -m proviso lsp`. Use a full path or a venv's python if `python` isn't on PATH. |
| `proviso.serverCwd` | *(empty)* | Working directory for the server. Empty = the first workspace folder. Set to the Proviso repo root if you did **not** `pip install -e .`. |

Example `settings.json`:
```json
{
  "proviso.pythonPath": "C:/Python311/python.exe",
  "proviso.serverCwd": "C:/Users/you/proviso"
}
```

## Troubleshooting

- **No diagnostics appear.** Open *Output → Proviso* (and *Output → Proviso Language
  Server*) to see startup errors. The usual cause is that `python -m proviso lsp`
  cannot import the package — install it (step 1) or set `proviso.serverCwd`.
- **`python` not found.** Set `proviso.pythonPath` to a full path.
- **Diagnostics are stale.** They refresh on edit and save; this client uses
  full-document sync.

## What this extension does *not* do (yet)

Go-to-definition, completion, and incremental document sync. Diagnostics and hover come
straight from `proviso/lsp.py`; the highlighting grammar is
`syntaxes/proviso.tmLanguage.json`.
