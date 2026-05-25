# KiCad CLI & File Format Payloads

Reference documentation for KiCad input/output shapes consumed and produced by `pcb-designer` agent workflows. Validates pydantic contracts in `hw_agent/core/{research_bundle.py, fab_bundle.py}` against real-world KiCad data structures.

**Date:** 2026-05-24 | **KiCad Version:** 9.0+ | **CLI Docs:** https://docs.kicad.org/9.0/en/cli/cli.html

---

## 1. KiCad CLI Command Signatures & Output

### 1.1 Schematic ERC (Electrical Rules Check)

**Command:**
```bash
kicad-cli sch erc [--output OUTPUT_FILE] [--format json|report] \
  [--units mm|mil] [--severity-all|error|warning|exclusions] \
  [--exit-code-violations] INPUT_FILE.kicad_sch
```

**Output Formats:**
- Default format: `.rpt` (human-readable report)
- JSON format: `.json` (structured violations)
- Exit code: 0 if no violations, non-zero if `--exit-code-violations` set

**Example JSON Output:**
```json
{
  "sheets": [
    {
      "name": "/",
      "violations": [
        {
          "type": "pin_not_connected",
          "severity": "warning",
          "description": "Pin not connected",
          "items": [
            {
              "uuid": "550e8400-e29b-41d4-a716-446655440000",
              "description": "U1 pin PA15"
            }
          ]
        },
        {
          "type": "global_label_dangling",
          "severity": "warning",
          "description": "Global label not used",
          "items": [
            {
              "uuid": "550e8400-e29b-41d4-a716-446655440001",
              "description": "GND"
            }
          ]
        }
      ]
    }
  ]
}
```

**Field Notes:**
- `sheets[]` — hierarchical schematic tree; root is name="/", subsheets have names like "/Buck_Converter"
- `violations[]` — list of ERC violations per sheet
- `type` — error category (pin_not_connected, global_label_dangling, hier_label_mismatch, lib_symbol_issues, etc.)
- `severity` — "error" or "warning"
- `items[]` — component/pin/net references involved; each has `uuid` + `description`

---

### 1.2 PCB DRC (Design Rules Check)

**Command:**
```bash
kicad-cli pcb drc [--output OUTPUT_FILE] [--format json|report] \
  [--all-track-errors] [--schematic-parity] [--units mm|mil] \
  [--severity-all|error|warning|exclusions] [--exit-code-violations] \
  INPUT_FILE.kicad_pcb
```

**Output Formats:**
- Default: `.rpt` (human-readable report)
- JSON format: `.json` (structured violations)
- Exit code: 0 if no violations, non-zero if `--exit-code-violations` set

**Example JSON Output:**
```json
{
  "violations": [
    {
      "type": "copper_edge_clearance",
      "severity": "error",
      "description": "Copper edge clearance violation",
      "items": [
        {
          "uuid": "550e8400-e29b-41d4-a716-446655440002",
          "description": "U1 pads; clearance 0.2mm, min 0.5mm"
        }
      ]
    },
    {
      "type": "track_dangling",
      "severity": "error",
      "description": "Track end not connected",
      "items": [
        {
          "uuid": "550e8400-e29b-41d4-a716-446655440003",
          "description": "Net GND, endpoint (25.4, 50.8)"
        }
      ]
    }
  ],
  "unconnected_items": [
    {
      "uuid": "550e8400-e29b-41d4-a716-446655440004",
      "description": "Net GND floating"
    }
  ]
}
```

**Field Notes:**
- `violations[]` — DRC violations (trace clearance, via size, copper-edge-clearance, etc.)
- `unconnected_items[]` — nets not routed; separate from violations
- `type` — error category (copper_edge_clearance, track_dangling, via_outside_pad, etc.)

---

### 1.3 Schematic Export BOM

