"""Reusable verification checks for component candidates.

Each check is a pure function `(actual, required) -> CheckResult`. Subsystems
list the checks they care about; `Subsystem.status()` runs the pipeline and
aggregates pass/fail/missing.

Adding a new check:
    1. Pick the right module (electrical, thermal, mechanical, supply, …).
    2. Write a function with this signature:
           def my_check(actual: dict, required: dict) -> CheckResult: ...
    3. If required input data is missing from `actual`, return a CheckResult
       with status="missing" and `missing_specs=[...]` so the agent knows
       what to extract next.
    4. Import it into a Subsystem class's `checks: ClassVar[list[Check]] = [...]`.

Status semantics:
    pass    — the requirement was satisfied
    fail    — the requirement was definitively not met
    missing — required input data wasn't in `actual`; agent should extract it
"""

from __future__ import annotations

from typing import Callable

from pydantic import BaseModel, Field


class CheckResult(BaseModel):
    name: str
    severity: str                       # "hard" | "soft"
    status: str                         # "pass" | "fail" | "missing" | "accepted"
    actual: str                         # human-readable
    required: str                       # human-readable
    missing_specs: list[str] = Field(default_factory=list)
    note: str = ""

    @property
    def passed(self) -> bool:
        """True if the check is non-blocking — either it passed or the engineer
        explicitly accepted the (soft) warning."""
        return self.status in ("pass", "accepted")


def normalize_check_id(s: str) -> str:
    """Canonicalize a check id for matching `accepted_warnings` entries against
    `CheckResult.name`. Both `"Stock threshold"` and `"stock_threshold"` collapse
    to the same key."""
    return s.strip().lower().replace(" ", "_").replace("-", "_")


# Type alias for a check function. Subsystems list these in `checks: ClassVar = [...]`.
Check = Callable[[dict, dict], CheckResult]


def _missing(name: str, severity: str, missing: list[str], required_str: str = "") -> CheckResult:
    """Helper: build a 'missing data' result with a useful note for the agent."""
    return CheckResult(
        name=name,
        severity=severity,
        status="missing",
        actual="(unknown)",
        required=required_str,
        missing_specs=missing,
        note=f"Need {missing} from datasheet to evaluate this check",
    )


def _missing_keys(d: dict, keys: list[str]) -> list[str]:
    """Return keys that are absent OR present-but-None. Treats None as missing
    so checks work uniformly against plain dicts and Pydantic Actuals models."""
    return [k for k in keys if d.get(k) is None]
