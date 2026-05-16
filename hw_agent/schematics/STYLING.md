# Schematic Styling Guide

The renderer is **dumb on purpose**. It draws exactly what the schem.json
declares — no auto-layout, no auto-shrink, no overlap detection. The
agent (you) is the smart layer: render → look at the SVG → adjust knobs
in the schem.json → re-render → loop until it reads cleanly.

This doc lists every visual knob and how to think about adjusting them.

## Why this split

Hardcoding "shrink the title if it overflows" or "stagger pin labels
when crowded" inside the renderer turns into a never-ending heuristic
arms race. Every project has different conventions. Instead:

- Renderer = `kicad_render_core` (vendored from KiCad). Stable, tested,
  KiCad-quality output.
- Stylistic intent = schema fields. Agent owns these.
- Iteration loop = re-render + visual inspection. Agent owns this too.

If a knob is missing, that's a schema gap — open a request to add it
rather than baking style logic into the renderer.

## Knobs at a glance

### Canvas-level

| Field | Default | Effect |
|-------|---------|--------|
| `width` (mm) | 100 | Canvas width. Shrink if there's whitespace; grow if symbols/labels run off the right edge. |
| `height` (mm) | 60 | Canvas height. Same logic vertically. |
| `grid` (mm) | 2.54 | Grid pitch. Always 2.54 (100 mil) for KiCad-style — don't change unless you know why. |
| `title` | `null` | Optional schematic title rendered top-center. |
| `title_size_mm` | 2.8 | Title text height. Drop to 1.8–2.2 if the title clips on the right side. |

### Symbol-level (KiCad library symbols)

| Field | Default | Effect |
|-------|---------|--------|
| `pin_name_size_mm` | 1.27 | Pin label text height. KiCad standard is 1.27. Drop to 1.0 for dense pinouts (>20 pins). |
| `pin_number_size_mm` | 0.9 | Pin number text height. Drop to 0.7 if numbers overlap names. |
| `reference_size_mm` | 1.5 | "U1"-style reference designator above the body. Bump up for emphasis, down to free vertical space. |

### Standalone label

| Field | Default | Effect |
|-------|---------|--------|
| `fontsize` | 10 | Label font size in CSS px. At SCALE=4 px/mm that's 2.5 mm. Drop for crowded labels. |
| `style` | `"normal"` | One of `"normal"` / `"title"` / `"small"`. (Currently visual only — fontsize controls actual size.) |
| `offset` | `(0, -1.5)` | mm offset from anchor. Use to nudge a label off a wire it's overlapping. |

## How to read a render

When you look at a re-rendered SVG/PNG, scan for these failure modes
and adjust the relevant knob:

| What you see | Likely cause | Knob to adjust |
|--------------|--------------|----------------|
| Title cut off at right edge | `title_size_mm` too big for canvas width | Drop `title_size_mm` to ~1.8, OR widen `canvas.width` |
| Pin numbers overlapping pin names | `pin_number_size_mm` too close to name size | Drop `pin_number_size_mm` to 0.7 |
| Pin labels mashed together vertically | Symbol body too small for pin density | Wider canvas, OR drop `pin_name_size_mm` to 1.0 |
| Reference (U1, C3, …) collides with wire above body | `reference_size_mm` too big | Drop to 1.2 or move symbol down |
| Labels under wires | Standalone label `offset` puts it on top of wire | Increase y-offset (more negative for "above") |
| Empty whitespace dominates | Canvas oversized for content | Shrink `canvas.width` / `canvas.height` |
| Symbols stacked / overlapping | Bad placement | Re-space `symbol.at` coords on the 2.54 mm grid |

## Iteration loop

```
1. Edit schem.json (placement, labels, knobs)
2. python -c "from hw_agent.schematics.schem_renderer import render_schematic; render_schematic('x.schem.json', '/tmp/out.svg')"
3. Convert to PNG: magick -background white -density 200 /tmp/out.svg /tmp/out.png
4. Read /tmp/out.png — find the worst visual problem
5. Pick the knob from the table above; bump it
6. Goto 1
```

Three iterations is usually enough. If you're past five, the geometry
is wrong (canvas size, symbol placement) — fix that first, not the
knobs.

## Defaults are KiCad-conservative

The defaults are chosen to mimic `kicad-cli sch export svg` output.
That style has tradeoffs:
- Pin labels read into the body (KiCad convention)
- Pin numbers sit just above the pin line, partially overlapping the body edge
- Body lines and wires are 0.381 mm (1.5 px at SCALE=4) — visible but not heavy

If you want a cleaner, more "schemdraw" style with thicker lines and
larger labels, the agent can override by setting all the knobs higher.
The renderer doesn't have a style preset switch — declare what you want.

## Adding a new knob

When you find yourself wishing for a knob that doesn't exist (e.g.
"capacitor plate length", "ground triangle width"), the right move is:

1. Add the field to the relevant Pydantic model in `schem_renderer.py`.
2. Plumb it through the `_draw_X` factory in `krc_renderer.py`.
3. Document it in this file under the appropriate Symbol/Canvas section.

Don't add layout logic that decides the knob automatically — that's
the agent's job, not the renderer's.

## What the renderer will not do

- Auto-shrink text to fit
- Auto-stagger overlapping pin labels
- Auto-route wires (manual L-shape only via `elbow_first`)
- Auto-place symbols
- Auto-snap to grid

All of these belong upstream of the rendering — either in the agent's
iteration loop or in a future declarative-layout pre-pass that
generates the schem.json.
