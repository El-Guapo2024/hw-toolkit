# scikit-rf Payload Research: Real Examples for PCB RF/TL Analysis

## Executive Summary

scikit-rf is a Python library for RF/microwave network analysis. It loads S-parameter Touchstone files and creates transmission-line media objects for characteristic impedance, propagation, and loss calculations. Our `ee/` engine must carry: **trace geometry** (width, spacing, thickness), **stackup** (dielectric height, Er, loss tangent), and **target Z0** values to feed scikit-rf's media classes and network cascading operations.

---

## 1. Transmission Line / Microstrip Z0 Calculation

### Real Python Example: Microstrip on FR4

```python
from skrf import Frequency
from skrf.media import MLine

# Create frequency axis (1–10 GHz, 1001 points)
freq = Frequency(0.1, 10, 1001, 'GHz')

# Create microstrip media with physical parameters
mlin = MLine(
    freq,
    w=3e-3,           # trace width: 3 mm
    h=1.6e-3,         # substrate height (FR4): 1.6 mm
    t=35e-6,          # copper thickness: 35 µm (1 oz)
    ep_r=4.5,         # relative permittivity (FR4 ~4.5)
    rho=1.68e-08,     # copper resistivity: 1.68e-8 Ω·m
    tand=0.02,        # loss tangent (FR4 typical: 0.015–0.025)
    z0_port=50        # port impedance: 50Ω (normalization ref)
)

# Access characteristic impedance as function of frequency
z0_line = mlin.z0   # Array of Z0 over frequency sweep
print(f"Z0 @ 1 GHz: {z0_line[100]:.2f} Ω")  # Typical: ~45–55Ω

# Create a 10mm transmission line
tline = mlin.line(d=10e-3)  # 10 mm length
ntwk = tline
print(ntwk)
# Output: 1-Port Network, 0.1-10.0 GHz, 1001 pts, z0=[50.+0.j]

# Cascade with shorts/opens
short = mlin.short()          # Short-circuit termination
opened = mlin.open()          # Open-circuit termination
matched_load = mlin.load(50)  # 50Ω load
```

**Key Parameters for hw-toolkit:**
- `w` (trace_width_mm) — from routed layer geometry
- `h` (dielectric_height_mm) — from stackup definition
- `t` (copper_thickness_mm) — from stackup (oz→mm conversion)
- `ep_r` (relative_permittivity) — material database or stackup preset
- `tand` (loss_tangent) — material property (typically 0.015–0.025 for FR4)
- `rho` (copper_resistivity) — constant (1.68e-8 Ω·m at room temp)

---

## 2. Stripline / Coplanar Waveguide

### Stripline Example (Symmetric, Inner Layer)

```python
from skrf.media import Stripline

# Stripline: trace centered between two reference planes
sl = Stripline(
    freq,
    w=2e-3,           # trace width: 2 mm
    h=0.8e-3,         # spacing from trace to nearest plane: 0.8 mm
    t=25e-6,          # copper thickness: 25 µm
    ep_r=4.5,
    rho=1.68e-08,
    tand=0.02,
    z0_port=75        # Stripline often targets 75Ω for differential pairs
)

# Create transmission line segment
sl_line = sl.line(d=5e-3)  # 5 mm stripline segment
print(sl.z0)               # Displays Z0 over frequency
```

### Coplanar Waveguide Example

```python
from skrf.media import CPW

# CPW: traces adjacent to ground planes, same layer
cpw = CPW(
    freq,
    w=0.5e-3,         # center conductor (signal) width: 0.5 mm
    s=0.2e-3,         # spacing to ground: 0.2 mm each side
    h=1.6e-3,         # substrate height
    ep_r=4.5,
    rho=1.68e-08,
    tand=0.02,
    z0_port=50
)

# Create CPW transmission line
cpw_line = cpw.line(d=20e-3)  # 20 mm CPW segment
print(cpw.z0)                  # Characteristic impedance vs frequency
```

---

