# teaching_car_v1 — Non-Templated BOM

Locked picks for subsystems without a designer-mcp template (no JSON path). These are committed here for schematic/BOM time.

Date: 2026-05-14

---

## rgb_status — Single addressable RGB status LED

- **MPN:** `WS2812B-B/T`
- **Manufacturer:** Worldsemi
- **LCSC:** C2761795
- **Package:** SMD5050-4P (5.0 × 5.0 × 1.6 mm)
- **Price:** $0.0997 @ qty 1 (LCSC)
- **Stock:** 377,000+ (LCSC)
- **Lifecycle:** Active
- **Datasheet:** https://www.lcsc.com/datasheet/lcsc_datasheet_2412041609_Worldsemi-WS2812B-B-T_C2761795.pdf
- **Qty per board:** 1

**Electrical:**
- Supply: 3.7–5.3 V (use 5V rail for full brightness)
- Protocol: WS2812 single-wire, 800 kbit/s
- Current: ~20 mA per channel @ full brightness, 0.6 mA idle
- Luminous intensity: R 300–500 mcd, G 600–1000 mcd, B 200–300 mcd

**Hidden BOM:**
- `74LVC1G07` open-drain buffer + 4.7kΩ pullup to 5V — required because ESP32-S3 3.3V GPIO output is marginal vs WS2812B 0.7×Vcc = 3.5V logic-1 threshold.
- 0.1µF + 10µF decoupling on VDD of WS2812B.

---

## tof_distance — I²C ToF distance sensor (kit add-on, not on-PCB IC)

- **Approach:** Generic VL53L0X breakout module sourced separately (AliExpress / Amazon / Pololu carrier)
- **Target price:** $3–5 per module
- **Range:** 20–2000 mm
- **Interface:** I²C @ 0x29
- **Supply:** 2.6–3.5 V (module includes onboard LDO usually)

**PCB-side BOM (this is the only line item on the teaching_car_v1 board):**
- 1× 4-pin 2.54mm pitch header (VCC / GND / SDA / SCL). Pin 1 marked.
- Optional: 4.7kΩ × 2 I²C pullups (if not present elsewhere on shared I²C bus).

**Rationale:** OLGA-12 bare-die VL53L0X IC has high SMT yield risk (optical window damaged by flux/reflow). Breakout module eliminates this and is the standard educational-kit approach. Trade off: a kit add-on instead of a single-board BOM, but yield + cost win.

---

## line_array — 5-channel IR reflectance line sensor (on-PCB, single-package per channel)

- **MPN:** `TCRT5000` (×5 per board)
- **Manufacturer:** Vishay
- **DigiKey:** 626-TCRT5000-TRD
- **Package:** 5mm plastic through-hole, 4-pin (matched emitter+detector in one housing)
- **Price:** ~$0.44 each @ qty 1 (DigiKey), ~$0.30 @ qty 50
- **Stock:** 2000+ (DigiKey), additional Mouser stock
- **Lifecycle:** Active
- **Datasheet:** https://www.vishay.com/docs/83054/tcrt5000.pdf
- **Qty per board:** 5

**Electrical:**
- Wavelength: 935 nm (factory-matched emitter + detector)
- Supply: 5 V (emitter side limited via resistor)
- Output: analog Vce phototransistor (0.5–5.0 V depending on reflectance)
- Sensing distance: 0.3–4 mm typical (target ~2 mm above floor)
- Response time: <1 ms

**Per-channel hidden BOM (×5 channels):**
- 100Ω 1/4W emitter current-limit resistor (limits LED to ~50 mA at 5V)
- 10kΩ phototransistor collector pulldown
- 10kΩ + 100nF RC low-pass filter on ADC input

Per-channel cost ≈ $0.04 passives × 5 = $0.20/board.

**Total line_array BOM/board ≈ $2.40**

**MCU mapping:** 5× ESP32-S3 ADC channels (ADC1_CH0..CH4 recommended; ESP32-S3 has 20 total ADC channels so this is comfortable).

---

## power_input — Battery input front-end (3 parts on PCB + 1 fuse holder)

### Part 1 — Battery connector

- **MPN:** `XT30PW-M30.G.Y`
- **Manufacturer:** Changzhou Amass Elec.
- **LCSC:** C431092
- **Package:** XT30 2-pin aviation, through-hole
- **Price:** $0.38 @ qty 1 (LCSC)
- **Stock:** 14,569 (LCSC)
- **Lifecycle:** Active
- **Rating:** 15 A continuous, 500 VDC, contact resistance 1.2 mΩ

### Part 2 — Reverse-polarity P-MOSFET (high-side)

- **MPN:** `AO4407`
- **Manufacturer:** ElecSuper (Alpha & Omega original)
- **LCSC:** C5224298
- **Package:** SOP-8
- **Price:** $0.10 @ qty 1 (LCSC)
- **Stock:** 56,471 (LCSC)
- **Lifecycle:** Active
- **Datasheet:** https://wmsc.lcsc.com/wmsc/upload/file/pdf/v2/lcsc/2210271700_ElecSuper-AO4407_C5224298.pdf

**Electrical:**
- Vds: -30 V max
- Id: -12 A continuous @ 25°C (derates to ~8 A @ 70°C)
- Rds(on): 9.5 mΩ @ Vgs = -10V, ~12 mΩ @ Vgs = -4.5V (logic-level OK)
- Vgs(th): -1.5 V typical
- Topology: source to VBAT+, drain to load, gate to GND via 10kΩ, gate-source Zener clamp (12V) to prevent overdrive on max VBAT (12.6V > -10V abs max Vgs is fine, but Zener adds margin against transients).

### Part 3 — Through-hole 6A glass fuse + holder

- **Approach:** Generic 5×20mm glass cartridge fuse holder (panel-mount or PCB-mount), 6 A fast-blow fuse cartridge
- **Source:** Any DigiKey/Mouser cartridge fuse holder line (e.g. Littelfuse 03450126ZXL holder + 0312006.MXP fuse)
- **Price:** ~$0.10 holder + ~$0.10 fuse cartridge
- **Rating:** 6 A fast / 250 V (huge margin over 12.6 V)
- **Rationale:** Resettable PPTC unavailable on JLC. Glass fuse is pedagogically clear (visible failure mode, student-replaceable) and cheapest.

**Optional add (recommended, ~$0.10):** SMBJ15CA bidirectional TVS across VBAT (after fuse, before MOSFET) for transient suppression on battery connect/disconnect arcing. LCSC has SMBJ15CA in stock; non-blocking add-on.

### power_input subtotal per board

| Part | Cost @ qty 50 |
|------|---------------|
| XT30PW connector | $0.30 |
| AO4407 P-MOSFET | $0.08 |
| Fuse holder + 6A cartridge | $0.20 |
| Gate resistor (10kΩ) + Zener (12V) | $0.05 |
| Optional SMBJ15CA TVS | $0.10 |
| **Total** | **~$0.73** |

---

## Deferred to schematic time (pure passives)

- 2× bump switches (Omron / TE microswitch, SPDT, 3-pin, momentary) — pick at schematic phase from JLC stock.
- Headers/connectors: 4-pin ToF header, 7-pin line-array header (if breakout module path was taken — N/A since we chose on-PCB TCRT5000), motor JST connectors, encoder headers, programming header, expansion header.
- All decoupling/bulk caps per subsystem.
- Status/power LEDs (non-addressable) — single-color SMD, generic.

These don't need research — pick at schematic phase from JLC basic-library parts.
