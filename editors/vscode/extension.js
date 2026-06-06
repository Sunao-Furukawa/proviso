// VS Code client for Proviso.
//
//   * launches `<python> -m proviso lsp` over stdio for live diagnostics + hover
//     (all the intelligence lives in the server, proviso/lsp.py); and
//   * adds a "Proviso: Run Current File" command that runs the open `.pvo` with
//     `proviso run` in an integrated terminal.
//
// Note: you do NOT press F5 (Start Debugging) to use this extension -- there is no
// Proviso debugger. F5 is only for *developing* the extension itself (it launches an
// Extension Development Host, and only when the `editors/vscode` folder is open). To
// run a Proviso program, use the command below (bound to F5 / Ctrl+F5 while a `.pvo`
// file is focused, or the play button in the editor title bar).

const { workspace, window, commands } = require("vscode");
const { LanguageClient, TransportKind } = require("vscode-languageclient/node");

let client;

function serverCwd(cfg) {
  let cwd = cfg.get("serverCwd", "");
  if (!cwd && workspace.workspaceFolders && workspace.workspaceFolders.length) {
    cwd = workspace.workspaceFolders[0].uri.fsPath;
  }
  return cwd;
}

function activate(context) {
  const cfg = workspace.getConfiguration("proviso");
  const python = cfg.get("pythonPath", "python");
  const cwd = serverCwd(cfg);

  // --- the language server (diagnostics + hover) ---
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

  // --- the "run this file" command ---
  context.subscriptions.push(
    commands.registerCommand("proviso.runFile", runCurrentFile)
  );
}

function runCurrentFile() {
  const editor = window.activeTextEditor;
  if (!editor || editor.document.languageId !== "proviso") {
    window.showInformationMessage("Open a .pvo file to run it with Proviso.");
    return;
  }
  const cfg = workspace.getConfiguration("proviso");
  const python = cfg.get("pythonPath", "python");
  const cwd = serverCwd(cfg);
  const file = editor.document.fileName;

  editor.document.save().then(() => {
    const terminal = window.createTerminal(
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