**Command:**
```bash
kicad-cli sch export bom [--output OUTPUT_FILE] [--preset PRESET] \
  [--fields FIELD_LIST] [--labels LABEL_LIST] [--group-by GROUP_FIELD] \
  [--sort-field SORT_BY] [--sort-asc] [--filter FILTER_EXPR] \
  [--exclude-dnp] [--include-excluded-from-bom] \
  [--field-delimiter DELIM] [--string-delimiter DELIM] \
  [--ref-delimiter DELIM] [--ref-range-delimiter DELIM] \
  [--keep-tabs] [--keep-line-breaks] INPUT_FILE.kicad_sch
```

**Output Format:** CSV (default `.csv`, configurable delimiter)

**Example CSV Output (Standard Fields):**
```csv
Reference,Value,Datasheet,Footprint,Manufacturer,MPN,Stock,LCSC,Price
U1,STM32F407VGT6,http://example.com/stm32f4.pdf,BGA100,ST Microelectronics,STM32F407VGT6,1500,C100123,12.50
U2,AMS1117-3.3,http://example.com/ldo.pdf,SOT-223,AMS,AMS1117-3.3,5000,C6186,0.15
C1,100nF,http://example.com/cap.pdf,0805,Samsung,CL21B104KBCNNNC,8000,C2976596,0.05
C2,10µF,http://example.com/cap.pdf,1206,TDK,C5750X5R0J106M230KA,3000,C28323,0.12
R1,10k,http://example.com/res.pdf,0603,Yageo,RC0603FR-0710KL,10000,C99781,0.01
L1,3.3µH,http://example.com/ind.pdf,1210,TDK,SPM6530T-3R3M,2000,C171074,0.35
```

**Field Notes:**
- `Reference` — component designator (U1, C1, R1, etc.); grouped by comma (U1,U2,U3 for identical parts)
- `Value` — component value (STM32F407VGT6, 100nF, 10k, 3.3µH)
- `Datasheet` — optional URL; depends on symbol properties
- `Footprint` — KiCad footprint library reference (BGA100, SOT-223, 0805, 1210)
- `Manufacturer` — IC/part manufacturer name
- `MPN` — Manufacturer Part Number (exact part ID)
- `Stock` — current stock quantity at distributor (informational only, not locked)
- `LCSC` — JLCPCB LCSC code (C-number format)
- `Price` — unit price USD; per-unit or tiered

**Custom Field Example:**
```csv
Reference,Value,Footprint,MPN,LCSC,Qty
U1,STM32F407VGT6,BGA100,STM32F407VGT6,C100123,1
C1,100nF,0805,CL21B104KBCNNNC,C2976596,2
```

---

### 1.4 PCB Export Position (CPL/Pick-and-Place)

**Command:**
```bash
kicad-cli pcb export pos [--output OUTPUT_FILE] \
  [--format ascii|csv|gerber] [--units mm|mil] \
  [--side front|back|both] [--bottom-negate-x] \
  [--smd-only] [--exclude-fp-th] [--exclude-dnp] \
  [--gerber-board-edge] INPUT_FILE.kicad_pcb
```

**Output Formats:**
- CSV (default): position + rotation per component
- ASCII: human-readable format
- Gerber: Gerber format for machine import

**Example CSV Output (format: ascii, side: both, units: mm):**
```csv
Designator,Midx,Midy,Layer,Rotation
U1,25.4,50.8,Front,90.0
U2,35.56,45.72,Front,180.0
C1,15.24,20.32,Front,0.0
C2,55.88,60.96,Back,270.0
R1,10.16,15.24,Front,45.0
R2,12.7,17.78,Back,90.0
L1,40.64,35.56,Front,0.0
```

**Field Notes:**
- `Designator` — component reference (U1, C1, R1, etc.)
- `Midx`, `Midy` — centroid position in mm or mils (per `--units`)
- `Layer` — "Front" (top), "Back" (bottom)
- `Rotation` — degrees (0, 90, 180, 270 typical; intermediate angles possible)
- `--smd-only` — excludes through-hole components
- `--exclude-dnp` — excludes do-not-place (DNP) parts
- `--bottom-negate-x` — flips X-axis for bottom side (some fabricators require this)

---

### 1.5 PCB Export Gerbers

