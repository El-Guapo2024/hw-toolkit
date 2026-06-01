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
board.export_kicad("foo.zip", unzip=True)  # auto-runs ERC before zipping
board.export_spice("foo.cir")           # optional
```

### Real KiCad symbols (use them — don't synthesize)

`board.module(...)` auto-resolves a real KiCad library symbol + footprint
when it can (`hw_toolkit/kicad/resolve.py`): passives → `Device:R/C/L`,
catalogued ICs → their real `lib_id`. Real symbols emit NO
`lib_symbol_issues` / `footprint_link_issues`, so a fully-real board gates
on the tighter `hw.ERC_REAL_SYMBOL_CODES` instead of the full baseline.

```python
# Force a specific symbol (net port names must match its real pin names):
mcu = board.module(id="u1", category="mcu", mpn="STM32F042K6Tx",
                   package="LQFP-32", lib_id="MCU_ST_STM32F0:STM32F042K6Tx")
```

If a part isn't in any installed lib it synthesizes a placeholder (the old
behavior) and you keep the full `hw.ERC_BASELINE_CODES`. Prefer adding a
recurring IC to the resolver catalog over letting it synthesize. NOTE:
checking that a `.kicad_sym` FILE exists does NOT prove the symbol is in
it — the resolver validates via `lib.load_symbol`; you should too.

### Full block factories

`hw_toolkit.parts.Buck` builds a whole regulator (IC + Cin/Cout/L/Cboot +
feedback divider, auto-wired) from component values — you don't place the
passives yourself:

```python
from hw_toolkit.parts import Buck
buck = Buck(board, id="buck_3v3", mpn="TPS54302", package="SOT-23-6",
            vin=12.0, vout=3.3, l="10uH", cin="10uF", cout="22uF",
            cboot="100nF", rtop="31.6k", rbot="10k")
source.power_out.connect_to(buck.power_in)   # typed
buck.power_out.connect_to(mcu.power_in)       # output is the inductor node
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

### Seeing the schematic as you build

Call `board.show()` to render the current schematic inline (scaled SVG) —
put it after subsystem cells so the engineer watches the board grow step
by step, not just at the end:

```python
mcu = board.module(...)
sensor = board.module(...)
board.show()        # inline render so far
```

`render_sch_svg(board.sch_path, out_dir=...)` writes a standalone `.svg`
file (and you can rsvg-convert it to PNG) for a VS Code pane. Always
render the final schematic before finishing — don't hand back only a zip.
Layout is auto-gridded; wires are point-to-point (some crossings are
expected), so judge correctness from ERC, not from wire aesthetics.

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
| `UnknownSubsystemError: 'mcu'` | net member references a subsystem id that was never `.module()`'d (e.g. typo `"mcu.VDD"` vs id `"mcu0"`) | fix the id in the `+=` member; runs implicitly on any write/export/ERC |

### 6.1 — `expected_codes` is the escape valve (use it!)

Most ERC failures on a hw_toolkit schematic are NOT real bugs — they're
auto-layout / synthesized-symbol artifacts. Use the exported constants
instead of hand-typing the tuple:

```python
import hw_toolkit as hw

board.check_erc(expected_codes=hw.ERC_BASELINE_CODES)      # any synthesized parts
board.check_erc(expected_codes=hw.ERC_REAL_SYMBOL_CODES)   # all parts real symbols
```

- `hw.ERC_REAL_SYMBOL_CODES` — the 4 auto-layout topology artifacts every
  board has (intentional NCs, rails tied straight to pins, PWR_FLAG-less
  connector pins, synthesized wire fragments).
- `hw.ERC_BASELINE_CODES` — the above **plus** `lib_symbol_issues` and
  `footprint_link_issues`, which only a SYNTHESIZED placeholder emits. A
  board whose parts all resolved to real symbols needs neither — gate on
  `ERC_REAL_SYMBOL_CODES`.

`board.export_kicad(...)` runs ERC itself with `ERC_BASELINE_CODES` by
default (pass `erc=False` to skip, or `expected_codes=` to override), so a
separate `check_erc()` cell is optional. Only add MORE codes if a new
violation appears AND you confirm it's an artifact, not a wiring bug.

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
