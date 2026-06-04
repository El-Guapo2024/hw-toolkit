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

import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

from hw_toolkit.exceptions import LayoutError
from hw_toolkit.kicad.layout_elk import ElkEdge, ElkNode, build_elk_layout
from hw_toolkit.kicad.projector import refdes_map_for_bundle
from hw_toolkit.core import Interface, ResearchBundle, SubsystemPick

# ---------------------------------------------------------------------------
# Layout policy
# ---------------------------------------------------------------------------
#
# Placement + wiring are done by ELK (hw_toolkit.kicad.layout_elk), the
# only layout path — there is no heuristic grid / point-to-point fallback.
# The constants below only size SYNTHESIZED custom-IC bodies (real library
# symbols carry their own geometry, measured empirically).
_IC_BODY_W_MM = 30.0    # body width for synthesized (placeholder) symbols
_IC_BODY_H_MM = 30.0    # body height for synthesized (placeholder) symbols

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
\t(generator "hw_toolkit.planner")
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


_PASSIVE_CATEGORIES = {"resistor", "capacitor", "inductor"}


@dataclass(frozen=True)
class _SymbolPins:
    """Pin geometry for one subsystem, in symbol-local mm (anchor at 0,0).

    `offsets` maps the canonical port id → (dx, dy). For a real library
    symbol the port id is the KiCad pin *number*; for a synthesized custom
    IC it is the pin *name*. `resolve` maps an interface port token (a pin
    name or number) to a canonical port id, or None if the symbol has no
    such pin. `synth_pins`, when set, is the custom-IC pin list (with
    anchor-relative `at`) to re-emit at the placed anchor.
    """
    offsets: dict[str, tuple[float, float]]
    resolve_map: dict[str, str]
    synth_pins: tuple[dict[str, object], ...] | None = None

    def resolve(self, token: str) -> str | None:
        if token in self.offsets:
            return token
        return self.resolve_map.get(token)


def _measure_real_offsets(
    lib_ids: list[str], measure_dir: Path
) -> dict[str, dict[str, tuple[float, float]]]:
    """Empirically measure pin offsets for each real library symbol.

    Scratch-places every unique `lib_id` at the origin in a throwaway
    `.kicad_sch` and reads back `list_component_pins` — sidesteps the
    lib-coord (Y-up) vs placed-coord (Y-down) conversion entirely, since
    the placed pin position IS the offset from a (0,0) anchor.
    """
    from hw_toolkit.kicad import sch_ops

    out: dict[str, dict[str, tuple[float, float]]] = {}
    uniques = list(dict.fromkeys(lib_ids))
    if not uniques:
        return out
    p = measure_dir / "_measure.kicad_sch"
    write_blank_schematic(p, overwrite=True)
    refs: dict[str, str] = {}
    for i, lib_id in enumerate(uniques):
        ref = f"M{i}"
        try:
            sch_ops.add_ic(path=p, ref=ref, lib_id=lib_id, at=(0.0, 0.0))
            refs[lib_id] = ref
        except Exception as e:  # symbol failed to place → can't lay it out
            raise LayoutError(
                reason="symbol_unplaceable",
                detail=f"{lib_id!r} could not be scratch-placed: {e}",
            ) from e
    sch = sch_ops._load(p)
    for lib_id, ref in refs.items():
        pins = sch.list_component_pins(ref) or []
        out[lib_id] = {num: (pos.x, pos.y) for num, pos in pins}
    return out


def _name_to_number(lib_id: str) -> dict[str, str]:
    """Build a {pin name|number → pin number} map from the library symbol.

    First-seen wins on a duplicate name (e.g. several GND pins) — matches
    the old `sch_ops._resolve_pin_number` behaviour.
    """
    mapping: dict[str, str] = {}
    try:
        from hw_toolkit.kicad.lib import load_symbol

        for pin in load_symbol(lib_id).pins:
            mapping.setdefault(pin.name, pin.number)
            mapping.setdefault(pin.number, pin.number)
    except Exception:
        pass
    return mapping


