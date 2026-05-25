# Module Design — `hw_agent/ee/`

## Purpose

Thin policy + glue layer that turns raw engineering math (done by lcapy / scikit-rf / ngspice / KiCad) into uniform `EEResult` records the rest of the harness consumes. Owns nothing complex — forward sizing (1-line algebra), pass thresholds, verdict formatting, and adapter wrappers around the mature libs.

This module is intentionally small (~300 lines total). It is not an "engine" — it is the **adapter layer** between mature math libraries and the harness's canonical model.

## Public contract

- `ee.result.EEResult` — pydantic model. Universal shape for any check output: `quantity, value, units, tier, passed, margin, verdict, inputs, extras, provenance`.
- `ee.sizing.*` — forward-sizing helpers, ~5 functions: `buck_inductor`, `buck_output_cap`, `voltage_divider`, `feedback_resistors`, `trace_width`. Each is 1–3 lines.
- `ee.policies.*` — threshold constants + verdict format strings: `PM_MIN_DEG = 45`, `RIPPLE_MAX_PCT = 1`, etc.
- `ee.adapters.lcapy_adapter` — `loop_stability(subsystem) -> EEResult`.
- `ee.adapters.skrf_adapter` — `trace_z0(geometry) -> EEResult`.
- `ee.adapters.ngspice_adapter` — `transient(subsystem) -> EEResult`.
- `ee.adapters.kicad_adapter` — `erc(sch_path) -> EEResult`, `drc(pcb_path) -> EEResult`.
- `ee.facade.run_check(subsystem, check_id) -> EEResult` — picks tier + adapter from the subsystem's template `check_rules`.

**Side effects:** none in pure functions. Adapters may shell out to external binaries (ngspice, kicad-cli). Results are persisted by the caller, not by `ee/` itself.

## Internal layout

```
hw_agent/ee/
├── __init__.py            # re-exports facade + EEResult
├── result.py              # EEResult pydantic model
├── sizing.py              # forward-sizing helpers (~30 lines total)
├── policies.py            # threshold constants + verdict templates (~50 lines)
├── facade.py              # run_check dispatcher
├── adapters/
│   ├── lcapy_adapter.py
│   ├── skrf_adapter.py
│   ├── ngspice_adapter.py
│   └── kicad_adapter.py
└── tests/
    ├── test_sizing.py     # golden values
    ├── test_lcapy.py
    └── ...
```

**Data flow:** caller passes a `Subsystem` model → `facade.run_check` looks up the check from the subsystem's template → routes to the right adapter → adapter extracts inputs from subsystem, calls the lib, wraps result in `EEResult` → returns. No state held.

## Dependencies

**Imports from:**
- `hw_agent.core` — only the `Subsystem` model (for adapter input typing).
- `pydantic` — for `EEResult`.
- `lcapy`, `scikit-rf`, `PySpice` — pinned versions per adapter.
- `subprocess` (stdlib) — for KiCad CLI.

**Imported by:**
- `mcp_server/designer/server.py` — MCP tools become 1-line shims.
- `hw_agent/skills/designer-math/` — skill calls `ee.facade.run_check`.

**Forbidden imports** (hexagonal rule, CI-enforced):
- ❌ `from hw_agent.scripts.*`
- ❌ `from mcp_server.*`
- ❌ `from hw_agent.skills.*`

Enforcement: `grep -rn 'from hw_agent\.\(scripts\|skills\)\|from mcp_server' hw_agent/ee/` must return zero.

## Configuration

- `ngspice` binary path via `SPICE_SIMULATOR` env var or `$PATH`.
- KiCad CLI path via `KICAD_CLI` env var or default location.
- Threshold overrides via `hw_agent/library/gates/<gate>.yaml` (not in this module).

## Lifecycle / state

Stateless. Pure functions. All inputs explicit. Results persisted by the caller (typically the post-tool hook in Phase 5).

## Failure modes

- ngspice missing → `EEResult{tier=sim, passed=False, verdict="ngspice binary not found"}` — adapter does not raise, returns failed result.
- KiCad CLI missing → same pattern.
- Lib convergence failure (lcapy nsolve diverges, ngspice fails to converge) → `EEResult{passed=None, verdict="convergence_failed: <reason>"}`.
- Malformed subsystem input → pydantic `ValidationError` at adapter boundary; surface to caller.

## Performance characteristics

- `sizing.*`: <0.1 ms per call. Pure algebra.
- `lcapy_adapter.loop_stability`: 100–500 ms for an averaged 2-3 node model. Slows quadratically past 10 nodes.
- `skrf_adapter.trace_z0`: <50 ms per call.
- `ngspice_adapter.transient`: **3–8 seconds per buck simulation**. Cache aggressively. Never run in tight loops.
- `kicad_adapter.erc/drc`: 1–5 seconds per board (KiCad CLI cold-start).

## Testing

- `hw_agent/ee/tests/` — golden values per function. 3+ cases per sizing fn.
- Adapter tests use a known-good subsystem (e.g. `buck_6v` w/ TI LMR14050) and assert PM/GM within ±5° of published values.
- CI runs all `ee/tests/` on every PR.
- Hexagonal rule check is a CI step.

## Open questions / known limitations

1. **EEResult uncertainty fields** — not currently modeled. Real `Tj` has ±15% uncertainty from θJA spread; we report a single value. Add `value_range: [min, max]` later if needed.
2. **Adapter caching strategy** — not specified per-adapter. ngspice especially needs disk-cache keyed on input hash.
3. **lcapy circuit size** — symbolic solver slows past ~10 nodes. Pre-reduce to averaged model before passing. No automated reduction yet.
4. **EEResult schema versioning** — `schema_version: 1` field exists but no migration story.

## Related

- `../../investigations/data-model-and-flow.md` entity #7 (`EEResult` full schema), engine-call flow diagram.
- `../../investigations/ee-project-organization.md` Pattern 4 (verification ladder).
- `../../investigations/gradual-implementation-plan.md` Phase 5 (creation) + Phase 6 (adapters).
- Sibling module: `core/` (provides `Subsystem` model that adapters consume).

## Status

`planned` — Phase 5 of gradual-implementation-plan. Owner: TBD. Last updated: 2026-05-23.
