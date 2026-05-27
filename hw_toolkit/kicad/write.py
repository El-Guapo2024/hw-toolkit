"""Apply a `SchematicPlan` to a `.kicad_sch` file (pure Python, no MCP).

The planner (`hw_toolkit.kicad.planner.plan_schematic`) returns
a `SchematicPlan` whose ops historically dispatched via designer-mcp tools.
This module walks the plan and calls the matching `sch_ops.add_*` function
directly so a notebook cell can produce a fully populated `.kicad_sch`
without an MCP round-trip.
"""
from __future__ import annotations

from pathlib import Path

from hw_toolkit.kicad.planner import (
    AddCustomIC,
    AddGround,
    AddPower,
    AddWire,
    SchematicPlan,
    plan_schematic,
    write_blank_schematic,
)
from hw_toolkit.kicad import lib as kicad_lib, sch_ops
from hw_toolkit.core import ResearchBundle


def apply_plan(plan: SchematicPlan) -> Path:
    """Execute every op in `plan` against `plan.kicad_sch`. Returns the path.

    AddWire endpoints are pre-resolved to absolute `@x,y` coords using
    the pin positions captured in the upstream `AddCustomIC` ops. This
    bypasses kicad-sch-api's `get_component_pin_position` which doesn't
    locate pins on our synthesized custom-IC instances.
    """
    path = plan.kicad_sch
    # `add_custom_ic` synthesizes `<sch_dir>/hwagent.kicad_sym`. Register that
    # dir so the wire-resolver's standalone `load_symbol()` can find it.
    kicad_lib.register_extra_lib_path(path.parent)

    # Build refdes.port → absolute (x, y) map from AddCustomIC ops up-front.
    pin_xy: dict[str, tuple[float, float]] = {}
    for op in plan.ops:
        if isinstance(op, AddCustomIC):
            for p in op.pins:
                pin_xy[f"{op.ref}.{p['name']}"] = (
                    float(p["at"][0]), float(p["at"][1]),
                )

    def _wire_endpoint(s: str) -> str:
        """Translate `<ref>.<port>` to `@x,y` if we know the coord; otherwise
        pass through (lets sch_ops fall back to its own resolver)."""
        if s in pin_xy:
            x, y = pin_xy[s]
            return f"@{x},{y}"
        return s

    for op in plan.ops:
        if isinstance(op, AddCustomIC):
            sch_ops.add_custom_ic(
                path=path,
                ref=op.ref,
                name=op.name,
                at=(op.at_x, op.at_y),
                pins=list(op.pins),
                size=(op.width, op.height),
                footprint=op.footprint,
                rotation=op.rotation,
            )
        elif isinstance(op, AddPower):
            sch_ops.add_power(
                path=path,
                ref=op.ref,
                at=(op.at_x, op.at_y),
                label=op.label,
            )
        elif isinstance(op, AddGround):
            sch_ops.add_ground(
                path=path,
                ref=op.ref,
                at=(op.at_x, op.at_y),
            )
        elif isinstance(op, AddWire):
            sch_ops.add_wire(
                path=path,
                src=_wire_endpoint(op.src),
                dst=_wire_endpoint(op.dst),
            )
        else:
            raise TypeError(f"unsupported plan op: {type(op).__name__}")
    return path


_SCRATCH_MARKER = ".hw_toolkit_scratch"


def mark_scratch(dir_path: Path) -> None:
    """Drop a marker file proving `dir_path` is a hw_toolkit scratch dir.

    `write_populated` only wipes lib files in dirs that carry this marker,
    so a user pointing the planner at a hand-curated project never has
    their `hwagent.kicad_sym` clobbered.
    """
    dir_path.mkdir(parents=True, exist_ok=True)
    marker = dir_path / _SCRATCH_MARKER
    if not marker.exists():
        marker.write_text("hw_toolkit-scratch\n", encoding="utf-8")


def write_populated(
    bundle: ResearchBundle,
    sch_path: str | Path,
    *,
    overwrite: bool = True,
) -> Path:
    """Blank-write + plan + apply, end-to-end. Returns the populated path.

    Also wipes the project-local `hwagent.kicad_sym` first so each call
    regenerates the lib with the CURRENT pin set. Wipe is *only* performed
    when the parent dir is marked as a hw_toolkit scratch (see
    `mark_scratch`) — never destroys user-owned project libs.
    """
    sch_path = Path(sch_path)
    is_scratch = (sch_path.parent / _SCRATCH_MARKER).exists()
    if overwrite and is_scratch:
        for stale in (
            sch_path.parent / "hwagent.kicad_sym",
            sch_path.parent / "sym-lib-table",
        ):
            stale.unlink(missing_ok=True)
    # kicad-sch-api caches lib parses by path. Clear so we re-read the
    # freshly synthesized `hwagent.kicad_sym` instead of returning a
    # stale (e.g. empty-pin) version from an earlier `module.show()` call.
    try:
        from kicad_sch_api.library.cache import get_symbol_cache
        get_symbol_cache().clear_cache()
    except Exception:
        pass
    write_blank_schematic(sch_path, overwrite=overwrite)
    plan = plan_schematic(bundle, sch_path)
    apply_plan(plan)
    return sch_path
