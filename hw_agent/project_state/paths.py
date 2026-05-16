"""Project path resolution and initialization."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


PROJECTS_ROOT = Path("docs/projects")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def project_dir(project: str) -> Path:
    """Resolve a project slug to its directory (relative to cwd)."""
    return (Path.cwd() / PROJECTS_ROOT / project).resolve()


def ensure_project(project: str) -> Path:
    """Create the project directory structure if it doesn't exist."""
    d = project_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    (d / "components").mkdir(exist_ok=True)
    return d


def _state_path(project: str, filename: str) -> Path:
    return ensure_project(project) / filename


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def _save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=False))
