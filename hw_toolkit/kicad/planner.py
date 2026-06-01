"""Phase 2 — flat-schematic generation from a ResearchBundle.

This module does NOT invoke MCP tools directly (no fastmcp client in the
agent runtime). Instead it produces:

  1. A blank `.kicad_sch` stub on disk (so designer-mcp's add_* tools
     have a file to mutate).
  2. A typed `SchematicPlan` — an ordered list of MCP operations the
     pcb-designer agent then executes one-by-one via its tool whitelist.

That split lets the planner be unit-tested without an MCP server, and
the agent loop be observed (one tool call per op = one feedback point).

Wire format (designer-mcp's add_wire spec):
    "U1.VCC"    — pin on a placed symbol
    "VCC1"      — bare net anchor (power/ground)
    "@x,y"      — explicit mm coord

For the MVP we only emit pin↔pin wires and pin↔power-anchor wires.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

from hw_toolkit.kicad.projector import refdes_map_for_bundle
from hw_toolkit.core import Interface, ResearchBundle, SubsystemPick

# ---------------------------------------------------------------------------
# Layout policy (flat MVP)
# ---------------------------------------------------------------------------

# Subsystems are placed on a wrapped grid (not one long lane — that ran
# parts off the page and fanned wires across the whole canvas). Cell size
# adapts to the largest placed symbol so real KiCad symbols (which carry
# their own geometry) don't overlap or sit absurdly far apart.
_GRID_X_START_MM = 40.0
_GRID_Y_START_MM = 50.0
_IC_BODY_W_MM = 30.0    # fallback extent for synthesized (no-bbox) symbols
_IC_BODY_H_MM = 30.0
_POWER_DROP_MM = 25.4   # power symbol sits N mm above its IC
_GROUND_DROP_MM = 20.0  # ground symbol sits N mm below

# Cluster packing: members of one group are laid out in a mini-grid;
# groups (blocks) are packed left-to-right and wrap past _PAGE_W_MM.
_CLUSTER_COLS = 3       # members per row within a cluster
_CLUSTER_GAP_MM = 12.0  # spacing between members inside a cluster
_BLOCK_GAP_MM = 28.0    # spacing between clusters
_PAGE_W_MM = 250.0      # wrap blocks once a row exceeds this width

# ---------------------------------------------------------------------------
# Plan operations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AddCustomIC:
    """Drop one SubsystemPick onto the schematic as a custom IC.

    `pins` is the side+name list designer-mcp's add_custom_ic expects.
    """
    kicad_sch: str
    ref: str
    name: str
    at_x: float
    at_y: float
    pins: tuple[dict[str, object], ...]
    width: float = _IC_BODY_W_MM
    height: float = _IC_BODY_H_MM
    footprint: str | None = None
    rotation: float = 0
    tool: Literal["mcp__designer-mcp__add_custom_ic"] = (
        "mcp__designer-mcp__add_custom_ic"
    )


@dataclass(frozen=True)
class AddSymbol:
    """Place a real KiCad-library symbol by `lib_id` (no pin synthesis).

    Used when the SubsystemPick resolved to an actual library part — pins
    come from the library symbol itself, so wires reference `<ref>.<pin
    name>` and are resolved against the placed symbol at write time.
    """
    kicad_sch: str
    ref: str
    lib_id: str
    value: str
    at_x: float
    at_y: float
    footprint: str | None = None
    rotation: float = 0
    tool: Literal["mcp__designer-mcp__add_ic"] = "mcp__designer-mcp__add_ic"


@dataclass(frozen=True)
class AddPower:
    kicad_sch: str
    ref: str
    label: str
    at_x: float
    at_y: float
    tool: Literal["mcp__designer-mcp__add_power"] = "mcp__designer-mcp__add_power"


@dataclass(frozen=True)
class AddGround:
    kicad_sch: str
    ref: str
    at_x: float
    at_y: float
    tool: Literal["mcp__designer-mcp__add_ground"] = "mcp__designer-mcp__add_ground"


@dataclass(frozen=True)
class AddWire:
    kicad_sch: str
    src: str
    dst: str
    tool: Literal["mcp__designer-mcp__add_wire"] = "mcp__designer-mcp__add_wire"


SchematicOp = AddCustomIC | AddSymbol | AddPower | AddGround | AddWire


@dataclass(frozen=True)
class SchematicPlan:
    """Ordered list of MCP operations the agent executes to build the sch."""
    kicad_sch: Path
    ops: tuple[SchematicOp, ...]

    def as_tool_calls(self) -> list[dict[str, object]]:
        """Render the plan as a list of `{tool, args}` dicts for the agent
        loop. Each entry is one MCP call."""
        out: list[dict[str, object]] = []
        for op in self.ops:
            d = {k: v for k, v in op.__dict__.items() if k != "tool"}
            out.append({"tool": op.tool, "args": d})
        return out


# ---------------------------------------------------------------------------
# Stub .kicad_sch generation
# ---------------------------------------------------------------------------


_BLANK_SCH_TEMPLATE = """\
(kicad_sch
\t(version 20240210)
\t(generator "hw_agent.pcb_designer")
\t(generator_version "1.0")
\t(uuid "{uuid}")
\t(paper "A4")
\t(lib_symbols)
\t(sheet_instances
\t\t(path "/"
\t\t\t(page "1")
\t\t)
\t)
)
"""


def write_blank_schematic(sch_path: str | Path, *, overwrite: bool = False) -> Path:
    """Write a minimal-but-valid empty .kicad_sch.

    designer-mcp's atomic add_* tools mutate this file; they don't create
    it. Refuses to overwrite an existing file unless `overwrite=True` —
    the pcb-designer never silently clobbers user-edited schematics.
    """
    p = Path(sch_path)
    if p.exists() and not overwrite:
        raise FileExistsError(
            f"{p} already exists; pass overwrite=True to wipe it. "
            f"(pcb-designer refuses to silently clobber a schematic.)"
        )
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_BLANK_SCH_TEMPLATE.format(uuid=uuid.uuid4()), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Pin synthesis
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SynthPin:
    name: str
    side: Literal["left", "right", "top", "bottom"]
    at: tuple[float, float]  # absolute mm relative to schematic origin


def _synthesize_pins(
    pick: SubsystemPick,
    body_center: tuple[float, float],
    ports: Iterable[str] | None = None,
) -> list[dict[str, object]]:
    """Pick a side per port name and emit add_custom_ic pin dicts.

    `ports` is the explicit list of port names to render. If `None`, the
    function falls back to `pick.port_bindings.keys()` for back-compat with
    the old researcher-driven flow. The new notebook flow passes the port
    set derived from the bundle's interfaces.

    Heuristic:
      power-in   (VIN, VBAT)            → left
      power-out  (VOUT, VDD, VCC, 3V3)  → right (for buck/LDO providers)
                                          left  (for consumers)
      ground     (GND, VSS)             → bottom
      I²C / SPI  (SDA, SCL, MISO, MOSI) → right
      default                           → right

    The body is `_IC_BODY_W_MM × _IC_BODY_H_MM` mm; pin coords are placed
    on the body perimeter, spaced evenly per side.
    """
    cx, cy = body_center
    half_w = _IC_BODY_W_MM / 2
    half_h = _IC_BODY_H_MM / 2

    if ports is None:
        ports = list(pick.port_bindings.keys())
    # de-dup while preserving first-seen order
    seen: set[str] = set()
    unique_ports: list[str] = []
    for p in ports:
        if p not in seen:
            seen.add(p)
            unique_ports.append(p)

    buckets: dict[str, list[str]] = {
        "left": [], "right": [], "top": [], "bottom": [],
    }
    for port in unique_ports:
        side = _classify_port(pick, port)
        buckets[side].append(port)

    pins: list[dict[str, object]] = []
    for side, names in buckets.items():
        if not names:
            continue
        if side in ("left", "right"):
            x = cx - half_w if side == "left" else cx + half_w
            spacing = _IC_BODY_H_MM / (len(names) + 1)
            for i, name in enumerate(names, start=1):
                y = cy - half_h + spacing * i
                pins.append({
                    "name": name,
                    "at": [_snap_grid(x), _snap_grid(y)],
                    "side": side,
                    "electrical_type": _electrical_type(pick, name),
                })
        else:  # top / bottom
            y = cy - half_h if side == "top" else cy + half_h
            spacing = _IC_BODY_W_MM / (len(names) + 1)
            for i, name in enumerate(names, start=1):
                x = cx - half_w + spacing * i
                pins.append({
                    "name": name,
                    "at": [_snap_grid(x), _snap_grid(y)],
                    "side": side,
                    "electrical_type": _electrical_type(pick, name),
                })
    return pins


_PROVIDER_OUTPUT_PORTS = {"VOUT", "VOUT1", "VOUT2"}


def _electrical_type(pick: SubsystemPick, port: str) -> str:
    """Map a port name to a KiCad pin electrical_type.

    Only the canonical output port name(s) on a power provider IC count
    as `power_out` — VDD/VCC on a buck is a bias-input pin, NOT an
    output, even though the category is `buck_converter`. Previously
    everything in `_POWER_OUT_NAMES` was promoted to `power_out` on
    provider ICs, which mis-tagged buck.VDD as a power source.

    KiCad's ERC uses this to validate `power_pin_not_driven`.
    """
    p = port.upper()
    if p in _GROUND_NAMES or p in _POWER_IN_NAMES:
        return "power_in"
    if p in _PROVIDER_OUTPUT_PORTS and _is_power_provider(pick):
        return "power_out"
    if p in _POWER_OUT_NAMES:
        return "power_in"  # VDD/VCC/3V3/5V/etc — always a bias/rail input
    if p in _RIGHT_SIG_NAMES:
        return "bidirectional"
    return "passive"


# KiCad schematic grid is 1.27 mm (50 mil). Wires + pins must land on it
# or ERC raises `endpoint_off_grid` warnings and `wire_dangling` errors.
_GRID_MM = 1.27


def _snap_grid(v: float) -> float:
    return round(v / _GRID_MM) * _GRID_MM


_GROUND_NAMES = {"GND", "VSS", "GROUND", "AGND", "DGND", "PGND"}
_POWER_IN_NAMES = {"VIN", "VBAT", "VBUS", "VPP"}
_POWER_OUT_NAMES = {"VOUT", "VDD", "VCC", "3V3", "5V", "12V", "VDDA"}
_RIGHT_SIG_NAMES = {"SDA", "SCL", "MISO", "MOSI", "SCK", "TX", "RX", "INT", "CS", "RST", "EN"}


def _classify_port(pick: SubsystemPick, port: str) -> Literal["left", "right", "top", "bottom"]:
    p = port.upper()
    if p in _GROUND_NAMES:
        return "bottom"
    if p in _POWER_IN_NAMES:
        return "left"
    # power providers (buck/LDO) emit on right; consumers receive on left.
    if p in _POWER_OUT_NAMES:
        return "right" if _is_power_provider(pick) else "left"
    if p in _RIGHT_SIG_NAMES:
        return "right"
    return "right"


def _is_power_provider(pick: SubsystemPick) -> bool:
    return pick.category in {"buck_converter", "ldo", "power"}


# Map common package strings → fully qualified KiCad `Library:Footprint`.
# Anything not in this table renders with an empty `Footprint` property so
# kicad-cli's ERC doesn't emit `footprint_link_issues` for unknown libs.
# Engineer can override during phase-2 hand-tune.
_PACKAGE_TO_KICAD_FP: dict[str, str] = {
    "SOIC-8":      "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
    "SOIC-14":     "Package_SO:SOIC-14_3.9x8.7mm_P1.27mm",
    "SOIC-16":     "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm",
    "TSSOP-8":     "Package_SO:TSSOP-8_3x3mm_P0.65mm",
    "TSSOP-14":    "Package_SO:TSSOP-14_4.4x5mm_P0.65mm",
    "TSSOP-16":    "Package_SO:TSSOP-16_4.4x5mm_P0.65mm",
    "SOT-23":      "Package_TO_SOT_SMD:SOT-23",
    "SOT-23-5":    "Package_TO_SOT_SMD:SOT-23-5",
    "SOT-23-6":    "Package_TO_SOT_SMD:SOT-23-6",
    "SOT-223":     "Package_TO_SOT_SMD:SOT-223",
    "LGA-14":      "Package_LGA:LGA-14_3x2.5mm_P0.5mm_LayoutBorder3x4y",
    "DFN-8":       "Package_DFN_QFN:DFN-8-1EP_3x3mm_P0.65mm_EP1.5x2.4mm",
    "QFN-16":      "Package_DFN_QFN:QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm",
    "QFN-32":      "Package_DFN_QFN:QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm",
}


def _kicad_footprint_for(package: str) -> str:
    """Translate a SubsystemPick.package into a fully qualified KiCad
    `Library:Footprint` string, or empty if unknown. Empty avoids
    kicad-cli's `footprint_link_issues` warning for unresolved libs."""
    return _PACKAGE_TO_KICAD_FP.get(package, "")


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


