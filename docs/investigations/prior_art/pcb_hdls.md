# Prior Art: PCB Hardware Description Languages & DSLs

Investigation of modern HDLs/DSLs for PCB design to understand how the field models subsystems, interfaces, and part selection before committing our Pydantic contract.

## 1. atopile (.ato DSL)

**Repository**: [atopile/atopile](https://github.com/atopile/atopile)  
**Language**: Python-inspired declarative DSL (`.ato` files)

### Syntax Example

```ato
from "atopile/power/voltage-regulators/AMS1117.ato" import AMS1117_3V3
from "atopile/interfaces.ato" import Power, Signal

module BuckRegulator:
    input = new Power(voltage_v=5.0)
    output = new Power(voltage_v=3.3)
    gnd = new Power(voltage_v=0.0)
    
    reg = new AMS1117_3V3
    cin = new Capacitor(capacitance=10uF, voltage_v=10.0)
    cout = new Capacitor(capacitance=10uF, voltage_v=10.0)
    
    input.vcc ~ reg.vin
    output.vcc ~ reg.vout
    gnd.gnd ~ reg.gnd
    
    cin.p1 ~ input.vcc
    cin.p2 ~ reg.gnd
    cout.p1 ~ output.vcc
    cout.p2 ~ reg.gnd
```

### How They Model Subsystems
- **Module**: encapsulates components and their connections. Modules are composable; you `new` a module instance and wire its ports.
- **Component instantiation**: part selection via imports (`from "path.ato" import ComponentName`). Each import carries MPN/package metadata baked into the `.ato` library.
- **Port definition**: explicit `new Power(voltage_v=...)` or `new Signal(...)` creates typed interfaces.

### How They Model Interfaces
- **Interface**: explicit type system. `Power`, `Signal`, `Electrical` are standard interface types.
- **Connection operator**: `~` wires two interfaces of the same type. Automatic type checking prevents mismatches.
- **Bus support**: `~>` operator for bridge/series connections when enabled.

### Part Selection
- Parts are pre-defined in `.ato` library modules with MPN, package, datasheet baked in.
- No inline MPN override; parts are selected at library design time, not board design time.
- Part parameters (voltage rating, capacitance, resistance) are declared as attributes on the module.

### Derived/Computed Fields
- **Units and tolerances**: inline via `10uF`, `10.0` literals. atopile automatically solves constraint systems to verify that selected part ratings exceed board requirements.
- **Assertions**: `assert output.voltage >= 3.0` validates design margins.
- No explicit trace-width calculation; that's delegated to layout phase.

### Primitives We Lack
- **Constraint solver**: atopile has a declarative constraint system ("auto-solves systems of constraints for you with free variables"). Our Pydantic model is strictly imperative.
- **Type-safe interface matching**: we use a `protocol` string; they use structured interface types.
- **Built-in units/tolerances**: we store raw floats; they store `10uF` with dimensional analysis.

### Primitives We Have That They Don't
- **Post-BOM port bindings**: we track `port_bindings` to map load pins to subsystem ports after part selection. atopile's library coupling bakes this in earlier.
- **Subsystem category taxonomy**: we use `category` enum (buck_converter, ldo, motor_driver, etc.) for templated validation. atopile treats all modules uniformly.
- **Explicit interface list**: we decouple interfaces from subsystems (separate `Interface` objects); atopile couples them (ports defined inline in module).

---

## 2. JITX (Stanza)

**Language**: Stanza (functional, compiled)  
**Resources**: [JITX Cookbook](https://github.com/JITx-Inc/jitx-cookbook)

### Syntax Example

```stanza
defn make-power-header (n:Int):
  inst j : pin-header(n)
  net VIN (j.p[1])
  net GND (j.p[2])

pcb-module led-circuit:
  inst ledx : led-0805()
  inst r1 : chip-resistor(220.0)
  net VCC (r1.p[1])
  net LED-OUT (r1.p[2] ledx.p[1])
  net GND (ledx.p[2])
  
  require vcc : power
  require gnd : power
  
  net VCC (vcc.vcc)
  net GND (gnd.gnd)
```

### How They Model Subsystems
- **pcb-module**: a circuit composed of `inst` statements (component/submodule instances) and `net` statements (connections).
- **Hierarchical design**: modules can contain sub-modules; instances can be instantiated from other modules or from PCB components.
- **Parametric modules**: `defn` allows module factories (e.g., `make-power-header(n:Int)` generates N pins).

### How They Model Interfaces
- **Port concept**: `require` statements define required ports (power, signal, physical).
- **Net-based**: connections are named nets. Two pins on the same net are connected.
- **Implicit type matching**: no strong typing; nets infer type from context.

### Part Selection
- Components are referenced by name + optional parameters (e.g., `led-0805()`, `chip-resistor(220.0)`).
- Resolution happens via a symbol/footprint library (open-components-database).
- MPN selected by library lookup; not explicit in the code.

### Derived/Computed Fields
- **Resistor value**: passed as parameter to `chip-resistor(220.0)`. No automatic derivation from load requirements.
- **No constraint solver**: purely imperative. Engineers must compute trace width, inductor values, etc. offline and pass as parameters.

### Primitives We Lack
- **Parametric factories**: our SubsystemPick is a single decision point. JITX lets you parameterize modules to generate variants.
- **Geometry-first design**: JITX embeds board physical layout (geom statements, copper pours) in the same language. We defer to KiCad/PCB phase.

### Primitives We Have That They Don't
- **Explicit part validation**: our `analyze_candidate` runs check pipeline against actuals (Tj, Pdiss, ripple, stock). JITX has no built-in validation.
- **BOM lineage**: we track `rejected` candidates and `tradeoffs`. JITX's design history lives in version control, not in the design artifact itself.
- **Interface decoupling**: we separate `Interface` objects from subsystems; JITX couples them as nets inside modules.

---

## 3. SKiDL (Python)

**Repository**: [devbisme/skidl](https://github.com/devbisme/skidl)  
**Language**: Python

### Syntax Example

```python
from skidl import *

# Define nets for power and signals
vin, vout, gnd = Net('VIN'), Net('VOUT'), Net('GND')

# Create resistors with templates
r_template = Part("Device", "R", dest=TEMPLATE, 
                  footprint='Resistor_SMD:R_0603_1608Metric')
r1, r2 = r_template(value='1K'), r_template(value='2K')

# Create LED
led = Part("Device", "LED", dest=TEMPLATE,
           footprint='LED_SMD:LED_0603_1608Metric')
d1 = led(value='red-led')

# Connect pins to nets using += operator
vin += r1[1]          # r1 pin 1 to VIN
vout += r1[2], r2[1]  # r1 pin 2 and r2 pin 1 to VOUT
gnd += r2[2], d1[2]   # r2 pin 2 and LED cathode to GND

generate_netlist(tool=KICAD9)
```

### How They Model Subsystems
- **Hierarchical circuits**: no formal "subsystem" type; use Python functions or classes to encapsulate reusable circuits.
- **Part-centric**: circuits are built by instantiating Parts and wiring their pins. No intermediate module abstraction.

### How They Model Interfaces
- **Net**: a collection of pins that are electrically connected. Wiring is done via `+=` operator.
- **Weak typing**: any pin can connect to any net; type errors detected at netlist validation time, not code time.
- **Pin references**: numeric indexing (`r1[1]`, `r1[2]`) or named pins if available.

### Part Selection
- Part lookup by library + name: `Part("Device", "R")`.
- MPN/footprint can be specified inline or via library templates.
- No constraint-based part selection; manual MPN assignment.

### Derived/Computed Fields
- **None**: purely data-flow. Engineers assign resistor values, capacitance directly.
- No automatic trace-width or thermal calculations.

### Primitives We Lack
- **Implicit connection semantics**: SKiDL's `+=` is intuitive for engineers. Our ResearchBundle uses explicit `Interface` objects; less discoverable for manual authoring.

### Primitives We Have That They Don't
- **Subsystem category taxonomy**: SKiDL has no concept of a "buck converter" vs "LDO" subsystem type with templated checks.
- **Post-selection actuals merging**: we track `actuals` (datasheet specs) separately from requirements, then merge on part choice. SKiDL has no parallel.
- **Decision history**: we log rejected candidates and rationales; SKiDL has no audit trail in the design artifact.

---

## 4. tscircuit (TypeScript/Circuit JSON)

**Repository**: [tscircuit/circuit-json](https://github.com/tscircuit/circuit-json)  
**Format**: Circuit JSON (low-level IR)  
**Higher-level**: [tscircuit/core](https://github.com/tscircuit/core) (TypeScript/React components)

### Syntax Example (TypeScript)

```typescript
import { Board, Resistor, LED, Trace } from 'tscircuit'

const board = new Board()

const r1 = new Resistor(board, { value: 1000, name: 'R1' })
const r2 = new Resistor(board, { value: 2000, name: 'R2' })
const led = new LED(board, { name: 'D1' })

// Port-based connections
board.addTrace('VIN', r1.pin1)
board.addTrace('VOUT', [r1.pin2, r2.pin1])
board.addTrace('GND', [r2.pin2, led.cathode])

export const circuit = board.compile()  // Outputs Circuit JSON
```

### Circuit JSON Structure (IR)

```json
[
  {
    "type": "source_component",
    "source_component_id": "R1",
    "ftype": "resistor",
    "resistance": 1000,
    "name": "R1"
  },
  {
    "type": "source_port",
    "source_port_id": "R1_p1",
    "source_component_id": "R1",
    "name": "pin1"
  },
  {
    "type": "source_port",
    "source_port_id": "R1_p2",
    "source_component_id": "R1",
    "name": "pin2"
  },
  {
    "type": "source_trace",
    "source_trace_id": "trace1",
    "connected_source_port_ids": ["R1_p1", "power_vcc_port"]
  }
]
```

### How They Model Subsystems
- **Components**: top-level concept; no explicit subsystem hierarchy beyond nested Board instances.
- **Circuit JSON elements**: prefix-based organization (`source_`, `schematic_`, `pcb_`) separates semantic contexts but same IR.

### How They Model Interfaces
- **Port**: a named connection point on a component. Identified by source_port_id.
- **Trace**: connects ports across the circuit. IDs reference ports; automatic net inference.
- **Name-based**: traces grouped by shared source_port_ids form implicit nets.

### Part Selection
- Components declared with type + parameters (resistance, voltage, name).
- No explicit MPN/package in Circuit JSON core; EasyEDA linkage adds that.
- Low-level IR format; not meant for part selection logic.

### Derived/Computed Fields
- **None in the IR**: Circuit JSON is a lowest common denominator. Derivation happens in the TypeScript layer before compilation.
- Tools like `tscircuit-dsn-converter` post-process to add trace widths, layer assignments for Gerber export.

### Primitives We Lack
- **Prefix-based context separation**: Circuit JSON uses `source_`, `schematic_`, `pcb_` to avoid semantic overload. Our model conflates all three.
- **JSON IR interchange**: we use Pydantic; Circuit JSON is a standard that other tools can consume.

### Primitives We Have That They Don't
- **Subsystem validation pipeline**: Circuit JSON has no type-checking or constraint solver. We validate against subsystem templates.
- **Part-to-footprint bidirectional binding**: we track `package` + `mpn`; Circuit JSON assumes that comes from EasyEDA/external lookup.

---

## 5. KiCad Python API (IPC)

**Documentation**: [KiCad IPC API](https://dev-docs.kicad.org/en/apis-and-binding/pcbnew/index.html)  
**Modern Approach**: Python bindings to KiCad's IPC server (replaces deprecated SWIG)

### Usage Pattern (IPC-based)

```python
import os
from kicad_ipc import KiCadClient

client = KiCadClient()

# Fetch board state
board = client.GetBoard()

# Iterate footprints
for footprint in board.GetFootprints():
    print(f"{footprint.GetReference()} at ({footprint.GetX()}, {footprint.GetY()})")

# Modify netlist (sync from schematic)
client.SyncNetlist("path/to/root.kicad_sch")
```

### How They Model Subsystems
- **Schematic sheet hierarchy**: modules are drawn as hierarchical sheets (`.kicad_sch` files with sub-sheet references).
- **Netlist-centric**: no explicit subsystem class; design intent lives in schematic topology + netlist export.

### How They Model Interfaces
- **Net**: fundamental unit. Pins belong to nets; nets are identified by name strings or IDs.
- **Sheet pins**: define port boundaries for hierarchical designs.
- **No type system**: nets are untyped; ERC checks catch mismatches after-the-fact.

### Part Selection
- **Schematic symbol**: has Footprint, MPN, Value properties (user-filled).
- **KiCad symbol library**: binds symbol to schematic appearance; separate PCB footprint library.
- **No parametric selection logic**: values/MPNs are manual annotations.

### Derived/Computed Fields
- **Designator/Reference**: auto-assigned by KiCad.
- **Netlist generation**: KiCad exports nets to `.net` file; external tools post-process.
- **No built-in constraint solver**.

### Primitives We Lack
- **Live IPC mutation**: KiCad's IPC allows real-time edits visible in the GUI. Our Pydantic model is file-based snapshots.

### Primitives We Have That They Don't
- **Validation + decision history**: KiCad is a tool, not a design language. We track part selection rationale and validation results in the artifact.

---

## 6. Magic VLSI / OpenROAD (Silicon-Level, Transferable Concepts)

**Scope**: Chip-level design; not directly PCB, but instructive patterns.

### Concepts
- **Hierarchy**: modules decompose into sub-modules recursively. Each module has an interface (input/output ports).
- **Instance semantics**: each instance names its type + supplies parameter values.
- **Geometric primacy**: physical layout constraints (routing, timing) are first-class.
- **No part library**: cells are pre-designed; no component searching. Closest analogy is IP blocks.

### Transferable Patterns
- **Clear separation of abstraction levels** (logic, physical, electrical). Our ResearchBundle mixes subsystem selection + interface routing; they are separated until place-and-route.

---

## Comparative Summary Table

| Aspect | atopile | JITX | SKiDL | tscircuit | KiCad API | Our Model |
|--------|---------|------|-------|-----------|-----------|-----------|
| **Language** | `.ato` DSL | Stanza | Python | TypeScript | Python IPC | Pydantic |
| **Subsystem Model** | Module (typed) | pcb-module | Function/Class | Component | Schematic Sheet | SubsystemPick + Interface |
| **Interface Typing** | Strong (Power, Signal) | Weak (nets) | Weak (nets) | Weak (port IDs) | Untyped | Enum (power, signal, data) |
| **Part Selection** | Library-baked | Library lookup | Manual | Parameter-based | Manual annotation | Template validation |
| **Constraint Solver** | Yes | No | No | No | No | No |
| **Validation** | Yes (tolerances) | ERC only | ERC only | ERC + DRC | ERC + DRC | Custom checks |
| **Decision History** | No | No | No | No | No | Yes (rejected, tradeoffs) |
| **Geometry** | Layout phase | In language | External | Post-IR | KiCad native | External (Phase 4) |
| **Interchange Format** | `.ato` files | `.stanza` | Netlist | Circuit JSON | `.kicad_sch` | Pydantic JSON |

---

## Recommendation

**Our 2-Pydantic-model contract is appropriate for a 2-agent AI harness and should NOT adopt patterns wholesale from any single prior art.**

### Rationale

1. **Different problem space**: atopile, JITX, SKiDL, and tscircuit are designed for **human-authored design capture** with IDE support, reusable libraries, and linting. Our ResearchBundle is an **AI-generated artifact** that must:
   - Track part validation against load constraints (compare to atopile's constraint solver, but post-hoc).
   - Preserve decision rationale (rejected candidates, tradeoffs) for audit and refinement.
   - Decouple subsystems from interfaces to enable independent optimization (agent 1 picks parts; agent 2 routes nets).

2. **Subsystem category taxonomy is our differentiator**: atopile treats all modules uniformly. JITX requires manual library registration. We use a `category` enum (buck_converter, ldo, motor_driver, dac_amp) to enable templated validation, question-generation, and part-selection strategies. This is **not a limitation**—it's a **design choice suited to AI-driven intake**.

3. **Post-selection port bindings are critical**: our `port_bindings` dict in SubsystemPick maps subsystem output ports to load pin names *after* part choice. atopile and JITX bake port names at library definition time. This late binding allows our Phase 2 agent (designer) to pick a buck converter without knowing which MCU pins it will serve until Phase 3 (after MCU is chosen).

4. **Pydantic + JSON interchange is sufficient**: we don't need Circuit JSON's complexity (prefix-based context separation) or Stanza's parametric factories. Pydantic's type checking + JSON export gives us enough structure to:
   - Validate against subsystem templates.
   - Export to KiCad schematic via designer-mcp's `add_ic`, `add_wire` API.
   - Merge datasheet actuals post-research.
   - Audit decision history.

### What to Borrow

- **From atopile**: idea of interface typing (Power, Signal, Electrical). We currently use string enums; consider structured interface types in Phase 4 (post-MVP).
- **From JITX**: parametric module factories for variants (e.g., dual-buck vs single). Defer to Phase 4.
- **From tscircuit**: the idea of a low-level IR (Circuit JSON) as an interchange format between schematic and PCB tools. Not needed until Phase 4 autorouting.
- **From KiCad IPC**: live mutation patterns (agent places footprints, user sees immediately). Already in use via live-edit-mcp; proven ergonomic.

### Conclusion

Lock the current Pydantic contract. It solves the "subsystem intake + interface routing" problem cleanly for an AI-driven workflow. Revisit interchange format (Circuit JSON compatibility) only when Phase 4 (autorouting, Gerber export) requires cross-tool data flow. No lock-in risk: all tools read/write JSON; migration cost is low.

---

## Sources

- [atopile](https://docs.atopile.io) — constraint-based PCB design DSL
- [JITX Cookbook](https://github.com/JITx-Inc/jitx-cookbook) — Stanza-based PCB reference designs
- [SKiDL](https://github.com/devbisme/skidl) — Python netlist DSL for KiCad
- [tscircuit](https://tscircuit.com/) — TypeScript/React PCB design; Circuit JSON IR
- [Circuit JSON](https://github.com/tscircuit/circuit-json) — low-level circuit interchange format
- [KiCad IPC API](https://dev-docs.kicad.org/en/apis-and-binding/pcbnew/index.html) — modern Python bindings
- [HDL Review Paper](https://arxiv.org/abs/2011.08242) — opportunities and challenges in PCB-level HDLs
