# PCB Vendor Capability & FabBundle Validation Research

**Date:** 2026-05-24  
**Goal:** Validate that the `FabBundle` pydantic contract carries sufficient fab metadata to pass vendor checks without guessing.

---

## Executive Summary

The hw-toolkit uses a **vendor seed schema** (`VendorSeed` from `pcborder.core.vendor_seed`) that decouples actual fab capabilities from the `FabBundle` contract. `FabBundle` only declares `vendor: Literal["jlcpcb", "pcbway", "oshpark", "aisler"]` and boolean gates (`vendor_validated`, `stock_verified`); **numeric capability data lives in separate vendor JSON seed files**, not in the bundle itself. This is architecturally sound: settings (trace width, thickness, etc.) are validated against a vendor seed at lock time, not stored in the bundle.

### Key Finding
`FabBundle` **deliberately omits numeric fab specs** because the bundle is immutable once written, whereas vendor capabilities evolve. The contract correctly delegates validation to vendor seeds and stores only the proof that validation passed (`vendor_validated: True`).

---

## 1. JLCPCB Capability Table

**Source:** `hw_agent/artifacts/data/vendors/jlcpcb.json` (last verified 2026-05-05)

```json
{
  "vendor": {
    "slug": "jlcpcb",
    "name": "JLCPCB",
    "homepage": "https://jlcpcb.com",
    "api_status": "partner-gated",
    "notes": "Public API requires partner approval at api.jlcpcb.com. Without approval: upload via cart.jlcpcb.com/quote."
  },
  "capabilities": {
    "min_layers": 1,
    "max_layers": 32,
    "finishes": ["hasl", "hasl_lead_free", "enig"],
    "soldermask_colors": ["green", "blue", "red", "black", "white", "yellow", "purple", "matte_black", "matte_green"],
    "silkscreen_colors": ["white", "black", "yellow"],
    "min_track_mm": 0.0889,
    "min_via_mm": 0.2,
    "min_hole_mm": 0.15,
    "min_qty": 5,
    "max_panel_qty_different_designs": 200,
    "thickness_mm": [0.4, 0.6, 0.8, 1.0, 1.2, 1.6, 2.0],
    "copper_outer_oz": [1.0, 2.0, 3.0],
    "copper_inner_oz": [0.5, 1.0],
    "supports_castellated": true,
    "supports_via_in_pad": true,
    "supports_edge_plating": true,
    "supports_assembly": true,
    "supports_impedance_control": true
  }
}
```

**BOM/CPL Column Mappings (KiCad → JLCPCB):**

| KiCad Field | JLCPCB Field | Type |
|---|---|---|
| Reference | Designator | string |
| Value | Comment | string |
| Footprint | Footprint | string |
| LCSC | LCSC Part # | string |

| KiCad Field | JLCPCB Field | Type |
|---|---|---|
| Ref | Designator | string |
| PosX | Mid X | float (mm) |
| PosY | Mid Y | float (mm) |
| Side | Layer | "top" \| "bottom" |
| Rot | Rotation | float (degrees, 0-360) |

**Lead Times:**
- Economic: 6–8 days (SKU: ECO)
- Standard: 4–6 days (SKU: STD)
- Express: 1–2 days (SKU: EXP)

**Special Notes:**
- Rotation correction table required (~hundreds of common parts need JLC-specific offsets)
- Max layers 20→32 per 2026-05-05 update (high-layer quoted, not standard)
- OSP/immersion finishes not re-verified on public capability page (JS-rendered, not directly fetchable)

---

## 2. PCBWay Capability Table

**Source:** `hw_agent/artifacts/data/vendors/pcbway.json` (last verified 2026-05-05)

