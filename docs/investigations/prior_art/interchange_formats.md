# PCB Interchange Formats: Prior Art & Suitability for AI Design Agents

Date: 2026-05-24
Research Goal: Evaluate whether `ResearchBundle` and `FabBundle` pydantic contracts should align to existing interchange specs, or occupy a unique higher-level design-intent layer.

---

## Executive Summary

**Recommendation:** Our `ResearchBundle` and `FabBundle` occupy a unique **design-intent layer above all existing interchange formats**. Existing specs model physical truth (nets, footprints, copper) or lower-level exchange (schematic → fab). None carry the "design intent" that agents need: subsystem abstraction, interface semantics, performance specs, and decision rationale.

**Action:** Keep our pydantic contracts as-is. At the `FabBundle` boundary, *reference* IPC-2581 exports where applicable, but do not force our payload into its schema.

---

## Format Inventory

### 1. **IPC-2581** (Digital Product Model eXchange)
**Scope:** ECAD ↔ fab interchange. Modern open standard (v1.0 published 2015, active updates).  
**Top-level entities:**
- Component library (symbol → footprint → 3D model links)
- Net topology (electrical connectivity)
- Geometric layout (traces, vias, copper pours, silkscreen)
- Manufacturing attributes (impedance, stackup, material specs)
- Assembly directives (component placement, rotations, DNP marks)

**Granularity:** **Physical truth only.** No design intent, no performance specs, no rationale.

**Schema form:** XML + embedded UUIDs for cross-reference.

**Sample snippet:**
```xml
<ECAD xmlns="http://www.ipc.org/2581">
  <Components>
    <Component id="C1">
      <Part manufacturer_pn="STM32F103C8T6">
        <Placement x="10.5" y="20.0" rotation="0" />
        <Footprint library_id="pkg_lqfp48" />
      </Part>
    </Component>
  </Components>
  <Nets>
    <Net name="VCC">
      <Pins>C1.VCC U2.pin5 R1.pad2</Pins>
    </Net>
  </Nets>
</ECAD>
```

**AI Suitability:** Low. Verbose, deeply nested. No way to express "this subsystem drives 2A at 5V" or "chose STM32 over PIC because of UART count". Pure geometry + assembly.

---

### 2. **ODB++** (Ohm Data Build)
**Scope:** Older de-facto fab interchange (Mentor/Siemens, now open-spec via IPC-5411).  
**Top-level entities:**
- Design metadata (author, timestamp, stackup definition)
- Net connectivity (nodes, pins, via connectivity)
- Component placement (X, Y, rotation, side, reference designator)
- Copper shapes (per-layer, via stacks)
- Soldermask, silkscreen, solder-paste apertures

**Granularity:** Physical truth. Richer geometric detail than IPC-2581 (explicit aperture/mask shapes), weaker in component metadata.

**Schema form:** Binary archive (ZIP + ASCII property files).

**AI Suitability:** Very low. Binary, antiquated, minimal semantic metadata. Still used in fab shops but irrelevant to agent handoff.

---

### 3. **EDIF** (Electronic Design Interchange Format)
**Scope:** Neutral schematic exchange. ISO 14629. Published 1992, last revision 2004. Legacy.  
**Top-level entities:**
- Library definitions (cells = logic symbols or macros)
- Design (top-level circuit: cell instances, port references, net bindings)
- Property lists (capacitance, timing, supply requirements)

**Granularity:** Schematic-level. No layout, no physical footprint info. Primarily logic-centric (gates, logic functions), weak for analog/power.

**Schema form:** S-expression (Lisp-like). Human-readable but sparse.

**Sample snippet:**
```lisp
(design "MyCircuit"
  (cellRef "AND2" (libraryRef "Logic"))
  (net "CLK"
    (portRef "Q" (instanceRef "U1"))
    (portRef "A" (instanceRef "U2"))))
```

**AI Suitability:** Very low. Designed for 1990s schematic tools, agnostic to modern power/signal design. No component selection, no performance attributes.

---

