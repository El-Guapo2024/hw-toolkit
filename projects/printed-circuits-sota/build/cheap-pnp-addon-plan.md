# Cheap PnP — 3D-Printable Add-On for Ender 3 (PLAN)

*Status: plan, chosen direction. Sub-$500 pick-and-place as a 3D-printable kit that converts an Ender 3 into a PnP. Laser (carving) is bought off-shelf; this is the part we build. Scoping (2026-05) found most of it already exists — this is mostly integration. See [[README]], [[fab-printer-v1]].*

## Product thesis

**A 3D-printable kit that turns any Ender 3 into a pick-and-place.** Print the toolhead + feeders, bolt on, add a cheap vacuum + camera, run OpenPnP + our glue. Sub-$500, rides the huge Ender install base, distributed as STLs + code. The 3D-printing distribution model applied to PnP.

## Decisions locked

| Decision | Choice | Why |
|----------|--------|-----|
| Build vs extend | **extend (add-on)** | sub-$500, leverage existing motion, distributable |
| Host printer | **Ender 3** | cheapest, ubiquitous, Marlin/gcode, hackable; 3DPlacer proved it |
| Carving (board fab) | **buy a laser** (xTool-class) | not our build — focus effort on PnP |
| Control software | **reuse OpenPnP** | mature, free, Marlin GcodeDriver, has vision | 
| Feeders | **printable cut-tape strips (OpenPnP BlindsFeeder)** — NOT loose tray | oriented parts (kills polarity problem), $0 printed, already coded |
| Reflow | human + hotplate | external |

## Scoping: REUSE vs BUILD (most of it exists)

| Thing | Status | Notes |
|-------|--------|-------|
| **OpenPnP** | ✅ reuse | control + vision (bottom-vision correction, fiducials, CvPipeline). Drives Marlin via GcodeDriver. |
| **3DPlacer (xpDIY)** | ✅ reuse/fork | Ender 3 V2 → PnP add-on already designed (nozzle, Z-rezero, board hold-down). Start here. github.com/xpDIY/3DPlacer |
| **BlindsFeeder** | ✅ reuse + print | passive 3D-printed cut-tape holder; nozzle works the sliding "blinds"; vision/fiducial located; no motor/wiring. The cheap feeder. |
| **Marlin M42** | ✅ reuse | stock gcode pin control for vacuum/solenoid (`M42 P9 S255`); protected pins need `I1`. No custom firmware. |
| LumenPnP Marlin fork | reference | sphawes/Marlin + Photon/M485 — only if we outgrow stock |
| **What we BUILD** | 🔨 | (1) hw_toolkit glue: KiCad → OpenPnP job + agent orchestration. (2) Ender integration of 3DPlacer + fast pick-Z. (3) print BlindsFeeders. |

**Big takeaway:** went from "build a PnP" to "**bolt OpenPnP + 3DPlacer + BlindsFeeder together on an Ender + write the agent glue.**"

## Why NOT loose-tray (your instinct, confirmed)

Loose parts on a tray fail two ways → rejected:
1. **Polarity/orientation** — vision sees an outline but can't tell which way a diode/IC/tantalum faces, or distinguish square-part corners → wrong-orientation placement.
2. **Flip** — loose parts land upside-down → unpickable.
Plus manual loading + low count. **BlindsFeeder (parts stay oriented in tape pockets) fixes all of it** and is still printable/cheap.

## Ender 3 — what we're working with

