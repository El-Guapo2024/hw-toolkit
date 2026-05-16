# Teaching Car v1

**Purpose:** Small indoor teaching/programming robotics platform. Lab-based curriculum: line-following, PID, odometry, sensor fusion, pick-and-place. Untethered, battery-powered, swappable top-mount tools.

**Build qty:** 1 prototype, hand-assembly
**Cost target:** Cost-aware, quality-first ("no cheap sensors" — but cheapest-good-enough where teaching value doesn't suffer)
**Runtime target:** ≥30 min per lab session

## Power
- **Source:** 3S Li-ion 18650 protected pack, 11.1V nom (9.0–12.6V), ~2500mAh, external balance charger (no on-board charge IC)
- **Rails:**
  - **5V @ ~3A** — 2× servos, line-follow array (5ch), RGB LED, 5V sensor loads
  - **3.3V @ ~1A** — MCU, IMU, ToF, logic
  - **VBAT direct** — 4× DC motor driver, 2× stepper driver

## Subsystems

| Name | Category | Key requirements |
|------|----------|------------------|
| `power_input` | connector + protection | XT30 or barrel, reverse-polarity FET, fuse, bulk cap. Battery from external protected 3S pack. |
| `buck_5v` | buck_converter | Vin 9–12.6V → 5V @ 3A min, sync buck, low ripple (servos sensitive to noise) |
| `ldo_3v3` | ldo | Vin 5V → 3.3V @ 1A. LDO acceptable (only 1.7V drop × 1A = 1.7W — manageable with thermal pad) |
| `mcu_esp32` | mcu_module | **ESP32 family**, Wi-Fi + BLE, ≥20 GPIO free after peripherals, hardware PCNT/encoder support for 4× quadrature, ≥4 PWM channels (2 servos + others), I²C, UART for stepper drivers if needed |
| `imu_9dof` | sensor | 9-DoF (accel + gyro + mag), I²C, learning-grade quality |
| `tof_distance` | sensor | I²C ToF distance sensor, ≥1m range, single forward-facing |
| `line_array` | sensor | IR reflectance array, **5 channels**, analog or digital output, bottom-mounted forward |
| `bump_switches` | input | **2× tactile bumper switches**, front-corners, simple GPIO + pull-up + debounce cap |
| `dc_driver` | motor_driver | **4× DC channels**, ~500mA–1A per channel, PWM speed control, direction control, encoder pass-through compatible. Single 4-ch IC or 2× dual-H-bridge. |
| `dc_motors_encoders` | actuator+sensor | 4× DC gear-motor with **built-in 2-channel quadrature encoder** (motor MPN picked in research, e.g. TT-class or N20 metal-gear with encoder). Connectors on board only — motors live off-board. |
| `stepper_driver_x` | stepper_driver | Bipolar stepper driver, microstepping, current programmable, hold-off-supported. Vin from VBAT. |
| `stepper_driver_y` | stepper_driver | Same as X. |
| `servo_header` | passive | **2× 3-pin servo headers**, 5V rail, PWM from MCU. Bulk cap near header for inrush. |
| `rgb_status` | led | Single addressable RGB (WS2812 / SK6812 class) — status indicator, 5V or 3.3V variant |
| `expansion_conn` | connector | Top-stage tool I/O. Pins: VBAT, 5V, 3V3, GND (×2), I²C (SDA, SCL), 2× GPIO, RESET. Pin header or modular connector. |

## Mechanical
- **Footprint:** ≥220mm × 220mm (derived from 200mm X/Y top-stage envelope)
- **Height:** TBD — driven by stepper Z-stack + claw clearance
- **Mounting:** standard M3 holes, pattern TBD
- **Top-stage interface:** lead-screw X/Y mounts on chassis, expansion connector cutout on PCB

## Top-stage / claw (mechanical separate, electrical only on this board)
- **X-axis:** lead-screw, 200mm travel, slow-precise, hold-off
- **Y-axis:** lead-screw, 200mm travel, slow-precise, hold-off
- **Payload:** 500g max on stage
- **Claw:** 2× servos (grip + wrist) — mechanical separate, electrical via `servo_header`
- **Electrical interface:** `expansion_conn`

## Open questions / assumptions
- **Bump switch count** — assumed 2 front-corner. Confirm or 4.
- **PCB exact form factor** — chassis design will set this; assume 220×220mm baseline.
- **Top-stage chassis design** — out of scope for this board; this PCB only exposes electrical interface.
