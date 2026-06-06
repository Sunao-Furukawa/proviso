// Minimal VS Code client for the Proviso language server.
//
// It launches `<python> -m proviso lsp` and connects to it over stdio, registering
// it for `.pvo` files. All the intelligence (the dialogue diagnostics and hover)
// lives in the server (proviso/lsp.py); this file is only the thin client glue.

const { workspace } = require("vscode");
const { LanguageClient, TransportKind } = require("vscode-languageclient/node");

let client;

function activate(context) {
  const cfg = workspace.getConfiguration("proviso");
  const python = cfg.get("pythonPath", "python");

  let cwd = cfg.get("serverCwd", "");
  if (!cwd && workspace.workspaceFolders && workspace.workspaceFolders.length) {
    cwd = workspace.workspaceFolders[0].uri.fsPath;
  }

  const exe = {
    command: python,
    args: ["-m", "proviso", "lsp"],
    options: cwd ? { cwd } : {},
    transport: TransportKind.stdio,
  };
  const serverOptions = { run: exe, debug: exe };

  const clientOptions = {
    documentSelector: [{ scheme: "file", language: "proviso" }],
    outputChannelName: "Proviso",
  };

  client = new LanguageClient(
    "proviso",
    "Proviso Language Server",
    serverOptions,
    clientOptions
  );
  client.start();
  context.subscriptions.push({ dispose: () => client && client.stop() });
}

function deactivate() {
  return client ? client.stop() : undefined;
}

module.exports = { activate, deactivate };
