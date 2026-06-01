"""ELK is the ONLY layout path — mandatory placement + orthogonal routing.

These tests pin the contract introduced when ELK replaced the heuristic
clustered-grid placement and point-to-point diagonal wiring:

  * `build_elk_layout` raises `LayoutError` when Node/the bridge is
    unavailable (no silent None / heuristic fallback), and
  * every wire the planner emits is axis-aligned (orthogonal) and its
    endpoints land on the 1.27 mm grid.
"""
from __future__ import annotations

import math
import re

import pytest

import hw_toolkit as hw
from hw_toolkit.exceptions import LayoutError
from hw_toolkit.kicad import layout_elk
from hw_toolkit.kicad.layout_elk import ElkEdge, ElkNode, build_elk_layout
from hw_toolkit.kicad.planner import AddWire, plan_schematic

_GRID = 1.27


# --------------------------------------------------------- mandatory ELK
def test_build_elk_layout_raises_when_node_missing(monkeypatch) -> None:
    monkeypatch.setattr(layout_elk.shutil, "which", lambda _: None)
    with pytest.raises(LayoutError) as ei:
        build_elk_layout(
            [ElkNode(id="a", pin_offsets={"1": (0.0, 0.0)})], []
        )
    assert ei.value.reason == "node_missing"


def test_build_elk_layout_raises_on_empty_graph() -> None:
    with pytest.raises(LayoutError) as ei:
        build_elk_layout([], [])
    assert ei.value.reason == "empty_graph"


def test_build_elk_layout_raises_on_node_without_pins() -> None:
    with pytest.raises(LayoutError) as ei:
        build_elk_layout([ElkNode(id="a", pin_offsets={})], [])
    assert ei.value.reason == "node_no_pins"


# --------------------------------------------------------- routing output
def test_routed_wires_are_orthogonal_and_on_grid() -> None:
    nodes = [
        ElkNode(id="u1", pin_offsets={"1": (0.0, 7.62), "2": (10.16, 0.0)}),
        ElkNode(id="u2", pin_offsets={"1": (-10.16, 0.0), "2": (10.16, 0.0)}),
        ElkNode(id="g", pin_offsets={"1": (0.0, 0.0)}),
    ]
    edges = [
        ElkEdge(src=("u1", "2"), dst=("u2", "1")),
        ElkEdge(src=("g", "1"), dst=("u1", "1")),
    ]
    out = build_elk_layout(nodes, edges)
    assert set(out.anchors) == {"u1", "u2", "g"}
    assert out.wires, "ELK produced no wire segments"
    for x1, y1, x2, y2 in out.wires:
        # axis-aligned: shares one coordinate
        assert math.isclose(x1, x2) or math.isclose(y1, y2), \
            f"diagonal segment ({x1},{y1})->({x2},{y2})"
        for v in (x1, y1, x2, y2):
            assert math.isclose(v / _GRID, round(v / _GRID), abs_tol=1e-6), \
                f"off-grid coordinate {v}"


def test_pins_land_on_routed_wire_endpoints() -> None:
    """Anchor placement + grid-snapping must put each pin exactly on the
    wire endpoint ELK routed to it (else KiCad reads `wire_dangling`)."""
    nodes = [
        ElkNode(id="u1", pin_offsets={"o": (10.16, 0.0)}),
        ElkNode(id="u2", pin_offsets={"i": (-10.16, 0.0)}),
    ]
    out = build_elk_layout(nodes, [ElkEdge(src=("u1", "o"), dst=("u2", "i"))])
    pin_u1 = (out.anchors["u1"][0] + 10.16, out.anchors["u1"][1] + 0.0)
    pin_u2 = (out.anchors["u2"][0] - 10.16, out.anchors["u2"][1] + 0.0)
    endpoints = {(round(x1, 4), round(y1, 4)) for x1, y1, _, _ in out.wires}
    endpoints |= {(round(x2, 4), round(y2, 4)) for _, _, x2, y2 in out.wires}
    assert (round(pin_u1[0], 4), round(pin_u1[1], 4)) in endpoints
    assert (round(pin_u2[0], 4), round(pin_u2[1], 4)) in endpoints


# --------------------------------------------------------- planner emits orthogonal
def test_planner_emits_only_orthogonal_absolute_wires() -> None:
    b = hw.Board("elk_orth")
    b.module(id="u1", category="custom_ic", mpn="WIDGET-A", package="SOT-23-6")
    b.module(id="u2", category="mcu", mpn="WIDGET-B", package="LQFP-32")
    n = b.signal("link", protocol="gpio")
    n += "u1.OUT", "u2.PA0"
    g = b.gnd()
    g += "u1.GND", "u2.GND"
    plan = plan_schematic(b.bundle, "/tmp/elk_orth.kicad_sch")

    wires = [o for o in plan.ops if isinstance(o, AddWire)]
    assert wires, "no wires emitted"
    coord = re.compile(r"^@(-?[\d.]+),(-?[\d.]+)$")
    for w in wires:
        ms, md = coord.match(w.src), coord.match(w.dst)
        assert ms and md, f"wire endpoint not absolute: {w.src} -> {w.dst}"
        x1, y1 = float(ms.group(1)), float(ms.group(2))
        x2, y2 = float(md.group(1)), float(md.group(2))
        assert math.isclose(x1, x2) or math.isclose(y1, y2), \
            f"diagonal wire {w.src} -> {w.dst}"
