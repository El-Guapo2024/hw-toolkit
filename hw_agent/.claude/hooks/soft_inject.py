#!/usr/bin/env python3
"""UserPromptSubmit hook: inject stage-scoped doctrine into prompt context.

Reads .state.json for current stage, loads doctrine.yaml, filters rules whose
soft.stages match (or contain "*"), prints a markdown block to stdout. Claude Code
treats hook stdout from UserPromptSubmit as additional context appended to the
user message, so the agent sees the active doctrine every turn (mode B).

Replaces the older one-line user_prompt_submit.sh reminder.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    while cur != cur.parent:
        gitp = cur / ".git"
        if gitp.is_dir() or gitp.is_file():
            return cur
        cur = cur.parent
    return start.resolve()


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    cwd = Path(payload.get("cwd", os.getcwd()))
    repo = find_repo_root(cwd)

    # Make the harness package importable regardless of how the hook is invoked.
    sys.path.insert(0, str(repo))

    try:
        from hw_agent.harness import loader
    except Exception as e:
        print(f"# hw-agent harness: import failed ({e})", file=sys.stderr)
        return 0

    state_file = repo / "hw_agent" / ".state.json"
    state: dict = {}
    if state_file.is_file():
        try:
            state = json.loads(state_file.read_text())
        except Exception:
            state = {}

    stage = state.get("stage", "")
    project = state.get("project", "")

    try:
        rules = loader.load_doctrine()
    except Exception as e:
        print(f"# hw-agent harness: doctrine load failed ({e})", file=sys.stderr)
        return 0

    matching = loader.rules_for_stage(rules, stage) if stage else []

    banner = f"hw-agent harness · stage={stage or '-'} · project={project or '-'}"
    print(f"## {banner}")
    if matching:
        print(loader.soft_inject_text(matching, heading="Active doctrine"))
    else:
        print("_no doctrine rules for current stage_")

    return 0


if __name__ == "__main__":
    sys.exit(main())
