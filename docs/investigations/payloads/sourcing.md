# Distributor API Payload Investigation

**Goal:** Validate that our `SubsystemPick` model captures all fields the parts distributors (Digi-Key, Mouser, JLCPCB) actually return, so we're not losing data or missing opportunities for cross-referencing.

**Real Part Used:** RT6228AGQUF (8A buck converter, available across all three distributors)

---

## 1. Digi-Key Product Details API v4 Response

**Source:** Live fixture from `/hw_agent/domain/templates/fixtures/RT6228AGQUF.digikey.json`

**Full Response Shape:**

```json
{
  "results": [
    {
      "source": "digikey",
      "part_number": "1028-RT6228AGQUFTR-ND",
      "mfr_part_number": "RT6228AGQUF",
      "manufacturer": "Richtek USA Inc.",
      "description": "IC REG BUCK ADJ 8A 13UQFN",
      "category": "Integrated Circuits (ICs)",
      "stock": 4,
      "price": 2.4,
      "price_breaks": [
        {"qty": 1500, "price": 1.3552}
      ],
      "datasheet_url": "https://www.richtek.com/SaveDownload.aspx?specid=RT6228A/RT6228B/RT6228C",
      "product_url": "https://www.digikey.com/en/products/detail/richtek-usa-inc/RT6228AGQUF/16376861",
      "rohs": "ROHS3 Compliant",
      "lifecycle": "Active",
      "parameters": {
        "Function": "Step-Down",
        "Output Configuration": "Positive",
        "Topology": "Buck",
        "Output Type": "Adjustable",
        "Number of Outputs": "1",
        "Voltage - Input (Min)": "4.5V",
        "Voltage - Input (Max)": "23V",
        "Voltage - Output (Min/Fixed)": "0.6V",
        "Voltage - Output (Max)": "5.1V",
        "Current - Output": "8A",
        "Frequency - Switching": "500kHz",
        "Synchronous Rectifier": "Yes",
        "Operating Temperature": "-40°C ~ 85°C (TA)",
        "Mounting Type": "Surface Mount",
        "Package / Case": "13-PowerUFQFN",
        "Supplier Device Package": "13-UQFN (FC) (3x3)"
      },
      "min_qty": 1500,
      "currency": "USD"
    }
  ],
  "total": 1
}
```

**Key Fields:**
- `source` — distributor identifier
- `part_number` — DK-internal SKU (1028-RT6228AGQUFTR-ND)
- `mfr_part_number` — canonical manufacturer MPN (RT6228AGQUF)
- `manufacturer` — full legal name ("Richtek USA Inc.")
- `description` — short English summary
- `category` — product category string
- `stock` — quantity available
- `price` — unit price (USD, likely qty 1)
- `price_breaks` — quantity tier discounts (array of {qty, price})
- `datasheet_url` — link to PDF
- `product_url` — DK product detail page
- `rohs` — RoHS compliance status string ("ROHS3 Compliant")
- `lifecycle` — product lifecycle ("Active", "EOL", etc.)
- `parameters` — key-value dict of searchable specs (Voltage Min/Max, Current, Frequency, Package, etc.)
- `min_qty` — minimum order quantity at the lowest price tier
- `currency` — price currency code

---

## 2. Mouser SearchByPartNumber API Response

**Source:** Live fixture from `/hw_agent/domain/templates/fixtures/RT6228AGQUF.mouser.json`

**Full Response Shape:**

