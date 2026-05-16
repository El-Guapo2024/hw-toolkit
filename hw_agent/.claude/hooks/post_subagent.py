#!/usr/bin/env python3
"""PostToolUse hook -- updates .live/dashboard.md after Task tool invocations.

Records which sub-agent was just run, with timestamp + subject. Gives engineer
top-level activity log in VS Code without having to scrub every per-agent
live pane.

Reads hook stdin payload. Operates on:
  tool_name == "Task" (Claude Code's sub-agent launch tool)
  tool_input.subagent_type and tool_input.description
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    while cur != cur.parent:
        if (cur / ".git").is_dir():
            return cur
        cur = cur.parent
    return start.resolve()


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if payload.get("tool_name") != "Task":
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    subagent = tool_input.get("subagent_type", "general-purpose")
    description = tool_input.get("description", "(no description)")

    repo = find_repo_root(Path(payload.get("cwd", os.getcwd())))
    dashboard = repo / "hw_agent" / ".live" / "dashboard.md"

    if not dashboard.is_file():
        return 0

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    entry = f"\n- `[{ts}]` **{subagent}** — {description}"

    text = dashboard.read_text()
    marker = "## Recent activity"
    if marker in text:
        # Insert immediately after the marker line.
        head, tail = text.split(marker, 1)
        # Strip leading newline after marker if present so we don't double up.
        tail_lines = tail.split("\n", 1)
        rest = tail_lines[1] if len(tail_lines) > 1 else ""
        new_text = f"{head}{marker}{entry}\n{rest}"
        dashboard.write_text(new_text)
    else:
        dashboard.write_text(text + f"\n\n## Recent activity{entry}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
