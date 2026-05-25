# Systems Engineering Formalisms: Prior Art Analysis

## Executive Summary

Investigated classical systems-engineering patterns (SysML, AUTOSAR, N2 matrices, ICDs) to inform the `ResearchBundle` contract for modeling hardware subsystems and typed interfaces. Found strong structural alignment with existing Pydantic models, plus three actionable recommendations for evolving the design.

---

## 1. SysML v2 (Systems Modeling Language)

### Core Primitives

**Part** (subsystem)
- Named, uniquely identified element representing a physical or functional component
- Owns a set of **ports** (not inherited; each part defines its own)
- Properties and usage context (e.g. quantity, mass, cost)

**Port**
- Typed endpoint on a Part; direction semantics (in/out/in-out)
- Each port references an **Interface Definition** (the *contract*)
- Port identity is local to its Part (e.g. `motor_driver.VOUT`)

**Interface Definition** (InterfaceDef)
- Reusable specification: signal names, voltage ranges, protocol (I²C @ 400kHz, 3.3V)
- Decoupled from any specific usage—shared across multiple ports
- Metadata: units, tolerance, flow direction, requirements traceability

**Connection Definition** (ConnectorDef)
- Specifies how two InterfaceDefs connect (e.g. "I²C primary connects to I²C secondary")
- Named, reusable; can be instantiated many times across the design

### Example

```
InterfaceDef "I2C_400kHz_3v3":
  - protocol: I2C
  - frequency: 400 kHz
  - voltage: 3.3V (±10%)
  - pullup_resistors: 4.7kΩ

Part "IMU_MPU6050":
  Port "data":
    - direction: in
    - interfaces: I2C_400kHz_3v3

Part "MCU_STM32F4":
  Port "i2c_bus":
    - direction: out
    - interfaces: I2C_400kHz_3v3

Connection "imu_to_mcu":
  - from: IMU_MPU6050.data
  - to: MCU_STM32F4.i2c_bus
```

### Transfer to `ResearchBundle`

**Good fit:**
- Part ↔ SubsystemPick (id, category, mpn)
- Port ↔ port_bindings (name → interface id)
- Interface Definition ↔ extract reusable spec layer

**Not adopted:**
- Flow direction metadata on ports (omit for now; add if multi-party buses become complex)
- Graphical Vee model (not needed for data contract)

---

## 2. AUTOSAR (Automotive Software Architecture)

### Core Primitives

**Software Component (SWC)**
- Named, reusable unit with **ports** that declare what it *provides* or *requires*
- Encapsulates logic; internal structure hidden from integrators

**Port Semantics**
- **P-Port (Provided Port)**: "I supply this service/signal"
  - Example: `VoltageSensor.output_5v` *provides* 5V at 2A peak
  - Consumer responsibility to use correctly

- **R-Port (Required Port)**: "I depend on this service/signal"
  - Example: `MotorController.pwm_input` *requires* PWM @ 20kHz

**Interface Definition** (PortInterface)
- Specifies the contract: data types, units, protocol, timing
- Decoupled from SWC; multiple SWCs can share one PortInterface

**Connection** (Assembly)
- Maps P-Port → R-Port
- Validation: ports must match the same PortInterface
- One P can feed multiple R (power rails, common buses)

### Example

```
PortInterface "PWM_20khz_5v":
  - frequency: 20 kHz
  - voltage: 5V
  - duty_range: [0%, 100%]
  - max_load: 5 mA

SWC "PWMGenerator":
  P-Port "pwm_out": PWM_20khz_5v  # I provide this

SWC "MotorController":
  R-Port "pwm_in": PWM_20khz_5v   # I need this

Assembly "pwm_conn":
  P-Port: PWMGenerator.pwm_out
  R-Port: MotorController.pwm_in
```

### Key Insight: Provider-Consumer Asymmetry

A **5V power rail** has:
- 1 **provider** (buck converter): P-Port "5v_out"
- N **consumers** (LEDs, sensors, drivers): R-Port "5v_rail"

All consumers bind to the same Interface (e.g. `rail_5v`), but the semantics are clear:
- Provider sets spec (voltage tolerance, ripple, max current)
- Consumers must satisfy requirements (e.g. "draw <= 500 mA")

