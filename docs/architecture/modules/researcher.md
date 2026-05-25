# Module Design — `researcher` agent

> **Self-contained module plan.** A fresh contributor (human or AI) should be able to read this top-to-bottom and implement the agent without needing to read other docs first. References to other modules are pointers, not prerequisites.

## Purpose

The `researcher` is one of two main agents in the hw-toolkit harness. Its job is to take a user's hardware idea, turn it into a fully-specified, math-verified, parts-locked design — and produce a typed artifact bundle that the `pcb-designer` agent consumes.

This agent owns everything **before** schematic capture. It does not generate `.kicad_sch` files. It does not place footprints. It does not route. It produces the **typed data** that lets the `pcb-designer` do those things.

## Public contract — handoff to `pcb-designer`

This is the **strong contract** between the two stages. The `pcb-designer` agent will refuse to start until all of this is present and validates.

### Required output artifacts (the handoff bundle)

```
docs/projects/<project>/
├── profile.md                          # human-readable summary
├── n2_matrix.yaml                      # N2Matrix (typed)
├── interfaces/                         # one file per interface contract
│   ├── rail_<name>.yaml                # InterfaceRecord, power type
│   ├── i2c_<name>.yaml                 # InterfaceRecord, protocol type
│   └── ...
├── subsystems/                         # one file per subsystem
│   ├── <subsystem_id>.yaml             # Subsystem (typed), with:
│   │   - requirements set
│   │   - actuals populated (every value carries provenance)
│   │   - chosen_part set
│   │   - port_bindings set (every required port has interface_id)
│   │   - calculations: all hard checks PASS
│   │   - decisions: at least one Decision recorded
│   └── ...
├── results/                            # one EEResult per check
│   └── <subsystem>_<check>_<ts>.yaml   # EEResult (typed), passed=true
├── manifest.yaml                       # ProjectManifest (typed)
└── baselines/
    └── research.yaml                   # Baseline record — locked when gate passes
```

### Contract enforcement

- All yaml files validate against pydantic models in `hw_agent/core/`.
- All actuals are `TrackedValue` (value + provenance, no bare floats).
- All interface IDs in `n2_matrix.edges[].interface` exist as `interfaces/<id>.yaml` files.
- All `port_bindings[].interface_id` reference existing interface files.
- All hard check rules in each subsystem's template produce PASS results.
- `research_to_pcb` gate (defined in `hw_agent/library/gates/research_to_pcb.yaml`) passes.

If any of the above is violated, `pcb-designer` refuses to start. No partial handoffs.

## Sub-stages within the agent

The researcher operates in 4 sub-stages, driven by project state. Injection swaps doctrine per sub-stage.