### 4. **KiCad Native Formats** (.kicad_sch, .kicad_pcb, .kicad_pro)
**Scope:** KiCad 6+ native interchange (S-expression). Not a published standard; de-facto open.  
**Top-level entities:**
- Schematic (.kicad_sch): symbols, wires, net labels, hierarchical sheets, properties
- PCB (.kicad_pcb): footprints, tracks, vias, zones, 3D models, design rules
- Project (.kicad_pro): metadata (layers, stackup, footprint library links, design rules, net classes)

**Granularity:** **Physical truth.** Everything KiCad needs to edit and export. No higher-level intent.

**Schema form:** S-expression. Human-readable, reasonably compact.

**AI Suitability:** Medium. Well-documented, stable format. But pure file I/O; agents still need to synthesize "subsystem" and "interface" concepts on top. KiCad_pro is closest to design intent, but still tool-centric.

---

### 5. **CircuitJSON / Open JSON Hardware**
**Scope:** Modern JSON circuit IR. Attempted lingua franca for tools.  
**Top-level entities:**
- Components (symbol reference + footprint + properties)
- Nets (node connectivity)
- Traces, vias, zones (if layout info included)
- Hierarchical blocks (functional grouping)

**Granularity:** Physical truth + optional hierarchical grouping. Blocks can represent subsystems, but semantics are tool-defined (no standard "interface" type).

**Schema form:** JSON. Compact, machine-friendly.

**Sample snippet:**
```json
{
  "components": [
    {
      "ref": "U1",
      "value": "STM32F103C8",
      "footprint": "LQFP48",
      "position": {"x": 10.5, "y": 20.0}
    }
  ],
  "nets": [
    {
      "name": "VCC",
      "connections": ["U1.VCC", "C1.1", "R1.1"]
    }
  ]
}
```

**AI Suitability:** Moderate. JSON is agent-friendly, and hierarchical blocks are a start. But no type system for interfaces, no performance specs, no decision rationale.

---

### 6. **EAGLE XML / Altium ASCII** (Proprietary but Documented)
**Scope:** Vendor-specific interchange. EAGLE (now Fusion 360) exports XML; Altium outputs ASCII schematic/PCB.  
**Top-level entities:**
- Components, nets, wires, traces
- Design rules, layer stack-up
- Libraries and symbol mappings

**Granularity:** Physical truth. Rich in tool-specific metadata (Altium: design primitives, net classes, routing rules).

**Schema form:** XML (EAGLE) / ASCII (Altium). Well-documented but vendor-locked.

**AI Suitability:** Low. Vendor-specific, bloated with tool metadata. Not suitable for agent interchange.

---

### 7. **Eurocircuits ECAD-API JSON**
**Scope:** Eurocircuits fab service JSON API. Publicly documented.  
**Top-level entities:**
- PCB specification (layers, stackup, material, finish)
- Components (position, rotation, BOM reference)
- Gerber/drill file references

**Granularity:** Fab order metadata. Bridges design → fab house.

**Schema form:** JSON. Compact, service-oriented.

**AI Suitability:** Low. Fab-order focused, not design-phase. Different use case than agent handoff.

---

## Comparison Table

| Format | Scope | Entity Type | Design Intent? | AI-Friendly? | Notes |
|--------|-------|-------------|----------------|--------------|-------|
| **IPC-2581** | ECAD↔Fab | Geometry, assembly | No | Low (verbose XML) | Modern standard, fab-safe |
| **ODB++** | Fab shipping | Physical truth | No | Very Low (binary) | Legacy, still used in fab shops |
| **EDIF** | Schematic | Nets, logic symbols | No | Very Low (dated) | 1990s standard, irrelevant today |
| **KiCad native** | Full design | Schematic + PCB | No | Medium (files, not contracts) | De-facto standard, well-documented |
| **CircuitJSON** | Layout ± hierarchy | Nets, blocks | Partial | Medium (JSON, weak semantics) | Emerging, incomplete spec |
| **EAGLE XML** | Full design | Tool-specific | No | Low (vendor-locked) | Proprietary, bloated |
| **Altium ASCII** | Full design | Tool-specific | No | Low (vendor-locked) | Proprietary, underdocumented |
| **Eurocircuits JSON** | Fab order | Order metadata | No | Low (service-oriented) | Fab house API, not design phase |

---

## What's Missing in Every Format

All existing interchange specs assume a **waterfall model:**
1. Engineer designs (schematic + PCB).
2. Fab file is exported (gerbers, BOM, assembly).
3. Fab house consumes it.

