# AGENT_GUIDE.md — building a project with hw_toolkit

You are a hardware-design agent. Your job is to take a project spec and ship
a **working, executed Jupyter notebook** that builds the schematic with
`hw_toolkit`, runs ERC clean, and exports a `.zip`. Iterate until it works.

## 0 — Environment

```
repo root:    /Users/juanantonioluera/ws/hw-toolkit
python venv:  /Users/juanantonioluera/ws/hw-toolkit/.venv
jupyter:      /Users/juanantonioluera/ws/hw-toolkit/.venv/bin/jupyter
kicad-cli:    /Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli (auto-resolved by hw_toolkit)
```

Always activate the venv before running anything:

```bash
source /Users/juanantonioluera/ws/hw-toolkit/.venv/bin/activate
```

## 1 — Deliverables for each project

Project lives at `docs/projects/<project_id>/`:

```
<project_id>/
├── <project_id>.ipynb         ← source notebook you write
├── <project_id>.executed.ipynb ← jupyter nbconvert writes this
├── DESIGN.md                  ← human-readable design doc
└── <project_id>.zip           ← board.export_kicad() output (KiCad project)
```

## 2 — The iteration loop (this is the job)

```
1. Write <project_id>.ipynb
2. Execute it:
   .venv/bin/jupyter nbconvert --to notebook --execute \
       docs/projects/<project_id>/<project_id>.ipynb \
       --output <project_id>.executed.ipynb
3. If ANY cell errors:
   - Read the traceback
   - Fix the .ipynb
   - Go to step 2
4. Done only when:
   - All cells executed without error
   - board.check_erc() passed (no MultipleERCViolations)
   - <project_id>.zip exists in the project dir
```

**Do not stop after writing the notebook. Execute it. Fix it. Repeat until clean.**

## 3 — Full hw_toolkit.Board API (use ONLY these — don't invent methods)

```python
import hw_toolkit as hw

board = hw.Board("project_id")    # scratch dir auto-tempdir

# --- add subsystems ---
mod = board.module(id="x", category="...", mpn="...", package="...",
                   price_usd=0.0, manufacturer="...")
r1  = board.resistor("R1", "10k", package="0603")
c1  = board.capacitor("C1", "100nF", package="0603")
l1  = board.inductor("L1", "10uH", package="0805")

# --- math + checks (optional) ---
buck.attach(hw.calc.Buck(vin=11.1, vout=3.3, iout=0.5))
buck.check(buck.math.thermal(rdson_mohm=80, theta_ja=40))

# --- power nets ---
v3v3 = board.power("3v3", voltage_v=3.3)
v5   = board.power("5v",  voltage_v=5.0)
gnd  = board.gnd()                              # id defaults to "gnd"
vp, vn = board.dual_supply("a15", vpos=15, vneg=15)

# --- bus factories (these are the ONLY net helpers — no .i2s/.spi variants
# that don't exist here) ---
sda, scl                  = board.i2c("bus0")           # 2 nets
mosi, miso, sck, cs       = board.spi("flash")          # 4 nets
tx, rx                    = board.uart("dbg")           # 2 nets
bclk, lrck, data          = board.i2s("audio")          # 3 nets
swdio, swdclk, nreset     = board.swd()                 # 3 nets, default id "swd"
canh, canl                = board.can("bus")            # 2 nets
left, right               = board.stereo("out", protocol="analog")  # 2 nets
pos, neg                  = board.diff_pair("usb1", protocol="usb") # 2 nets
usb                       = board.usbc("conn0")         # dict[str, Net]
sig                       = board.signal("led_pwm", protocol="pwm") # 1 net

# protocol enum (for signal/diff_pair):
# i2c, spi, uart, can, usb, swd, i2s, analog, gpio, pwm, onewire

# --- joining pins to nets — Net += "id.PIN", ... ---
v3v3 += "buck.VOUT", "mcu.VDD", "imu.VDD"
gnd  += "buck.GND",  "mcu.GND", "imu.GND"
sda  += "mcu.SDA",   "imu.SDA"
usb["vbus"] += "conn0.VBUS", "buck.VIN"

# --- intentionally-unused pins ---
nc = board.nc("usb_sbu1_nc")
nc += "conn0.SBU1"

# --- finalize ---
board.check_erc()                       # raises MultipleERCViolations
board.export_kicad("foo.zip", unzip=True)
board.export_spice("foo.cir")           # optional
```

### What does NOT exist (do not call):
- `board.i2c1`, `board.i2c_bus` (use `board.i2c(id)`)
- `board.gpio`, `board.pwm` (use `board.signal(id, protocol="pwm")`)
- `board.analog` (use `board.signal(id, protocol="analog")` — also the default)
- `board.add_resistor` (use `board.resistor(...)`)
- `board.connect(mod, "PIN", net)` exists but is rarely needed — prefer `net += "mod.PIN"`

### Net constraints (will trip ERC if violated):
- A net with **only one member** raises `EmptyNetError` at bundle time. If a
  pin is genuinely unused, route it through `board.nc(id)` so it gets the
  `external.NC` sentinel as a second member.
- Pin names must match the symbol's pin names. For ICs without a known
  symbol, hw_toolkit synthesizes a generic placeholder — pin names you
  reference become the symbol's pins. Be consistent.

## 4 — Notebook structure (write it like this)

