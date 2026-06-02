# Consolidation + Product Plan

Status: DRAFT for review (2026-06-02). Nothing deleted yet.

## 1. Why

The repo carries **two parallel stacks** that do the same things:

| Concern | Legacy: `hw_agent/` (75 files) | Pivot target: `hw_toolkit/` (28 files) |
|---|---|---|
| schematic write | `artifacts/schematics/ksa_writer, sch_ops` | `kicad/sch_ops, write` |
| PCB | `pcb_writer, pcb_backend, pcb_render, pcb_ipc` | `kicad/pcb` |
| render | `svg, schem_renderer, render_focus` | `kicad/schem_renderer, cli.render_sch_svg` |
| ERC/DRC | `eval, drc_filters, erc_filters, validators` | `kicad/planner (parse_erc_report)` |
| compose | `system_composer` | `board.Board` |
| models | `core/subsystem, research_bundle` | `core/models` |
| calc | `domain/calculators` | `calc/` |

`mcp_server/{designer,live_edit,router}` is built on **`hw_agent`**, not `hw_toolkit`.
The notebook/library workflow (the pivot) is built on **`hw_toolkit`**.

Goal: **one project, one stack.** `hw_toolkit` is the library; MCP servers ride
it; `hw_agent` deleted. Then build the copilot product on top.

## 2. Target layout

```
hw-toolkit/
├─ hw_toolkit/            # THE library (unchanged role: board, kicad, calc, parts, iface, spice)
├─ mcp/                   # MCP servers, thin, ALL on hw_toolkit (renamed from mcp_server/)
│  ├─ live_edit/          # KiCad IPC live edits      (keep; migrate small deps)
│  ├─ router/             # autoroute (freerouting)   (keep; migrate small deps)
│  └─ designer/           # SLIM rebuild OR retire    (see Decision A)
├─ copilot/               # the product
│  ├─ backend/            # FastAPI: `claude -p` bridge, Jupyter kernel, render, IPC
│  └─ web/                # web UI: notebook pane (top) + claude terminal pane (bottom)
├─ kicad_plugin/          # pcbnew action-plugin launcher (Phase 6)
├─ tools/elk/             # ELK bridge (keep)
├─ docs/
│  ├─ architecture/       # this plan + per-module DESIGN docs
│  └─ projects/           # example notebooks (power_brick, can_servo, sensor_node)
├─ tests/
└─ attic/                 # parked hw_agent research bits (gitignored), pre-delete safety net
```

## 3. Cleanup (Phase 0 — cheap, safe, do first)

Delete / ignore (no code depends on these):
- `.pytest_cache/`, `.ruff_cache/`, `.tmp/`, all `__pycache__/` → add to `.gitignore`.
- Stray render artifacts I generated: `docs/projects/*/*.pcb.svg`, `*.pcb.png`.
- `projects/` (untracked `printed-circuits-sota`) → **eject to its own repo**
  (memory: PnP lives in `ws/smart-printer`, service in `ws/pcb-service`). Not part of hw-toolkit.

Keep: `hw_toolkit/`, `mcp_server/`, `tools/`, `tests/`, `docs/`, `LICENSE`,
`README.md`, `CHANGELOG.md`, `pyproject.toml`, `.claude*`.

## 4. Migration (the real work)

### 4a. Park research-only `hw_agent` modules → `attic/` (Decision B)
These have NO `hw_toolkit` twin and are research/agent-phase, likely out of MVP:
- `artifacts/datasheets/` (downloader, navigator, parser)
- `core/investigator.py`, `core/research_bundle.py`, `core/fab_bundle.py`
- `artifacts/project_state/`
- `domain/templates/`
Move to `attic/` (gitignored) instead of deleting — recover later if needed.

### 4b. Migrate `live_edit` + `router` off `hw_agent` (small)
- `live_edit` imports: `artifacts.schematics`, `render_focus`. Map →
  `hw_toolkit.kicad.{sch_ops, schem_renderer}` (live-edit is mostly raw `kipy`
  IPC; only render/format helpers come from the lib).