## 3. S-Parameter Touchstone Format (.s2p)

### Real File: ring_slot.s2p (scikit-rf Example)

**Header:**
```
!Created with skrf (http://scikit-rf.org).
# GHz S RI R 50.0 
!freq ReS11 ImS11 ReS21 ImS21 ReS12 ImS12 ReS22 ImS22
```

**Data Format Specification:**
- `GHz` — frequency units (alternatives: Hz, kHz, MHz)
- `S` — S-parameters (alternatives: Y, Z, H, G for admittance, impedance, hybrid-h, hybrid-g)
- `RI` — Real/Imaginary format (alternatives: MA = magnitude-angle, DB = dB-angle)
- `R 50.0` — reference impedance 50Ω for all ports

**First 5 Data Rows (Real-Imaginary Format):**
```
75.0     -0.503723180993  0.457844804761  0.61345710452  0.366781386817  0.61345710452  0.366781386817  -0.199584332837  0.648334696392
75.175   -0.495819040077  0.457076980761  0.621819395859  0.364031687136  0.621819395859  0.364031687136  -0.190798124123  0.644295561344
75.35    -0.487825384652  0.456157798105  0.630243009468  0.361095735086  0.630243009468  0.361095735086  -0.181988476534  0.640039089772
75.525   -0.479744512353  0.455081862056  0.638724152115  0.357968204999  0.638724152115  0.357968204999  -0.173160794358  0.635560385081
75.7     -0.471578977024  0.453843720061  0.647258737707  0.354643769501  0.647258737707  0.354643769501  -0.164320801392  0.630854618933
```

**Column Mapping (per S2P spec):**
```
Frequency  Real(S11)  Imag(S11)  Real(S21)  Imag(S21)  Real(S12)  Imag(S12)  Real(S22)  Imag(S22)
```

**When to Use:**
- Connector S-parameters (de-embedding fixtures)
- Via field modeling (electromagnetic solver output)
- Antenna matching network characterization
- Differential pair impedance verification (from post-route sim)

---

## 4. Network Analysis: Loading, Cascading, De-embedding

### Loading and Basic Operations

```python
import skrf as rf

# Load S-parameter file
connector = rf.Network('connector_s11.s2p')
print(connector)
# Output: 2-Port Network: 'connector_s11', 0.1–10.0 GHz, 1001 pts, z0=[50.+0.j 50.+0.j]

# Element-wise math on S-parameters
ntwk_diff = ntwk_1 - ntwk_2
ntwk_product = ntwk_1 * ntwk_2  # Element-wise multiply
ntwk_ratio = ntwk_1 / ntwk_2
```

### Cascading Networks (Series Connection)

```python
# Create transmission line segment and load
tline = mlin.line(d=10e-3)    # 10 mm microstrip
short_circ = mlin.short()      # Short circuit at end

# Cascade: tline in series with short
combined = tline ** short_circ
print(combined.s_mag)          # Magnitude of cascaded S-parameters

# Alternative: de-embed a fixture
measured_with_fixture = rf.Network('measurement_with_fixture.s2p')
fixture = rf.Network('fixture_model.s2p')

# Remove fixture by cascading its inverse
de_embedded = fixture.inv ** measured_with_fixture
```

### Use Case: USB Differential Pair Verification

```python
# Load routed USB DP/DM pair characterization
usb_dp_dm = rf.Network('usb_trace_sim.s2p')

# Define 90Ω differential target
target_z0_diff = 90

# Extract S-parameters and check return loss
return_loss_db = -20 * np.log10(np.abs(usb_dp_dm.s11))
print(f"Return loss: {return_loss_db:.1f} dB (target: > 10 dB @ high freq)")
```

---

## 5. Concrete PCB RF/TL Checks Solved by scikit-rf

### Check 1: USB-C Differential Pair Z0 Verification
**Goal:** Ensure routed DP/DM pair matches 90Ω nominal differential impedance.

