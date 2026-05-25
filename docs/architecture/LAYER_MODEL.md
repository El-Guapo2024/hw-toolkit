# PCB Design Layer Model: AI Agents + Industry Formats

This document clarifies the altitude of our contracts and how they sit relative to existing PCB interchange standards.

## Layer Stack (Bottom → Top)

### Layer 2: Fab House Interchange (Output)
**Consumers:** PCB manufacturers (JLCPCB, PCBWay, Eurocircuits, etc.)  
**Formats:** IPC-2581 XML, ODB++, gerbers/drill files, BOM CSV, CPL CSV, vendor JSON APIs  
**What it says:** "Here are the manufacturing files, stackup, placement coordinates, and lead times."  
**Semantics:** Physical truth only. No design rationale.

**Examples:**
- `gerbers/` — IPC-2581 compatible layer files + drill data
- `bom.csv` — Bill of materials with LCSC codes, rotation, placement
- Vendor JSON (Eurocircuits API) — PCB spec + component placement

---

### Layer 1: EDA / Design Truth (Canonical)
**Consumers:** KiCad (pcbnew, eeschema), other EDA tools on import  
**Formats:** KiCad native (.kicad_sch, .kicad_pcb, .kicad_pro), EDIF, EAGLE XML  
**What it says:** "Here are the nets, the physical layout, the design rules, the symbol-to-footprint bindings."  
**Semantics:** EDA truth. No design intent, but machine-parse-able by tools.

**Examples:**
- `.kicad_sch` — Hierarchical schematic with net labels, sheet references, properties
- `.kicad_pcb` — Footprints, traces, vias, zones, 3D models, DRC rules
- `.kicad_pro` — Project metadata, layer stack, library links

**Property of Layer 1:**
- Produced by humans (or agents) via EDA editing
- Consumed by EDA tools and by Layer 0 (agents) during refinement
- **Immutable once `FabBundle` is locked**

---

### Layer 0: AI Design Intent (Our Innovation)
**Consumers:** Hardware design agents (researcher, pcb-designer, router)  
**Formats:** Our pydantic contracts (`ResearchBundle`, `FabBundle`)  
**What it says:** "Here are the subsystems I picked, why I picked them, what interfaces connect them, and what gates passed."  
**Semantics:** Design intent. Agent-to-agent handoff with auditability.

**Examples:**
- `ResearchBundle.subsystems[0]` — STM32H743 LQFP100, 200nF decap, "chose for DMA+CAN"
- `ResearchBundle.interfaces[0]` — 5V rail, 10A continuous, powers motor driver + sensors
- `FabBundle.locked_at` — timestamp + git tag proving ERC/DRC/vendor checks passed

**Property of Layer 0:**
- Produced by agents as they design
- Consumed by downstream agents and human engineers
- Carries decision history, spec lineage, and gate-pass proof
- **No existing standard models this layer**

---

## Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│ Fab House (JLCPCB, PCBWay, Eurocircuits, etc.)                  │
│ Consumes: IPC-2581, ODB++, gerbers, BOM CSV, CPL CSV            │
│ Produces: Manufactured PCB + assembly                           │
└─────────────────────────────────────────────────────────────────┘
                           ↑
                    [FabBundle export]
                           ↑
┌─────────────────────────────────────────────────────────────────┐
│ Layer 2 Output Files                                             │
│  • docs/projects/{pid}/fab/{rev}/gerbers/                       │
│  • docs/projects/{pid}/fab/{rev}/bom.csv                        │
│  • docs/projects/{pid}/fab/{rev}/cpl.csv                        │
└─────────────────────────────────────────────────────────────────┘
                           ↑
                  [kicad-cli export]
                           ↑
