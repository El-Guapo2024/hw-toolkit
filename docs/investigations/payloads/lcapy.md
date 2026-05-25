# Lcapy Payload Research

Research on lcapy (symbolic circuit analysis) inputs/outputs for `ee/` engine layer validation. Goal: confirm our pydantic contract carries sufficient data to feed symbolic analyses without guessing.

---

## 1. Lcapy Circuit Input Format

Lcapy accepts netlist strings (SPICE-like syntax) passed to `Circuit()` constructor. Each line defines a component with node connections and values.

### Example 1: RC Low-Pass Filter

```python
from lcapy import Circuit

# 10k resistor in series, 1uF cap to ground
cct = Circuit("""
V1 1 0 step 1
R1 1 2 10k
C1 2 0 1u
""")

# Extract transfer function from input (V1) to output (node 2)
H = cct.transfer_function(2, 1)  # Output node 2, input source V1
print(H)  # symbolic: 1/(1 + s*R*C)
```

### Example 2: RLC Series Circuit

```python
cct = Circuit("""
V1 1 0 step 10
R1 1 2 1k
L1 2 3 100m
C1 3 0 100u
""")

# Bode magnitude and phase
cct.bode_plot(2)  # node 2 voltage response
```

### Example 3: Op-Amp Inverting Amplifier (Ideal)

```python
cct = Circuit("""
V1 1 0 dc 1 ac 1
Rin 1 2 10k
Rf 2 3 100k
E1 3 0 2 0 -100000
""")
# Note: E1 is a voltage-controlled voltage source (VCVS)
# Syntax: E node+ node- control+ control- gain
# This models high open-loop gain; realistic op-amp uses E or O element
```

**Component Syntax Summary:**

| Element | Syntax | Example |
|---------|--------|---------|
| Voltage source | `V name n1 n2 [type value]` | `V1 1 0 step 5` |
| Resistor | `R name n1 n2 value` | `R1 1 2 10k` |
| Inductor | `L name n1 n2 value` | `L1 2 3 100m` |
| Capacitor | `C name n1 n2 value` | `C1 3 0 1u` |
| VCVS | `E name n+ n- nc+ nc- gain` | `E1 3 0 2 0 10` |
| VCCS | `G name n+ n- nc+ nc- transcond` | `G1 3 0 2 0 0.1` |
| Current source | `I name n1 n2 [type value]` | `I1 1 0 ac 1m` |

Values use SI suffixes: `k=1e3, m=1e-3, u=1e-6, p=1e-12, M=1e6`.

---

## 2. What Lcapy Needs Per Component

### Passive Components (R, L, C)

- **Resistor**: Just value in ohms. No models needed for ideal analysis.
- **Inductor**: Value in henries. Can include series resistance (model parameter, optional).
- **Capacitor**: Value in farads. ESR modeled as series resistor if needed.

Example with ESR:

```python
cct = Circuit("""
V1 1 0 step 1
R1 1 2 1k
Resr 2 3 0.1  # Capacitor ESR
C1 3 0 10u
""")
```

### Op-Amps & Dependent Sources

- **Ideal VCVS** (`E`): node connections + gain factor. No frequency response.
- **Realistic op-amp**: Use voltage follower or frequency-dependent model via transfer function injection.
  
Lcapy can load a pre-computed transfer function (e.g., op-amp open-loop Bode) and substitute:

```python
from lcapy import s, Circuit
from lcapy.core.opamp import Opamp

# Option 1: Frequency-dependent open-loop gain
A_ol = 100000 / (1 + s / (2 * 3.14159 * 100))  # Gain with 100 Hz bandwidth
# Then use in circuit model

# Option 2: Direct VCVS (ignores frequency for idealized analysis)
cct = Circuit("""
... op-amp internals modeled as E (VCVS) ...
""")
```

### Transformers & Isolation

- Not commonly used in lcapy examples; focus is on R, L, C, sources, and dependent sources.

---

## 3. Analyses Available

### Transfer Function (Frequency Domain)

