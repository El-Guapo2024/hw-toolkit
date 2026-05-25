"""Phase 1 — load + validate a ResearchBundle from disk.

The pcb-designer refuses to start until the bundle parses cleanly.
Any failure produces a structured error pointing at the offending file
and the pydantic message; the agent surfaces it to the user and exits.
It does NOT attempt to fix the bundle — that's the researcher's job.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from hw_agent.core import ResearchBundle


class ResearchBundleLoadError(RuntimeError):
    """Raised when a ResearchBundle file can't be loaded or validated."""

    def __init__(self, path: Path, reason: str, *, cause: Exception | None = None):
        self.path = path
        self.reason = reason
        self.cause = cause
        super().__init__(f"ResearchBundle invalid at {path}: {reason}")


@dataclass(frozen=True)
class LoadResult:
    """Outcome of a load attempt.

    `bundle` is set iff validation passed. `errors` lists pydantic field
    paths + messages so the agent can surface them line-by-line.
    """

    bundle: ResearchBundle | None
    path: Path
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.bundle is not None


def load_research_bundle(path: str | Path) -> LoadResult:
    """Load + validate. Returns LoadResult; never raises for validation
    failures — those go in `errors`. Only raises for I/O / parse problems
    where there's no structured error to report."""
    p = Path(path)
    if not p.exists():
        raise ResearchBundleLoadError(p, "file does not exist")
    if not p.is_file():
        raise ResearchBundleLoadError(p, "path is not a regular file")

    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as e:
        raise ResearchBundleLoadError(p, f"read failed: {e}", cause=e) from e

    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ResearchBundleLoadError(p, f"invalid JSON: {e}", cause=e) from e

    try:
        bundle = ResearchBundle.model_validate(data)
    except ValidationError as e:
        errors = tuple(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
            for err in e.errors()
        )
        return LoadResult(bundle=None, path=p, errors=errors)

    return LoadResult(bundle=bundle, path=p)
