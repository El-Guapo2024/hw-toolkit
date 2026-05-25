# Module Design — `pcb-designer` agent

> **Self-contained module plan.** A fresh contributor (human or AI) should be able to read this top-to-bottom and implement the agent without needing to read other docs first. References to other modules are pointers, not prerequisites.
>
> **For a fresh Claude implementing this:** start by reading the "Required input artifacts" + "Required output artifacts" sections. Those are the contract. Then read "Internal flow." Skip everything else until you need it.

## Purpose

The `pcb-designer` is the second of two main agents in the hw-toolkit harness. It takes the typed handoff bundle produced by `researcher` — fully-specified subsystems with locked parts, verified math, interface contracts — and turns it into a fabricated-ready PCB design: schematic, layout, route, and fab-house deliverables.

This agent owns everything **from the typed model to gerbers**. It does not pick parts. It does not write requirements. It does not run engineering math. The researcher already did all of that. This agent's job is the physical realization.

## Public contract

### Required input artifacts (from `researcher`)

Will refuse to start unless this exists and pydantic-validates as `ResearchBundle`:

```
docs/projects/<project>/research_bundle.json
```

Schema: `hw_agent/core/research_bundle.py`. One file. Validation failure → structured error, no partial start.

### Required output artifacts (the agent's job to produce)

```
docs/projects/<project>/
├── kicad/
│   ├── <project>.kicad_sch          # generated schematic, ERC clean
│   └── <project>.kicad_pcb          # routed board, DRC clean
└── fab/
    └── rev_A/                       # letter bumps on each successful tag
        ├── gerbers/                 # JLCPCB gerber set + drill
        ├── bom.csv                  # one row per SubsystemPick
        └── cpl.csv                  # placement positions
```

Git tag `<project>/fab-baseline` is the lock. No separate manifest yaml.

### Exit checks before the git tag

Tagged only if all hold:

1. ERC clean (0 real violations).
2. DRC clean (0 real violations).
3. Gerbers + BOM + CPL all exist under `fab/rev_<X>/`.

No separate gate yaml. The checks live in the Phase 4 step list.

## Internal flow

Four phases. Each produces an artifact; failure surfaces a structured error and waits for user direction. Routing, DRC, and fab export run together at the end — the agent does not stop between them unless something fails.

### Phase 1 — Validate input bundle
1. Load `research_bundle.json` via `hw_agent.agents.pcb_designer.load_research_bundle`.
2. If pydantic errors → surface line-by-line and stop. **Do not attempt to fix.**

### Phase 2 — Schematic generation
1. `write_blank_schematic("docs/projects/<proj>/kicad/<proj>.kicad_sch")`.
2. `plan = plan_schematic(r.bundle, sch_path)` → typed op list.
3. Execute ops via `add_custom_ic` / `add_power` / `add_ground` / `add_wire`.
4. Run KiCad CLI ERC → parse via `parse_erc_report`.
5. Iterate until `erc.real_count == 0` or user says "ship it".

### Phase 3 — Placement
1. `plan = plan_placement(r.bundle, sch_path)` → MoveSymbol ops.
2. Dispatch each via `live_move_symbol`. Last op carries `with_render=True`.
3. Tell user to press ⌘S in eeschema (IPC `SaveDocument` not wired in 10.99).

### Phase 4 — PCB export (route + fab, automatic)
1. Call `pcb_route` — engine defaults to `freerouting-hosted`. Auto-fallback to `orthoroute` if it fails. On both-fail: surface unrouted nets and stop.
2. Run KiCad CLI DRC. Real violations > 0 → surface and stop.
3. Call `pcb_export_fabrication` → gerbers + drills under `fab/rev_<A>/gerbers/`.
4. Call `pcb_export_bom` → BOM CSV from `SubsystemPick.mpn` rows.
5. Write CPL CSV from `.kicad_pcb` placement.
6. Tag `<project>/fab-baseline` via `git tag`.

No separate "lock" phase — git tag is the lock.

## Tool whitelist

Allowed:
- `Read`, `Edit`, `Write`, `Bash` (limited to KiCad CLI + git tag)
- `mcp__designer-mcp__system_export_kicad`, `kicad_export_schem`, `pcb_save`
- `mcp__designer-mcp__eval_subsystem`, `pcb_ipc_status`, `pcb_export_fabrication`, `pcb_export_bom`
- `mcp__designer-mcp__pcborder_*` (vendor validation)
- `mcp__designer-mcp__move_footprint`, `set_footprint`, `list_pcb_footprints`
- `mcp__designer-mcp__find_kicad_lib`, `install_kicad_lib`, `list_installed_libs`
- `mcp__designer-mcp__get_render` (schematic preview)
- `mcp__designer-mcp__design_view`, `design_state`, `project_status` (state queries)
- `mcp__designer-mcp__calc_trace_width`, `calc_microstrip_z0`, `calc_stripline_z0`, `calc_via_inductance`
- `mcp__live-edit-mcp__*` (live KiCad mutations, if eeschema/pcbnew open)
- `mcp__router-mcp__*` (routing)
- `mcp__pcbparts__digikey_get_part`, `jlc_stock_check`, `jlc_get_part`, `mouser_get_part`

