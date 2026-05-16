"""KiBot-inspired ERC violation filter system.

Replaces the hardcoded `EXPECTED_PER_SUBSYSTEM` set with a configurable
`erc-filters.yaml` that lets per-project tuning of which violations should
be bucketed as 'real_issues' vs 'expected' (or reclassified outright).

Inspired by KiBot's `pre_erc.py` filter pattern:
    - Each filter has an `error_type`, optional `regex`, `filter_msg`,
      and `change_to: ignore | warning | error`.
    - First matching filter wins.
    - `change_to: ignore` excludes the violation from real_issues and
      counts it under `expected`.
    - `change_to: warning|error` reclassifies severity.
    - No matching filter → violation passes through as a real issue.

Lookup order (first found wins):
    1. <schem_json_dir>/erc-filters.yaml         (per-subsystem override)
    2. <project_root>/erc-filters.yaml           (project-wide)
    3. Built-in DEFAULT_FILTERS                  (mirrors prior behavior)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


# Built-in defaults — keep parity with the hardcoded EXPECTED_PER_SUBSYSTEM
# behavior so existing projects don't see a regression. Override with an
# erc-filters.yaml file next to the schematic or at the project root.
DEFAULT_FILTERS_YAML = """\
# Default ERC filters — mirror prior EXPECTED_PER_SUBSYSTEM behavior.
# Override per-project by dropping an erc-filters.yaml at the project root.
filters:
  - error_type: pin_not_connected
    filter_msg: "Cross-subsystem signal pin — wired at the system root sheet"
    change_to: ignore
  - error_type: global_label_dangling
    filter_msg: "Cross-subsystem terminal — matched at the root sheet"
    change_to: ignore
  - error_type: label_dangling
    filter_msg: "Internal-only net label (standalone subsystem mode)"
    change_to: ignore
  - error_type: hier_label_mismatch
    filter_msg: "Hierarchical label without parent (standalone subsystem mode)"
    change_to: ignore
  - error_type: lib_symbol_issues
    regex: "hwagent"
    filter_msg: "Project-local hwagent namespace — cosmetic warning"
    change_to: ignore
"""


@dataclass
class Filter:
    error_type: str
    filter_msg: str
    regex: Optional[re.Pattern] = None
    change_to: str = "ignore"  # ignore | warning | error

    def matches(self, viol_type: str, description: str, items_text: str) -> bool:
        if self.error_type and self.error_type != viol_type:
            return False
        if self.regex and not self.regex.search(f"{description}\n{items_text}"):
            return False
        return True


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def _build_filters(raw: dict) -> list[Filter]:
    filters: list[Filter] = []
    for f in raw.get("filters", []):
        et = f.get("error_type", "")
        regex_str = f.get("regex")
        filters.append(Filter(
            error_type=et,
            filter_msg=f.get("filter_msg", "") or et,
            regex=re.compile(regex_str) if regex_str else None,
            change_to=f.get("change_to", "ignore"),
        ))
    return filters


def load_filters(schem_json: Path, project_root: Optional[Path] = None) -> list[Filter]:
    """Resolve filter config in priority order: per-subsystem → project → defaults."""
    candidates = [schem_json.parent / "erc-filters.yaml"]
    if project_root:
        candidates.append(project_root / "erc-filters.yaml")
    for c in candidates:
        if c.exists():
            return _build_filters(_load_yaml(c))
    return _build_filters(yaml.safe_load(DEFAULT_FILTERS_YAML))


def classify(
    erc_json: dict,
    filters: list[Filter],
) -> dict:
    """Apply the filter list to a kicad-cli ERC or DRC report; bucket into
    real_issues / expected, mirroring KiBot's three-bucket output.

    Handles both report shapes:
        ERC: {"sheets": [{"violations": [...]}, ...]}
        DRC: {"violations": [...], "unconnected_items": [...]}

    Returns:
        {
          "total":        int,             # total violations seen
          "by_type":      {type: count},   # counts before filtering
          "real_issues":  [...],           # passed through (or escalated)
          "expected":     {type: count},   # ignored by filters
          "filter_log":   [{type, desc, filter_msg}],  # for auditability
        }
    """
    from collections import Counter

    by_type: Counter[str] = Counter()
    real: list[dict] = []
    expected: Counter[str] = Counter()
    filter_log: list[dict] = []

    # Collect violations from either shape
    all_violations: list[dict] = []
    if "sheets" in erc_json:
        for sheet in erc_json.get("sheets", []):
            all_violations.extend(sheet.get("violations", []))
    else:
        all_violations.extend(erc_json.get("violations", []))
        # DRC also reports unconnected_items as a separate top-level list
        for u in erc_json.get("unconnected_items", []):
            uu = dict(u)
            uu.setdefault("type", "unconnected_items")
            all_violations.append(uu)

    for v in all_violations:
        if True:  # preserve original loop body shape
            t = v.get("type", "?")
            by_type[t] += 1
            desc = v.get("description") or ""
            items = [(it.get("description", "") or "")[:120]
                     for it in v.get("items", [])[:3]]
            items_text = "\n".join(items)

            match = next((f for f in filters if f.matches(t, desc, items_text)), None)
            if match is None:
                # No filter matched → real issue at original severity
                real.append({"type": t, "desc": desc[:120], "items": items})
            elif match.change_to == "ignore":
                expected[t] += 1
                filter_log.append({
                    "type": t,
                    "desc": desc[:80],
                    "filter_msg": match.filter_msg,
                    "excluded_by_filter": True,
                })
            elif match.change_to in ("warning", "error"):
                real.append({
                    "type": t, "desc": desc[:120], "items": items,
                    "severity": match.change_to,
                    "modified_by_filter": True,
                })

    return {
        "total": sum(by_type.values()),
        "by_type": dict(by_type.most_common()),
        "real_issues": real,
        "expected": dict(expected),
        "filter_log": filter_log,
    }
