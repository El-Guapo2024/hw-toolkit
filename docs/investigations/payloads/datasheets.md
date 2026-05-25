# Datasheet Spec Field Analysis

**Goal:** Validate that `SubsystemPick.actuals` dict captures the right fields per component category so `pcb-designer` has all necessary data for placement/routing/SPICE.

---

## 1. Buck Converter / Switching Regulator

**Example part:** TI LMR14050 (40V, 5A step-down)

### Spec table fields extracted:
- **Topology:** buck
- **VIN (Input Voltage):** min=4V, max=40V
- **VOUT (Output Voltage):** min=1V, max=36V (adjustable via FB divider)
- **IOUT max (Output Current):** 5A
- **Switching Frequency:** 200–2500 kHz (programmable)
- **Duty Cycle max:** 98%
- **Quiescent Current (Iq):** 40µA typical
- **Shutdown Current:** 1µA
- **Operating Temperature:** -40°C to +125°C
- **Control Mode:** current mode
- **Package:** (from JLCPCB context) SOIC-8-EP, TSSOP-14, etc.
- **Thermal properties:** θJA (depends on package, typically 40–50°C/W for small packages)

### Fields not in spec table but critical for design:
- **Reference voltage (Vref):** ~0.6V (for feedback divider math)
- **Error amplifier gain (gm):** affects loop stability
- **Switching frequency tolerance:** affects inductor ripple calculation
- **PCB thermal pad size:** affects thermal performance

### Machine-extractable?
- ✅ Numeric ranges: VIN, VOUT, IOUT, fsw, temp range
- ⚠️ Partial: Control mode (usually text description, needs keyword match)
- ❌ Thermal properties: Often require detailed reading of thermal sections

---

## 2. LDO (Low-Dropout Regulator)

**Example parts:** AMS1117 (fixed 3.3V/5V, 1A), TLV757, NCP1117

### Spec table fields extracted (from JLCPCB AMS1117-3.3):
- **VIN (Input Voltage):** 15V max (varies by variant; some 12V, some 18V)
- **VOUT (Output Voltage):** fixed 3.3V / 5V / adjustable via divider
- **IOUT max:** 1A
- **Voltage Dropout (Vdo):** 1.1–1.3V @ rated current
- **Quiescent Current (Iq):** 4–5mA standby
- **PSRR (Power Supply Rejection Ratio):** 60–72 dB @ 120Hz
- **Output Noise:** 0.003% Vout (typical)
- **Operating Temperature:** -40°C to +125°C
- **Output Type:** fixed or adjustable
- **Package:** SOT-223, SOT-89, etc.
- **Features:** over-current protection, thermal shutdown, short-circuit protection

### Fields critical for placement/SPICE:
- **Output Current (Iout):** needed for thermal analysis
- **Dropout voltage:** determines minimum VIN required
- **PSRR:** affects power-supply-coupled noise
- **Thermal resistance (θJA):** typically 60–100°C/W for SOT-223

### Machine-extractable?
- ✅ All numeric: Vin, Vout, Iout, dropout, noise, PSRR, temp range
- ✅ Package type
- ⚠️ Partial: Protection features (text parsing)

---

## 3. MCU (Microcontroller)

**Example:** STM32G031F4 (ARM Cortex-M0+, 64MHz, 16KB flash)

### Spec table fields extracted (from JLCPCB):
- **CPU Core:** ARM Cortex-M0+
- **CPU Speed max:** 64MHz
- **Program Memory (Flash):** 16KB
- **RAM (SRAM):** 8KB
- **EEPROM:** — (not all variants have it)
- **GPIO count:** 18
- **ADC:** 12-bit, channel count varies
- **Supply Voltage:** 1.7V–3.6V (wide range typical for M0/M0+)
- **Operating Temperature:** -40°C to +85°C
- **Package:** TSSOP-20, LQFP48, BGA, etc.
- **Oscillator:** built-in + external support
- **Peripherals:** UART, SPI, I2C, etc. (count varies)

### Fields needed for design:
- **GPIO count:** determines feasibility of pin-limited designs
- **Peripheral interfaces:** SPI/I2C/UART counts (bus topology planning)
- **ADC specs:** channels, resolution, sampling rate (for sensor integration)
- **Power domains:** most M0+ are single supply, but some have analog iso
- **Pin assignment:** exact pin locations for layout (available from LQFP/BGA mapping)
- **Max current per pin / total:** affects decoupling strategy

