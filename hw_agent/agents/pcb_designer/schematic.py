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
from typing import Literal

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


def _synthesize_pins(pick: SubsystemPick, body_center: tuple[float, float]) -> list[dict[str, object]]:
    """Pick a side per port name and emit add_custom_ic pin dicts.

    Heuristic:
      power-in   (VIN, VBAT)            → left
      power-out  (VOUT, VDD, VCC, 3V3)  → right (for buck/LDO providers)
                                          left  (for consumers — but
                                          designer-mcp doesn't care which
                                          side, ergonomics only)
      ground     (GND, VSS)             → bottom
      I²C / SPI  (SDA, SCL, MISO, MOSI) → right
      default                           → right

    The body is `_IC_BODY_W_MM × _IC_BODY_H_MM` mm; pin coords are placed
    on the body perimeter, spaced evenly per side.
    """
    cx, cy = body_center
    half_w = _IC_BODY_W_MM / 2
    half_h = _IC_BODY_H_MM / 2

    buckets: dict[str, list[str]] = {
        "left": [], "right": [], "top": [], "bottom": [],
    }
    for port in pick.port_bindings.keys():
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
                pins.append({"name": name, "at": [round(x, 3), round(y, 3)], "side": side})
        else:  # top / bottom
            y = cy - half_h if side == "top" else cy + half_h
            spacing = _IC_BODY_W_MM / (len(names) + 1)
            for i, name in enumerate(names, start=1):
                x = cx - half_w + spacing * i
                pins.append({"name": name, "at": [round(x, 3), round(y, 3)], "side": side})
    return pins


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

    # 1. Place one custom IC per subsystem on a horizontal lane.
    positions: dict[str, tuple[float, float]] = {}
    for i, sub in enumerate(bundle.subsystems):
        cx = _LANE_X_START_MM + i * _LANE_X_STEP_MM
        cy = _LANE_Y_MM
        positions[sub.id] = (cx, cy)
        ref = refmap[sub.id]
        pins = _synthesize_pins(sub, (cx, cy))
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

    # 2. Drop GND symbol below each subsystem (every IC needs GND).
    for sub in bundle.subsystems:
        cx, cy = positions[sub.id]
        pwr_counter += 1
        ops.append(
            AddGround(
                kicad_sch=sch,
                ref=f"#PWR{pwr_counter:03d}",
                at_x=cx,
                at_y=cy + _GROUND_DROP_MM,
            )
        )

    # 3. For each power interface, drop a labelled power symbol at the
    #    consumer side so eeschema renders the rail name visibly.
    for iface in bundle.interfaces:
        if iface.type != "power":
            continue
        if iface.to_subsystem == "external":
            continue
        label = _label_for_power_interface(iface)
        cx, cy = positions[iface.to_subsystem]
        pwr_counter += 1
        ops.append(
            AddPower(
                kicad_sch=sch,
                ref=f"#PWR{pwr_counter:03d}",
                label=label,
                at_x=cx,
                at_y=cy - _POWER_DROP_MM,
            )
        )

    # 4. Wires per interface — pin to pin (refdes.port format).
    for iface in bundle.interfaces:
        for src, dst in _interface_wire_endpoints(iface, bundle, refmap):
            ops.append(AddWire(kicad_sch=sch, src=src, dst=dst))

    return SchematicPlan(kicad_sch=Path(sch_path), ops=tuple(ops))


def _label_for_power_interface(iface: Interface) -> str:
    """Pick a KiCad power-label string from the rail's nominal voltage."""
    v = iface.voltage_nominal_v
    if v is None:
        return "VCC"
    # Common rails get canonical names; otherwise stringify as V<n>V<d>.
    canon = {3.3: "3V3", 5.0: "5V", 12.0: "12V", 24.0: "24V"}
    if v in canon:
        return canon[v]
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
