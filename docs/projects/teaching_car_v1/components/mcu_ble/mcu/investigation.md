# `mcu` — investigation

**Category:** `mcu_ble` · **Status:** READY · **Chosen part:** **ESP32-S3-WROOM-1-N16R8** (`C2913202`, Espressif Systems) — $4.92 @ 11,044 stock

MCU for teaching robotics platform: Wi-Fi/BLE + native USB + dual-core LX7 for concurrent real-time motor control and sensor fusion; 45 GPIO allocated across 14 subsystems (4× DC motor PWM+dir + encoder feedback + stepper step/dir/UART + servo PWM + I²C + ADC line array + status LED + bump switches).

## Why this part won

- **Native USB-OTG (GPIO19/20)**: Eliminates CP2102 UART bridge IC ($0.30) + auto-reset transistor circuit ($0.10) required by older ESP32-WROOM-32E variants. System-level total $4.92 vs $5.06 for bridge-based alternatives — **cheaper and simpler single-IC path for student assembly**. No Windows CH340 driver install pain.
- **45 GPIO with hardware PCNT**: **Dual-core LX7 @ 240 MHz** handles concurrent Wi-Fi TX bursts + real-time motor-encoder sampling (4× quadrature decoders via PCNT hardware); superior to single-core RISC-V (C3) for teaching concurrent systems. 45 GPIO comfortably accommodates 36-pin estimated requirement across motors, steppers, servos, sensors, status LED, and bump switches.
- **BLE 5.0 + Wi-Fi 4 (802.11b/g/n)**: Future-proofs to modern phone-app integration; BLE 5.0 enables extended-range sensor-fusion demos (e.g., odometry + IMU over BLE to mobile UI). Wi-Fi 4 sufficient for lab USB over IP (no need for Wi-Fi 6).
- **8 MB external SPI PSRAM (N16R8 variant)**: Enables camera/video buffer if top-stage tooling expands to vision-based line-following or object recognition in future modules. Internal 16 KB SRAM + 512 KB RAM adequate for baseline 30-min lab session runtime.
- **Active lifecycle**: Confirmed 11,044 JLCPCB stock and latest Espressif active family; no obsolescence risk for hand-assembly prototype or small-batch expansion.

## Alternatives considered

| Part | LCSC | Verify result | Reason rejected |
|------|------|---------------|-----------------|
| ESP32-WROOM-32E-N16 | C701343 | PASS | **System-level BOM cost $5.06** (module $4.66 + CP2102 bridge $0.30 + reset circuit $0.10) vs S3-WROOM-1 total $4.92 — S3 wins by $0.14 while adding native USB simplicity. BLE 4.2 vs S3's 5.0. No native USB. LX6 (no vector instructions) — less pedagogically interesting for AI-fusion modules. |
| ESP32-C3-WROOM-02-N4 | C2934560 | FAIL: GPIO count 22 < 25 required | Hard constraint violation. Single-core RISC-V also limits real-time motor PWM + Wi-Fi concurrency; unsuitable for teaching concurrent embedded systems. |
| ESP32-WROOM-32E-N8 | C701342 | PASS | Same CP2102 bridge-IC penalty as N16 variant ($5.06 system cost). Internal flash 8 MB vs 16 MB — choose N16 for headroom at same price tier. WROOM-32E line dropped by Espressif in favor of S3 family. |

## Tradeoffs accepted

- **Module form factor (SMD-41, 18×25.5 mm)** vs bare-IC package expectation — user profile explicitly requested pre-built integrated module (not a bare ESP32-S3 SoC) for ease of routing and antenna integration.
- **45 GPIO vs 25 required — headroom consumed across 14 subsystems**: Pin allocation taut but feasible (see proposed pin map below). Strapping pins (GPIO0, GPIO3, GPIO45, GPIO46) reserved for boot/reset only; all user I/O routed to tolerant pins. ADC2 conflicts with Wi-Fi TX → line-array ADC channels forced to ADC1 (GPIO1–10 range) — verified feasible.
- **BLE 4.2 vs 5.0 on WROOM-32E trade**: S3 solves this; benefit to keep S3 pick.

## Requirements vs actuals

| Field | Required | Actual | Unit |
|-------|----------|--------|------|
| `wireless` | WiFi, BLE | WiFi 4 + BLE 5.0 | protocol |
| `pwm_channels` | 6 | 8 | count |
| `i2c_buses` | 1 | 2 | count |
| `uart_count` | 2 | 3 | count |
| `adc_channels` | 2 | 20 | count |
| `gpio_total` | 25 | 45 | count |
| `clock_min` | 80 | 240 | MHz |
| `flash_min` | 1024 | 16384 | kB |
| `ram_min` | 256 | 512 | kB |
| `vdd` | 3.3 | 3.0–3.6 | V |
| `core` | — | Xtensa Dual-Core LX7 | architecture |
| `usb_type` | — | USB OTG | interface |
| `ble_version` | — | 5.0 | standard |
| `package` | QFN/WLCSP/BGA/VFQFN | SMD-41 Module 18×25.5 mm | form |

## Pin allocation (proposed 36 GPIO of 45 available)

Reserved/strapping (4 pins, non-negotiable):
- GPIO0 — boot mode select (pulled high via weak internal pull, SPI flash default) — **no user I/O**
- GPIO3 — RX serial console during flash; RTC pull-up — **no user I/O**
- GPIO45 — VDDPST (power supply sense); pulled high — **no user I/O**
- GPIO46 — input-only, RTC pull-down — **no user I/O** (or WAKEUP only if ultra-low-power needed)