### Machine-extractable?
- ✅ All top-level specs: clock speed, memory, GPIO, ADC
- ✅ Package type
- ⚠️ Partial: Peripheral inventory (datasheet p.2–3, text-heavy)
- ❌ Detailed pin current limits: rarely in spec summary, requires electrical section

---

## 4. Sensor IC

**Example parts:** BMI270 (6-axis IMU), BME280 (environmental), BME680 (gas+environmental)

### Spec table fields extracted (from JLCPCB BMI270):
- **Sensor Type:** IMU (accel + gyro)
- **Acceleration Range:** ±16g
- **Gyroscope Range:** ±2000°/s
- **Resolution:** 16-bit output
- **Measurement Axes:** X, Y, Z (3-axis)
- **Interface:** SPI, I2C
- **Supply Voltage:** 1.71V–3.6V
- **Standby Current:** 3.5µA
- **Operating Temperature:** -40°C to +85°C
- **Cache/FIFO:** 2KB
- **Package:** LGA-14(2.5×3)
- **Features:** motion event detection, sync, timestamp

### Related fields from BME280/BME68x (recommended by sensor_recommend):
- **Measurands:** temperature, pressure, humidity (+ gas for BME68x)
- **Resolution:** varies by sensor
- **I2C address:** typically 0x77 or 0x76
- **Current draw:** µA range (critical for battery-powered designs)

### Machine-extractable?
- ✅ All numeric: range, resolution, voltage, current, temp
- ✅ Interface type (I2C/SPI)
- ✅ Pin count
- ⚠️ Partial: Advanced features like FIFO behavior (prose in detailed sections)

---

## 5. Motor Driver

**Example:** DRV8833 (dual H-bridge, 2A peak)

### Spec table fields available (from datasheet):
- **VIN (Motor Supply):** 2.7–10.8V (dual H-bridge variants vary)
- **IOUT max (Peak):** 2A per channel, 3.2A total (DRV8833)
- **IOUT continuous:** ~1.5A per channel (thermal limited)
- **RDS(on) (on-state resistance):** ~4Ω high-side, ~5Ω low-side (thermal)
- **Control Interface:** parallel (IN/PWM pins) or serial (UART/I2C variants)
- **Decay Modes:** fast/slow (architecture-dependent)
- **Logic Supply Voltage:** 2.7V–5.5V
- **Supply Current (no-load):** ~5mA typical
- **Operating Temperature:** -40°C to +125°C
- **Package:** VQFN-24, SOIC-28, etc.
- **Thermal Pad:** critical for dissipation

### Fields critical for placement:
- **IOUT max / continuous:** determines heatsinking
- **RDS(on):** affects power loss and thermal design
- **Control pin count:** affects PCB routing (3–12 pins for various modes)
- **Thermal resistance (θJA):** ~40–60°C/W depending on package

### Machine-extractable?
- ✅ All numeric: Vin, Iout, RDS, temp
- ✅ Package type
- ⚠️ Partial: Decay mode options (text in control section)
- ❌ Detailed control pin timing: datasheet appendix, not in spec table

---

## 6. MOSFET (Discrete Transistor)

**Example:** AO3400A (N-channel, 30V, 5.7A)

### Spec table fields extracted (from JLCPCB AO3400A):
- **Type:** N-channel (or P-channel)
- **VDS (Drain-Source Voltage):** 30V max
- **VGS (Gate-Source Voltage):** ±20V typical
- **Vgs(th) (Gate Threshold):** 1.45V
- **ID (Continuous Drain Current):** 5.7A @ 25°C
- **RDS(on) (Drain-Source On-resistance):** 48mΩ @ VGS=2.5V (temperature-dependent)
- **Qg (Gate Charge):** 7nC @ VGS=10V
- **Ciss (Input Capacitance):** 630pF
- **Crss (Reverse Transfer Capacitance):** 50pF
- **Coss (Output Capacitance):** 75pF
- **Pd (Power Dissipation):** 1.4W @ 25°C (derate with temp)
- **Operating Temperature:** -55°C to +150°C
- **Package:** SOT-23, TO-252, DPAK, etc.