| sub-stage | trigger (state condition) | doctrine | exit when |
|---|---|---|---|
| **intake** | `profile.md` does not exist | `intake.yaml` (load-first, narration-one-at-a-time, ask-before-pick) | `profile.md` written + N2 sketched + user confirms |
| **interface authoring** | `profile.md` exists, `interfaces/` empty | `interfaces.yaml` (one-file-per-interface, current_budget check) | every N2 edge has matching `interfaces/<id>.yaml` |
| **part selection** | interfaces complete, ≥1 subsystem missing `chosen_part` | `selection.yaml` (digikey-primary, pass1-no-math, thermal-gate-hard) | every subsystem has `chosen_part` |
| **verification** | every subsystem has `chosen_part`, calculations empty | `verification.yaml` (cheap-before-expensive, fail-reject-don't-retry) | every hard check passes for every subsystem |

Sub-stage transitions are detected by the harness — researcher doesn't decide which mode it's in; the project state does. Injection block updates accordingly each turn.

## Tool whitelist

Allowed:
- `AskUserQuestion` — intake Q&A
- `Write`, `Edit`, `Read` — for `profile.md`, `n2_matrix.yaml`, `interfaces/*.yaml`
- `mcp__designer-mcp__subsystem_*` (add, status, update_requirements, update_actuals, choose_part, list, remove)
- `mcp__designer-mcp__analyze_candidate`, `verify_candidate`
- `mcp__designer-mcp__bom_*`, `design_state`, `project_status`
- `mcp__designer-mcp__ds_*` (datasheet research)
- `mcp__designer-mcp__q_*` (questionnaire)
- `mcp__designer-mcp__list_templates`, `get_template_specs`
- `mcp__pcbparts__*` (jlc_search, digikey_get_part, mouser_get_part, sensor_recommend, board_search/get)
- `Agent` to spawn `parts-finder` sub-agent
- `ee.facade.run_check` (post-P5 — a Python function call through a thin MCP wrapper or CLI)

Explicitly **blocked** (PreToolUse hook rejects):
- ❌ `mcp__designer-mcp__system_export_kicad`, `kicad_export_schem`, `pcb_*`, `schem_system`
- ❌ `mcp__live-edit-mcp__*` (schematic editing — pcb-designer's job)
- ❌ `mcp__router-mcp__*` (routing — pcb-designer's job)
- ❌ `Bash` shell access beyond a small allowlist (gate runner, manifest builder)

## Self-injection bundle (auto-emitted by SessionStart hook)

On every turn while in researcher mode:

```
<system-reminder>
## RESEARCHER ACTIVE — project=<name> · sub_stage=<intake|interfaces|selection|verification>

## Doctrine for this sub-stage
- <doctrine_id>: <one-line rule>
- <doctrine_id>: <one-line rule>
- (sourced from library/doctrine/research/<sub_stage>.yaml)

## Current state
- Subsystems: <N> total, <K> ready (chosen+verified), <M> in progress
- Open interfaces: <list of n2 edges without matching interface file>
- Last gate run: research_to_pcb — <N>/<M> PASS, <failures>
- Last failure: <subsystem>.<check> — <verdict>

## Required outputs (cannot exit researcher mode without)
- profile.md
- n2_matrix.yaml (no empty edges)
- interfaces/<each_n2_edge>.yaml
- subsystems/<each>.yaml with chosen_part + calculations all PASS

## Tools allowed (others blocked at call site)
- <tool 1>
- <tool 2>

## Hard rules
- Provenance required on every actual you set.
- Thermal gate FAIL → eliminate candidate, never retry same part.
- Decisions are append-only; never edit prior entries.
- Out-of-scope tool calls (sch gen, routing, kicad mutation) are blocked.

## Exit condition
- All required outputs exist + research_to_pcb gate PASS.
- Then: lock baseline (git tag <project>/research-baseline) and tell user to invoke /pcb-designer.
</system-reminder>
```

## Internal flow per sub-stage

### Intake (sub-stage 1)
1. Greet briefly. Wait for the user's one-liner.
2. Drive Q&A (one topic per message) using `AskUserQuestion` for bounded choices, prose for open lists. Cover: power source, output rails, MCU, sensors, actuators, connectivity, mech, budget.
3. Draft `profile.md` with subsystems table.
4. Draft initial `n2_matrix.yaml` from the load tally (rows = subsystems, edges = power/signal connections).
5. Confirm slug + subsystem list with user in one message.
6. Wait for explicit "go" before advancing.

### Interface authoring (sub-stage 2)
1. For each edge in `n2_matrix.yaml` without a matching `interfaces/<id>.yaml`:
   - Determine type (power / signal / data) from source + sink port types.
   - Write `interfaces/<id>.yaml` with electrical (for power) or protocol (for signal/data) spec.
   - Set participants[], owner=spec, electrical bounds.
2. Run cross-check (sum sink currents ≤ source current; I²C addresses unique on bus).
3. Surface any inconsistencies to user; iterate.

### Part selection (sub-stage 3)
For each subsystem without `chosen_part`:
1. Call `parts-finder` sub-agent with subsystem requirements → ranked candidate list (3–5 MPNs).
2. For each candidate:
   a. Call `analyze_candidate` → runs analytic checks (thermal_gate, etc).
   b. If hard fail → eliminate, no retry.
   c. If pass → keep in shortlist.
3. From shortlist, pick winner per: cost / stock / package / margin.
4. Call `subsystem_choose_part` with rationale + rejected[] + tradeoffs.
5. Populate `actuals` with `TrackedValue(value, provenance)` for every datasheet-extracted field.

### Verification (sub-stage 4)
For each subsystem with `chosen_part` and no calculations:
1. For each `check_rule` in the subsystem's template:
   a. Call `ee.facade.run_check(subsystem, check_id)` — function call, deterministic.
   b. Persist returned `EEResult` to `results/<subsystem>_<check>_<ts>.yaml`.
   c. Append `CalculationRef` to `subsystem.calculations`.
   d. On hard fail: surface to user with verdict + recommendation (swap part, change L, etc).
2. When all hard checks pass for all subsystems:
   - Run `research_to_pcb` gate.
   - On PASS: lock baseline + tell user to invoke `/pcb-designer`.

## Failure modes

| failure | behavior |
|---|---|
| User aborts mid-intake | Save partial `profile.md`, exit cleanly; resume on next session |
| Cross-check finds load > source budget | Surface to user; suggest larger buck or load reduction; iterate |
| All candidates fail thermal_gate | Suggest larger package / different topology / lower current target; ask user |
| `ee.facade.run_check` raises (lib error) | Treat as failed check; do NOT crash agent; report to user with traceback |
| Out-of-scope tool call attempted | PreToolUse hook rejects with reason; agent reads + adjusts |
| Gate fails on exit | Surface failures; agent does not exit researcher mode until resolved or user overrides with `--force` |

## Performance characteristics

- Intake: 5–10 user turns over ~10 min of wall clock.
- Interface authoring: ~30 seconds per interface, mostly file writes.
- Part selection: ~5 min per subsystem (parts-finder + analyze_candidate per candidate).
- Verification: ~30 sec per check (ee.facade) — total ~1-2 min for a 7-subsystem project.
- Total: 30 min – 2 hours for a fresh 7-subsystem project, depending on intake depth.

## Testing

- `hw_agent/agents/tests/test_researcher_intake.py` — fixed user inputs, assert profile.md output matches golden.
- `hw_agent/agents/tests/test_researcher_handoff.py` — full pipeline on `control_hub_v1`-fixture, assert all output artifacts validate + gate passes.
- Contract test: spec a deliberately-broken handoff (missing interface, bad provenance) and verify gate FAILS with structured reason.

## Dependencies

**Imports / depends on:**
- `hw_agent.core` — pydantic models (Subsystem, InterfaceRecord, N2Matrix, EEResult, Baseline, ProjectManifest)
- `hw_agent.library/doctrine/research/*.yaml` — sub-stage doctrine bundles
- `hw_agent.library/gates/research_to_pcb.yaml` — gate definition
- `hw_agent.ee.facade` — for math checks
- `hw_agent.scripts.hooks.inject_*` — injection scripts (auto-fired by harness)
- MCP servers: `designer-mcp`, `pcbparts`
- Sub-agent: `parts-finder`

**Does NOT depend on:**
- `mcp_server.live_edit`, `mcp_server.router`
- KiCad CLI
- pcb-designer agent (no direct calls — communicates via artifact files only)

## Open questions / known limitations

1. **`ee.facade.run_check` invocation surface.** Function call from a Claude tool requires either an MCP wrapper, a CLI, or stdin/stdout pipe. Decide before P5 ships.
2. **Cross-project subsystem reuse.** Today the researcher always starts fresh. After P7 (library), it should be able to copy a `buck_5v` from `rc_car_v1` into `control_hub_v1`. Defer.
3. **Iterative refinement after gate fail.** Currently agent exits gate-blocked; needs UI for "fix X, re-run from sub-stage Y."
4. **Cost budget.** No per-stage token budget yet. Add circuit-breaker after first 3 runs reveal typical cost.
5. **Math invocation latency.** If `ee.facade.run_check` shells out to lcapy (300ms) + ngspice (3s), per-subsystem verification is 5+ seconds per check. Acceptable for 7 subsystems × 3 checks = ~2 min. Caches results by input hash so re-runs are instant.

## Related

- `pcb-designer.md` — consumer of this agent's output bundle
- `../../investigations/typed-core-spec.md` — pydantic contracts the artifacts conform to
- `../../investigations/harness-injection-deep-dive.md` — how doctrine swaps per sub-stage
- `library/doctrine/research/*.yaml` — actual doctrine snippets (to be authored as part of implementation)
- `library/gates/research_to_pcb.yaml` — gate definition (to be authored)
- Sub-agent: `.claude/agents/parts-finder.md` (existing)

## Status

`planned` — depends on typed core (P-1), pipeline/injection skeleton (P0.3/P0.5), doctrine library (P0.6), and `ee.facade` shape (P5). Owner: TBD. Last updated: 2026-05-24.
