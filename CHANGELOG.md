# Changelog

All notable changes to `hw_toolkit` are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses [Semantic Versioning](https://semver.org/).

## [0.2.0] — 2026-05-26

### Added
- `Board.power(id, voltage_v)` / `Board.gnd()` / `Board.i2c(id)` /
  `Board.signal(id, protocol=...)` — factory shortcuts for the common
  net types so the engineer writes one line instead of three kwargs.
- `Module.attach(math)` chainable mutation as an alternative to
  `module.math = ...`.
- `Module.set_value(...)` / `Module.set_footprint(...)` — matplotlib-style
  late-mutation setters, both chainable.
- `Board.parts` / `Board.nets` — read-only mapping views.
- `Board.summary()` — one-screen text overview.
- `Board.export_kicad(zip_path, unzip=True)` — drops an unpacked copy
  alongside the zip for quick inspection.
- Typed exceptions: `CheckFailed`, `DuplicateNetError`, `EmptyNetError`,
  `UnknownSubsystemError`, `KiCadCliTimeoutError`, `NoSvgProducedError`.
- `py.typed` marker so downstream `mypy` sees the type hints.
- `__version__` constant at the package root.
- Basic `tests/` suite (16 unit tests covering the public API surface).

### Changed
- Planner now injects one `power:PWR_FLAG` per declared power net that
  lacks a `power_out` source pin, wired to the hub. KiCad ERC sees every
  rail as driven.
- GND drops are wired to their IC's GND pin (previously floated).
- `pick.package` is normalized to fully qualified `Library:Footprint`
  via `_PACKAGE_TO_KICAD_FP`; unknown packages emit an empty Footprint.
- Pin coords are snapped to KiCad's 1.27mm schematic grid.
- Pin `electrical_type` (`power_in`/`power_out`/`bidirectional`/`passive`)
  derived from port name + category — ERC can validate driver rules.
- `kicad-cli sch erc` invocation passes `-D KIPRJMOD=<sch.parent>` so the
  project-local `sym-lib-table` interpolation resolves.
- `Board(..., scratch_dir=...)` — KiCad scratch lives under `/tmp` by
  default; no folder artifacts in the engineer's tree until
  `export_kicad()`.
- `register_extra_lib_path()` is now a `contextvar` — no process-global
  list races between concurrent `Board` instances.
- Voltage canonicalization uses `math.isclose` (was `==`).
- `kicad_sch_api` logger raised to `ERROR` level — suppresses noisy
  `power:+PWR_FLAG not found` chatter on the expected fallback path.

### Removed
- The per-consumer floating power-label drops (planner step 3). They
  produced unwired `power_in` symbols on isolated nets which drove every
  prior ERC error.
- `hw_toolkit.schematic` — `Schematic` was an unnecessary indirection;
  its behaviour is absorbed into `Board`.
- Stale pre-pivot subdirs under `docs/projects/control_hub_v1/`
  (`components/`, `kicad/`, `render/`, `subsystems/`).

### Fixed
- `write_populated` only wipes `hwagent.kicad_sym` + `sym-lib-table` in
  directories carrying a `.hw_toolkit_scratch` marker — never destroys
  user-owned project libraries.
- `kicad-cli` invocations gain a 60s default timeout
  (`HW_TOOLKIT_KICAD_CLI_TIMEOUT` overrides).

## [0.1.0] — 2026-05-25

### Added
- Initial `hw_toolkit` package — `Board`, `Module`, `Net`, `calc.Buck`,
  exception hierarchy, `kicad-cli` subprocess wrappers, KiCad project
  zip export.
- `control_hub_v1` reference notebook demonstrating the schematic-only
  flow end-to-end.
