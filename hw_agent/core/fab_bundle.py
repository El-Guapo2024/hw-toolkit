"""FabBundle — the contract OUTPUT from pcb-designer.

What the pcb-designer agent produces and locks at the `ready_to_fab` gate.
References back to the `ResearchBundle` it consumed so the lineage is auditable.

Kept intentionally minimal. File paths only — actual binary contents live on
disk at the paths declared here.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FabBundle(BaseModel):
    """Locked fab-ready output. Immutable once written.

    The agent stamps `rev_letter` (A, B, C, ...); subsequent revisions write
    new FabBundles, never edit existing ones. `consumed_research_tag` proves
    which research baseline was the basis.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    project_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    rev_letter: str = Field(pattern=r"^[A-Z]$")
    """Single uppercase letter: A, B, C. Never reused; increments per fab spin."""

    # Lineage — proves which research baseline this fab consumed
    consumed_research_tag: str = Field(min_length=4, max_length=128)

    # KiCad outputs
    kicad_sch: Path
    kicad_pcb: Path

    # Fab deliverables
    gerbers_dir: Path
    """Directory containing the gerber + drill files."""
    bom_csv: Path
    cpl_csv: Path

    # Gate results — booleans here, full reports referenced separately
    erc_clean: bool
    drc_clean: bool
    vendor_validated: bool
    """`pcborder_validate_for_vendor` PASS at lock time."""
    stock_verified: bool
    """Every BOM line had stock ≥ qty_per_board × build_qty at lock time."""

    # Build settings echo (must match ResearchBundle.vendor etc.)
    vendor: Literal["jlcpcb", "pcbway", "oshpark", "aisler"] = "jlcpcb"
    build_qty: int = Field(ge=1)

    # Audit
    fab_baseline_git_tag: str = Field(min_length=4, max_length=128)
    """e.g. `control_hub_v1/fab-baseline-rev_A`."""
    locked_at: datetime

    notes: str = ""

    @model_validator(mode="after")
    def all_paths_relative_to_project(self) -> "FabBundle":
        """Reject absolute paths — fab artifacts live under the project tree."""
        for name, p in (
            ("kicad_sch", self.kicad_sch),
            ("kicad_pcb", self.kicad_pcb),
            ("gerbers_dir", self.gerbers_dir),
            ("bom_csv", self.bom_csv),
            ("cpl_csv", self.cpl_csv),
        ):
            if p.is_absolute():
                raise ValueError(
                    f"{name} must be a relative path under the project tree, "
                    f"got absolute: {p}"
                )
        return self

    @model_validator(mode="after")
    def all_gates_passed(self) -> "FabBundle":
        """A FabBundle cannot be written until every gate check PASSed.

        If you need to record a fab attempt that failed checks, write it
        to a separate failure log — not to a FabBundle.
        """
        failures = []
        if not self.erc_clean:
            failures.append("erc_clean")
        if not self.drc_clean:
            failures.append("drc_clean")
        if not self.vendor_validated:
            failures.append("vendor_validated")
        if not self.stock_verified:
            failures.append("stock_verified")
        if failures:
            raise ValueError(
                f"FabBundle cannot be created with failing gates: {failures}. "
                f"Resolve failures, then construct."
            )
        return self
