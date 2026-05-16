# Full board design — orchestrator

End-to-end skill: take a project profile (board purpose + constraints + subsystem list) and produce a complete READY BOM with per-subsystem investigations, schematics, and a system architecture diagram. This is the **top-level skill** an agent should run when asked to "design a board" — it sequences the per-phase skills below.

## Sub-skills referenced

This skill orchestrates three others. Read each before invoking it on a real project:

| Phase | Skill | Lives at |
|-------|-------|----------|
| Per-subsystem research (JLC search → verify → commit) | **research-subsystem** | `hw_agent/skills/research-subsystem.md` |
| Per-subsystem investigation report | **investigate-subsystem** | `hw_agent/skills/investigate-subsystem.md` |
| Project architecture diagram + README | **architecture-diagram** | `hw_agent/skills/architecture-diagram.md` |

## Execution model — always swarm, always dialogue

This orchestrator NEVER runs `research-subsystem` or `investigate-subsystem` inline in the main context — every per-subsystem invocation goes through an `Agent` subagent. This is non-negotiable, regardless of whether subsystems are processed sequentially or in parallel waves. Reasons:

1. **Main context stays clean.** Research absorbs many MCP responses (jlc_search × 3, jlc_get_part × 4–6, verify_candidate × 4–6, distributor cross-references, sometimes substitutions). Inline, that's 10K+ tokens of noise per subsystem. Via subagent, the main context sees only a ≤300-word structured report.
2. **Predictable token economics.** A 9-subsystem design done inline in main context can blow past compaction. Done via swarm, the main context absorbs ~2.7K tokens of summaries — nine times less.
3. **Orchestrator stays in dialogue with the engineer.** With the swarm doing heavy lifting, the orchestrator narrates: "spawning subagent for buck_5v"; "buck_5v back: chose SY8205 — Iout=8A, η=92%, 3 alternatives rejected, 1 accepted warning. Approve?". The engineer can redirect at every transition.

**Mechanics for sequential waves (1 subsystem at a time):**
- Spawn `Agent({subagent_type: "general-purpose", model: "haiku", prompt: "<research-subsystem skill body inline + project_slug + subsystem_name + category>"})`. The `model: "haiku"` is mandatory per `~/.claude/CLAUDE.md` — escalate to `sonnet` only if a haiku subagent reports back stuck on a hard tradeoff.
- Wait for the subagent's structured report.
- Surface to the user as a 4–6-line narration: chosen part (MPN, price, source distributor), 2–3 key actuals, 1 line on alternatives rejected, any accepted_warnings.
- Wait for user ack (explicit "ok" / "yes" / "approve") OR a redirect ("try Y instead", "go cheaper", "bump iout to 10A"). The user is the engineer-of-record; the orchestrator is staffing the dialogue.

**Mechanics for parallel waves (N subsystems at once):**
- Send ONE message containing N `Agent` tool calls, each with `model: "haiku"`. All run concurrently.
- Collect all reports.
- Surface to the user as a compact table (name | chosen MPN | $/u | warnings | source) + a 1-line per-subsystem narration for any accepted_warnings or unexpected findings.
- Wait for ack or per-subsystem redirect.

**No exceptions:** even a single subsystem in isolation goes through a subagent. The orchestrator's role is conductor, not performer.

## Arguments

`$ARGUMENTS` is the project slug (e.g. `robocar_hub_v2`). The agent expects EITHER:
- A `docs/projects/<slug>/project_profile.md` already on disk describing the board (preferred — durable spec), OR
- A free-form description from the user that the agent transcribes into `project_profile.md` as Phase 0.

Project_profile.md must include: **purpose**, **environment** (temp / vibration / weather), **power source**, **subsystem list with rough requirements**, **budget cap**, **target manufacturer (JLC basic/extended)**.

## Phases

### Phase 0 — Project setup

1. If `docs/projects/<slug>/project_profile.md` doesn't exist: interview the user (briefly — match their technical level), capture the spec, write `project_profile.md`.
2. Walk the profile's subsystem list. Each entry maps to an `hw_agent` template category (`buck_converter`, `ldo`, `mcu_ble`, `motor_driver`, `pwm_servo_driver`, `stepper_driver`, `imu`). If a subsystem doesn't fit any template (e.g. a single sensor like VL53L1X), add it to the **additional sensor pack** list — these don't get formal subsystem state but DO appear in the BOM and README. Persist the sensor pack to `docs/projects/<slug>/sensor_pack.json` as a list of objects with the fields `{role, mpn, lcsc, manufacturer, package, price, stock, qty, lifecycle?}` (one entry per item; `qty` defaults to 1; `lifecycle` is optional, one of `"Active" | "EOL" | "single-source"`). The orchestrator writes this file in Phase 0 using the multi-distributor flow below — same discipline as templated subsystems (research-subsystem.md now uses the same `sensor_recommend` + DigiKey/Mouser cross-reference pattern): no MPNs from memory. Downstream phases read it: Phase 4 renders it into the README's sensor-pack table, and Phase 5's done check folds it into the budget comparison.

