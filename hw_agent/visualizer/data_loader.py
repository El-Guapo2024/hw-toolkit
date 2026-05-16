"""Load agent-written data files for the visualizer.

Conventions:
  hw_agent/.state.json                                 -- global state (project, stage)
  hw_agent/.live/data/<project>/<subsystem>.json       -- parts-finder dump (candidates)
  hw_agent/.live/data/<project>/<subsystem>_actuals.json -- parts-specker dump
  docs/projects/<project>/profile.md                   -- spec doc
  docs/projects/<project>/render/datasheet/<...>.pdf   -- cached datasheets
"""

from __future__ import annotations

import json
from pathlib import Path


def find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    while cur != cur.parent:
        if (cur / ".git").is_dir():
            return cur
        cur = cur.parent
    return start.resolve()


REPO = find_repo_root(Path(__file__).parent)
LIVE_DIR = REPO / "hw_agent" / ".live"
DATA_DIR = LIVE_DIR / "data"
STATE_FILE = REPO / "hw_agent" / ".state.json"


def load_state() -> dict:
    if not STATE_FILE.is_file():
        return {"project": "(none)", "stage": "(idle)", "doctrine": []}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"project": "(error)", "stage": "(error)", "doctrine": []}


def project_dir(project: str) -> Path:
    return REPO / "docs" / "projects" / project


def project_profile_md(project: str) -> str | None:
    p = project_dir(project) / "profile.md"
    return p.read_text() if p.is_file() else None


def list_subsystems(project: str) -> list[str]:
    """Return subsystems with agent-written data (candidates or actuals)."""
    pdir = DATA_DIR / project
    if not pdir.is_dir():
        return []
    seen: set[str] = set()
    for f in pdir.glob("*.json"):
        name = f.stem
        if name.endswith("_actuals"):
            name = name[: -len("_actuals")]
        seen.add(name)
    return sorted(seen)


def load_candidates(project: str, subsystem: str) -> dict | None:
    p = DATA_DIR / project / f"{subsystem}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def load_actuals(project: str, subsystem: str) -> dict | None:
    p = DATA_DIR / project / f"{subsystem}_actuals.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def list_datasheets(project: str) -> list[dict]:
    ddir = project_dir(project) / "render" / "datasheet"
    if not ddir.is_dir():
        return []
    out = []
    for f in sorted(ddir.glob("*.pdf")):
        rel = f.relative_to(REPO)
        out.append({"name": f.stem, "path": f"/static-files/{rel}"})
    return out


def list_projects() -> list[str]:
    pdir = REPO / "docs" / "projects"
    if not pdir.is_dir():
        return []
    return sorted([p.name for p in pdir.iterdir() if p.is_dir()])
