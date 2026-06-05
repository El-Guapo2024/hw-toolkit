// hw_toolkit copilot — two-pane VS Code layout.
//
// Left pane: the copilot notebook (you drive the agent here).
// Right pane: VS Code's Simple Browser pointed at board.serve_live()'s
// localhost URL — the live KiCanvas preview that auto-reloads on every
// write_kicad(). Both panes live in VS Code; no external window.
const vscode = require("vscode");

function cfg() {
  return vscode.workspace.getConfiguration("hwToolkit");
}

function previewUrl() {
  const port = cfg().get("previewPort", 8731);
  return `http://127.0.0.1:${port}/`;
}

// Open (or focus) the Simple Browser preview in the given editor column.
async function openPreview(column) {
  const url = previewUrl();
  try {
    // Preferred: lets us pin it to a specific column (the right pane).
    await vscode.commands.executeCommand(
      "simpleBrowser.api.open",
      vscode.Uri.parse(url),
      { viewColumn: column, preserveFocus: false }
    );
  } catch (_e) {
    // Fallback for VS Code builds without the api command.
    await vscode.commands.executeCommand("simpleBrowser.show", url);
  }
}

// Open the copilot notebook in the left column.
async function openNotebook() {
  const ws =
    vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0];
  if (!ws) {
    vscode.window.showErrorMessage(
      "hw_toolkit: open the hw-toolkit workspace folder first."
    );
    return false;
  }
  const rel = cfg().get("notebookPath", "docs/projects/copilot/copilot.ipynb");
  const uri = vscode.Uri.joinPath(ws.uri, rel);
  try {
    await vscode.commands.executeCommand(
      "vscode.openWith",
      uri,
      "jupyter-notebook",
      vscode.ViewColumn.One
    );
    return true;
  } catch (e) {
    vscode.window.showErrorMessage(`hw_toolkit: can't open ${rel}: ${e}`);
    return false;
  }
}

function activate(context) {
  context.subscriptions.push(
    vscode.commands.registerCommand("hwToolkit.openCopilot", async () => {
      const ok = await openNotebook();
      if (ok) await openPreview(vscode.ViewColumn.Two);
      vscode.window.setStatusBarMessage(
        "hw_toolkit copilot: notebook + live preview", 4000
      );
    }),
    vscode.commands.registerCommand("hwToolkit.showPreview", () =>
      openPreview(vscode.ViewColumn.Two)
    )
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
