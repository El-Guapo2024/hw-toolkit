"""Subsystem persistence — one JSON file per subsystem per project.

Layout:
    docs/projects/<project>/subsystems/<name>.json
        {
          "category": "ldo",
          "name": "ldo_3v3_main",
          "requirements": { ... },
          "actuals": { ... },
          "chosen_part": null | { ... }
        }

The `category` field tells the loader which Pydantic class to dispatch to
(see SUBSYSTEM_REGISTRY).
"""

from __future__ import annotations

import json
from pathlib import Path

from hw_agent.project_state.paths import ensure_project, project_dir
from hw_agent.subsystem import ElectronicSubsystem, ProjectStatus, SubsystemStatus
from hw_agent.templates import SUBSYSTEM_REGISTRY


def _subsystems_dir(project: str) -> Path:
    d = ensure_project(project) / "subsystems"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _subsystem_path(project: str, name: str) -> Path:
    return _subsystems_dir(project) / f"{name}.json"


# ─── Persistence ────────────────────────────────────────────────────────────

def subsystem_save(subsystem: ElectronicSubsystem, project: str) -> Path:
    """Persist a subsystem to disk. Always includes category for dispatch on reload."""
    path = _subsystem_path(project, subsystem.name)
    data = json.loads(subsystem.model_dump_json())
    data["category"] = subsystem.__class__.category
    path.write_text(json.dumps(data, indent=2))
    return path


def subsystem_load(project: str, name: str) -> ElectronicSubsystem | None:
    """Load a subsystem by name. Returns None if not found."""
    path = _subsystem_path(project, name)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    category = data.pop("category", None)
    if category is None or category not in SUBSYSTEM_REGISTRY:
        raise ValueError(
            f"Subsystem `{name}` has unknown category `{category}`. "
            f"Available: {list(SUBSYSTEM_REGISTRY.keys())}"
        )
    cls = SUBSYSTEM_REGISTRY[category]
    return cls.model_validate(data)


def subsystem_delete(project: str, name: str) -> bool:
    """Delete a subsystem file. Returns True if it existed."""
    path = _subsystem_path(project, name)
    if path.exists():
        path.unlink()
        return True
    return False


def subsystem_list(project: str) -> list[str]:
    """List subsystem names (filenames without .json) for a project."""
    sub_dir = project_dir(project) / "subsystems"
    if not sub_dir.exists():
        return []
    return sorted(f.stem for f in sub_dir.glob("*.json"))


# ─── Aggregation ────────────────────────────────────────────────────────────

def aggregate_project_status(project: str) -> ProjectStatus:
    """Load all subsystems for a project, run each one's status pipeline,
    and return the aggregated ProjectStatus.

    Subsystems with malformed JSON are skipped silently — for a lower-level view
    use `subsystem_list` + `subsystem_load` directly to surface load errors.

    (Renamed from `project_status` to avoid shadowing the MCP tool of the same
    name when imported into `mcp_server.py`.)
    """
    statuses: list[SubsystemStatus] = []
    for name in subsystem_list(project):
        try:
            sub = subsystem_load(project, name)
        except Exception:
            continue
        if sub is None:
            continue
        statuses.append(sub.status())
    return ProjectStatus.aggregate(project, statuses)
