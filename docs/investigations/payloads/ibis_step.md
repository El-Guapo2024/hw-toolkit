# IBIS & STEP 3D Files: Tooling Analysis for hw-toolkit

## 1. IBIS (I/O Buffer Information Specification)

### What It Is
IBIS is a behavioral I/O model format (text-based, maintained by IBIS Open Forum / SAE International) used for signal-integrity (SI) analysis of digital interfaces. Unlike SPICE (which simulates transistor-level physics), IBIS describes the gross electrical behavior of a chip's input/output buffers: rise/fall times, pull-up/pull-down curves, clamping behavior.

### Real IBIS File Example (Snippet)
```ibis
[IBIS Ver] 5.1
[File Name] SN74HC595.ibs
[File Rev] 1.0
[Source] Texas Instruments
[Date] 05/01/2020
[File Format] ASCII

[Component] SN74HC595
[Manufacturer] Texas Instruments
[Description] 8-bit shift register with storage register and tri-state outputs

[Package]
[Typ] DIP16

[Pin] 1 QA
[Pin] 2 QB
[Pin] 3 QC
[Pin] 4 QD
[Pin] 5 QE
[Pin] 6 QF
[Pin] 7 QG
[Pin] 8 GND
[Pin] 9 QH
[Pin] 10 SRCLR
[Pin] 11 SRCLK
[Pin] 12 SER
[Pin] 13 OE
[Pin] 14 RCLK
[Pin] 15 VCC
[Pin] 16 NC

[Model] QOutput
[Model Type] Output
[Polarity] Non-Inverting
[Enable] Active High
[Ramp] I_Power_up
|Time(ns) I(mA)
0.0       0.0
1.0       10.0
2.0       20.0
3.0       25.0

[Pull Up]
|V(V)  I(uA)
0.0    0.0
1.5    100.0
3.0    250.0
5.0    400.0

[Pull Down]
|V(V)  I(uA)
0.0    400.0
1.5    100.0
3.0    50.0
5.0    0.0

[GND Clamp]
|V(V)  I(mA)
0.0    0.0
-0.5   -10.0
-1.0   -50.0

[Power Clamp]
|V(V)  I(mA)
5.0    0.0
5.5    5.0
6.0    50.0

[Model] GNDPin
[Model Type] Ground

[Model] VCCPin
[Model Type] Power

[Model] InputPin
[Model Type] Input
[Ramp] I_Typical
|Time(ns) I(mA)
0.0       0.0
1.0       2.0
2.0       3.0
```

### Key Sections
- **[Component]**: Part metadata (name, manufacturer, package)
- **[Pin]**: Maps pin numbers to signal names (QA, QB, GND, VCC, etc.)
- **[Model]**: I/O buffer type (Output, Input, Power, Ground)
  - **[Ramp]**: Rise/fall time curves (V vs. time for power-up/down)
  - **[Pull Up] / [Pull Down]**: V-I curves showing how the output transistor sources/sinks current
  - **[GND Clamp] / [Power Clamp]**: Clamp diode behavior for ESD protection
- **[Typ/Min/Max]**: Temperature/voltage corners (optional, usually three corners)

### When Manufacturers Ship IBIS
- **Universally available for**: All digital logic ICs (MCUs, FPGAs, shift registers, drivers) from major vendors (TI, NXP, Microchip, STM, Intel, Xilinx, etc.)
- **Location**: Often a separate download on Digi-Key, Mouser, or manufacturer datasheets
- **Common file extension**: `.ibs`

### Why hw-toolkit Would Need It
High-speed digital designs (USB 2.0 FS/HS, Ethernet PHY, fast SPI) benefit from SI simulations to verify:
- Eye diagram opening (signal integrity margin)
- Reflections and crosstalk (trace length matching, impedance)
- Setup/hold time violations at the receiver
Tools like HyperLynx, ADS, or open-source alternatives (e.g., ngspice + IBIS) use these models.

---

## 2. STEP 3D Files

### What It Is
STEP (Standard for the Exchange of Product model data, ISO 10303) is a 3D CAD file format (text-based ASCII or binary) that represents solid geometry: vertices, edges, faces, and complete assemblies. KiCad supports STEP export to verify mechanical clearance.

### Real STEP File Example (Header + Geometry)
```step
ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('3D model of a PCB capacitor footprint'),
  '2;1');
FILE_NAME('cap_0805.step',
  2026-05-24T12:00:00,
  (''),
  (''),
  'KiCad',
  'KiCad 8.0',
  '');
FILE_SCHEMA(('CONFIG_CONTROL_DESIGN'));
ENDSEC;
DATA;
#10=AXIS2_PLACEMENT_3D('placement',#20,#30,#40);
#20=CARTESIAN_POINT('origin',(0.,0.,0.));
#30=DIRECTION('x-axis',(1.,0.,0.));
#40=DIRECTION('z-axis',(0.,0.,1.));
#50=RECTANGULAR_BOX('capacitor_body',#10,2.0,1.25,1.5);
#60=ADVANCED_FACE('',(#70),#50,.T.);
#70=FACE_BOUND('pad_area',#80,.T.);
#80=EDGE_LOOP('',(#90,#100,#110,#120));
#90=ORIENTED_EDGE('',*,*,#91,.T.);
#91=EDGE_CURVE('',#92,#93,#94,.UNSPECIFIED.);
#92=VERTEX_POINT('',#95);
#93=VERTEX_POINT('',#96);
#94=LINE('',#95,#97);
#95=CARTESIAN_POINT('p1',(0.,0.,0.));
#96=CARTESIAN_POINT('p2',(2.0,0.,0.));
...
ENDSEC;
END-ISO-10303-21;
```