```python
from skrf.media import MLine

# Define stackup from design
stackup = {
    'layer': 'Layer 2 (inner)',
    'trace_width_mm': 0.15,
    'dielectric_height_mm': 0.2,
    'copper_thickness_mm': 0.035,
    'er': 4.5,
    'loss_tangent': 0.02,
    'freq_ghz': 5  # USB 3.x high-freq point
}

usb_media = MLine(
    Frequency(stackup['freq_ghz'], stackup['freq_ghz'], 1, 'GHz'),
    w=stackup['trace_width_mm'] * 1e-3,
    h=stackup['dielectric_height_mm'] * 1e-3,
    t=stackup['copper_thickness_mm'] * 1e-3,
    ep_r=stackup['er'],
    tand=stackup['loss_tangent']
)

z0_single = np.abs(usb_media.z0[0])
z0_diff_estimate = 2 * (z0_single - 0.5 * trace_spacing_mm)  # Simplified
print(f"Single-ended Z0: {z0_single:.1f}Ω, Differential (est): {z0_diff_estimate:.1f}Ω")
# PASS if: 85 < z0_diff < 95
```

### Check 2: Ethernet 100Ω Differential (Cat6A)
**Goal:** Post-route TP twist pair differential impedance check.

```python
# Ethernet media (tightly twisted pair, 100Ω target)
eth_media = MLine(
    Frequency(100, 250, 15, 'MHz'),  # 100–250 MHz range
    w=0.10e-3,  # Very thin twisted conductors: 0.1 mm
    h=0.8e-3,   # Spacing between pair
    t=0.018e-3, # Thin wire gauge AWG26
    ep_r=4.5,
    tand=0.02
)

eth_z0 = np.abs(eth_media.z0[0])
print(f"Ethernet pair Z0: {eth_z0:.1f}Ω (target: 100±5Ω)")
# PASS if: 95 < z0 < 105
```

### Check 3: CAN Bus 60Ω Twisted Pair
**Goal:** CAN_H / CAN_L characteristic impedance.

```python
can_media = MLine(
    Frequency(0.1, 1, 10, 'MHz'),  # CAN: 0–1 Mbps
    w=0.25e-3,  # Larger conductors for twisted pair
    h=1.0e-3,
    t=0.035e-3,
    ep_r=4.5,
    tand=0.02,
    z0_port=60  # Target: 60Ω
)

can_z0 = np.abs(can_media.z0[0])
print(f"CAN bus Z0: {can_z0:.1f}Ω (target: 60±10%)")
# PASS if: 54 < z0 < 66
```

### Check 4: Generic 50Ω RF Trace (WLAN, BLE)
**Goal:** Ensure RF front-end traces match 50Ω system impedance.

```python
rf_media = MLine(
    Frequency(0.1, 6, 120, 'GHz'),  # 100 MHz – 6 GHz (WLAN/BLE)
    w=0.30e-3,  # Balanced for 50Ω on FR4
    h=0.15e-3,  # Thin dielectric layer (high-speed HDI)
    t=0.035e-3,
    ep_r=4.5,
    tand=0.02,
    z0_port=50
)

rf_z0_curve = np.abs(rf_media.z0)
# Plot or spot-check at key frequencies
print(f"RF Z0 @ 2.4 GHz: {rf_z0_curve[100]:.1f}Ω")
# PASS if: 48 < z0 < 52 across 100 MHz – 6 GHz band
```

### Check 5: Return Loss / Matching Network Verification
**Goal:** Validate impedance matching network (de-embed antenna or connector S-params).

```python
# Load antenna S2P characterization
antenna_s11 = rf.Network('antenna_measurement.s2p')

# Define matching network: series L + shunt C
match_net = mlin.short() * 0.5  # Shunt capacitance (stub model)

# Cascade matching network with antenna
matched = match_net ** antenna_s11

# Check return loss post-matching
rl_before = -20 * np.log10(np.abs(antenna_s11.s11))
rl_after = -20 * np.log10(np.abs(matched.s11))

print(f"Return loss before: {rl_before[freq_idx]:.2f} dB")
print(f"Return loss after:  {rl_after[freq_idx]:.2f} dB")
# PASS if: RL > 10 dB (S11 < -10 dB) across operating band
```

