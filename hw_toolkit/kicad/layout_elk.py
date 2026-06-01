"""ELK-based schematic placement.

Feeds the bundle's parts (sized by symbol bbox) and nets (as edges) to the
Eclipse Layout Kernel via a small Node bridge (tools/elk/bridge.mjs), and
returns net-optimal node positions — connected parts are pulled together,
which the in-house heuristics couldn't achieve. Routing is left to the
planner for now (point-to-point); ELK placement alone shortens wires a lot.

Everything here is best-effort: if Node or elkjs is missing, or the layout
call fails, `elk_positions` returns None and the caller falls back to the
heuristic clusterer. ELK is never a hard dependency.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from hw_toolkit.core import ResearchBundle

_BRIDGE = Path(__file__).resolve().parents[2] / "tools" / "elk" / "bridge.mjs"

# ELK works in abstract units; we feed millimetres directly. Generous node
# spacing keeps wires from overlapping symbol bodies.
_ELK_OPTS = {
    "elk.algorithm": "layered",
    "elk.direction": "RIGHT",
    "elk.spacing.nodeNode": "20",
    "elk.layered.spacing.nodeNodeBetweenLayers": "35",
    "elk.spacing.edgeNode": "12",
}
_ORIGIN_MM = 30.0  # top-left margin for the whole drawing


def _elk_available() -> bool:
    return shutil.which("node") is not None and _BRIDGE.exists()


def _build_graph(bundle: ResearchBundle, sizes: dict[str, tuple[float, float]]) -> dict:
    ids = {s.id for s in bundle.subsystems}
    children = [
        {"id": s.id, "width": round(sizes[s.id][0], 2),
         "height": round(sizes[s.id][1], 2)}
        for s in bundle.subsystems
    ]
    edges = []
    for i, itf in enumerate(bundle.interfaces):
        a, b = itf.from_subsystem, itf.to_subsystem
        if a in ids and b in ids and a != b:
            edges.append({"id": f"e{i}", "sources": [a], "targets": [b]})
    return {
        "id": "root",
        "layoutOptions": _ELK_OPTS,
        "children": children,
        "edges": edges,
    }


def _run(graph: dict) -> dict:
    proc = subprocess.run(
        ["node", str(_BRIDGE)],
        input=json.dumps(graph),
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ELK bridge failed: {proc.stderr[:400]}")
    return json.loads(proc.stdout)


def elk_positions(
    bundle: ResearchBundle,
    sizes: dict[str, tuple[float, float]],
) -> dict[str, tuple[float, float]] | None:
    """Return `{subsystem_id: (x_mm, y_mm)}` from ELK, or None if ELK is
    unavailable / the layout call fails (caller falls back)."""
    if not _elk_available():
        return None
    try:
        result = _run(_build_graph(bundle, sizes))
    except Exception:
        return None
    out: dict[str, tuple[float, float]] = {}
    for node in result.get("children", []):
        nid = node.get("id")
        if nid is None or "x" not in node or "y" not in node:
            continue
        out[nid] = (_ORIGIN_MM + float(node["x"]), _ORIGIN_MM + float(node["y"]))
    # Require every subsystem placed, else fall back wholesale.
    if len(out) != len(bundle.subsystems):
        return None
    return out
