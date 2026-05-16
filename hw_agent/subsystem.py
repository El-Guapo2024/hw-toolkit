"""Base class for electronic subsystems.

Each component type subclasses `ElectronicSubsystem` to declare:
  - Type metadata (category, description, prior-art searches, AI hints) as ClassVars
  - The shape of the engineer's requirements (`Requirements: ClassVar[type[BaseModel]]`)
  - The shape of extracted datasheet specs (`Actuals: ClassVar[type[BaseModel]]`)
  - The check pipeline (`checks: ClassVar[list[Check]]`)
  - The calculations to run (`calculations: ClassVar[list]`)

Instances of a subclass represent a specific subsystem in a project — with state
that fills in over time as the agent gathers requirements, extracts datasheet
specs, and chooses a part.

Example:
    buck = BuckSubsystem(
        name="buck_5v_main",
        requirements=BuckRequirements(vin=7.4, vout=5.0, iout=5.0),
    )
    print(buck.status())                  # all MISSING — no actuals yet
    buck = buck.with_actuals(iout_max=8.0, vin_min=4.5, vin_max=36.0, theta_ja=40.0)
    print(buck.status())                  # most PASS now

Persistence:
    json = buck.model_dump_json(indent=2)
    later = BuckSubsystem.model_validate_json(json)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar, Optional

from pydantic import BaseModel, ConfigDict, Field

from hw_agent.checks import Check, CheckResult, normalize_check_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ChosenPart(BaseModel):
    """The part the engineer / agent committed to for this subsystem.

    Holds the commercial/sourcing details so the BOM is fully derivable from
    `[s.chosen_part for s in subsystems if s.chosen_part]` — no separate BOM
    file. `datasheet_url` makes the datasheet link travel with the choice.
    """
    lcsc: str
    mpn: str
    manufacturer: str = ""
    description: str = ""
    package: str = ""
    price: float = 0.0
    price_tiers: dict[str, float] = Field(default_factory=dict)
    """Qty-tiered pricing, e.g. {'1': 0.65, '10': 0.50, '100': 0.30}."""
    stock: int = 0
    datasheet_url: str = ""
    library_type: str = "extended"
    """JLCPCB library type: 'basic' (no setup fee) | 'preferred' | 'extended'."""
    qty_per_board: int = 1
    notes: str = ""


class ExaminedCandidate(BaseModel):
    """A candidate part examined during research — kept for transparency.

    Distinct from `decisions[].rejected` (post-hoc summary list). This list
    accumulates EVERY part that went through `analyze_candidate`, win or lose,
    so the engineer can audit the full search journey: what we considered,
    which checks passed/failed for each, and what the analytical math said.
    Append-only; newest at the end.
    """
    timestamp: str = Field(default_factory=_now)
    lcsc: str
    mpn: str = ""
    manufacturer: str = ""
    package: str = ""
    price: float = 0.0
    stock: int = 0
    library_type: str = ""
    actuals: dict = Field(default_factory=dict)
    """The merged actuals used for verification (auto-filled from JLC + agent overrides)."""
    verdict: str = ""
    """`READY` (all hard checks pass) | `BLOCKED` (one or more hard fail) | `INCOMPLETE` (missing data)."""
    checks: list[dict] = Field(default_factory=list)
    """Serialized `CheckResult` per check — name, severity, status, actual, required, note."""
    calculations: dict = Field(default_factory=dict)
    """Output of the category's analytical calculators (Tj, Pdiss, ripple, divider math, etc.)."""
    notes: str = ""


class Decision(BaseModel):
    """A choice made about this subsystem — append-only history.

    Lives inside the subsystem's `decisions` list. Captures rationale,
    rejected alternatives, and tradeoffs so future-you (or another agent) can
    audit why this part was picked.
    """
    timestamp: str = Field(default_factory=_now)
    chosen: ChosenPart
    rejected: list[dict] = Field(default_factory=list)
    """[{lcsc, mpn, reason}] — alternatives considered and ruled out."""
    rationale: str = ""
    tradeoffs: list[str] = Field(default_factory=list)
    """Free-form prose: known downsides accepted with this pick."""
    accepted_warnings: list[str] = Field(default_factory=list)
    """Machine-readable list of soft-check names explicitly acknowledged for this pick.
    Each entry must match a `CheckResult.name` from the subsystem's check pipeline
    (e.g. "Package suitable", "Stock threshold"). Used to clear soft fails so the
    agent isn't pestered about the same warning on every revisit. Free-form rationale
    for *why* the warning is accepted goes in `tradeoffs`."""
    alternate_lcsc: str = ""
    """Backup LCSC if chosen part goes out of stock."""
    requirements_snapshot: dict = Field(default_factory=dict)
    """Snapshot of requirements at decision time, for audit if requirements change later."""


