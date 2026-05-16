"""Focused PNG renders of a `.kicad_sch` — the agent's eyes.

`get_render(kicad_sch, zoom_to="U1")` returns a PNG cropped to that
component's bbox + padding. Used by MCP tools so the agent sees its own
edits without a separate /review-schem round trip.

Pipeline:
    .kicad_sch ──► kicad-cli sch export svg ──► full-sheet SVG (mm units)
                   │
                   └── parse symbol position + lib bbox from same file
                       (embedded `(lib_symbols ...)` block — no external
                        lib lookup needed)

    cropped SVG ──► cairosvg ──► PNG bytes ──► base64

Cache key is (mtime, zoom_to, padding, dpi). Hits skip kicad-cli + cairo.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import sexpdata

from hw_agent.schematics.kicad_paths import kicad_cli


# ─── S-expression helpers (local — kicad_lib's are private) ────────────────

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


# ─── BBox extraction ───────────────────────────────────────────────────────

@dataclass
class _LibBBox:
    """Lib-space bbox in KiCad lib coords (mm, Y-up). (0,0,0,0) if unknown."""
    min_x: float = 0.0
    min_y: float = 0.0
    max_x: float = 0.0
    max_y: float = 0.0

    def is_empty(self) -> bool:
        return self.min_x == 0 and self.max_x == 0 and self.min_y == 0 and self.max_y == 0

    @classmethod
    def from_points(cls, xs: list[float], ys: list[float]) -> "_LibBBox":
        if not xs:
            return cls()
        return cls(min(xs), min(ys), max(xs), max(ys))


def _walk_collect_xy(node, xs: list[float], ys: list[float]) -> None:
    """Recursively collect (x, y) coords from primitives of a lib symbol.

    Looks at rectangles (start/end), polylines (pts/xy), arcs (start/mid/end),
    circles (center+radius), and pin (at). Ignores text effects/property
    coords — those bloat the bbox without representing the symbol body.
    """
    if not isinstance(node, list):
        return
    tag = _tag(node)
    if tag in ("property", "text", "pin_names", "pin_numbers", "effects"):
        return  # skip text-positioning metadata

    if tag == "rectangle":
        for child in _children(node, "start") + _children(node, "end"):
            if len(child) >= 3:
                xs.append(float(child[1])); ys.append(float(child[2]))
    elif tag == "polyline":
        pts_node = next((c for c in node[1:] if isinstance(c, list) and _tag(c) == "pts"), None)
        if pts_node:
            for xy in _children(pts_node, "xy"):
                if len(xy) >= 3:
                    xs.append(float(xy[1])); ys.append(float(xy[2]))
    elif tag == "arc":
        for sub in ("start", "mid", "end"):
            n = next((c for c in node[1:] if isinstance(c, list) and _tag(c) == sub), None)
            if n and len(n) >= 3:
                xs.append(float(n[1])); ys.append(float(n[2]))
    elif tag == "circle":
        center = next((c for c in node[1:] if isinstance(c, list) and _tag(c) == "center"), None)
        radius_node = next((c for c in node[1:] if isinstance(c, list) and _tag(c) == "radius"), None)
        r = float(radius_node[1]) if radius_node and len(radius_node) >= 2 else 0.0
        if center and len(center) >= 3:
            cx, cy = float(center[1]), float(center[2])
            xs.extend([cx - r, cx + r]); ys.extend([cy - r, cy + r])
    elif tag == "pin":
        at = next((c for c in node[1:] if isinstance(c, list) and _tag(c) == "at"), None)
        if at and len(at) >= 3:
            xs.append(float(at[1])); ys.append(float(at[2]))

    for child in node[1:]:
        if isinstance(child, list):
            _walk_collect_xy(child, xs, ys)


def _lib_bbox_from_sch(sch_tree: list, lib_id: str) -> _LibBBox:
    """Find lib_id inside `(lib_symbols ...)` and compute its bbox."""
    lib_symbols_node = next(
        (c for c in sch_tree[1:] if isinstance(c, list) and _tag(c) == "lib_symbols"),
        None,
    )
    if not lib_symbols_node:
        return _LibBBox()

    for sym in _children(lib_symbols_node, "symbol"):
        if _str_value(sym) == lib_id:
            xs, ys = [], []
            _walk_collect_xy(sym, xs, ys)
            return _LibBBox.from_points(xs, ys)
    return _LibBBox()


@dataclass
class _Placement:
    ref: str
    lib_id: str
    at: tuple[float, float]
    rot: float


def _placements(sch_tree: list) -> list[_Placement]:
    """All top-level (symbol ...) placements with their reference + lib_id."""
    out = []
    for sym in _children(sch_tree, "symbol"):
        # lib_id
        lib_id_node = next((c for c in sym[1:] if isinstance(c, list) and _tag(c) == "lib_id"), None)
        if not lib_id_node:
            continue
        lib_id = _str_value(lib_id_node)
        # at
        at_node = next((c for c in sym[1:] if isinstance(c, list) and _tag(c) == "at"), None)
        if not at_node or len(at_node) < 3:
            continue
        x, y = float(at_node[1]), float(at_node[2])
        rot = float(at_node[3]) if len(at_node) >= 4 else 0.0
        # reference
        ref = ""
        for prop in _children(sym, "property"):
            if _str_value(prop) == "Reference" and len(prop) >= 3:
                v = prop[2]
                ref = v.value() if isinstance(v, sexpdata.Symbol) else str(v)
                break
        out.append(_Placement(ref=ref, lib_id=lib_id, at=(x, y), rot=rot))
    return out


def find_symbol_bbox(
    kicad_sch: Path, ref: str, padding_mm: float = 5.0,
) -> Optional[tuple[float, float, float, float]]:
    """Return absolute schematic-space bbox (mm, Y-down) for the symbol named `ref`.

    Returns None if the reference isn't found or the lib has no body.
    The bbox already includes padding.
    """
    text = kicad_sch.read_text()
    tree = sexpdata.loads(text)

    placements = _placements(tree)
    placement = next((p for p in placements if p.ref == ref), None)
    if not placement:
        return None

    lib_bbox = _lib_bbox_from_sch(tree, placement.lib_id)
    if lib_bbox.is_empty():
        # Fall back to a small box around the placement so we still return *something*
        x, y = placement.at
        return (x - 10, y - 10, x + 10, y + 10)

    ax, ay = placement.at
    # Rotation: lib coords are (lib_x, lib_y) Y-up. Rotation rotates the body,
    # so we apply the rotation to the four corners of the lib bbox before
    # mapping to absolute. Rot is in degrees, CCW in lib-space.
    import math
    rad = math.radians(placement.rot)
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    corners = [
        (lib_bbox.min_x, lib_bbox.min_y), (lib_bbox.max_x, lib_bbox.min_y),
        (lib_bbox.min_x, lib_bbox.max_y), (lib_bbox.max_x, lib_bbox.max_y),
    ]
    abs_xs, abs_ys = [], []
    for lx, ly in corners:
        rx = lx * cos_r - ly * sin_r
        ry = lx * sin_r + ly * cos_r
        # KiCad lib is Y-up, schematic is Y-down — flip Y when mapping to abs.
        abs_xs.append(ax + rx)
        abs_ys.append(ay - ry)

    pad = padding_mm
    return (min(abs_xs) - pad, min(abs_ys) - pad,
            max(abs_xs) + pad, max(abs_ys) + pad)


# ─── SVG export + crop ─────────────────────────────────────────────────────

_VIEWBOX_RE = re.compile(
    r'width="[\d.]+mm"\s+height="[\d.]+mm"\s+viewBox="[\d.\s\-]+"',
    re.IGNORECASE,
)


def _kicad_sch_to_svg(kicad_sch: Path, out_dir: Path) -> Path:
    """Run kicad-cli to export an SVG for the sheet. Returns the SVG path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cli = kicad_cli()
    proc = subprocess.run(
        [cli, "sch", "export", "svg", "--output", str(out_dir) + "/", str(kicad_sch)],
        capture_output=True, text=True,
    )
    svg_path = out_dir / f"{kicad_sch.stem}.svg"
    if proc.returncode != 0 or not svg_path.exists():
        raise RuntimeError(
            f"kicad-cli failed to produce SVG for {kicad_sch.name} "
            f"(rc={proc.returncode}):\n"
            f"  stdout: {proc.stdout.strip()[:300]}\n"
            f"  stderr: {proc.stderr.strip()[:300]}"
        )
    return svg_path


