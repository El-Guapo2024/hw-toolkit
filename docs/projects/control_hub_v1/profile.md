# control_hub_v1

**Purpose:** Multi-actuator control hub for a teaching car / robot platform.
**Build qty:** 1–5 units (prototype, hand assembly)
**BOM ceiling:** $50–$60 per unit (PCB + ICs + passives; excludes off-board motors/servos)
**Assembly:** hand-solder, no JLC turnkey

## Power source
- 3S Li-ion (11.1 V nom, 12.6 V max, ~9 V cutoff)
- Vin range to board: 9.0 V – 12.6 V
- 6 A glass-fuse + holder (TH) at input

## Loads (spec — drives rails downstream)

### Actuators
| name | count | Vnom | I_cont | I_peak | notes |
|------|-------|------|--------|--------|-------|
| dc_motor | 4 | 6 V | 0.3 A | 0.6 A | N20 6V geared, stall ≈ 0.6 A |
| servo | 4 | 5 V | 0.4 A | 1.2 A | MG90S class, metal-gear hobby |
| stepper | 2 | 11.1 V (VBAT direct) | 0.8 A | 1.4 A | NEMA17 via TMC2209-LA, 1/16 microstep |

### Sensors
| name | bus | addr | count | Vsupply | notes |
|------|-----|------|-------|---------|-------|
| imu_6dof | I²C | 0x6A | 1 | 3V3 | LSM6DSOXTR (DK OOS on -TR variant; LSM6DSOXTR ok) |
| as5600 | I²C | 0x36 | 4 | 3V3 | shared 0x36 → behind TCA9548A mux |
| tca9548a | I²C | 0x70 | 1 | 3V3 | I²C mux for 4× AS5600 |
| vl53l0x | I²C | 0x29 | 1 | 3V3 | ToF distance, bare IC on-board |
| tcrt5000 | analog | — | 5 | 5V | line array on-PCB |

### MCU / brain
- ESP32-S3-WROOM-1-N16R8 (Wi-Fi + BLE, native USB, 16 MB flash, 8 MB PSRAM)
- GPIO budget rough:
  - 4× PWM/DIR for DC motors (8 pins)
  - 4× PWM for servos (4 pins)
  - 2× STEP/DIR for steppers (4 pins) + 1× UART for both (shared 2 pins)
  - 1× I²C bus (SDA/SCL) → IMU + ToF + TCA9548A → AS5600 ×4
  - 5× analog for TCRT5000 (5 pins)
  - 1× GPIO for WS2812B data
  - 1× ADC for VBAT monitor (divider)
  - UART for debug + USB-C native
  - Total ≈ 30 GPIOs used, fits ESP32-S3 budget

### Status / misc
- WS2812B × 1 (status RGB on-board)
- USB-C (data + alt power-in for bench / programming)
- VBAT divider → ESP32-S3 ADC for battery monitor

## Rail load tally (preliminary — /designer sizes converters)

| rail | derived from | I_continuous | I_peak | margin policy | spec target |
|------|--------------|--------------|--------|---------------|-------------|
| **VBAT (11.1 V)** | 2× TMC2209 (1.6 A cont + headroom) | 1.6 A | 2.8 A | 1.5× | fuse ≥ 6 A (TH glass) |
| **6 V buck** (motors) | 4× N20 @ 0.3 A cont / 0.6 A peak | 1.2 A | 2.4 A | 2× (motor) | **buck ≥ 5 A** |
| **5 V buck** (servos + analog 5V) | 4× MG90S @ 0.4 A cont / 1.2 A peak; + TCRT5000 LEDs ≈ 50 mA | 1.7 A | 4.9 A | 2× (motor-class load) | **buck ≥ 6 A** |
| **3V3 LDO/buck** (MCU + sensors) | ESP32-S3 ≈ 355 mA TX peak + LSM6DSOX 3 mA + AS5600 ×4 ≈ 26 mA + TCA9548A 0.08 mA + VL53L0X 20 mA | 0.45 A | 0.55 A | 1.5× | **≥ 700 mA** |

**Notes on policy:** 2× margin on motor / motor-class loads (per `feedback-load-first-design-order`). 1.5× on logic / sensor / continuous loads.