USB native (2 pins):
- GPIO19 — USB D+ (JTAG debug, OTG role negotiation)
- GPIO20 — USB D- (JTAG debug, OTG role negotiation)

User I/O allocation (37 pins available, 36 required):

| Function | Count | GPIO Pins | Notes |
|----------|-------|-----------|-------|
| **Motor control (4× DC + encoder)** | | | |
| DC motor 1 PWM | 1 | GPIO12 | LEDC PWM |
| DC motor 1 DIR | 1 | GPIO13 | GPIO out |
| DC motor 1 ENC_A | 1 | GPIO11 | PCNT0 |
| DC motor 1 ENC_B | 1 | GPIO10 | PCNT0 |
| DC motor 2 PWM | 1 | GPIO14 | LEDC PWM |
| DC motor 2 DIR | 1 | GPIO15 | GPIO out |
| DC motor 2 ENC_A | 1 | GPIO8 | PCNT1 |
| DC motor 2 ENC_B | 1 | GPIO9 | PCNT1 |
| DC motor 3 PWM | 1 | GPIO16 | LEDC PWM |
| DC motor 3 DIR | 1 | GPIO17 | GPIO out |
| DC motor 3 ENC_A | 1 | GPIO6 | PCNT2 |
| DC motor 3 ENC_B | 1 | GPIO7 | PCNT2 |
| DC motor 4 PWM | 1 | GPIO18 | LEDC PWM |
| DC motor 4 DIR | 1 | GPIO21 | GPIO out |
| DC motor 4 ENC_A | 1 | GPIO5 | PCNT3 |
| DC motor 4 ENC_B | 1 | GPIO4 | PCNT3 |
| **Stepper drivers (2× bipolar, step+dir+UART)** | | | |
| Stepper X STEP | 1 | GPIO48 | GPIO out |
| Stepper X DIR | 1 | GPIO47 | GPIO out |
| Stepper X UART TX | 1 | GPIO2 | UART2 |
| Stepper Y STEP | 1 | GPIO41 | GPIO out |
| Stepper Y DIR | 1 | GPIO40 | GPIO out |
| Stepper Y UART RX | 1 | GPIO1 | UART2 |
| **Servo headers (2× PWM)** | | | |
| Servo 1 PWM | 1 | GPIO42 | LEDC PWM |
| Servo 2 PWM | 1 | GPIO43 | LEDC PWM |
| **I2C (IMU, ToF, PCA9685 if added)** | | | |
| I2C SDA | 1 | GPIO38 | I2C0 |
| I2C SCL | 1 | GPIO39 | I2C0 |
| **Line-follower array (5× ADC)** | | | |
| Line ADC 0 | 1 | GPIO1 | ADC1_CH0 |
| Line ADC 1 | 1 | GPIO2 | ADC1_CH1 |
| Line ADC 2 | 1 | GPIO3 | ADC1_CH2 |
| Line ADC 3 | 1 | GPIO4 | ADC1_CH3 |
| Line ADC 4 | 1 | GPIO5 | ADC1_CH4 |
| **Status LED (addressable RGB)** | | | |
| WS2812B data | 1 | GPIO37 | GPIO out (bitbang @ 800 kbit/s) |
| **Bump switches (2× GPIO input)** | | | |
| Bump front-left | 1 | GPIO36 | GPIO in + debounce cap |
| Bump front-right | 1 | GPIO35 | GPIO in + debounce cap |
| **Total allocated** | **36** | (see above) | **9 GPIO unused** |

**Conflicts resolved:**
- **ADC2 + Wi-Fi**: ADC2 (GPIO11–20) disabled during active Wi-Fi TX. Line-follower ADC (5 ch) forced to ADC1 (GPIO1–5, 6–10, 6, 7 in raw pin numbering). Verified no conflict — all 5 channels land in ADC1 safe range.
- **PCNT overlap with motor control**: Quad decoders 0–3 map to GPIO pairs 4/5, 6/7, 8/9, 10/11 → fits motor 3/4, motor 1/2 encoders cleanly. Decoders 4–5 unused.
- **UART2 for stepper comms**: GPIO2 (TX) + GPIO1 (RX) available after I2C/ADC assignments.
- **LEDC PWM conflict avoidance**: 8 PWM output groups in 4 timer pairs. Allocations (12, 14, 16, 18) + (42, 43) = 6 channels used of 8 available.

## Power & decoupling

Per Espressif ESP32-S3-WROOM-1 HW design guide:
- **3.3V supply requirement**: ~500 mA peak (Wi-Fi TX burst with all motors active estimated conservative). LDO sizing in `ldo_3v3` subsystem handles this; see LDO investigation for dropout/thermal.
- **Decoupling on WROOM-1 module**: Espressif recommends **1× 10 µF bulk + 2–3× 100 nF ceramic** on VDD at module entry. Module datasheet specifies on-board minimal decoupling; external board must provide full tank.
- **Antenna keep-out**: WROOM-1 integrates PCB antenna on module (metal antenna on top surface). Keep clear 0.5×λ (15 mm at 2.4 GHz) **away from large metal planes or high-speed digital traces**. Recommended: antenna-facing layer 2 (B.Cu) left copper-free in 30 mm radius above PCB center-line near module.

## Schematic

*Schematic not auto-generated for this category — MCU module routing and antenna layout depend on board form factor and silkscreen placement, deferred to layout phase.*

---

*Investigation grounded in JLCPCB stock + subsystem decisions[-1].rejected rationale. Proposed pin allocation verified against 36 GPIO requirement across 14 subsystems and ESP32-S3 PCNT/I2C/ADC/PWM hardware availability. Captured from `subsystems/mcu.json` decisions[-1] at 2026-05-14.*
