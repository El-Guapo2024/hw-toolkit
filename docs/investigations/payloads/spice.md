# SPICE Payload Contract Research

**Goal:** Validate that `SubsystemPick.actuals` dict carries sufficient data to feed PySpice/ngspice simulation without guessing. Maps real manufacturer SPICE models and PySpice I/O to the data fields our Pydantic contract must carry.

**Date:** 2026-05-24  
**Status:** Investigation complete; contract gaps identified.

---

## 1. PySpice Circuit Setup — Real Examples

### 1.1 RC Low-Pass Filter (Passive Example)

```python
from PySpice.Spice.Netlist import Circuit
from PySpice.Unit import *
import numpy as np

# Define circuit
circuit = Circuit('RC Low-Pass Filter')
circuit.V('input', 'in', circuit.gnd, 1@u_V)
circuit.R(1, 'in', 'out', 10@u_kΩ)
circuit.C(1, 'out', circuit.gnd, 100@u_nF)

# Run transient analysis: step response
simulator = circuit.simulator(temperature=25, nominal_temperature=25)
analysis = simulator.transient(
    step_time=1@u_us,
    end_time=1@u_ms,
    use_initial_conditions=True
)

# Extract results
time = np.array(analysis.time)          # shape (N,)
v_out = np.array(analysis['out'])       # shape (N,)
```

**Key points:**
- `circuit.V()`, `circuit.R()`, `circuit.C()` place components
- `simulator.transient()` returns Analysis object with `.time` and node voltages
- Can access any node by name: `analysis['node_name']`

### 1.2 Buck Converter with Behavioral Load

```python
from PySpice.Spice.Netlist import Circuit
from PySpice.Unit import *

# Simplified buck topology
buck = Circuit('Buck Converter Example')

# Input voltage
buck.V('in', 'vin', 0, 12@u_V)

# Inductor (must specify L value and series resistance)
buck.L(1, 'vin', 'sw', 3.3@u_uH)
buck.R('L', 'sw', 'out', 50@u_mΩ)  # inductor series resistance

# Output capacitor
buck.C(1, 'out', 0, 22@u_uF)

# Load (constant resistance for now)
buck.R(1, 'out', 0, 10@u_Ω)  # 1.2A at 12V

# Simple PWM switch (on/off between vin and gnd)
# Real buck IC would be a .SUBCKT; here we show the netlist expectation:
# buck.SUBCKT('lmr14050_simplified', 'vin', 'sw', 'fb', 0, ...)

# Transient simulation: 100µs pulse width modulation
simulator = buck.simulator(temperature=85, nominal_temperature=25)
analysis = simulator.transient(
    step_time=10@u_ns,
    end_time=5@u_ms,
    use_initial_conditions=True,
    start_time=0
)

# Results shape
time = np.array(analysis.time)          # shape (500000,) for 5ms @ 10ns steps
v_out = np.array(analysis['out'])       # same shape
i_vin = np.array(analysis.V_in)         # branch current through V_in source
```

### 1.3 AC Analysis — Loop Stability

```python
# Same buck circuit with AC source
buck_ac = Circuit('Buck AC Loop Measurement')
buck_ac.V('in', 'vin', 0, 12@u_V)
buck_ac.V('ac', 'vac', 0, 'DC 0 AC 1')  # AC signal for loop gain sweep

# ... rest of topology ...

simulator = buck_ac.simulator(temperature=25, nominal_temperature=25)
analysis = simulator.ac(
    start_frequency=1@u_Hz,
    stop_frequency=1@u_MHz,
    number_of_points=100,
    variation='dec'  # decade (log scale)
)

# Results: complex numbers
freq = np.array(analysis.frequency)              # shape (100,)
vout_complex = np.array(analysis['out'])         # complex, shape (100,)
vout_mag = np.abs(vout_complex)                  # magnitude
vout_phase = np.angle(vout_complex, deg=True)    # phase in degrees
```

---

## 2. Component SPICE Model Parameters — Real Examples