### Fields critical for switching design:
- **RDS(on):** determines conduction loss (highly temperature-dependent)
- **Vgs(th):** determines minimum gate drive voltage
- **Qg:** affects gate drive requirements and switching speed
- **Capacitances (Ciss, Crss, Coss):** affect switching speed and EMI
- **θJA / thermal pad area:** determines thermal margin

### Machine-extractable?
- ✅ All numeric: Vds, Id, RDS, Qg, capacitances, Vgs(th), temp range
- ✅ Package type
- ✅ Operating temperature range
- ⚠️ Partial: Temperature derating curves (graphs in detailed section)

---

## Common Fields Across All Categories

| Field | Type | Extractable? | Notes |
|-------|------|--------------|-------|
| **Package** | string | ✅ | Always listed; critical for footprint selection |
| **Supply Voltage (Vin/Vcc)** | range | ✅ | Numeric min/max |
| **Output Current (Iout/Id)** | float (A) | ✅ | Max and continuous often differ |
| **Operating Temperature** | range | ✅ | Min/max in °C |
| **Operating Temp derating** | curve | ⚠️ | Usually graphs; needs careful reading |
| **Datasheet URL** | string | ✅ | Can be captured at query time |
| **Manufacturer** | string | ✅ | JLCPCB provides |
| **LCSC / MPN** | string | ✅ | Sourcing identifier |
| **Stock / Price** | int / float | ✅ | JLCPCB provides |
| **Thermal (θJA)** | float (°C/W) | ⚠️ | Often buried in thermal section; package-dependent |

---

## Category-Specific Fields

### Buck Converter `actuals`:
```python
{
    "vin_min": 4.0,           # V
    "vin_max": 40.0,          # V
    "vout_min": 1.0,          # V
    "vout_max": 36.0,         # V
    "iout_max": 5.0,          # A
    "fsw_khz": 500,           # kHz (typical operating point)
    "fsw_min": 200,           # kHz
    "fsw_max": 2500,          # kHz
    "efficiency": 85.0,       # % (at nominal Vin/Iout)
    "iq_ma": 0.04,            # mA quiescent
    "vref": 0.6,              # V (feedback divider reference)
    "control_mode": "current_mode",  # or "voltage_mode"
    "package": "SOIC-8-EP",
    "theta_ja": 50.0,         # °C/W
}
```

### LDO `actuals`:
```python
{
    "vin_max": 15.0,          # V
    "vout": 3.3,              # V (fixed; use for adjustable too)
    "iout_max": 1.0,          # A
    "dropout_vdo_mv": 1100,   # mV @ Iout
    "iq_ma": 5.0,             # mA standby
    "psrr_db": 72.0,          # dB @ 120Hz
    "noise_mv": 0.005,        # mV RMS
    "package": "SOT-223",
    "theta_ja": 100.0,        # °C/W
}
```

### MCU `actuals`:
```python
{
    "cpu_arch": "ARM Cortex-M0+",
    "cpu_speed_mhz": 64,      # MHz max
    "flash_kb": 16,           # KB
    "ram_kb": 8,              # KB
    "gpio_count": 18,
    "adc_bits": 12,
    "adc_channels": 12,
    "vin_min": 1.7,           # V
    "vin_max": 3.6,           # V
    "uart_count": 2,
    "spi_count": 2,
    "i2c_count": 2,
    "package": "TSSOP-20",
}
```

### Sensor IC `actuals`:
```python
{
    "sensor_type": "imu",     # or "environmental", "gas", etc.
    "measurement_range": "±16g",
    "resolution_bits": 16,
    "interface": ["i2c", "spi"],
    "vin_min": 1.71,          # V
    "vin_max": 3.6,           # V
    "iq_standby_ua": 3.5,     # µA
    "iout_active_ma": 10.0,   # mA (typical active)
    "package": "LGA-14",
    "fifo_bytes": 2048,
}
```

### Motor Driver `actuals`:
```python
{
    "vin_min": 2.7,           # V
    "vin_max": 10.8,          # V
    "iout_peak_a": 2.0,       # A per channel
    "iout_continuous_a": 1.5, # A (thermal limit)
    "rdson_high_mohm": 4.0,   # mΩ high-side
    "rdson_low_mohm": 5.0,    # mΩ low-side
    "control_interface": "parallel",  # or "serial"
    "decay_modes": ["fast", "slow"],
    "logic_vin_min": 2.7,     # V
    "logic_vin_max": 5.5,     # V
    "package": "VQFN-24",
    "theta_ja": 50.0,         # °C/W
}
```

