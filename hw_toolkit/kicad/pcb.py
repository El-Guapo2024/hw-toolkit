"""PCB backend — synthesize a placed `.kicad_pcb` from a schematic.

This is the first PCB step on top of the schematic generator: given a
finished `.kicad_sch`, it

  1. exports the netlist (kicad-cli sch export netlist),
  2. loads each component's real KiCad footprint (.kicad_mod) from the
     installed footprint libraries,
  3. places the footprints (grid for now — ELK net-aware placement is a
     later pass),
  4. assigns the schematic's nets to the footprint pads, and
  5. draws a straight-line **ratsnest** (one star per net) on a comment
     layer so the airwires actually render — kicad-cli's SVG export only
     plots board layers, and the live ratsnest is not one of them.

The output is a real `.kicad_pcb` that opens in pcbnew (with a true,
recomputed ratsnest) and renders to a placed-board SVG/PNG via
`render_pcb_svg`. Copper routing is NOT done here — that's the
autorouter's job (router-mcp).

Coordinates are millimetres, Y-down (KiCad board frame), same as the
schematic side. Footprints place at rotation 0 in this first pass.

Parts whose footprint couldn't be resolved (e.g. a connector whose
package isn't in the footprint map) are SKIPPED and reported on the
returned result — they are silently dropped from neither the board nor
the report.
"""
from __future__ import annotations

import os
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import sexpdata
from sexpdata import Symbol

from hw_toolkit.exceptions import HwToolkitError
from hw_toolkit.kicad.cli import find_cli

# Footprint library search roots (mirror lib.py's symbol roots).
_FP_DIRS = [
    Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"),
    Path("/usr/share/kicad/footprints"),
    Path.home() / ".local/share/kicad/footprints",
]

_GRID_MM = 1.27


class PcbError(HwToolkitError):
    """PCB synthesis failed (netlist export, footprint load, or render)."""


@dataclass
class PcbResult:
    """Outcome of `write_pcb`. `placed` is the refdes list that made it onto
    the board; `skipped` maps refdes → reason (usually no footprint)."""
    pcb_path: Path
    placed: tuple[str, ...]
    skipped: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Footprint resolution
# ---------------------------------------------------------------------------


def _fp_dirs() -> list[Path]:
    env = os.environ.get("KICAD9_FOOTPRINT_DIR") or os.environ.get(
        "KICAD_FOOTPRINT_DIR"
    )
    dirs = [Path(env)] if env else []
    return dirs + _FP_DIRS


def _find_footprint(fp_id: str) -> Path | None:
    """`Lib:Name` → path of `Lib.pretty/Name.kicad_mod`, or None."""
    if not fp_id or ":" not in fp_id:
        return None
    lib, name = fp_id.split(":", 1)
    for base in _fp_dirs():
        cand = base / f"{lib}.pretty" / f"{name}.kicad_mod"
        if cand.exists():
            return cand
    return None


# ---------------------------------------------------------------------------
# s-expr helpers
# ---------------------------------------------------------------------------


def _tag(node) -> str | None:
    if isinstance(node, list) and node and isinstance(node[0], Symbol):
        return node[0].value()
    return None


def _children(node, tag: str) -> list:
    return [c for c in node[1:] if _tag(c) == tag]


def _first(node, tag: str):
    cs = _children(node, tag)
    return cs[0] if cs else None


# ---------------------------------------------------------------------------
# Netlist
# ---------------------------------------------------------------------------


@dataclass
class _Netlist:
    footprints: dict[str, str]                       # ref -> fp_id (only w/ fp)
    pad_net: dict[tuple[str, str], int]              # (ref, pad) -> net code
    net_name: dict[int, str]                         # code -> name
    net_nodes: dict[int, list[tuple[str, str]]]      # code -> [(ref, pad)]
    all_refs: tuple[str, ...] = ()                   # every component ref