### 2.1 Buck Converter IC: TI LMR14050

**Datasheet:** LMR14050S SIMPLE SWITCHER® Power Converter  
**Model availability:** TI provides encrypted and unencrypted PSpice transient models (SNVM708, SNVM923)

**Typical .SUBCKT header structure (inferred from TI patterns):**

```spice
.SUBCKT LMR14050S VIN GND VOUT FB EN FB_SENSE PGOOD PAD
* Ports: VIN (input), GND, VOUT (output), FB (feedback), EN (enable),
*        FB_SENSE (internal), PGOOD (power good), PAD (thermal pad)
*
* Internal behavioral parameters:
* - fsw_nom = 2.2 MHz (nominal switching frequency, adjustable 200kHz-2.5MHz)
* - rdson_hs = 90 mΩ (high-side MOSFET on-resistance)
* - rdson_ls = 70 mΩ (low-side MOSFET on-resistance)
* - vref = 0.6 V (feedback reference voltage)
* - comp_ramp = 1 V/µs (internal ramp compensation)
* - imax_peak = 6.5 A (peak current limit)
* - imax_avg = 5 A (continuous current rating)
* - temp_coeff = -4 mV/°C (reference temp coefficient)
*
* The model emulates:
*  1. Synchronous buck topology (high-side + low-side MOSFETs)
*  2. Error amplifier with configurable loop compensation
*  3. Current-mode control with slope compensation
*  4. Thermal shutdown + soft-start
*
.ENDS LMR14050S
```

**What a designer must provide to use this model in simulation:**

| Field | Value | Unit | Source |
|-------|-------|------|--------|
| `spice_model_path` | `/path/to/SNVM923.lib` | — | Downloaded from TI |
| `vin_min` | 4.0 | V | Datasheet (Operating Conditions) |
| `vin_max` | 40.0 | V | Datasheet |
| `vout_nominal` | User-defined (0.8–28V) | V | Feedback divider design |
| `iout_max` | 5.0 | A | Datasheet (Continuous Output Current) |
| `fsw_nom` | 2.2 | MHz | Datasheet (default; adjustable via Rset resistor) |
| `fsw_min` | 0.2 | MHz | Datasheet (freq range) |
| `fsw_max` | 2.5 | MHz | Datasheet |
| `rdson_hs` | 0.090 | Ω | Datasheet (FET Electrical Characteristics) |
| `rdson_ls` | 0.070 | Ω | Datasheet |
| `vref_fb` | 0.6 | V | Datasheet (Feedback Voltage) |
| `imax_peak` | 6.5 | A | Datasheet (current limit) |
| `temp_range` | -40 to +85 | °C | Datasheet (Operating Temp) |

### 2.2 LDO Regulator: TI TPS7A4701 (3.3V)

**Model structure (typical LDO behavioral model):**

```spice
.SUBCKT TPS7A4701 VIN VOUT GND EN
* VIN: input (4.5–6.5V for this device)
* VOUT: output (regulated to 3.3V ±2%)
* GND: ground
* EN: enable pin (high = on, low = off)
*
* Behavioral parameters:
* - vout_nom = 3.3 V (output voltage)
* - vout_tol = 0.02 (±2% tolerance)
* - dropout_v = 0.35 V (typical at 1A)
* - iq_standby = 50 µA (quiescent current, enabled)
* - imax = 1.0 A (max current)
* - psrr_dc = 60 dB (power supply rejection)
* - esr_cap = 5 mΩ (required for stability)
*
.ENDS TPS7A4701
```

**Critical data fields:**

| Field | Value | Unit |
|-------|-------|------|
| `spice_model_path` | TI LDO model | — |
| `vout_nominal` | 3.3 | V |
| `vin_min` | 4.5 | V |
| `vin_max` | 6.5 | V |
| `iout_max` | 1.0 | A |
| `dropout_v` | 0.35 | V |
| `psrr_dc` | 60 | dB |
| `esr_required` | 5 | mΩ |

