"""KiCad symbol library parser.

Reads `.kicad_sym` files (S-expression format) and extracts the geometric
primitives (rectangle, polyline, arc, circle, pin) that make up each symbol.

KiCad uses Y-up coordinates internally. We preserve KiCad coords here; the
renderer flips Y when projecting to SVG (Y-down).

Pin angle convention: angle = direction from outer (tip) end TOWARD the body.
- 0   → body is to the right of pin tip (pin sticks out on left side of part)
- 90  → body is above pin tip          (pin sticks out at bottom)
- 180 → body is to the left of pin tip (pin sticks out on right side)
- 270 → body is below pin tip          (pin sticks out at top)
"""

from __future__ import annotations

import contextvars
import math
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

import sexpdata


# ─── Default library search paths ──────────────────────────────────────────

DEFAULT_LIB_PATHS = [
    Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols"),
    Path("/usr/share/kicad/symbols"),
    Path.home() / ".local/share/kicad/symbols",
]

# Project-local lib dirs held in a contextvar so concurrent Boards (multi-
# kernel, async cells, threadpool) each see their own search path without
# clobbering siblings. Use `register_extra_lib_path` to append.
_extra_lib_paths: contextvars.ContextVar[tuple[Path, ...]] = contextvars.ContextVar(
    "hw_toolkit_extra_lib_paths", default=()
)


def register_extra_lib_path(path: Path) -> None:
    """Add a directory to the symbol-library search path. Idempotent.

    Project-local `hwagent.kicad_sym` files synthesized by `add_custom_ic`
    don't live on the system search paths, so `_find_lib` needs them
    registered here before `add_wire`'s pin resolver can find them.

    Scoped to the current contextvars context (per-task / per-thread).
    """
    p = Path(path)
    current = _extra_lib_paths.get()
    if p not in current:
        _extra_lib_paths.set(current + (p,))


def _find_lib(lib_name: str) -> Path:
    """Locate a .kicad_sym file by library name (e.g. 'Regulator_Linear')."""
    env = os.environ.get("KICAD_SYMBOL_DIR")
    paths = (
        ([Path(env)] if env else [])
        + list(_extra_lib_paths.get())
        + DEFAULT_LIB_PATHS
    )
    for base in paths:
        candidate = base / f"{lib_name}.kicad_sym"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"KiCad symbol library '{lib_name}' not found in any of {paths}"
    )


# ─── Data classes for parsed symbol ────────────────────────────────────────

@dataclass
class Rectangle:
    start: tuple[float, float]
    end: tuple[float, float]


@dataclass
class Polyline:
    points: list[tuple[float, float]]


@dataclass
class Arc:
    start: tuple[float, float]
    mid: tuple[float, float]
    end: tuple[float, float]


@dataclass
class Circle:
    center: tuple[float, float]
    radius: float


@dataclass
class KicadPin:
    name: str
    number: str
    at: tuple[float, float]      # outer (tip) end of the pin in KiCad coords
    angle: float                  # 0/90/180/270 — direction toward body
    length: float                 # pin stub length in mm
    electrical_type: str          # power_in, power_out, passive, input, output, etc.

    @property
    def inner(self) -> tuple[float, float]:
        """Position where the pin meets the body (KiCad coords)."""
        rad = math.radians(self.angle)
        return (
            self.at[0] + self.length * math.cos(rad),
            self.at[1] + self.length * math.sin(rad),
        )


@dataclass
class KicadSymbol:
    name: str
    rectangles: list[Rectangle] = field(default_factory=list)
    polylines: list[Polyline] = field(default_factory=list)
    arcs: list[Arc] = field(default_factory=list)
    circles: list[Circle] = field(default_factory=list)
    pins: list[KicadPin] = field(default_factory=list)
    extends: Optional[str] = None

    def pin(self, name_or_number: str) -> Optional[KicadPin]:
        """Find a pin by name or number."""
        for p in self.pins:
            if p.name == name_or_number or p.number == name_or_number:
                return p
        return None

    def bbox(self) -> tuple[float, float, float, float]:
        """Bounding box (min_x, min_y, max_x, max_y) in KiCad coords."""
        xs, ys = [], []
        for r in self.rectangles:
            xs.extend([r.start[0], r.end[0]])
            ys.extend([r.start[1], r.end[1]])
        for p in self.polylines:
            for x, y in p.points:
                xs.append(x); ys.append(y)
        for a in self.arcs:
            for pt in (a.start, a.mid, a.end):
                xs.append(pt[0]); ys.append(pt[1])
        for c in self.circles:
            xs.extend([c.center[0] - c.radius, c.center[0] + c.radius])
            ys.extend([c.center[1] - c.radius, c.center[1] + c.radius])
        for pin in self.pins:
            xs.append(pin.at[0]); ys.append(pin.at[1])
            ix, iy = pin.inner
            xs.append(ix); ys.append(iy)
        if not xs:
            return (0, 0, 0, 0)
        return (min(xs), min(ys), max(xs), max(ys))


# ─── S-expression helpers ──────────────────────────────────────────────────

def _tag(node) -> str:
    """Get the leading tag of an s-expression list."""
    if isinstance(node, list) and node:
        head = node[0]
        if isinstance(head, sexpdata.Symbol):
            return head.value()
        return str(head)
    return ""


def _children(node, tag: str) -> list:
    """Find direct children matching `tag`."""
    if not isinstance(node, list):
        return []
    return [c for c in node[1:] if isinstance(c, list) and _tag(c) == tag]