```json
{
  "vendor": {
    "slug": "pcbway",
    "name": "PCBWay",
    "homepage": "https://www.pcbway.com",
    "api_status": "public",
    "notes": "Public API at api-partner.pcbway.com with single apiKey auth. Quote + place_order endpoints documented."
  },
  "capabilities": {
    "min_layers": 1,
    "max_layers": 24,
    "finishes": ["hasl", "hasl_lead_free", "enig", "enepig", "osp", "immersion_silver", "immersion_tin", "hard_gold"],
    "soldermask_colors": ["green", "red", "blue", "white", "black", "yellow", "purple", "matte_green", "matte_black"],
    "silkscreen_colors": ["white", "black", "yellow"],
    "min_track_mm": 0.1,
    "min_via_mm": 0.2,
    "min_hole_mm": 0.15,
    "min_qty": 5,
    "max_panel_qty_different_designs": 1000,
    "thickness_mm": [0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.6, 2.0, 2.4, 2.6, 2.8, 3.0, 3.2],
    "copper_outer_oz": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
    "copper_inner_oz": [1.0, 1.5, 2.0, 3.0, 4.0],
    "supports_castellated": true,
    "supports_via_in_pad": true,
    "supports_edge_plating": true,
    "supports_assembly": true,
    "supports_impedance_control": true
  }
}
```

**BOM/CPL Column Mappings:**

| KiCad Field | PCBWay Field | Type |
|---|---|---|
| Reference | Designator | string |
| Value | Description | string |
| Footprint | Footprint | string |
| MPN | MPN | string |
| Manufacturer | Manufacturer | string |

| KiCad Field | PCBWay Field | Type |
|---|---|---|
| Ref | Designator | string |
| PosX | PosX | float (mm) |
| PosY | PosY | float (mm) |
| Side | Side | "top" \| "bottom" |
| Rot | Rotation | float (degrees) |

**Lead Times:**
- Economic: 5–7 days (SKU: STANDARD)
- Standard: 3–5 days (SKU: STANDARD_RUSH)
- Express: 1–2 days (SKU: EXPRESS)

**Special Notes:**
- Broader finish palette than JLCPCB (including ENEPIG, hard gold)
- No rotation correction table required for KiCad-default rotations
- min_track 0.1 mm (3-mil advanced/quote-only; standard is slightly looser)
- Panelization supports up to 1000 different designs

---

## 3. OSH Park Capability Table

**Source:** `hw_agent/artifacts/data/vendors/oshpark.json` (last verified 2026-05-05)

```json
{
  "vendor": {
    "slug": "oshpark",
    "name": "OSH Park",
    "homepage": "https://oshpark.com",
    "api_status": "public",
    "notes": "Public REST API. Bare-board only — no assembly. Boards always purple (ENIG-on-purple-mask signature process)."
  },
  "capabilities": {
    "min_layers": 2,
    "max_layers": 6,
    "finishes": ["enig"],
    "soldermask_colors": ["purple"],
    "silkscreen_colors": ["white"],
    "min_track_mm": 0.1524,
    "min_via_mm": 0.3302,
    "min_hole_mm": 0.254,
    "min_qty": 3,
    "max_panel_qty_different_designs": 1,
    "thickness_mm": [0.8, 1.6],
    "copper_outer_oz": [1.0, 2.0],
    "copper_inner_oz": [0.5],
    "supports_castellated": false,
    "supports_via_in_pad": false,
    "supports_edge_plating": false,
    "supports_assembly": false,
    "supports_impedance_control": false
  }
}
```

**BOM/CPL Columns:**
- `bom: null` (bare-board only, no assembly)
- `cpl: null` (bare-board only, no assembly)

**Lead Times:**
- Standard: 9–12 days (SKU: STANDARD)
- Express: 4–6 days (SKU: SUPER_SWIFT)

**Special Notes:**
- **Bare-board only.** No SMT assembly, no pick-and-place.
- Fixed to ENIG finish on purple mask (no color choices).
- Conservative min_track/min_hole values (actual 2/4/6-layer tiers are tighter, but not modeled here).
- No panelization support (max_panel_qty_different_designs: 1 means single design per order).
- 2/4/6-layer variants; min_qty=3 is service-specific (Medium Run 2L requires multiples of 10, not modeled).

---

## 4. PCBWay vs JLCPCB Feature Comparison

