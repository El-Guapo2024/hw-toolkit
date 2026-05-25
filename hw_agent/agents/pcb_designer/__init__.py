"""pcb_designer agent — turns a ResearchBundle into a KiCad schematic
(and, later, a placed/routed PCB + fab deliverables).

Phase 1: validate.py    — load + pydantic-validate ResearchBundle
Phase 2: schematic.py   — emit flat .kicad_sch via designer-mcp,
                          run kicad-cli sch erc, surface results
Phase 3: placement.py   — refine schematic placement via live-edit-mcp
                          live_move_symbol, zone-based heuristic
"""
from hw_agent.agents.pcb_designer.placement import (
    MoveSymbol,
    PlacementPlan,
    plan_placement,
    zone_assignments,
)
from hw_agent.agents.pcb_designer.schematic import (
    AddCustomIC,
    AddGround,
    AddPower,
    AddWire,
    ErcResult,
    ErcViolation,
    SchematicPlan,
    parse_erc_report,
    plan_schematic,
    write_blank_schematic,
)
from hw_agent.agents.pcb_designer.validate import (
    ResearchBundleLoadError,
    load_research_bundle,
)

__all__ = [
    # Phase 1
    "load_research_bundle",
    "ResearchBundleLoadError",
    # Phase 2
    "write_blank_schematic",
    "plan_schematic",
    "SchematicPlan",
    "AddCustomIC",
    "AddPower",
    "AddGround",
    "AddWire",
    "parse_erc_report",
    "ErcResult",
    "ErcViolation",
    # Phase 3
    "plan_placement",
    "PlacementPlan",
    "MoveSymbol",
    "zone_assignments",
]