```python
cct = Circuit("""
V1 1 0 ac 1
R1 1 2 10k
C1 2 0 1u
""")

H = cct.transfer_function(2, 1)  # Output node 2, input source V1
print(H)  # SymPy Expr: 1/(1 + 10000*s*1e-6) = 1/(1 + 0.01*s)

# Simplify & substitute
H_simplified = H.simplify()
print(H_simplified.pole_residues())  # Poles & residues for partial fraction
```

### Bode Plot (Magnitude & Phase)

```python
from lcapy import Circuit
import matplotlib.pyplot as plt

cct = Circuit("""
V1 1 0 step 1
R1 1 2 1k
C1 2 0 100n
""")

# Returns magnitude (dB) and phase (degrees) over frequency
cct.bode_plot(2)  # Plot voltage at node 2
plt.show()

# Programmatic access to Bode data:
H = cct.transfer_function(2, 1)
# H.magnitude(...) and H.phase(...) available for numeric eval
```

### Transient Response (Time Domain)

```python
cct = Circuit("""
V1 1 0 step 1
R1 1 2 10k
C1 2 0 100u
""")

# Step response: output voltage at node 2
# Symbolic Laplace → inverse transform gives time-domain
# Lcapy computes this symbolically or via numerical simulation

# Access via:
H = cct.transfer_function(2, 1)  # Transfer function
step_response = H.step_response()  # t, y(t) arrays
```

### Pole-Zero Analysis

```python
H = cct.transfer_function(2, 1)

# Extract symbolic poles & zeros
poles = H.poles()  # List of pole locations (complex s-plane)
zeros = H.zeros()

print(poles)  # e.g., [-100] means pole at s = -100
print(zeros)  # e.g., [0] means zero at origin

# Plot pole-zero diagram
H.pole_zero_plot()
```

### Gain & Phase Margin (Stability)

```python
# Not explicitly in standard docs, but derivable from Bode
# For closed-loop system: find crossover frequencies, margins

# Manual approach:
H = cct.transfer_function(...)  # Open-loop or closed-loop
# Find magnitude = 0 dB (gain crossover) and phase at that frequency
# Phase margin = 180° - phase_at_gain_crossover
# Gain margin = 20*log10(1 / magnitude_at_180deg_phase)
```

---

## 4. Output Shapes & Symbolic Substitution

### Symbolic Expressions (SymPy)

Transfer function returns a SymPy `Expr` object:

```python
from lcapy import Circuit, s

cct = Circuit("""
V1 1 0 ac 1
R1 1 2 10k
C1 2 0 1u
""")

H = cct.transfer_function(2, 1)
print(type(H))  # <class 'lcapy.core.transfer_function.TransferFunction'>
print(H)  # Unevaluated: 1/(1 + s*R*C) or numeric equivalent

# H is a rational function (num/den)
print(H.N)  # Numerator polynomial
print(H.D)  # Denominator polynomial
```

### Numeric Substitution

```python
# Define symbols if not already in the netlist
R, C = symbols('R C', real=True, positive=True)

cct = Circuit(f"""
V1 1 0 ac 1
R1 1 2 {R}
C1 2 0 {C}
""")

H = cct.transfer_function(2, 1)

# Substitute numeric values
H_numeric = H.subs({R: 10e3, C: 1e-6})
print(H_numeric)  # 1/(1 + 0.01*s)

# Evaluate at a specific frequency (rad/s)
import numpy as np
s_val = 2j * np.pi * 1000  # s = j*2*pi*f for f=1kHz
magnitude = abs(H_numeric.subs(s, s_val))
phase_rad = np.angle(H_numeric.subs(s, s_val))
print(f"Magnitude: {magnitude:.3f}, Phase: {np.degrees(phase_rad):.1f}°")
```

### Array-like Output (Transient)

```python
H = cct.transfer_function(2, 1)
t, y = H.impulse_response(None)  # Time array, impulse response

# y is numpy array; plot with matplotlib
import matplotlib.pyplot as plt
plt.plot(t, y)
plt.show()
```

---