**Command:**
```bash
kicad-cli pcb export gerbers [--output OUTPUT_DIR] [--layers LAYER_LIST] \
  [--no-protel-ext] [--no-x2] [--no-netlist] [--subtract-soldermask] \
  [--precision PRECISION] [--sketch-pads-on-fab-layers] \
  [--hide-DNP-footprints-on-fab-layers] INPUT_FILE.kicad_pcb
```

**Output Files:** One `.gbr` (or `.gbl`, `.gtl` with Protel extensions) per layer

**Example Files Generated:**
```
gerbers/
  system-F_Cu.gbr              # Front copper
  system-B_Cu.gbr              # Back copper
  system-F_Silkscreen.gbr      # Front silk/labels
  system-B_Silkscreen.gbr      # Back silk/labels
  system-F_Mask.gbr            # Front solder mask
  system-B_Mask.gbr            # Back solder mask
  system-Edge_Cuts.gbr         # Board outline/edge
  system-F_Paste.gbr           # Front stencil (SMD paste)
  system-B_Paste.gbr           # Back stencil
  system-User_Comments.gbr     # Optional user layer
  system-User_Drawings.gbr     # Optional drawings
```

**Protel Extensions (default, unless `--no-protel-ext`):**
```
.GBL    # Back copper (bottom)
.GTL    # Front copper (top)
.GBS    # Back solder mask
.GTS    # Front solder mask
.GBP    # Back paste
.GTP    # Front paste
.GBO    # Back silk
.GTO    # Front silk
.GKO    # Edge cuts
```

---

### 1.6 PCB Export Drill Files

**Command:**
```bash
kicad-cli pcb export drill [--output OUTPUT_DIR] \
  [--format pth|npth|all-pth|excellon|gerber] [--map-format ps|pdf|gbr] \
  [--generate-map] [--use-drill-file-origin] INPUT_FILE.kicad_pcb
```

**Output Files:**
```
drill/
  system-PTH.drl              # Plated through holes
  system-NPTH.drl             # Non-plated through holes
  system-NPTH.xln             # X2 format (newer standard)
  system.xln                  # Combined (all holes)
  system-drl.gbr              # Gerber drill format (alternative)
  system-drl_map.pdf          # Drill map (visual reference)
```

---

## 2. Schematic .kicad_sch File Structure (S-Expression)

### 2.1 Symbol with Custom Properties

A typical resistor or IC symbol in `.kicad_sch`:

```scheme
(symbol (lib_id "Device:R") (at 100.33 50.8 0)
  (property "Reference" "R1" (at 101.6 50.165 0)
    (effects (font (size 1.27 1.27)) (justify left)))
  )
  (property "Value" "10k" (at 101.6 52.07 0)
    (effects (font (size 1.27 1.27)) (justify left)))
  )
  (property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at 98.552 50.8 90)
    (effects (font (size 1.27 1.27)) hide)
  )
  (property "Datasheet" "https://example.com/10k.pdf" (at 100.33 50.8 0)
    (effects (font (size 1.27 1.27)) hide)
  )
  (property "MPN" "RC0603FR-0710KL" (at 100.33 50.8 0)
    (effects (font (size 1.27 1.27)) hide)
  )
  (property "Manufacturer" "Yageo" (at 100.33 50.8 0)
    (effects (font (size 1.27 1.27)) hide)
  )
  (property "LCSC" "C99781" (at 100.33 50.8 0)
    (effects (font (size 1.27 1.27)) hide)
  )
  (property "Stock" "10000" (at 100.33 50.8 0)
    (effects (font (size 1.27 1.27)) hide)
  )
  (property "Price" "0.01" (at 100.33 50.8 0)
    (effects (font (size 1.27 1.27)) hide)
  )
  (pin "1" (uuid "550e8400-e29b-41d4-a716-446655440000"))
  (pin "2" (uuid "550e8400-e29b-41d4-a716-446655440001"))
)
```

### 2.2 IC Symbol Example

