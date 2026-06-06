"""Session state bus for the human-in-the-loop ambient loop.

A tiny JSON file shared between the running kernel (which writes the design
phase + ERC status as the engineer works) and the plugin's status line +
UserPromptSubmit hook (which read it). This is the caveman flag-file pattern:
out-of-chat state that several processes communicate through, so the human sees
live design state in the status bar and the agent gets its constraints
re-injected every turn (surviving context compaction).

State keys (all optional):
    project    : str  — active board name
    mode       : "design" | "planning"  — planning blocks committing writes
    phase      : "schematic" | "pcb"     — what's being authored
    erc_clean  : bool — last `check_erc()` result

Path: ``$CLAUDE_CONFIG_DIR/.hw-state`` (falls back to ``~/.claude/.hw-state``).
Override with ``$HW_STATE_PATH`` (used by tests).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

VALID_MODES = ("design", "planning")


def state_path() -> Path:
    """Resolve the state file path. Honors $HW_STATE_PATH, else
    $CLAUDE_CONFIG_DIR/.hw-state, else ~/.claude/.hw-state."""
    override = os.environ.get("HW_STATE_PATH")
    if override:
        return Path(override)
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return Path(base) / ".hw-state"


def read_state() -> dict[str, Any]:
    """Return the current state dict, or {} if absent/malformed."""
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {}


def write_state(**changes: Any) -> dict[str, Any]:
    """Merge non-None changes into the state file (atomic temp→rename).
    Returns the new state."""
    s = read_state()
    s.update({k: v for k, v in changes.items() if v is not None})
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(s), encoding="utf-8")
    tmp.replace(p)
    return s


def clear_state() -> None:
    """Remove the state file (e.g. session end)."""
    try:
        state_path().unlink()
    except FileNotFoundError:
        pass


# ----------------------------------------------------------------- mode gate
def current_mode() -> str:
    """Active session mode; defaults to 'design' when unset."""
    m = read_state().get("mode")
    return m if m in VALID_MODES else "design"


def set_mode(mode: str) -> dict[str, Any]:
    """Switch session mode. 'planning' blocks committing writes; 'design'
    allows authoring. Returns the new state."""
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {VALID_MODES}, got {mode!r}")
    return write_state(mode=mode)
