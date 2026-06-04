# Module — Pick-Place Toolhead Add-On

*Status: design. The hand — pick (vacuum), rotate (θ), lower/raise (pick-Z), place. Replaces the Ender hotend. Fork 3DPlacer. See [[cheap-pnp-addon-plan]], [[shopping-list]].*

## Purpose
Pick a part off the feeder, rotate it to the right angle, lower it onto the pad, release. The Ender's X/Y position it; this module adds the pick mechanics.

## Sub-functions
| Function | How |
|----------|-----|
| Pick/hold | vacuum nozzle (pump + solenoid; release = vent or reverse) |
| Rotate (θ) | small stepper/servo + printed gear |
| Down-stroke (pick-Z) | fast actuator (solenoid/servo) — NOT the slow Ender Z |
| Position (X/Y) | reuse the Ender's motion |

## Printed vs bought
- **Printed:** toolhead bracket (replaces hotend on X-carriage), nozzle holder, θ-gear housing, pick-Z housing.
- **Bought:** θ motor (NEMA8/servo), pick-Z actuator (solenoid/micro-servo), vacuum pump + solenoid valve, nozzle tips, bearings/springs/magnets, MCU (Pi Pico).

## Interfaces
- **Mechanical:** bolts to the Ender X-carriage (2 screws, swaps hotend). Reversible.
- **Control:** Pi Pico (or spare board pins via Marlin `M42`) drives vacuum solenoid + θ + pick-Z. Host (OpenPnP) commands it.
- **Software:** OpenPnP nozzle config — tip offset, θ as an axis, vacuum on/off, pick/place Z moves.
- **Feeder:** the nozzle also opens the [[module-feeder]] blinds.

## Design notes
- **Vacuum path:** pump → solenoid → nozzle. Optional vacuum **pressure sensor** = pick-detect (did it grab the part?). Cheap, worth it.
- **Pick-Z:** dedicated fast down-stroke (solenoid/servo) → speed + protects parts. Reuse Ender Z only as a fallback (slow).
- **θ range:** ±180°+ for any orientation; printed gear reduction for resolution.
- **Nozzle change:** manual for v1 (swap tip for part size).

## Build steps
1. Print bracket + housings.
2. Assemble nozzle + θ-motor + gear + pick-Z actuator into the bracket.
3. Swap Ender hotend → toolhead bracket.
4. Wire vacuum solenoid + θ + pick-Z (+ optional vacuum sensor) to the Pi Pico.
5. OpenPnP: define the nozzle, tip offset, θ axis, vacuum + Z actions.

## Test
- Pick a part off a feeder pocket → vacuum holds through a move.
- θ rotates to commanded angle accurately.
- Pick-Z down/up reliable, gentle on the part.
- Place on a pad → release cleanly.
- (If sensor) pick-detect flags a missed pick.

## Open questions
- Pick-Z actuator: push-pull solenoid vs micro-servo (force/speed/repeatability).
- Vacuum pick-detect sensor worth it? (yes, likely).
- Nozzle tip sizes for the part range; quick manual-change scheme.
- θ-motor: stepper (open-loop, OpenPnP axis) vs servo.
