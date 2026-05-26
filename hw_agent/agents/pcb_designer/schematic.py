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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal

from hw_agent.agents.pcb_designer.kicad_projector import refdes_map_for_bundle
from hw_agent.core import Interface, ResearchBundle, SubsystemPick

# ---------------------------------------------------------------------------
# Layout policy (flat MVP)
# ---------------------------------------------------------------------------

# A4 ≈ 297 × 210 mm. Leave margin; place subsystems on a horizontal lane.
_LANE_Y_MM = 100.0
_LANE_X_START_MM = 60.0
_LANE_X_STEP_MM = 80.0
_IC_BODY_W_MM = 30.0
_IC_BODY_H_MM = 30.0
_POWER_DROP_MM = 25.4   # power symbol sits N mm above its IC
_GROUND_DROP_MM = 25.4  # ground symbol sits N mm below

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


SchematicOp = AddCustomIC | AddPower | AddGround | AddWire


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


def _electrical_type(pick: SubsystemPick, port: str) -> str:
    """Map a port name to a KiCad pin electrical_type.

    Power providers (buck/LDO) emit `power_out` on VOUT; consumers see
    VDD/VCC as `power_in`. GND/VIN are always `power_in`. Signals are
    `bidirectional` (good default for I²C/SPI). KiCad's ERC uses this
    to validate `power_pin_not_driven`.
    """
    p = port.upper()
    if p in _GROUND_NAMES or p in _POWER_IN_NAMES:
        return "power_in"
    if p in _POWER_OUT_NAMES:
        return "power_out" if _is_power_provider(pick) else "power_in"
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


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


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

    # 1. Place one custom IC per subsystem on a horizontal lane.
    positions: dict[str, tuple[float, float]] = {}
    for i, sub in enumerate(bundle.subsystems):
        cx = _snap_grid(_LANE_X_START_MM + i * _LANE_X_STEP_MM)
        cy = _snap_grid(_LANE_Y_MM)
        positions[sub.id] = (cx, cy)
        ref = refmap[sub.id]
        # Merge auto-derived ports w/ any legacy port_bindings on the pick.
        ports = list(sub.port_bindings.keys()) + ports_by_sub.get(sub.id, [])
        pins = _synthesize_pins(sub, (cx, cy), ports=ports)
        ops.append(
            AddCustomIC(
                kicad_sch=sch,
                ref=ref,
                name=sub.mpn,
                at_x=cx,
                at_y=cy,
                pins=tuple(pins),
                footprint=sub.package or None,
            )
        )

    # 2. Drop a GND symbol per subsystem ONLY if that subsystem has a GND
    #    port participating in some net. Otherwise we'd leave floating
    #    power_in pins that ERC flags as `power_pin_not_driven`.
    for sub in bundle.subsystems:
        ports = ports_by_sub.get(sub.id, [])
        if not any(p.upper() in _GROUND_NAMES for p in ports):
            continue
        cx, cy = positions[sub.id]
        pwr_counter += 1
        ops.append(
            AddGround(
                kicad_sch=sch,
                ref=f"#PWR{pwr_counter:03d}",
                at_x=cx,
                at_y=_snap_grid(cy + _GROUND_DROP_MM),
            )
        )

    # 3. Wires per interface — pin to pin (refdes.port format). The
    #    floating per-consumer power-label drops from earlier versions are
    #    GONE: they were unwired `power_in` symbols on isolated nets and
    #    drove every ERC warning. KiCad infers the rail name from the
    #    `power_out` pin (e.g. buck.VOUT) via wires alone; the engineer
    #    can sprinkle visual labels during phase-2 hand-tune.
    for iface in bundle.interfaces:
        for src, dst in _interface_wire_endpoints(iface, bundle, refmap):
            ops.append(AddWire(kicad_sch=sch, src=src, dst=dst))

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
