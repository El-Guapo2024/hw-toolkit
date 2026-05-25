"""Typed exceptions — the feedback channel from library to Claude.

The library never returns markdown error strings. It raises a typed
exception with structured attrs (refdes, sheet, expected_codes,
pydantic_errors). Claude reads `exc.__class__.__name__` + attrs and
branches without string parsing. Pytest catches the same exceptions
for unit tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


class HwToolkitError(Exception):
    """Base for every typed exception raised by the library."""


# ---------------------------------------------------------------------------
# Load-time failures
# ---------------------------------------------------------------------------


@dataclass
class BundleValidationError(HwToolkitError):
    """`research_bundle.json` failed pydantic validation.

    `errors` is the flattened pydantic error list — one string per
    field error, formatted as "loc: msg". Caller can iterate or count.
    """
    path: Path
    errors: tuple[str, ...] = ()

    def __str__(self) -> str:
        head = f"BundleValidationError at {self.path}: {len(self.errors)} error(s)"
        body = "\n".join(f"  - {e}" for e in self.errors)
        return f"{head}\n{body}" if self.errors else head


# ---------------------------------------------------------------------------
# Schematic-build failures
# ---------------------------------------------------------------------------


@dataclass
class ERCViolation(HwToolkitError):
    """KiCad ERC found one violation. `Board.write_schematic` raises a
    `MultipleERCViolations` aggregating these when the report isn't clean.
    """
    type: str
    severity: str
    description: str
    refs: tuple[str, ...] = ()
    sheet: str = ""

    def __str__(self) -> str:
        ref_str = f" [{','.join(self.refs)}]" if self.refs else ""
        return f"ERC {self.severity} {self.type}{ref_str}: {self.description}"


@dataclass
class MultipleERCViolations(HwToolkitError):
    """ERC report has ≥1 real violation. `violations` is the full list;
    `expected` is the subset filtered by the project's expected_codes."""
    report_path: Path
    violations: tuple[ERCViolation, ...]
    expected: tuple[ERCViolation, ...] = ()

    def __str__(self) -> str:
        return (
            f"MultipleERCViolations: {len(self.violations)} real, "
            f"{len(self.expected)} expected — see {self.report_path}"
        )


@dataclass
class FootprintMissingError(HwToolkitError):
    """A SubsystemPick.package didn't resolve to a KiCad footprint."""
    refdes: str
    mpn: str
    package: str

    def __str__(self) -> str:
        return f"FootprintMissingError: {self.refdes} ({self.mpn}) — package={self.package!r}"


# ---------------------------------------------------------------------------
# Routing / DRC / fab failures (Phase 4 land — stubs for now)
# ---------------------------------------------------------------------------


@dataclass
class RoutingFailedError(HwToolkitError):
    engine: str
    unrouted_nets: tuple[str, ...] = ()

    def __str__(self) -> str:
        return f"RoutingFailedError({self.engine}): {len(self.unrouted_nets)} net(s) unrouted"


@dataclass
class DRCViolation(HwToolkitError):
    type: str
    severity: str
    description: str
    refs: tuple[str, ...] = ()


@dataclass
class MultipleDRCViolations(HwToolkitError):
    report_path: Path
    violations: tuple[DRCViolation, ...]


@dataclass
class EvalReport:
    """Result of `Board.verify()`. Empty `fails` == pass. Not an exception
    — verify() returns this rather than raising, because the engineer
    usually wants to inspect the full list, not catch one and stop.
    """
    fails: list[HwToolkitError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.fails

    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self) -> str:
        return f"EvalReport(ok={self.ok}, fails={len(self.fails)})"
