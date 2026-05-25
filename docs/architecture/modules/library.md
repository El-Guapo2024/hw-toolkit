# Module Design — `hw_agent/library/`

## Purpose

Org-wide vetted, versioned, reusable design assets — subsystem templates, interface specs, gate definitions, protocol descriptors. The library is what makes projects fast: most subsystems pull from a template + override a few fields, instead of being designed from scratch.

Library entries are **immutable once released**. New versions bump semver. Projects pin to a version range.

## Public contract

Library is read-only at runtime (loaded as YAML). Public surface:

- **`library/subsystems/<category>/v<X.Y.Z>/template.yaml`** — `SubsystemTemplate` records. One immutable file per version.
- **`library/interfaces/<name>.yaml`** — `InterfaceTemplate` records (USB-C 5V, I²C 3.3V, etc.).
- **`library/gates/<name>.yaml`** — `Gate` definitions: which checks run, severity, what stage they block.
- **`library/protocols/<name>.yaml`** — protocol descriptors (i2c, spi, can, uart) — typed for use by interface specs.
- **`hw_agent.library.resolve(template_id, version_constraint)`** — Python loader that resolves a semver constraint against available versions and returns a parsed template.

**Side effects:** none. Pure data + a loader.

## Internal layout

```
hw_agent/library/
├── __init__.py
├── resolve.py             # semver constraint solver + YAML loader + cache
├── subsystems/
│   ├── buck_converter/
│   │   ├── v1.0.0/template.yaml
│   │   ├── v1.1.0/template.yaml
│   │   └── v1.2.0/template.yaml
│   ├── ldo/
│   ├── esp32_s3_module/
│   ├── tmc2209_driver/
│   └── ...
├── interfaces/
│   ├── power_rail_dc.yaml
│   ├── i2c_bus.yaml
│   ├── spi_bus.yaml
│   ├── uart.yaml
│   └── usb_c_5v_3a.yaml
├── gates/
│   ├── spec_to_design.yaml
│   └── design_to_fab.yaml
├── protocols/
│   ├── i2c.yaml
│   ├── spi.yaml
│   └── can.yaml
└── tests/
    └── test_resolve.py
```

**Data flow:** project's `manifest.yaml` declares `library_refs` with semver constraints → `resolve()` finds matching versions → returns parsed pydantic models for project code to consume.

## Dependencies

**Imports from:**
- `hw_agent.core` — for `SubsystemTemplate`, `InterfaceTemplate`, `Gate` pydantic models.
- `packaging.version` or `semver` — for constraint solving.
- `pyyaml` — for loading.

**Imported by:**
- `hw_agent/skills/designer/` — instantiates subsystems from templates.
- `hw_agent/scripts/gate_runner.py` — loads gate definitions.
- `mcp_server/designer/server.py` — `subsystem_add` tool consumes templates.

**Forbidden imports:**
- ❌ `from hw_agent.skills.*` (library must not know about skills)
- ❌ `from mcp_server.*`
- ❌ Templates may not import Python — they are pure YAML.

## Configuration

- Path to library root via `HW_LIBRARY_PATH` env var (default: `<repo>/hw_agent/library/`).
- Cache TTL via `HW_LIBRARY_CACHE_SECONDS` (default: 3600).

## Lifecycle / state

Stateless module. Loader has an in-process cache keyed on `(id, version)`. Cache invalidates on file mtime change.

**Templates are immutable.** A v1.2.0 file, once committed, never changes. To fix a typo, release v1.2.1. Breaking changes bump major.

## Failure modes

- Constraint with no match → `LibraryResolveError("no version of buck_converter matches ^2.0.0; available: 1.0.0, 1.1.0, 1.2.0")`.
- Malformed YAML → pydantic `ValidationError` at load time. Hard fail.
- Conflicting transitive constraints (project A uses `^1.0`, transitive ref needs `^2.0`) → `LibraryConflictError`.

## Performance characteristics

- First resolve of a template: 10–30 ms (yaml parse + pydantic).
- Cached resolve: <1 ms.
- 100-template library loads in <2 seconds full-scan.

## Testing

- `hw_agent/library/tests/test_resolve.py` — semver constraint matrix.
- Integration: each project's `manifest.yaml` must resolve cleanly (CI check).
- Schema lint: every `template.yaml` validates against `SubsystemTemplate` model.

## Open questions / known limitations

1. **Where do user-private templates live?** Today: all in-repo. If a user wants private subsystem designs, need a discovery mechanism for `~/.hw_library/` or similar.
2. **Distribution strategy.** Initially in-repo. At maturity, may split to standalone repo + git submodule or pip package. Defer until library grows past ~50 templates.
3. **Template inheritance.** Should `buck_synchronous` extend `buck_converter`? Currently no — flat. May add later if duplication appears.
4. **Library-of-libraries.** Org-shared vs personal vs project-local templates. Today: one tier. Tiered lookup possible later.
5. **Migration helpers.** When v1 → v2 of a template is breaking, projects pinned to v1 need an explicit upgrade path. Currently no `migrate_v1_to_v2.py` convention; add when first breaking bump happens.

## Related

- `../../investigations/data-model-and-flow.md` entities #1, #5, #10, #11 (`SubsystemTemplate`, `InterfaceTemplate`, `Gate`, `ProjectManifest`); section "Reusability — library + project split."
- `../../investigations/gradual-implementation-plan.md` Phase 7 (creation).
- Sibling module: `core/` (defines the pydantic models for library entries).

## Status

`planned` — Phase 7 of gradual-implementation-plan. Templates extracted from existing `hw_agent/domain/templates/` logic. Owner: TBD. Last updated: 2026-05-23.