**Sensor-pack acquisition flow (per item):**

1. **Discovery** — fire in parallel (one orchestrator message):
   - `mcp__pcbparts__jlc_search(query=<descriptive keywords>)`  for JLC stock
   - `mcp__distributor-mcp__mouser_search(query=<same keywords>, in_stock_only=true)`
   - `mcp__distributor-mcp__digikey_search(query=<same keywords>, in_stock_only=true)`
   - `mcp__pcbparts__sensor_recommend(measure=..., protocol=..., type=...)`  to get curated MPN suggestions
2. **Cross-stock check** — for any high-value MPN that appeared in only one source, fire `mcp__pcbparts__jlc_search(query=<MPN>)` to see if JLC also stocks it (turnkey win) and `mcp__pcbparts__digikey_get_part` / `mouser_get_part` for the inverse direction.
3. **Lifecycle filter** — drop any candidate whose lifecycle field reads Obsolete / End of Life / NRND / Last Time Buy on any distributor. EOL parts cannot be re-ordered for the next build.
4. **Pick** — preference order:
   (a) Active everywhere AND on JLC → cheapest path, JLC turnkey assembly
   (b) Active at DigiKey and/or Mouser, not on JLC → multi-source kit (off-board breakout via QWIIC, OR hand-placed during kit assembly)
   (c) Active at only one distributor → accept with a tradeoff note flagging single-source risk
5. **Persist** — write the picked entry to `sensor_pack.json` with the schema below, plus the optional `lifecycle: "Active" | "EOL" | "single-source"` field.

**Discipline (sensor pack, NON-NEGOTIABLE):**
- No MPN from memory — every candidate originates from `sensor_recommend`, `jlc_search`, or distributor cross-reference.
- Daily quota on `digikey_get_part` / `mouser_get_part` / `digikey_search` / `mouser_search` (same 1000/day) — call only on the top 1-2 winners per item, not on every alternative.
- Profile spec is editable. If the profile names a part that comes back EOL/Obsolete, surface to the user and propose updating the profile to a current alternative — don't silently substitute.
3. Compute the power budget across all loads — `5 V total = Σ(load_peak_mA)`, `3.3 V total = Σ(load_peak_mA)`. The buck rating must be ≥1.2× the 5 V total; the LDO rating ≥1.2× the 3.3 V total. **Surface this math before adding subsystems** so the requirements are derivable, not arbitrary.
4. **Parallel `mcp__designer-mcp__q_load`** for each unique category to confirm template availability + read the requirements + actuals_schema upfront. The tool requires the `component_type=` kwarg — positional args will fail validation:
   ```
   mcp__designer-mcp__q_load(component_type="buck_converter")
   mcp__designer-mcp__q_load(component_type="ldo")
   mcp__designer-mcp__q_load(component_type="mcu_ble")
   mcp__designer-mcp__q_load(component_type="motor_driver")
   mcp__designer-mcp__q_load(component_type="pwm_servo_driver")
   mcp__designer-mcp__q_load(component_type="stepper_driver")
   mcp__designer-mcp__q_load(component_type="imu")
   # ALL in ONE tool-call message
   ```
5. **Parallel `subsystem_add`** for all subsystems with their requirements derived from the profile + power budget. Use the unit conventions from `q_load.requirements[].unit` (Amps, not mA; Volts, not mV). Set `iout_margin=1.2` (default) unless the profile justifies otherwise.

### Phase 1 — Per-subsystem research (JLCPCB grounding)

For each subsystem, run the **research-subsystem** skill via an `Agent` subagent. See the "Execution model" section above — this is always swarmed, never inline.

**Wave structure** (the rationale stays the same; only the mechanism changes — every wave goes through subagents):

- **Wave 1 — power-tree roots, sequential.** Research the MCU first (its peak current sets the LDO requirement; its package + footprint reservation drives layout). Then research the buck (its iout_max actuals confirm headroom for downstream loads). One Agent call per wave entry.
- **Wave 2 — downstream loads, parallel.** All non-power-tree subsystems (LDO if separate from buck rail, motor drivers, servo driver, stepper drivers, IMU). One orchestrator message containing N concurrent Agent calls.

**Critical:** between waves the orchestrator narrates the transition to the engineer ("Wave 1 done — MCU + buck committed. Spawning Wave 2 with N subagents in parallel."). After Wave 2 completes, the orchestrator surfaces the full BOM-so-far in a compact table and waits for ack before moving to Phase 2.

**Discipline reminder:** the no-MPNs-from-memory rule lives in the research-subsystem skill itself. The orchestrator's job is just: pick the wave, dispatch the subagent(s), collect the report(s), narrate, wait for ack.

### Phase 2 — Per-subsystem investigation reports

