# hw_agent/skills — pipeline overview

Six slash-commands form a linear hardware design pipeline. Each stage consumes the output of the previous and hands off to the next. Invoke them in order; skip stages only if you know what you are doing.

## Pipeline

    /spec  →  /designer  →  /designer-math  →  /pcb  →  /router  →  /gtm

---

### /spec — hardware spec stage

**Skill:** `spec/spec.md`

Load-first intake. Discovers every actuator, sensor, MCU, connector, and mechanical constraint. Writes `docs/projects/<slug>/profile.md` and registers requirements in designer-mcp via `subsystem_add`. Does NOT pick parts or draw schematics. Ends with a confirmed load-tally and rail budgets ready for `/designer`.

---

### /designer — parts selection + schematic

**Skill:** `designer/designer.md`

Consumes `/spec` output. Drives a Q&A intake (if `/spec` was skipped), then spawns a swarm of haiku subagents to search JLC/Mouser/DigiKey, verify candidates, and commit chosen parts via `subsystem_choose_part`. Narrates results one subsystem at a time. Produces architecture diagram and project README. Ends with a BOM-complete design ready for math verification.

Supporting skills in `designer/`:
- `research-subsystem.md` — JLC/Mouser search → verify → commit (swarm prompt)
- `investigate-subsystem.md` — per-component investigation report writer
- `full-board-design.md` — older end-to-end orchestrator (reference only)
- `architecture-diagram.md` — Excalidraw + PNG + project README generator

---

### /designer-math — Pass 2 verification math

**Skill:** `designer-math/designer-math.md`

Consumes `/designer` output (subsystems with chosen_part + actuals). Runs Layer 1 closed-form checks (inductor peak current, output ripple, feedback divider accuracy, thermal Tj) on every power-conversion subsystem. Optionally runs Layer 2 averaged-model Bode/PM/GM analysis (python-control, no SPICE). Flags failures with recommended part swaps or re-sizing. Produces `designer-math-report.md`. Ends with a verified design ready for layout.

---

### /pcb — schematic-to-PCB layout

**Skill:** `pcb/pcb.md`

Consumes verified schematic + designer-math report. Assigns footprints (if missing), sets board outline + layer stack-up + design rules, then places all components in functional clusters (power entry → converters → MCU → sensors → connectors). Uses `move_footprint`, `pcb_ipc_status`, `constraints_check`. Does NOT route traces. Ends with a placed `.kicad_pcb` with 0 DRC shorts and a clean ratsnest, ready for `/router`.

---

### /router — PCB autorouting

**Skill:** `router/router.md`

Consumes the placed `.kicad_pcb` from `/pcb`. Dispatches to `freerouting-hosted` (default, our self-hosted FreeRouting Docker service) or `orthoroute` (CUDA-accelerated) via router-mcp. Applies the SES result, verifies DRC (0 shorts, 0 unrouted). Provides manual-assist flow if autorouter leaves >2% unrouted. Ends with a fully-routed board ready for fab file generation.

---

### /gtm — fab and assembly handoff

**Skill:** `gtm/gtm.md`

Consumes the routed `.kicad_pcb` from `/router`. Validates against JLC (default) or PCBWay design rules, generates gerbers + drill files + BOM CSV + CPL (centroid pick-and-place), and packages everything into a dated `_fab.zip`. Final deliverable: upload to jlcpcb.com and order.

---

## Directory layout

```
hw_agent/skills/
  README.md                      ← this file
  spec/spec.md
  designer/
    designer.md
    research-subsystem.md
    investigate-subsystem.md
    full-board-design.md
    architecture-diagram.md
  designer-math/designer-math.md
  pcb/pcb.md
  router/router.md
  gtm/gtm.md
```