def _child(node, tag: str):
    """Find one direct child matching `tag`."""
    cs = _children(node, tag)
    return cs[0] if cs else None


def _str_value(node) -> str:
    """Extract a string from a 1-arg s-expr like (name "VIN" ...)."""
    if not isinstance(node, list) or len(node) < 2:
        return ""
    v = node[1]
    if isinstance(v, str):
        return v
    if isinstance(v, sexpdata.Symbol):
        return v.value()
    return str(v)


def _xy_at(node) -> tuple[float, float]:
    """Parse (xy X Y) or (start X Y) etc."""
    return (float(node[1]), float(node[2]))


def _at_xy_rot(node) -> tuple[float, float, float]:
    """Parse (at X Y rot) — rot may be missing."""
    rot = float(node[3]) if len(node) > 3 else 0.0
    return (float(node[1]), float(node[2]), rot)


# ─── Parser ────────────────────────────────────────────────────────────────

def _parse_symbol_node(node) -> KicadSymbol:
    """Parse a (symbol "name" ...) s-expression into a KicadSymbol.

    KiCad uses nested (symbol "Name_X_Y" ...) for sub-units; we flatten
    everything into one KicadSymbol because we don't care about units here.
    """
    name = _str_value(node)
    sym = KicadSymbol(name=name)

    for item in node[2:]:
        if not isinstance(item, list):
            continue
        tag = _tag(item)

        if tag == "extends":
            sym.extends = _str_value(item)

        elif tag == "rectangle":
            start = _xy_at(_child(item, "start"))
            end = _xy_at(_child(item, "end"))
            sym.rectangles.append(Rectangle(start=start, end=end))

        elif tag == "polyline":
            pts_node = _child(item, "pts")
            pts = [_xy_at(c) for c in pts_node[1:] if _tag(c) == "xy"]
            sym.polylines.append(Polyline(points=pts))

        elif tag == "arc":
            sym.arcs.append(Arc(
                start=_xy_at(_child(item, "start")),
                mid=_xy_at(_child(item, "mid")),
                end=_xy_at(_child(item, "end")),
            ))

        elif tag == "circle":
            center = _xy_at(_child(item, "center"))
            radius = float(_child(item, "radius")[1])
            sym.circles.append(Circle(center=center, radius=radius))

        elif tag == "pin":
            etype = item[1].value() if isinstance(item[1], sexpdata.Symbol) else str(item[1])
            at_node = _child(item, "at")
            x, y, rot = _at_xy_rot(at_node)
            length = float(_child(item, "length")[1])
            name_node = _child(item, "name")
            number_node = _child(item, "number")
            pin_name = _str_value(name_node) if name_node else ""
            pin_num = _str_value(number_node) if number_node else ""
            sym.pins.append(KicadPin(
                name=pin_name,
                number=pin_num,
                at=(x, y),
                angle=rot,
                length=length,
                electrical_type=etype,
            ))

        elif tag == "symbol":
            # Nested unit symbol — recurse and merge
            child = _parse_symbol_node(item)
            sym.rectangles.extend(child.rectangles)
            sym.polylines.extend(child.polylines)
            sym.arcs.extend(child.arcs)
            sym.circles.extend(child.circles)
            sym.pins.extend(child.pins)

    return sym


@lru_cache(maxsize=32)
def _parse_lib_file(lib_path: Path) -> dict[str, KicadSymbol]:
    """Parse a complete .kicad_sym file. Cached by path."""
    text = lib_path.read_text()
    parsed = sexpdata.loads(text)
    symbols = {}
    for node in parsed[1:]:
        if isinstance(node, list) and _tag(node) == "symbol":
            sym = _parse_symbol_node(node)
            symbols[sym.name] = sym
    return symbols


def load_symbol(lib_id: str) -> KicadSymbol:
    """Load a KiCad symbol by lib_id like 'Regulator_Linear:AMS1117-3.3'.

    Resolves `extends` chains so the returned symbol has all primitives
    from its base symbol(s) merged in.
    """
    if ":" not in lib_id:
        raise ValueError(f"lib_id must be 'Library:Name', got {lib_id!r}")
    lib_name, sym_name = lib_id.split(":", 1)
    lib_path = _find_lib(lib_name)
    symbols = _parse_lib_file(lib_path)

    if sym_name not in symbols:
        # Try fuzzy match
        candidates = [n for n in symbols if sym_name.lower() in n.lower()]
        raise KeyError(
            f"Symbol {sym_name!r} not in {lib_name}. "
            f"Did you mean: {candidates[:5]}"
        )

    sym = symbols[sym_name]

    # Resolve extends chain (max depth 4 to avoid cycles)
    visited = {sym_name}
    for _ in range(4):
        if sym.extends is None:
            break
        if sym.extends in visited:
            break
        if sym.extends not in symbols:
            break
        visited.add(sym.extends)
        base = symbols[sym.extends]
        # Inherit primitives from base if not already set on derived
        if not sym.rectangles and not sym.polylines and not sym.arcs and not sym.circles:
            sym.rectangles = list(base.rectangles)
            sym.polylines = list(base.polylines)
            sym.arcs = list(base.arcs)
            sym.circles = list(base.circles)
        if not sym.pins:
            sym.pins = list(base.pins)
        sym.extends = base.extends  # walk up the chain

    return sym
