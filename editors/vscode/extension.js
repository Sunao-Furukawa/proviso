// VS Code client for Proviso.
//
//   * a "Proviso: Run Current File" command that runs the open `.pvo` with
//     `proviso run` in an integrated terminal (bound to F5 / Ctrl+F5 for `.pvo`);
//   * a language server (`proviso lsp`) for live diagnostics + hover.
//
// The run command is registered FIRST and the language server is started lazily in a
// try/catch, so syntax highlighting and "run" keep working even if the bundled
// `vscode-languageclient` is missing or the server fails to start. (Only `vscode`
// is required at module load -- everything else is required lazily, so a packaging
// problem can never stop the extension from activating.)
//
// You do NOT press plain F5-to-debug to use this; there is no Proviso debugger.

const vscode = require("vscode");

let client;

function serverCwd(cfg) {
  let cwd = cfg.get("serverCwd", "");
  const folders = vscode.workspace.workspaceFolders;
  if (!cwd && folders && folders.length) {
    cwd = folders[0].uri.fsPath;
  }
  return cwd;
}

function activate(context) {
  // 1) Register the command first -- it must always exist.
  context.subscriptions.push(
    vscode.commands.registerCommand("proviso.runFile", runCurrentFile)
  );

  // 2) Start the language server; tolerate any failure.
  try {
    startLanguageServer(context);
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    vscode.window.showWarningMessage(
      "Proviso: highlighting and Run are active, but the language server could not " +
        "start (diagnostics/hover disabled): " +
        msg +
        " — run `npm install` in editors/vscode before `vsce package`, or check the " +
        "`proviso.pythonPath` / `proviso.serverCwd` settings."
    );
  }
}

function startLanguageServer(context) {
  const { LanguageClient, TransportKind } = require("vscode-languageclient/node");
  const cfg = vscode.workspace.getConfiguration("proviso");
  const python = cfg.get("pythonPath", "python");
  const cwd = serverCwd(cfg);

  const exe = {
    command: python,
    args: ["-m", "proviso", "lsp"],
    options: cwd ? { cwd } : {},
    transport: TransportKind.stdio,
  };
  const clientOptions = {
    documentSelector: [{ scheme: "file", language: "proviso" }],
    outputChannelName: "Proviso",
  };

  client = new LanguageClient(
    "proviso",
    "Proviso Language Server",
    { run: exe, debug: exe },
    clientOptions
  );
  client.start();
  context.subscriptions.push({ dispose: () => client && client.stop() });
}

function runCurrentFile() {
  const editor = vscode.window.activeTextEditor;
  if (!editor || editor.document.languageId !== "proviso") {
    vscode.window.showInformationMessage("Open a .pvo file to run it with Proviso.");
    return;
  }
  const cfg = vscode.workspace.getConfiguration("proviso");
  const python = cfg.get("pythonPath", "python");
  const cwd = serverCwd(cfg);
  const file = editor.document.fileName;

  editor.document.save().then(() => {
    const terminal = vscode.window.createTerminal(
      cwd ? { name: "Proviso", cwd } : { name: "Proviso" }
    );
    terminal.show(true);
    terminal.sendText(`${python} -m proviso run "${file}"`);
  });
}

function deactivate() {
  return client ? client.stop() : undefined;
}

module.exports = { activate, deactivate };
