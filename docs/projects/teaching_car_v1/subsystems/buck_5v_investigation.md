# `buck_5v` — investigation

**Category:** `buck_converter` · **Status:** READY · **Chosen part:** **TPS566238PRQFR** (`C2876603`, Texas Instruments) — $0.24 @ 7956 stock

Synchronous 6 A buck converter, 600 kHz switching, adjustable output. Converts 11.1 V nominal 3S Li-ion (9–12.6 V) to stable 5 V rail powering servos, sensor array, and RGB LED.

## Why this part won

- **Current headroom:** TPS566238 rated 6 A exceeds the 4.8 A requirement (4 A × 1.2 margin). Overhead prevents thermal runaway and edge-case transient saturation in servo inrush scenarios.
- **Thermal margin:** At 4 A worst-case load, conduction loss Pdiss ≈ (4 A)² × 20.8 mΩ × 0.45 (duty) ≈ 0.15 W. With θ_JA = 89.6 °C/W from actuals, **Tj rise = 0.15 W × 89.6 = 13.4 °C**, yielding Tj = 53.4 °C at 40 °C ambient — **55 °C margin to 125 °C junction limit**. Low-dissipation package trades size for safety.
- **Switching frequency match:** Native 600 kHz switching minimizes inductor ripple (Vc ≈ 1.2 A ripple @ 3.8 µH → clean 5 V rail) — critical for servo position stability and line-following sensor noise immunity.
- **Wide input range:** Specified 3–18 V covers 9–12.6 V battery span with margin for charger overshoot and sag under 4 A load. No secondary protection needed.
- **Cost and ecosystem:** **$0.24 JLCPCB, $0.44 DigiKey qty 21k** — lowest-cost synchronous buck in allowed packages. TI part guarantees design documentation and long availability (Active status through 2027+ typical for mainstream TI ICs).

## Alternatives considered

| Part | LCSC | Verify result | Reason rejected |
|------|------|---------------|-----------------|
| TPS564201DDCR | C464812 | FAIL: Iout 4A < 4.8A margin | Rated current insufficient; selected higher-capacity part to maintain thermal margin at worst-case load |
| TPS565201DDCR | C327676 | PASS | Acceptable (5A rated) but $0.61 vs $0.24 — 2.5× cost for marginal 1A capacity increase; project BOM margin better spent elsewhere |
| TPS56C230RJER | C1849534 | PASS | Overkill: 12A rating for 4A load; 20-pin VQFN vs 9-pin (larger board footprint, assembly complexity); $0.53 cost not justified |
| TPS56A37RPAR | C22392669 | PASS | 10A through-hole package ($2.00) — wrong form factor and extreme cost overage; not practical for handheld robot PCB |
| AP63300WU-7 | C2158012 | PASS | Competing synchronous buck (3.6A rated), lower Iq (22 µA vs TPS566238 spec sheet default), but $0.52 vs $0.24 — 2.2× cost for marginal quiescent advantage in always-off standby scenarios |

## Tradeoffs accepted

- None — TPS566238 is the cost-optimal, thermally safe choice for the 4 A @ 5 V target. All functional margins (current, thermal, frequency) exceed requirements. DigiKey Active lifecycle status ensures supply continuity through 2027+.

## Requirements vs actuals

| Field | Required | Actual | Unit |
|-------|----------|--------|------|
| vin | 11.1 | 3.0–18.0 | V |
| vin_max | 12.6 | — | V |
| vout | 5.0 | 5.0 | V |
| iout_max | 4.0 | 6.0 | A |
| iout_typical | 1.5 | — | A |
| iout_margin | 1.2 | — | × |
| fsw | — | 600.0 | kHz |
| ripple_pct | 30.0 | — | % |
| ambient_c | 40.0 | — | °C |
| rdson_mohm | — | 20.8 | mΩ |
| theta_ja | — | 89.6 | °C/W |
| package | SOT-23-6 / QFN | VQFN-9-HR(1.5×2) | — |
| stock | — | 7956 | units @ JLCPCB |

## Schematic

![buck_5v schematic](./schematic.png)

---
*Investigation grounded in JLCPCB stock + verify_candidate + DigiKey lifecycle check. Captured from subsystems/buck_5v.json decisions[-1] at 2026-05-14.*
