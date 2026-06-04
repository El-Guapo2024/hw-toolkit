# Build Sheet — Ender PnP Add-On (v1, mostly 3D-printed)

*The actionable checklist. Retrofit a stock Ender into a pick-and-place: print the structure, buy ~$100 of parts, reuse the Ender's motion, run OpenPnP + our glue. Reversible. See [[cheap-pnp-addon-plan]].*

## Impact thesis (why this one)
Largest installed base (millions of Enders) × lowest barrier (print + ~$100) × distributable (STLs + buy-list + software). Democratizes the still-expensive step: assembly.

## PRINT (free, on the Ender — do this first while it's still a printer)
Fork the STLs from **3DPlacer** (github.com/xpDIY/3DPlacer) — most are already designed for Ender:
- [ ] Toolhead bracket (replaces hotend on X-carriage)
- [ ] Nozzle holder (takes a dispense-needle tip)
- [ ] θ-rotation housing + printed gear (for the rotate motor)
- [ ] Pick-Z housing (for the down-stroke actuator)
- [ ] BlindsFeeder strip holders (OpenPnP) — print a few
- [ ] Down-camera mount + up-camera (bottom-vision) mount
- [ ] Board hold-down clips / fixture for the bed

## BUY (~$75–150 — the non-printable bits)
| # | Part | Search for | ~$ |
|---|------|-----------|----|
| [ ] | θ-rotation motor | NEMA8/NEMA14 stepper *or* small geared servo | $5–15 |
| [ ] | Pick-Z actuator | push-pull solenoid 12V *or* micro-servo (or reuse Ender Z = $0, slow) | $0–15 |
| [ ] | Vacuum pump + solenoid valve | "12V vacuum pump pick and place" + 2-way solenoid | $15–40 |
| [ ] | Nozzle tips | SMD vacuum nozzles / blunt dispense needles (set) | $8 |
| [ ] | Down-camera | USB camera module (fixed-focus, decent res) | $15–25 |
| [ ] | Up-camera (bottom vision) | second USB camera | $15–25 |
| [ ] | MCU + driver | Raspberry Pi Pico + small stepper/solenoid driver | $10 |
| [ ] | Hardware | bearings, magnets, springs, M3 fasteners, silicone tubing | $20 |

## REUSE from the Ender (free)
- ✅ X/Y/Z steppers + motion · ✅ control board + PSU · ✅ frame
- You only *add* θ-rotate + pick-Z + vacuum. That's the "some motors" you buy.

## BRAIN (own / separate — not in kit price)
- **v1:** a laptop you own → runs OpenPnP + vision. $0.
- **later:** Jetson Orin Nano (separate) → on-device agent + VLM, self-contained "smart printer."

## SOFTWARE (free)
- [ ] **OpenPnP** (openpnp.org) — control + vision. Select `GcodeDriver` for Marlin.
- [ ] **Ender firmware** — stock Marlin; enable `M42` pin control for vacuum/solenoid (spare pin, or `I1` for protected pins).
- [ ] **hw_toolkit glue** — KiCad placement file → OpenPnP job + agent orchestration (our build).
- [ ] **fine-tuned YOLO** — optional, add only if OpenPnP's classic CV isn't enough. Train on auto-labeled data from placement ground-truth.

## ASSEMBLE (order)
1. Print all parts (Ender still a printer).
2. Swap hotend → toolhead bracket; mount nozzle + θ-motor + pick-Z.
3. Mount down-camera on carriage; fix up-camera in the work area.
4. Wire θ / pick-Z / vacuum to the Pi Pico (or spare board pins via M42).
5. USB: Ender → laptop running OpenPnP.
6. Calibrate: camera, nozzle offset, fiducials, feeder positions, bottom-vision.
7. Print + load BlindsFeeders with cut tape; clamp a test board on the bed.

## FIRST TEST (prove it)
1. Place a few **0805/0603** parts on a scrap board (big = forgiving) → check accuracy.
2. Tune acceleration low (bed-slinger → parts shift if too fast).
3. Verify bottom-vision corrects pick offset/rotation.
4. Reflow on a hotplate → inspect joints.
5. → iterate down to 0402 if wanted.

## BUDGET
```
Ender:        own (or ~$150 used)
Printed:      ~$5 filament
Buy parts:    ~$75–150
Brain:        laptop (own)
Software:     free
─────────────────────────────
Out of pocket (printer+laptop owned): ~$100–150  ✅
```

## SCOPE (v1)
- ✅ THT + 0603/0805-class SMD, OpenPnP + BlindsFeeder, reversible Ender retrofit.
- ⚠️ stretch: 0402/fine pitch.
- ❌ not v1: BGA, auto reflow, motorized feeders, the laser (bought separately for board carving).

## DISTRIBUTION (the impact play)
Publish: STLs + this buy-list + the hw_toolkit glue + OpenPnP config. Anyone with an Ender prints + buys ~$100 → has a PnP. The 3D-printing model applied to assembly.