### Transfer to `ResearchBundle`

**Strong candidate for adoption:**
- Split Interface into **ProviderPort** vs **RequiredPort** (asymmetric directions)
- Power rails become explicit: `buck_5v.provides("5V_rail")` → N subsystems `.requires("5V_rail")`
- Signal buses (I²C, SPI): clarify clock provider vs data followers

**Blocker:**
- Current Interface model is symmetric (from_subsystem/to_subsystem)
- Would require restructuring; recommend post-MVP

---

## 3. Interface Control Document (ICD) & N2 Matrix

### ICD Definition

Canonical project-level artifact:
- **Purpose**: "record of all interface information (drawings, diagrams, tables)"
- **Scope**: inputs/outputs of subsystems + protocols (electrical levels, timing, logical structure)
- **Key property**: "describes only the interface, not the connecting systems" → enables independent team work

### N2 Matrix Structure

**Layout**: N × N matrix where N = number of subsystems/functions

| | MCU | Buck | LDO | Motor Driver |
|---|---|---|---|---|
| **MCU** | — | enable; 24V | status; 3.3V | gpio_pwm; 3.3V |
| **Buck** | n/a | — | input 24V | input_load feedback |
| **LDO** | n/a | n/a | — | n/a |
| **Motor Driver** | feedback ADC; 3.3V | n/a | vcc 3.3V | — |

**Interpretation:**
- Diagonal: subsystems
- Cell (i,j): "what flows from i → j" (data, power, signal)
- Blank: no interface
- Bidirectional interfaces appear in both (i,j) and (j,i)

### Transfer to `ResearchBundle`

**Immediate export target:**
- Generate N2 matrix from `ResearchBundle.interfaces` list
- Rows/columns from `SubsystemPick.id` list
- Each cell (i,j) populated with interfaces where `from_subsystem=i, to_subsystem=j`
- Artifact for user: human-readable interface audit

**ICD mapping:**
- `Interface.notes` field → partial ICD coverage (voltage, protocol, units)
- Recommend extending Interface with explicit `spec: InterfaceSpec` (reusable)

---

## 4. IP Reuse Methodology & Port Lists

### Chip IP Documentation Pattern

Typical IP block datasheet includes:
- **Port List**: Every pin + function + electrical spec
  ```
  VCC: 3.3V ±10%
  GND: 0V (reference)
  SDA_IN: I²C data, pull-up 4.7kΩ, Vin 0-3.6V
  SCL_IN: I²C clock, pull-up 4.7kΩ, Vin 0-3.6V
  INT_OUT: Active-high interrupt, 3.3V CMOS
  ```

- **Compliance Matrix**: Voltage, temperature, timing specs under corner conditions
- **Application Circuit**: Recommended decoupling, pullups, filtering
- **Behavioral Model**: Timing diagrams, state machines

### Transfer to `ResearchBundle`

**Currently covered:**
- SubsystemPick.actuals (free-form dict) captures electrical specs extracted from datasheets
- `Interface.voltage_nominal_v`, `current_continuous_max_a` cover simple power rails

**Gap:**
- No **InterfaceDefinition** table (reusable specs)
- Datasheet extraction is ad-hoc; no standard form per category

**Recommendation:**
- Create per-category template for InterfaceDefinition (e.g. "I2C@400kHz@3.3V")
- Subsystems reference (not inline) these definitions
- Decoupling patterns, pullup values part of Interface spec

---

## 5. Systems Engineering Lifecycle (INCOSE Pattern)

### Canonical Vee Model

```
ConOps → Requirements → Architecture → Design → Implementation
   ↓          ↓              ↓           ↓            ↓
User Validation ← Verification ← Integration ← Unit Test
```

**Artifacts at each stage:**
- **ConOps**: Operational scenarios, stakeholder needs
- **Requirements**: Functional + non-functional (power budget, EMC, cost)
- **Architecture**: Subsystems, interfaces, allocation (what goes where)
- **Verification**: Test plans, acceptance criteria per interface

### Hardware Systems Engineering Specialization