class SubsystemStatus(BaseModel):
    """Snapshot of one subsystem's checks at a point in time."""
    name: str
    category: str
    ready: bool                                 # all hard checks pass
    checks: list[CheckResult]
    passed: list[CheckResult] = Field(default_factory=list)
    failed: list[CheckResult] = Field(default_factory=list)
    missing: list[CheckResult] = Field(default_factory=list)
    accepted: list[CheckResult] = Field(default_factory=list)
    """Soft fails the engineer explicitly acknowledged via `accepted_warnings`
    on a Decision. Don't block READY and aren't reported as FAIL."""

    def summary(self) -> str:
        """One-line markdown summary."""
        parts = []
        if self.passed:
            parts.append(f"{len(self.passed)} PASS")
        if self.failed:
            parts.append(f"**{len(self.failed)} FAIL**")
        if self.accepted:
            parts.append(f"{len(self.accepted)} ACK")
        if self.missing:
            parts.append(f"{len(self.missing)} MISSING")
        verdict = "READY" if self.ready else "BLOCKED" if self.failed else "IN PROGRESS"
        return f"`{self.name}` ({self.category}): {verdict} — {', '.join(parts)}"


class ProjectStatus(BaseModel):
    """Aggregated status across all subsystems in a project."""
    project: str
    subsystems: list[SubsystemStatus]
    ready_count: int = 0
    total_count: int = 0
    data_needed: list[CheckResult] = Field(default_factory=list)
    blocking_failures: list[CheckResult] = Field(default_factory=list)
    """Hard-severity fails only — checks that prevent a subsystem from being READY.
    Soft fails (warnings) are visible per-subsystem but don't aggregate here."""
    soft_warnings: list[CheckResult] = Field(default_factory=list)
    """Soft-severity fails NOT yet acknowledged — these are the ones still needing engineer attention."""
    accepted_warnings: list[CheckResult] = Field(default_factory=list)
    """Soft fails explicitly accepted via `accepted_warnings` on a Decision."""

    @classmethod
    def aggregate(cls, project: str, subsystems: list[SubsystemStatus]) -> "ProjectStatus":
        return cls(
            project=project,
            subsystems=subsystems,
            ready_count=sum(1 for s in subsystems if s.ready),
            total_count=len(subsystems),
            data_needed=[r for s in subsystems for r in s.missing],
            blocking_failures=[r for s in subsystems for r in s.failed if r.severity == "hard"],
            soft_warnings=[r for s in subsystems for r in s.failed if r.severity == "soft"],
            accepted_warnings=[r for s in subsystems for r in s.accepted],
        )


