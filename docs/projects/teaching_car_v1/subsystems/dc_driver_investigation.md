# `dc_driver` — investigation

**Category:** `motor_driver` · **Status:** READY · **Chosen part:** **TB6612FNG(O,C,8,EL)** (`C88224`, TOSHIBA) — $1.12 @ 8198 stock (qty 2 per board)

Dual-channel H-bridge brushed DC motor driver for 4× mecanum motors (2 ICs per board). Supplies PWM speed + direction control to each motor via independent feedback to MCU GPIO, with pass-through encoder support.

## Why this part won

- **VM range 2.5–15.0 V covers the entire battery envelope** (3S Li-ion 9.0–12.6 V nominal, plus margin). No additional regulators required on the motor supply rail.
- **Low on-resistance (500 mΩ) minimizes per-channel dissipation:** at 1.0 A per channel, Pdiss/ch = (1.0)² × 0.5 = 0.5 W. Dual IC (2 channels per IC at 1 A stall nominal) yields Tj = 40 + (2 × 0.5) × 85 = 125 °C — meets shutdown margin and operational limits within ambient 40 °C.
- **Continuous Iout 1.2 A per channel exceeds 1.0 A target** with peak 3.2 A overhead for stall transients. Quadrature encoders pull <1 mA and pass through IC GPIO directly to MCU without extra conditioning.
- **VLOGIC 2.7–5.5 V accepts 3.3 V from LDO** (nominal) plus typical logic supply margin. Dual-channel PWM+direction interface is native (no translation required).
- **Mature, widely-documented part** with reference designs (robotics kits, educational platforms). Single-pair sourcing from JLCPCB (8198 stock at qty 1), basic-library eligible for cost; 2-IC dual approach is industry-standard for 4-channel brushed DC.

## Alternatives considered

| Part | LCSC | Verify result | Reason rejected |
|------|------|---------------|-----------------|
| DRV8848PWPR | C131079 | Stepper driver (STEP/DIR interface), not PWM+dir for brushed DC. Stock just below threshold (490 < 500). 900mΩ RDS(on) vs TB6612's 500mΩ means 80% higher losses. | Wrong interface + lower stock + higher thermal burden; dual-IC approach wouldn't reduce complexity. |
| BA6406F-E2 | C509746 | Undocumented chip, limited specs available. Lower current rating likely (SOP-8 package thermal pad constraints). | No public datasheet; package constraints suggest insufficient current headroom. |
| DRV91680RGZR | C5125831 | Large VQFN-48 package, likely for brushless/stepper, not brushed DC. Over-specified and costly ($3.51 vs $1.12). | Feature overkill (48-pin suggests integrated encoder/stepper logic); 3.1× cost premium not justified for simple PWM+dir brushed DC. |

## Tradeoffs accepted

- **Motor supply VM requires separate 5V or higher LDO from VBAT** — TB6612FNG's Vlogic (2.7–5.5 V) is narrower than VBAT directly. Design mitigates with 5V buck from battery; LDO downstream drops 5V → 3.3V for logic. Trade-off: one additional DCDC stage, but essential for encoder + MCU supply isolation.
- **2-IC approach** — single 4-channel IC (e.g., DRV8833, quad monolithic) would reduce part count, but availability at JLCPCB is poor and cost/package typically worse. Dual TB6612 is proven, cost-effective ($2.24 for 4 channels), and distributes thermal load.
- **SSOP-24 thermal pad (85 °C/W) is tight at stall** — junction approaches 125 °C at 40 °C ambient + dual channels at 1 A each. Mitigation: thermal vias (≥8 × 0.3 mm) under pad, layer routing to GND planes, solder-free region around pad to prevent bridging. Runtime stall events are brief (lab environment); design is safe but not over-margined. Future reorder if 100+ unit production should explore higher-pincount packages (QFN-32 or similar with better theta_ja).

## Requirements vs actuals

| Field | Required | Actual | Unit |
|-------|----------|--------|------|
| `channels` | 4 | 2 (per IC; qty 2 ICs = 4 total) | — |
| `current_per_channel` | 1.0 | 1.2 | A |
| `motor_voltage` | 11.1 (nom) | 2.5–15.0 | V |
| `control_interface` | pwm_dir | pwm_dir | — |
| `iout_peak` | — | 3.2 | A |
| `vm_min` | 9.0 | 2.5 | V |
| `vm_max` | 12.6 | 15.0 | V |
| `vlogic_min` | 2.7 | 2.7 | V |
| `vlogic_max` | 5.5 | 5.5 | V |
| `rdson_mohm` | — | 500 | mΩ |
| `theta_ja` | — | 85 | °C/W |
| `tsd` | — | 175 | °C |
| `package` | SOIC-8-EP, TSSOP-16-EP, QFN, DFN | SSOP-24 | — |
| `stock` | ≥500 | 8198 | units @ JLCPCB |

## Schematic

![dc_driver schematic](./schematic.png)

---

*Investigation grounded in JLCPCB search + verify_candidate. Captured from `subsystems/dc_driver.json` decisions[-1] at 2026-05-14.*
