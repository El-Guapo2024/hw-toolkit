# Build Log

Newest first. Each entry: subsystem · decision · reason.

---

### 2026-05-15 · /spec complete — control_hub_v1 load-first lock
- Loads locked: 4× N20 6V geared motors (0.6 A stall), 4× MG90S servos (1.2 A stall), 2× NEMA17 + TMC2209-LA on VBAT 1/16 microstep.
- Sensors locked: LSM6DSOX 6-DoF (BNO055 rejected — 9-DoF uncommon now), VL53L0X bare IC, 4× AS5600 behind TCA9548A, 5× TCRT5000 line array on-PCB, WS2812B status.
- MCU: ESP32-S3-WROOM-1-N16R8 (templated).
- Rail tally: VBAT (TMC2209 direct, fuse ≥6 A), 6 V buck ≥5 A, 5 V buck ≥6 A, 3V3 LDO/buck ≥700 mA.
- Constraints: prototype 1–5 units, hand assembly, $50–60/unit ceiling, mechanical deferred to /pcb.
- Stock sanity: all ICs Active on DK. LSM6DSO**TR** OOS → switched to LSM6DSO**X**TR variant.
- profile.md rewritten load-first format. designer-mcp `subsystem_add` calls deferred until MCP reconnects.

### 2026-05-15 · hooks + sub-agents wired
- Statusline `[hardware-agent] | stage | project` reads `hw_agent/.state.json`.
- Hooks: SessionStart (doctrine), UserPromptSubmit (stage reminder), PostToolUse on `subsystem_choose_part` (auto-append BUILD_LOG, warn-mode).
- Sub-agents `parts-finder` + `parts-specker` (haiku, narrow tool list) added in `hw_agent/.claude/agents/`.
- Doctrine `digikey-primary` adopted (DK > JLC > Mouser for low-vol hand-assembly).
- Memory: `feedback-digikey-primary`, `feedback-announce-workspace` added.

### 2026-05-15 · repo refactor #2 — hw_agent internals
- Reorg `hw_agent/` from 7 loose .py + 14 sibling folders → `core/` (agent logic), `domain/` (calculations, calculators, checks, templates), `artifacts/` (data, datasheets, schematics, project_state, reports), `skills/` (docs), plus `scripts/` + `router/` standalone.
- ~217 import sites rewritten across `hw_agent/`, `mcp_server/`, `tests/`.
- Pipeline view: load-first design doctrine + math-deferral doctrine now codified in skill files.

### 2026-05-15 · repo refactor #1 — MCP modularization + skill folders
- Split MCP server transport into sibling `mcp_server/` package: `designer/`, `router/`, `live_edit/`. Was at `hw_agent/{designer,router,live_edit}_mcp.py`.
- Folderized skills under `hw_agent/skills/`: `spec/`, `designer/` (legacy bodies in here), `designer-math/`, `pcb/`, `router/`, `gtm/`. Each skill self-contained.
- `.claude/` + `.mcp.json` moved into `hw_agent/`; root `.claude/commands` symlinks for CWD-agnostic slash discovery.
- Top-level `pyproject.toml` installs both packages. `pcborder` installed editable from sibling `freight_flow_ai/hardware/pcborder` (added minimal pyproject.toml there).
- Pipeline established: `spec → designer → designer-math → pcb → router → gtm`.

### 2026-05-15 · doctrine — load-first design + Pass 1/2 math split
- Decided: pick loads (motors, servos, sensors, MCU) BEFORE sizing power rails. Rails derived from loads.
- Decided: Pass 1 (selection) skips math entirely. Use datasheet typical-application BOM verbatim. Only calc fb divider if Vout not in datasheet examples.
- Decided: Pass 2 (verify) uses python-control averaged model (Layer 2). SPICE (Layer 3) reserved for ripple/EMI spot-check at final BOM.
- Memory: `feedback-load-first-design-order`, `feedback-designer-narration-style`.

### 2026-05-15 · buck_6v → TPS54620RHLR _(invalidated by doctrine pivot)_
- 11.1V → 6V, 6A sync buck, VQFN-14-EP, JLC C263274 · 10,370 stock · $0.98.
- **Status:** pick stands but was made under wrong doctrine (rails first, before motor MPN locked). To revisit during `/designer` stage of control_hub_v1 once `/spec` finalizes motor + servo MPNs.
- Runner-up TPS564201DDCR ($0.30, SOT-23-6, 4A) likely better Pass 1 pick on margin policy. Decide after spec.

## Picks (auto-logged by post_subsystem_pick.py)

_(hook smoke-test entry removed. Future picks land here automatically.)_