def _symbol_extent(sub: SubsystemPick) -> tuple[float, float]:
    """(width, height) in mm for grid sizing. Real symbols use their
    library bbox; synthesized parts use the fixed body size."""
    if sub.lib_id:
        try:
            from hw_toolkit.kicad.lib import load_symbol

            x0, y0, x1, y1 = load_symbol(sub.lib_id).bbox()
            return (abs(x1 - x0), abs(y1 - y0))
        except Exception:
            pass
    return (_IC_BODY_W_MM, _IC_BODY_H_MM)


def _place_subsystems(
    subs: "list[SubsystemPick]",
) -> dict[str, tuple[float, float]]:
    """Cluster-aware placement → `{subsystem_id: (x, y)}`.

    Parts sharing a `group` are packed into a tight mini-grid block (so a
    factory block's internal wires stay short); ungrouped parts are their
    own block. Blocks are laid left-to-right and wrap past `_PAGE_W_MM`.
    """
    # Group, preserving first-seen order.
    groups: dict[str, list[SubsystemPick]] = {}
    for s in subs:
        groups.setdefault(s.group or s.id, []).append(s)

    sizes = {s.id: _symbol_extent(s) for s in subs}
    positions: dict[str, tuple[float, float]] = {}

    cursor_x = _GRID_X_START_MM
    row_y = _GRID_Y_START_MM
    row_max_h = 0.0
    for members in groups.values():
        # Mini-grid dimensions for this block.
        cols = max(1, min(len(members), _CLUSTER_COLS))
        cell_w = max(sizes[m.id][0] for m in members) + _CLUSTER_GAP_MM
        cell_h = (max(sizes[m.id][1] for m in members)
                  + _GROUND_DROP_MM + _CLUSTER_GAP_MM)
        n_rows = (len(members) + cols - 1) // cols
        block_w = cols * cell_w
        block_h = n_rows * cell_h

        # Wrap to a new block-row if this block overflows the page width.
        if cursor_x > _GRID_X_START_MM and cursor_x + block_w > _PAGE_W_MM:
            cursor_x = _GRID_X_START_MM
            row_y += row_max_h + _BLOCK_GAP_MM
            row_max_h = 0.0

        for i, m in enumerate(members):
            mx = cursor_x + (i % cols) * cell_w
            my = row_y + (i // cols) * cell_h
            positions[m.id] = (_snap_grid(mx), _snap_grid(my))

        cursor_x += block_w + _BLOCK_GAP_MM
        row_max_h = max(row_max_h, block_h)

    return positions


def plan_schematic(bundle: ResearchBundle, sch_path: str | Path) -> SchematicPlan:
    """Build the full sequence of MCP ops needed to render `bundle` as a
    flat schematic. Caller has already (or will) `write_blank_schematic`.
    """
    sch = str(sch_path)
    ops: list[SchematicOp] = []
    refmap = refdes_map_for_bundle(bundle)
    pwr_counter = 0

    # Derive each subsystem's port set from the bundle's interfaces.
    # The notebook flow never sets `subsystem.port_bindings` directly —
    # nets/connects produce interfaces, and ports fall out of those.
    ports_by_sub: dict[str, list[str]] = {s.id: [] for s in bundle.subsystems}
    for iface in bundle.interfaces:
        if iface.from_subsystem in ports_by_sub:
            ports_by_sub[iface.from_subsystem].append(iface.from_port)
        if iface.to_subsystem in ports_by_sub:
            ports_by_sub[iface.to_subsystem].append(iface.to_port)

    # 1. Place subsystems. Parts sharing a `group` (e.g. a buck IC + its
    #    passives) are packed into one tight cluster; clusters are then
    #    arranged on a wrapped grid. Keeps a block's internal wires short.
    positions = _place_subsystems(bundle.subsystems)
    for sub in bundle.subsystems:
        cx, cy = positions[sub.id]
        ref = refmap[sub.id]
        if sub.lib_id:
            # Real KiCad library part — place the actual symbol. Pins come
            # from the library, so we synthesize none; wires resolve against
            # the placed symbol at write time.
            ops.append(
                AddSymbol(
                    kicad_sch=sch,
                    ref=ref,
                    lib_id=sub.lib_id,
                    value=sub.mpn,
                    at_x=cx,
                    at_y=cy,
                    footprint=sub.footprint or _kicad_footprint_for(sub.package) or None,
                )
            )
            continue
        # Merge auto-derived ports w/ any legacy port_bindings on the pick.
        ports = list(sub.port_bindings.keys()) + ports_by_sub.get(sub.id, [])
        pins = _synthesize_pins(sub, (cx, cy), ports=ports)
        normalized_fp = sub.footprint or _kicad_footprint_for(sub.package)
        ops.append(
            AddCustomIC(
                kicad_sch=sch,
                ref=ref,
                name=sub.mpn,
                at_x=cx,
                at_y=cy,
                pins=tuple(pins),
                footprint=normalized_fp or None,
            )
        )

    # Build the (refdes, port) → (x, y) lookup once after step 1 so the
    # GND-symbol-to-IC-pin wire (step 2) + PWR_FLAG-to-hub-pin wire
    # (step 4) can resolve coords without re-computing the layout math.
    pin_xy_early: dict[tuple[str, str], tuple[float, float]] = {}
    for op in ops:
        if isinstance(op, AddCustomIC):
            for p in op.pins:
                pin_xy_early[(op.ref, p["name"])] = (
                    float(p["at"][0]), float(p["at"][1]),
                )

    # 2. Drop a GND symbol per subsystem ONLY if that subsystem has a GND
    #    port. The symbol is wired to the IC's GND pin so it actually
    #    sits on the GND net (otherwise ERC sees it as a floating
    #    `power_in` pin and emits `pin_not_connected`).
    for sub in bundle.subsystems:
        ports = ports_by_sub.get(sub.id, [])
        gnd_port = next((p for p in ports if p.upper() in _GROUND_NAMES), None)
        if gnd_port is None:
            continue
        cx, cy = positions[sub.id]
        gnd_y = _snap_grid(cy + _GROUND_DROP_MM)
        pwr_counter += 1
        ops.append(
            AddGround(
                kicad_sch=sch,
                ref=f"#PWR{pwr_counter:03d}",
                at_x=cx,
                at_y=gnd_y,
            )
        )
        # Wire the GND symbol's anchor to the IC's GND pin.
        ic_ref = refmap[sub.id]
        ic_gnd_xy = pin_xy_early.get((ic_ref, gnd_port))
        if ic_gnd_xy is not None:
            ops.append(
                AddWire(
                    kicad_sch=sch,
                    src=f"@{cx},{gnd_y}",
                    dst=f"@{ic_gnd_xy[0]},{ic_gnd_xy[1]}",
                )
            )

    # 3. Wires per interface — pin to pin. Group star-expanded
    #    interfaces by their shared source so colinear consumers (same Y
    #    or same X as the hub) collapse to one straight wire instead of
    #    two overlapping wires that KiCad splinters into `wire_dangling`
    #    sub-pixel fragments.
    wires_by_src: dict[str, list[str]] = {}
    for iface in bundle.interfaces:
        for src, dst in _interface_wire_endpoints(iface, bundle, refmap):
            wires_by_src.setdefault(src, []).append(dst)

    def _resolve(endpoint: str) -> tuple[float, float] | None:
        if "." not in endpoint:
            return None
        ref, port = endpoint.split(".", 1)
        return pin_xy_early.get((ref, port))

    for src, dsts in wires_by_src.items():
        src_xy = _resolve(src)
        if src_xy is not None and len(dsts) > 1:
            # Chain colinear consumers (hub → c1 → c2 → c3) so each pin
            # sits at a wire endpoint. KiCad treats wire-to-pin endpoint
            # contacts as connected without needing junction markers;
            # mid-segment pins would otherwise read as "not driven".
            dst_xys = [_resolve(d) for d in dsts]
            if all(xy is not None for xy in dst_xys):
                ys = {round(xy[1], 4) for xy in dst_xys}  # type: ignore[index]
                xs = {round(xy[0], 4) for xy in dst_xys}  # type: ignore[index]
                if len(ys) == 1 and round(src_xy[1], 4) in ys:
                    # All horizontal; chain by ascending |x - src_x|.
                    ordered = sorted(
                        zip(dsts, dst_xys),
                        key=lambda pair: abs(pair[1][0] - src_xy[0]),  # type: ignore[index]
                    )
                    prev = src
                    for dst_name, xy in ordered:
                        ops.append(AddWire(kicad_sch=sch, src=prev, dst=dst_name))
                        prev = dst_name
                    continue
                if len(xs) == 1 and round(src_xy[0], 4) in xs:
                    ordered = sorted(
                        zip(dsts, dst_xys),
                        key=lambda pair: abs(pair[1][1] - src_xy[1]),  # type: ignore[index]
                    )
                    prev = src
                    for dst_name, xy in ordered:
                        ops.append(AddWire(kicad_sch=sch, src=prev, dst=dst_name))
                        prev = dst_name
                    continue
        for dst in dsts:
            ops.append(AddWire(kicad_sch=sch, src=src, dst=dst))

    # 4. PWR_FLAG per declared power net so KiCad ERC sees each rail as
    #    driven (no more `power_pin_not_driven`). Place each flag offset
    #    from the hub pin (first member of the net = star-expansion
    #    source) and wire it back to that pin. GND rails carry voltage_v=0;
    #    high-voltage rails (3V3/5V/etc.) get one PWR_FLAG each.
    pin_xy: dict[tuple[str, str], tuple[float, float]] = {}
    for op in ops:
        if isinstance(op, AddCustomIC):
            for p in op.pins:
                pin_xy[(op.ref, p["name"])] = (
                    float(p["at"][0]), float(p["at"][1]),
                )

    power_hubs: dict[tuple[str, str], Interface] = {}
    for iface in bundle.interfaces:
        if iface.type != "power":
            continue
        if iface.from_subsystem == "external":
            continue
        power_hubs.setdefault(
            (iface.from_subsystem, iface.from_port), iface,
        )

    flag_offset_idx = 0
    sub_by_id = {s.id: s for s in bundle.subsystems}
    for (hub_sub, hub_port), iface in power_hubs.items():
        hub_ref = refmap.get(hub_sub)
        if hub_ref is None:
            continue
        hub_xy = pin_xy.get((hub_ref, hub_port))
        if hub_xy is None:
            continue
        # Skip PWR_FLAG on rails that already have a `power_out` driver —
        # KiCad ERC then complains about two power_out pins connected.
        hub_pick = sub_by_id.get(hub_sub)
        if hub_pick and _electrical_type(hub_pick, hub_port) == "power_out":
            continue
        # Place the PWR_FLAG on the SAME row as the hub pin (axis-aligned
        # wire — KiCad gets unhappy with diagonal wires and breaks them
        # into sub-pixel segments that trigger `wire_dangling`).
        flag_x = _snap_grid(hub_xy[0] + 10.16)  # 8 grid steps right
        flag_y = hub_xy[1]                       # same Y → horizontal wire
        pwr_counter += 1
        ops.append(
            AddPower(
                kicad_sch=sch,
                ref=f"#FLG{pwr_counter:03d}",
                label="PWR_FLAG",
                at_x=flag_x,
                at_y=flag_y,
            )
        )
        ops.append(
            AddWire(
                kicad_sch=sch,
                src=f"@{flag_x},{flag_y}",
                dst=f"@{hub_xy[0]},{hub_xy[1]}",
            )
        )
        flag_offset_idx += 1

    return SchematicPlan(kicad_sch=Path(sch_path), ops=tuple(ops))


def _label_for_power_interface(iface: Interface) -> str:
    """Pick a KiCad power-label string from the rail's nominal voltage."""
    import math
    v = iface.voltage_nominal_v
    if v is None:
        return "VCC"
    if math.isclose(v, 0.0, abs_tol=1e-6):
        return "GND"
    canon = {3.3: "3V3", 5.0: "5V", 12.0: "12V", 24.0: "24V"}
    for canon_v, label in canon.items():
        if math.isclose(v, canon_v, rel_tol=1e-3, abs_tol=1e-3):
            return label
    whole = int(v)
    frac = round((v - whole) * 10)
    return f"{whole}V{frac}" if frac else f"{whole}V"


def _interface_wire_endpoints(
    iface: Interface,
    bundle: ResearchBundle,
    refmap: dict[str, str],
) -> list[tuple[str, str]]:
    """Translate one Interface into the (src, dst) wire pairs to draw.

    For pin↔pin connections we use `<refdes>.<port>` on both ends. The
    "external" pseudo-subsystem (e.g. battery → buck) is rendered via
    the power-rail symbol already placed at the consumer pin, so no
    two-endpoint wire is emitted here.
    """
    if iface.from_subsystem == "external" or iface.to_subsystem == "external":
        return []
    src_ref = refmap.get(iface.from_subsystem)
    dst_ref = refmap.get(iface.to_subsystem)
    if src_ref is None or dst_ref is None:
        return []
    return [(f"{src_ref}.{iface.from_port}", f"{dst_ref}.{iface.to_port}")]


# ---------------------------------------------------------------------------
# ERC feedback
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ErcViolation:
    severity: str
    type: str
    description: str
    items: tuple[str, ...] = ()


@dataclass(frozen=True)
class ErcResult:
    """Parsed kicad-cli sch erc --format json output.

    `clean` is True iff the report has zero real violations after the
    `expected_codes` filter is applied. Counts are split so a phase-2 UI
    can show "5 real / 3 expected" without the agent re-parsing.
    """
    report_path: Path
    clean: bool
    real_violations: tuple[ErcViolation, ...]
    expected_violations: tuple[ErcViolation, ...]

    @property
    def real_count(self) -> int:
        return len(self.real_violations)

    @property
    def expected_count(self) -> int:
        return len(self.expected_violations)


def parse_erc_report(report_path: str | Path, expected_codes: tuple[str, ...] = ()) -> ErcResult:
    """Parse `--format json` ERC output. `expected_codes` is the list of
    ERC violation `type` strings the project chooses to ignore (e.g.
    `pin_not_connected` on intentional unused MCU pins). Real violations
    are everything else."""
    import json as _json

    p = Path(report_path)
    if not p.exists():
        raise FileNotFoundError(f"ERC report not found at {p}")
    data = _json.loads(p.read_text(encoding="utf-8"))

    real: list[ErcViolation] = []
    expected: list[ErcViolation] = []
    for sheet in data.get("sheets", []):
        for v in sheet.get("violations", []):
            vio = ErcViolation(
                severity=str(v.get("severity", "unknown")),
                type=str(v.get("type", "unknown")),
                description=str(v.get("description", "")),
                items=tuple(
                    str(it.get("description", "")) for it in v.get("items", [])
                ),
            )
            (expected if vio.type in expected_codes else real).append(vio)
    return ErcResult(
        report_path=p,
        clean=len(real) == 0,
        real_violations=tuple(real),
        expected_violations=tuple(expected),
    )