### Structure
- **Header**: File metadata (filename, timestamp, source tool)
- **Data section**: Numbered entities (#10, #20, etc.) defining geometry
  - Points, vectors, directions
  - Curves, edges, loops (topology)
  - Faces, shells, solids
  - Assembly references (STEP assemblies reference sub-components)

### Sources
- **Manufacturer 3D models**: Available on Digi-Key, Mouser, distributor product pages
- **MCAD libraries**: sites like 3dcontentcentral.com, snapeda.com, GrabCAD
- **Hobby**: Adafruit, SparkFun, Arduino designs publish STEP for enclosures
- **Generated**: `kicad-cli pcb export step` produces STEP from a fully placed board

### KiCad CLI Export Command
```bash
kicad-cli pcb export step --output design.step myboard.kicad_pcb
```

**Key options:**
- `--output`: Output filename (`.step` or `.stp`)
- `--board-only`: Board outline + copper, no components
- `--no-components`: Skip all 3D component models (faster)
- `--no-dnp`: Skip do-not-populate components
- `--component-filter`: Include only specified refdes (e.g., `--component-filter R1,C1,U1`)
- `--user-origin`: Shift coordinate origin (useful for enclosure alignment)

### Why hw-toolkit Would Need It
Any board designed for a custom enclosure, 3D-printed housing, or mechanical assembly needs STEP export to:
- Verify component heights don't collide with lid/walls
- Align connectors with cutouts
- Plan cable routing, heatsink mounting
- Validate assembly drawings

---

## 3. Current hw-toolkit Contract Gap

### What `SubsystemPick.actuals` Currently Holds
Defined in `/Users/juanantonioluera/ws/hw-toolkit/hw_agent/core/subsystem.py`:
```python
class ChosenPart(BaseModel):
    lcsc: str
    mpn: str
    manufacturer: str = ""
    description: str = ""
    package: str = ""
    price: float = 0.0
    price_tiers: dict[str, float] = Field(default_factory=dict)
    stock: int = 0
    datasheet_url: str = ""
    library_type: str = "extended"
    qty_per_board: int = 1
    notes: str = ""
```

**Missing**: No `ibis_model_url` or `step_3d_url` fields.

### What `FabBundle` Currently Holds
Defined in `/Users/juanantonioluera/ws/hw-toolkit/hw_agent/core/fab_bundle.py`:
```python
class FabBundle(BaseModel):
    kicad_sch: Path
    kicad_pcb: Path
    gerbers_dir: Path
    bom_csv: Path
    cpl_csv: Path
    erc_clean: bool
    drc_clean: bool
    vendor_validated: bool
    stock_verified: bool
    ...
```

**Missing**: No `step_file: Path` reference for the assembled board 3D model.

---

## 4. Recommendations: v1 vs v2+

### STEP 3D Files: **v1 INCLUDE** ✓
**Rationale:**
- Nearly every hobby/prosumer project needs to verify board-in-enclosure fit
- KiCad already has native STEP export; no external tools needed
- Minimal scope: add `step_file: Optional[Path] = None` to `FabBundle`
- Value-add is immediate: designer can run `kicad-cli pcb export step` as part of the fab checklist

**Proposal for v1:**
```python
class FabBundle(BaseModel):
    ...
    step_file: Optional[Path] = None
    """Optional 3D STEP model of the assembled board. Omit if mechanical
    enclosure is not a concern. Run `kicad-cli pcb export step` to generate."""
```

### IBIS Models: **v2+ DEFER** (Year-2 Material)
**Rationale:**
- Requires SI simulation tools (HyperLynx, ADS, ngspice) outside current scope
- Current hw-toolkit targets analog power + simple digital (GPIO, I2C, SPI)
- High-speed interface validation (USB HS, Ethernet, DDR) is a specialty skill
- If needed: add optional `ibis_model_url: Optional[str] = None` to individual component Actuals (not ChosenPart), sourced from datasheet investigation. But don't build SI sim pipeline yet.

**Proposal for future (v2+, deferred):**
```python
class BuckActuals(BaseModel):
    # ... existing fields ...
    ibis_model_url: Optional[str] = None
    """Link to manufacturer's IBIS model if high-speed switching is a concern."""
```

---

## 5. Summary

| Artifact | Format | v1 Status | v2+ | Reasoning |
|----------|--------|-----------|-----|-----------|
| **IBIS** | `.ibs` text | Defer | Placeholder OK | SI sims require external tools; specialty skill |
| **STEP** | `.stp/.step` ISO 10303 | Include | Auto-generate | Mechanical fit is immediate need; KiCad native |

**Actionable for v1:**
1. Add `step_file: Optional[Path]` to `FabBundle` (no logic needed yet, just the field)
2. Update fab checklist docs to recommend `kicad-cli pcb export step` before packaging
3. In v2, if SI analysis becomes needed, add `ibis_model_url: Optional[str]` to component Actuals and integrate SI tool discovery

