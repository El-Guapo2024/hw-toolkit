"""Tests for Phase A iface additions: bus reference check, I2C.add_pulls,
Buck typed factory.
"""
from __future__ import annotations

import pytest

import hw_toolkit as hw
from hw_toolkit.iface import BusReferenceMismatch, I2C, Power
from hw_toolkit.parts import Buck


def _board() -> hw.Board:
    return hw.Board("test_phase_a")


# ----------------------------------------------- bus reference voltage check


def test_i2c_connect_matching_reference_voltage_passes():
    b = _board()
    mcu = b.module(id="mcu", category="mcu", mpn="XX1", package="SOIC-8")
    sen = b.module(id="sen", category="sensor", mpn="XX1", package="SOIC-8")
    rail = Power(hv=mcu.pin("VDD"), lv=mcu.pin("GND"), voltage=3.3)
    sen_rail = Power(hv=sen.pin("VDD"), lv=sen.pin("GND"), voltage=3.3)
    mcu.expose(vdd=rail, i2c0=I2C(scl=mcu.pin("SCL"), sda=mcu.pin("SDA"),
                                  frequency=400_000, reference=rail))
    sen.expose(vdd=sen_rail, i2c=I2C(scl=sen.pin("SCL"), sda=sen.pin("SDA"),
                                     reference=sen_rail))
    mcu.i2c0.connect_to(sen.i2c)  # should not raise


def test_i2c_connect_mismatched_reference_voltage_raises():
    b = _board()
    mcu = b.module(id="mcu", category="mcu", mpn="XX1", package="SOIC-8")
    sen = b.module(id="sen", category="sensor", mpn="XX1", package="SOIC-8")
    rail_33 = Power(hv=mcu.pin("VDD"), lv=mcu.pin("GND"), voltage=3.3)
    rail_18 = Power(hv=sen.pin("VDD"), lv=sen.pin("GND"), voltage=1.8)
    mcu.expose(vdd=rail_33, i2c0=I2C(scl=mcu.pin("SCL"), sda=mcu.pin("SDA"),
                                     reference=rail_33))
    sen.expose(vdd=rail_18, i2c=I2C(scl=sen.pin("SCL"), sda=sen.pin("SDA"),
                                    reference=rail_18))
    with pytest.raises(BusReferenceMismatch) as exc:
        mcu.i2c0.connect_to(sen.i2c)
    assert exc.value.v_a == 3.3
    assert exc.value.v_b == 1.8


def test_i2c_connect_no_reference_skips_check():
    """If either side leaves reference=None, the check is skipped — the
    engineer is acknowledging an unmodeled level shifter or pull arrangement.
    """
    b = _board()
    mcu = b.module(id="mcu", category="mcu", mpn="XX1", package="SOIC-8")
    sen = b.module(id="sen", category="sensor", mpn="XX1", package="SOIC-8")
    mcu.expose(i2c0=I2C(scl=mcu.pin("SCL"), sda=mcu.pin("SDA")))
    sen.expose(i2c=I2C(scl=sen.pin("SCL"), sda=sen.pin("SDA")))
    mcu.i2c0.connect_to(sen.i2c)  # both reference=None → no raise


# ------------------------------------------------ I2C.add_pulls


def test_i2c_add_pulls_creates_two_resistors_on_board():
    b = _board()
    mcu = b.module(id="mcu", category="mcu", mpn="XX1", package="SOIC-8")
    vdd = Power(hv=mcu.pin("VDD"), lv=mcu.pin("GND"), voltage=3.3)
    mcu.expose(vdd=vdd, i2c0=I2C(scl=mcu.pin("SCL"), sda=mcu.pin("SDA"),
                                  reference=vdd))
    n_before = len(b._modules)
    r_scl, r_sda = mcu.i2c0.add_pulls(value="4k7")
    assert len(b._modules) == n_before + 2
    assert "4k7" in r_scl.mpn
    assert "4k7" in r_sda.mpn