## 5. Concrete Use Cases in Hardware Context

### Use Case 1: Buck Converter Feedback Loop Stability

**Problem**: Design a compensation network (resistor + capacitor) in the feedback path of a buck converter to achieve target phase margin (e.g., 45°).

**What we need**:
- Power stage transfer function: G_vd(s) (duty-cycle to output voltage)
- Feedback resistor divider: β (voltage divider gain from Vout → Vfb)
- Error amplifier: transimpedance (μA/V), frequency response
- Output capacitor: value, ESR, ESL
- Load: bulk impedance (varies with frequency)

**Lcapy netlist approach**:

```python
from lcapy import Circuit, s, symbols

# Parameters extracted from datasheet + component selections
Gvd, Gea, beta, Cout, Esr, fz = symbols('Gvd Gea beta Cout Esr fz', real=True, positive=True)

cct = Circuit(f"""
* Buck power stage transfer function (Laplace)
Gvd_src 1 0 ac 1
* Gvd is pre-computed from L, Vin, Fsw, etc.

* Feedback divider (ideal)
Rfb1 1 2 10k
Rfb2 2 0 10k

* Output cap + ESR (in feedback path)
Cout_series 2 3 {Esr}
Cout_cap 3 0 {Cout}

* Compensation network (series RC)
Rcomp 3 4 10k
Ccomp 4 0 100n

* Error amplifier (transimpedance)
Gea 5 0 4 0 {Gea}

* Close loop via feedback
* (simplified representation; actual loop is more complex)
""")

# Extract loop gain (product of all gains)
H_loop = cct.transfer_function(...)
# Check phase margin at crossover
```

### Use Case 2: ADC Anti-Alias Filter Design

**Problem**: Design an RC low-pass filter before ADC to reject out-of-band noise while preserving signal.

**Specification**:
- Signal bandwidth: 1 kHz
- ADC sample rate: 10 kHz
- Attenuation at 5 kHz (Nyquist neighborhood): > 20 dB

**Lcapy check**:

```python
from lcapy import Circuit

R_val, C_val = 10e3, 10e-6  # Guessed values

cct = Circuit(f"""
V1 1 0 ac 1
R1 1 2 {R_val}
C1 2 0 {C_val}
""")

H = cct.transfer_function(2, 1)

# Evaluate magnitude at Nyquist
import numpy as np
f_nyquist = 5000  # Hz
s_nyquist = 2j * np.pi * f_nyquist
mag_db = 20 * np.log10(abs(H.subs(s, s_nyquist)))
print(f"Attenuation at {f_nyquist} Hz: {mag_db:.1f} dB")
# If < -20 dB, design passes; else iterate R, C
```

### Use Case 3: Passive RC Matching Network (Impedance Control)

**Problem**: Match source impedance to load over frequency (e.g., USB data lines, antenna feed).

**Lcapy use**:
- Compute impedance looking into network vs. frequency
- Extract group delay (slope of phase response)
- Check impedance flatness over bandwidth

### Use Case 4: Filter Transfer Function (Multiple Feedback, Sallen-Key)

**Problem**: Design a Butterworth low-pass filter with cutoff at 10 kHz and Butterworth response (maximally flat).

**Lcapy verification**:

```python
# Design via closed-form (cutoff, Q, topology), then verify in Lcapy
# E.g., multiple-feedback topology with R1, R2, C1, C2

cct = Circuit("""
V1 1 0 ac 1
R1 1 2 10k
R2 2 3 10k
C1 1 3 10n
C2 3 0 10n
""")

H = cct.transfer_function(3, 1)
# Check Butterworth flat response in passband, roll-off at cutoff
```

### Use Case 5: Transient Overshoot & Settling Time

**Problem**: Verify that an LDO output doesn't ring when load steps from 0 to full current.

**Lcapy use**:
- Model output impedance (capacitor + ESR)
- Model load step as current pulse
- Compute transient response → settling time, overshoot