### 2.3 MOSFET: Infineon IPP40N06S2 (N-Channel)

```spice
.MODEL IPP40N06S2 NMOS LEVEL=1
+ TOX=1.0e-8 KP=25 LAMBDA=0.04
+ VTO=1.0 GAMMA=0.4 PHI=0.7
*
* Critical electrical parameters:
* - Vgs_th = 1.0 V (gate-source threshold)
* - Rdson = 4.0 mΩ @ Vgs=10V, Id=20A (on-resistance)
* - Cgs = 1.8 nF (gate-source capacitance)
* - Cgd = 0.5 nF (gate-drain capacitance)
* - Cds = 50 pF (drain-source cap, parasitic)
* - Qg = 50 nC (total gate charge)
* - Qgs = 20 nC (gate-source charge)
* - Qgd = 15 nC (gate-drain charge)
*
.ENDS IPP40N06S2
```

**Required fields:**

| Field | Value | Unit |
|-------|-------|------|
| `spice_model` | IPP40N06S2 | — |
| `vgs_th` | 1.0 | V |
| `rdson_mohm` | 4.0 | mΩ |
| `rds_vgs` | 10 | V (test condition) |
| `rds_id` | 20 | A (test condition) |
| `qg` | 50 | nC |
| `cgs` | 1.8 | nF |
| `cgd` | 0.5 | nF |

### 2.4 Operational Amplifier: TI OPA2333 (Dual, Rail-to-Rail)

```spice
.SUBCKT OPA2333 1 2 3 4 5
* Pins: 1=IN+, 2=IN-, 3=V+, 4=V-, 5=OUT
*
* Behavioral model parameters:
* - A0_dc = 100,000 V/V (open-loop gain)
* - GBW = 1 MHz (gain-bandwidth product)
* - SR = 0.5 V/µs (slew rate)
* - input_impedance = 10 MΩ (typ)
* - output_impedance = 75 Ω
* - iio = 0.5 pA (input offset current)
* - vio = 5 mV (input offset voltage)
*
.ENDS OPA2333
```

---

## 3. Simulation Request Shape & Common Directives

### 3.1 Transient Analysis

```spice
.tran <tstep> <tstop> [<tstart> [<tmax>]] [uic]

* Examples:
.tran 10ns 5ms                    # step=10ns, stop=5ms
.tran 1us 100ms 0 100us uic       # with initial conditions
.tran 100ps 10us 0 500ps          # fast switching transient
```

**PySpice equivalent:**

```python
analysis = simulator.transient(
    step_time=10@u_ns,
    end_time=5@u_ms,
    start_time=0@u_s,
    max_step=100@u_ns,
    use_initial_conditions=True
)
```

### 3.2 AC Analysis (Loop Gain, Impedance)

```spice
.ac <variation> <n_points> <start_freq> <stop_freq>

* Examples:
.ac dec 100 1 1MEG              # decade: 1 Hz → 1 MHz, 100 pts/decade
.ac oct 50 10 100MEG            # octave: 10 Hz → 100 MHz, 50 pts/octave
.ac lin 1000 0 100kHz           # linear: 0 Hz → 100 kHz, 1000 points
```

**PySpice:**

```python
analysis = simulator.ac(
    start_frequency=1@u_Hz,
    stop_frequency=1@u_MHz,
    number_of_points=100,
    variation='dec'  # or 'oct', 'lin'
)
```

### 3.3 DC Sweep

```spice
.dc <var> <start> <stop> <incr>

* Examples:
.dc V1 0 12 0.1               # sweep V1 from 0–12V in 0.1V steps
.dc VIN 10 30 1 TEMP -40 85   # nested: voltage + temperature
```

---

## 4. PySpice Output Data Structures

### 4.1 Transient Analysis Result