def test_i2c_add_pulls_ties_resistors_between_lines_and_rail():
    b = _board()
    mcu = b.module(id="mcu", category="mcu", mpn="XX1", package="SOIC-8")
    vdd = Power(hv=mcu.pin("VDD"), lv=mcu.pin("GND"), voltage=3.3)
    mcu.expose(vdd=vdd, i2c0=I2C(scl=mcu.pin("SCL"), sda=mcu.pin("SDA"),
                                  reference=vdd))
    r_scl, r_sda = mcu.i2c0.add_pulls(value="4k7")
    # The SCL net should contain mcu.SCL + r_scl.A
    scl_net = b._find_net_with_member(("mcu", "SCL"))
    assert (r_scl.id, "A") in scl_net.members
    # The HV pull-up rail should contain both .B pins
    hv_net = b._find_net_with_member(("mcu", "VDD"))
    assert (r_scl.id, "B") in hv_net.members
    assert (r_sda.id, "B") in hv_net.members


def test_i2c_add_pulls_without_reference_raises_when_no_power_passed():
    b = _board()
    mcu = b.module(id="mcu", category="mcu", mpn="XX1", package="SOIC-8")
    mcu.expose(i2c0=I2C(scl=mcu.pin("SCL"), sda=mcu.pin("SDA")))
    with pytest.raises(ValueError):
        mcu.i2c0.add_pulls(value="4k7")


# ------------------------------------------------ Buck typed factory


def _full_buck(b, **kw):
    defaults = dict(
        id="buck_3v3", mpn="TPS54302", package="SOT-23-6",
        vin=12.0, vout=3.3, l="10uH", cin="10uF", cout="22uF",
        cboot="100nF", rtop="31.6k", rbot="10k",
    )
    defaults.update(kw)
    return Buck(b, **defaults)


def test_buck_factory_builds_full_block_with_typed_power_iface():
    b = _board()
    buck = _full_buck(b, price_usd=1.85, manufacturer="Texas Instruments")
    assert isinstance(buck, hw.Module)
    assert isinstance(buck.power_in, Power) and isinstance(buck.power_out, Power)
    assert buck.power_in.voltage == 12.0 and buck.power_out.voltage == 3.3
    # Input is the IC pin; output is the INDUCTOR node, not an IC pin.
    assert buck.power_in.hv == hw.Pin("buck_3v3", "VIN")
    assert buck.power_out.hv == hw.Pin("buck_3v3_l", "2")
    # All support components got created.
    for suffix in ("cin", "cout", "cboot", "l", "rtop", "rbot"):
        assert f"buck_3v3_{suffix}" in b.parts
    # IC resolved to a real symbol; passives too.
    assert buck.lib_id == "Regulator_Switching:TPS54302"
    assert b.parts["buck_3v3_l"].lib_id == "Device:L"


def test_buck_to_mcu_power_connect_merges_output_rail():
    b = _board()
    buck = _full_buck(b)
    mcu = b.module(id="mcu", category="mcu", mpn="XX1", package="LQFP-32")
    mcu.expose(vdd=Power(hv=mcu.pin("VDD"), lv=mcu.pin("VSS"), voltage=3.3))
    buck.power_out.connect_to(mcu.vdd)
    # MCU VDD joins the existing vout rail (inductor node); VSS joins gnd.
    vout_members = {m for m in b.nets["buck_3v3_vout"].members}
    assert ("mcu", "VDD") in vout_members
    assert ("mcu", "VSS") in b.nets["gnd"].members


def test_buck_full_block_passes_tight_erc():
    b = _board()
    _full_buck(b)
    # All parts real → no synthesis suppressions needed.
    b.check_erc(expected_codes=hw.ERC_REAL_SYMBOL_CODES)


def test_buck_tie_enable_false_leaves_en_floating_pin():
    b = _board()
    buck = _full_buck(b, tie_enable=False)
    assert buck.en == hw.Pin("buck_3v3", "EN")
    assert ("buck_3v3", "EN") not in b.nets["buck_3v3_vin"].members


def test_buck_pinout_override():
    """Custom chip with non-default pin names: pass `pinout=`."""
    b = _board()
    buck = _full_buck(
        b, id="lmr16030", mpn="LMR16030SDDAR", package="SO-PowerPAD-8",
        vin=24.0, vout=5.0,
        pinout={"vin": "PVIN", "gnd": "PGND", "en": "EN",
                "sw": "SW", "boot": "BOOT", "fb": "FB"},
    )
    assert buck.power_in.hv == hw.Pin("lmr16030", "PVIN")
    assert buck.power_in.lv == hw.Pin("lmr16030", "PGND")
    assert buck.power_out.hv == hw.Pin("lmr16030_l", "2")
