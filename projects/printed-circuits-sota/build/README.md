# Home Circuit-Fab Line — Architecture

*The "new method" for hobby PCB fab = not a new chemistry, an **integrated automatable line**. Built from proven steps the verifiers confirmed; skips the refuted clever ones.*

## The line

```
  KiCad design ─▶ [SOFTWARE]  Gerber → toolpath + registration
                      │        hw_toolkit.fab   → see module-gerber-to-laser.md
                      ▼
                 [PATTERN]  fiber-laser ablation (chem-free)  OR  diode-LDI + etch
                      │     traces defined + holes drilled
                      ▼
                 [VIAS]     graphite seed → Cu-sulfate electroplate
                      │     → 2-layer plated-through-holes   → see module-graphite-via.md
                      ▼
                 [ASSEMBLE] LumenPnP + reflow oven  (already solved by user)
                      ▼
                 2-layer PTH board, agent-driven, low-hazard
```

## Docs

| Doc | What |
|-----|------|
| **[shopping-list.md](shopping-list.md)** ⭐ | **THE BUILD SHEET** — actionable checklist: print parts (fork 3DPlacer) + buy ~$100 + reuse Ender motion + OpenPnP. Assembly + first-test steps. Start here to build. |
| **[cheap-pnp-addon-plan.md](cheap-pnp-addon-plan.md)** | **THE PLAN** — Ender PnP add-on rationale: reuse OpenPnP + 3DPlacer + BlindsFeeder, the scoping, hard problems, build phases. |
| [module-feeder.md](module-feeder.md) | **Module 1** — feeder add-on (printable BlindsFeeder, passive, oriented parts). |
| [module-camera.md](module-camera.md) | **Module 2** — camera/vision (down + bottom cameras, fiducials, pick correction, AOI). |
| [module-pickplace.md](module-pickplace.md) | **Module 3** — pick-place toolhead (vacuum nozzle + θ + pick-Z, swaps the hotend). |
| [fab-printer-v1.md](fab-printer-v1.md) | earlier full-machine concept (laser-carve + paste + PnP in one printer). Superseded by the add-on plan for v1; useful for the fab side + the laser-vs-mill reasoning. |
| [method-landscape.md](method-landscape.md) | full landscape: 12 methods, pros/cons, maturity, why the 4 additive bets were refuted, R&D backlog |
| [module-graphite-via.md](module-graphite-via.md) | the wall-breaker: PTH via graphite + electroplate. BOM, params, 3-phase experiment plan |
| [module-gerber-to-laser.md](module-gerber-to-laser.md) | the moat: `hw_toolkit.fab` Gerber→toolpath software, typed exceptions, milestones |
| [../research/SOTA-printed-circuits.md](../research/SOTA-printed-circuits.md) | upstream SOTA report (commercial machines, materials, market gap) |

## Build order

1. **`hw_toolkit.fab` v1** (gerber→geometry→mill/ablation G-code) — testable in Jupyter today, no chemistry, no risk. Start here.
2. **Graphite-via module** — phase-1 flat-coupon plating to prove the bath, then single via. The highest-value hardware unlock.
3. **Integrate** — registration, fume extraction, endpoint, then double-sided end-to-end.

## Guardrails (from the adversarial swarm)

- Don't build: electroless baths, laser-LDS, copper-formate reduce, maskless ECM → all `feasibleDIY: no, high`.
- Ceiling: **2 layers**. 4+ → JLCPCB.
- Economics: JLC = ~$2/board w/ vias. This wins on **iteration speed / air-gap / odd sizes / the build itself**, not cost. Build it as a *capability + learning platform*, not a money-saver.
