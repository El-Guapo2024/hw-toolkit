"""Project state — Pydantic Subsystem persistence.

Each project lives at `docs/projects/<slug>/` and stores its investigation
state as one JSON file per subsystem under `subsystems/`. The chosen part,
BOM, decision history, and current status all live inside those subsystem
JSONs; there is no separate bom.json / decisions.json / design.yaml.

Public API:
  - project_dir / ensure_project (paths)
  - subsystem_save / subsystem_load / subsystem_list / subsystem_delete
  - project_status (aggregator)
"""

from hw_agent.artifacts.project_state.paths import project_dir, ensure_project
from hw_agent.artifacts.project_state.subsystems import (
    subsystem_save,
    subsystem_load,
    subsystem_list,
    subsystem_delete,
    aggregate_project_status,
)

__all__ = [
    "project_dir",
    "ensure_project",
    "subsystem_save",
    "subsystem_load",
    "subsystem_list",
    "subsystem_delete",
    "aggregate_project_status",
]