```scheme
(symbol (lib_id "Regulator_Linear:AMS1117-3.3") (at 45.72 100.33 0)
  (property "Reference" "U1" (at 45.72 99.695 0)
    (effects (font (size 1.27 1.27)))
  )
  (property "Value" "AMS1117-3.3" (at 45.72 102.235 0)
    (effects (font (size 1.27 1.27)))
  )
  (property "Footprint" "Package_TO_SOT_SMD:SOT-223-3_TabPin2" (at 45.72 105.41 0)
    (effects (font (size 1.27 1.27)) hide)
  )
  (property "Datasheet" "http://www.advanced-monolithic.com/pdf/ds1117.pdf" (at 50.8 104.14 0)
    (effects (font (size 1.27 1.27)) hide)
  )
  (property "MPN" "AMS1117-3.3" (at 45.72 100.33 0)
    (effects (font (size 1.27 1.27)) hide)
  )
  (property "LCSC" "C6186" (at 45.72 100.33 0)
    (effects (font (size 1.27 1.27)) hide)
  )
  (property "Stock" "5000" (at 45.72 100.33 0)
    (effects (font (size 1.27 1.27)) hide)
  )
  (property "Price" "0.15" (at 45.72 100.33 0)
    (effects (font (size 1.27 1.27)) hide)
  )
  (pin "1" (uuid "550e8400-e29b-41d4-a716-446655440010"))
  (pin "2" (uuid "550e8400-e29b-41d4-a716-446655440011"))
  (pin "3" (uuid "550e8400-e29b-41d4-a716-446655440012"))
)
```

**Key Fields:**
- `lib_id` — "Category:Symbol" (e.g., "Device:R", "Regulator_Linear:AMS1117-3.3")
- `at` — (x, y, rotation) position in mm
- `property` — key-value pairs (Reference, Value, Footprint, Datasheet, MPN, LCSC, Stock, Price, ...)
- `pin` — numbered pin endpoints with UUIDs for netlist connectivity

---

## 3. PCB .kicad_pcb File Structure (S-Expression)

### 3.1 Footprint Placement

```scheme
(footprint "Resistor_SMD:R_0603_1608Metric" (at 25.4 50.8 90)
  (layer "F.Cu")
  (property "Reference" "R1" (at 0 -1.43 90)
    (effects (font (size 1.27 1.27)) (justify left))
  )
  (property "Value" "10k" (at 0 1.43 90)
    (effects (font (size 1.27 1.27)) (justify left))
  )
  (property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at 0 0 0)
    (effects (font (size 1.27 1.27)) hide)
  )
  (pad "1" smd rect (at -0.8 0 90) (size 0.9 1.6) (layers "F.Cu" "F.Paste" "F.Mask")
    (net 5 "GND")
  )
  (pad "2" smd rect (at 0.8 0 90) (size 0.9 1.6) (layers "F.Cu" "F.Paste" "F.Mask")
    (net 3 "+5V")
  )
)
```

### 3.2 IC Footprint with BGA

```scheme
(footprint "Package_BGA:BGA100" (at 50.8 25.4 0)
  (layer "F.Cu")
  (property "Reference" "U1" (at 0 -7 0)
    (effects (font (size 1.27 1.27)))
  )
  (property "Value" "STM32F407VGT6" (at 0 7 0)
    (effects (font (size 1.27 1.27)))
  )
  (pad "A1" smd circle (at -6.35 -6.35 0) (size 0.76 0.76) (layers "F.Cu" "F.Paste" "F.Mask")
    (net 1 "+3V3")
  )
  (pad "B1" smd circle (at -5.08 -6.35 0) (size 0.76 0.76) (layers "F.Cu" "F.Paste" "F.Mask")
    (net 2 "GND")
  )
  (pad "PA0" smd circle (at 2.54 3.81 0) (size 0.76 0.76) (layers "F.Cu" "F.Paste" "F.Mask")
    (net 10 "GPIO_A0")
  )
)
```

**Key Fields:**
- `at` — (x, y, rotation) position in mm
- `layer` — copper layer (F.Cu = front, B.Cu = back)
- `pad` — numbered pad with location, size, shape, layers, and net assignment
- `property` — metadata (Reference, Value, Footprint, MPN, etc.)

---

## 4. BOM CSV Fields

### 4.1 Required Columns (Minimum)