```python
cct = Circuit("""
I_load 1 0 step 1  * Load step (1A)
* Parasitic output inductance
L_out 1 2 10n
* Output cap
C_out 2 0 1u
* ESR
R_esr 2 3 10m
""")

# Transient: output voltage response to load step
```

---

## 6. Current Contract Gap Analysis

Our pydantic contract (`hw_agent/core/research_bundle.py`) has:

```python
class SubsystemPick(BaseModel):
    # ...
    actuals: dict[str, float | int | str] = Field(default_factory=dict)
```

**Current limitation**: `actuals` is a free-form dict. No typed schema per category.

### Fields Currently Missing for Lcapy Analyses

#### For Buck Converter Feedback Loop Stability:

- `cout_uf` ✓ (likely in actuals)
- `cout_esr_mohm` ✗ **MISSING**
- `l_uh` ✗ **MISSING** (inductor value)
- `vin_min_v`, `vin_max_v` ✓ (power interface voltage)
- `vout_v` ✓ (output voltage)
- `iout_max_a` ✓ (max output current)
- `fsw_khz` ✗ **MISSING** (switching frequency for G_vd computation)
- `gm_ua_per_v` ✗ **MISSING** (error amplifier transconductance)
- `fbdiv_r1_ohm`, `fbdiv_r2_ohm` ✗ **MISSING** (feedback divider)
- `rcomp_ohm`, `ccomp_uf` ✗ **MISSING** (compensation network)
- `cout_esl_nh` ✗ **MISSING** (capacitor effective series inductance)

#### For ADC Anti-Alias Filter:

- `adc_input_impedance_ohm` ✗ **MISSING**
- `signal_bw_hz` ✗ **MISSING** (signal bandwidth requirement)
- `adc_sample_rate_hz` ✗ **MISSING**
- `filter_cutoff_hz` ✗ **MISSING** (target cutoff)
- `filter_order` ✗ **MISSING** (Butterworth, Chebyshev, etc.)
- `filter_components` ✗ **MISSING** (R, C values or topology)

#### For Passive Matching Networks:

- `source_impedance_ohm` ✗ **MISSING**
- `target_impedance_ohm` ✗ **MISSING**
- `frequency_range_hz` ✗ **MISSING** (min-max)
- `network_components` ✗ **MISSING** (R, L, C values)

---

## 7. Recommendation: Schema Evolution

For Phase 2 (symbolic analysis layer), we should:

1. **Define per-category field schemas** in `hw_agent/templates/` (e.g., `buck_converter.py`, `adc_filter.py`).
   - Use Pydantic `Field()` with descriptions.
   - Distinguish between designer-facing inputs (requirements) and lcapy-facing outputs (actuals).

2. **Extend SubsystemPick.actuals** with typed overlays:
   ```python
   class BuckConverterActuals(BaseModel):
       cout_uf: float
       cout_esr_mohm: float
       cout_esl_nh: float = 0.1  # default if not known
       l_uh: float
       fsw_khz: float
       gm_ua_per_v: float
       # ... etc
   
   class SubsystemPick(BaseModel):
       # ... existing fields ...
       actuals_typed: dict[str, BaseModel] | None = None
       # Fallback: original `actuals` dict for migration
   ```

3. **Validation gates** before handing to lcapy:
   - Check all required fields are present.
   - Validate numeric ranges (e.g., capacitance > 0, ESR >= 0).
   - Warn if optional fields (ESR, ESL) are missing and use defaults.

4. **Lcapy netlist generation** in `ee/symbolic/` engine:
   - Takes SubsystemPick (fully populated actuals) → generates Circuit() netlist.
   - Runs analyses (Bode, transfer function, stability margins).
   - Returns results as structured output (JSON or proto).

---

## References

- **Lcapy Docs**: https://lcapy.readthedocs.io/
- **Circuit Netlist Syntax**: https://lcapy.readthedocs.io/en/latest/circuits.html
- **Transfer Functions & Analysis**: Implicitly via Laplace domain methods (documented in tutorials)
- **Op-Amp Circuits**: https://lcapy.readthedocs.io/en/latest/tutorials.html