Run **investigate-subsystem** for each committed subsystem via an `Agent` subagent — same swarm-always rule. All 9 reports run in one parallel orchestrator message.

The orchestrator narrates "Phase 2 — fanning out 9 investigation subagents" before dispatch, and on completion surfaces a 1-line confirmation per subsystem (file path written + word count) plus any subagent that flagged issues during write-up.

### Phase 3 — Schematic renders

For subsystems whose category has a schemdraw renderer (`buck_converter`, `ldo`, `motor_driver`, `voltage_divider`):
```
mcp__designer-mcp__svg_buck(project=<slug>, subsystem=<name>, …)        # parallel
mcp__designer-mcp__svg_ldo(project=<slug>, subsystem=<name>, …)
mcp__designer-mcp__svg_motor_driver(project=<slug>, subsystem=<name>, …)
```
Pass `subsystem=` so two same-category instances (e.g. `motor_a` + `motor_b`) don't clobber each other.

For categories without renderers (`mcu_ble`, `imu`, `pwm_servo_driver`, `stepper_driver`), the investigation report notes "Schematic not auto-generated for this category" — application-specific layout.

### Phase 4 — Architecture diagram + project README

Run the **architecture-diagram** skill (`hw_agent/skills/architecture-diagram.md`). It walks all subsystems on disk, builds the Excalidraw block diagram (power tree + control overlay + legend), rasterizes to PNG via PIL, and writes the project README.md with the embedded diagram + BOM table + investigation links + additional-sensor-pack table.

### Phase 5 — Done check

```
mcp__designer-mcp__project_status(project=<slug>)        # READY/BLOCKED roll-up
mcp__designer-mcp__bom_summary(project=<slug>)           # cost, supply risk, parts table — SUBSYSTEMS ONLY
```

Compute the **combined BOM total** before the budget comparison:
1. Read `docs/projects/<slug>/sensor_pack.json` (treat missing file as `[]` → $0 contribution; do NOT fail the check on absence).
2. `sensor_pack_total = Σ(item.price * item.qty)` over the list.
3. `combined_total = bom_summary.total + sensor_pack_total`.
4. Compare `combined_total` against the budget cap from `project_profile.md`. `bom_summary` reads subsystems on disk and is blind to the sensor pack — using its total alone undercounts the BOM whenever a sensor pack exists.

Done iff:
- All subsystems READY (or BLOCKED only with explicitly accepted soft warnings — see investigation reports for justification).
- `combined_total` (subsystem BOM + sensor pack) under the budget cap from project_profile.md.
- All `investigation.md` files present at `components/<cat>/<name>/`, each with ≥3 alternatives in the table, each row with a quoted verify_candidate result.
- `architecture.excalidraw` + `architecture.excalidraw.md` + `architecture.png` + `README.md` at the project root.
- The agent has `Read()` the architecture.png at least once to confirm no `□` glyph boxes / overflow / missing arrows.

## Final report

≤500 words. Structure:

```
## <project> — design complete

**BOM:** $<combined_total>/board (single qty — subsystems $<bom_summary.total> + sensor pack $<sensor_pack_total>) · <N>/<total> READY · <supply_risk>% risk
**Architecture:** ![](./docs/projects/<slug>/architecture.png) — open in Excalidraw to edit

### Subsystem picks
- buck_5v: <MPN> ($<price>) — <one-line key reason>
- ldo_3v3: <MPN> ($<price>) — <…>
- … (one bullet per subsystem)

### Tradeoffs accepted
- <each accepted_warnings entry, with the subsystem and why>

### Additional sensor pack
<table>

### What's next
- KiCad schematic capture: subsystem schemdraw renders are at `components/<cat>/<name>/schematic.{svg,png}`. Per-component layout + interconnect is application-specific.
- PCB layout: pass to designer-mcp's KiCad export tools or do manually.
- Fabrication: `pcb_export_fabrication` produces JLCPCB-ready gerbers + drill + pick-and-place.
```

No praise. Concrete. The user reads this once and either approves or asks for revisions to a specific subsystem (which means re-running research-subsystem + investigate-subsystem for that one).

## Discipline reminder

The three sub-skills enforce their own rules. The orchestrator's job is just to:
1. Set up the project state (Phase 0).
2. Sequence the sub-skills correctly (research → investigate → architecture).
3. Maximize parallelism *across* subsystems (since they're mostly independent).
4. Verify the done definition before declaring complete.

If a sub-skill's discipline rule conflicts with what the orchestrator wants (e.g. the user pressures you to commit a part you remember without searching), the sub-skill wins. The discipline rules exist because skipping them produces designs that fail verify_candidate or contain made-up parts. Don't compromise.

- **Never run research-subsystem or investigate-subsystem inline.** Every invocation goes through an Agent subagent. This is the load-bearing discipline for token economics + dialogue cadence; violating it bloats the main context and breaks the engineer-orchestrator conversation.