def _export_netlist(sch_path: Path) -> Path:
    cli = find_cli()
    out = sch_path.with_suffix(".net")
    proc = subprocess.run(
        [cli, "sch", "export", "netlist", "--format", "kicadsexpr",
         "-o", str(out), str(sch_path)],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0 or not out.exists():
        raise PcbError(f"netlist export failed: {proc.stderr[:400]}")
    return out


def _sval(node) -> str:
    if not isinstance(node, list) or len(node) < 2:
        return ""
    v = node[1]
    return v.value() if isinstance(v, Symbol) else str(v)


def _parse_netlist(net_path: Path) -> _Netlist:
    d = sexpdata.loads(net_path.read_text())

    footprints: dict[str, str] = {}
    all_refs: list[str] = []
    comps = _first(d, "components")
    if comps is not None:
        for c in _children(comps, "comp"):
            ref = _sval(_first(c, "ref"))
            if not ref:
                continue
            all_refs.append(ref)
            fp = _first(c, "footprint")
            if fp is not None and _sval(fp):
                footprints[ref] = _sval(fp)

    pad_net: dict[tuple[str, str], int] = {}
    net_name: dict[int, str] = {}
    net_nodes: dict[int, list[tuple[str, str]]] = {}
    nets = _first(d, "nets")
    if nets is not None:
        for n in _children(nets, "net"):
            code = int(_sval(_first(n, "code")))
            name = _sval(_first(n, "name"))
            net_name[code] = name
            net_nodes[code] = []
            for nd in _children(n, "node"):
                ref = _sval(_first(nd, "ref"))
                pin = _sval(_first(nd, "pin"))
                pad_net[(ref, pin)] = code
                net_nodes[code].append((ref, pin))
    return _Netlist(footprints, pad_net, net_name, net_nodes, tuple(all_refs))


# ---------------------------------------------------------------------------
# Footprint placement
# ---------------------------------------------------------------------------


def _pad_offsets(fp_node) -> list[tuple[str, tuple[float, float]]]:
    """[(pad_name, (dx, dy))] from a parsed footprint, in footprint frame."""
    out = []
    for pad in _children(fp_node, "pad"):
        name = pad[1].value() if isinstance(pad[1], Symbol) else str(pad[1])
        at = _first(pad, "at")
        if at is None:
            continue
        out.append((name, (float(at[1]), float(at[2]))))
    return out


def _fp_extent(fp_node) -> tuple[float, float]:
    """Rough (w, h) from pad span, for grid sizing."""
    offs = [o for _, o in _pad_offsets(fp_node)]
    if not offs:
        return (5.0, 5.0)
    xs = [o[0] for o in offs]
    ys = [o[1] for o in offs]
    return (max(xs) - min(xs) + 4.0, max(ys) - min(ys) + 4.0)


def _place_grid(
    refs_fp: list[tuple[str, object]], origin: float = 30.0
) -> dict[str, tuple[float, float]]:
    """Lay footprints left-to-right, wrapping rows; spacing adapts to size."""
    pos: dict[str, tuple[float, float]] = {}
    cursor_x = origin
    row_y = origin
    row_h = 0.0
    per_row = 5
    for i, (ref, fp_node) in enumerate(refs_fp):
        w, h = _fp_extent(fp_node)
        if i and i % per_row == 0:
            cursor_x = origin
            row_y += row_h + 8.0
            row_h = 0.0
        pos[ref] = (round(cursor_x, 3), round(row_y, 3))
        cursor_x += w + 8.0
        row_h = max(row_h, h)
    return pos


# ---------------------------------------------------------------------------
# Board assembly
# ---------------------------------------------------------------------------


def _prep_footprint(fp_node, fp_id: str, ref: str, value: str,
                    at: tuple[float, float], nl: _Netlist) -> object:
    """Mutate a parsed `.kicad_mod` node into a board footprint instance:
    set library id + placement + reference, and assign nets to pads."""
    # Rename the footprint to its full Lib:Name id.
    fp_node[1] = fp_id
    # Insert (at X Y) right after the (layer ...) child.
    at_node = [Symbol("at"), at[0], at[1]]
    insert_idx = 2
    for i, c in enumerate(fp_node[1:], start=1):
        if _tag(c) == "layer":
            insert_idx = i + 1
            break
    fp_node.insert(insert_idx, at_node)
    fp_node.insert(insert_idx + 1, [Symbol("uuid"), str(uuid.uuid4())])

    # Reference + Value property text.
    for prop in _children(fp_node, "property"):
        if len(prop) >= 3 and prop[1] == "Reference":
            prop[2] = ref
        elif len(prop) >= 3 and prop[1] == "Value":
            prop[2] = value

    # Assign nets to pads.
    for pad in _children(fp_node, "pad"):
        pname = pad[1].value() if isinstance(pad[1], Symbol) else str(pad[1])
        code = nl.pad_net.get((ref, pname))
        if code is not None:
            pad.append([Symbol("net"), code, nl.net_name.get(code, "")])
    return fp_node


def _ratsnest_lines(
    nl: _Netlist, pad_abs: dict[tuple[str, str], tuple[float, float]]
) -> list[str]:
    """Straight-line airwires (star per net) on Cmts.User so they render.

    Skips power/ground? No — every multi-pad net gets a star from its
    first placed pad to each other placed pad. Pads we couldn't place
    (skipped footprints) are dropped from the star.
    """
    lines: list[str] = []
    for code, nodes in nl.net_nodes.items():
        pts = [pad_abs[k] for k in nodes if k in pad_abs]
        if len(pts) < 2:
            continue
        hub = pts[0]
        for p in pts[1:]:
            lines.append(
                f'\t(gr_line (start {hub[0]:.3f} {hub[1]:.3f}) '
                f'(end {p[0]:.3f} {p[1]:.3f}) '
                f'(stroke (width 0.1) (type solid)) (layer "Cmts.User") '
                f'(uuid "{uuid.uuid4()}"))'
            )
    return lines


def _edge_rect(pad_abs: dict, margin: float = 6.0) -> str:
    if not pad_abs:
        return ""
    xs = [p[0] for p in pad_abs.values()]
    ys = [p[1] for p in pad_abs.values()]
    x0, y0 = min(xs) - margin, min(ys) - margin
    x1, y1 = max(xs) + margin, max(ys) + margin
    return (
        f'\t(gr_rect (start {x0:.3f} {y0:.3f}) (end {x1:.3f} {y1:.3f}) '
        f'(stroke (width 0.15) (type solid)) (fill no) '
        f'(layer "Edge.Cuts") (uuid "{uuid.uuid4()}"))'
    )


# Board header: layers + minimal setup. Versions match KiCad 9/10 board
# format (sampled from the bundled stm32 discovery template).
_BOARD_HEADER = """\
(kicad_pcb
\t(version 20241229)
\t(generator "hw_toolkit.pcb")
\t(generator_version "9.0")
\t(general
\t\t(thickness 1.6)
\t\t(legacy_teardrops no)
\t)
\t(paper "A4")
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(9 "F.Adhes" user "F.Adhesive")
\t\t(11 "B.Adhes" user "B.Adhesive")
\t\t(13 "F.Paste" user)
\t\t(15 "B.Paste" user)
\t\t(5 "F.SilkS" user "F.Silkscreen")
\t\t(7 "B.SilkS" user "B.Silkscreen")
\t\t(1 "F.Mask" user)
\t\t(3 "B.Mask" user)
\t\t(17 "Dwgs.User" user "User.Drawings")
\t\t(19 "Cmts.User" user "User.Comments")
\t\t(21 "Eco1.User" user "User.Eco1")
\t\t(23 "Eco2.User" user "User.Eco2")
\t\t(25 "Edge.Cuts" user)
\t\t(27 "Margin" user)
\t\t(31 "F.CrtYd" user "F.Courtyard")
\t\t(29 "B.CrtYd" user "B.Courtyard")
\t\t(35 "F.Fab" user)
\t\t(33 "B.Fab" user)
\t)
\t(setup
\t\t(pad_to_mask_clearance 0)
\t\t(allow_soldermask_bridges_in_footprints no)
\t)
"""


def write_pcb(sch_path: str | Path, pcb_path: str | Path | None = None) -> PcbResult:
    """Synthesize a placed `.kicad_pcb` (with ratsnest) from `sch_path`.

    Returns a `PcbResult` listing what placed and what was skipped (no
    footprint). Raises `PcbError` on netlist-export failure.
    """
    sch_path = Path(sch_path)
    pcb_path = Path(pcb_path) if pcb_path else sch_path.with_suffix(".kicad_pcb")

    nl = _parse_netlist(_export_netlist(sch_path))

    # Load + keep only components with a resolvable footprint.
    loaded: list[tuple[str, str, object]] = []  # (ref, fp_id, node)
    skipped: dict[str, str] = {}
    # Components carrying no footprint at all (e.g. connectors whose package
    # isn't mapped) never reach the loop below — report them up front.
    for ref in nl.all_refs:
        if ref not in nl.footprints:
            skipped[ref] = "no footprint assigned"
    for ref, fp_id in nl.footprints.items():
        path = _find_footprint(fp_id)
        if path is None:
            skipped[ref] = f"footprint not found: {fp_id}"
            continue
        try:
            node = sexpdata.loads(path.read_text())
        except Exception as e:
            skipped[ref] = f"footprint parse failed: {e}"
            continue
        loaded.append((ref, fp_id, node))

    if not loaded:
        raise PcbError(
            f"no placeable footprints (skipped {len(skipped)}): "
            f"{', '.join(sorted(skipped))}"
        )

    pos = _place_grid([(ref, node) for ref, _, node in loaded])

    # Absolute pad positions (footprint at rotation 0).
    pad_abs: dict[tuple[str, str], tuple[float, float]] = {}
    for ref, _fp_id, node in loaded:
        fx, fy = pos[ref]
        for pname, (dx, dy) in _pad_offsets(node):
            pad_abs[(ref, pname)] = (fx + dx, fy + dy)

    # Prepare each footprint node (placement + nets).
    fp_blocks: list[str] = []
    for ref, fp_id, node in loaded:
        _prep_footprint(node, fp_id, ref, nl.footprints.get(ref, ""),
                        pos[ref], nl)
        fp_blocks.append("\t" + sexpdata.dumps(node))

    # Net declarations: net 0 is the mandatory unconnected net.
    net_decls = ['\t(net 0 "")']
    for code in sorted(c for c in nl.net_name if c != 0):
        net_decls.append(f'\t(net {code} "{nl.net_name[code]}")')

    body = (
        [_BOARD_HEADER]
        + net_decls
        + fp_blocks
        + _ratsnest_lines(nl, pad_abs)
        + [_edge_rect(pad_abs)]
        + [")"]
    )
    pcb_path.write_text("\n".join(b for b in body if b), encoding="utf-8")
    return PcbResult(
        pcb_path=pcb_path, placed=tuple(r for r, _, _ in loaded), skipped=skipped
    )


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

_RENDER_LAYERS = "F.Cu,F.SilkS,F.Fab,F.CrtYd,Edge.Cuts,Cmts.User"


def render_pcb_svg(pcb_path: str | Path, out_path: str | Path | None = None) -> Path:
    """Render the placed board (copper + silk + courtyard + ratsnest) to SVG."""
    cli = find_cli()
    pcb_path = Path(pcb_path)
    out_path = Path(out_path) if out_path else pcb_path.with_suffix(".pcb.svg")
    proc = subprocess.run(
        [cli, "pcb", "export", "svg", "--page-size-mode", "2",
         "--layers", _RENDER_LAYERS, "--exclude-drawing-sheet",
         "-o", str(out_path), str(pcb_path)],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0 or not out_path.exists():
        raise PcbError(f"pcb svg export failed: {proc.stderr[:400]}")
    return out_path