## Mechanical
- Outline: **deferred to /pcb**
- Mounting pattern: **deferred to /pcb**
- Connector positions: **deferred to /pcb**
- Antenna keepout: required (ESP32-S3-WROOM-1 has on-module PCB antenna — needs clear keepout per datasheet)

## Locked MPNs (templated parts + spec stage decisions)

| subsystem | MPN | source | $1pc | stock | notes |
|-----------|-----|--------|------|-------|-------|
| mcu | ESP32-S3-WROOM-1-N16R8 | DK 5407-…-TR-ND | $6.76 | 10,753 | Active |
| stepper_driver | TMC2209-LA | DK 505-TMC2209-LA-ND | $5.36 | 1,418 | Active, cut-tape qty 1 |
| imu_6dof | LSM6DSOXTR | DK / JLC C481766 | $3.19 | 4,809 (JLC) | DK -TR OOS, use -X variant |
| encoder | AS5600-ASOT | DK AS5600-ASOTTR-ND | $3.19 | 6,726 | × 4 |
| i2c_mux | TCA9548APWR | DK 296-34905-2-ND | $1.36 | 31,673 | TSSOP-24 hand-solderable |
| tof | VL53L0CXV0DH/1 | DK 497-16538-2-ND | $5.39 | 40,387 | bare IC on-board |
| status_led | WS2812B-B/T | JLC C2761795 | $0.10 | 368k | dirt-cheap |
| line_sensor | TCRT5000 | (off-board common) | ~$0.30 | n/a | analog reflective |
| fuse | 6 A glass + TH holder | (generic) | — | — | input protection |

## To research in /designer (no MPN yet)

1. **buck_6v** — 11.1 V → 6 V, ≥ 5 A. Candidate: TPS54620 family (logged earlier under wrong doctrine — re-verify Pass 1).
2. **buck_5v** — 11.1 V → 5 V, ≥ 6 A. Higher than buck_6v. Same family or step up.
3. **ldo_3v3** (or buck_3v3) — 5 V → 3V3 (more efficient than VBAT → 3V3), ≥ 700 mA.
4. **dc_driver** — 4× channel motor driver. Candidates: DRV8833 (dual H-bridge, ×2 → 4 motors), TB6612FNG (dual H-bridge, ×2 → 4 motors), or single 4-channel.
5. **servo_header** — no driver IC needed. 4× 3-pin headers (GND/5V/PWM) + decoupling. /pcb concern more than /designer.
6. **power_switch** — reverse-polarity + soft on/off. P-FET ideal-diode topology candidate.
7. **vbat_monitor** — resistor divider into ESP32-S3 ADC. Trivial.

## Open assumptions / deferred decisions

- LSM6DSO**X**TR chosen over LSM6DSO**TR** because DK is OOS on plain -TR. -X variant adds embedded ML core, otherwise compatible.
- BNO055 (9-DoF onboard fusion) **rejected** earlier in intake — user noted 9-DoF uncommon now. 6-DoF (gyro + accel) only. Heading from LSM6DSOX + magnetometer-less estimator.
- AS5600 has fixed I²C address 0x36 → forces TCA9548A mux for ≥ 2 encoders. 4 used here.
- TCRT5000 is reflective IR pair (LED + phototransistor). Each draws ~20 mA on its IR LED (≈ 100 mA total on 5V from 5 channels). Captured in 5V rail tally.
- Buck topology for 5V and 6V rails likely two separate buck ICs (cheaper + simpler than single dual-output).
- All deferred-to-/pcb mechanical items are non-blocking for /designer.

## Doctrine reminders applied

- Load-first: loads picked above; rails derived in tally table.
- Pass 1 = no math: /designer copies datasheet typical-application BOM. /designer-math runs averaged-model verify after.
- Digi-Key primary: stock checks above pulled from DK first; JLC second.
- Narration one-at-a-time: /spec intake was iterative across this session.

## Next stage

Run `/designer` to:
- Pick MPNs for buck_6v, buck_5v, ldo_3v3, dc_driver, power_switch.
- Stock-verify each at DK (then JLC fallback).
- Capture datasheet typical-app BOM per subsystem (parts-specker sub-agent).
- Update designer-mcp `design.yaml` SoT.
- Hand off to `/designer-math` for averaged-model verification.

**SoT:** this `profile.md` + designer-mcp `design.yaml` (once MCP reconnects, run `subsystem_add` per row above).
