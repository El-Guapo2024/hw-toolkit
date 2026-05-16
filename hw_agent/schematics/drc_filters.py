"""DRC violation filter system — mirrors erc_filters.py for PCB DRC.

Reuses the generic `classify()` function from erc_filters; this module just
provides the DRC-specific default filter set + lookup paths.

Lookup priority for filter config (first found wins):
    1. <schem_json_dir>/drc-filters.yaml         (per-subsystem override)
    2. <project_root>/drc-filters.yaml           (project-wide)
    3. Built-in DEFAULT_DRC_FILTERS              (sensible defaults below)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from .erc_filters import _build_filters, classify, Filter  # re-export


DEFAULT_DRC_FILTERS_YAML = """\
# Default DRC filters — minimal, conservative.
#
# `copper_edge_clearance` is bucketed as expected by default because the
# placement step often puts components near the board outline; the agent
# can tune layout to fix these but they're rarely real bugs at the
# initial-iteration stage. Override per-project if you want them surfaced.
filters:
  - error_type: copper_edge_clearance
    filter_msg: "Component too close to board edge — adjust placement"
    change_to: ignore

  # Unconnected items (airwires) become DRC errors when the netlist has
  # connections but routing didn't complete them. Useful to see at first;
  # can be filtered if you're iterating before final routing.
  # - error_type: unconnected_items
  #   filter_msg: "Net not yet routed"
  #   change_to: ignore
"""


def load_filters(schem_json: Path,
                 project_root: Optional[Path] = None) -> list[Filter]:
    """Resolve DRC filter config: per-subsystem → project → built-in defaults."""
    candidates = [schem_json.parent / "drc-filters.yaml"]
    if project_root:
        candidates.append(project_root / "drc-filters.yaml")
    for c in candidates:
        if c.exists():
            return _build_filters(yaml.safe_load(c.read_text()) or {})
    return _build_filters(yaml.safe_load(DEFAULT_DRC_FILTERS_YAML))


# `classify(drc_json, filters)` is identical to erc_filters.classify —
# kicad-cli's DRC and ERC outputs share the same JSON shape. Re-exported
# above so callers only need to import this module.