```
Cell 1 (markdown):  # <project> — title + 1-line spec
Cell 2 (code):      import hw_toolkit as hw; board = hw.Board("<id>")
Cell 3 (code):      mcu = board.module(...)            # one cell per subsystem
Cell 4 (code):      sensor = board.module(...)
...
Cell N-3 (code):    v3v3 = board.power(...); v3v3 += ...   # net wiring
Cell N-2 (code):    board.summary()                         # optional sanity
Cell N-1 (code):    board.check_erc()
Cell N (code):      board.export_kicad("<id>.zip", unzip=True)
```

Keep cells small — one module per cell makes ERC failures easy to localize.

## 5 — Executing the notebook

```bash
cd /Users/juanantonioluera/ws/hw-toolkit
.venv/bin/jupyter nbconvert --to notebook --execute \
    docs/projects/<project_id>/<project_id>.ipynb \
    --output <project_id>.executed.ipynb \
    --ExecutePreprocessor.timeout=120
```

If a cell errors, the traceback is embedded in `<project_id>.executed.ipynb`.
Read it with: `python -c "import nbformat; nb=nbformat.read('path', 4); print([c.outputs for c in nb.cells if c.cell_type=='code'])"` or just open the file and grep for `"ename"`.

## 6 — Common errors + fixes

| Error | Cause | Fix |
|---|---|---|
| `EmptyNetError: net 'foo' has 1 member` | only one pin joined | add another pin or route through `board.nc(...)` |
| `AttributeError: 'Board' object has no attribute 'X'` | invented method | check section 3 — use only what's listed |
| `ValidationError: id should match pattern '^[a-z][a-z0-9_]*$'` | net id starts with digit | rename `"3v3"` → `"v3v3"`, `"5v"` → `"v5"`, etc. IDs MUST start with `[a-z]`. |
| `ValueError: net member must be '<subsystem>.<port>'; got 'X'` | free-form pseudo-member string | a `+=` member MUST be `"sub_id.PORT"`. To tie a pin to a power rail, add it to the rail net directly: `gnd += "led.K"`. Never invent placeholder strings like `"v3v3_pot_tie"` or `"gnd_led_k"`. |
| `KeyError` / subsystem id not found in net member | used Python varname instead of refdes id | `board.resistor("R1", ...)` → subsystem id is `r1` (lowercased refdes), not your Python variable name. Net member is `"r1.A"`, NOT `"r_led.A"`. Same for `capacitor("C3", ...)` → `"c3.POS"`. |
| `MultipleERCViolations` | KiCad ERC flagged synthesized-schematic noise | mostly NOT real bugs — see §6.1 expected_codes. |
| `KiCadCliNotFound` | kicad-cli missing | macOS path is `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli` — should auto-resolve |
| `ValidationError` on `board.module(...)` | required field missing | `id, category, mpn, package` are required |

### 6.1 — `expected_codes` is the escape valve (use it!)

Most ERC failures on a synthesized hw_toolkit schematic are NOT real bugs.
They are artifacts of the auto-generated symbol library. Start every
project with this exact tuple:

```python
board.check_erc(expected_codes=(
    "pin_not_connected",         # intentional NCs (USB-C SBU/data on charge-only, MCP73831 STAT, etc.)
    "lib_symbol_issues",         # hwagent lib synthesized at runtime, not registered globally
    "pin_to_pin",                # rails tied directly to pins (e.g. LDO EN to VBAT for always-on)
    "power_pin_not_driven",      # connector power pins without PWR_FLAG
    "unconnected_wire_endpoint", # synthesized wire-layout artifact
))
```

Only add MORE codes if a new violation appears AND you confirm it's a
synthesis artifact, not a wiring bug. Do not remove codes from this baseline.

### 6.2 — Multi-device buses (CS lines)

`board.spi("flash")` returns one `(mosi, miso, sck, cs)` 4-tuple. The single
`cs` is fine for a single peripheral. For N devices on the same SPI bus,
share `mosi/miso/sck` but declare ONE `board.signal("devN_cs", protocol="spi")`
per device:

```python
mosi, miso, sck, _shared_cs_unused = board.spi("enc")  # take 3, ignore the 4th
mosi += "mcu.MOSI", "enc1.MOSI", "enc2.MOSI"
miso += "mcu.MISO", "enc1.MISO", "enc2.MISO"
sck  += "mcu.SCK",  "enc1.SCK",  "enc2.SCK"
enc1_cs = board.signal("enc1_cs", protocol="spi"); enc1_cs += "mcu.PA4", "enc1.CSN"
enc2_cs = board.signal("enc2_cs", protocol="spi"); enc2_cs += "mcu.PA3", "enc2.CSN"
```

If you don't use the bundled `cs` net, it will trip `EmptyNetError` (1 member).
Either fill it or skip the helper entirely and declare all 4 lines manually.

## 7 — Export path (avoid double-nesting)

`nbconvert` runs from the repo root by default, so a relative path inside
`board.export_kicad(...)` resolves from there. But if cwd differs, the zip
lands at `<cwd>/<rel_path>` and you get a nested directory.

**Always use an absolute path:**

```python
import pathlib
out = pathlib.Path("/Users/juanantonioluera/ws/hw-toolkit/docs/projects/<project_id>/<project_id>.zip")
board.export_kicad(out, unzip=True)
```

## 8 — Reporting back

When you finish, report in <150 words:
- Files written (with absolute paths)
- MPN highlights
- Whether `check_erc()` passed and on which iteration
- Any violations you suppressed via `expected_codes=(...,)` and why
