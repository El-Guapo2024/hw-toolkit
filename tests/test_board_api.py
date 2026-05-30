"""Board / Module / Net API contract tests.

Covers the public surface: factory shortcuts, typed exceptions, net
expansion, late-mutation setters, summary text. KiCad-cli interactions
(render, ERC) live in tests/test_kicad_pipeline.py — those are slower
and require kicad-cli to be installed.
"""
from __future__ import annotations

import pytest

import hw_toolkit as hw


# --------------------------------------------------------------- module()
def test_module_factory_returns_handle_with_pick_fields() -> None:
    board = hw.Board("t")
    buck = board.module(
        id="buck", category="buck_converter", mpn="TPS54331DR",
        package="SOIC-8", price_usd=1.05,
    )
    assert buck.id == "buck"
    assert buck.mpn == "TPS54331DR"
    assert buck.package == "SOIC-8"
    assert buck.price_usd == 1.05


def test_module_accessor_via_board_getitem() -> None:
    board = hw.Board("t")
    board.module(id="mcu", category="mcu_module", mpn="ESP32-S3-WROOM-1-N16R8")
    assert board["mcu"].mpn == "ESP32-S3-WROOM-1-N16R8"


def test_unknown_subsystem_raises_typed() -> None:
    board = hw.Board("t")
    board.module(id="mcu", category="mcu_module", mpn="ESP32-S3-WROOM-1-N16R8")
    with pytest.raises(hw.UnknownSubsystemError) as exc:
        _ = board["nope"]
    assert "nope" in str(exc.value)
    assert exc.value.known == ("mcu",)


# --------------------------------------------------------------- validate()
def test_validate_passes_when_all_members_resolve() -> None:
    board = hw.Board("t")
    board.module(id="a", category="mcu_module", mpn="ESP32-S3")
    board.module(id="b", category="mcu_module", mpn="ESP32-S3")
    net = board.net("sig", type="data", protocol="i2c")
    net += "a.SDA", "b.SDA"
    board.validate()  # no raise


def test_validate_raises_on_unknown_subsystem_member() -> None:
    board = hw.Board("t")
    board.module(id="mcu0", category="mcu_module", mpn="ESP32-S3")
    rail = board.net("v3v3", type="power", voltage_v=3.3)
    rail += "mcu0.VDD", "mcu.VDD"  # typo: "mcu" not a module id
    with pytest.raises(hw.UnknownSubsystemError) as exc:
        board.validate()
    assert exc.value.subsystem_id == "mcu"
    assert "mcu0" in exc.value.known


def test_validate_allows_external_nc_sentinel() -> None:
    board = hw.Board("t")
    board.module(id="conn", category="connector", mpn="USB4125")
    nc = board.nc("conn_sbu1_nc")
    nc += "conn.SBU1"  # NC sentinel is the second member
    board.validate()  # no raise


def test_validate_runs_implicitly_on_bundle() -> None:
    # the gate fires without an explicit validate() call
    board = hw.Board("t")
    board.module(id="mcu0", category="mcu_module", mpn="ESP32-S3")
    rail = board.net("v3v3", type="power", voltage_v=3.3)
    rail += "mcu0.VDD", "ghost.VDD"
    with pytest.raises(hw.UnknownSubsystemError):
        _ = board.bundle


# --------------------------------------------------------------- net()
def test_net_iadd_joins_string_endpoints() -> None:
    board = hw.Board("t")
    board.module(id="a", category="mcu_module", mpn="ESP32-S3")
    board.module(id="b", category="mcu_module", mpn="ESP32-S3")
    net = board.net("sig", type="data", protocol="i2c")
    net += "a.SDA", "b.SDA"
    assert net.members == [("a", "SDA"), ("b", "SDA")]


def test_net_star_expansion_one_hub_per_consumer() -> None:
    board = hw.Board("t")
    board.module(id="src", category="buck_converter", mpn="TPS54331DR")
    board.module(id="d1", category="mcu_module", mpn="ESP32-S3")
    board.module(id="d2", category="mcu_module", mpn="ESP32-S3")
    rail = board.net("v3v3", type="power", voltage_v=3.3)
    rail += "src.VOUT", "d1.VDD", "d2.VDD"
    # Two pairwise interfaces emerge (N-1 from hub).
    ifaces = board.bundle.interfaces
    assert len(ifaces) == 2
    assert all(i.from_subsystem == "src" and i.from_port == "VOUT" for i in ifaces)