### MOSFET `actuals`:
```python
{
    "channel_type": "n",      # or "p"
    "vds_max": 30.0,          # V
    "vgs_max": 20.0,          # V
    "vgs_th": 1.45,           # V threshold
    "id_continuous": 5.7,     # A @ 25°C
    "rdson_at_2v5": 48,       # mΩ @ Vgs=2.5V
    "rdson_at_10v": 30,       # mΩ @ Vgs=10V (typical best case)
    "qg_nc": 7.0,             # nC @ Vgs=10V
    "ciss_pf": 630,           # pF input
    "crss_pf": 50,            # pF transfer
    "coss_pf": 75,            # pF output
    "power_dissipation_w": 1.4,  # W @ 25°C
    "package": "SOT-23",
    "theta_ja": 250.0,        # °C/W (SOT-23)
}
```

---

## Machine-Extractable vs. LLM-Required

### Easily extractable (numeric + standard locations):
- ✅ Supply voltage ranges (Vin/Vcc/Vdd)
- ✅ Output voltage / current ratings
- ✅ Frequency ratings
- ✅ Operating temperature range
- ✅ Package type
- ✅ Pin count
- ✅ LCSC / MPN / manufacturer
- ✅ Stock & pricing (JLCPCB API)

### Requires structured LLM extraction:
- ⚠️ Thermal properties (θJA varies by package; need full thermal section read)
- ⚠️ Efficiency curves (graphs; only point estimates available in tables)
- ⚠️ Control modes / decay modes (prose descriptions)
- ⚠️ Advanced features (detection, timestamps, etc.)
- ⚠️ PSRR / noise specs (sometimes omitted from main table)
- ⚠️ Derating equations (power dissipation vs. temperature)

### Not in datasheets:
- ❌ Expected lifespan / reliability metrics
- ❌ Supplier-specific notes (stock, alternative part numbers)
- ❌ Cost analysis per unit volume
- ❌ Design examples (found in application notes, not spec tables)

---

## Recommendation for `SubsystemPick.actuals`

### Current implementation: `actuals: dict[str, float|int|str]`

**✅ Assessment: Sufficient for v1** if:
1. **Field names are standardized** per category (e.g., `vin_min`, `vin_max`, `iout_max`, not `input_voltage` vs `vin`)
2. **Numeric values have units understood by convention** (V for voltage, A for current, °C for temp, etc.)
3. **Category-specific validators** (pydantic `SubsystemPick` subclasses) enforce required fields per component type

### Future consideration (v2):
Consider **typed sub-models** per category if:
- Validation rules become complex (e.g., "dropout must be < 10% of Vout")
- Automated thermal/efficiency calculations proliferate
- Datasheet extraction tooling matures and needs strict schemas

### Suggested v1 contract:
```python
class SubsystemPick:
    category: str  # "buck_converter", "ldo", "mcu", "sensor_ic", "motor_driver", "mosfet"
    actuals: dict[str, float | int | str | list]  # Per-category field names
    # Validation: category determines which fields are required/optional
```

Add validation logic:
```python
REQUIRED_FIELDS_BY_CATEGORY = {
    "buck_converter": ["vin_min", "vin_max", "vout_min", "vout_max", "iout_max", "fsw_khz", "package"],
    "ldo": ["vin_max", "vout", "iout_max", "dropout_vdo_mv", "package"],
    "mcu": ["cpu_speed_mhz", "flash_kb", "ram_kb", "gpio_count", "package"],
    "sensor_ic": ["vin_min", "vin_max", "interface", "package"],
    "motor_driver": ["vin_min", "vin_max", "iout_peak_a", "package"],
    "mosfet": ["vds_max", "id_continuous", "rdson_at_10v", "package"],
}
```

---

## References

- **JLCPCB:** Real-time spec extraction via `jlc_get_part` / `jlc_search`
- **PCBParts MCP:** sensor_recommend, board_search, board_get (consensus decoupling strategies)
- **Datasheets:** TI LMR14050, DRV8833; AMS AMS1117; Bosch BMI270, BME280; STM32G0 series
- **Design rules:** `get_design_rules` for PCB best practices per component type

---

**Date:** 2026-05-24  
**Maintained by:** Claude Code (hw-toolkit investigation)
