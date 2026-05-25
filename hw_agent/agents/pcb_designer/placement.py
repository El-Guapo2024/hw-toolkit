"""Phase 3 — schematic placement planner.

After Phase 2 has dropped every subsystem onto a horizontal lane (a
deliberately dumb starting point that lets ERC run), Phase 3 refines
positions into functional zones the engineer can actually read:

    ┌──────────────┬───────────────┬──────────────┬──────────────┐
    │ power_in     │ switcher      │ mcu          │ connector    │
    │ (J, fuse)    │ regulator     │ sensors      │ (J right)    │
    └──────────────┴───────────────┴──────────────┴──────────────┘

Each move is one `mcp__live-edit-mcp__live_move_symbol` call so the
engineer sees the parts walk into place in eeschema. The planner does
NOT touch KiCad itself — it emits ops; the agent loop dispatches them.

The agent must have eeschema open with API server enabled
(Preferences → API server) for the live tool to land. If it isn't,
`live_move_symbol` returns a clear error; the planner stays valid.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from hw_agent.agents.pcb_designer.kicad_projector import refdes_map_for_bundle
from hw_agent.core import ResearchBundle, SubsystemPick

# ---------------------------------------------------------------------------
# Zone geometry (A4 ≈ 297 × 210 mm; the schematic stub uses A4)
# ---------------------------------------------------------------------------

Zone = Literal["power_in", "switcher", "regulator", "mcu", "sensor", "actuator", "connector", "misc"]

# Zone center column (mm). Tuned for ~6-8 subsystems on one A4 sheet.
_ZONE_X: dict[Zone, float] = {
    "power_in":  40.0,
    "switcher":  90.0,
    "regulator": 130.0,
    "mcu":       170.0,
    "sensor":    210.0,
    "actuator":  240.0,
    "connector": 270.0,
    "misc":      150.0,
}

# Vertical lane the zone occupies. Multiple parts in one zone stack
# downward from this y, spaced by _ZONE_Y_STEP_MM.
_ZONE_Y_TOP_MM = 70.0
_ZONE_Y_STEP_MM = 40.0


# ---------------------------------------------------------------------------
# Plan ops
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MoveSymbol:
    """One live_move_symbol call against eeschema.

    `kicad_sch` is the file path for logging only — the IPC bridge needs
    it but doesn't read the file. The actual mutation happens in the
    open eeschema instance.
    """
    kicad_sch: str
    ref: str
    x_mm: float
    y_mm: float
    with_render: bool = False
    tool: Literal["mcp__live-edit-mcp__live_move_symbol"] = (
        "mcp__live-edit-mcp__live_move_symbol"
    )


PlacementOp = MoveSymbol


@dataclass(frozen=True)
class PlacementPlan:
    """Ordered list of MoveSymbol ops the agent executes one-by-one."""
    kicad_sch: Path
    ops: tuple[PlacementOp, ...]

    def as_tool_calls(self) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for op in self.ops:
            d = {k: v for k, v in op.__dict__.items() if k != "tool"}
            out.append({"tool": op.tool, "args": d})
        return out


# ---------------------------------------------------------------------------
# Zone classification
# ---------------------------------------------------------------------------


_CATEGORY_ZONE: dict[str, Zone] = {
    "buck_converter": "switcher",
    "ldo":            "regulator",
    "mcu_module":     "mcu",
    "mcu":            "mcu",
    "sensor_i2c":     "sensor",
    "sensor":         "sensor",
    "motor_driver":   "actuator",
    "connector":      "connector",
    "fuse":           "power_in",
    "tvs":            "power_in",
}


def _zone_for(pick: SubsystemPick, *, is_first_power_connector: bool) -> Zone:
    """Map a SubsystemPick to its layout zone.

    Connectors get a finer split: the first power-bearing connector goes
    to `power_in` (board edge, far left). Everything else lands in
    `connector` (right edge). Caller passes the precomputed
    `is_first_power_connector` flag.
    """
    if pick.category == "connector" and is_first_power_connector:
        return "power_in"
    return _CATEGORY_ZONE.get(pick.category, "misc")


def _first_power_connector_id(bundle: ResearchBundle) -> str | None:
    """Find the connector subsystem that sources a power interface.

    A connector is "power-in" if it appears as the `from_subsystem` of
    any `power` interface. The first such connector in bundle order
    wins — deterministic on rerun.
    """
    power_sources: set[str] = {
        i.from_subsystem
        for i in bundle.interfaces
        if i.type == "power" and i.from_subsystem != "external"
    }
    for sub in bundle.subsystems:
        if sub.category == "connector" and sub.id in power_sources:
            return sub.id
    return None


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


def plan_placement(
    bundle: ResearchBundle,
    sch_path: str | Path,
    *,
    with_render_on_last: bool = True,
) -> PlacementPlan:
    """Build the move sequence that walks every subsystem into its zone.

    Order of ops is deterministic: zones in canvas order (power_in →
    connector), subsystems within a zone in bundle order. Same bundle
    + sch_path always produces the same plan.

    `with_render_on_last` flips `with_render=True` on the final op so
    the agent gets one PNG back instead of one per move (cheaper feedback).
    """
    sch = str(sch_path)
    refmap = refdes_map_for_bundle(bundle)
    power_conn_id = _first_power_connector_id(bundle)

    # Group by zone, preserving bundle order within each zone.
    zones: dict[Zone, list[SubsystemPick]] = {}
    for sub in bundle.subsystems:
        is_pwr = sub.id == power_conn_id
        z = _zone_for(sub, is_first_power_connector=is_pwr)
        zones.setdefault(z, []).append(sub)

    # Emit moves zone-by-zone in canvas order.
    canvas_order: tuple[Zone, ...] = (
        "power_in", "switcher", "regulator", "mcu",
        "sensor", "actuator", "connector", "misc",
    )

    ops: list[PlacementOp] = []
    for zone in canvas_order:
        picks = zones.get(zone, [])
        if not picks:
            continue
        x = _ZONE_X[zone]
        for row, pick in enumerate(picks):
            y = _ZONE_Y_TOP_MM + row * _ZONE_Y_STEP_MM
            ops.append(
                MoveSymbol(
                    kicad_sch=sch,
                    ref=refmap[pick.id],
                    x_mm=round(x, 3),
                    y_mm=round(y, 3),
                    with_render=False,
                )
            )

    # Flip render flag on the last op so the agent sees the final layout.
    if ops and with_render_on_last:
        last = ops[-1]
        ops[-1] = MoveSymbol(
            kicad_sch=last.kicad_sch,
            ref=last.ref,
            x_mm=last.x_mm,
            y_mm=last.y_mm,
            with_render=True,
        )

    return PlacementPlan(kicad_sch=Path(sch_path), ops=tuple(ops))


# ---------------------------------------------------------------------------
# Introspection — useful when the agent needs to explain a layout
# ---------------------------------------------------------------------------


def zone_assignments(bundle: ResearchBundle) -> dict[str, Zone]:
    """Return {subsystem_id: zone} for the whole bundle.

    Lets the agent narrate "I placed U1 in the switcher zone" without
    re-running the full planner.
    """
    power_conn_id = _first_power_connector_id(bundle)
    return {
        sub.id: _zone_for(sub, is_first_power_connector=(sub.id == power_conn_id))
        for sub in bundle.subsystems
    }