| Column | Type | Example | Notes |
|--------|------|---------|-------|
| `Reference` | string | U1, C1, R1 | Component designators (comma-separated for multiples) |
| `Value` | string | STM32F407VGT6, 100nF, 10k | Part value or part number |

### 4.2 Optional BOM Columns

| Column | Type | Example | Notes |
|--------|------|---------|-------|
| `Footprint` | string | BGA100, 0805, SOT-223 | KiCad footprint library reference |
| `Datasheet` | string | http://... | URL to component datasheet |
| `Manufacturer` | string | ST Microelectronics, Yageo | IC/part manufacturer |
| `MPN` | string | STM32F407VGT6 | Manufacturer Part Number (exact) |
| `LCSC` | string | C100123 | JLCPCB LCSC part code |
| `Stock` | integer | 1500 | Available quantity at distributor (informational) |
| `Price` | float | 12.50 | Unit price USD |
| `Quantity` | integer | 1, 2, 10 | Qty per board (if BOM is not pre-grouped) |
| `DNP` | boolean/string | true, "DNP" | Do-not-place flag |
| `Assembly` | string | "JLCPCB" | Preferred assembly partner |

---

## 5. CPL CSV Fields

### 5.1 Standard Pick-and-Place Format

| Column | Type | Example | Notes |
|--------|------|---------|-------|
| `Designator` | string | U1, C1 | Component reference |
| `Midx` | float | 25.4 | X coordinate of centroid (mm or mils) |
| `Midy` | float | 50.8 | Y coordinate of centroid |
| `Layer` | string | Front, Back | Placement side (Front/Back or TopLayer/BottomLayer) |
| `Rotation` | float | 0.0, 90.0, 180.0, 270.0 | Placement rotation in degrees |

**Alternative Format (some vendors):**
```csv
Ref,X(mm),Y(mm),Side,Rotation(degrees)
U1,25.4,50.8,Top,90.0
C1,15.24,20.32,Top,0.0
R2,12.7,17.78,Bottom,90.0
```

---

## 6. ERC/DRC Report JSON Structure

### 6.1 Shared JSON Format (ERC & DRC)

Both `kicad-cli sch erc` and `kicad-cli pcb drc` output the same JSON structure:

```json
{
  "sheets": [
    {
      "name": "/",
      "violations": [
        {
          "type": "violation_type_name",
          "severity": "error" | "warning",
          "description": "Human-readable violation description",
          "items": [
            {
              "uuid": "550e8400-e29b-41d4-a716-446655440000",
              "description": "Component/pin/net reference"
            }
          ]
        }
      ]
    }
  ]
}
```

### 6.2 DRC-Specific: unconnected_items

DRC adds a top-level `unconnected_items` array for floating nets:

```json
{
  "violations": [...],
  "unconnected_items": [
    {
      "uuid": "550e8400-e29b-41d4-a716-446655440100",
      "description": "Net GND floating; no routed connections"
    }
  ]
}
```

### 6.3 Common Violation Types

**ERC Violations:**
- `pin_not_connected` — pin has no wire attached
- `global_label_dangling` — global label not used elsewhere
- `label_dangling` — net label has no connection
- `hier_label_mismatch` — hierarchical label without parent sheet pin
- `lib_symbol_issues` — symbol definition mismatch

**DRC Violations:**
- `copper_edge_clearance` — copper too close to board edge
- `track_dangling` — trace not connected to pad/via
- `via_outside_pad` — via placement outside pad boundary
- `clearance_violation` — trace/trace or trace/pad clearance < minimum
- `unconnected_items` — net has no copper (no routed connectivity)

---

## 7. Validation Against Pydantic Contracts

### 7.1 SubsystemPick Fields → BOM CSV Columns

**ResearchBundle.SubsystemPick fields that must be in final BOM CSV:**

