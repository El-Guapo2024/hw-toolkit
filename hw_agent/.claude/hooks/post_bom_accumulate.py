#!/usr/bin/env python3
"""PostToolUse hook: accumulate BOM running total after subsystem_choose_part.

Reads tool_input (price + qty_per_board), adds line cost to state.bom_running_total_usd
so the next subsystem_choose_part call sees the cumulative total instead of $0.

Only mutates state if the tool call succeeded (no tool_response error).
Never blocks (PostToolUse runs after the tool already ran).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    while cur != cur.parent:
        # Worktrees have .git as a file pointing to the main .git; accept either.
        gitp = cur / ".git"
        if gitp.is_dir() or gitp.is_file():
            return cur
        cur = cur.parent
    return start.resolve()


def normalize_tool_name(raw: str) -> str:
    if not raw:
        return ""
    if "__" in raw:
        return raw.split("__")[-1]
    return raw


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    tool_name = normalize_tool_name(payload.get("tool_name", ""))
    if tool_name != "subsystem_choose_part":
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    tool_response = payload.get("tool_response", {}) or {}

    if isinstance(tool_response, dict) and tool_response.get("isError"):
        return 0

    try:
        unit = float(tool_input.get("price") or 0.0)
        qty = int(tool_input.get("qty_per_board") or 1)
    except (TypeError, ValueError):
        return 0

    line_cost = unit * qty
    if line_cost <= 0:
        return 0

    cwd = Path(payload.get("cwd", os.getcwd()))
    repo = find_repo_root(cwd)
    state_file = repo / "hw_agent" / ".state.json"
    if not state_file.is_file():
        return 0

    try:
        state = json.loads(state_file.read_text())
    except Exception:
        return 0

    running = float(state.get("bom_running_total_usd") or 0.0)
    state["bom_running_total_usd"] = round(running + line_cost, 4)

    items = state.get("bom_line_items") or []
    items.append({
        "name": tool_input.get("name"),
        "lcsc": tool_input.get("lcsc"),
        "mpn": tool_input.get("mpn"),
        "price": unit,
        "qty_per_board": qty,
        "line_cost": line_cost,
    })
    state["bom_line_items"] = items

    try:
        state_file.write_text(json.dumps(state, indent=2) + "\n")
    except Exception as e:
        print(f"hw-agent harness: failed to write state.json ({e})", file=sys.stderr)
        return 0

    print(
        f"hw-agent harness: bom_running_total_usd = ${state['bom_running_total_usd']:.2f} "
        f"(+${line_cost:.2f} from {tool_input.get('name','?')})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
