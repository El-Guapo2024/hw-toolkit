"""PCB backend — placed `.kicad_pcb` (footprints + nets + ratsnest) from a
finished schematic. Touches kicad-cli (netlist export + svg render) and the
installed footprint libraries.
"""
from __future__ import annotations

import shutil

import pytest

import hw_toolkit as hw
from hw_toolkit.kicad.pcb import PcbError, render_pcb_svg, write_pcb


def _buck_board(tmp_path):
    from hw_toolkit.parts import Buck

    b = hw.Board("pcbtest", scratch_dir=str(tmp_path))
    b.module(id="j_in", category="connector", mpn="Conn_01x02",
             package="PinHeader_2.54mm_1x02", price_usd=0.1)
    Buck(b, id="buck5", mpn="TPS54302", package="SOT-23-6", vin=12, vout=5,
         l="10uH", cin="10uF", cout="22uF", cboot="100nF",
         rtop="52.3k", rbot="10k")
    b.nets["buck5_vin"] += ("j_in.Pin_1",)
    b.nets["gnd"] += ("j_in.Pin_2",)
    return b


def test_write_pcb_places_real_footprints_and_reports_skips(tmp_path) -> None:
    b = _buck_board(tmp_path)
    res = b.write_pcb()
    assert res.pcb_path.exists()
    # IC + its passives place (real footprints); the connector has no mapped
    # footprint and is reported, not silently dropped.
    assert "U1" in res.placed
    assert len(res.placed) >= 6
    assert "J1" in res.skipped


def test_pcb_assigns_nets_to_pads(tmp_path) -> None:
    b = _buck_board(tmp_path)
    res = b.write_pcb()
    txt = res.pcb_path.read_text()
    # Net declarations + at least one pad carrying a GND net assignment.
    assert '(net 0 "")' in txt
    assert '"GND"' in txt
    assert "(net " in txt and "(pad " in txt
    # Ratsnest airwires drawn on the comment layer so they render.
    assert '"Cmts.User"' in txt
    assert "(gr_line" in txt


def test_render_pcb_svg(tmp_path) -> None:
    b = _buck_board(tmp_path)
    res = b.write_pcb()
    svg = render_pcb_svg(res.pcb_path)
    assert svg.exists() and svg.stat().st_size > 1000


def test_write_pcb_raises_when_nothing_placeable(tmp_path) -> None:
    # A board of only unmapped-footprint parts → nothing to place.
    b = hw.Board("nopcb", scratch_dir=str(tmp_path))
    b.module(id="j1", category="connector", mpn="Conn_01x02",
             package="PinHeader_2.54mm_1x02", price_usd=0.1)
    b.module(id="j2", category="connector", mpn="Conn_01x02",
             package="PinHeader_2.54mm_1x02", price_usd=0.1)
    b.connect("j1.Pin_1", "j2.Pin_1", type="signal", protocol="gpio")
    b.connect("j1.Pin_2", "j2.Pin_2", type="signal", protocol="gpio")
    with pytest.raises(PcbError):
        b.write_pcb()