- `router` imports: `artifacts.schematics`, `kicad_paths`, `core.freerouting`.
  Map → `hw_toolkit.kicad` for paths/render; **keep `freerouting.py`** (move into
  `mcp/router/` as a local module — it's router-specific, no twin needed).
- Verify each tool still passes `router_check_setup` + a live edit smoke test.

### 4c. Decision A — `designer` MCP fate
`designer` (3,659 lines) is the heavy one and is **largely superseded**: the
product brain is Claude Code writing `hw_toolkit` code (run_python), so it
doesn't need designer's schematic-authoring tools — `hw_toolkit` IS that layer.
Three options:
- **A1 (recommended): slim rebuild.** New `mcp/designer/` exposing only what an
  agent can't do as plain `hw_toolkit` calls — e.g. ERC/DRC gates, focused
  render, BOM. ~10 tools on `hw_toolkit`, drop the rest. Smallest, cleanest.
- **A2: migrate wholesale.** Re-point all ~40 designer tools onto `hw_toolkit`
  twins. Most faithful, most work, keeps research tools (needs 4a un-parked).
- **A3: drop designer entirely.** Product uses Claude Code + `hw_toolkit` direct
  + `live_edit` + `router` only. Leanest; lose file-based authoring tools + BOM/
  datasheet tooling until rebuilt.

### 4d. Delete `hw_agent/`
Once `mcp/*` imports only `hw_toolkit` (+ parked attic), `grep -r hw_agent`
returns nothing → delete `hw_agent/`. Run full `pytest`.

## 5. Product build (after consolidation)

Architecture (locked in chat): **web UI + Python backend; Claude Code is the
brain via `claude -p`; LibreChat/Tauri/KiCad are swappable hosts.**

- **L0 — VS Code shell (today, ~0 build):** workspace opens `copilot.ipynb`
  (top) + terminal running `claude` (bottom); context auto-wraps via root
  `.mcp.json` + `CLAUDE.md`.
- **L1 — KiCad launch:** `kicad_plugin/` pcbnew action plugin button → launches
  the workspace pointed at the open board; live board via `live_edit` IPC.
- **Backend (`copilot/backend/`):** FastAPI — spawn user's `claude -p`
  (BYO-Claude: subscription via CLI / API key via SDK; never proxy OAuth),
  Jupyter kernel, `hw_toolkit` render, MCP wiring, artifact bridge (emit
  `:::artifact{type=image/svg+xml}` so any web host shows the schematic).
- **Web UI (`copilot/web/`):** notebook pane + `xterm` claude pane. Durable
  across hosts (browser / VS Code / Tauri / KiCad `wxWebView`).
- **L2:** wrap web UI in Tauri/Electron (branded desktop) — optional.
- **L3:** embed same web UI in a KiCad `wxWebView` AUI pane (the fork) — truly docked.

Auth rule: subscription works **only** via the `claude` CLI (`claude -p`), not
raw Agent SDK + OAuth (ToS). Per-user, local. Never centrally proxy OAuth.

## 6. Sequencing

1. **Phase 0** cleanup (caches, eject `projects/`, gitignore). ½ day.
2. **Phase 1** migrate `live_edit` + `router` → `hw_toolkit`; rename `mcp_server/`→`mcp/`. 1–2 days.
3. **Phase 2** Decision A → build/retire `designer`. 1–4 days by choice.
4. **Phase 3** delete `hw_agent/`; green `pytest`. ½ day.
5. **Phase 4** product backend (claude bridge + kernel + render + artifact). 2–3 days.
6. **Phase 5** product web UI (notebook + terminal panes). 2–4 days.
7. **Phase 6** KiCad launcher plugin (L1). 1–2 days.
8. **Phase 7** shell (Tauri/Electron) / `wxWebView` panel (L2/L3). later.

## 7. Decisions — LOCKED (2026-06-02)
- **A. designer = A1 slim-rebuild** on `hw_toolkit`. Keep ~10 tools, drop the rest.
  Slim set: `check_erc` (gate), `check_drc`, `render_sch` / `render_pcb` (focused
  SVG), `bom_summary` / `bom_list`, plus the `calc_*` helpers that map to
  `hw_toolkit.calc`. Drop: datasheets (`ds_*`), investigator/research, project_state,
  part/order/q/subsystem/vendor tooling, raw `add_*`/`set_*` (use hw_toolkit directly).
- **B. Research bits → attic** (datasheets, investigator, research/fab bundle,
  project_state, domain/templates). Parked, not deleted.
- **C. Keep name `hw-toolkit` / `hw_toolkit`.** MCP servers move IN → `hw_toolkit/mcp/`.
- **D. Delete `hw_agent/`** once nothing imports it.
- **E.** Consolidate first, product backend after.

## 8. Milestones (committed, branch `consolidate`)
1. **M1 Phase-0 cleanup** — gitignore caches, drop stray render artifacts. ✅ first.
2. **M2 move** `mcp_server/` → `hw_toolkit/mcp/`; fix refs; imports still green.
3. **M3 migrate** `live_edit` + `router` off `hw_agent` → `hw_toolkit` (+ vendor `freerouting`).
4. **M4 slim-rebuild** `designer` on `hw_toolkit` (the ~10 tools above).
5. **M5 delete** `hw_agent/`; `attic/` holds parked research bits; full `pytest` green.
```