def _crop_svg(svg_path: Path, bbox: tuple[float, float, float, float], out_path: Path) -> Path:
    """Rewrite the SVG's width/height/viewBox to bbox. Output in mm.

    If KiCad's SVG attribute order changes and our regex misses, log
    once and pass through uncropped — better a full sheet than a
    crash, but the user should know the crop failed."""
    import warnings
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
        warnings.warn(
            f"crop regex didn't match {svg_path.name}; KiCad's SVG "
            f"attribute order may have changed. Returning uncropped sheet.",
            stacklevel=2,
        )
        cropped = text
    out_path.write_text(cropped)
    return out_path


def _svg_to_png(svg_path: Path, png_path: Path, dpi: int = 200) -> Path:
    """Rasterize SVG → PNG via cairosvg. dpi controls output resolution.
    Clamped at 72 DPI minimum so sub-readable renders don't get cached."""
    import cairosvg
    # cairosvg uses 96 DPI as base; scale = dpi / 96 maps mm→px linearly.
    scale = max(dpi, 72) / 96.0
    cairosvg.svg2png(
        url=str(svg_path), write_to=str(png_path),
        scale=scale, background_color="white",
    )
    return png_path


# ─── Cache ─────────────────────────────────────────────────────────────────

def _cache_key(kicad_sch: Path, zoom_to: Optional[str], padding_mm: float, dpi: int) -> str:
    h = hashlib.sha1()
    h.update(str(kicad_sch.resolve()).encode())
    h.update(str(int(kicad_sch.stat().st_mtime_ns)).encode())
    h.update(str(zoom_to).encode())
    h.update(f"{padding_mm:.3f}|{dpi}".encode())
    return h.hexdigest()[:16]