Explicitly **blocked** (PreToolUse hook rejects):
- ❌ `mcp__designer-mcp__subsystem_*` mutations (researcher's job)
- ❌ `mcp__designer-mcp__analyze_candidate`, `subsystem_choose_part` (researcher's job)
- ❌ `mcp__designer-mcp__q_*`, `ds_*` (researcher's job)
- ❌ `mcp__pcbparts__jlc_search`, `sensor_recommend`, `board_search` (research / part-discovery — researcher's job)
- ❌ `AskUserQuestion` for spec / load / part-pick (those are locked from research baseline)

## Self-injection bundle (auto-emitted by SessionStart hook)

```
<system-reminder>
## PCB-DESIGNER ACTIVE — project=<name> · phase=<validate|schematic|placement|routing|drc|fab|lock>

## Doctrine for this phase
- truth_vs_view: KiCad fields are GENERATED from chosen_part. Never hand-author MPN/LCSC in eeschema.
- baseline_readonly: research-baseline is locked. Do not propose part swaps; surface to user, do not act.
- one_rev_per_build: each baseline lock increments rev letter; never overwrite prior gerbers.
- (additional sourced from library/doctrine/pcb/<phase>.yaml)

## Current state
- Input bundle valid: <yes|no — surface invalid if no>
- Phase complete: validate=<bool>, schematic=<bool>, placement=<bool>, routing=<bool>, drc=<bool>, fab=<bool>
- Last ERC: <N real / M expected>
- Last DRC: <N real / M expected>
- Next rev letter: <A|B|C|...>

## Required outputs (cannot exit pcb-designer mode without)
- kicad/*.kicad_sch (ERC clean)
- kicad/*.kicad_pcb (DRC clean)
- fab/rev_<X>/{gerbers/, bom.csv, cpl.csv, manifest.yaml}
- baselines/fab.yaml + git tag

## Tools allowed (others blocked at call site)
- <tool list per phase>

## Hard rules
- If input bundle invalid: STOP, emit researcher-handoff-error. Do NOT attempt to fix research artifacts.
- KiCad symbol fields are derived. If they disagree with chosen_part.mpn: regenerate, do not edit symbol field.
- Never bump rev letter without ready_to_fab PASS.
- ERC/DRC violations must be resolved before fab export.

## Exit condition
- All required outputs exist + ready_to_fab gate PASS.
- Then: lock baseline (git tag <project>/fab-baseline) and tell user fab bundle is ready.
</system-reminder>
```

## Failure modes

| failure | behavior |
|---|---|
| Researcher handoff invalid | Emit structured error pointing at missing/broken artifact. **Do not attempt to fix it** — that's researcher's job. Tell user to re-invoke `/researcher`. |
| KiCad CLI missing | Error: "kicad-cli not found at $KICAD_CLI or PATH"; suggest install. No fallback. |
| ERC produces real violations | Iterate via live-edit-mcp tools or, for structural issues, surface to user with refdes-level detail. |
| Router fails on freerouting-hosted | Try orthoroute. If both fail: surface unrouted nets, suggest density reduction. |
| DRC fails after routing | Loop back to placement (Phase 3) — placement is usually the root cause; never reroute over bad placement. |
| Vendor rule fail (PCBOrder) | Surface the rule + offending feature + suggested fix; iterate. |
| Stock fail on a chosen_part | **Do not swap parts** — surface to user with stock snapshot. User decides: wait, change qty, or re-invoke researcher for a swap. |
| Out-of-scope tool call | PreToolUse hook rejects with reason; agent reads + adjusts. |
| Schematic field disagrees with chosen_part | Regenerate `.kicad_sch` (fields are derived). Never edit the field in eeschema. |

## Performance characteristics

- Phase 1 (validate): <2 sec — pure pydantic load.
- Phase 2 (schematic gen): 5-30 sec — `system_export_kicad` + ERC.
- Phase 3 (placement): manual / agent-iterative — 5-30 min depending on board complexity.
- Phase 4 (routing): 18-60 sec for freerouting-hosted on a 7-subsystem board.
- Phase 5 (DRC): 5-15 sec — KiCad CLI.
- Phase 6 (fab export): 10-30 sec — gerber gen + BOM + CPL + vendor check.
- Phase 7 (lock baseline): <1 sec — git tag + yaml write.
- Total: 15-60 min wall clock for a fresh 7-subsystem project. Mostly placement.

## Testing

- `hw_agent/agents/tests/test_pcb_designer_input_validation.py` — feed deliberately-broken handoff bundles, verify each is rejected with the right error class.
- `hw_agent/agents/tests/test_pcb_designer_smoke.py` — full pipeline on `control_hub_v1`-fixture; assert all output artifacts exist + gate passes.
- Contract test: round-trip — researcher's output bundle parses cleanly into pcb-designer's input loader without any field loss.

## Dependencies

**Imports / depends on:**
- `hw_agent.core` — pydantic models (Subsystem, InterfaceRecord, N2Matrix, EEResult, Baseline, ProjectManifest)
- `hw_agent.core.converters.{kicad_fields,bom_csv}` — derive KiCad fields + BOM rows from typed models
- `hw_agent.library/doctrine/pcb/*.yaml` — per-phase doctrine
- `hw_agent.library/gates/ready_to_fab.yaml` — final gate
- `hw_agent.scripts.hooks.inject_*` — injection scripts (harness-fired)
- MCP servers: `designer-mcp` (schematic + PCB tools), `live-edit-mcp` (live KiCad mutations), `router-mcp` (routing), `pcbparts` (stock check only)
- KiCad CLI binary
- FreeRouting / OrthoRoute services (via `router-mcp`)

**Does NOT depend on:**
- `researcher` agent (no direct calls — communicates via artifact files only)
- `parts-finder` sub-agent (researcher's tool)
- `ee.facade` (math runs in researcher; pcb-designer reads results, doesn't generate new ones)

## Open questions / known limitations

1. **Placement strategy.** Initial placement is heuristic; for complex boards, manual eeschema work is unavoidable. Agent should support iterative placement-refinement loop.
2. **Multi-board projects.** Today scoped to one PCB. Multi-board needs nested or sibling agent invocations. Defer.
3. **Rev-letter conflict resolution.** If two builds happen on same day, both might land at `rev_A` if `fab/` is wiped. Convention: never reuse a letter once tagged.
4. **Live-edit-mcp coupling.** Requires KiCad open with API server enabled. If headless: live mutations fail with explicit error. CI runs schematic gen + DRC only (no placement iteration).
5. **Footprint library coverage.** Some chosen parts may lack KiCad footprints in installed libs. Agent must call `find_kicad_lib` / `install_kicad_lib` proactively. Failure mode: surface "no footprint for refdes X (MPN Y)" — never fabricate one.
6. **Vendor lock.** Default = JLCPCB. PCBWay / OSHPark / Aisler need their own rule sets in `pcborder_settings`. Add per vendor request.
7. **Cost budget.** Phase 3 (placement) can blow context if it goes 100 turns. Add `max_turns_per_phase` config; surface at 80% budget.
8. **Bring-up gap.** After fab, physical board arrives → bring-up data flows into `bringup.yaml` per researcher's rev-B input. pcb-designer is **not** involved in bring-up.

## Bootstrap notes for a fresh implementation

If a fresh Claude session is reading this to implement the agent from scratch:

1. Start by reading `../../investigations/typed-core-spec.md` — the pydantic models you'll be loading.
2. Then read this file's "Public contract" section twice — that's the API.
3. Skim "Internal flow" once for the phase structure; don't memorize.
4. Implement Phase 1 first — input validation. Test it against `docs/projects/control_hub_v1/` once researcher has populated it.
5. Implement Phase 2 (schematic gen) next — verify ERC works on the generated `.kicad_sch`.
6. Then 3-6 in order. Each phase is mergeable alone.
7. Phase 7 (baseline lock) is the trivial wrap-up.
8. Write the agent `.md` frontmatter at `.claude/agents/pcb-designer.md` last — it just declares model/tools/description.

Don't try to implement all 7 phases in one PR. Each is its own commit.

## Related

- `researcher.md` — producer of this agent's input bundle
- `../../investigations/typed-core-spec.md` — pydantic contracts the artifacts conform to
- `../../investigations/harness-injection-deep-dive.md` — how doctrine swaps per phase
- `library/doctrine/pcb/*.yaml` — actual doctrine snippets (to be authored as part of implementation)
- `library/gates/ready_to_fab.yaml` — gate definition (to be authored)

## Status

`planned` — depends on typed core (P-1), researcher agent (must produce valid bundle), pipeline/injection skeleton (P0.3/P0.5), doctrine library (P0.6). Owner: TBD. Last updated: 2026-05-24.
