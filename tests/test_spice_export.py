"""SPICE backend tests — board.export_spice + board.spice property."""
from __future__ import annotations

from pathlib import Path

import pytest

import hw_toolkit as hw


@pytest.fixture()
def small_board() -> hw.Board:
    b = hw.Board("spice_test")
    b.module(id="buck", category="buck_converter", mpn="TPS54331DR", package="SOIC-8")
    b.module(id="mcu",  category="mcu_module",     mpn="ESP32-S3-WROOM-1-N16R8")
    rail = b.power("v3v3", 3.3); rail += "buck.VOUT", "mcu.VDD"
    g = b.gnd(); g += "buck.GND", "mcu.GND"
    return b


def test_spice_property_returns_string(small_board: hw.Board) -> None:
    txt = small_board.spice
    assert isinstance(txt, str)
    assert txt.endswith(".END\n")


def test_spice_includes_title_line(small_board: hw.Board) -> None:
    txt = small_board.spice
    first = txt.splitlines()[0]
    assert first.startswith("*") and "spice_test" in first


def test_spice_emits_subcircuit_calls(small_board: hw.Board) -> None:
    txt = small_board.spice
    assert "X" in txt
    # Each subsystem -> one X line referencing the MPN.
    assert "TPS54331DR" in txt
    assert "ESP32_S3_WROOM_1_N16R8" in txt  # hyphens sanitized to underscores


def test_spice_gnd_collapses_to_node_zero(small_board: hw.Board) -> None:
    txt = small_board.spice
    # The `gnd` net id should map to SPICE node `0`.
    assert "net `gnd` → node `0`" in txt


def test_spice_node_for_non_gnd_uses_net_id(small_board: hw.Board) -> None:
    txt = small_board.spice
    assert "net `v3v3` → node `v3v3`" in txt


def test_spice_includes_port_order_comment(small_board: hw.Board) -> None:
    txt = small_board.spice
    assert "* port order:" in txt
    assert "VOUT=v3v3" in txt
    assert "VDD=v3v3" in txt


def test_spice_sanitizes_dashes_in_mpn() -> None:
    b = hw.Board("t")
    b.module(id="x", category="mcu_module", mpn="ESP32-S3-WROOM-1-N16R8")
    b.module(id="y", category="mcu_module", mpn="ESP32-S3-WROOM-1-N16R8")
    # No nets — bundle still needs ≥1 subsystem.
    rail = b.power("v", 3.3); rail += "x.VDD", "y.VDD"
    txt = b.spice
    # Hyphens must not appear inside the subckt name on an X line.
    for line in txt.splitlines():
        if line.startswith("X"):
            tokens = line.split()
            subckt = tokens[-1]
            assert "-" not in subckt, f"X-line subckt has hyphen: {subckt!r}"


def test_export_spice_writes_file(small_board: hw.Board, tmp_path: Path) -> None:
    target = tmp_path / "out.cir"
    result = small_board.export_spice(target)
    assert result == target
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert content.endswith(".END\n")


def test_export_spice_creates_parent_dirs(small_board: hw.Board, tmp_path: Path) -> None:
    target = tmp_path / "sub" / "deeper" / "out.cir"
    small_board.export_spice(target)
    assert target.exists()


def test_spice_skips_subsystems_with_no_ports() -> None:
    b = hw.Board("t")
    b.module(id="orphan", category="mcu_module", mpn="ESP32-S3")
    b.module(id="a", category="buck_converter", mpn="TPS54331DR")
    b.module(id="bus", category="mcu_module", mpn="ESP32-S3")
    # Only `a` and `bus` participate in a net.
    rail = b.power("v", 3.3); rail += "a.VOUT", "bus.VDD"
    txt = b.spice
    # Orphan must be commented as skipped (since no ports bound).
    assert "no ports bound" in txt


def test_spice_node_inventory_lines_sorted() -> None:
    b = hw.Board("t")
    b.module(id="a", category="buck_converter", mpn="TPS54331DR")
    b.module(id="b", category="mcu_module", mpn="ESP32-S3")
    rail = b.power("zzz", 5.0); rail += "a.VOUT", "b.VDD"
    g = b.gnd("aaa"); g += "a.GND", "b.GND"
    txt = b.spice
    # The inventory section lists net mappings; ordering should be stable.
    aaa_idx = txt.index("net `aaa`")
    zzz_idx = txt.index("net `zzz`")
    assert aaa_idx < zzz_idx
