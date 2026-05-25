# KiCad Library Convention (KLC) — Symbol & Footprint Fields Research

**Date:** 2026-05-24  
**Goal:** Validate `SubsystemPick` model field-name mappings to KiCad symbol properties.

---

## 1. KLC Mandatory Symbol Properties

Per [KLC official spec (klc.kicad.org)](https://klc.kicad.org/):

### Required Fields (visible)
- **Reference** — Symbol instance designator (e.g., "U1", "R5"). Must be selected appropriately for symbol type and remain visible.
- **Value** — Symbol name or component value (e.g., "LM358P", "10kΩ"). Must be visible.

### Required for Fully-Specified Symbols (invisible)
- **Footprint** — Link to PCB footprint in format `<library>:<footprint_name>` (e.g., `Package_SO:SOIC-8_3.9x4.9mm_P1.27mm`). Empty for generic symbols.
- **Datasheet** — URL to manufacturer's datasheet or `~` for generic symbols. Invisible.

### Recommended (invisible)
- **Description** — Comma-separated device info. For fully-specified symbols, include simplified footprint name (e.g., "Operational Amplifier, 8-Pin SOIC").
- **Keywords** — Space-separated search terms to help locate component.

### Not Standard
Per KLC, "the symbol contains no other custom fields" except those prefixed with `KLC_` (only after librarian discussion). This means standard KiCad library symbols avoid ad-hoc "MPN", "Manufacturer", or "LCSC" fields in the public library.

---

## 2. Actual Community & Assembly Practice

Despite KLC's formal restriction, real-world designs use custom fields extensively:

### Common Custom Fields (de facto standard)
From web research and tool analysis:

| Field Name | Purpose | Example | Source |
|-----------|---------|---------|--------|
| **MPN** or **Manufacturer Part Number** | IC/component part number | `LM358P`, `STM32F103C8T6` | Bouni/kicad-jlcpcb-tools, uPesy/easyeda2kicad |
| **Manufacturer** | Company name (ASCII-safe) | `Texas Instruments`, `ST Microelectronics` | KiCad Schematic Editor (v9.0) |
| **LCSC** or **LCSC Part #** | JLCPCB assembly database code | `C8304`, `C2557` | JLCPCB community practice |
| **Package** | Footprint package type | `SOIC-8`, `QFN-20_4x4mm` | EasyEDA/LCSC exports |
| **Datasheet** (standard) | Mfr documentation URL | `https://...` | KLC standard |

**No single universal standard exists.** Different tools and vendors use different names. However, these four are de facto dominant in the open-source & Chinese assembly (JLCPCB) ecosystem.

---

## 3. Footprint Properties (.kicad_mod format)

Footprints define the physical PCB layout. Mandatory properties:

```
(footprint "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"
  (layer "F.Cu")
  (attr smd)
  (fp_text reference "U1" (at 0 -3.5) (effects (font (size 1.27 1.27))))
  (fp_text value "LM358P" (at 0 3.5) (effects (font (size 1.27 1.27))))
  
  (pad "1" smd rect (at -1.95 -2.35) (size 1.9 0.6) (drill 0))
  (pad "2" smd rect (at -1.95 -0.78) (size 1.9 0.6) (drill 0))
  ...
)
```

**Mandatory per footprint:**
- Library name (e.g., `Package_SO`)
- Layer assignment (`F.Cu`, `B.Cu`, `*Cu`)
- Pads: pad number, position (x, y mm), size, drill info
- Reference + Value text fields (at minimum)

No "custom" footprint fields — properties are positional and topological (pad#, net, layer).

---

## 4. Real Symbol .kicad_sym File Format

KiCad symbol library format (S-expression, version 20231120):

```kicad_sym
(kicad_symbol_lib (version 20231120) (generator "hw-agent")

  (symbol "LM358P"
    (pin_numbers (hide yes))
    (pin_names (offset 0.254))
    (in_bom yes) (on_board yes)
    
    ; Mandatory properties
    (property "Reference" "U" (at 0 5.08 0)
      (effects (font (size 1.27 1.27))))
    (property "Value" "LM358P" (at 0 -5.08 0)
      (effects (font (size 1.27 1.27))))
    (property "Footprint" "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm" (at 0 0 0)
      (effects (font (size 1.27 1.27)) (hide yes)))
    (property "Datasheet" "http://www.ti.com/lit/ds/symlink/lm358.pdf" (at 0 0 0)
      (effects (font (size 1.27 1.27)) (hide yes)))
    
    ; Recommended (optional in KLC)
    (property "Description" "Operational Amplifier, 8-Pin SOIC" (at 0 0 0)
      (effects (font (size 1.27 1.27)) (hide yes)))
    (property "Keywords" "opamp amplifier op-amp" (at 0 0 0)
      (effects (font (size 1.27 1.27)) (hide yes)))
    
    ; Custom fields (NOT in KLC standard library, but common in projects)
    (property "Manufacturer" "Texas Instruments" (at 0 0 0)
      (effects (font (size 1.27 1.27)) (hide yes)))
    (property "MPN" "LM358P" (at 0 0 0)
      (effects (font (size 1.27 1.27)) (hide yes)))
    (property "LCSC" "C8304" (at 0 0 0)
      (effects (font (size 1.27 1.27)) (hide yes)))
    
    ; Symbol graphic (units and pins)
    (symbol "LM358P_0_1" ...)
    (symbol "LM358P_1_1" ...)
  )
)
```

**Key observations:**
1. Properties are name-value pairs with optional position + formatting.
2. `(effects ... (hide yes))` marks invisible fields (standard for Datasheet, Footprint, custom fields).
3. No schema validation — you can add any property name. KLC forbids ad-hoc ones in the official library.
4. Pin definitions are in sub-units (`_0_1`, `_1_1`, etc.) — separate from properties.

---

## 5. JLCPCB Assembly Workflow & Field Names

### JLC Plugin (Bouni/kicad-jlcpcb-tools)
The plugin assigns LCSC part numbers to footprints during assembly generation. Per community practice:

- **Read field name**: Usually looks for `LCSC` or `LCSC Part #` in the symbol
- **Write field name**: Assigns to footprint property (not schematic symbol) during BOM export
- **BOM export**: Gerber job file + CSV with columns like `Designator, Value, Footprint, LCSC Part #`

### JLCPCB's Official Expectations
No public doc explicitly names the schematic field requirements. However, de facto standard from user community:

| Field | Name Variant(s) | Used By |
|-------|---|---|
| Part ID | `LCSC`, `LCSC Part #`, `LCSC Part Code` | JLC plugin, EasyEDA exports |
| MPN | `MPN`, `Manufacturer Part Number` | Database libraries, some plugins |
| Manufacturer | `Manufacturer` | Database libraries, part specs |
| Package | `Package` (or absent — inferred from footprint) | EasyEDA, LCSC |

**Reality:** Most projects use ad-hoc naming. The JLCPCB plugin is forgiving — it looks for multiple variants and falls back gracefully.

---

## 6. Community Best Practices

From KiCad forums and real projects:

1. **Official KLC symbols** (in `kicad-symbols` repo): Only Reference, Value, Footprint, Datasheet, optionally Description/Keywords.
2. **Database-backed project symbols**: Reference, Value, Footprint, Datasheet + custom Manufacturer, MPN.
3. **LCSC/EasyEDA-sourced symbols**: Same as (2) plus LCSC field.
4. **Internal corporate libraries**: Use KLC_-prefixed fields for house-specific data (e.g., `KLC_Cost_Internal`, `KLC_Supplier`).

### Naming Consistency Matters
Mismatch between schematic symbol field name and BOM/assembly tool expectations breaks workflow. Example:
- Symbol has `"MPN"` field → BOM column expects `"Manufacturer Part Number"` → data lost or corrupted.

---

## 7. hw-agent SubsystemPick → KiCad Mapping

### Current SubsystemPick Fields (from `hw_agent/core/research_bundle.py`)

```python
class SubsystemPick(BaseModel):
    id: str                          # Symbol reference (U1, R5, etc.)
    category: str                    # Template type (buck_converter, ldo, etc.)
    
    # Part identity — projected into KiCad symbol fields
    mpn: str                         # Manufacturer part number
    manufacturer: str = ""           # Company name
    lcsc: str | None = None          # JLCPCB assembly code
    package: str = ""                # Footprint package type
    datasheet_url: str = ""          # Documentation URL
    
    # BOM + stock
    qty_per_board: int = 1
    price_usd: float = 0.0
    stock: int = 0
    
    # Layout-relevant actuals (internal to design)
    actuals: dict[str, float | int | str]
    
    # Port → Interface binding (internal)
    port_bindings: dict[str, str]
```

### Recommended KiCad Symbol Property Mapping

| SubsystemPick Field | → | KiCad Property Name | Visibility | Notes |
|-----|---|---|---|---|
| `mpn` | → | `"MPN"` | Hidden | Alternative: "Manufacturer Part Number" (avoid if possible) |
| `manufacturer` | → | `"Manufacturer"` | Hidden | Must be ASCII-safe |
| `lcsc` | → | `"LCSC"` | Hidden | Alternative: "LCSC Part #" (for compatibility with JLC plugin) |
| `package` | → | `"Package"` | Hidden | Informational; footprint is authoritative |
| `datasheet_url` | → | `"Datasheet"` | Hidden | KLC standard; must be URL or `~` |
| `id` (ref designator) | → | `"Reference"` | Visible | Generated at placement time (U1, R5, etc.) |
| `category` (value) | → | `"Value"` | Visible | Fallback if no other value; often overridden |

### Implementation Notes

1. **"MPN" is safe**: Widely recognized by tools (database libraries, BOM generators, plugins).
2. **"LCSC" vs "LCSC Part #"**: Use `"LCSC"` for conciseness; tools are forgiving.
3. **"Package" is FYI only**: The authoritative package is the Footprint property (e.g., `"Package_SO:SOIC-8..."`). Store `package` as metadata for reference.
4. **Internal fields** (`qty_per_board`, `price_usd`, `stock`, `actuals`, `port_bindings`) → **DO NOT project into KiCad symbols**. Keep them in the subsystem's JSON or design database only.

---

## 8. Current hw-agent KiCad Writer Status

From `/hw_agent/artifacts/schematics/ksa_writer.py`:

- **Inline-pin ICs** (`type="ic"`) are synthesized into `hwagent.kicad_sym` with:
  - Mandatory: Reference, Value, Footprint (from Symbol.footprint), Datasheet (empty string)
  - No custom fields currently written (MPN, Manufacturer, LCSC absent)

- **KiCad-library symbols** (`type="kicad"`) are placed via kicad-sch-api with:
  - Only lib_id, reference, value, footprint passed
  - Properties from the original library symbol are preserved; no overrides

- **Passives** (R, C, L):
  - Reference, Value, Footprint set; Datasheet left empty

**Gap:** Custom fields (MPN, Manufacturer, LCSC) are not currently written to any symbol. They must be added for BOM export to work correctly.

---

## 9. Design Rule: Mandatory KLC vs. Project Practice

| Context | Reference | Value | Footprint | Datasheet | MPN | Manufacturer | LCSC |
|---------|---|---|---|---|---|---|---|
| **KLC official library** | ✓ visible | ✓ visible | ✓ hidden (or empty) | ✓ hidden (or ~) | ✗ forbidden | ✗ forbidden | ✗ forbidden |
| **hw-agent export** | ✓ (auto) | ✓ (auto) | ✓ if provided | ✗ empty | **MISSING** | **MISSING** | **MISSING** |
| **JLCPCB assembly req'd** | ✓ | ✓ | ✓ | optional | ✓ (from LCSC) | optional | ✓ |
| **Best practice (project)** | ✓ visible | ✓ visible | ✓ hidden | ✓ hidden | ✓ hidden | ✓ hidden | ✓ hidden (if LCSC) |

**Recommendation:** hw-agent **must project MPN, Manufacturer, LCSC into KiCad symbols** before BOM export, even though they're non-standard per KLC. This is the only way to preserve part identity through the schematic → PCB → fabrication flow.

---

## 10. Field-Name Strings for Projection

When `SubsystemPick` is converted to a KiCad symbol (in `ksa_writer._place_symbol` or equivalent), write these exact property names:

```python
# From SubsystemPick → into (property "..." ...) blocks in .kicad_sch

if sym.mpn:
    properties.add(name="MPN", value=sym.mpn, hidden=True)

if sym.manufacturer:
    properties.add(name="Manufacturer", value=sym.manufacturer, hidden=True)

if sym.lcsc:
    # Use "LCSC" for brevity; some tools also accept "LCSC Part #"
    properties.add(name="LCSC", value=sym.lcsc, hidden=True)

if sym.package:
    properties.add(name="Package", value=sym.package, hidden=True)

if sym.datasheet_url:
    properties.add(name="Datasheet", value=sym.datasheet_url, hidden=True)
```

**Critical:** Use exact string match on property names. KiCad's BOM exporters (including JLC plugin) are case-sensitive and may not find fields if names are misspelled or inconsistent.

---

## 11. Fields NOT to Project into KiCad

The following `SubsystemPick` fields are **internal design state**, not symbol metadata:

| Field | Reason | Storage |
|-------|--------|---------|
| `id` (subsystem ID) | This is the template instance name, not the symbol ref. Ref is auto-generated at placement. | Design JSON only |
| `category` | Subsystem type (buck_converter, ldo, etc.). Belongs in design spec, not symbol. | Design JSON only |
| `qty_per_board` | BOM/procurement; belongs in component table or order metadata. | BOM CSV or order_settings.json |
| `price_usd` | Cost data; not a design property. | BOM/cost tracking database |
| `stock` | Inventory state; not a design property. | Supply-chain database |
| `actuals` | Layout-internal specs (Tj, Pdiss, ripple, etc.); not user-facing. | Design JSON only |
| `port_bindings` | Interface routing; belongs in netlist or connection diagram. | Design JSON only |

**Rationale:** Storing these in KiCad symbols pollutes the schematic with non-electrical metadata and breaks downstream ERC/DRC checks. Keep them in the research bundle JSON and BOM database.

---

## Conclusion

**For hw-agent SubsystemPick → KiCad symbol export:**

1. **Always write:** Reference (auto), Value (auto), Footprint (if present), Datasheet (if present).
2. **Must add:** MPN, Manufacturer, LCSC (if present in SubsystemPick).
3. **Field names:** Use exact strings: `"MPN"`, `"Manufacturer"`, `"LCSC"`, `"Package"`.
4. **Visibility:** All custom fields hidden (`effects ... (hide yes)`).
5. **Never write into KiCad:** qty_per_board, price_usd, stock, actuals, port_bindings, category, id.

This ensures the schematic is electrically correct, the symbol carries part-identity metadata for BOM assembly, and downstream tools (JLC plugin, fabrication house, internal accounting) can extract the data they need.

---

## References

- [KLC Full Spec](https://klc.kicad.org/)
- [KiCad Forum: Standard Symbol Field Names Initiative](https://forum.kicad.info/t/standard-symbol-field-names-initiative/4870)
- [KiCad Schematic Editor Docs v9.0](https://docs.kicad.org/9.0/en/eeschema/eeschema.html)
- [uPesy easyeda2kicad.py](https://github.com/uPesy/easyeda2kicad.py)
- [Bouni kicad-jlcpcb-tools](https://github.com/Bouni/kicad-jlcpcb-tools)
