"""hw_toolkit data models — the validated shapes used throughout.

`SubsystemPick`, `Interface`, `ResearchBundle` are pydantic models the
notebook flow builds up as the engineer adds modules and nets. The
`Board` object exposes them via `.bundle` for inspection.
"""
from hw_toolkit.core.models import Interface, ResearchBundle, SubsystemPick

__all__ = ["Interface", "ResearchBundle", "SubsystemPick"]