| SubsystemPick Field | BOM CSV Column | Type | Required | Notes |
|---------------------|---|------|----------|-------|
| `id` | Reference | string | YES | Component designator (U1, C1, etc.) |
| `mpn` | MPN | string | YES | Manufacturer Part Number |
| `manufacturer` | Manufacturer | string | NO | IC/part manufacturer |
| `lcsc` | LCSC | string | NO | JLCPCB LCSC code (if applicable) |
| `package` | Footprint | string | NO | KiCad footprint reference |
| `datasheet_url` | Datasheet | string | NO | Datasheet URL |
| `qty_per_board` | Quantity | integer | YES | Units per board |
| `price_usd` | Price | float | NO | Unit price USD |
| `stock` | Stock | integer | NO | Available quantity at distributor |

**ACTION ITEM:** Ensure `SubsystemPick.actuals` dict captures any non-stock fields (e.g., voltage ratings, current limits) needed for BOM export — consider extending `actuals` schema per category.

---

### 7.2 FabBundle Output Validation

**FabBundle must declare:**

```python
kicad_sch: Path     # .kicad_sch file with all symbols + properties
kicad_pcb: Path     # .kicad_pcb file with footprints placed + routed
bom_csv: Path       # CSV with Reference, Value, Footprint, MPN, LCSC, Stock, Price
cpl_csv: Path       # CSV with Designator, Midx, Midy, Layer, Rotation
gerbers_dir: Path   # Directory with gerber files (.gbr, .drl, edge cut)
```

**Validation gates before FabBundle lock:**
1. `erc_clean: bool` — ERC JSON has no violations (or only expected ones)
2. `drc_clean: bool` — DRC JSON has no violations (or only expected ones)
3. `vendor_validated: bool` — `pcborder_validate_for_vendor` PASS
4. `stock_verified: bool` — Every BOM line has stock ≥ `qty_per_board × build_qty`

---

### 7.3 Known Discrepancies

**Gap 1: Custom Properties in .kicad_sch**

KiCad symbols can carry arbitrary properties beyond Reference/Value/Footprint:
```scheme
(property "MPN" "STM32F407VGT6" ...)
(property "LCSC" "C100123" ...)
(property "Stock" "1500" ...)
(property "Price" "12.50" ...)
```

**Current Status:** `SubsystemPick` exposes `mpn`, `lcsc`, `stock`, `price_usd` as top-level fields. When writing to .kicad_sch, these should land as symbol properties so `kicad-cli sch export bom` can extract them with custom field mappings.

**Action:** Verify that `kicad_writer.py` / `ksa_writer.py` inject these properties into each placed symbol's S-expression.

---

**Gap 2: BOM CSV Field Mapping**

`kicad-cli sch export bom` accepts custom `--fields` and `--labels` to map symbol properties → CSV columns:

```bash
kicad-cli sch export bom --fields "Reference,Value,Footprint,MPN,LCSC,Stock,Price" \
  --labels "Ref,Value,Footprint,MPN,LCSC_Code,Qty_Available,Unit_Price" \
  input.kicad_sch --output bom.csv
```

**Current Status:** Code calls `export_fabrication()` with default kicad-cli args; BOM fields are not explicitly mapped.

**Action:** Add configurable BOM field list to FabBundle generation. Ensure all SubsystemPick fields are queryable downstream.

---

**Gap 3: DRC/ERC Filter Classification**

`erc_filters.py` and `drc_filters.py` classify violations into `real_issues` vs `expected`. The pydantic gate only checks `erc_clean` / `drc_clean` booleans; no detailed violation audit is persisted.

**Current Status:** Reports are generated but not linked from FabBundle. No pre-serialization of violation metadata.

**Action:** Consider adding optional `erc_report_path` / `drc_report_path` to FabBundle so failure audits can be traced.

---

## 8. CLI Command Invocation Examples

### 8.1 ERC with JSON output (recommended for agents)

```bash
kicad-cli sch erc \
  --output schematic.json \
  --format json \
  --exit-code-violations \
  system.kicad_sch
```

### 8.2 DRC with JSON output

```bash
kicad-cli pcb drc \
  --output board.json \
  --format json \
  --all-track-errors \
  --schematic-parity \
  --exit-code-violations \
  system.kicad_pcb
```

### 8.3 BOM export with custom fields

```bash
kicad-cli sch export bom \
  --output bom.csv \
  --fields "Reference,Value,Footprint,MPN,LCSC,Stock,Price" \
  system.kicad_sch
```