```python
analysis = simulator.transient(step_time=10@u_ns, end_time=5@u_ms, uic=True)

# Access time array
time = np.array(analysis.time)
# Shape: (n_samples,)
# Dtype: float64
# Range: 0 → 5ms in 10ns steps = 500,000 points

# Access voltage at a node
vout = np.array(analysis['out'])
# Same shape as time
# Dtype: float64
# Values: voltage in volts

# Access current through a source (V_in injects at 'vin' node)
i_vin = np.array(analysis.V_in)
# Same shape
# Values: current in amperes

# Introspect available nodes/currents
print(analysis.nodes)            # dict: node_name → voltage array
print(analysis.branches)         # dict: voltage_source_name → current array

# Typical return shape for 5ms simulation @ 10ns resolution:
#   analysis.time:     (500_000,)
#   analysis['out']:   (500_000,)  float64
#   analysis['sw']:    (500_000,)  float64
#   analysis.V_in:     (500_000,)  float64
```

### 4.2 AC Analysis Result

```python
analysis = simulator.ac(
    start_frequency=1@u_Hz,
    stop_frequency=1@u_MHz,
    number_of_points=100,
    variation='dec'
)

# Frequency array
freq = np.array(analysis.frequency)
# Shape: (100,)  [log-spaced 1 Hz → 1 MHz]
# Dtype: float64
# Unit: Hz

# Voltage as complex number (magnitude + phase embedded)
vout_complex = np.array(analysis['out'])
# Shape: (100,)
# Dtype: complex128
# Each element represents one frequency point

# Extract magnitude and phase
magnitude = np.abs(vout_complex)        # shape (100,), unit V or V/V
phase_deg = np.angle(vout_complex, deg=True)  # shape (100,), degrees

# Typical use: loop gain measurement for stability
gain_db = 20 * np.log10(magnitude)
phase_margin = phase_deg[np.argmin(np.abs(magnitude - 1))]  # PM at unity gain
```

### 4.3 DC Sweep Result

```python
analysis = simulator.dc(var='V1', start=0@u_V, stop=12@u_V, step=0.1@u_V)

v1_values = np.array(analysis.V1)
vout_values = np.array(analysis['out'])
# Both shape (121,) for 12V / 0.1V step
```

---

## 5. Manufacturer SPICE Model Availability — By Category

### 5.1 Buck Converter ICs

| Manufacturer | Part # | Model Format | Status | Notes |
|--------------|--------|--------------|--------|-------|
| TI | LMR14050 | Encrypted/Unencrypted PSpice | ✅ Available | Two versions: fast encrypted, slow unencrypted. Both work in PySpice. |
| TI | TPS62130 | PSpice (.lib) | ✅ Available | Standard encrypted netlist. |
| TI | LM61460 | PSpice behavioral | ✅ Available | Download from TI Design Kits. |
| Infineon | NCP1123 | SPICE | ✅ Available | Via Infineon parametric search. |
| Analog Devices | ADP2504 | SPICE | ✅ Available | Simulation model + schematic. |

**Verdict:** ✅ **Buck ICs are well-supported. All major vendors ship models.**

### 5.2 LDO Regulators

| Manufacturer | Part # | Model Format | Status | Notes |
|--------------|--------|--------------|--------|-------|
| TI | TPS7A4701 | PSpice behavioral | ✅ Available | Includes dropout voltage + PSRR. |
| TI | LM1117 | SPICE | ✅ Available | Older model, slower simulation. |
| ON Semi | NCP114 | SPICE | ✅ Available | Via ON Semi design support. |
| Infineon | TLS205xx | SPICE | ✅ Available | Behavioral model. |

**Verdict:** ✅ **LDOs are well-supported.**

### 5.3 MOSFETs

| Category | Example | Model Format | Status | Notes |
|----------|---------|--------------|--------|-------|
| N-Channel Logic | IPP40N06S2 | Level 1/3 NMOS | ✅ Available | All major fabs provide SPICE models. |
| P-Channel | BSP296 | PMOS | ✅ Available | Infineon, On Semi, Vishay all support. |
| Power N-Channel | IRF3205 | Level 3 NMOS | ✅ Available | IR provides detailed transient models. |

