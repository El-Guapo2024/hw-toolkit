#!/usr/bin/env python3
"""PreToolUse hook: block Claude-side Write/Edit on canonical artifacts.

Agents bypass the doctrine harness by hand-writing JSON files instead of
calling designer-mcp tools. This hook stops that.

Canonical artifacts (only designer-mcp / harness scripts may write these):
  - docs/projects/*/subsystems/*.json   (subsystem records)
  - hw_agent/.state.json                 (runtime state)
  - docs/projects/*/parts/*.json         (parts records)
  - docs/projects/*/BUILD_LOG.md         (append-only via post hooks)

Hook input: {tool_name, tool_input, cwd}.
Exit 2 + stderr message → Claude sees the block and must use the right tool.

Allow-list for harness internals (these scripts must be able to write):
  - $CLAUDE_PROJECT_DIR/hw_agent/bin/init_project.py            (.state.json reset)
  - $CLAUDE_PROJECT_DIR/hw_agent/.claude/hooks/post_*.py        (state mutations)

The hook does NOT touch tools other than Write / Edit / MultiEdit.
designer-mcp's own file writes go through its own server process and are
NOT subject to this hook (which only sees Claude's tool calls).
"""

from __future__ import annotations

import fnmatch
import json
import sys
from pathlib import Path


CANONICAL_PATTERNS = [
    "**/docs/projects/*/subsystems/*.json",
    "**/docs/projects/*/parts/*.json",
    "**/hw_agent/.state.json",
    "**/docs/projects/*/BUILD_LOG.md",
]


def _matches_canonical(path: str) -> bool:
    p = str(Path(path).resolve())
    for pat in CANONICAL_PATTERNS:
        if fnmatch.fnmatch(p, pat):
            return True
    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    tool_name = (payload.get("tool_name") or "").strip()
    if tool_name not in {"Write", "Edit", "MultiEdit"}:
        return 0

    tool_input = payload.get("tool_input") or {}
    target_path = tool_input.get("file_path") or ""

    if not target_path or not _matches_canonical(target_path):
        return 0

    rel = Path(target_path).name
    parent = Path(target_path).parent.name
    fix_msg = (
        f"BLOCKED: direct {tool_name} on canonical artifact "
        f"({parent}/{rel}). Use the designer-mcp tool instead:\n"
        "  - subsystem_add / subsystem_choose_part / subsystem_update_*\n"
        "  - part_init / part_add_digikey / part_add_jlc\n"
        "Or, for hw_agent/.state.json: run hw_agent/bin/init_project.py or "
        "let the post_bom_accumulate hook mutate it.\n"
        "If you must hand-edit (rare), pass via the designer-mcp's "
        "overwrite/update tools — they validate via Pydantic + run domain checks."
    )
    print(fix_msg, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