def _symbol_pins_for(
    sub: SubsystemPick,
    ports: list[str],
    real_offsets: dict[str, dict[str, tuple[float, float]]],
) -> _SymbolPins:
    """Resolve one subsystem's pin geometry for the ELK graph."""
    if sub.lib_id:
        offsets = real_offsets.get(sub.lib_id, {})
        return _SymbolPins(
            offsets=offsets,
            resolve_map=_name_to_number(sub.lib_id),
        )
    # Synthesized custom IC: pin layout generated about a (0,0) anchor, so
    # each pin's `at` IS its offset. Port id == pin name.
    pins = _synthesize_pins(sub, (0.0, 0.0), ports=ports)
    offsets = {
        str(p["name"]): (float(p["at"][0]), float(p["at"][1])) for p in pins
    }
    return _SymbolPins(
        offsets=offsets,
        resolve_map={},  # custom-IC interfaces always reference pin names
        synth_pins=tuple(pins),
    )


def plan_schematic(bundle: ResearchBundle, sch_path: str | Path) -> SchematicPlan:
    """Build the full sequence of ops needed to render `bundle` as a flat
    schematic, placed + wired by ELK (the only layout path).

    The whole drawing — subsystem symbols, per-subsystem GND symbols, and
    per-rail PWR_FLAGs — is laid out as one ELK graph: each symbol is a
    node carrying its empirically-measured pin offsets as fixed ports, and
    every connection (interface pin↔pin, GND-symbol↔GND-pin,
    flag↔hub-pin) is an ELK edge. ELK returns placement anchors plus
    orthogonal wire routes; we place each symbol so its pins land on the
    fixed ports, then emit one straight `AddWire` per route segment.

    Raises `LayoutError` if ELK is unavailable or the layout fails — there
    is no heuristic / point-to-point fallback.
    """
    sch = str(sch_path)
    refmap = refdes_map_for_bundle(bundle)
    sub_by_id = {s.id: s for s in bundle.subsystems}

    # Derive each subsystem's port set from the bundle's interfaces. The
    # notebook flow never sets `subsystem.port_bindings` directly — nets /
    # connects produce interfaces, and ports fall out of those.
    ports_by_sub: dict[str, list[str]] = {s.id: [] for s in bundle.subsystems}
    for iface in bundle.interfaces:
        if iface.from_subsystem in ports_by_sub:
            ports_by_sub[iface.from_subsystem].append(iface.from_port)
        if iface.to_subsystem in ports_by_sub:
            ports_by_sub[iface.to_subsystem].append(iface.to_port)

    # --- 1. Measure pin geometry (empirically for real symbols) ----------
    real_lib_ids = [s.lib_id for s in bundle.subsystems if s.lib_id]
    measure_dir = Path(tempfile.mkdtemp(prefix="hw_elk_pins_"))
    try:
        real_offsets = _measure_real_offsets(real_lib_ids, measure_dir)
    finally:
        shutil.rmtree(measure_dir, ignore_errors=True)

    sym_pins: dict[str, _SymbolPins] = {}
    for sub in bundle.subsystems:
        ports = list(sub.port_bindings.keys()) + ports_by_sub.get(sub.id, [])
        sym_pins[sub.id] = _symbol_pins_for(sub, ports, real_offsets)

    # --- 2. Build ELK nodes/edges ----------------------------------------
    nodes: list[ElkNode] = [
        ElkNode(id=s.id, pin_offsets=sym_pins[s.id].offsets)
        for s in bundle.subsystems
    ]
    edges: list[ElkEdge] = []

    # 2a. Interface pin↔pin edges (skip the `external` pseudo-subsystem).
    for iface in bundle.interfaces:
        a, b = iface.from_subsystem, iface.to_subsystem
        if a == "external" or b == "external":
            continue
        if a not in sym_pins or b not in sym_pins:
            continue
        pa = sym_pins[a].resolve(iface.from_port)
        pb = sym_pins[b].resolve(iface.to_port)
        if pa is None or pb is None:
            raise LayoutError(
                reason="unresolved_pin",
                detail=(f"interface {iface.id!r}: "
                        f"{a}.{iface.from_port}→{b}.{iface.to_port} — "
                        f"pin not found on the placed symbol"),
            )
        edges.append(ElkEdge(src=(a, pa), dst=(b, pb)))

    # 2b. One GND symbol per subsystem that exposes a GND port, tied to
    #     that pin (so the pin sits on the GND net instead of reading as a
    #     floating `power_in`). Each GND symbol is a 1-pin node.
    gnd_nodes: list[tuple[str, str]] = []  # (node_id, refdes)
    pwr_counter = 0
    for sub in bundle.subsystems:
        ports = ports_by_sub.get(sub.id, [])
        gnd_port = next((p for p in ports if p.upper() in _GROUND_NAMES), None)
        if gnd_port is None:
            continue
        port_id = sym_pins[sub.id].resolve(gnd_port)
        if port_id is None:
            continue
        pwr_counter += 1
        nid = f"GND\x1f{sub.id}"
        ref = f"#PWR{pwr_counter:03d}"
        gnd_nodes.append((nid, ref))
        nodes.append(ElkNode(id=nid, pin_offsets={"1": (0.0, 0.0)}))
        edges.append(ElkEdge(src=(nid, "1"), dst=(sub.id, port_id)))

    # 2c. One PWR_FLAG per power-rail hub pin so ERC sees the rail driven.
    #     Skip rails whose hub pin is itself a `power_out` driver (two
    #     power_out pins on one net trips ERC). Each flag is a 1-pin node.
    power_hubs: dict[tuple[str, str], None] = {}
    for iface in bundle.interfaces:
        if iface.type != "power" or iface.from_subsystem == "external":
            continue
        power_hubs.setdefault((iface.from_subsystem, iface.from_port), None)

    flag_nodes: list[tuple[str, str]] = []  # (node_id, refdes)
    for hub_sub, hub_port in power_hubs:
        if hub_sub not in sym_pins:
            continue
        port_id = sym_pins[hub_sub].resolve(hub_port)
        if port_id is None:
            continue
        hub_pick = sub_by_id.get(hub_sub)
        if hub_pick and _electrical_type(hub_pick, hub_port) == "power_out":
            continue
        pwr_counter += 1
        nid = f"FLG\x1f{hub_sub}\x1f{hub_port}"
        ref = f"#FLG{pwr_counter:03d}"
        flag_nodes.append((nid, ref))
        nodes.append(ElkNode(id=nid, pin_offsets={"1": (0.0, 0.0)}))
        edges.append(ElkEdge(src=(nid, "1"), dst=(hub_sub, port_id)))

    # --- 3. Run ELK ------------------------------------------------------
    layout = build_elk_layout(nodes, edges)

    # --- 4. Emit symbol placements at the ELK anchors --------------------
    ops: list[SchematicOp] = []
    for sub in bundle.subsystems:
        ax, ay = layout.anchors[sub.id]
        ref = refmap[sub.id]
        sp = sym_pins[sub.id]
        if sub.lib_id:
            ops.append(
                AddSymbol(
                    kicad_sch=sch,
                    ref=ref,
                    lib_id=sub.lib_id,
                    value=sub.mpn,
                    at_x=ax,
                    at_y=ay,
                    footprint=sub.footprint or _kicad_footprint_for(sub.package) or None,
                )
            )
            continue
        # Custom IC: re-emit synthesized pins shifted to the placed anchor
        # (offsets were measured about a 0,0 anchor).
        pins = tuple(
            {**p, "at": [float(p["at"][0]) + ax, float(p["at"][1]) + ay]}
            for p in (sp.synth_pins or ())
        )
        ops.append(
            AddCustomIC(
                kicad_sch=sch,
                ref=ref,
                name=sub.mpn,
                at_x=ax,
                at_y=ay,
                pins=pins,
                footprint=(sub.footprint or _kicad_footprint_for(sub.package)) or None,
            )
        )

    for nid, ref in gnd_nodes:
        ax, ay = layout.anchors[nid]
        ops.append(AddGround(kicad_sch=sch, ref=ref, at_x=ax, at_y=ay))
    for nid, ref in flag_nodes:
        ax, ay = layout.anchors[nid]
        ops.append(
            AddPower(kicad_sch=sch, ref=ref, label="PWR_FLAG", at_x=ax, at_y=ay)
        )

    # --- 5. Emit ELK's orthogonal routes as straight wire segments -------
    for x1, y1, x2, y2 in layout.wires:
        ops.append(AddWire(kicad_sch=sch, src=f"@{x1},{y1}", dst=f"@{x2},{y2}"))

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