| Trait | Detail | Implication |
|-------|--------|-------------|
| Motion | bed-slinger (Y=bed moves, X=gantry, Z=leadscrew), 220×220 | **feeders + board both on the bed** → same frame |
| Firmware | Marlin, USB serial | OpenPnP GcodeDriver streams gcode |
| Control board | Creality 4.2.x, few spare ports | M42 on spare pins, or small add-on MCU |
| Carriage | hotend on 2 screws | 3D-printed bracket swaps hotend → PnP toolhead (3DPlacer) |
| Z | single leadscrew, slow | **add fast pick-Z actuator** (don't crank leadscrew per pick) |
| Accuracy | belt backlash ~0.1–0.2mm | OpenPnP **bottom-vision + fiducials correct it**; fine-pitch = stretch |
| Frame | some flex | irrelevant — PnP is near-zero force |

## Ender bed layout

```
ENDER BED (220×220, moves in Y)
┌─────────────────────────────────┐
│ [BlindsFeeder strips]  [board]  │  ← both on bed → move together
│ [up-cam ↑ bottom vision]        │  ← fixed in work area
└─────────────────────────────────┘
 X-carriage: nozzle + θ-rotate + pick-Z + down-camera
 OpenPnP (Pi/PC) ──USB──> Marlin (gcode + M42 vacuum)
```
Constraint: 220×220 shares space between board + feeders → limited part variety per run (fine for prototypes; swap strips between runs).

## BOM (sub-$500)

| Part | Printed/Bought | ~$ |
|------|---------------|-----|
| Ender 3 | host (owned or used) | $0–180 |
| Toolhead bracket + mount (3DPlacer-based) | **printed** | ~$3 filament |
| Vacuum pump + solenoid valve | bought | $40 |
| Rotation (nozzle θ) — small stepper/servo | bought | $15 |
| Pick-Z actuator (solenoid/servo) | bought + printed | $15 |
| Down-camera (USB/Pi) | bought | $25 |
| Up-camera (bottom vision) | bought | $25 |
| Add-on MCU (Pi Pico) — if M42 pins insufficient | bought | $10 |
| BlindsFeeder strip holders | **printed** | ~$2 filament |
| Tubing, nozzles, wiring | bought | $25 |
| OpenPnP software | free | $0 |
| **Add-on total** (printer owned) | | **~$160** |
| **Total** (with used Ender) | | **~$320–470** ✅ |

## The hard problems → answers

1. **Mounting** → 3DPlacer bracket replaces hotend on X-carriage. Reuse.
2. **Controlling the printer** → OpenPnP GcodeDriver streams gcode over USB to Marlin.
3. **Vacuum + rotation + pick-Z** → M42 spare pins (or Pi Pico). OpenPnP drives θ as an axis.
4. **Pick-Z speed** → dedicated fast down-stroke actuator (the one mechanical improvement on 3DPlacer).
5. **Feeders** → BlindsFeeder (printable, oriented, vision-located) — reuse, NOT loose tray.
6. **The glue (our build)** → hw_toolkit: KiCad design → OpenPnP job + agent orchestration + AOI.

## Bed-slinger gotchas (plan for them)

- Feeders + board **both on the bed** → consistent frame.
- **Gentle acceleration** → parts don't shift.
- **Clamp the board** to the bed.

## What's printed vs bought / reused

- **Printed:** toolhead bracket, pick-Z housing, BlindsFeeder strip holders, camera mounts.
- **Bought:** vacuum pump, solenoid, stepper/servo, pick-Z actuator, 2 cameras, (maybe MCU), tubing/nozzles.
- **Reused (free):** OpenPnP (control+vision+BlindsFeeder), 3DPlacer (mechanics), stock Marlin (M42).

## Build phases

```
Phase 0  ✅ target = Ender 3 · reuse OpenPnP + 3DPlacer + BlindsFeeder
Phase 1  assemble 3DPlacer toolhead on Ender + add fast pick-Z; get OpenPnP driving it
Phase 2  print + set up BlindsFeeders; calibrate fiducials + bottom vision
Phase 3  hw_toolkit glue: KiCad placement file → OpenPnP job + agent orchestration
Phase 4  AOI (camera vs Gerber) + reliability pass
Phase 5  test placement accuracy (0603/THT) → iterate → publish STLs + glue code
```

## Software — reuse + the moat

- **Reuse OpenPnP** as the controller + vision engine (don't rebuild it).
- **Build the glue** in `hw_toolkit`: KiCad design → OpenPnP job generation, agent orchestration (one "make" → routed → carved → placed), AOI compare. The agent-driven, design-to-placed-board flow is the unsold part. The hardware + OpenPnP are off-the-shelf.

## Open questions to verify (next swarm, when ready)

1. Placement accuracy on an Ender with OpenPnP bottom-vision — smallest reliable part (0603? 0402?).
2. Pick-Z actuator choice — solenoid vs micro-servo, force/speed for tape pickup.
3. OpenPnP GcodeDriver throughput on Marlin (or move to Klipper) for acceptable speed.
4. Bed area: how many BlindsFeeder strips + board fit in 220×220 per run.

## Scope guardrails

- ✅ v1: THT + 0603-class SMD, OpenPnP + BlindsFeeder, sub-$500, Ender 3.
- ⚠️ stretch: 0402/fine pitch (better vision + less backlash).
- ❌ not v1: BGA, automated reflow, motorized feeders, the laser (bought).
