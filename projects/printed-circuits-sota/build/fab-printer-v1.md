# The Fab Printer — v1 Architecture (CHOSEN: LASER)

*Status: chosen. One enclosed 3D printer, laser carves the board, paste + pick-place finish it. Chemistry-free, no Z-probing, no bits. Human only reflows. See [[README]], [[method-landscape]].*

## Decision: laser, not milling

Milling was evaluated and rejected for v1. It carries a heavy *tax*:
- Z-critical (±10–25µm) → needs height-mapping every board + tool-zero + sprung probe
- bit changes → #1 failure source (re-zero errors, snapped bits)
- warp-sensitive, dust, serial/slow

**Laser deletes all of it** (non-contact, ±1–3mm depth-of-focus → no probing, no bit, no tool-zero). The cost is fume + fiber price + slower carving — accepted. For PCB specifically, laser is the right pick.

## What it is

**"The Fab Printer"** — one enclosed 3D printer with three fixed heads that takes a KiCad design → outputs a **placed, paste-applied 2-layer THT board**. Human carries it to an oven/hotplate for reflow. Chemistry-free end to end.

Nobody sells exactly this: fiber lasers pattern only; LumenPnP places only. This **fuses carve + drill + paste + place on one frame**, driven by `hw_toolkit.fab` + machine vision.

## The machine

```
        ONE ENCLOSED 3D PRINTER  (stiffened CoreXY + GRBL/Klipper + hw_toolkit.fab)
        ├── HEAD 1: fiber laser (gantry-mounted, fiber-delivered)  → carve traces + drill holes
        ├── HEAD 2: paste dispenser                                 → solder paste on pads
        └── HEAD 3: vacuum nozzle                                   → pick & place parts
        SENSE:  down-camera (object detection: fiducials · part-pose · AOI)
        SAFETY: full enclosure + fume extraction + interlock (Class 4)
        VACUUM: pump for pickup
                                     ↓
                     HUMAN → oven / hotplate → reflow → tested board
```

**Fiber mount = gantry, not galvo.** Galvo is fast but fixed/off-gantry. A gantry-mounted fiber head (heavy source box beside the printer, fiber cable to a light focusing head) **rides the carriage** like the other heads → keeps it ONE machine. Trade: slower (carves line-by-line, no galvo mirrors).

## Why the printer frame works

Laser = zero force. Paste/PnP = light force. No milling = no lateral cutting force → the cheap-printer rigidity problem never appears. (This is why milling-on-printer failed and laser-on-printer doesn't.)

## The flow (all on one machine)

```
1. LOAD     copper-clad board onto bed (camera reads fiducials → register)
2. LASER    carve traces top → flip → carve bottom → drill via/THT holes   ← chemistry-free
3. CLEAN    brush/vacuum copper debris
4. PASTE    dispense solder paste on pads
5. PLACE    camera locates parts → pick & place
6. HUMAN    carry to oven/hotplate → reflow
   = placed, soldered 2-layer THT board, one machine, zero wet chemistry
```

Layer connection: **THT parts soldered both sides = vias for free** (no plating). Few signal-only vias = soldered wire/rivet.

## What this design DELETES (vs milling)

- ❌ Z-probe / height-mapping (laser Z-tolerant)
- ❌ tool-zero / sprung setter / electrical touch-off (no bit)
- ❌ bit changes (no bit → no #1 failure source)
- ❌ the entire probing subsystem + the copper/plastic conductivity problem

## What it still needs

| Item | Why | Essential? |
|------|-----|-----------|
| **Down-camera + object detection** | PnP is blind without it: fiducials, part-pose, placement check, AOI | **critical** |
| **Part presentation** | trays (vision-guided picking) or tape feeders | critical (trays ok for v1) |
| **Enclosure + fume extraction** | copper + FR4 ablation fume; Class 4 laser safety | **mandatory** |
| **Vacuum pump** | PnP pickup suction | yes |
| Workholding | hold board (loose — laser tolerates warp) | light |
| Reflow | external (human + oven/hotplate) | external |
| **Software** `hw_toolkit.fab` | the brain: toolpaths, vision, registration, placement, AOI | **the moat** |

## BOM (rough)

| Item | Spec | ~$ |
|------|------|----|
| Donor printer | stiffened CoreXY | $200–400 (or owned) |
| **Fiber laser** | 20–50W pulsed/MOPA, gantry head | **$1,000–3,000** |
| Paste dispenser head | syringe + pneumatic/extruder | $80–200 |
| Pick-place head | vacuum nozzle + down-camera | $150–400 |
| Enclosure + fume extraction | sealed box + carbon/HEPA or duct-out | $200–500 |
| Vacuum pump | small | $40–100 |
| Reflow | oven / hotplate (human step) | $50–150 |
| **Total** | | **~$1.7–4.7k** |

Fiber laser dominates cost — and is the enabler.

## Honest catches + mitigations

| Catch | Reality | Fix |
|-------|---------|-----|
| Carving slow | gantry fiber, no galvo → minutes/board | accept; or galvo-off-gantry later for speed |
| FR4 charring (IR) | copper reflects IR, FR4 chars when cleared | MOPA pulse tuning · FR1 substrate · or green fiber |
| Fume | the chemistry-free hazard is airborne | full enclosure + extraction — non-negotiable |
| Class 4 safety | eye hazard | enclosure + interlock |
| Double-sided flip | re-register after flip | fiducials + camera affine |
| Fine pitch | camera + gantry good for THT + ~0.5mm | BGA = not v1 |
| Reflow manual | human + oven | fine for v1 |

## What it will / won't do

- ✅ **Will:** 2-layer (front+back), THT + ~0.5mm SMD, chemistry-free carve + drill + paste + place; same-day; reliable enough for real loads (size traces via `hw_toolkit.calc_trace_width`).
- ⚠️ **Stretch:** clean fine pitch, 4-layer (needs lamination — deferred).
- ❌ **Won't (v1):** plated vias (THT solder-both-sides), blind/buried, BGA, automated reflow.

## Reliability checklist ("bolt it to the car")

1. **Trace current** — fat traces, 1–2oz copper, sized via `calc_trace_width`.
2. **Clean carve** — no copper bridges/slivers; camera AOI vs Gerber before assembly.
3. **Solid joints** — THT soldered both sides (vibration-robust).
4. **Test first** — continuity + bench-supply load test BEFORE connecting to anything live.
5. Optional conformal coat for moisture/vibration.

## Build ladder

- **v1.0** — printer + gantry fiber: carve + drill a single-sided board. Prove ablation + Gerber→laser software.
- **v1.1** — double-sided (flip + fiducial registration) + THT vias.
- **v1.2** — add paste dispenser head.
- **v1.3** — add pick-and-place head + down-camera + object detection.
- **v1.4** — AOI (camera vs Gerber) + the agent ties it all together.
- **v2** — galvo-laser speed upgrade; 4-layer (lamination); fine pitch.

## Software (the moat) — `hw_toolkit.fab`

Drives every head from one Gerber: laser carve toolpath, drill cycles, paste points, PnP placement, fiducial registration, head offsets, object-detection vision, AOI. See [[module-gerber-to-laser]]. The hardware is buyable; this integrated agent-driven flow is the unsold part.

## Open questions to verify (next swarm)

1. Gantry-mounted fiber on a printer frame — head weight, fiber delivery, real resolution, carve speed.
2. FR4 char mitigation at hobby budget (MOPA params vs FR1 vs green fiber).
3. Object-detection placement accuracy on a cheap camera — smallest reliable package.
4. Copper-fume extraction spec for an enclosed desktop unit.