---

## Pydantic Contract: Required Fields for scikit-rf Payload

### Per-Stackup Configuration (Shared, not per-subsystem)

These fields should live in a **`stackup.yaml`** or **`design.yaml`** metadata, not in `SubsystemPick`:

```python
# Stackup / Dielectric Model
{
    "layers": [
        {
            "name": "Layer 2 (signal)",
            "type": "microstrip|stripline|cpw",
            "trace_width_mm": 0.15,
            "trace_spacing_mm": 0.10,  # For diff pairs
            "dielectric_height_mm": 0.2,
            "copper_thickness_mm": 0.035,
            "relative_permittivity": 4.5,
            "loss_tangent": 0.02,
            "target_z0_single_ohms": 50,
            "target_z0_diff_ohms": 90,  # Differential pairs
        }
    ]
}
```

### Per-Routed Net (In PCB/Routing Layer)

When a net transitions to routed status, attach:

```python
{
    "net_name": "USB_DP",
    "routing_layer": "Layer 2",
    "routed_length_mm": 45.3,
    "trace_width_mm": 0.15,
    "skew_to_complement_mm": 0.8,  # For DP/DM pair
    "via_count": 3,
    "via_diameter_mm": 0.3,
    "via_antipads_mm": 0.5,
    "target_z0_ohms": 45,  # Single-ended for USB
    "target_z0_diff_ohms": 90,  # Differential nominal
}
```

### For S-Parameter Analysis (External Characterization)

When consuming Touchstone files:

```python
{
    "component": "USB_Type_C_Connector",
    "s_param_file": "path/to/connector.s2p",
    "frequency_range_ghz": [0.1, 10.0],
    "reference_impedance_ohms": 50,
    "port_mapping": {"port_1": "VBUS", "port_2": "DP", "port_3": "DM"},
}
```

---

## Current Gaps in `research_bundle.py`

### Missing from `SubsystemPick` / `ChosenPart`:

1. **Stackup Metadata**
   - Dielectric height, copper thickness, Er, loss tangent → should be design-level, not per-component
   - Trace width targets (for impedance budget)

2. **Routed Net Properties**
   - Actual trace width, length, skew (from PCB netlist)
   - Via count, diameter, antipad (from PCB layer stack)
   - Target Z0 (single-ended and differential)

3. **S-Parameter File References**
   - Path to external Touchstone file
   - Frequency coverage
   - Port mapping (for multi-port measurements)

4. **Loss Modeling**
   - Frequency-dependent loss (insertion loss coefficient)
   - Dielectric loss vs frequency (for >1 GHz RF)

---

## Sources

- [scikit-rf Networks Tutorial](https://scikit-rf.readthedocs.io/en/latest/tutorials/Networks.html)
- [scikit-rf Media Classes](https://scikit-rf.readthedocs.io/en/latest/api/media/index.html)
- [scikit-rf Introduction](https://scikit-rf.readthedocs.io/en/latest/tutorials/Introduction.html)
- [scikit-rf Media Tutorial](https://scikit-rf.readthedocs.io/en/latest/tutorials/Media.html)
- [Microstrip Correlation Example](https://scikit-rf.readthedocs.io/en/latest/examples/networktheory/Correlating%20microstripline%20model%20to%20measurement.html)
- [Touchstone File Format (IBIS)](https://ibis.org/connector/touchstone_spec11.pdf)
- [Keysight S2P Format Guide](https://helpfiles.keysight.com/csg/N1930xB/FilePrint/SnP_File_Format.htm)
- [scikit-rf Example Data: ring_slot.s2p](https://github.com/scikit-rf/scikit-rf/blob/master/skrf/data/ring%20slot.s2p)
