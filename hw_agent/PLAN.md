# hw_agent Improvement Plan

> **Coordinate with `docs/DIRECTION.md`.** That doc holds the
> three-phase ship structure (research → schematic autogen → PCB live
> edit), architecture commitments, and long-term tracks. This plan
> covers tactical MCP-surface improvements that fit *inside* those
> phases. Where "Phase X" is referenced below, it's a local label for
> incremental MCP-surface work, **not** the same as DIRECTION's ship
> phases. If guidance here conflicts with DIRECTION, DIRECTION wins.

Phased plan distilled from researching how Anthropic's CAD/creative-tool connectors (Autodesk Fusion, Blender, Figma — all April 2026) function. The point is to apply the patterns those connectors converged on to `hw_agent`'s MCP surface.

`hw_agent` stays a **local stdio MCP server**. We are NOT pursuing public-connector deployment (HTTPS + OAuth + cloud hosting) — that's a distribution play, not what's needed for an engineer building tools for themselves. The convergent design patterns from those connectors apply regardless of transport.

## Connector design patterns we're stealing

1. **Screenshots as a first-class read primitive.** Blender's `get_viewport_image`, Figma's `get_screenshot` — every mature connector lets the agent "look at" current state any time. The AI is blind without it.
2. **Asymmetric read/write.** Reads are free, side-effect-free, repeatable. Writes are explicit and return confirmation + new state image so the agent can verify.
3. **Batch mutation god tool.** Figma's `use_figma` accepts an array of mutations in one call. Cuts agent round-trips; lets the agent compose multi-step intent in a single shot.
4. **Dual representation in returns.** Image + structured data (Figma's `get_design_context`). Lets the agent reason both visually and semantically.
5. **Project-level scoping.** Auth implicit (running locally), one project at a time.

Patterns we're explicitly NOT stealing:
- Public connector deployment (HTTPS + OAuth).
- Arbitrary code execution (Blender's escape hatch — footgun).
- Semantic tool-name refactor (defer until tool count exceeds ~100).

---

## Phase A — DONE (2026-05-01)

Rich content returns + markdown summaries.

- `svg_*` tools (`svg_buck`, `svg_ldo`, `svg_motor_driver`, `svg_voltage_divider`, `schem_system`) return `[text, Image(png)]` inline.
- `eval_subsystem`, `kicad_eval` return `[markdown ERC report, schematic PNG]`.
- `system_export_kicad` runs `kicad-cli sch export svg` on the composed root and returns `[text, image]`.
- Summary tools (`bom_summary`, `decision_list`, `design_summary`, `verifications_list`) return markdown tables instead of raw dicts.
- Helpers in `mcp_server.py`: `_svg_path_to_image`, `_kicad_sch_to_svg`, `_format_erc_markdown`, `_format_bom_summary_md`, `_format_decisions_md`, `_format_design_summary_md`, `_format_verifications_md`.
- `mcp__designer-mcp__*` / `mcp__live-edit-mcp__*` / `mcp__router-mcp__*` wildcards added to `.claude/settings.json` so all current and future MCP tools auto-allow without prompts.

## Mermaid removal — DONE (2026-05-02)

KiCad is now the source of truth for schematic rendering (via `kicad-cli sch export svg`, surfaced through `design_view` + `eval_subsystem`). The parallel Mermaid path is fully removed:

- 8 `schem_*` Mermaid MCP tools deleted (`schem_buck`, `schem_ldo`, `schem_motor_subsystem`, `schem_servo_subsystem`, `schem_stepper`, `schem_i2c_bus`, `schem_power_flow`, `schem_voltage_divider`).
- `project_render` MCP tool deleted (replaced by `design_view`).
- `svg_from_schem` MCP tool deleted (redundant with `eval_subsystem` / `design_view` since KiCad renders).
- `Project.render_power_flow`, `render_i2c_topology`, `render_full_system`, `render_component_pins` methods deleted from `schematics/model.py`.
- `hw_agent/schematics/mermaid.py` module deleted.
- `schematics/__init__.py` re-exports replaced with a clean module-listing docstring.
- `schem_renderer` (Pydantic Schematic model + render_schematic function) **kept** — used internally by preview, circuit_builder, json_ops, system_composer, kicad_writer, pcb_writer.
- Tool count: 71 (down from 75 + parallel agent additions).

## Phase I + J — Pydantic-driven subsystem pipeline — DONE (2026-05-03)

Rebuilt the per-candidate verification flow around Pydantic Subsystem classes + a reusable check library + a unified pipeline. Replaces the old dataclass `ComponentTemplate` + JSON questionnaires + caller-supplied check lists.

**Architecture:**
```
hw_agent/
├── subsystem.py                 ← ElectronicSubsystem base + SubsystemStatus + ProjectStatus
├── checks/                      ← Reusable check library (pure functions)
│   ├── electrical.py            (vin_range, iout_capability, dropout_headroom, switching_freq_in_range,
│   │                             quiescent_current_reasonable, supply_voltage_compatible)
│   ├── thermal.py               (junction_temperature)
│   ├── mechanical.py            (package_suitable)
│   ├── supply.py                (stock_threshold)
│   ├── digital.py               (gpio_count_sufficient, peripherals_sufficient, memory_sufficient,
│   │                             clock_speed_sufficient, wireless_protocol_present)
│   ├── sensor.py                (axes_sufficient, accel_range_sufficient, gyro_range_sufficient,
│   │                             interface_supported)
│   └── motor.py                 (vm_range_covers, channel_count_sufficient,
│                                 per_channel_current_capable, microstep_capable)
├── calculations/                ← Math libraries, called by templates
│   ├── ldo.py                   (thermal_analysis)
│   ├── buck.py                  (inductor_value, output_cap_value, thermal_estimate)
│   └── motor_driver.py          (thermal_estimate)
└── templates/                   ← One Pydantic Subsystem class per component
    ├── ldo.py / buck.py / mcu_ble.py / motor_driver.py / imu.py /
    ├── pwm_servo_driver.py / stepper_driver.py
    ├── base.py                  (SpecDefinition, SearchCriteria, Requirement — kept)
    └── __init__.py              (SUBSYSTEM_REGISTRY: dict[category, type[ElectronicSubsystem]])
```

**Key behaviors:**
- Each Subsystem has a `Requirements` model (engineer answers, validated) and an `Actuals` model (extracted datasheet specs, all Optional).
- `subsystem.status()` runs the check pipeline. Each check returns `pass`/`fail`/`missing` — never throws on incomplete data. The `missing` list IS the agent's todo: tells the agent exactly which datasheet fields to extract next.
- `ProjectStatus.aggregate(...)` walks all subsystems, surfaces per-project blocking failures and a unified data-needed list.
- Pydantic gives free serialization (`model_dump_json` / `model_validate_json`) for persisting subsystem state.

**Calculations + checks share a unified contract:** `(actual_specs: dict, required_inputs: dict) → result`. No component-specific dispatch in the orchestrator.

**MCP tool changes:**
- `verify_candidate(project, subsystem, lcsc, requirements, actuals, mpn)` — new signature. Looks up the Subsystem class, builds an instance, runs `.status()`, returns markdown PASS/FAIL/MISSING table + persists.
- `q_load(component_type)` — returns Pydantic JSON Schema for the Requirements model + prior_art_searches + ai_instructions.
- `q_validate(component_type, answers)` — uses Pydantic validation; returns structured errors.
- `q_searches(component_type, answers)` — substitutes answers into `cls.searches` query templates.
- `q_derive` — alias for `q_validate` (Pydantic does coercion + defaults).
- `q_prior_art_hints(component_type)` — returns `cls.prior_art_searches`.
- `q_save` / `q_add_question` — return error messages (dynamic creation no longer supported; edit the Pydantic class).

**Deletions:**
- `hw_agent/questionnaires/` directory (7 JSON files + `schema.py`).
- Old dataclass-based `ComponentTemplate` instance pattern (replaced by Subsystem classes; `base.py` keeps the dataclasses still used).

**Tool count: 72** (steady — net zero from migration).

**Smoke test result:** 4-subsystem project (buck + ldo + mcu_ble + imu); buck and ldo had datasheet actuals, mcu and imu didn't. Pipeline ran clean: 1 READY, 3 IN PROGRESS, 16 missing fields aggregated, 0 blocking failures. Pydantic JSON roundtrip verified — 1723 bytes for the 4 subsystems, reload status matches original.

## Phase K — Subsystem persistence + simplification — DONE (2026-05-03)

**Single source of truth: `subsystems/<name>.json` per project.** ChosenPart and Decision moved INTO the subsystem JSON. The 5 redundant state files (`bom.json`, `decisions.json`, `design.yaml`, `design.json`, `verifications.json`) are gone — BOM/decision history are now derivable from subsystems on demand.

**On-disk layout for an investigation:**
```
docs/projects/<project>/
├── subsystems/
│   └── <name>.json          ← canonical (requirements + actuals + chosen_part + decisions[])
└── components/<name>/        ← datasheets, schematic SVGs, notes (artifacts)
```

**MCP tool changes:**
- `subsystem_add(project, category, name, requirements)` — creates subsystem; returns project_status.
- `subsystem_update_actuals(project, name, actuals)` — merges extracted specs; returns project_status.
- `subsystem_choose_part(project, name, lcsc, mpn, price, datasheet_url, ...)` — single tool to commit a part choice. Writes ChosenPart + appends Decision. Replaces the old `explore_record` + `bom_add` + `decision_add` + `design_update` chain.
- `subsystem_remove(project, name)` — deletes subsystem.
- `subsystem_status(project, name)` — single subsystem status (markdown).
- `project_status(project)` — aggregated status across all subsystems (markdown).
- `verify_candidate(project, name, actuals_proposed)` — REWRITTEN as hypothetical, non-persisting. Returns "what if we picked this" status. No verifications.json.
- `bom_list(project)` / `bom_summary(project)` — REWRITTEN as derived views. No bom.json writes.

**Tool deletions (16 redundant tools):**
- BOM writes: `bom_add`, `bom_remove`
- Decision tools: `decision_add`, `decision_list`, `verifications_list`
- Design.yaml tools: `design_update`, `design_update_rail`, `design_summary`, `design_sanity_check`
- Project-model tools: `project_load`, `project_add_component`, `project_add_power_rail`, `project_add_i2c_bus`, `project_check`
- Composite: `explore_record`

**Module deletions:**
- `hw_agent/project_state/bom.py` (write paths)
- `hw_agent/project_state/decisions.py`
- `hw_agent/project_state/verify.py`
- `hw_agent/project_state/sanity.py`
- `hw_agent/project_state/design.py`
- `hw_agent/schematics/model.py` (Project class)

**Pydantic additions to `subsystem.py`:**
- `ChosenPart` (lcsc, mpn, manufacturer, description, package, price, price_tiers, stock, datasheet_url, library_type, qty_per_board, notes)
- `Decision` (timestamp, chosen, rejected, rationale, tradeoffs, alternate_lcsc, requirements_snapshot)
- `ElectronicSubsystem.chosen_part: Optional[ChosenPart]`
- `ElectronicSubsystem.decisions: list[Decision]`
- `ElectronicSubsystem.with_chosen_part(chosen, decision=None)` immutable updater

**Tool count: 64** (down from 77). Server boots cleanly.

**Smoke test passed:** end-to-end flow (add buck → update actuals → hypothetical verify with low stock → commit choice → BOM derived correctly → no orphan state files).

**Architectural rationale:**
- Investigation phase (this agent) only needs Pydantic subsystem state. Rails / pin pools / I2C topology are schematic-phase concerns owned by a different agent.
- Single source of truth means BOM and decision history can never drift out of sync with the actual subsystem state.
- Adding a new commercial field (e.g., RoHS status, lead time) is one Pydantic field, no new files or tools.
- See `docs/DIRECTION.md` for the broader phase split (research → schematic autogen → PCB live edit).

## Phase B — partially shipped

Vision feedback at any time, plus progress streaming.

| # | Tool / change | What it does | Effort | Owner |
|---|---|---|---|---|
| B1 | `design_view(project, view)` | Returns inline PNG + structured state. Views: `schematic`, `system`, `subsystem:<name>`, `pcb`, `pcb:<name>`, `system_pcb`. Wraps `render_system_schematic`, `kicad-cli sch export svg`, `kicad-cli pcb export svg`, etc. — wrapper, not new rendering. | ½ day | **PCB agent — SHIPPED 2026-05-02**. All 4 view shapes work; agent gets `[markdown ERC/DRC summary, Image(png)]`. |
| B2 | `design_state(project)` | Pure-data peer to B1. Returns design.yaml + BOM totals + ERC pass/fail + PCB state (footprints assigned, DRC pass/fail, fabrication_ready). Cheap to call. | hour | **PCB agent — SHIPPED 2026-05-02**. 83 ms on 10-subsystem project. Reads cached eval files; no subprocess. |
| B3 | Progress streaming via `ctx.report_progress` | For `run_investigation`, `eval_subsystem`, `system_export_kicad`, `design_sanity_check`, `pcb_route`. Engineer watches Phase 1 unfold instead of seeing one big result block. `pcb_route` is highest-value: 18-60s today with no feedback. | ½ day | **PCB agent — `pcb_route` + `eval_subsystem` SHIPPED 2026-05-02**; others (run_investigation, system_export_kicad, design_sanity_check) unclaimed |

## Phase C — read/write separation + last-mile markdown

| # | Change | Why | Effort |
|---|---|---|---|
| C1 | Tag every tool docstring `[read]` / `[write]` / `[compute]` | Helps agent (and humans) understand surface; zero behavior change | hours |
| C2 | `verify_candidate` returns markdown PASS/FAIL table + structured | Engineer sees verification at a glance instead of raw dict | 30 min |
| C3 | `explore_record` returns one-screen confirmation instead of 4-key dict | Currently noisy; should look like "✓ Added TPS62933 to BOM, decision logged, design.yaml updated" | 30 min |
| C4 | `q_load` adds optional markdown view alongside structured | Engineer can read the questionnaire instead of parsing JSON | hour |

## Phase D — defer until pain

| # | Change | Pattern | Notes |
|---|---|---|---|
| D1 | `design_modify(project, ops=[...])` | Figma's `use_figma` god tool | Wait until agent makes 5+ small calls in a row to assemble a subsystem. `explore_record` already covers the most common batch case. |

## Phase E — long-term direction (live human + agent in KiCad)

The end-state vision: human and agent operate on the same live KiCad document, not file-roundtrip. Phase 1 (headless bootstrap) stays as the way to populate an empty project; Phase 2 (live IPC) takes over once content exists.

**E1 spike result (2026-05-02): RED.** Schematic IPC does not exist on KiCad 9. Verified facts:

- kicad-python 0.7.1 (current PyPI release) has no `Schematic` class.
- KiCad 9.0 protobuf `schematic_commands.proto` is empty (license header only) — the running KiCad has no IPC handler for any schematic RPC.
- Upstream `main` branch added `kipy/schematic.py` on 2026-04-17, but every class is tagged `versionadded:: 0.x.y (KiCad 11)`.
- No `run_erc` method anywhere on any branch (master included).
- Mixelpixx confirms in-the-wild: their IPC backend is board-only.
- Tracking issues `#40`, `#103` open through entire 9.x series.

**Implication: E2 / E3 are blocked until KiCad 11 ships** (rough estimate ~2027 based on cadence). E4 (VLM feedback) is independent and can proceed via KiCanvas + kicad-cli SVG (already working).

**For now, stay file-based.** The schematic-side pivot to direct `.kicad_sch` editing (other agent's lane) is the right move because it operates at the file layer where KiCad 9 fully supports us. When KiCad 11 ships, re-spike E1; if the API matures, then E2.

| # | Change | Notes | Effort | Status |
|---|---|---|---|---|
| E1 | KiCad 9 IPC spike (1-day de-risk) | Validate `add_symbol` / `connect` / `list` / `run_erc` work for **schematics**. | 1 day | **RED — DONE 2026-05-02. Schematic IPC blocked at KiCad version (KiCad 11 required).** |
| E2 | KiCad IPC writer alongside `kicad_writer.py` | Same MCP tool surface, new backend. | week | **BLOCKED on E1 / KiCad 11 release** |
| E3 | KiCad plugin "side pane" with status + activity log | Ambient state (current activity, per-subsystem traffic light, click-symbol-to-see-rationale). | week+ | **BLOCKED on E2** |
| E4 | VLM feedback loop in agent | Agent reads back `design_view` PNG with vision capability, reasons visually like a human. Catches "agent placed two symbols overlapping" / "wires routed weirdly" before they cause ERC failures. | ongoing | **UNBLOCKED — independent of IPC.** Can proceed using KiCanvas / kicad-cli SVG renders (already produced by `design_view`). |
| E5 | Filesystem watcher for human edits | `watchdog` on `.kicad_sch`, parse via `kicad-skip` when human saves in eeschema, re-sync agent's view of state. Workaround for E2 being blocked. | ½ day | **UNBLOCKED — replaces E2 until KiCad 11.** Pairs naturally with the existing `preview.py` watcher daemon. |

## Phase F — explicitly NOT doing

- **Public connector deployment** (HTTPS + OAuth + Vercel/Railway). Distribution play. We hit the OAuth rabbit hole already with freightflow on 2026-05-01/02 — that's exactly this path failing.
- **Semantic tool-name refactor** while tool count <100.
- **Arbitrary code execution tool** (Blender's escape hatch).

## Phase G — Research Dashboard (local web UI)

Research is a **separable mode** from schematic/PCB editing — it has zero KiCad dependency. It uses datasheets, parts databases (`pcbparts` MCP), internal state files, and calculations. So it can have its own UI surface independent of KiCad.

We already have the foundation: `hw_agent/preview.py` is a 778-line live-reload web server with SSE broadcasts, file-watching, and browser refresh. Extend it into a research dashboard the engineer keeps open as a browser tab alongside Claude Code (or any chat client).

**Architecture:**

```
Claude Code (chat) ─── MCP ─── hw-agent server ─── writes ─── state files
                                                                 │
                                              SSE / file-watch ──┘
                                                                 ▼
                                              Browser tab @ localhost:8770
                                              (research dashboard)
```

The agent calls existing tools that write to state files; the dashboard subscribes to those files and re-renders. The agent doesn't need to know the dashboard exists — it just keeps writing well-structured state.

**Why this beats betting on MCP Apps for research:**
- Works with **any** chat client (Claude Code, Cursor, claude.ai web, vim, no chat at all). MCP Apps locks the engineer to Claude Code / Cursor / VS Code.
- preview.py is already half the work. Adding tabs and state subscriptions is incremental.
- Hardware-specific UX flexibility — datasheet PDF viewer, schematic SVGs, BOM filters — is wide open. MCP Apps constrains us to its component model.
- Independent of Anthropic's MCP Apps roadmap stability.

**Same pattern Figma actually uses:** chat in IDE + Figma in another tab. Two surfaces, complementary roles.

| # | Tab / panel | Content | Effort |
|---|---|---|---|
| G1 | **Candidates** | Sortable table of parts under consideration: LCSC, MPN, $/board, stock, package, spec scores. Filter, sort, pin/unpin, mark eliminated with reason. | 1 day |
| G2 | **Compare** | Side-by-side spec table for 2–4 selected candidates. Pick a winner; agent reads the selection on next turn. | ½ day |
| G3 | **Datasheets** | Currently-loaded PDFs with bookmarks the agent built. Click to navigate; agent's spec extractions pinned to pages. | 1 day |
| G4 | **BOM** | Running total, cost at 1/10/100, supply risk, drill-into subsystems, assembly fees. | ½ day |
| G5 | **Decisions** | Per-subsystem chosen + rationale + rejected. Click a part to jump to its datasheet. | ½ day |
| G6 | **Constraints** | Power / thermal / pin margins with proximity warnings. Highlights subsystems near limits. | ½ day |
| G7 | **Notes** | Research notes the agent wrote (per R4 / R-series in research-flow plan). Engineer can also add manual notes; agent reads them on next turn. | hour |
| G8 | **Live updates** | SSE push from state-file changes so all tabs auto-refresh as the agent writes. | ½ day (extends existing preview.py SSE) |

Total: 4–5 days for a useful first version. Deliver as `python -m hw_agent.dashboard <project>` (parallel command to existing `hw_agent.preview`).

## Phase H — MCP Apps overlays (optional polish, depends on G + protocol stability)

If MCP Apps stabilizes and engineers want richer-than-markdown content **inside the chat itself** (not just the side dashboard), add interactive components. Lower priority because:
- Phase G dashboard already covers the same use cases for any client.
- MCP Apps protocol still has stability questions (subagent research conflicts on whether it ships today).
- Lock-in to Claude Code / Cursor / VS Code.

| # | MCP Apps component | Replaces | Effort |
|---|---|---|---|
| H1 | **Verify protocol stability** | n/a — gate for everything below | 1 day spike |
| H2 | **Candidate comparison cards** in chat | "agent narrates comparison in prose" | 1 day |
| H3 | **Click-to-approve part picks** | "agent asks 'should I add this?' in prose" | ½ day |
| H4 | **Parameter sliders** for calc tools | "re-prompt the agent for each value" | 1 day |
| H5 | **Filtered BOM browser** in chat | "agent re-runs `bom_summary` with different params" | ½ day |
| H6 | **ERC issue browser** in chat | markdown table with no click-through | 1 day |

Don't start H until G is shipping value and the engineer reports the dashboard isn't enough.

---

## Recommended next-action order

1. **B1 `design_view`** — biggest leap in agent capability per unit effort. Once the agent can "look around" any time, exploration gets dramatically more reliable.
2. **B3 progress streaming** (in progress by PCB agent for `pcb_route`) — finishes Phase B; smooth UX during long ops.
3. **C2 + C3** — quick wins; tighten the exploration outputs that the user actively tests in chat.
4. **G1 + G2 + G8** — the candidates+compare+live-updates slice of the research dashboard. Single biggest UX leap for the research flow.
5. **B2 + C1 + C4** — round out the read surface and read/write clarity.
6. **G3–G7** — fill out the dashboard tabs as needed.
7. **E1 spike** — kick off in parallel; gate for the long-term vision regardless of where Phase B–D land.
8. **H1 spike** (optional) — only if engineer wants in-chat interactive UI on top of the dashboard.

## Patterns reference

Sources for the connector patterns above (re-check when patterns drift):

- https://www.anthropic.com/news/claude-for-creative-work
- https://aps.autodesk.com/blog/bringing-fusion-claude-creative-work
- https://help.figma.com/hc/en-us/articles/32132100833559-Guide-to-the-Figma-MCP-server
- https://developers.figma.com/docs/figma-mcp-server/tools-and-prompts/
- https://www.blender.org/lab/mcp-server/
- https://claude.com/resources/tutorials/using-the-blender-connector-in-claude