In hardware, the architecture stage produces:
- **Block diagram** (subsystems + labeled connections)
- **Interface definitions** (voltage, current, protocol for every link)
- **Port allocation matrix** (which subsystem owns which port)

### Transfer to `ResearchBundle`

**Strong alignment:**
- `SubsystemPick` represents architecture-level allocation (MCU, buck, LDO chosen)
- `Interface` list represents connection table
- Missing: explicit link back to requirements (e.g. "interface i2c_main satisfies req-007")
- Missing: verification matrix (test cases per interface)

**Recommendation:**
- Add optional `requirement_id` field to Interface (traceability)
- Export verification checklist per Interface (voltage tolerance tests, timing, load transients)

---

## Recommendations

### 1. Adopt Provider-Consumer Port Semantics (P-Port/R-Port Pattern)

**Status**: Post-MVP, high impact

**Rationale**:
- Power rails are fundamentally asymmetric: one source, many sinks
- AUTOSAR has proven this for 20 years in automotive production systems
- Current `from_subsystem/to_subsystem` conflates provider and consumer roles

**Implementation**:
- Extend Interface: add `port_role: Literal["provides", "requires"]` or split into ProviderPort/RequiredPort
- Validation: for power interfaces, exactly one provider, N receivers
- Benefit: explicit intent, catch wiring errors early (e.g. "two 5V providers conflict")

### 2. Introduce InterfaceDefinition Layer (Reusable Specs)

**Status**: Post-MVP, medium impact

**Rationale**:
- Current Interface inlines all spec details per instance (voltage, protocol, speed)
- Creates duplication: "every I²C connection re-states @400kHz @3.3V"
- SysML, AUTOSAR, and IP datasheets all use reusable interface definitions

**Implementation**:
```python
class InterfaceDefinition(BaseModel):
    """Reusable contract (e.g., 'I2C@400kHz@3.3V')"""
    id: str  # e.g., "i2c_standard_3v3"
    type: Literal["power", "signal", "data"]
    spec: dict  # {protocol: "i2c", frequency_hz: 400_000, voltage: 3.3, ...}

class Interface(BaseModel):
    id: str
    definition_id: str  # References InterfaceDefinition
    from_subsystem: str
    from_port: str
    to_subsystem: str
    to_port: str
    # Instance-level overrides (optional)
    derate: float = 1.0  # e.g., use @ 80% rated current
```

**Benefit**: Single source of truth; easier to audit and propagate spec changes

### 3. Export N2 Matrix Artifact for User Inspection

**Status**: MVP, low cost

**Rationale**:
- N2 matrix is NASA/aerospace gold standard for interface audit
- Provides human-readable overview of all subsystem connections
- Catches wiring gaps and disconnects early

**Implementation**:
- Tool: generate N2 from ResearchBundle.interfaces list
- Output: CSV or Markdown table
- Artifact path: `<project>/interfaces_n2_matrix.csv`
- User benefit: interface validation before PCB layout

---

## Conclusion

Our `ResearchBundle` contract is **conceptually sound** and aligns with 40+ years of systems-engineering formalism. The three recommendations are:

1. **P-Port/R-Port semantics** for power rails (clarity + error detection)
2. **InterfaceDefinition** layer (reduce duplication, improve maintainability)
3. **N2 matrix export** (human-readable audit artifact)

Items 2 and 3 are low-friction additions; item 1 requires schema migration but is high-value for electrical subsystems (where provider-consumer is fundamental).

---

## References

- **SysML v2.0 Beta**: https://www.omg.org/spec/SysML/2.0/Beta1 (Part, Port, Interface Definition concepts)
- **AUTOSAR**: Software Component Architecture; P-port/R-port pattern well-documented in automotive specs
- **N2 Matrix**: Invented by Robert J. Lano (TRW, 1970s); NASA systems engineering standard
- **Interface Control Document (ICD)**: Foundational aerospace/defense pattern; standardized across DoD, ESA programs
- **Component-Based Software Engineering (CBSE)**: UML component model (lollipop/socket notation) for provider-consumer interfaces
- **INCOSE Systems Engineering Handbook**: Vee model lifecycle; architecture ↔ verification linking
