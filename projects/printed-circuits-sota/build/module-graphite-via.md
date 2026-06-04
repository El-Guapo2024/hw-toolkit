# Module Design — Graphite-Seeded Via Plating

*Status: design. Source: validated as "the only genuinely DIY through-hole method" by the method-landscape adversarial verifiers. See [[method-landscape]].*

## Purpose

Turn drilled holes in a 2-layer copper-clad board into **plated through-holes (PTH)** — the one wall that blocks real 2-layer boards at home. Pairs with any patterning method (fiber-laser / LDI / mill / etch). **No palladium, no formaldehyde, no electroless bath** — only conductive graphite + acid copper-sulfate electroplating (hobby-proven electroforming chemistry).

## Why this approach (vs the refuted ones)

| Path | Status | Reason |
|------|--------|--------|
| Electroless copper (Pd + formaldehyde) | rejected | carcinogen, Pd cost, bath decomposes in hours, needs UV-Vis monitoring |
| Laser-LDS seeding | rejected | FR4 has no laser-activatable particles; needs $5k+ UV laser |
| **Graphite ink + electroplate** | **chosen** | low-hazard, validated DIY, rides electroforming chemistry |

## Process chain

```
1. DRILL          holes (CNC/laser) — done upstream
2. DEBURR/CLEAN   micro-etch + rinse → clean copper + clean hole walls
3. CONDITION      surfactant pre-dip so graphite wets the FR4 hole wall
4. GRAPHITE COAT  dip in colloidal graphite dispersion → coats EVERYTHING
5. DRY            bake ~60-80°C → continuous conductive carbon film on walls
6. FIX / MICRO-ETCH  remove graphite from copper PADS/traces (keep it in holes)
                  → also de-smears; leaves walls conductive, surfaces clean
7. ELECTROPLATE   acid CuSO4 bath, constant current → copper grows on ALL
                  conductive surface incl. now-conductive via walls
8. RINSE/DRY      → plated through-holes, ~25µm barrel, both layers joined
```

Key insight: graphite makes the **non-conductive hole wall conductive** so electroplating (which only deposits on conductive surfaces) can bridge top↔bottom copper. Step 6 is the subtle one — graphite must stay in the hole but leave the pads, or you plate a blurry mess.

## Hardware / BOM (rough)

| Item | Spec | ~$ |
|------|------|----|
| DC power supply | constant-**current**, 0–3A, 0–15V, programmable ideal | $40–120 |
| Plating tank | 1–2 L acid-resistant (PP/glass) | $15 |
| Anode | phosphorized copper (anode bag) | $20 |
| Agitation | magnetic stirrer + air sparge (aquarium pump) | $30 |
| Heater | aquarium heater, 25–30°C setpoint | $15 |
| Graphite dispersion | colloidal graphite (Electrodag/aquadag-type) or DIY graphite+dispersant | $20–40 |
| Acid copper bath | CuSO₄·5H₂O + H₂SO₄ + trace HCl (Cl⁻) + brightener | $25 |
| Cathode contact | edge clip / plating bus on board | — |
| PPE + vent | nitrile gloves, goggles, acid-rated, ventilation | $30 |
| **Total** | | **~$220–320** |

## Process parameters (starting points — tune empirically)

- Current density: **1–3 A/dm² (10–30 mA/cm²)**. Start low (1 ASD) for throwing power into holes.
- Plate time to ~25µm barrel: **30–60 min** at ~2 ASD.
- Bath: ~200 g/L CuSO₄·5H₂O, ~50 g/L H₂SO₄, ~50 ppm Cl⁻, brightener per supplier. Temp 25–30°C.
- Agitation: vigorous — **throwing power into holes is agitation-limited**.
- Anode:cathode area ratio ≥ 1:1, anode bagged to trap sludge.

## Hard constraints / known failure modes

- **Aspect ratio ceiling.** Throwing power into deep narrow holes is the limiter. Hobby realistic: **AR < 4:1**. 1.6mm board ÷ 0.4mm hole = 4:1 = borderline. Mitigate: **thinner board (0.8mm)** or **larger vias (≥0.5mm)** early on.
- **Graphite uniformity in small holes** — incomplete coverage = open via. Dual-coat passes help.
- **Step 6 selectivity** — over-micro-etch strips wall graphite (→ open); under = graphite on pads (→ rough plate). Tune time.
- **Thermal-cycle reliability** — verifier flagged DIY PTH as "prototype only"; barrels may crack over thermal cycles. Fine for prototypes, not field hardware.
- Acid copper bath = sulfuric acid: corrosive. Gloves/goggles/vent. Far safer than electroless.

## Automation hooks (for the agent-driven line)

- Each wet step = timed dip + agitation → robotic Z-dip arm or carousel of tanks.
- Electroplate = **constant-current source under software control** (set ASD from board copper area → compute current). Coulomb-count for thickness (Faraday).
- Endpoint: integrate current × time → target deposited mass → stop. No sensor needed.
- Board area auto-computed from the Gerber (shared with the [[module-gerber-to-laser]] geometry layer).

## Experiment plan (derisk in 3 phases)

1. **Flat coupon** — plate bare conditioned FR4 coupon graphite→Cu. Confirm bath + current + adhesion. Pass = continuous bright copper film.
2. **Single via** — one 0.5mm hole in 0.8mm board. Plate, cross-section or continuity-test top↔bottom. Pass = <1Ω, visible barrel.
3. **Via array + test board** — 2-layer board, via daisy-chain, measure chain resistance + 10× thermal cycle. Pass = stable continuity. → graduate to real boards.

## Open R&D (what's-left)

- Repeatable graphite coat in <0.5mm holes (coat count, dispersion solids %, vacuum-assist dip).
- Step-6 micro-etch window characterization (time vs temp vs selectivity).
- Constant-current driver + coulomb-counting firmware (couples to hw_toolkit).
- Throwing-power additives to push AR > 4:1 at hobby scale.