### 8.4 Fabrication bundle (gerbers + drill + POS)

```bash
# Gerbers
kicad-cli pcb export gerbers \
  --output fabrication/gerbers \
  --no-protel-ext \
  system.kicad_pcb

# Drill
kicad-cli pcb export drill \
  --output fabrication/drill \
  system.kicad_pcb

# Pick-and-place
kicad-cli pcb export pos \
  --output fabrication/system-pos.csv \
  --format csv \
  --units mm \
  --side both \
  --smd-only \
  system.kicad_pcb
```

---

## 9. Summary: Fields Required in Our Pydantic Models

### For SubsystemPick → Schematic .kicad_sch

**MUST include in symbol properties:**
- Reference (designator)
- Value (part number or description)
- Footprint (KiCad footprint library reference)
- MPN (Manufacturer Part Number)
- Manufacturer (IC/part manufacturer)
- LCSC (JLCPCB LCSC code, if available)
- Datasheet (URL)
- Stock (quantity available; informational)
- Price (unit price USD)

**Currently in SubsystemPick:**
```python
id: str              # → Reference
mpn: str             # → MPN (property)
manufacturer: str    # → Manufacturer (property)
lcsc: str | None     # → LCSC (property)
package: str         # → Footprint
datasheet_url: str   # → Datasheet (property)
qty_per_board: int   # → Quantity in BOM (not in symbol)
price_usd: float     # → Price (property)
stock: int           # → Stock (property)
```

**Missing/Gap:**
- `stock` must be injected as symbol property so BOM export captures it
- `price_usd` should be injected as symbol property
- Consider persisting `qty_per_board` per-symbol if needed

---

### For FabBundle Output

**MUST produce before locking:**
- ✓ `kicad_sch` — path to .kicad_sch (ERC-clean)
- ✓ `kicad_pcb` — path to .kicad_pcb (DRC-clean)
- ✓ `bom_csv` — path to BOM CSV (with Reference, Value, Footprint, MPN, LCSC, Stock, Price columns)
- ✓ `cpl_csv` — path to CPL CSV (with Designator, Midx, Midy, Layer, Rotation columns)
- ✓ `gerbers_dir` — directory with all gerber + drill files
- ✓ `erc_clean`, `drc_clean`, `vendor_validated`, `stock_verified` — boolean gates

**Optional (future):**
- `erc_report_path` — path to ERC JSON for failure audit
- `drc_report_path` — path to DRC JSON for failure audit

---

## 10. Recommendations

1. **Ensure symbol properties are written atomically.** When `kicad_writer.py` / `ksa_writer.py` place a symbol from SubsystemPick, inject all fields (MPN, LCSC, Stock, Price, Datasheet) as symbol properties in the .kicad_sch.

2. **Standardize BOM field export.** Define a canonical BOM field list in a central config or constant; pass it to `kicad-cli sch export bom` via `--fields` flag.

3. **Validate BOM CSV shape downstream.** After export, verify that the CSV has expected columns before FabBundle lock.

4. **Persist DRC/ERC reports.** Save JSON reports to `fabrication/<rev>/` and optionally link them from FabBundle for audit trails.

5. **Test real KiCad files.** Once a real project (.kicad_sch + .kicad_pcb) exists in the repo, validate that all pydantic round-trips work end-to-end.

---

**Document Generated:** 2026-05-24  
**Reference Repo Paths:**
- `/Users/juanantonioluera/ws/hw-toolkit/hw_agent/core/research_bundle.py` — SubsystemPick, Interface, ResearchBundle
- `/Users/juanantonioluera/ws/hw-toolkit/hw_agent/core/fab_bundle.py` — FabBundle
- `/Users/juanantonioluera/ws/hw-toolkit/hw_agent/artifacts/schematics/pcb_writer.py` — export_fabrication()
- `/Users/juanantonioluera/ws/hw-toolkit/hw_agent/artifacts/schematics/ksa_writer.py` — symbol writing
- `/Users/juanantonioluera/ws/hw-toolkit/hw_agent/artifacts/schematics/erc_filters.py`, `drc_filters.py` — violation classification
