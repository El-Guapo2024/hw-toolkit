#!/usr/bin/env python3
"""PostToolUse hook -- auto-opens edited files in VS Code so user sees live changes.

Fires after Write or Edit tools. Extracts file_path from tool_input, filters to
hw-toolkit project paths, and runs `code -g <file>:1` (reuse window, focus tab).
VS Code re-reads on external writes when file is already open.

Filters:
- Only opens files under the repo root (skips /tmp, ~/.claude/, etc.)
- Skips __pycache__, .git/, node_modules/, build/, dist/
- Skips binary kicad outputs (.kicad_pcb, .kicad_sch open in KiCad not VS Code)
- Skips files that don't exist (hook may fire on path miss)

Idempotent: if file is already open in VS Code, `code -g` just focuses the tab.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


WATCH_TOOLS = {"Write", "Edit"}
SKIP_PARTS = {"__pycache__", ".git", "node_modules", "build", "dist", ".venv"}
SKIP_SUFFIX = {".kicad_pcb", ".kicad_sch", ".kicad_pro", ".pyc"}


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

    tool = payload.get("tool_name", "")
    if tool not in WATCH_TOOLS:
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path")
    if not file_path:
        return 0

    path = Path(file_path)
    if not path.is_absolute() or not path.exists():
        return 0

    if any(part in SKIP_PARTS for part in path.parts):
        return 0
    if path.suffix in SKIP_SUFFIX:
        return 0

    cwd = Path(payload.get("cwd", os.getcwd()))
    repo = find_repo_root(cwd)
    try:
        path.relative_to(repo)
    except ValueError:
        return 0  # outside repo

    code_bin = shutil.which("code")
    if not code_bin:
        return 0  # VS Code CLI not on PATH; silently no-op

    try:
        subprocess.run(
            [code_bin, "-r", "-g", f"{path}:1"],
            check=False,
            timeout=3,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
