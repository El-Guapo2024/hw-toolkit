# hw_toolkit

Jupyter-first Python library for hardware (KiCad) schematic design.
Engineers write one notebook cell per module, declare nets as buckets,
and export a finalized `.kicad_sch` zip ready for phase-2 hand-tune
in eeschema.

```python
import hw_toolkit as hw

board = hw.Board("control_hub_v1")

# One subsystem per cell — pick part, run math, check thermal.
buck = board.module(
    id="buck_3v3", category="buck_converter",
    mpn="TPS54331DR", package="SOIC-8",
)
buck.attach(hw.calc.Buck(vin=11.1, vout=3.3, iout=0.5))
buck.check(buck.math.thermal(rdson_mohm=80, theta_ja=40))
buck.show()

mcu = board.module(id="mcu", category="mcu_module",
                   mpn="ESP32-S3-WROOM-1-N16R8")
imu = board.module(id="imu", category="imu_i2c", mpn="LSM6DSOXTR")

# Nets are buckets — pins drop in.
v3v3 = board.power("rail_3v3", voltage_v=3.3)
v3v3 += "buck_3v3.VOUT", "mcu.VDD", "imu.VDD"

gnd = board.gnd()
gnd += "buck_3v3.GND", "mcu.GND", "imu.GND"

sda, scl = board.i2c("bus0")
sda += "mcu.SDA", "imu.SDA"
scl += "mcu.SCL", "imu.SCL"

# Validate + ship.
board.check_erc()
board.show()
zip_path = board.export_kicad("control_hub_v1.zip", unzip=True)
# → engineer opens .kicad_pro in eeschema, hand-tunes, runs PCB layout, fab.
```

## Core concepts

- **`Board`** — the one object a notebook holds. Owns parts, nets, and the
  scratch dir KiCad files live in.
- **`Module`** — the matplotlib-style handle returned by `board.module(...)`.
  Carries math, runs checks, renders inline via `.show()`.
- **`Net`** — a bucket of pins (SKiDL / atopile style).
  `net += "sub.port", "sub.port"` joins. Star-expands at bundle time.
- **`Board.export_kicad("foo.zip")`** — the phase-2 handoff artifact.
  Engineer unzips, opens `.kicad_pro` in eeschema.
- **`Board.export_spice("foo.cir")`** — Berkeley-SPICE netlist for
  downstream simulation (topology only — engineer supplies models).

## Installation

```bash
pip install -e .
```

Requires:
- Python 3.11+
- KiCad 9 (`kicad-cli` on PATH or at `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`)

## Test

```bash
pytest -q          # 41 unit tests
ruff check hw_toolkit/   # 0 lint errors
```

## Layout

```
hw_toolkit/
├── core/         pydantic models (SubsystemPick, Interface, ResearchBundle)
├── calc/         engineering math wrappers (Buck operating point, thermal)
├── kicad/        the KiCad backend (planner, sch_ops, kicad-cli wrappers)
├── spice/        SPICE netlist emitter
├── board.py      Board + Module + Net (the public surface)
└── exceptions.py typed exception hierarchy
```

## Companion: MCP servers

`mcp_server/` holds the research + KiCad-live-edit MCP servers used by
Claude Code agents during the part-picking phase (before the notebook).
Not required to use the library.

## License

MIT — see [LICENSE](LICENSE).