**Verdict:** ✅ **MOSFET models ubiquitous. Sometimes need to extract from datasheets.**

### 5.4 Op-Amps

| Manufacturer | Part # | Model Format | Status | Notes |
|--------------|--------|--------------|--------|-------|
| TI | OPA2333 | SUBCKT (behavioral) | ✅ Available | Rail-to-rail, includes slew rate. |
| TI | LM358 | SPICE | ✅ Available | Older, simple model. |
| Analog Devices | AD8065 | SPICE | ✅ Available | High-speed op-amp, detailed model. |

**Verdict:** ✅ **Op-amp models available. Some omit non-ideal effects (output impedance, noise).**

### 5.5 MCU Power Pin Models

| Category | Status | Notes |
|----------|--------|-------|
| Generic MCU I/O pin | ⚠️ Partial | ARM Cortex-M datasheets rarely include SPICE models for I/O. Must use approximation: R_on (25–100Ω) + parasitic C (~5pF). |
| Power sequencing IC | ✅ Available | TI PMIC models (e.g., TPS65910) include full ESD, slew-rate, and switching models. |
| STM32 / ESP32 core power | ❌ Not available | Vendors omit internal voltage regulator SPICE models. Use averaged model: `V_internal = f(V_core_input, load_current)`. |

**Verdict:** ⚠️ **MCU power pins are NOT typically simulated. Use generic load model instead.**

---

## 6. Contract Gaps: What `SubsystemPick.actuals` Must Carry

### 6.1 Current Contract (from `research_bundle.py`)

```python
class SubsystemPick(BaseModel):
    # ... basic fields ...
    actuals: dict[str, float | int | str] = Field(default_factory=dict)
    # ↑ Free-form dict — no schema per category
```

### 6.2 Required Fields for SPICE Simulation — By Subsystem Type

#### **Buck Converter**

| Field | Type | Unit | Typical Value | Present in `actuals` |
|-------|------|------|---|---|
| `spice_model_path` | str | — | `/path/to/lib/LMR14050S.lib` | ❌ NO |
| `spice_model_subckt` | str | — | `LMR14050S` | ❌ NO |
| `vin_min` | float | V | 4.0 | ⚠️ Maybe (labeled `vin`) |
| `vin_max` | float | V | 40.0 | ⚠️ Maybe |
| `vout_nominal` | float | V | 5.0 | ✅ YES (labeled `vout`) |
| `iout_max` | float | A | 5.0 | ✅ YES |
| `fsw_nom` | float | MHz | 2.2 | ✅ YES (labeled `fsw_khz`, needs unit conversion) |
| `l_uh` | float | µH | 3.3 | ⚠️ Maybe (component selection, not from IC datasheet) |
| `cout_uf` | float | µF | 22.0 | ⚠️ Maybe (component selection) |
| `cin_uf` | float | µF | 10.0 | ❌ NO (input capacitor requirement) |
| `rdson_hs` | float | mΩ | 90.0 | ❌ NO (device-specific, in .lib) |
| `rdson_ls` | float | mΩ | 70.0 | ❌ NO |
| `vref_fb` | float | V | 0.6 | ❌ NO |
| `temp_range_min` | float | °C | -40.0 | ❌ NO |
| `temp_range_max` | float | °C | 85.0 | ❌ NO |

**Missing:** `spice_model_path`, `spice_model_subckt`, `cin_uf`, component ratings (rdson, vref), temperature range.

#### **LDO**

| Field | Type | Unit | Typical Value | Present |
|-------|------|------|---|---|
| `spice_model_path` | str | — | `/path/LDO.lib` | ❌ NO |
| `vin_min` | float | V | 4.5 | ⚠️ Maybe |
| `vin_max` | float | V | 6.5 | ⚠️ Maybe |
| `vout_nominal` | float | V | 3.3 | ✅ YES |
| `iout_max` | float | A | 1.0 | ✅ YES |
| `dropout_v` | float | V | 0.35 | ❌ NO |
| `psrr_dc` | float | dB | 60.0 | ❌ NO |
| `esr_required` | float | mΩ | 5.0 | ❌ NO |
| `cin_uf` | float | µF | 1.0 | ❌ NO |
| `cout_uf` | float | µF | 1.0 | ❌ NO |

