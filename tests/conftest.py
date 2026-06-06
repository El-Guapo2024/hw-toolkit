"""Shared test fixtures."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_hw_state(tmp_path_factory, monkeypatch):
    """Point the hw-toolkit session state file at a throwaway path for every
    test, so write_kicad()/check_erc()/mode toggles never touch the real
    ~/.claude/.hw-state."""
    p = tmp_path_factory.mktemp("hw_state") / ".hw-state"
    monkeypatch.setenv("HW_STATE_PATH", str(p))
    yield
