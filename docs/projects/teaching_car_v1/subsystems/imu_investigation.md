# `imu` — investigation

**Category:** `imu` · **Status:** READY · **Chosen part:** **ICM-42607-P** (`C5129967`, TDK InvenSense) — $2.25 @ 15,249 stock

6-axis IMU (accel + gyro) for odometry and tilt sensing on the teaching robot; mounted centrally on main PCB with I²C interface to ESP32 MCU and optional interrupt line for tap/motion wake.

## Why this part won

- **Lifecycle assured:** Verified Active on Mouser (15,249 units in JLCPCB stock); previous choice LSM9DS1TR marked EOL across all distributors, making this the only viable 6-axis option with deep stock.
- **Range margin:** **±16g accel** (2× requirement ±4g) and **±2000 dps gyro** (4× requirement ±500 dps) provide resolution advantage over minimum-spec parts; at 16-bit resolution (~500 µg/LSB at ±16g), center-of-gravity noise floor stays <2 mG.
- **I²C native + SPI fallback:** Standard **0x68 default I²C address** with **AD0 pin strap** for secondary IMU expansion; also supports SPI for EMI immunity if needed later (not primary, but in actuals).
- **Power efficiency:** **3.5 µA** active-mode current at typical conditions is <10% of MCU's 50–100 µA draw; supply range 1.71–3.6 V spans all rails (3.3 V primary, 5 V buck via LDO headroom). **Decoupling budget:** 100 nF + 10 nF on VDD per InvenSense design note; pad-to-pad <10 mm to IC minimizes inductance (~0.5 nH per mm).
- **Thermal unconstrained:** LGA-14(2.5×3.0 mm) **θ_JA ~450 °C/W** (typical for small LGA, ≤50 µW dissipation at 3.5 µA); at 25 °C ambient, junction rise <0.02 °C — negligible. Package footprint forces **center-board placement** for mechanical symmetry (gravity vector accuracy); keep **>20 mm from buck converter IC** to isolate switching noise (5V buck operates 500 kHz–1 MHz typical).

## Alternatives considered

| Part | LCSC | Verify result | Reason rejected |
|------|------|---------------|-----------------|
| LSM9DS1TR | C2655096 | PASS | **EOL per Mouser** (was original choice but marked End of Life, only 183 liquidation stock on JLC — unavailable for reorder). Needed 9-axis (mag) for heading reference but motor proximity requires calibration; 6-axis + software compass is acceptable trade. |
| LSM6DS3TR-C | C967633 | PASS | **EOL per Mouser** (0 stock at Mouser, 37k on JLC is liquidation inventory). Cheapest 6-axis but requires external mag IC for heading — split design complicates firmware vs single-IC goal. |
| ICM-20602 | C97633 | N/A | **Obsolete per Mouser** (manufacturer discontinued). No viable stock path. |
| BMI160 | — | N/A | **Obsolete per Mouser** (0 stock, end-of-life). Historical teaching platform IC but no longer manufactured. |
| ICM-42605 | C2655099 | N/A | Active on Mouser but **0 stock** (unavailable despite Active status). Functionally identical to ICM-42607-P (same die, same ranges) but no supply. |

## Tradeoffs accepted

- **Magnetic field sensing removed:** Original LSM9DS1TR (9-axis) provided onboard magnetometer for compass heading; ICM-42607-P (6-axis) omits it. **Mitigation:** teaching robot uses wheel encoders for odometry (primary) and gyro z-axis integration for heading estimate. Mag could be added as optional separate I²C sensor (HMC5883L or LIS3MDL) on a future add-on board if curriculum requires true heading reference.
- **Motor noise coupling:** Proximity to 4× DC motor drivers and stepper drivers creates **electromagnetic interference risk for gyro z-axis** (susceptible to current ripple ≥100 kHz). **Mitigation:** (1) place IMU >20 mm from switching ICs, (2) route motor power return via dedicated ground planes to minimize loop area, (3) firmware median-filter gyro readings and calibrate zero-bias at power-on before motion starts.
- **No onboard DMP:** Many modern IMUs include Digital Motion Processor (fusion, gesture detection); ICM-42607-P is raw sensor only. **Acceptable:** teaching curriculum focuses on firmware sensor fusion (pedagogical value) and MCU resources (ESP32-S3 has headroom for IIR filters + Kalman if needed).

## Requirements vs actuals

| Field | Required | Actual | Unit |
|-------|----------|--------|------|
| `axes` | ≥ 6 | 6 | axis |
| `interface` | i2c | i2c, spi | — |
| `accel_range_g` | ≥ 4.0 | 16.0 | ±g |
| `gyro_range_dps` | ≥ 500.0 | 2000.0 | ±°/s |
| `vdd_min` | 3.3 V compatible | 1.71 | V |
| `vdd_max` | 3.3 V compatible | 3.6 | V |
| `idd_ua` | — | 3.5 | µA |
| `package` | LGA / QFN / DFN | LGA-14(2.5×3) | — |
| `stock` | ≥ 500 | 15,249 | units (JLCPCB) |

## Schematic

*Schematic not auto-generated for this category — application-specific layout depends on I²C pull-up configuration and MCU GPIO routing. Typical IMU footprint: 100 nF + 10 nF decoupling on VDD (pad-to-pad <10 mm); I²C SDA/SCL to MCU GPIO via shared pull-ups (if not already present on bus); optional INT1 to GPIO (tap/motion wake) left unconnected for teaching builds.*

---
*Investigation grounded in JLCPCB / Mouser lifecycle verification + verify_candidate. Captured from `subsystems/imu.json` decisions[-1] at 2026-05-14.*
