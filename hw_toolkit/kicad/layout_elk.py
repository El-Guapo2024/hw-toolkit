"""ELK-based schematic placement **and** orthogonal wire routing.

This is the ONLY layout path for hw_toolkit schematics — there is no
heuristic fallback. The planner hands a set of symbols (each with its
empirically-measured pin offsets) plus the pin-to-pin edges, and ELK
returns:

  * a placement anchor per symbol (so connected parts pull together), and
  * orthogonal wire routes (bend points) per edge.

The planner then places each symbol so its pins land exactly on ELK's
fixed port positions, and emits one straight `AddWire` per ELK section
segment. The result is clean Manhattan wiring instead of point-to-point
diagonal fan-out.

ELK is a HARD dependency. If Node or elkjs is missing, or the bridge
fails, `build_elk_layout` raises `LayoutError` — callers do NOT fall back.

Coordinate frame
----------------
Everything is millimetres in KiCad's placed-schematic frame (Y grows
DOWN). Pin offsets are measured empirically by scratch-placing each
symbol at the origin and reading back its pin positions (see the
planner), so we never have to reason about KiCad's lib-coord Y-up vs
placed Y-down conversion. ELK is fed those offsets verbatim and its
output is in the same frame.

Grid
----
KiCad's schematic grid is 1.27 mm (50 mil). Pins, wire endpoints and
bend points must sit on it or ERC raises `endpoint_off_grid` /
`wire_dangling`. Pin offsets are already grid multiples; we snap every
emitted coordinate (anchors + every wire point) to the grid. Because
orthogonal segments share one coordinate between their two endpoints,
independent rounding keeps them axis-aligned, and because port offsets
are grid multiples the snapped anchor still lands each pin exactly on its
routed wire endpoint.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from hw_toolkit.exceptions import LayoutError

_BRIDGE = Path(__file__).resolve().parents[2] / "tools" / "elk" / "bridge.mjs"

# KiCad schematic grid (50 mil).
_GRID_MM = 1.27

# Body margin reserved around a symbol's pin cloud, so ELK keeps routed
# wires from clipping through the symbol graphic. Grid multiple.
_PAD_MM = 2.54

# Top-left margin for the whole drawing (grid multiple: 40 × 1.27).
_ORIGIN_MM = 50.8

# ELK layout knobs. Layered placement, left-to-right, ORTHOGONAL routing
# (the whole point of wiring ELK in). Generous spacing keeps the Manhattan
# routes from stacking on top of one another / the symbol bodies.
_ELK_OPTS = {
    "elk.algorithm": "layered",
    "elk.direction": "RIGHT",
    "elk.edgeRouting": "ORTHOGONAL",
    "elk.spacing.nodeNode": "12.7",
    "elk.layered.spacing.nodeNodeBetweenLayers": "25.4",
    "elk.spacing.edgeNode": "7.62",
    "elk.spacing.edgeEdge": "5.08",
    "elk.layered.spacing.edgeEdgeBetweenLayers": "5.08",
    "elk.layered.spacing.edgeNodeBetweenLayers": "7.62",
    "elk.portConstraints": "FIXED_POS",
}


def _snap(v: float) -> float:
    return round(v / _GRID_MM) * _GRID_MM


# ---------------------------------------------------------------------------
# Inputs / outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ElkNode:
    """One symbol to place.

    `pin_offsets` maps a port id (a string unique within this node — we use
    the KiCad pin *number*) to that pin's (dx, dy) offset from the symbol
    anchor, in placed-schematic mm. Offsets must be grid multiples (they
    always are for KiCad symbols). A node with no pins (shouldn't happen)
    is rejected by `build_elk_layout`.
    """
    id: str
    pin_offsets: dict[str, tuple[float, float]]


@dataclass(frozen=True)
class ElkEdge:
    """A pin-to-pin connection. `src`/`dst` are `(node_id, port_id)`."""
    src: tuple[str, str]
    dst: tuple[str, str]


@dataclass
class LayoutResult:
    """ELK output, translated to KiCad placed-schematic coordinates.

    `anchors[node_id]` is where to place the symbol so its pins land on the
    routed wire endpoints. `wires` is a flat list of orthogonal segments
    `(x1, y1, x2, y2)` (absolute mm, grid-snapped) to draw — one per ELK
    section segment, degenerate segments dropped.
    """
    anchors: dict[str, tuple[float, float]]
    wires: list[tuple[float, float, float, float]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ELK invocation
# ---------------------------------------------------------------------------


def _require_elk() -> str:
    """Return the resolved `node` binary, or raise `LayoutError`."""
    node = shutil.which("node")
    if node is None:
        raise LayoutError(
            reason="node_missing",
            detail="`node` not on PATH; install Node ≥18 to run the ELK router.",
        )
    if not _BRIDGE.exists():
        raise LayoutError(
            reason="bridge_missing",
            detail=f"ELK bridge not found at {_BRIDGE}",
        )
    return node


def _run_bridge(graph: dict) -> dict:
    node = _require_elk()
    try:
        proc = subprocess.run(
            [node, str(_BRIDGE)],
            input=json.dumps(graph),
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired as e:
        raise LayoutError(reason="elk_timeout", detail=str(e)) from e
    if proc.returncode != 0:
        # The bridge prints `Cannot find module 'elkjs'` here when
        # `npm install` was never run in tools/elk.
        raise LayoutError(reason="elk_failed", detail=proc.stderr[:600]) from None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise LayoutError(
            reason="elk_bad_output", detail=proc.stdout[:600] or str(e),
        ) from e


# ---------------------------------------------------------------------------
# Graph build + result translation
# ---------------------------------------------------------------------------


def _port_id(node_id: str, port_id: str) -> str:
    return f"{node_id}\x1f{port_id}"  # unit-separator → can't collide with refs


def build_elk_layout(nodes: list[ElkNode], edges: list[ElkEdge]) -> LayoutResult:
    """Place `nodes` and route `edges` with ELK. Raises `LayoutError`.

    The returned anchors + wires are in KiCad placed-schematic mm,
    grid-snapped, ready to emit as `AddSymbol`/`AddWire` ops.
    """
    if not nodes:
        raise LayoutError(reason="empty_graph", detail="no symbols to place")

    children = []
    # Per-node reference corner (top-left of the padded pin cloud); the
    # placement anchor is derived from it after ELK runs.
    ref: dict[str, tuple[float, float]] = {}
    for n in nodes:
        if not n.pin_offsets:
            raise LayoutError(
                reason="node_no_pins",
                detail=f"symbol {n.id!r} has no pins to anchor on",
            )
        xs = [off[0] for off in n.pin_offsets.values()]
        ys = [off[1] for off in n.pin_offsets.values()]
        ref_x = min(xs) - _PAD_MM
        ref_y = min(ys) - _PAD_MM
        ref[n.id] = (ref_x, ref_y)
        width = (max(xs) - min(xs)) + 2 * _PAD_MM
        height = (max(ys) - min(ys)) + 2 * _PAD_MM
        ports = [
            {"id": _port_id(n.id, pid),
             "x": round(off[0] - ref_x, 4),
             "y": round(off[1] - ref_y, 4)}
            for pid, off in n.pin_offsets.items()
        ]
        children.append({
            "id": n.id,
            "width": round(width, 4),
            "height": round(height, 4),
            "layoutOptions": {"portConstraints": "FIXED_POS"},
            "ports": ports,
        })

    node_ids = {n.id for n in nodes}
    elk_edges = []
    for i, e in enumerate(edges):
        sn, sp = e.src
        dn, dp = e.dst
        if sn not in node_ids or dn not in node_ids:
            raise LayoutError(
                reason="edge_unknown_node",
                detail=f"edge {i} references missing node ({sn!r} or {dn!r})",
            )
        elk_edges.append({
            "id": f"e{i}",
            "sources": [_port_id(sn, sp)],
            "targets": [_port_id(dn, dp)],
        })

    graph = {
        "id": "root",
        "layoutOptions": _ELK_OPTS,
        "children": children,
        "edges": elk_edges,
    }
    result = _run_bridge(graph)

    # --- anchors ---------------------------------------------------------
    anchors: dict[str, tuple[float, float]] = {}
    for child in result.get("children", []):
        nid = child.get("id")
        if nid is None or "x" not in child or "y" not in child:
            continue
        ref_x, ref_y = ref[nid]
        # anchor = node_topleft - ref, shifted by the global origin. Snap
        # node_topleft+origin (ref is already a grid multiple), so each pin
        # = snapped_anchor + grid_offset lands on its routed wire endpoint.
        ax = _snap(float(child["x"]) + _ORIGIN_MM) - ref_x
        ay = _snap(float(child["y"]) + _ORIGIN_MM) - ref_y
        anchors[nid] = (ax, ay)
    missing = node_ids - set(anchors)
    if missing:
        raise LayoutError(
            reason="elk_unplaced",
            detail=f"ELK returned no position for: {sorted(missing)}",
        )

    # --- wires -----------------------------------------------------------
    wires: list[tuple[float, float, float, float]] = []
    for edge in result.get("edges", []):
        for sec in edge.get("sections", []):
            pts = [sec["startPoint"]]
            pts.extend(sec.get("bendPoints", []))
            pts.append(sec["endPoint"])
            snapped = [
                (_snap(float(p["x"]) + _ORIGIN_MM), _snap(float(p["y"]) + _ORIGIN_MM))
                for p in pts
            ]
            for (x1, y1), (x2, y2) in zip(snapped, snapped[1:]):
                if (x1, y1) == (x2, y2):
                    continue  # degenerate after snapping
                wires.append((x1, y1, x2, y2))
    return LayoutResult(anchors=anchors, wires=wires)
