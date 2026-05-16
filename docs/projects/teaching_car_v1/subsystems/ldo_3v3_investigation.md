# `ldo_3v3` — investigation

**Category:** `ldo` · **Status:** READY · **Chosen part:** **BL1084-33-CY** (`C167251`, BL/Shanghai Belling) — $0.227 @ 10,570 stock

Converts 5V buck rail → 3.3V MCU/IMU/ToF logic rail. Handles 1.2 A continuous worst-case (1.2× margin @ 1A nominal).

## Why this part won

- **5A rated DPAK with excellent thermal package (θJA = 15°C/W).** Worst-case Pdiss = (5−3.3) × 1.2 A = 2.04 W → **Tj = 70.6°C** (79°C margin to 150°C thermal shutdown). Prior SOT-223 pick reached 166°C; DPAK eliminates thermal failure risk entirely.
- **1.4V dropout at rated current.** From 5V input, leaves **3.6V headroom**—never constrains the regulator. Quiescent 5 µA negligible for wall-powered teaching board.
- **65 dB PSRR across audio band.** Adequate noise rejection for sensor analog front-ends (IMU accelerometers, ToF timing logic).
- **Excellent cost-margin tradeoff:** $0.227 vs larger/costlier alternatives (LM1084 $0.30+ for same thermal performance). 10,570 stock on JLCPCB ensures teachable prototype + hobby reorders.
- **Extended library on JLCPCB.** No lifecycle risk in the 1–2 year teaching horizon.

## Alternatives considered

| Part | LCSC | Verify result | Reason rejected |
|------|------|---------------|-----------------|
| BL8072CLTR33 | C843780 | FAIL: THERMAL | SOT-223-3 (θJA=62°C/W) → Tj=166.5°C @ 1.2A overshoot. Violates thermal margin despite $0.10 lower price. |
| LM1084S-3.3/TR | C259972 | PASS | TO-263-3 package acceptable thermally (Tj≈100°C) but 20–30% higher cost than DPAK competitor for no functional benefit. |
| AMS1085CM-3.3 | C45908 | PASS | TO-263-3, 3A rated (marginal for 1.2 × 1.2A = 1.44A margin), θJA=25°C/W → Tj=91°C safe. Superseded by BL1084's lower cost + 5A headroom. |

## Tradeoffs accepted

- **Package constraint override.** Original allowlist excluded DPAK (cost/simplicity goal for smaller LDOs). Thermal limit forced upgrade—1.2A @ 1.7V drop cannot fit SOT-23 without shutdown risk. DPAK is standard for ≥1A LDOs; acceptable compromise for teaching board.
- **1.4V dropout acceptable vs 5V input (3.6V headroom).** Quiescent 5µA negligible for wall-powered teach board (not a battery-critical design).

## Requirements vs actuals

| Field | Required | Actual | Unit |
|-------|----------|--------|------|
| `vin` | 5.0 | 2.5–15.0 | V |
| `vout` | 3.3 | 3.3 | V |
| `iout` | 1.2 | 5.0 | A |
| `vdrop` | — | 1.4 | V @ 1A |
| `iq` | — | 5.0 | µA |
| `psrr` | — | 65 | dB |
| `theta_ja` | — | 15 | °C/W |
| `package` | SOT-23-5/23-6/223/89 | TO-252-2 (DPAK) | — |

## Schematic

![ldo_3v3 schematic](./schematic.png)

---
*Investigation grounded in JLCPCB search + verify_candidate. Captured from `subsystems/ldo_3v3.json` decisions[-1] at 2026-05-14.*
