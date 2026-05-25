# Handoff Prompt — Implement the `pcb-designer` Agent

> Copy the prompt block below into a fresh Claude Code session. It is self-contained.

---

## Prompt to paste

```
You are implementing the `pcb-designer` agent for the hw-toolkit harness. This is
the second of two main agents in a personal hardware-design tool. The first agent
(`researcher`) produces a typed handoff bundle (`ResearchBundle`); your job is to
take that bundle and turn it into KiCad files + fab-house deliverables (`FabBundle`).

The contract is LOCKED. Two pydantic models in `hw_agent/core/`:
  - `ResearchBundle` (research_bundle.py) — your input. Read-only.
  - `FabBundle` (fab_bundle.py) — your output. Frozen, gate-validated.

Before you write code, read these in order:

## 1. Read first (contract + doctrine)

1. `hw_agent/core/research_bundle.py` (~140 LOC) — the input contract.
   Models: `ResearchBundle`, `SubsystemPick`, `Interface`.
2. `hw_agent/core/fab_bundle.py` (~90 LOC) — the output contract.
   Frozen, validates that all gates passed before construction.
3. `docs/architecture/README.md` — 4 core convictions:
     (a) skills are slim step-lists
     (b) tools give feedback
     (c) feedback drives design
     (d) re-inject every turn to prevent drift
4. `docs/architecture/LAYER_MODEL.md` — 3-layer model:
     Layer 0 AI intent (us) → Layer 1 KiCad files → Layer 2 fab artifacts.
5. `docs/architecture/modules/pcb-designer.md` — your full module design:
     contract, 7 internal phases, tool whitelist, forbidden tools, failure modes.
6. `docs/investigations/typed-core-spec.md` — specifically the
     "Ownership & mutation rules" section. HARD rules on what you can/cannot edit.
7. `docs/investigations/prior_art/SYNTHESIS.md` — why the contract is what it is
     and what NOT to try to refactor.

## 2. Skim as needed (payload references)

You don't need to read these upfront — pull the relevant one when you start each
phase. Each documents real-world payload shapes for a tool the agent talks to:

  - `docs/investigations/payloads/kicad.md` — kicad-cli signatures, .kicad_sch /
      .kicad_pcb S-expressions, BOM CSV format, CPL CSV format, ERC/DRC JSON.
  - `docs/investigations/payloads/kicad_klc.md` — exact field-name conventions
      for KiCad symbol properties (Reference, Value, Footprint, Datasheet, MPN,
      Manufacturer, LCSC). You MUST project SubsystemPick into these.
  - `docs/investigations/payloads/router.md` — Specctra DSN/SES format,
      FreeRouting + OrthoRoute HTTP API, net class shape.
  - `docs/investigations/payloads/vendors.md` — JLCPCB/PCBWay/OSH/Aisler
      capability sheets. Live in `hw_agent/artifacts/data/vendors/*.json`.
      Note: Aisler seed not yet populated.
  - `docs/investigations/payloads/sourcing.md` — DK/Mouser/JLC API response
      shapes (researcher pulls this; you mostly consume the cached result).
  - `docs/investigations/payloads/datasheets.md` — per-category extractable
      fields. Documents what should be in `SubsystemPick.actuals`.
  - `docs/investigations/payloads/spice.md` — PySpice/ngspice shapes (used by
      ee/ when called from your phases, e.g. ripple sanity check).
  - `docs/investigations/payloads/lcapy.md` — symbolic stability analysis.
  - `docs/investigations/payloads/scikit_rf.md` — controlled-impedance routing.
  - `docs/investigations/payloads/ibis_step.md` — STEP 3D export
      (add to FabBundle output as `step_file: Path | None`); IBIS deferred.

## 3. Your mission

Implement `.claude/agents/pcb-designer.md` (the agent definition file) plus the
Python helpers it needs. The agent must:

(a) Validate the ResearchBundle on startup (Phase 1).
    - Load via `ResearchBundle.model_validate_json(path)`.
    - Refuse all further work if validation fails.
    - Emit structured error pointing at the missing/broken artifact.

(b) Walk through Phases 2-7 in order:
    2. Schematic generation — project SubsystemPick → KiCad symbol properties
       (use KLC field names from kicad_klc.md), then run KiCad ERC.
    3. Component placement — heuristic + iterative, using interface graph from
       ResearchBundle.interfaces for grouping.
    4. Routing via router-mcp — derive net classes from Interface
       (type + current_continuous_max_a + speed_hz) → trace width / clearance.
    5. DRC via KiCad CLI — must pass with 0 violations.
    6. Fab export — gerbers, BOM CSV, CPL CSV. Run pcborder_validate_for_vendor.
    7. Lock baseline — git tag + construct FabBundle (constructor enforces gates).

(c) Respect the tool whitelist. The frontmatter `tools:` field in
    `.claude/agents/pcb-designer.md` lists allowed tools. Researcher's tools
    (intake/spec/parts/math) are NOT in your list.

## 4. Hard rules (cannot violate)

1. READ-ONLY on research artifacts (listed in typed-core-spec.md).
2. NEVER edit the schema (`hw_agent/core/*.py` or `typed-core-spec.md`).
3. KiCad symbol properties are derived from `SubsystemPick`. If they disagree,
   regenerate the schematic. Never hand-edit fields in eeschema.
4. Never bump rev letter (rev_A → rev_B) without `ready_to_fab` gate PASS.
5. ERC and DRC violations must be 0 (real, not expected) before fab export.
6. If you discover a part-pick or spec issue: STOP, emit structured error, tell
   the user to re-invoke `/researcher`. Do not "helpfully" fix it.
7. Output artifacts only go in `kicad/` and `fab/rev_<X>/`. Never elsewhere.
8. The 4 gate bools on FabBundle (erc_clean, drc_clean, vendor_validated,
   stock_verified) must all be True before you can construct a FabBundle. The
   constructor enforces this — a failing-gate FabBundle is unconstructible.

## 5. Known gaps you'll need to address

These came out of the payload + prior-art research and are concrete things you
will need to build during your implementation:

  - **kicad_projector helper** — pure function
    `SubsystemPick → dict[str, str]` projecting MPN/Manufacturer/LCSC/Datasheet
    into KiCad symbol properties using exact KLC field names from
    `docs/investigations/payloads/kicad_klc.md`. Without this, BOM export
    doesn't carry the data the fab house needs.

  - **DesignRules derivation** — pure function
    `Interface → NetClass(trace_width_mm, clearance_mm, via_diameter_mm)`. Map
    `type=power` + `current_continuous_max_a` → wider trace, `type=signal/data`
    + `speed_hz` → controlled-impedance class. Feed into DSN export before
    routing.

  - **ERC/DRC JSON report links in FabBundle** — current FabBundle doesn't
    carry paths to the ERC/DRC reports. Consider adding optional
    `erc_report_json: Path | None` and `drc_report_json: Path | None` to
    FabBundle for audit trail. Schema change — propose it, don't just do it.

  - **STEP 3D export** — call `kicad-cli pcb export step` at fab time. Consider
    adding optional `step_file: Path | None` to FabBundle. Schema change —
    propose it.

  - **Aisler vendor seed** — not yet populated in
    `hw_agent/artifacts/data/vendors/aisler.json`. If user picks vendor=aisler,
    surface that the seed is missing.

  - **Canonical `actuals` keys** — the `SubsystemPick.actuals` dict is
    free-form. `docs/investigations/payloads/datasheets.md` documents
    recommended canonical keys per category (buck, ldo, mcu, sensor, motor
    driver, mosfet). Consider writing `docs/architecture/actuals_keys.md` as
    doctrine. Surface a structured warning if a key you need is missing rather
    than crashing.

## 6. Build order (each mergeable alone)

1. Write `.claude/agents/pcb-designer.md` with frontmatter (model, description,
   tools whitelist) and a minimal body.
2. Phase 1 (validate input bundle) as a Python helper:
   `hw_agent/agents/pcb_designer/validate.py`. Pure function: load bundle via
   pydantic, return ValidationReport. Unit test with control_hub_v1 fixture if
   one exists, otherwise fabricate a minimal ResearchBundle.
3. kicad_projector helper (gap above) — pure function + unit test.
4. Phase 2 (schematic generation) — wrapper around system_export_kicad +
   kicad-cli sch erc. Use kicad_projector to set symbol properties.
5. Phase 3 (placement).
6. DesignRules derivation helper (gap above) — pure function + unit test.
7. Phase 4 (routing) — DSN export with derived net classes, call router-mcp,
   import SES.
8. Phase 5 (DRC).
9. Phase 6 (fab export + vendor validation).
10. Phase 7 (baseline lock + git tag + FabBundle construction).

Do NOT try to implement all 7 phases in one PR. Each is its own commit.

## 7. What "done" looks like

Running `/pcb-designer` on `docs/projects/control_hub_v1/` (assuming researcher
has populated it with a locked ResearchBundle) produces:
  - kicad/control_hub_v1.kicad_sch (ERC clean)
  - kicad/control_hub_v1.kicad_pcb (DRC clean)
  - fab/rev_A/{gerbers/, bom.csv, cpl.csv, manifest.yaml}
  - baselines/fab.yaml (the serialized FabBundle)
  - git tag control_hub_v1/fab-baseline-rev_A
ready_to_fab gate PASSES. No research artifacts mutated. Unit tests pass.

## 8. Ask the user before starting

Confirm:
  - KiCad CLI is installed and on PATH.
  - router-mcp + FreeRouting (or OrthoRoute) service is reachable.
  - Which vendor to target by default (JLCPCB unless told otherwise).
  - Whether to add STEP export + ERC/DRC report links to FabBundle now (small
    schema additions) or defer to v1.1.

If clear: start with step 1 of build order. Report progress per phase.

## 9. What NOT to do

- Don't redesign the architecture. The two-agent split and the 2-pydantic-model
  contract are locked (see prior_art/SYNTHESIS.md for the receipts).
- Don't add new MCP tools to designer-mcp or live-edit-mcp. Use what's there.
- Don't try to make this agent also do research / part picking / math. Math
  runs in the researcher stage; you read `actuals` as facts.
- Don't add a UI. Text + KiCad files are the entire interface.
- Don't write a workflow runner. Manual invocation is fine for v1.
- Don't refactor `SubsystemPick.actuals` into typed per-category sub-models.
  That's a v2 candidate — wait for real friction.
- Don't refactor `Interface` into `Net` (one provider, many consumers). Also
  v2. For now multiple Interface rows per shared rail is acceptable.
```

---

## After the fresh session reads the prompt

It should ask 2-4 clarifying questions (KiCad CLI path, router service URL, vendor target, schema addition decisions), then start with `.claude/agents/pcb-designer.md`.

If it tries to mutate researcher artifacts or schema files, the PreToolUse hook (when it ships per `knowledge-injection.md`) will block — but until then, the prompt above is the enforcement. Watch for it in early commits.

## Notes for you (the human handing this off)

- The prompt assumes the fresh session has access to this repo (read the docs at the listed paths).
- The prompt assumes typed core (P-1) is built and committed — `hw_agent/core/research_bundle.py` and `fab_bundle.py` must exist before Phase 1 can validate.
- If the fresh session pushes back on a hard rule, that's a signal — the rule may be wrong, or the session may be missing context. Investigate before relaxing.
- Two small schema additions are flagged as user-decision items in section 8: STEP file path + ERC/DRC report paths on FabBundle. Decide before the fresh session starts to avoid mid-implementation churn.

## Companion handoff (when needed): researcher

A similar prompt for `researcher` lives in `modules/researcher.md`'s "Internal flow per sub-stage" section — wrap that the same way when you're ready to spin up the researcher implementation in its own session.