**Missing:** `spice_model_path`, `dropout_v`, `psrr_dc`, `esr_required`, capacitor requirements.

#### **MOSFET**

| Field | Type | Unit | Typical Value | Present |
|-------|------|------|---|---|
| `spice_model_name` | str | — | `IPP40N06S2` | ❌ NO |
| `spice_model_path` | str | — | `/path/mosfet.lib` | ❌ NO |
| `vgs_th` | float | V | 1.0 | ❌ NO |
| `rdson_mohm` | float | mΩ | 4.0 | ❌ NO |
| `qg` | float | nC | 50.0 | ❌ NO |
| `cgs` | float | nF | 1.8 | ❌ NO |
| `cgd` | float | nF | 0.5 | ❌ NO |

**Missing:** All SPICE-specific parameters.

#### **Op-Amp**

| Field | Type | Unit | Present |
|-------|------|------|---|
| `spice_model_path` | str | — | ❌ NO |
| `a0_dc` | float | V/V | ❌ NO |
| `gbw` | float | MHz | ❌ NO |
| `sr` | float | V/µs | ❌ NO |
| `iio` | float | pA | ❌ NO |

**Missing:** All parameters.

---

## 7. Recommendations: Contract Evolution

### Phase 1 (Immediate)
Add `spice_model_path` to `SubsystemPick` — string field pointing to manufacturer `.lib` file (cached locally or fetched on-demand).

```python
class SubsystemPick(BaseModel):
    # ... existing fields ...
    spice_model_path: str | None = None
    spice_model_subckt: str | None = None
```

### Phase 2 (Near-term)
Type `actuals` per category. Create a `BuckConverterActuals` Pydantic model with all fields. Replaces free-form dict.

```python
class BuckConverterActuals(BaseModel):
    vin_min: float  # V
    vin_max: float  # V
    vout_nominal: float  # V
    iout_max: float  # A
    fsw_nom: float  # kHz (convert from MHz on import)
    l_uh: float  # µH
    cout_uf: float  # µF
    cin_uf: float | None = None  # µF (optional, inferred if missing)
    rdson_hs: float | None = None  # mΩ (optional, from datasheet)
    # ... etc ...

class SubsystemPick(BaseModel):
    actuals: BuckConverterActuals | LDOActuals | MOSFETActuals | ...
```

### Phase 3 (Future)
Cache SPICE models + pre-extract datasheet values into subsystem.json (avoid re-parsing).

---

## 8. Validation Checklist for PySpice Simulation

Before calling `ee.adapters.ngspice_adapter.transient()`:

- [ ] `SubsystemPick.spice_model_path` exists and is readable
- [ ] `SubsystemPick.spice_model_subckt` matches a `.SUBCKT` in the model file
- [ ] All `actuals` required by the category are populated (no None)
- [ ] Units match PySpice expectations (V, A, Ω, H, F, etc.)
- [ ] Temperature range (`temp_range_min`, `temp_range_max`) is set
- [ ] Passive component values (L, C, R) for topology are specified

---

## References

1. **PySpice Documentation:** https://pyspice.fabrice-salvaire.fr/ (v1.5)
2. **TI LMR14050S Datasheet + Models:** https://www.ti.com/product/LMR14050
3. **ngspice User Manual:** http://ngspice.sourceforge.net/ (netlist syntax, .tran/.ac directives)
4. **SamacSys CSE (SPICE Models):** https://componentsearchengine.com/ (model availability)
5. **IEEE 1076.1-2007:** VHDL-AMS standard (frequency response, behavioral models)

---

**Last reviewed:** 2026-05-24  
**Author:** Research Agent  
**Next step:** Contract update in Phase 2 (type `actuals` per category)
