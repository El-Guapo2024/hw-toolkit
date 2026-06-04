# Module — Feeder Add-On (BlindsFeeder)

*Status: design. Presents oriented SMD parts for pickup — printable, passive, cheap. Reuses OpenPnP's BlindsFeeder. See [[cheap-pnp-addon-plan]], [[shopping-list]].*

## Purpose
Hold cut SMD tape strips so the nozzle can pick **oriented** parts in sequence — without motorized feeders ($20–150 each) and without loose-tray polarity errors.

## Approach: BlindsFeeder (OpenPnP, printed)
A printed block with channels holds cut tape; a sliding "blinds" cover replaces the tape's cover film. The **nozzle itself pushes the blind open** to expose one pocket → no motor, no wiring. Fiducials on the block let the down-camera locate every pocket.

## Printed vs bought
- **Printed:** strip-holder block (channels sized to tape width) + sliding blind cover + fiducial markers. OpenPnP provides a **parametric generator** — set tape width + lane count.
- **Bought:** nothing. (Your parts already come on cut tape; double-sided tape mounts the block to the bed.)

## Interfaces
- **Mechanical:** sits on the Ender bed (on the bed → same frame as the board, bed-slinger-safe). Mount with tape/clips.
- **Vision:** fiducials read by the down-camera ([[module-camera]]).
- **Software:** OpenPnP `BlindsFeeder` config — pocket pitch, lane positions, fiducial coords.
- **Actuation:** the pick-place nozzle ([[module-pickplace]]) opens/closes the blinds — no separate actuator.

## Design parameters
| Param | Typical |
|-------|---------|
| Tape widths | 8mm (most passives/SOT), 12mm (bigger) |
| Pocket pitch | 2mm (0402), 4mm (0603/0805), 8mm (larger) |
| Lanes | as many as fit 220×220 minus the board |
| Fiducials | ≥2 per block for registration |

## Constraints
- **Bed area 220×220** shared with the board → limited lanes/part-variety per run. Swap strips between runs.
- **Bed-slinger** → mount on the bed + gentle accel so parts don't shift.
- Strips must sit **flat + registered** → snug channels.

## Build steps
1. Generate + print a BlindsFeeder array (parametric, per your tape widths).
2. Lay cut tape strips in channels, slide the blind cover on.
3. Tape/clip the block to the bed.
4. In OpenPnP: add BlindsFeeder, set fiducials + pocket pitch, calibrate.

## Test
- Pick repeatability from first/middle/last pocket of a lane.
- Blind slides open/closed reliably with the nozzle.
- Reload a spent lane.

## Open questions
- How many lanes realistically fit 220×220 alongside a board?
- Smallest part that stays seated in the pocket (0402? 0201?).
- Blind-slide reliability with the Ender's nozzle force.
