# `stepper_driver` — investigation

**Category:** `stepper_driver` · **Status:** READY · **Chosen part:** **TMC2209-LA-T** (`C2150710`, TRINAMIC) — $2.68 @ 17,750 stock

Stepper motor driver with UART control interface (two instances on board, shared UART bus with MS1/MS2 strap pins for address 0/1 selection) delivering 11.1 V @ 1 A RMS to dual lead-screw steppers.

## Why this part won

- **UART dynamic current control** enables firmware to adjust motor current on-the-fly for teaching (torque/energy demos), not locked to fixed sense resistor like basic drivers. DRV8825 lacks UART entirely.
- **256-microstep maximum** far exceeds 16-step preference, enabling smooth lead-screw motion at ultra-low speeds (ideal for precision positioning demos) while maintaining ~1 A torque margin.
- **StealthChop PWM mode** delivers near-silent operation in classroom — critical for teaching without auditory distraction. DRV8824/8825 produce audible high-frequency switching.
- **2.0 A continuous rating** (actuals) provides 2× safety margin over 1.0 A RMS requirement. Sense resistor math: IRMS = Vref / (2 × RSENSE). At Vref = 0.31 V and RSENSE = 110 mΩ, IRMS ≈ 1.41 A nominal; firmware sets lower via UART for efficient operation.
- **Dual-instance UART addressing:** MS1 (pin 27) and MS2 (pin 28) strapped to GND (address 0) and VCC (address 1) respectively, sharing single PDN_UART + 22 kΩ pull-up. Each instance independently configurable without separate SPI/I2C lines.

## Alternatives considered

| Part | LCSC | Verify result | Reason rejected |
|------|------|---------------|-----------------|
| DRV8824PWPR | C86252 | PASS | Cheaper ($1.00) but 1.6 A output cap vs 2.0 A needed for margin; no UART reduces teaching value — relegated to fallback option only. |
| DRV8825PWPR | C81582 | PASS | Mid-tier ($2.04), higher 2.5 A output, but lacks UART and quiet modes. TMC2209 UART + StealthChop justify $0.64 premium for classroom. |

## Tradeoffs accepted

- **UART address strapping:** MS1/MS2 hard-strapped (no dynamic switching), requires jumpers or dedicated pull-up/GND trace routing per instance. Acceptable for fixed dual-motor configuration; future boards could use SMD 0-Ω resistors for address flexibility.
- **Sense resistor per-instance:** 110 mΩ external RSENSE (1206 package, 1 W rated) required for current limiting; UART-set Vref allows software tuning without resistor swap. Small BOM footprint per motor.
- **Shared UART noise sensitivity:** Two drivers on single 22 kΩ pull-up + PDN_UART net require ~100 nF + 10 µF bulk caps at each instance (44 nF total per channel). Acceptable given low switching frequency (~16 kHz typical) and short PCB runs anticipated in teaching cart.

## Hidden BOM per instance

- **VCC_IO decoupling:** 0.1 µF ceramic + 4.7 µF MLCC (tandem bypass near pin 4/23)
- **VS (motor supply) decoupling:** 0.1 µF ceramic + 100 µF bulk cap (tandem on VS rail, solid ground plane under thermal pad)
- **RSENSE:** 110 mΩ (1206, 1 W rated) on GND pin 26 for 1 A RMS setting
- **PDN_UART pull-up:** 22 kΩ resistor to VCC (shared across both instances); single 100 nF cap at first instance, 10 µF bulk at second
- **MS1/MS2 strap resistors:** 10 kΩ pull-up to VCC (for logic high, address 1) or short to GND (address 0)

## Requirements vs actuals

| Field | Required | Actual | Unit |
|-------|----------|--------|------|
| `channels` | 1 | 1 | — |
| `current_per_phase` | 1.0 | 2.0 | A |
| `motor_voltage` | 11.1 | 4.75–29.0 | V |
| `microstepping` | 16 | 256 | — |
| `control_interface` | step_dir | [step_dir, uart] | — |
| `rdson_mohm` | — | 340 | mΩ |
| `ilim_method` | — | sense_resistor | — |
| `has_thermal_pad` | — | true | — |
| `package` | [QFN, DFN, TSSOP-16-EP, HTSSOP-28] | QFN-28-EP(5×5) | — |
| `stock` | — | 17,750 | pcs |

## Schematic

![stepper_driver schematic](./schematic.png)

---
*Investigation grounded in JLCPCB search + verify_candidate. Captured from `subsystems/stepper_driver.json` decisions[-1] at 2026-05-14.*
