# Module Design — Gerber → Laser/Mill Toolpath (`hw_toolkit.fab`)

*Status: design. This is the software glue — the agent-driven moat. Fits the hw_toolkit Python-library + Jupyter pivot (typed exceptions, errors-as-feedback). See [[method-landscape]], [[project_library_pivot]].*

## Purpose

Take a finished design (Gerber + Excellon drill) → emit **machine toolpaths** for the patterning hardware (fiber-laser galvo, diode-LDI gantry, or CNC mill) and the drill step, with **camera-fiducial registration** so the job lands on the physical board. Turns "I have a board file" into "the machine makes it" — unattended, agent-callable.

## Why this is the leverage point

Hardware is assemblable from known parts (verifiers confirmed). What does NOT exist as a clean product = **the integrated Gerber→machine pipeline with registration + multi-pass + agent control.** Own the software, own the line.

## Scope (v1)

- IN: 2-layer copper, drills, board outline. Two output modes: **ablation** (fiber laser, chem-free) and **resist-expose** (diode LDI). Plus **mill** (isolation routing) as a third postprocessor.
- OUT (v1): soldermask, silkscreen, multilayer >2, paste.

## Pipeline

```
Gerber (RS-274X) + Excellon
        │  parse
        ▼
  Geometry (shapely polygons, per layer)        ← hw_toolkit.fab.gerber
        │  mode select
        ├─ ABLATION:  copper-to-REMOVE = board_copper − (traces+pads+pours)
        │             → raster or vector fill of the negative
        ├─ LDI:       resist-to-EXPOSE = traces+pads (positive)
        │             → raster the copper-keep regions
        └─ MILL:      isolation = offset trace outlines by tool radius
        │  toolpath gen                          ← hw_toolkit.fab.toolpath
        ▼
  Toolpath (passes, power, speed, focus Z)
        │  registration (camera fiducials → affine)  ← hw_toolkit.fab.align
        ▼
  Postprocessor                                 ← hw_toolkit.fab.post
        ├─ GRBL G-code   (diode-LDI / mill gantry)
        └─ galvo format  (fiber laser: open Balor / .ezd bridge)
        ▼
  Job runner (stream + monitor)                 ← hw_toolkit.fab.run
```

## Package layout (proposed — fits existing hw_toolkit/ structure)

```
hw_toolkit/fab/
  __init__.py
  gerber.py      # parse RS-274X + Excellon → shapely geometry
  geometry.py    # boolean ops, offsets, board-area calc (shared w/ via module)
  toolpath.py    # ablation negative / LDI positive / mill isolation → passes
  align.py       # fiducial detect (opencv) → affine transform
  post/
    grbl.py      # G-code for gantry (laser PWM / spindle)
    galvo.py     # fiber-laser galvo job (Balor/ezd bridge)
  run.py         # stream G-code / trigger galvo, monitor, halt
  errors.py      # FabError + subclasses; inherit from hw_toolkit/exceptions.py base
```

## Typed exceptions (errors-as-feedback, per library pivot)

Inherit from the existing `hw_toolkit/exceptions.py` base so they compose with the rest of the library:

```python
class FabError(HwToolkitError): ...      # base in exceptions.py is HwToolkitError
class GerberParseError(FabError):        # malformed/unsupported aperture
class EmptyLayerError(FabError):         # layer has no copper → likely wrong file
class UnreachableFeatureError(FabError): # trace finer than spot/tool → quote min feature
class FiducialNotFoundError(FabError):   # camera couldn't locate registration marks
class OutOfBoundsError(FabError):        # job exceeds machine travel / board size
class RegistrationError(FabError):       # affine residual > tolerance
```

Each carries actionable context (e.g. `UnreachableFeatureError(feature_um=80, min_um=100, mode="ablation")`) so the agent/notebook can react, not just fail.

## Key design decisions

- **Geometry = shapely.** Boolean ops (negative for ablation, offset for mill) are the core. Reuse `geometry.py` board-area calc to feed the [[module-graphite-via]] coulomb-counter.
- **Mode is a strategy, not a fork.** `toolpath.generate(geom, mode=Ablation|LDI|Mill, params)` — same geometry, different emitter. Easy to add modes.
- **Registration mandatory for double-sided.** Camera finds ≥2 fiducials → affine (rot+trans+scale) → transform all toolpaths. Residual > tol → `RegistrationError` (don't burn a misaligned board).
- **Multi-pass is a parameter.** Ablation needs N passes (10–25 for 35µm Cu). `params.passes/power/speed/z_per_pass`.
- **Min-feature gate.** Before emit, check finest feature vs machine spot/tool → `UnreachableFeatureError` with the number. No silent under-resolution.
- **Notebook-first.** Each stage returns inspectable objects (plot geometry, preview toolpath) so it works cell-by-cell in Jupyter.

## Reuse / don't reinvent

- Gerber/Excellon parsing: lean on **pcb-tools / gerbonara** if license fits; wrap, don't rewrite.
- FlatCAM proves Gerber→G-code isolation works — reference its approach for milling.
- Galvo: open-source **Balor** / galvo-rs for fiber output (avoid Windows-only EZCAD lock-in — a flagged "what's-left").
- KiCad already in your stack (designer-mcp / hw_toolkit.kicad) → export Gerbers straight into this.

## v1 milestones

1. `gerber.py` + `geometry.py` — parse a real KiCad Gerber → shapely, render in notebook. Pass = polygons match KiCad view.
2. `toolpath.py` Ablation + Mill modes → preview passes. Pass = visually correct negative/isolation.
3. `post/grbl.py` → G-code, dry-run on a gantry (pen/low-power). Pass = traces drawn match design.
4. `align.py` — fiducial detect + affine on a webcam shot. Pass = residual < 50µm.
5. `run.py` — stream + halt. End-to-end: Gerber → milled/ablated single-sided board.
6. Excellon → drill cycles + galvo postproc (fiber). → double-sided w/ [[module-graphite-via]].

## Open R&D (what's-left, from the swarm)

- Open galvo control replacing EZCAD (Linux, Gerber-native) — recurring gap across fiber-laser builds.
- Auto-focus / height-map for warped boards (Z-probe → per-region focus).
- Pass-completion vision (reflectance: copper vs substrate) → closed-loop pass count instead of fixed N.
- Double-side fiducial fixture < 50µm.