```json
{
  "results": [
    {
      "source": "mouser",
      "part_number": "835-RT6228AGQUF",
      "mfr_part_number": "RT6228AGQUF",
      "manufacturer": "Richtek",
      "description": "Switching Voltage Regulators 8A, 23V Synchronous Step-Down Converter with 3.3V/5V LDO",
      "category": "Switching Voltage Regulators",
      "stock": 1418,
      "price": 2.4,
      "price_breaks": [
        {"qty": 1, "price": 2.4, "currency": "USD"},
        {"qty": 10, "price": 2.36, "currency": "USD"},
        {"qty": 25, "price": 2.18, "currency": "USD"},
        {"qty": 1000, "price": 1.29, "currency": "USD"},
        {"qty": 1500, "price": 1.22, "currency": "USD"},
        {"qty": 3000, "price": 1.2, "currency": "USD"}
      ],
      "datasheet_url": "",
      "product_url": "https://www.mouser.ca/ProductDetail/Richtek/RT6228AGQUF?qs=amGC7iS6iy9Xb%2FAqb9shQw%3D%3D",
      "rohs": "RoHS Compliant",
      "lifecycle": "Active",
      "parameters": {
        "Packaging": "Cut Tape",
        "Standard Pack Qty": "1500"
      },
      "min_qty": 1,
      "currency": "USD"
    }
  ],
  "total": 1
}
```

