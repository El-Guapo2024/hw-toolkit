# hw_toolkit copilot — VS Code extension

Two-pane EE copilot inside VS Code:

- **Left** — the copilot notebook (`docs/projects/copilot/copilot.ipynb`).
- **Right** — VS Code **Simple Browser** on `board.serve_live()`'s localhost
  URL: the live KiCanvas preview that auto-reloads on every `write_kicad()`.

Both panes are VS Code tabs — Simple Browser renders the KiCanvas site, so no
external window and no notebook-only view.

## Commands

- **hw_toolkit: Open Copilot (notebook + live preview)** — opens the notebook
  left and the preview right, split.
- **hw_toolkit: Show Live Preview (right pane)** — (re)opens just the preview.

Run from the Command Palette (Cmd-Shift-P).

## Settings

- `hwToolkit.previewPort` (default `8731`) — must match `board.serve_live(port=…)`.
- `hwToolkit.notebookPath` (default `docs/projects/copilot/copilot.ipynb`).

## Run it (dev host — no packaging)

1. Open this folder (`copilot/vscode-ext/`) in VS Code.
2. Press **F5** → "Run Extension" launches an Extension Development Host window.
3. In that window, open the `hw-toolkit` workspace, then run the setup cell in
   the notebook (it calls `board.serve_live()` → starts the preview server on
   port 8731).
4. Cmd-Shift-P → **hw_toolkit: Open Copilot**. Notebook left, live preview right.

## Install it (persistent)

```bash
npm install -g @vscode/vsce      # once
cd copilot/vscode-ext
vsce package                     # → hw-toolkit-copilot-0.1.0.vsix
code --install-extension hw-toolkit-copilot-0.1.0.vsix
```

Then **hw_toolkit: Open Copilot** is available in your normal VS Code window.

## Order of operations

The preview pane shows a connection error until `board.serve_live()` is
running (the notebook cell starts it). Start the server first, then run **Open
Copilot** (or **Show Live Preview** to refresh).