┌─────────────────────────────────────────────────────────────────┐
│ Layer 1: EDA Truth (KiCad files)                                │
│  • .kicad_sch (schematic with properties)                       │
│  • .kicad_pcb (layout, footprints, traces, zones)               │
│  • .kicad_pro (project config, rules, libs)                    │
│  Edited by: Human + agents via live-edit-mcp / designer-mcp     │
└─────────────────────────────────────────────────────────────────┘
                           ↑
                  [layout & routing by pcb-designer agent]
                           ↑
┌─────────────────────────────────────────────────────────────────┐
│ Layer 0: AI Design Intent (ResearchBundle consumed here)        │
│  • subsystems: [MPN, package, actuals, bindings]                │
│  • interfaces: [typed connections with voltage/current/proto]   │
│  • gates: [erc_clean, drc_clean, vendor_ok, stock_ok]          │
│  • audit: [git tags, locked_at, consumed_research_tag]         │
└─────────────────────────────────────────────────────────────────┘
                           ↑
                   [ResearchBundle from researcher agent]
                           ↑
┌─────────────────────────────────────────────────────────────────┐
│ Researcher Agent (part selection, spec extraction)              │
│ Consumes: Design spec, BOM templates, datasheet queries         │
│ Produces: ResearchBundle (locked subsystems + interfaces)       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Why Three Layers?

### Why not just one (EDA truth)?
- EDA files (KiCad) don't carry design intent, decision rationale, or performance specs
- Agents need to reason about subsystems + interfaces, not wires + nets
- A human can't read a .kicad_sch and understand "why was this cap value chosen?" or "which loads does this 5V rail serve?"

### Why not collapse into two (Layer 0 only)?
- Fab houses don't understand "subsystems" or "interfaces"
- EDA tools (KiCad, SPICE, PCB layout software) need physical truth (Layer 1)
- Separation of concerns: agents reason at Layer 0; EDA tools operate at Layer 1; fab houses consume Layer 2

### Why not adopt IPC-2581 or CircuitJSON?
- They model Layer 1 + 2 (physical truth + fab), not Layer 0 (design intent)
- IPC-2581 has no "interface" type, no "subsystem" concept, no performance specs
- Our `ResearchBundle` is information-denser and agent-optimized. Forcing it into IPC-2581 would lose semantic richness (interface types, voltage nominal, protocol, current specs)
- We *can* export to IPC-2581 at the Layer 2 boundary if needed, but our contract shouldn't conform to it

---

## Immutability Boundaries

| Layer | Mutable When? | Frozen When? | Producer | Consumer |
|-------|---------------|--------------|----------|----------|
| Layer 2 (Fab) | Never | At FabBundle lock | kicad-cli export | Fab house |
| Layer 1 (EDA) | During design | FabBundle lock | pcb-designer agent | KiCad, ERC/DRC checker |
| Layer 0 (Intent) | During research | ResearchBundle lock | researcher agent | pcb-designer agent |

---

## Integration Points

### Layer 0 → 1
- Researcher produces `ResearchBundle`
- pcb-designer consumes it, creates `system.kicad_sch` + empty `system.kicad_pcb`
- pcb-designer places footprints, routes, verifies ERC/DRC
- Result: `FabBundle` ready at Layer 1

### Layer 1 → 2
- kicad-cli exports schematic → SVG, PCB → PNG, gerbers, drill, BOM
- BOM aggregated from footprint properties (MPN, LCSC, qty)
- CPL generated from footprint position + rotation data
- Result: fab-ready files + Layer 2 exports

### Layer 1 ↔ Fab House (optional IPC-2581)
- If fab house requires formal IPC-2581, generate it from .kicad_pcb + gerbers
- Not required; most fab houses accept gerbers + CSV BOM + CPL
- Only generate if vendor explicitly requests it

---

## Related Files

- `/hw_agent/core/research_bundle.py` — Layer 0 contract (researcher output)
- `/hw_agent/core/fab_bundle.py` — Layer 0 contract (pcb-designer output, audit wrapper)
- `/docs/investigations/prior_art/interchange_formats.md` — Detailed format comparison + recommendation
