#!/usr/bin/env python3
"""PostToolUse hook for designer-mcp's subsystem_choose_part.

Reads hook stdin (Claude Code hook JSON payload), appends a structured entry
to BUILD_LOG.md, and emits a warn line if the call happened outside the
/designer stage (warn-mode -- non-blocking).

Hook stdin schema (from Claude Code):
  {
    "hook_event_name": "PostToolUse",
    "tool_name": "mcp__designer-mcp__subsystem_choose_part",
    "tool_input":  { ...args... },
    "tool_response": { ...result... },
    "session_id": "...",
    "cwd": "...",
    ...
  }
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


def load_state(repo: Path) -> dict:
    state_file = repo / "hw_agent" / ".state.json"
    if not state_file.is_file():
        return {}
    try:
        return json.loads(state_file.read_text())
    except Exception:
        return {}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # malformed input: don't break the flow

    if payload.get("tool_name", "").split("__")[-1] != "subsystem_choose_part":
        return 0  # not our tool

    repo = find_repo_root(Path(payload.get("cwd", os.getcwd())))
    state = load_state(repo)
    stage = state.get("stage", "-")
    project = state.get("project", "-")

    tool_input = payload.get("tool_input", {}) or {}
    tool_resp = payload.get("tool_response", {}) or {}

    subsystem = tool_input.get("subsystem") or tool_input.get("name") or "?"
    mpn = tool_input.get("mpn") or tool_input.get("part_number") or "?"
    vendor_part = tool_input.get("vendor_part") or tool_input.get("dk_part") or tool_input.get("jlc_part") or ""
    stock = tool_input.get("stock") or tool_resp.get("stock") or ""
    price = tool_input.get("price_usd") or tool_input.get("price") or ""

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")

    line = (
        f"- [{ts}] [{project}/{stage}] **{subsystem}** -> {mpn}"
        + (f" (vendor={vendor_part})" if vendor_part else "")
        + (f" stock={stock}" if stock else "")
        + (f" price=${price}" if price else "")
    )

    log_file = repo / "BUILD_LOG.md"
    section_header = "## Picks (auto-logged by post_subsystem_pick.py)"
    existing = log_file.read_text() if log_file.is_file() else "# Build Log\n\n"
    if section_header not in existing:
        existing += f"\n{section_header}\n\n"
    new_content = existing + line + "\n"
    log_file.write_text(new_content)

    if stage != "designer":
        # Warn mode: emit caveat to hook output (visible to model)
        print(
            f"WARN: subsystem_choose_part fired in stage={stage} (expected: designer). "
            "Logged anyway. Per doctrine, /spec captures requirements only; MPN picks belong in /designer.",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