def test_duplicate_net_id_raises_typed() -> None:
    board = hw.Board("t")
    board.net("nn", type="signal", protocol="i2c")
    with pytest.raises(hw.DuplicateNetError):
        board.net("nn", type="signal", protocol="i2c")


def test_net_signal_without_protocol_raises_at_call_site() -> None:
    """Fails fast with a helpful message — not deep in bundle validation."""
    board = hw.Board("t")
    with pytest.raises(ValueError) as exc:
        board.net("foo", type="signal")
    msg = str(exc.value)
    assert "protocol" in msg
    assert "board.signal" in msg


def test_net_data_without_protocol_raises_at_call_site() -> None:
    board = hw.Board("t")
    with pytest.raises(ValueError) as exc:
        board.net("foo", type="data")
    assert "protocol" in str(exc.value)


def test_net_power_without_protocol_still_allowed() -> None:
    board = hw.Board("t")
    n = board.net("v3v3", type="power", voltage_v=3.3)
    assert n.protocol is None


def test_empty_net_raises_typed_on_bundle() -> None:
    board = hw.Board("t")
    board.module(id="a", category="mcu_module", mpn="ESP32-S3")
    n = board.net("sig", type="signal", protocol="i2c")
    n += "a.X"  # only 1 member — should fail when bundle is built
    with pytest.raises(hw.EmptyNetError):
        _ = board.bundle


# --------------------------------------------------------------- factory shortcuts
def test_power_factory() -> None:
    board = hw.Board("t")
    rail = board.power("v3v3", 3.3)
    assert rail.type == "power"
    assert rail.voltage_v == 3.3


def test_gnd_factory() -> None:
    board = hw.Board("t")
    g = board.gnd()
    assert g.id == "gnd"
    assert g.voltage_v == 0


def test_i2c_factory_returns_sda_scl_pair() -> None:
    board = hw.Board("t")
    sda, scl = board.i2c("bus0")
    assert sda.id == "bus0_sda"
    assert scl.id == "bus0_scl"
    assert sda.protocol == "i2c" and scl.protocol == "i2c"


# --------------------------------------------------------------- module mutation
def test_module_attach_is_chainable() -> None:
    board = hw.Board("t")
    buck = board.module(id="buck", category="buck_converter", mpn="TPS54331DR")
    result = buck.attach(hw.calc.Buck(vin=11.1, vout=3.3, iout=0.5))
    assert result is buck
    assert buck.math is not None


def test_module_check_raises_typed_on_failure() -> None:
    board = hw.Board("t")
    m = board.module(id="x", category="mcu_module", mpn="ESP32-S3")
    with pytest.raises(hw.CheckFailed) as exc:
        m.check(False, label="should fail")
    assert exc.value.subsystem_id == "x"
    assert "should fail" in str(exc.value)


def test_module_set_footprint_chainable() -> None:
    board = hw.Board("t")
    m = board.module(id="x", category="mcu_module", mpn="ESP32-S3", package="OLD")
    result = m.set_footprint("NEW:FP")
    assert result is m
    assert m.package == "NEW:FP"


# --------------------------------------------------------------- introspection
def test_parts_and_nets_mapping_views() -> None:
    board = hw.Board("t")
    board.module(id="a", category="mcu_module", mpn="ESP32-S3")
    board.module(id="b", category="mcu_module", mpn="ESP32-S3")
    board.power("v3v3", 3.3)
    assert list(board.parts) == ["a", "b"]
    assert list(board.nets) == ["v3v3"]


def test_summary_contains_parts_and_nets() -> None:
    board = hw.Board("t")
    board.module(id="mcu", category="mcu_module", mpn="ESP32-S3")
    board.power("v3v3", 3.3)
    out = board.summary()
    assert "mcu" in out and "v3v3" in out and "3.3V" in out


# --------------------------------------------------------------- version
def test_version_is_string() -> None:
    assert isinstance(hw.__version__, str)
    assert hw.__version__.count(".") >= 1