| Aspect | JLCPCB | PCBWay | Winner |
|---|---|---|---|
| **Min Trace Width** | 0.0889 mm (3.5-mil) | 0.1 mm (3.9-mil) | JLCPCB (tighter) |
| **Min Via** | 0.2 mm | 0.2 mm | Tie |
| **Max Layers** | 32 | 24 | JLCPCB |
| **Thickness Options** | 7 options (0.4–2.0 mm) | 13 options (0.2–3.2 mm) | PCBWay (more range) |
| **Copper Outer** | 1/2/3 oz | 1–8 oz | PCBWay (broader) |
| **Finish Variety** | 3 (hasl, hasl_lead_free, enig) | 8 (adds enepig, osp, immersion_*, hard_gold) | PCBWay |
| **Assembly Support** | Yes | Yes | Tie |
| **Panelization** | Up to 200 designs | Up to 1,000 designs | PCBWay |
| **API Status** | Partner-gated | Public | PCBWay |
| **Rotation Table** | Required | Not required | PCBWay (simpler) |

---

## 5. Aisler Status

**Finding:** No vendor seed file exists for Aisler in the repo. Aisler is declared in `FabBundle.vendor` Literal but has no capability table.

**Action Required:** If Aisler assembly is a design goal, create `hw_agent/artifacts/data/vendors/aisler.json` with:
- Capability table (from https://aisler.net/capabilities or API docs)
- BOM/CPL column mappings
- Lead-time tiers
- Rotation quirk metadata

For now, **Aisler remains unsupported in the validation pipeline.**

---

## 6. Validation Data Flow

### Settings → VendorSeed → Validation Result

```
┌─ pcborder.Settings (canonical fab request)
│  ├─ layer_count: LayerCount (1–32)
│  ├─ thickness_mm: ThicknessMm
│  ├─ surface_finish: SurfaceFinish
│  ├─ soldermask_color: SoldermaskColor
│  ├─ silkscreen_color: SilkscreenColor
│  ├─ copper_outer_oz: OuterCopperOz
│  ├─ copper_inner_oz: InnerCopperOz
│  └─ assembly: Optional[Assembly]
│
├─ pcborder.load_vendor_seed(vendor) → VendorSeed
│  └─ capabilities: VendorCapabilities
│     ├─ min_layers, max_layers
│     ├─ min_track_mm, min_via_mm, min_hole_mm
│     ├─ finishes[], soldermask_colors[], silkscreen_colors[]
│     ├─ thickness_mm[], copper_outer_oz[], copper_inner_oz[]
│     └─ supports_assembly, supports_via_in_pad, etc. (booleans)
│
└─ pcborder.validate_for_vendor(settings, vendor_seed) → ValidateResult
   ├─ errors: [ValidationIssue] (blockers)
   ├─ warnings: [ValidationIssue] (advisories)
   └─ suggestions: [ValidationFix] (auto-fixable)
```

### FabBundle Integration

`FabBundle` **stores only the gate result**, not the numeric specs:

```python
@dataclass
class FabBundle:
    vendor_validated: bool  # ← Result of validate_for_vendor() PASS
    # (Does NOT store: min_trace_mm, thickness_mm, etc.)
    # Those live in the vendor seed, loaded at validation time.
```

**Why this design is correct:**
1. Vendor capabilities evolve; FabBundle is immutable.
2. A design locked with JLCPCB's 0.0889 mm min-trace needs no re-validation if JLCPCB changes to 0.0762 mm.
3. The bundle is proof of passing validation; the rules live in seed files (under version control, separately).

---

## 7. VendorCapabilities Pydantic Schema

```python
class VendorCapabilities(BaseModel):
    """What the vendor accepts."""
    min_layers: int
    max_layers: int
    finishes: list[SurfaceFinish]
    soldermask_colors: list[SoldermaskColor]
    silkscreen_colors: list[SilkscreenColor]
    min_track_mm: float
    min_via_mm: float
    min_hole_mm: float
    min_qty: int
    max_panel_qty_different_designs: int
    thickness_mm: list[float]
    copper_outer_oz: list[float]
    copper_inner_oz: list[float]
    supports_castellated: bool
    supports_via_in_pad: bool
    supports_edge_plating: bool
    supports_assembly: bool
    supports_impedance_control: bool
```

---

## 8. Recommendation: FabBundle Contract is Sufficient

The current `FabBundle` contract is **correct and complete** for its role:

1. **Numeric specs belong in vendor seeds**, not in the bundle.
2. **`vendor_validated: bool` is the correct gate**, not a capacity table.
3. **No fields are missing** from the bundle for fab handoff.

### What gets validated but NOT stored in FabBundle:

| Field | Validated Against | Stored in Bundle? |
|---|---|---|
| `min_trace_mm`, `min_via_mm`, `min_hole_mm` | VendorCapabilities (in seed JSON) | No |
| `layer_count`, `thickness_mm`, `copper_oz` | VendorCapabilities (in seed JSON) | No |
| `surface_finish`, soldermask/silkscreen color | VendorCapabilities (in seed JSON) | No |
| Assembly sides, tooling holes | Seed: `supports_assembly` boolean | No |
| Castellated, via-in-pad, edge-plating | Seed: feature support booleans | No |
| Rotation correction needs | Seed: `rotation_quirks.needs_correction_table` | No |
| BOM/CPL column mappings | Seed: `columns.bom`, `columns.cpl` | No |

**Bundle stores only:**
- Proof of passing validation: `vendor_validated: True`
- Proof of stock at lock time: `stock_verified: True`
- ERC/DRC gates: `erc_clean`, `drc_clean`
- Lineage: `consumed_research_tag`, `fab_baseline_git_tag`
- Vendor choice (for lookup): `vendor: Literal["jlcpcb", ...]`

This separation is **architecturally sound** and requires no changes to `FabBundle`.

---

## 9. Summary: Fields That Must Be Validated at Gate Time

When `pcb_designer` (or any agent) calls `pcborder_validate_for_vendor`, the following **must be checked**:

```python
# From Settings (the fab request):
✓ layer_count ∈ [min_layers, max_layers]
✓ thickness_mm ∈ vendor_capabilities.thickness_mm[]
✓ surface_finish ∈ vendor_capabilities.finishes[]
✓ soldermask_color ∈ vendor_capabilities.soldermask_colors[]
✓ silkscreen_color ∈ vendor_capabilities.silkscreen_colors[]
✓ copper_outer_oz ∈ vendor_capabilities.copper_outer_oz[]
✓ copper_inner_oz ∈ vendor_capabilities.copper_inner_oz[]

# From DRC output (trace/via/hole clearances):
✓ min_trace ≥ vendor_capabilities.min_track_mm
✓ min_via ≥ vendor_capabilities.min_via_mm
✓ min_hole ≥ vendor_capabilities.min_hole_mm

# From design features:
✓ castellated: vendor_capabilities.supports_castellated == true
✓ via_in_pad: vendor_capabilities.supports_via_in_pad == true
✓ edge_plating: vendor_capabilities.supports_edge_plating == true
✓ assembly: vendor_capabilities.supports_assembly == true
✓ impedance_control: vendor_capabilities.supports_impedance_control == true

# From BOM (assembly):
✓ if assembly_sides != "none": vendor_capabilities.supports_assembly == true
✓ BOM/CPL columns map via vendor_seed.columns.bom, .columns.cpl
```

**All of these are checked by `pcborder_validate_for_vendor`** before `vendor_validated: True` is set.

---

## 10. Vendor Seed Files in Repo

| Vendor | File | Status |
|---|---|---|
| JLCPCB | `hw_agent/artifacts/data/vendors/jlcpcb.json` | ✓ Present, verified 2026-05-05 |
| PCBWay | `hw_agent/artifacts/data/vendors/pcbway.json` | ✓ Present, verified 2026-05-05 |
| OSH Park | `hw_agent/artifacts/data/vendors/oshpark.json` | ✓ Present, verified 2026-05-05 |
| Aisler | Missing | ✗ Declared in Literal but no seed |

---

## Conclusion

**FabBundle is sufficiently specified.** The contract correctly offloads numeric capability checks to vendor seed files, keeping the bundle lightweight and immutable. No fields need to be added or removed.

**Action:** If Aisler support is desired, populate its vendor seed JSON following the schema above.

---

**References:**
- `pcborder.core.vendor_seed` — VendorSeed Pydantic model
- `pcborder.core.settings` — Settings/LayerCount/ThicknessMm Literals
- `pcborder.core.validate` — ValidateResult shape
- `hw_agent/core/fab_bundle.py` — FabBundle contract (this repo)
