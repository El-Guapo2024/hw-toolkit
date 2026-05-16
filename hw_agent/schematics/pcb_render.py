"""Focused PNG renders of a `.kicad_pcb` — the agent's eyes on the board.

`render_pcb(kicad_pcb, zoom_to="U1")` returns a PNG cropped to a
component's bbox + padding. Used by `with_render=True` on PCB-side MCP
tools (`move_footprint`, `pcb_save`, etc.) so the agent sees what each
edit produced as an inline image.

Why PNG (not SVG): Claude Code's Read tool only rasterizes raster
formats — SVG comes back as raw XML, which the VLM can't pattern-match
into a layout. PNG is round-tripped through the VLM as pixels.

Pipeline:
    .kicad_pcb ──► kicad-cli pcb export svg ──► full-board SVG (mm units)
                   │
                   ├── viewBox rewrite to bbox crop
                   │
                   └── cairosvg.svg2png ──► PNG (rasterized, agent sees)
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Optional

import sexpdata

from hw_agent.schematics.kicad_paths import kicad_cli


# ─── S-expression helpers (local) ──────────────────────────────────────────

def _tag(node) -> str:
    if isinstance(node, list) and node:
        head = node[0]
        if isinstance(head, sexpdata.Symbol):
            return head.value()
        return str(head)
    return ""


def _children(node, tag: str) -> list:
    if not isinstance(node, list):
        return []
    return [c for c in node[1:] if isinstance(c, list) and _tag(c) == tag]


def _str_value(node) -> str:
    if not node or len(node) < 2:
        return ""
    v = node[1]
    if isinstance(v, sexpdata.Symbol):
        return v.value()
    return str(v)


# ─── Footprint bbox extraction ─────────────────────────────────────────────

def find_footprint_bbox(
    kicad_pcb: Path, ref: str, padding_mm: float = 5.0,
) -> Optional[tuple[float, float, float, float]]:
    """Return absolute PCB-space bbox (mm) for the footprint named `ref`.

    Approximates by `at` ± half-package-size; padded for context.
    Returns None if the ref isn't found.
    """
    text = kicad_pcb.read_text()
    tree = sexpdata.loads(text)

    for fp in _children(tree, "footprint"):
        # Locate placement + reference field
        at_node = next((c for c in fp[1:] if isinstance(c, list) and _tag(c) == "at"),
                       None)
        if not at_node or len(at_node) < 3:
            continue
        cx, cy = float(at_node[1]), float(at_node[2])

        # Find Reference field (first property fp_text type "reference")
        fp_ref = ""
        for fp_text in _children(fp, "fp_text"):
            if len(fp_text) >= 3 and _str_value(fp_text) == "reference":
                v = fp_text[2]
                fp_ref = v.value() if isinstance(v, sexpdata.Symbol) else str(v)
                break
        # Newer KiCad uses (property "Reference" "U1" ...)
        if not fp_ref:
            for prop in _children(fp, "property"):
                if _str_value(prop) == "Reference" and len(prop) >= 3:
                    v = prop[2]
                    fp_ref = v.value() if isinstance(v, sexpdata.Symbol) else str(v)
                    break

        if fp_ref != ref:
            continue

        # Estimate package extent from pad positions if present.
        xs, ys = [cx], [cy]
        for pad in _children(fp, "pad"):
            pad_at = next((c for c in pad[1:] if isinstance(c, list) and _tag(c) == "at"),
                          None)
            if pad_at and len(pad_at) >= 3:
                # pad coords are local — rotate ignored for bbox approximation
                xs.append(cx + float(pad_at[1]))
                ys.append(cy + float(pad_at[2]))

        return (
            min(xs) - padding_mm, min(ys) - padding_mm,
            max(xs) + padding_mm, max(ys) + padding_mm,
        )
    return None


# ─── SVG export + crop ─────────────────────────────────────────────────────

_VIEWBOX_RE = re.compile(
    r'width="[\d.]+mm"\s+height="[\d.]+mm"\s+viewBox="[\d.\s\-]+"',
    re.IGNORECASE,
)


def _kicad_pcb_to_svg(kicad_pcb: Path, out_dir: Path) -> Path:
    """Run kicad-cli to export an SVG of the PCB front + back copper +
    silkscreen + edge cuts. Returns the SVG path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{kicad_pcb.stem}.svg"
    cli = kicad_cli()
    proc = subprocess.run(
        [cli, "pcb", "export", "svg",
         "--output", str(out_path),
         "--layers", "F.Cu,B.Cu,F.SilkS,B.SilkS,Edge.Cuts",
         "--page-size-mode", "2",  # fit-to-board
         "--mode-single",
         str(kicad_pcb)],
        capture_output=True, text=True,
    )
    if not out_path.exists():
        raise RuntimeError(
            f"kicad-cli failed to produce SVG for {kicad_pcb.name}:\n"
            f"  stdout: {proc.stdout.strip()[:300]}\n"
            f"  stderr: {proc.stderr.strip()[:300]}"
        )
    return out_path


