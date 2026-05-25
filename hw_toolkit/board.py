"""`Board` — the single object an engineer works with in a notebook.

Replaces the old agent-pipeline. No JSON intermediate, no MCP. The
notebook itself holds project state in code: declare a Board, `.add()`
subsystems + interfaces, then ask for derived artifacts (schematic
plan, placement plan, BOM, route). Re-running the notebook recreates
state from scratch — code is the source of truth.

Typical flow:

    >>> import hw_toolkit as hw
    >>> from hw_agent.core import SubsystemPick, Interface
    >>>
    >>> board = hw.Board("control_hub_v1")
    >>> board.add(SubsystemPick(id="buck_3v3", category="buck_converter",
    ...                         mpn="TPS54331DR", package="SOIC-8"))
    >>> board.add(SubsystemPick(id="mcu", category="mcu_module",
    ...                         mpn="ESP32-S3-WROOM-1-N16R8"))
    >>> board.add(Interface(id="rail_3v3", type="power",
    ...                     from_subsystem="buck_3v3", from_port="VOUT",
    ...                     to_subsystem="mcu", to_port="VDD",
    ...                     voltage_nominal_v=3.3))
    >>> board                          # _repr_html_ → table
    >>> board.bundle                   # computed pydantic view (validates)
    >>> board.write_schematic()        # → SchematicPlan
    >>> board.place()                  # → PlacementPlan

Pydantic validation happens lazily on `board.bundle` access — typos in
field values raise then, not at `.add()`. Construct the model directly
(`SubsystemPick(...)`) to get eager validation on the row.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Union

from hw_agent.agents.pcb_designer.placement import (
    PlacementPlan,
    plan_placement,
    zone_assignments,
)
from hw_agent.agents.pcb_designer.schematic import (
    SchematicPlan,
    parse_erc_report,
    plan_schematic,
    write_blank_schematic,
)
from hw_agent.core import Interface, ResearchBundle, SubsystemPick

from hw_toolkit.exceptions import (
    BundleValidationError,
    ERCViolation,
    MultipleERCViolations,
)

DEFAULT_PROJECTS_ROOT = Path("docs/projects")

Addable = Union[SubsystemPick, Interface]


class Board:
    """A board project — built up in a notebook by `.add()`-ing items.

    State lives in the Board instance. No disk reads, no JSON. The
    notebook's code IS the project definition; re-running the notebook
    rebuilds the Board.

    Settings (`build_qty`, `assembly`, `vendor`) are constructor args
    with sensible defaults; override per project.
    """

    def __init__(
        self,
        project_id: str,
        *,
        build_qty: int = 1,
        assembly: str = "hand_solder",
        vendor: str = "jlcpcb",
        projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
    ) -> None:
        self.project_id = project_id
        self.project_dir = Path(projects_root) / project_id
        self._build_qty = build_qty
        self._assembly = assembly
        self._vendor = vendor
        self._subsystems: list[SubsystemPick] = []
        self._interfaces: list[Interface] = []

    # ----------------------------------------------------------- builder API
    def add(self, item: Addable) -> "Board":
        """Append a SubsystemPick or Interface to this board. Returns
        self for chaining: `board.add(buck).add(mcu).add(rail_3v3)`."""
        if isinstance(item, SubsystemPick):
            self._subsystems.append(item)
        elif isinstance(item, Interface):
            self._interfaces.append(item)
        else:
            raise TypeError(
                f"Board.add() takes SubsystemPick or Interface, "
                f"not {type(item).__name__}"
            )
        return self

    # ----------------------------------------------------- pydantic view
    @property
    def bundle(self) -> ResearchBundle:
        """Snapshot the live board state as a pydantic-validated
        ResearchBundle. Raises `BundleValidationError` on any
        cross-field problem (duplicate ids, dangling iface refs).
        Computed on every access — cheap; no caching."""
        try:
            return ResearchBundle(
                schema_version=1,
                project_id=self.project_id,
                subsystems=list(self._subsystems),
                interfaces=list(self._interfaces),
                build_qty=self._build_qty,
                assembly=self._assembly,  # type: ignore[arg-type]
                vendor=self._vendor,  # type: ignore[arg-type]
                research_baseline_git_tag=f"{self.project_id}/live",
                locked_at=datetime.now(timezone.utc),
            )
        except Exception as e:
            errors = getattr(e, "errors", None)
            err_list = (
                tuple(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
                      for err in e.errors())  # pydantic ValidationError
                if callable(errors) else (str(e),)
            )
            raise BundleValidationError(
                path=Path(f"<live:{self.project_id}>"),
                errors=err_list,
            ) from e

    # ------------------------------------------------------------- paths
    @property
    def kicad_dir(self) -> Path:
        return self.project_dir / "kicad"

    @property
    def sch_path(self) -> Path:
        return self.kicad_dir / f"{self.project_id}.kicad_sch"

    @property
    def pcb_path(self) -> Path:
        return self.kicad_dir / f"{self.project_id}.kicad_pcb"

    @property
    def fab_dir(self) -> Path:
        return self.project_dir / "fab"

    # ------------------------------------------------------ active schematic
    @property
    def schematic(self) -> "Schematic":
        """Active-object view of this board's schematic. Cached per
        Board instance — same call returns the same object so
        `.move()` overrides accumulate."""
        if not hasattr(self, "_schematic"):
            from hw_toolkit.schematic import Schematic
            self._schematic = Schematic(board=self)
        return self._schematic

    # ------------------------------------------------------------ schematic
    def write_schematic(self, *, overwrite: bool = False) -> SchematicPlan:
        """Write the .kicad_sch stub from current live state + return
        the op plan to populate it. Dispatch ops via designer-mcp
        add_* tools.
        """
        write_blank_schematic(self.sch_path, overwrite=overwrite)
        return plan_schematic(self.bundle, self.sch_path)

    def check_erc(
        self,
        report_path: str | Path | None = None,
        *,
        expected_codes: tuple[str, ...] = (),
    ) -> None:
        """Parse a KiCad ERC JSON report. Raise `MultipleERCViolations`
        if any real violations remain.
        """
        path = Path(report_path) if report_path else (self.kicad_dir / "erc.json")
        erc = parse_erc_report(path, expected_codes=expected_codes)
        if erc.clean:
            return
        raise MultipleERCViolations(
            report_path=erc.report_path,
            violations=tuple(
                ERCViolation(
                    type=v.type,
                    severity=v.severity,
                    description=v.description,
                    refs=v.items,
                )
                for v in erc.real_violations
            ),
            expected=tuple(
                ERCViolation(
                    type=v.type,
                    severity=v.severity,
                    description=v.description,
                    refs=v.items,
                )
                for v in erc.expected_violations
            ),
        )

    # ------------------------------------------------------------- placement
    def place(self) -> PlacementPlan:
        """Move plan from live state. Dispatch ops via live-edit-mcp."""
        return plan_placement(self.bundle, self.sch_path)

    def zones(self) -> dict[str, str]:
        return dict(zone_assignments(self.bundle))

    # ------------------------------------------------------------------ repr
    def __repr__(self) -> str:
        return (
            f"Board(project_id={self.project_id!r}, "
            f"subsystems={len(self._subsystems)}, "
            f"interfaces={len(self._interfaces)})"
        )

    def _repr_html_(self) -> str:
        try:
            return self.bundle._repr_html_()
        except BundleValidationError as e:
            errs = "".join(f"<li>{html_escape(err)}</li>" for err in e.errors)
            return (
                f"<h4>Board <code>{self.project_id}</code> "
                "<span style='color:#c00'>(invalid)</span></h4>"
                f"<ul>{errs}</ul>"
            )


def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
