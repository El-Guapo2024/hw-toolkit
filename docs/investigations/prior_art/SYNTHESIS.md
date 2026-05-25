# Prior-Art Synthesis — Should We Change the Contract?

Read first: `interchange_formats.md`, `pcb_hdls.md`, `systems_eng.md`. This file is the verdict.

## TL;DR

**Keep `ResearchBundle` + `FabBundle` as-is for v1.** No existing standard occupies our altitude (AI-driven design intent), and the two-pydantic-model contract is already structurally aligned with SysML v2 primitives.

Three optional additions worth considering — none block v1:

1. **N2 matrix exporter** (cheap, MVP-friendly view).
2. **InterfaceDefinition reusable layer** (medium, deduplicates repeated specs).
3. **P-Port / R-Port asymmetry on Interface** (post-MVP, clarifies provider/consumer for power rails).

## Findings by domain

### Interchange formats (IPC-2581, ODB++, EDIF, KiCad native, tscircuit CircuitJSON, EAGLE, Altium, Eurocircuits)

All operate at the **physical layer** — nets, footprints, copper polygons, drill files. None carry design intent (why this part, what alternatives, decision lineage, gate proofs).

**Verdict:** Our contracts sit at a higher altitude. Three-layer model formalized in `docs/architecture/LAYER_MODEL.md`:

- **Layer 0 (AI intent):** ResearchBundle, FabBundle ← our agents
- **Layer 1 (EDA truth):** `.kicad_sch`, `.kicad_pcb` ← KiCad
- **Layer 2 (Fab):** IPC-2581, gerbers, BOM CSV, CPL CSV ← fab houses

Export to IPC-2581 *only at the boundary* if a fab house asks. Do not retrofit our pydantic models into its schema.

### PCB HDLs (atopile, JITX, SKiDL, tscircuit, KiCad Python API, Magic VLSI)

All optimize for **human-authored DSL** workflows: library reuse, IDE linting, parametric factories. Different use case from ours.

We optimize for: AI-driven intake, post-selection validation against subsystem templates, decision lineage (rejected candidates preserved), late port-binding (mapping subsystem ports to load pins after part choice), and two-agent decoupling (researcher → pcb-designer).

**Verdict:** No migration. Borrow only later: atopile's typed `Power`/`Signal`/`Electrical` interfaces (interesting once we add InterfaceDefinition), tscircuit's CircuitJSON IR (only as an autorouter input format if we ever need a non-KiCad routing engine).

### Systems-engineering (SysML v2, AUTOSAR, N2 matrix, ICDs, IP Reuse Methodology Manual)

Strong structural alignment with our contract:

| our model | SysML v2 | AUTOSAR | N2 / ICD |
|---|---|---|---|
| `SubsystemPick` | `part def` | Software Component (SWC) | row + column on the diagonal |
| `SubsystemPick.port_bindings` | `port def` | P-port / R-port | row × column cell |
| `Interface` | `connection def` + `interface def` | Sender-Receiver / Client-Server | one row in the ICD |
| `Interface.type` | `flow` (power/signal/data) | communication pattern | interface category |

Three patterns from this family that *could* improve our contract:

#### 1. N2 matrix exporter — RECOMMENDED for v1

Pure view (no schema change). A `tools/n2_export.py` that takes `ResearchBundle` and prints an N×N markdown table with subsystems on the diagonal and interface ids in the off-diagonal cells. Aerospace standard for human-readable interface audit. Low cost, immediate user value (the engineer *sees* the topology at a glance).

#### 2. InterfaceDefinition reusable spec — DEFER

Today, every I²C interface inlines `protocol="i2c"`, `speed_hz=400000`, voltage in `voltage_nominal_v` separately. AUTOSAR + SysML extract these into a reusable `InterfaceDefinition("I2C_400k_3v3", protocol=..., speed=..., voltage=...)` that each `Interface` references by id.

Pro: dedup, one source of truth, consistency.
Con: extra indirection, more validators, harder to read raw bundle.

**Verdict:** premature for v1 (we have maybe 3-5 interfaces per board). Revisit when a real project has > 15 interfaces of the same kind and the duplication actually hurts.

#### 3. P-Port / R-Port asymmetry — DEFER

AUTOSAR distinguishes provider (P-port) from requirer (R-port). For a 5 V rail: one provider (LDO output), many consumers (MCU VDD, sensor VDD, motor driver VCC). Our current `Interface` is `from_subsystem → to_subsystem` (1-to-1), so we'd need either:

- (a) Multiple `Interface` rows per shared rail (one per consumer) — `ldo_3v3 → mcu`, `ldo_3v3 → bme280`, etc. Awkward, but works.
- (b) A new `Net` model with one `provider` and a list of `consumers`.

Pro: matches physical reality (a rail *is* one provider + many consumers), catches "no provider for VDD_3V3" errors, simplifies routing's net-class assignment.
Con: schema break, harder to write by hand, second model class.

**Verdict:** option (a) is the workaround today and is acceptable for v1. Option (b) is a real improvement for v2 once we have a few projects' worth of pain. Tag this in `docs/architecture/modules/core.md` as a v2 candidate.

## What is genuinely novel in our contract (vs all prior art)

- **`SubsystemPick.actuals: dict[str, float|int|str]`** — late-bound, datasheet-extracted facts that downstream tools (SPICE, lcapy, placement) consume. None of the surveyed HDLs has this concept. SysML has "value properties" but they're statically declared in the schema. Ours is intentionally schemaless v1 because the AI is the one filling it.
- **Lineage by git tag** (`research_baseline_git_tag`, `consumed_research_tag`) — no surveyed system encodes "this output was produced from that input snapshot" as a first-class field. Most rely on version-control conventions out-of-band. Ours hardwires it.
- **Gate-pass bools on FabBundle with constructor refusal** — closest analogue is AUTOSAR's BSW configuration validators, but those are external lint. Ours makes a failing-gate FabBundle *unconstructible*.

These three are the contract's real value-add. Preserve them through any future revision.

## Action items (post sign-off, none required for v1)

1. **v1 (now):** No schema change. Maybe write `tools/n2_export.py` (~30 LOC) once we have a real ResearchBundle on disk.
2. **v1.1:** Document the v2 candidates inside `docs/architecture/modules/core.md` — InterfaceDefinition refactor, P-Port/R-Port `Net` model, typed per-category `actuals` sub-models. These are pre-known evolution paths, not regrets.
3. **v2 trigger:** When any of the following happens, revisit:
   - A project has > 15 interfaces of the same protocol type → InterfaceDefinition becomes worth it.
   - Routing's net-class assignment gets ambiguous because of shared rails → `Net` model becomes worth it.
   - Datasheet extraction starts producing inconsistent keys across projects → typed per-category `actuals` sub-models become worth it.

## Sources

- `interchange_formats.md`, `pcb_hdls.md`, `systems_eng.md` (companion files)
- `docs/architecture/LAYER_MODEL.md` (three-layer altitude diagram)
