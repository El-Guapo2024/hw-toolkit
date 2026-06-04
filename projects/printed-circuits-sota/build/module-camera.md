# Module — Camera / Vision

*Status: design. The eyes — fiducials, part pose, pick correction, AOI. See [[cheap-pnp-addon-plan]], [[module-edge-vision]] (model side).*

## Purpose
Give the machine sight: locate the board (fiducials), correct each pick (bottom vision), and inspect (AOI). Vision is half of what makes PnP work.

## Two cameras
| Camera | Mount | Job |
|--------|-------|-----|
| **Down-camera** | on the carriage (moves) | board fiducials, feeder fiducials, placement check, AOI |
| **Up-camera (bottom vision)** | fixed in work area | after pick, look up at the part → measure pick offset + rotation → correct before place |

## Printed vs bought
- **Printed:** carriage mount (down-cam), fixed stand (up-cam), LED diffuser/ring.
- **Bought:** 2× USB cameras (fixed-focus, decent res), LED light(s).

## Interfaces
- **USB** → the brain (laptop now / Jetson later).
- **OpenPnP** camera config: down-camera + bottom-vision pipelines (fiducial locator, DetectRectlinearSymmetry).
- **Modular vision API** (`detect(img) → boxes+pose`) → swap in fine-tuned YOLO later ([[module-edge-vision]]).

## Design — the numbers that matter
- **Resolution / FOV / working distance** set accuracy. Target ~**10–30µm/pixel** for reliable 0603 (→ pick a res + lens + distance combo). Bigger FOV = coarser; small FOV = more accurate but more moves.
- **Lighting is critical** — diffuse, controllable. Bad lighting = the #1 vision failure. Ring/dome diffuser + consistent exposure.
- **Camera↔nozzle offset** must be calibrated (down-camera sees where the nozzle will go).

## Calibration
- Pixel scale (mm/pixel) via a known target.
- Down-camera → nozzle XY offset.
- Lens distortion (OpenPnP supports).
- Bottom-vision: nozzle-tip reference + part-centroid pipeline.

## Build steps
1. Print mounts; fit cameras + lights.
2. Down-cam on carriage; up-cam fixed where the nozzle can hover over it.
3. Wire USB to the brain.
4. OpenPnP: add both cameras, calibrate scale + offset + distortion, build fiducial + bottom-vision pipelines.

## Test
- Fiducial detection repeatability (<½ pixel).
- Bottom-vision pick-offset measurement repeatable.
- Detect a deliberately-rotated part → correct before place.

## Open questions
- Cheap-camera resolution enough for the µm/pixel target at 0603/0402?
- Best lighting design (ring vs dome, diffuse).
- Down-cam on carriage adds weight — fine on Ender?
