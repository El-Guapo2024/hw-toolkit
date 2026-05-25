"""Public surface of the typed core.

Two contracts cross the agent boundary:

  - `ResearchBundle` — produced by the researcher agent, consumed by pcb-designer.
  - `FabBundle`      — produced by the pcb-designer agent, the final fab output.

Everything else in this package is supporting infrastructure (existing
`ElectronicSubsystem` for legacy MCP tools, enums, typed IDs, provenance).
"""
from hw_agent.core.fab_bundle import FabBundle
from hw_agent.core.research_bundle import Interface, ResearchBundle, SubsystemPick

__all__ = [
    # Contracts
    "ResearchBundle",
    "FabBundle",
    # Sub-models exposed because callers construct them directly
    "SubsystemPick",
    "Interface",
]