class ElectronicSubsystem(BaseModel):
    """Base for all electronic subsystem types — buck, ldo, mcu, motor driver, etc.

    Subclasses MUST declare these ClassVars:
      - category:        str, machine key like "buck_converter"
      - description:     str, what this component does
      - Requirements:    type[BaseModel], the questionnaire fields (engineer answers)
      - Actuals:         type[BaseModel], extracted spec fields (Optional, fill over time)
      - checks:          list of Check functions to run
      - calculations:    list of calculation functions (optional, may be empty)

    Subclasses SHOULD declare:
      - ai_instructions:     str        — neutral engineering guidance for the agent
      - page_hints:          dict[str, list[int]]  — where to look in datasheets
      - searches:            list[SearchCriteria]  — JLC search templates
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    # ── Type-level metadata (override in subclass) ────────────────────────
    category: ClassVar[str] = ""
    description: ClassVar[str] = ""
    ai_instructions: ClassVar[str] = ""
    """Free-form engineering guidance (neutral — no brand recommendations)."""

    Requirements: ClassVar[type[BaseModel]] = BaseModel  # type: ignore[assignment]
    Actuals: ClassVar[type[BaseModel]] = BaseModel       # type: ignore[assignment]

    # Pipeline
    checks: ClassVar[list[Check]] = []
    calculations: ClassVar[list] = []

    # ── Instance state ────────────────────────────────────────────────────
    name: str
    requirements: BaseModel
    actuals: BaseModel = Field(default=None)  # type: ignore[assignment]  — populated in __init__
    chosen_part: Optional[ChosenPart] = None
    decisions: list[Decision] = Field(default_factory=list)
    """Append-only history of part-choice decisions. Newest at the end."""
    candidates_examined: list[ExaminedCandidate] = Field(default_factory=list)
    """Append-only log of every part examined via `analyze_candidate`. Distinct from
    `decisions[].rejected` (post-hoc reasons): this captures the verify table +
    calculator outputs at the moment we looked at each candidate, win or lose."""

    def __init__(self, **data: Any) -> None:
        # Default `actuals` to an empty instance of the subclass's Actuals model
        if "actuals" not in data or data["actuals"] is None:
            data["actuals"] = self.__class__.Actuals()
        super().__init__(**data)

    # ── Status / verification ──────────────────────────────────────────────

    def status(self) -> SubsystemStatus:
        """Run the full check pipeline against current state. Doesn't break on missing.

        After running checks, applies `accepted_warnings` from the most recent
        Decision (if any) — re-classifies soft fails the engineer has explicitly
        acknowledged from "fail" to "accepted" so the table doesn't keep
        screaming FAIL after they've been signed off.
        """
        actual_dict = self.actuals.model_dump(exclude_none=False)
        required_dict = self.requirements.model_dump()
        results = [check(actual_dict, required_dict) for check in self.__class__.checks]

        acked: set[str] = set()
        if self.decisions:
            acked = {normalize_check_id(a) for a in self.decisions[-1].accepted_warnings}
        if acked:
            for r in results:
                if (r.severity == "soft" and r.status == "fail"
                        and normalize_check_id(r.name) in acked):
                    r.status = "accepted"
                    suffix = "accepted by engineer"
                    r.note = f"{r.note} — {suffix}" if r.note else suffix

        return SubsystemStatus(
            name=self.name,
            category=self.__class__.category,
            ready=all(r.passed for r in results if r.severity == "hard"),
            checks=results,
            passed=[r for r in results if r.status == "pass"],
            failed=[r for r in results if r.status == "fail"],
            missing=[r for r in results if r.status == "missing"],
            accepted=[r for r in results if r.status == "accepted"],
        )

    def run_calculations(self) -> dict:
        """Run the template's calculations against current state. Returns merged dict."""
        actual_dict = self.actuals.model_dump(exclude_none=False)
        required_dict = self.requirements.model_dump()
        results: dict = {}
        for calc in self.__class__.calculations:
            results.update(calc(actual_dict, required_dict))
        return results

    # ── Mutations (immutable-style, returns new instance) ─────────────────

    def with_actuals(self, **kwargs) -> "ElectronicSubsystem":
        """Return a new subsystem with extra actuals merged in.

        Re-validates the merged dict through the subclass's Actuals model so
        type errors (e.g. `iout_max='300mA'`) raise immediately rather than
        being silently persisted and blowing up on a later read.
        Unknown field names also raise — we don't accept typos.
        """
        Actuals = self.__class__.Actuals
        unknown = set(kwargs) - set(Actuals.model_fields)
        if unknown:
            valid = sorted(Actuals.model_fields)
            raise ValueError(
                f"Unknown actuals field(s): {sorted(unknown)}. "
                f"Valid keys for {self.__class__.__name__}.Actuals: {valid}"
            )
        merged = {**self.actuals.model_dump(), **kwargs}
        new_actuals = Actuals.model_validate(merged)
        return self.model_copy(update={"actuals": new_actuals})

    def with_requirements(self, **kwargs) -> "ElectronicSubsystem":
        """Return a new subsystem with extra requirements merged in (rare — usually set once).

        Same validation discipline as `with_actuals`: type errors raise, unknown
        keys raise.
        """
        Reqs = self.__class__.Requirements
        unknown = set(kwargs) - set(Reqs.model_fields)
        if unknown:
            valid = sorted(Reqs.model_fields)
            raise ValueError(
                f"Unknown requirements field(s): {sorted(unknown)}. "
                f"Valid keys for {self.__class__.__name__}.Requirements: {valid}"
            )
        merged = {**self.requirements.model_dump(), **kwargs}
        new_reqs = Reqs.model_validate(merged)
        return self.model_copy(update={"requirements": new_reqs})

    def with_chosen_part(self, chosen: ChosenPart, decision: Optional[Decision] = None) -> "ElectronicSubsystem":
        """Return a new subsystem with the chosen part set and (optionally) a decision appended."""
        update = {"chosen_part": chosen}
        if decision is not None:
            update["decisions"] = self.decisions + [decision]
        return self.model_copy(update=update)
