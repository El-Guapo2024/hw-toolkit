#!/usr/bin/env python3
"""PreToolUse hook: enforce doctrine via tool-arg validators.

Reads Claude Code hook stdin (tool_name, tool_input, cwd), normalizes the tool
name (strips MCP prefix), loads doctrine.yaml, runs every matching rule's
validator against the tool input + current state.

Exit codes:
  0 -- pass (no rule matched, or all validators returned None)
  2 -- block; stderr message is shown to the model so it can correct course

Multiple rules can match a single tool; all violations are reported together.
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


def normalize_tool_name(raw: str) -> str:
    """Strip MCP prefix: 'mcp__designer-mcp__subsystem_add' -> 'subsystem_add'.

    Built-in tools like 'Write' / 'Edit' / 'Task' pass through unchanged.
    """
    if not raw:
        return ""
    if "__" in raw:
        return raw.split("__")[-1]
    return raw


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # malformed input: do not block

    cwd = Path(payload.get("cwd", os.getcwd()))
    repo = find_repo_root(cwd)
    sys.path.insert(0, str(repo))

    try:
        from hw_agent.harness import loader, validators
    except Exception as e:
        # Never block on harness import failure; surface as stderr warn.
        print(f"hw-agent harness: import failed ({e})", file=sys.stderr)
        return 0

    raw_tool = payload.get("tool_name", "")
    tool_name = normalize_tool_name(raw_tool)
    tool_input = payload.get("tool_input", {}) or {}

    state_file = repo / "hw_agent" / ".state.json"
    state: dict = {}
    if state_file.is_file():
        try:
            state = json.loads(state_file.read_text())
        except Exception:
            state = {}

    try:
        rules = loader.load_doctrine()
    except Exception as e:
        print(f"hw-agent harness: doctrine load failed ({e})", file=sys.stderr)
        return 0

    matching = loader.rules_for_tool_call(rules, tool_name)
    if not matching:
        return 0

    violations: list[tuple[str, str]] = []
    for rule in matching:
        if not loader.evaluate_when(rule.hard_when, tool_input):
            continue
        if not rule.hard_validator:
            continue
        validator_fn = validators.REGISTRY.get(rule.hard_validator)
        if validator_fn is None:
            print(
                f"hw-agent harness: validator '{rule.hard_validator}' "
                f"for rule '{rule.id}' not in REGISTRY (skipped).",
                file=sys.stderr,
            )
            continue
        try:
            validator_fn(tool_input, state)
        except validators.ValidationError as ve:
            violations.append((rule.id, str(ve)))
        except Exception as e:
            # Validator itself broke -- log and skip; do not block on harness bugs.
            print(
                f"hw-agent harness: validator '{rule.hard_validator}' raised "
                f"unexpected error ({e}); rule '{rule.id}' skipped.",
                file=sys.stderr,
            )

    if violations:
        lines = [f"BLOCKED by hw-agent harness ({len(violations)} violation(s)):"]
        for rid, msg in violations:
            lines.append(f"  - [{rid}] {msg}")
        lines.append(f"Tool: {raw_tool}  (normalized: {tool_name})")
        lines.append("Fix the inputs to satisfy the rule(s), then retry.")
        print("\n".join(lines), file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