**Key Fields:**
- `source` — distributor identifier
- `part_number` — Mouser-internal SKU (835-RT6228AGQUF)
- `mfr_part_number` — canonical MPN
- `manufacturer` — manufacturer name (shorter than DK: "Richtek" vs "Richtek USA Inc.")
- `description` — longer, more detailed English summary than DK
- `category` — product category string (category names differ from DK: "Switching Voltage Regulators" vs "Integrated Circuits (ICs)")
- `stock` — quantity available (1418 vs DK's 4 — high variation!)
- `price` — unit price (2.4 USD, same as DK for qty 1)
- `price_breaks` — **much richer than DK**: 6 tiers vs DK's 1
- `datasheet_url` — empty string (DK has it, Mouser doesn't)
- `product_url` — Mouser product detail page
- `rohs` — RoHS compliance ("RoHS Compliant" vs DK's "ROHS3 Compliant" — both same meaning, different wording)
- `lifecycle` — product lifecycle ("Active")
- `parameters` — minimal spec dict (only 2 fields: Packaging, Pack Qty) — **much thinner than DK**
- `min_qty` — minimum order quantity (1 vs DK's 1500)
- `currency` — price currency code

**Mouser vs DK Gap:** Mouser has NO parametric specs (no voltage, current, frequency) — parameters are packaging-only. All technical specs must come from Digi-Key or datasheet.

---

## 3. JLCPCB / LCSC Component Library Response

**Source:** Live fixture from `/hw_agent/domain/templates/fixtures/RT6228AGQUF.jlc.json`

**Full Response Shape:**

```json
{
  "lcsc": "C2976596",
  "model": "RT6228AGQUF",
  "manufacturer": "Richtek Tech",
  "package": "UQFN-12HL(3x3)",
  "stock": 6879,
  "price": 0.7916,
  "price_10": 0.6358,
  "library_type": "extended",
  "preferred": false,
  "category": "Power Management (PMIC)",
  "subcategory": "DC-DC Converters",
  "subcategory_id": 3005,
  "mounting_type": "smd",
  "specs": {
    "Function": "Step-down type",
    "Synchronous Rectifier": "Yes",
    "Frequency - Switching": "500kHz",
    "Output Type": "Adjustable",
    "Voltage - Supply": "4.5V~23V",
    "Output Current": "8A",
    "Switch tube (built-in/external)": "Built-in",
    "Topology": "Buck",
    "Output Voltage": "600mV~5.1V",
    "Number of Outputs": "1"
  },
  "description": "1 4.5V~23V 500kHz 600mV~5.1V 8A Adjustable Buck Buck Built-in Yes UQFN-12HL(3x3) DC-DC Converters ROHS",
  "min_order": 1,
  "reel_qty": 1500,
  "datasheet": "https://wmsc.lcsc.com/wmsc/upload/file/pdf/v2/lcsc/2205241716_Richtek-Tech-RT6228AGQUF_C2976596.pdf",
  "lcsc_url": "https://www.lcsc.com/product-detail/dc-dc-converters_richtek-tech-rt6228agquf_C2976596.html",
  "prices": [
    {"qty": "1+", "price": 0.7916},
    {"qty": "10+", "price": 0.6358},
    {"qty": "30+", "price": 0.5588},
    {"qty": "100+", "price": 0.4817},
    {"qty": "500+", "price": 0.4368},
    {"qty": "1500+", "price": 0.4127}
  ],
  "attributes": [
    {"name": "Function", "value": "Step-down type"},
    {"name": "Synchronous Rectifier", "value": "Yes"},
    {"name": "Frequency - Switching", "value": "500kHz"},
    {"name": "Output Type", "value": "Adjustable"},
    {"name": "Voltage - Supply", "value": "4.5V~23V"},
    {"name": "Output Current", "value": "8A"},
    {"name": "Switch tube (built-in/external)", "value": "Built-in"},
    {"name": "Topology", "value": "Buck"},
    {"name": "Output Voltage", "value": "600mV~5.1V"},
    {"name": "Number of Outputs", "value": "1"}
  ],
  "has_easyeda_footprint": true,
  "easyeda_symbol_uuid": "633042cdd34a401d9dce1a0e2abf3689",
  "easyeda_footprint_uuid": "5fcc703f989740ea977f712325a088cc"
}
```

**Key Fields:**
- `lcsc` — LCSC catalog number (C2976596) — **JLC-specific, not available from DK/Mouser**
- `model` — manufacturer MPN (same as DK/Mouser)
- `manufacturer` — manufacturer name (JLC convention: "Richtek Tech")
- `package` — package name (JLC format: "UQFN-12HL(3x3)" vs DK's "13-UQFN (FC) (3x3)")
- `stock` — quantity available (6879, highest of all three — JLC stock is usually best)
- `price` — unit price (0.7916 USD, **cheapest of the three**)
- `price_10` — shorthand for qty-10 price (0.6358)
- `library_type` — **JLC-specific**: "extended" (triggers $3 SMT assembly fee) vs "basic"/"preferred" (free SMT)
- `preferred` — boolean flag for special pricing tier
- `category` & `subcategory` — hierarchical categorization
- `subcategory_id` — numeric ID (3005 for DC-DC Converters)
- `mounting_type` — "smd" or "tht"
- `specs` — dict of parametric specs (similar to DK but with slightly different field names)
- `description` — concatenated auto-generated string (verbose, machine-readable)
- `min_order` — minimum order (1, no MOQ constraint unlike DK)
- `reel_qty` — reel size for manufacturing (1500)
- `datasheet` — PDF link (LCSC-hosted mirror)
- `lcsc_url` — product page on LCSC.com
- `prices` — **string-keyed qty tiers** with price (different format than DK/Mouser: qty is "1+" not 1)
- `attributes` — redundant spec array (same as specs dict, but as array of {name, value})
- `has_easyeda_footprint` — **assembly-relevant**: whether EasyEDA footprint is available (JLC uses EasyEDA)
- `easyeda_symbol_uuid` & `easyeda_footprint_uuid` — **design-tool integration**: UUIDs for EasyEDA import

---

## 4. Cross-Reference Summary

| Field | Digi-Key | Mouser | JLC/LCSC | Purpose | Notes |
|-------|----------|--------|----------|---------|-------|
| **Distributor ID** | `source: "digikey"` | `source: "mouser"` | — (single source) | Identify origin | DK/Mouser use wrapper; JLC is implicit |
| **Distributor SKU** | `part_number: "1028-..."` | `part_number: "835-..."` | — | Cross-reference same part across distros | DK/Mouser SKUs differ; JLC uses LCSC# |
| **MPN (Canonical)** | `mfr_part_number` | `mfr_part_number` | `model` | Unique identifier across distributors | All three have it; use for matching |
| **LCSC Code** | — | — | `lcsc: "C2976596"` | **JLC-only**, needed for JLC assembly orders | Not available elsewhere |
| **Manufacturer** | `manufacturer` | `manufacturer` | `manufacturer` | Brand name | Varies in capitalization/format |
| **Package** | `parameters["Package / Case"]` + `parameters["Supplier Device Package"]` | `parameters["Packaging"]` (thin) | `package: "UQFN-12HL(3x3)"` | PCB footprint matching | DK most detailed; Mouser minimal; JLC is design-focused |
| **Stock** | `stock: 4` | `stock: 1418` | `stock: 6879` | Availability (highly variable!) | Can differ by 100x between distributors |
| **Unit Price** | `price: 2.4` | `price: 2.4` | `price: 0.7916` | Cost comparison | JLC often 50-70% cheaper for volume |
| **Pricing Tiers** | `price_breaks: [{qty, price}]` (sparse) | `price_breaks: [{qty, price, currency}]` (rich: 6 tiers) | `prices: [{qty: "1+", price}]` (rich: 6 tiers) | Bulk discounts | Mouser/JLC much more granular |
| **Min Order Qty** | `min_qty: 1500` | `min_qty: 1` | `min_order: 1` | Ordering constraint | **DK has high MOQ** (1500), others flexible |
| **Datasheet URL** | `datasheet_url` | `datasheet_url: ""` (empty) | `datasheet` (JLC mirror) | Technical reference | DK reliable; Mouser empty; JLC has backup |
| **Product URL** | `product_url` | `product_url` | `lcsc_url` | Distributor product page | Different per source |
| **Lifecycle Status** | `lifecycle: "Active"` | `lifecycle: "Active"` | — (inferred from stock) | EOL/discontinued risk | DK/Mouser explicit; JLC implicit |
| **RoHS Compliance** | `rohs: "ROHS3 Compliant"` | `rohs: "RoHS Compliant"` | (in description) | Regulatory | DK/Mouser explicit; JLC implicit |
| **Parametric Specs** | `parameters: {...}` (20+ fields) | `parameters: {...}` (2 fields, thin) | `specs: {...}` (10 fields) | Design calculations | **DK dominates** for specs; Mouser useless for components |
| **Assembly Capability** | — | — | `library_type: "extended"` | Cost/availability for SMT | **JLC-specific**: affects turnkey assembly cost |
| **EasyEDA Integration** | — | — | `has_easyeda_footprint`, `easyeda_symbol_uuid`, `easyeda_footprint_uuid` | **Design tool import** | JLC-specific; critical for schematic/PCB work |

---

## 5. Data Model Gaps: What SubsystemPick Is Missing

Current `SubsystemPick` model (from `hw_agent/core/research_bundle.py`):

```python
class SubsystemPick(BaseModel):
    id: str
    category: str
    mpn: str
    manufacturer: str = ""
    lcsc: str | None = None
    package: str = ""
    datasheet_url: str = ""
    qty_per_board: int = 1
    price_usd: float = 0.0
    stock: int = 0
    actuals: dict[str, float | int | str] = {}
    port_bindings: dict[str, str] = {}
```

### **Missing Fields from API Payloads:**

1. **`lifecycle_status: str | None`**
   - From: Digi-Key, Mouser
   - Use: Warn if "EOL", "Discontinued", "Obsolete"
   - Example: "Active" (good), "EOL" (risky)

2. **`rohs_status: str | None`**
   - From: Digi-Key, Mouser (explicit); JLC (implicit in description)
   - Use: Compliance requirement validation
   - Example: "ROHS3 Compliant" vs "RoHS Compliant"

3. **`price_breaks: list[tuple[int, float]] | None`**
   - From: All three (different structures)
   - Use: Cost modeling for different production volumes
   - Example: [(1, 2.4), (10, 2.36), (25, 2.18), (1500, 1.22)]
   - **Critical for:** "what if we need 1000 units?" cost assessment

4. **`min_order_qty: int | None`**
   - From: Digi-Key (`min_qty`), Mouser, JLC
   - Use: Check if minimum order is achievable
   - Example: DK 1500, Mouser 1, JLC 1

5. **`jlc_library_type: str | None`**
   - From: JLC only (library_type: "extended" or "basic"/"preferred")
   - Use: Calculate SMT assembly cost ($3 per extended part)
   - Example: "extended" (+ $3 fee) vs "basic" (free)

6. **`easyeda_available: bool`**
   - From: JLC only (`has_easyeda_footprint`)
   - Use: Can this part be directly imported into schematics?
   - Example: true if symbol/footprint exist in EasyEDA

7. **`distributor_sku: str | None`**
   - From: DK and Mouser (their internal SKU)
   - Use: Direct re-ordering without MPN lookup
   - Example: DK "1028-RT6228AGQUFTR-ND", Mouser "835-RT6228AGQUF"

8. **`stock_per_distributor: dict[str, int]`** (or keep as separate fields)
   - From: All three
   - Use: Sourcing decision (which distributor has best stock?)
   - Example: `{"digikey": 4, "mouser": 1418, "jlc": 6879}`
   - Currently we lose this — we only store single `stock` value

9. **`price_per_distributor: dict[str, float]`** (or keep as separate)
   - From: All three
   - Use: Cost comparison across sources
   - Currently we only store single `price_usd` value

10. **`parametric_specs: dict[str, str]`**
    - From: All three (different field names, but consistent meaning)
    - Use: Engineering validation (already in `actuals`, but could be explicit)
    - Example: `{"voltage_input_min": "4.5V", "current_output": "8A", "switching_freq": "500kHz"}`

---

## 6. Recommendations

### **Tier 1: Add to SubsystemPick Immediately**

These fields solve real distribution/cost problems:

1. **`lifecycle_status: str | None = None`** — Warn on EOL parts
2. **`price_breaks: list[tuple[int, float]] | None = None`** — Cost modeling
3. **`min_order_qty: int | None = None`** — Feasibility check
4. **`jlc_library_type: str | None = None`** — Assembly cost calculation

### **Tier 2: Add as Structured Commercial Metadata**

Create a parallel `SubsystemCommercial` model:

```python
class DistributorRecord(BaseModel):
    source: Literal["digikey", "mouser", "jlc"]
    sku: str  # part_number or lcsc
    stock: int
    price: float
    price_breaks: list[tuple[int, float]]
    min_order_qty: int
    datasheet_url: str = ""
    product_url: str = ""
    lifecycle: str = "Unknown"
    rohs: str = ""

class SubsystemCommercial(BaseModel):
    mpn: str
    manufacturer: str
    distributors: list[DistributorRecord]
    # Best price per tier (Mouser > DK > JLC usually differs)
    cheapest_source_qty_1: tuple[str, float]  # ("jlc", 0.7916)
    cheapest_source_qty_1000: tuple[str, float]
```

**Then SubsystemPick stays lean** and references commercial as a separate lookup.

### **Tier 3: Track EasyEDA Availability**

For JLC assembly workflows:

```python
class JLCAssemblyInfo(BaseModel):
    lcsc: str
    library_type: Literal["basic", "preferred", "extended"]
    easyeda_symbol_available: bool
    easyeda_footprint_available: bool
    reel_qty: int
```

---

## 7. Implementation Priority

**Do first:** Add Tier 1 fields to `SubsystemPick` — these unblock cost validation and EOL detection.

**Do next:** If turnkey JLC assembly is the project goal, add `JLCAssemblyInfo` as optional field.

**Do later:** Refactor commercial metadata into structured `DistributorRecord` list if we support multi-distributor sourcing strategies.

---

## Testing

The fixtures already exist at:
- `/hw_agent/domain/templates/fixtures/RT6228AGQUF.digikey.json`
- `/hw_agent/domain/templates/fixtures/RT6228AGQUF.mouser.json`
- `/hw_agent/domain/templates/fixtures/RT6228AGQUF.jlc.json`

**Next steps:** Write unit tests for `_commercial_from_dk()`, `_commercial_from_mouser()`, `_commercial_from_jlc()` to ensure we're not silently losing fields from the fixtures.
