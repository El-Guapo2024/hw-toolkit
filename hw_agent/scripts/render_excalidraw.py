#!/usr/bin/env python3
"""Rasterize an Excalidraw v2 JSON file to a PNG via PIL.

Usage:
    python render_excalidraw.py <input.excalidraw> <output.png>

Or as a library:
    from render_excalidraw import render
    render("architecture.excalidraw", "architecture.png")

This is the reference renderer for the /architecture-diagram skill. It exists so
each project doesn't re-invent the rectangle/text/arrow drawing code from scratch.

Supported Excalidraw element types:
  - rectangle   (with backgroundColor fill, strokeColor outline,
                 best-effort rounded corners when `roundness` is set)
  - text        (honors fontSize, textAlign, verticalAlign; fontFamily 5
                 maps to Helvetica/Arial Unicode)
  - arrow       (line + filled triangular arrowhead at the end point;
                 honors strokeStyle="dashed" by emitting dashed segments)

Unsupported (will be skipped with a warning printed to stderr):
  - ellipse     (the architecture-diagram skill standardizes on rectangles)
  - diamond     (likewise)
  - line        (use `arrow` with startArrowhead/endArrowhead = null instead)
  - freedraw    (not used by the architecture skill)
  - image       (not used by the architecture skill)
  - frame       (not used by the architecture skill)

Rendering details:
  - SCALE = 2 supersample, then LANCZOS downsample for crisp text.
  - Bounding box is computed over non-deleted elements; output canvas is
    (bbox + 80px padding) at 1x.
  - Fonts are looked up at /System/Library/Fonts/Supplemental/Arial Unicode.ttf
    first (broad glyph coverage) then /System/Library/Fonts/Helvetica.ttc as
    fallback. If both miss, PIL's bitmap default is used (text will be tiny —
    upstream skill should sanitize to ASCII-only and use a real font).
  - The renderer does not crash on non-ASCII characters; missing glyphs render
    as `□` boxes. Sanitization is the caller's responsibility (see the skill).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SCALE = 2
PAD = 40  # px on each side, at 1x


# ---------------------------------------------------------------------------
# Font lookup
# ---------------------------------------------------------------------------

_FONT_PATHS = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]

_font_cache: dict[int, ImageFont.ImageFont] = {}


def _font(size_pt: int) -> ImageFont.ImageFont:
    """Return a TrueType font at `size_pt * SCALE`, falling back to default."""
    key = size_pt
    cached = _font_cache.get(key)
    if cached is not None:
        return cached
    px = max(1, int(size_pt * SCALE))
    for path in _FONT_PATHS:
        try:
            f = ImageFont.truetype(path, px)
            _font_cache[key] = f
            return f
        except OSError:
            continue
    f = ImageFont.load_default()
    _font_cache[key] = f
    return f


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _bbox(elements: list[dict]) -> tuple[float, float, float, float]:
    """Return (min_x, min_y, max_x, max_y) over non-deleted elements."""
    xs: list[float] = []
    ys: list[float] = []
    for e in elements:
        x = float(e.get("x", 0))
        y = float(e.get("y", 0))
        w = float(e.get("width", 0) or 0)
        h = float(e.get("height", 0) or 0)
        # arrows can have negative width/height via point deltas; use points too
        xs.append(x)
        ys.append(y)
        xs.append(x + w)
        ys.append(y + h)
        for px, py in e.get("points", []) or []:
            xs.append(x + float(px))
            ys.append(y + float(py))
    if not xs:
        return 0.0, 0.0, 100.0, 100.0
    return min(xs), min(ys), max(xs), max(ys)


def _dashed_segments(
    x1: float, y1: float, x2: float, y2: float, dash: float = 8.0, gap: float = 6.0
) -> list[tuple[float, float, float, float]]:
    """Split a line into dashed pieces. Returns a list of (x1,y1,x2,y2) tuples."""
    dx = x2 - x1
    dy = y2 - y1
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return []
    ux, uy = dx / length, dy / length
    out: list[tuple[float, float, float, float]] = []
    pos = 0.0
    while pos < length:
        a = pos
        b = min(pos + dash, length)
        out.append((x1 + ux * a, y1 + uy * a, x1 + ux * b, y1 + uy * b))
        pos += dash + gap
    return out


# ---------------------------------------------------------------------------
# Element renderers
# ---------------------------------------------------------------------------


def _draw_rectangle(draw: ImageDraw.ImageDraw, e: dict, ox: float, oy: float) -> None:
    x = (e["x"] + ox) * SCALE
    y = (e["y"] + oy) * SCALE
    w = e.get("width", 0) * SCALE
    h = e.get("height", 0) * SCALE
    fill = e.get("backgroundColor") or None
    if fill in ("transparent", ""):
        fill = None
    stroke = e.get("strokeColor") or "#1e1e1e"
    sw = max(1, int(e.get("strokeWidth", 2) * SCALE))
    radius = 0
    if e.get("roundness"):
        # Excalidraw uses a small adaptive radius; matching exactly isn't
        # important — pick something visually close.
        radius = int(min(w, h) * 0.08)
    if radius > 0:
        draw.rounded_rectangle(
            [x, y, x + w, y + h], radius=radius, fill=fill, outline=stroke, width=sw
        )
    else:
        draw.rectangle([x, y, x + w, y + h], fill=fill, outline=stroke, width=sw)


def _draw_text(draw: ImageDraw.ImageDraw, e: dict, ox: float, oy: float) -> None:
    text = e.get("text", "")
    if not text:
        return
    size_pt = int(e.get("fontSize", 16))
    f = _font(size_pt)
    color = e.get("strokeColor") or "#1e1e1e"
    align = e.get("textAlign", "left")
    valign = e.get("verticalAlign", "top")
    bx = (e["x"] + ox) * SCALE
    by = (e["y"] + oy) * SCALE
    bw = e.get("width", 0) * SCALE
    bh = e.get("height", 0) * SCALE

    # Multi-line support: Excalidraw stores `\n` literal newlines.
    lines = text.split("\n")
    line_h = int(size_pt * SCALE * 1.25)
    total_h = line_h * len(lines)

    if valign == "middle":
        y0 = by + (bh - total_h) / 2
    elif valign == "bottom":
        y0 = by + bh - total_h
    else:
        y0 = by

    for i, line in enumerate(lines):
        try:
            tw = draw.textlength(line, font=f)
        except (AttributeError, TypeError):
            tw = len(line) * size_pt * SCALE * 0.55
        if align == "center":
            x0 = bx + (bw - tw) / 2
        elif align == "right":
            x0 = bx + bw - tw
        else:
            x0 = bx
        draw.text((x0, y0 + i * line_h), line, font=f, fill=color)


def _draw_arrow(draw: ImageDraw.ImageDraw, e: dict, ox: float, oy: float) -> None:
    points = e.get("points") or []
    if len(points) < 2:
        return
    base_x = (e["x"] + ox) * SCALE
    base_y = (e["y"] + oy) * SCALE
    abs_pts = [(base_x + px * SCALE, base_y + py * SCALE) for px, py in points]
    color = e.get("strokeColor") or "#1e1e1e"
    sw = max(1, int(e.get("strokeWidth", 2) * SCALE))
    dashed = e.get("strokeStyle") == "dashed"

    # draw segments along all sequential point pairs
    for (x1, y1), (x2, y2) in zip(abs_pts, abs_pts[1:]):
        if dashed:
            for sx1, sy1, sx2, sy2 in _dashed_segments(
                x1, y1, x2, y2, dash=8 * SCALE, gap=6 * SCALE
            ):
                draw.line([sx1, sy1, sx2, sy2], fill=color, width=sw)
        else:
            draw.line([x1, y1, x2, y2], fill=color, width=sw)

    # arrowhead at end (filled triangle along last segment direction)
    if e.get("endArrowhead") == "arrow":
        x_prev, y_prev = abs_pts[-2]
        x_end, y_end = abs_pts[-1]
        _arrowhead(draw, x_prev, y_prev, x_end, y_end, color, sw)
    if e.get("startArrowhead") == "arrow":
        x_next, y_next = abs_pts[1]
        x_start, y_start = abs_pts[0]
        _arrowhead(draw, x_next, y_next, x_start, y_start, color, sw)


def _arrowhead(
    draw: ImageDraw.ImageDraw,
    x_from: float,
    y_from: float,
    x_to: float,
    y_to: float,
    color: str,
    sw: int,
) -> None:
    dx = x_to - x_from
    dy = y_to - y_from
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return
    ux, uy = dx / length, dy / length
    head_len = max(8 * SCALE, sw * 4)
    head_w = max(5 * SCALE, sw * 2.5)
    # back-step from the tip along the line
    bx = x_to - ux * head_len
    by = y_to - uy * head_len
    # perpendicular
    px, py = -uy, ux
    p1 = (bx + px * head_w, by + py * head_w)
    p2 = (bx - px * head_w, by - py * head_w)
    draw.polygon([(x_to, y_to), p1, p2], fill=color)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render(input_path: str | Path, output_path: str | Path) -> None:
    """Render `input_path` (Excalidraw JSON) to `output_path` (PNG)."""
    src = Path(input_path)
    dst = Path(output_path)
    data = json.loads(src.read_text())
    elements = [e for e in data.get("elements", []) if not e.get("isDeleted")]
    if not elements:
        raise ValueError(f"{src}: no non-deleted elements to render")

    min_x, min_y, max_x, max_y = _bbox(elements)
    W = int(math.ceil(max_x - min_x)) + PAD * 2
    H = int(math.ceil(max_y - min_y)) + PAD * 2
    ox = PAD - min_x
    oy = PAD - min_y

    img = Image.new("RGB", (W * SCALE, H * SCALE), "white")
    draw = ImageDraw.Draw(img)

    # Pass 1: rectangles + (unsupported shape warnings).
    # Pass 2: text. Pass 3: arrows on top.
    for e in elements:
        t = e.get("type")
        if t == "rectangle":
            _draw_rectangle(draw, e, ox, oy)
        elif t in ("ellipse", "diamond", "line", "freedraw", "image", "frame"):
            print(
                f"render_excalidraw: skipping unsupported element type '{t}' "
                f"(id={e.get('id')!r})",
                file=sys.stderr,
            )
    for e in elements:
        if e.get("type") == "text":
            _draw_text(draw, e, ox, oy)
    for e in elements:
        if e.get("type") == "arrow":
            _draw_arrow(draw, e, ox, oy)

    img = img.resize((W, H), Image.LANCZOS)
    img.save(str(dst), "PNG")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            "Usage: python render_excalidraw.py <input.excalidraw> <output.png>",
            file=sys.stderr,
        )
        return 2
    render(argv[1], argv[2])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