def _cache_dir(kicad_sch: Path) -> Path:
    d = kicad_sch.parent / ".render_cache"
    d.mkdir(exist_ok=True)
    return d


# ─── Public entry point ────────────────────────────────────────────────────

def render_focused_png(
    kicad_sch: Path,
    zoom_to: Optional[str] = None,
    padding_mm: float = 5.0,
    dpi: int = 200,
) -> dict:
    """Render `kicad_sch` to PNG + SVG, optionally cropped around
    `zoom_to` reference.

    Returns:
        {
            "png_path": Path to the cached PNG (rasterized — VLM sees this),
            "svg_path": Path to the source SVG (text — for grep / coord lookups),
            "bbox_mm": (x0, y0, x1, y1) used for the crop, or None if full sheet,
            "ref": zoom_to ref (echoed),
            "from_cache": bool,
        }
    """
    kicad_sch = Path(kicad_sch).resolve()
    if not kicad_sch.exists():
        raise FileNotFoundError(f"schematic not found: {kicad_sch}")

    key = _cache_key(kicad_sch, zoom_to, padding_mm, dpi)
    cache = _cache_dir(kicad_sch)
    png_path = cache / f"{kicad_sch.stem}.{key}.png"
    cropped_svg = cache / f"{kicad_sch.stem}.{key}.svg"

    bbox = None
    if zoom_to:
        bbox = find_symbol_bbox(kicad_sch, zoom_to, padding_mm=padding_mm)

    if png_path.exists():
        # Cache hit: cropped_svg sits next to the PNG when bbox was
        # set; otherwise the full-sheet kicad-cli output is at <stem>.svg.
        svg = cropped_svg if cropped_svg.exists() else cache / f"{kicad_sch.stem}.svg"
        return {"png_path": png_path, "svg_path": svg, "bbox_mm": bbox,
                "ref": zoom_to, "from_cache": True}

    full_svg = _kicad_sch_to_svg(kicad_sch, out_dir=cache)
    if bbox is not None:
        _crop_svg(full_svg, bbox, cropped_svg)
        _svg_to_png(cropped_svg, png_path, dpi=dpi)
        svg_out = cropped_svg
    else:
        _svg_to_png(full_svg, png_path, dpi=dpi)
        svg_out = full_svg

    return {"png_path": png_path, "svg_path": svg_out, "bbox_mm": bbox,
            "ref": zoom_to, "from_cache": False}
