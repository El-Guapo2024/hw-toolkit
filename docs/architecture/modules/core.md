# Module Design — `hw_agent/core/`

## Purpose

Defines the canonical data model for the entire harness — `Subsystem`, `ChosenPart`, `Decision`, `ExaminedCandidate`, `SubsystemStatus`, `ProjectStatus`. Plus orchestration primitives (`orchestrator.py`, `investigator.py`, `design_tree.py`) that work over those models.

This module is the **shared vocabulary** every other layer speaks. If you change a model here, you change the entire harness.

## Public contract

Pydantic models (in `subsystem.py`):
- `ElectronicSubsystem` — the canonical project subsystem entity. Has `Requirements`, `Actuals`, `ChosenPart`, `decisions[]`, `candidates_examined[]`, `calculations` (planned).
- `ChosenPart` — committed MPN + sourcing details. BOM derives from this.
- `Decision` — append-only history of part choices, rejected alts, rationale, tradeoffs, accepted_warnings.
- `ExaminedCandidate` — audit trail of every part considered during research.
- `SubsystemStatus` — point-in-time check snapshot per subsystem.
- `ProjectStatus` — aggregated status across all subsystems.
- `CheckResult` — single rule check outcome (name, severity, status, actual, required).

Orchestration (in `orchestrator.py`, `investigator.py`, `design_tree.py`):
- `investigate(part_number, ...)` — datasheet research orchestrator.
- Design-tree traversal helpers.

Persistence helpers (in `artifacts/project_state/subsystems.py`):
- `subsystem_load(project, name) -> ElectronicSubsystem | None`
- `subsystem_save(subsystem, project) -> Path`
- `subsystem_list(project) -> list[str]`
- `subsystem_delete(project, name) -> bool`
- `aggregate_project_status(project) -> ProjectStatus`

**Side effects:** persistence helpers read/write `docs/projects/<project>/subsystems/<name>.json`. Models themselves are pure.

## Internal layout

```
hw_agent/core/
├── __init__.py
├── subsystem.py          # all pydantic models, ~315 lines
├── orchestrator.py
├── investigator.py
├── design_tree.py
├── freerouting.py        # router service client (may move to mcp_server/router/)
├── preview.py            # live web dashboard server (large; may extract)
└── subsystems related persistence at hw_agent/artifacts/project_state/
```

## Dependencies

**Imports from:**
- `pydantic` — model layer.
- `hw_agent.artifacts.project_state.*` — persistence helpers (imports from `core`, not the other way around).
- `hw_agent.domain.checks` — check pipeline (being refactored).

**Imported by:** essentially everything — `ee/`, `library/`, `skills/`, MCP servers all depend on these models. This module is the bottom of the stack.

**Forbidden imports:**
- ❌ `from mcp_server.*` (core must not depend on transport)
- ❌ `from hw_agent.skills.*`
- ❌ `from hw_agent.scripts.*`

## Configuration

Persistence path via `HW_PROJECTS_ROOT` env var (default: `docs/projects/`).

## Lifecycle / state

Models are stateless value types. Persistence layer (`artifacts/project_state/`) handles state: load → mutate in-process → save. File-per-subsystem JSON on disk is the source of truth.

## Failure modes

- `subsystem_load` returns `None` if file missing. Never raises.
- Malformed JSON → pydantic `ValidationError`.
- `subsystem_save` overwrites atomically (write to temp + rename). On disk-full, raises `OSError`.

## Performance characteristics

- Model construction / validation: <1 ms per subsystem.
- Disk save: 1–5 ms per file.
- `aggregate_project_status`: O(N) over subsystems; <100 ms for 50 subsystems.

## Testing

- `hw_agent/core/` does not currently have its own test directory. **TBD as part of next touch.**
- Indirect coverage from `mcp_server/designer/` tool tests.

## Open questions / known limitations

1. **Provenance on Actuals** — not yet modeled. Phase 1 of gradual plan adds it.
2. **Schema versioning** — no `schema_version` field on `ElectronicSubsystem`. Add when first migration is needed.
3. **`calculations` field** — exists as `dict` on `ExaminedCandidate`. Needs promotion to top-level `EEResult` list after Phase 5.
4. **`preview.py` is 778 lines** — bigger than the rest of core combined. Should likely extract to `hw_agent/dashboard/` as its own module.
5. **`freerouting.py`** — feels misplaced in `core/`. Belongs in `mcp_server/router/` or a `hw-router-service/` client module.
6. **No test coverage on the models themselves.** Pydantic catches most issues but invariants (e.g. "iout_max > 0") aren't tested.

## Related

- `../../investigations/data-model-and-flow.md` entity #2 (`Subsystem`).
- `../../investigations/ee-project-organization.md` — references `core/subsystem.py:42-176` repeatedly.
- `../../investigations/gradual-implementation-plan.md` Phases 1, 5, 7 all touch `core/`.

## Status

`mature` — model is stable, used everywhere. Provenance + schema_version pending (Phase 1). Owner: TBD. Last updated: 2026-05-23.
