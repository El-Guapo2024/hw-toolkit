# hw-toolkit — Claude Code plugin

An EE design copilot. Author schematics in `hw_toolkit` Python (file-as-truth),
render a **live KiCanvas preview**, gate with ERC/DRC, place the PCB + ratsnest,
search parts, autoroute — conversation-first.

## What's in the plugin

- **MCP servers** (`.mcp.json`): `designer` (ERC/DRC gates, render, BOM, converter
  math), `router` (autoroute via FreeRouting), `live-edit` (edits an OPEN
  eeschema over IPC).
- **Skill** `ee-design` + **agent** `ee-copilot` — the author flow + the
  go/assume/ask conversation policy (a plugin can't ship a `CLAUDE.md`, so the
  behavior lives here).
- **SessionStart hook** — lazy-installs node deps (elkjs) and checks for KiCad.

## How it installs (lazy, no manual env)

The Python servers launch with **`uvx`** — an ephemeral isolated venv that
fetches + caches `hw_toolkit` on first run. No `pip install`, no venv to manage.
Node deps (elkjs, for ELK layout) install once into the plugin's persistent data
dir via the SessionStart hook.

**One external prerequisite — KiCad** (a hundreds-of-MB binary, not a pip/npm
package, so the plugin detects it rather than installing it):

- macOS: `brew install --cask kicad`
- else: <https://www.kicad.org/download/> (KiCad 9+)
- or set `KICAD_CLI` to the `kicad-cli` binary path.

Node 18+ is also needed for ELK layout (`brew install node` / nodejs.org). The
SessionStart hook prints a hint if either is missing.

## Install

```
/plugin marketplace add https://github.com/ORG/hw-toolkit
/plugin install hw-toolkit
```

(For local dev: `/plugin marketplace add /path/to/hw-toolkit` — the repo root is
the marketplace, serving `./plugin`.)

Then ask the copilot to design a board. Pre-PyPI, the `.mcp.json` pins the
server to a git tag (`uvx --from git+…@v0.1.0`); once `hw_toolkit` is on PyPI it
becomes a plain `uvx designer-mcp`.

## Notes

- **`live-edit` is host-only** — it drives your running KiCad GUI over IPC, so it
  can't be containerized; it needs KiCad open with Preferences → API server on.
- **`designer`/`router` are headless** (kicad-cli + node) — fully sandboxable; a
  Docker image for CI may be published later.
- The optional **VS Code extension** (`copilot/vscode-ext/`) gives the two-pane
  notebook + live-preview UI on top of this plugin.