**None** model the intermediate layer where an AI agent:
- Tracks **design intent** (subsystem abstraction, performance goals, tradeoffs)
- Carries **decision history** (why this part? what alternatives were rejected?)
- Validates **interface semantics** (this power rail feeds these loads; this SPI bus has these targets)
- Preserves **spec lineage** (which datasheet specs flowed into which design decisions?)

---

## `ResearchBundle` and `FabBundle` Architecture

### ResearchBundle (Researcher → PCB Designer)
**What it carries:**
- Subsystem picks (locked MPN, package, actuals extracted from datasheets)
- Typed interfaces (power/signal/data, with voltage, current, protocol)
- Port bindings (how subsystem ports connect to interfaces)
- Build settings (vendor, assembly method, qty)
- Baseline git tag (proof of gate pass)

**Altitude:** One level above physical. Speaks in "subsystems" and "interfaces", not "nets" and "footprints".

**No existing standard aligns to this.**

### FabBundle (PCB Designer → Fab)
**What it carries:**
- Paths to KiCad files (schematic, PCB)
- Paths to fab outputs (gerbers, BOM CSV, CPL CSV)
- Gate booleans (ERC clean, DRC clean, vendor OK, stock verified)
- Consumption proof (which research baseline was input)
- Rev letter and git tag (immutable audit trail)

**Altitude:** Mixed. References physical (KiCad) but gates are semantic (ERC, DRC, vendor validation).

**Partial alignment to IPC-2581**, but our FabBundle is richer in audit metadata and gate semantics.

---

## Recommendation

### 1. Keep ResearchBundle as-is
No existing format models design intent at this altitude. It's a **new layer**, not a mapping of old specs.

### 2. Keep FabBundle mostly as-is
`FabBundle` is an audit wrapper. At the physical boundary (kicad_pcb/kicad_sch files), KiCad's native format is canonical. When exporting fab files, reference IPC-2581 compliance if needed:
- Gerbers/drill: already IPC-2581 compatible (standard fab output)
- BOM CSV: structured; can cross-reference IPC-2581 component UUIDs if needed
- Assembly data (CPL): similar to IPC-2581 placement data

### 3. If future integration with fab APIs is needed
- **For JLCPCB / PCBWay:** Use their documented JSON order APIs (not a standard, but service-specific).
- **For Eurocircuits:** Map `FabBundle` fields to their ECAD-API JSON on export.
- **For generic fab houses:** Export to IPC-2581 XML alongside KiCad files. But don't force `FabBundle` into IPC-2581 schema; keep our contracts clean.

### 4. Document the layer model
Add a README in `docs/architecture/` explaining:
- **Layer 0 (AI design intent):** ResearchBundle + FabBundle (our contracts)
- **Layer 1 (EDA truth):** KiCad files (.kicad_sch, .kicad_pcb, .kicad_pro)
- **Layer 2 (Fab interchange):** IPC-2581, vendor JSON APIs, gerbers/BOM/CPL
- **Why separate:** each layer has different consumers (agents, tools, fab houses) with different needs

---

## References

- **IPC-2581:** https://www.ipc.org/working-groups/2581-dpmx-digital-product-model-exchange
- **ODB++ (IPC-5411):** https://www.ipc.org/product/ipc-5411-outline-bit-stream-format-specification
- **EDIF (ISO 14629):** https://en.wikipedia.org/wiki/Electronic_Design_Interchange_Format
- **KiCad Native Formats:** https://docs.kicad.org/latest/en/file_formats/index.html
- **CircuitJSON:** https://github.com/ands/open-json-hardware (incomplete)
- **EAGLE XML:** https://www.autodesk.com/products/fusion-360/eagle (documentation in Fusion)
- **Eurocircuits ECAD-API:** https://www.eurocircuits.com/pcb-api/ (publicly documented)

---

## Conclusion

**ResearchBundle** and **FabBundle** are not reimplementations of existing specs; they're a new altitude layer for AI agents, sitting **above** all physical interchange formats. Our contracts are fit for purpose. Keep them, document the layer model, and integrate with industry formats (IPC-2581, vendor APIs) only at the boundaries where other tools and services consume the output.