def _crop_svg(svg_path: Path, bbox: tuple[float, float, float, float],
              out_path: Path) -> Path:
    """Rewrite SVG width/height/viewBox to bbox; reuses render_focus pattern."""
    text = svg_path.read_text()
    x0, y0, x1, y1 = bbox
    w = max(x1 - x0, 1.0)
    h = max(y1 - y0, 1.0)
    new_attrs = (
        f'width="{w:.4f}mm" height="{h:.4f}mm" '
        f'viewBox="{x0:.4f} {y0:.4f} {w:.4f} {h:.4f}"'
    )
    cropped, n = _VIEWBOX_RE.subn(new_attrs, text, count=1)
    if n == 0:
        cropped = text
    out_path.write_text(cropped)
    return out_path


# ─── SVG → PNG ─────────────────────────────────────────────────────────────

def _svg_to_png(svg_path: Path, png_path: Path, dpi: int = 200) -> Path:
    """Rasterize SVG → PNG via cairosvg. dpi controls output resolution."""
    import cairosvg
    scale = dpi / 96.0
    cairosvg.svg2png(
        url=str(svg_path), write_to=str(png_path),
        scale=scale, background_color="white",
    )
    return png_path


# ─── Cache (mtime + zoom params + dpi) ─────────────────────────────────────

def _cache_key(kicad_pcb: Path, zoom_to: Optional[str],
               padding_mm: float, dpi: int) -> str:
    h = hashlib.sha1()
    h.update(str(kicad_pcb.resolve()).encode())
    h.update(str(int(kicad_pcb.stat().st_mtime_ns)).encode())
    h.update(str(zoom_to).encode())
    h.update(f"{padding_mm:.3f}|{dpi}".encode())
    return h.hexdigest()[:16]


def _cache_dir(kicad_pcb: Path) -> Path:
    d = kicad_pcb.parent / ".pcb_render_cache"
    d.mkdir(exist_ok=True)
    return d


# ─── Public entry point ────────────────────────────────────────────────────

def render_pcb(
    kicad_pcb: Path,
    zoom_to: Optional[str] = None,
    padding_mm: float = 5.0,
    dpi: int = 200,
) -> dict:
    """Render `kicad_pcb` to PNG, optionally cropped around `zoom_to`
    footprint reference.

    Returns:
        {"png_path": Path, "svg_path": Path, "bbox_mm": tuple|None,
         "ref": str|None, "from_cache": bool}

    Caller (typically an MCP tool) wraps the PNG in `Image(...)` so the
    VLM sees the rasterized board inline. The SVG path is surfaced for
    structural lookups — grep `<text>U1</text>` for label coords, parse
    layer/track widths as text — when the visual isn't enough.
    """
    kicad_pcb = Path(kicad_pcb).resolve()
    if not kicad_pcb.exists():
        raise FileNotFoundError(f"PCB not found: {kicad_pcb}")

    key = _cache_key(kicad_pcb, zoom_to, padding_mm, dpi)
    cache = _cache_dir(kicad_pcb)
    out_png = cache / f"{kicad_pcb.stem}.{key}.png"
    cropped_svg = cache / f"{kicad_pcb.stem}.{key}.svg"

    bbox = None
    if zoom_to:
        bbox = find_footprint_bbox(kicad_pcb, zoom_to, padding_mm=padding_mm)

    if out_png.exists():
        # Cache hit: cropped_svg sits next to the PNG when bbox was set,
        # otherwise the full-board SVG was saved as `<stem>.svg`.
        svg = cropped_svg if cropped_svg.exists() else cache / f"{kicad_pcb.stem}.svg"
        return {"png_path": out_png, "svg_path": svg,
                "bbox_mm": bbox, "ref": zoom_to, "from_cache": True}

    full_svg = _kicad_pcb_to_svg(kicad_pcb, out_dir=cache)
    if bbox is not None:
        _crop_svg(full_svg, bbox, cropped_svg)
        _svg_to_png(cropped_svg, out_png, dpi=dpi)
        svg_out = cropped_svg
    else:
        _svg_to_png(full_svg, out_png, dpi=dpi)
        svg_out = full_svg

    return {"png_path": out_png, "svg_path": svg_out,
            "bbox_mm": bbox, "ref": zoom_to, "from_cache": False}
