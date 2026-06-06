"""Session state bus + planning-mode gate."""
from __future__ import annotations

import pytest

import hw_toolkit as hw
from hw_toolkit import state


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Point the state file at a tmp path so tests don't touch ~/.claude."""
    monkeypatch.setenv("HW_STATE_PATH", str(tmp_path / ".hw-state"))
    yield


def test_read_empty_when_absent():
    assert state.read_state() == {}


def test_write_merges_and_persists():
    state.write_state(project="b", phase="schematic")
    state.write_state(erc_clean=True)
    s = state.read_state()
    assert s == {"project": "b", "phase": "schematic", "erc_clean": True}


def test_write_drops_none():
    state.write_state(project="b", phase=None)
    assert "phase" not in state.read_state()


def test_clear():
    state.write_state(project="b")
    state.clear_state()
    assert state.read_state() == {}
    state.clear_state()  # idempotent


def test_mode_default_is_design():
    assert state.current_mode() == "design"


def test_set_mode_roundtrip():
    hw.planning()
    assert state.current_mode() == "planning"
    hw.design()
    assert state.current_mode() == "design"


def test_set_mode_rejects_unknown():
    with pytest.raises(ValueError):
        state.set_mode("nonsense")


def test_corrupt_state_reads_as_empty():
    state.state_path().write_text("{ not json", encoding="utf-8")
    assert state.read_state() == {}
    assert state.current_mode() == "design"


def test_write_kicad_blocked_in_planning():
    hw.planning()
    board = hw.Board("gate_test")
    with pytest.raises(hw.PlanningModeError) as exc:
        board.write_kicad()
    assert exc.value.action == "write_kicad"


def test_write_pcb_blocked_in_planning():
    hw.planning()
    board = hw.Board("gate_test")
    with pytest.raises(hw.PlanningModeError):
        board.write_pcb()


def test_design_mode_does_not_block_gate_check():
    """In design mode the gate is a no-op (write proceeds to real work)."""
    hw.design()
    assert state.current_mode() == "design"
