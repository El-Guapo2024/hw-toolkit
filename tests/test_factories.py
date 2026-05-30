"""Factory shortcut tests — spi, uart, i2s, usbc, dual_supply."""
from __future__ import annotations

import pytest

import hw_toolkit as hw


def _board() -> hw.Board:
    return hw.Board("t")


# --------------------------------------------------------------- spi
def test_spi_returns_four_nets() -> None:
    b = _board()
    mosi, miso, sck, cs = b.spi("flash")
    assert {n.id for n in (mosi, miso, sck, cs)} == {
        "flash_mosi", "flash_miso", "flash_sck", "flash_cs",
    }
    assert all(n.protocol == "spi" for n in (mosi, miso, sck, cs))


def test_spi_nets_are_data_type() -> None:
    b = _board()
    nets = b.spi("bus0")
    assert all(n.type == "data" for n in nets)


# --------------------------------------------------------------- uart
def test_uart_returns_tx_rx() -> None:
    b = _board()
    tx, rx = b.uart("gps")
    assert tx.id == "gps_tx" and rx.id == "gps_rx"
    assert tx.protocol == "uart" and rx.protocol == "uart"


# --------------------------------------------------------------- i2s
def test_i2s_returns_three_nets() -> None:
    b = _board()
    bclk, lrck, data = b.i2s("audio0")
    assert bclk.protocol == "i2s"
    assert {n.id for n in (bclk, lrck, data)} == {
        "audio0_bclk", "audio0_lrck", "audio0_data",
    }


# --------------------------------------------------------------- usbc
def test_usbc_default_is_power_plus_data_only() -> None:
    b = _board()
    usb = b.usbc("conn0")
    # cc/sbu are opt-in so a plain power+data port doesn't trip EmptyNetError
    assert set(usb.keys()) == {"vbus", "gnd", "dp", "dm"}
    assert usb["vbus"].voltage_v == 5.0
    assert usb["gnd"].voltage_v == 0
    assert all(usb[k].protocol == "usb" for k in ("dp", "dm"))


def test_usbc_opt_in_cc_and_sbu() -> None:
    b = _board()
    usb = b.usbc("conn0", cc=True, sbu=True)
    assert set(usb.keys()) == {
        "vbus", "gnd", "cc1", "cc2", "dp", "dm", "sbu1", "sbu2",
    }
    assert all(usb[k].protocol == "usb" for k in ("cc1", "cc2", "sbu1", "sbu2"))


def test_usbc_charge_only_drops_data() -> None:
    b = _board()
    usb = b.usbc("conn0", data=False)
    assert set(usb.keys()) == {"vbus", "gnd"}


def test_usbc_default_bundle_passes_erc() -> None:
    # regression: default usbc() used to create empty cc/sbu nets that
    # tripped EmptyNetError at bundle time for a plain power+data port.
    b = _board()
    conn = b.module(id="conn0", category="connector", mpn="USB4125", package="USB-C")
    mcu = b.module(id="mcu", category="mcu", mpn="STM32F042", package="LQFP-32")
    usb = b.usbc("conn0")
    usb["vbus"] += "conn0.VBUS", "mcu.VBUS"
    usb["gnd"] += "conn0.GND", "mcu.GND"
    usb["dp"] += "conn0.DP", "mcu.USB_DP"
    usb["dm"] += "conn0.DM", "mcu.USB_DM"
    b.check_erc(expected_codes=hw.ERC_BASELINE_CODES)


# --------------------------------------------------------------- export gate
def _usb_power_board() -> "hw.Board":
    b = _board()
    conn = b.module(id="conn0", category="connector", mpn="USB4125", package="USB-C")
    mcu = b.module(id="mcu", category="mcu", mpn="STM32F042", package="LQFP-32")
    usb = b.usbc("conn0")
    usb["vbus"] += "conn0.VBUS", "mcu.VBUS"
    usb["gnd"] += "conn0.GND", "mcu.GND"
    usb["dp"] += "conn0.DP", "mcu.USB_DP"
    usb["dm"] += "conn0.DM", "mcu.USB_DM"
    return b


def test_export_kicad_runs_erc_by_default(tmp_path) -> None:
    # default erc=True ships ERC with the baseline artifact suppressions —
    # a clean board exports without the caller calling check_erc() first.
    b = _usb_power_board()
    out = b.export_kicad(tmp_path / "usb.zip")
    assert out.exists()


def test_export_kicad_erc_false_skips_check(tmp_path) -> None:
    # erc=False still exports; just no ERC gate.
    b = _usb_power_board()
    out = b.export_kicad(tmp_path / "usb.zip", erc=False)
    assert out.exists()


# --------------------------------------------------------------- dual_supply
def test_dual_supply_returns_pos_neg_pair() -> None:
    b = _board()
    vp, vn = b.dual_supply("analog15", vpos=15, vneg=15)
    assert vp.id == "analog15_pos" and vn.id == "analog15_neg"
    assert vp.voltage_v == 15 and vn.voltage_v == 15


def test_dual_supply_rejects_negative_magnitudes() -> None:
    b = _board()
    with pytest.raises(ValueError, match="positive magnitudes"):
        b.dual_supply("bad", vpos=-15, vneg=15)


# --------------------------------------------------------------- new protocols
def test_extended_protocol_enum_accepts_analog_gpio_pwm() -> None:
    b = _board()
    n1 = b.signal("audio_l", protocol="analog")
    n2 = b.signal("led_data", protocol="gpio")
    n3 = b.signal("esc_pwm", protocol="pwm")
    n4 = b.signal("ds18b20_data", protocol="onewire")
    assert n1.protocol == "analog"
    assert n2.protocol == "gpio"
    assert n3.protocol == "pwm"
    assert n4.protocol == "onewire"


def test_extended_protocol_validates_at_bundle() -> None:
    b = _board()
    b.module(id="a", category="mcu_module", mpn="ESP32-S3")
    b.module(id="b", category="mcu_module", mpn="ESP32-S3")
    sig = b.signal("data", protocol="gpio")
    sig += "a.PIN", "b.PIN"
    # bundle expansion should accept the new protocol
    bundle = b.bundle
    assert bundle.interfaces[0].protocol == "gpio"


# --------------------------------------------------------------- swd / can
def test_swd_returns_three_nets() -> None:
    b = _board()
    swdio, swdclk, nreset = b.swd()
    assert swdio.id == "swd_swdio"
    assert swdclk.id == "swd_swdclk"
    assert nreset.id == "swd_nreset"
    assert all(n.protocol == "swd" for n in (swdio, swdclk, nreset))


def test_can_returns_pair() -> None:
    b = _board()
    h, l = b.can("can0")
    assert h.protocol == "can" and l.protocol == "can"
    assert h.id == "can0_h" and l.id == "can0_l"


# --------------------------------------------------------------- stereo / diff_pair
def test_stereo_returns_left_right() -> None:
    b = _board()
    l, r = b.stereo("hp_out")
    assert l.id == "hp_out_l" and r.id == "hp_out_r"
    assert l.protocol == "analog"


def test_diff_pair_p_n_suffix() -> None:
    b = _board()
    p, n = b.diff_pair("usb_hs")
    assert p.id == "usb_hs_p" and n.id == "usb_hs_n"


# --------------------------------------------------------------- nc
def test_nc_net_survives_empty_net_check() -> None:
    b = _board()
    b.module(id="usbc", category="connector", mpn="USB4135-GF-A")
    sbu = b.nc("sbu1")
    sbu += "usbc.SBU1"  # only ONE real pin
    # Normally would raise EmptyNetError at .bundle (only 1 real member);
    # nc() pre-binds external.NC so total is 2.
    bundle = b.bundle
    assert any(i.id.startswith("sbu1") for i in bundle.interfaces)


# --------------------------------------------------------------- passives
def test_resistor_factory_creates_module() -> None:
    b = _board()
    r1 = b.resistor("R1", "10k")
    assert r1.id == "r1"
    assert r1.category == "resistor"
    assert r1.mpn == "R_10k_0603"
    assert r1.package == "0603"


def test_capacitor_factory_with_package_override() -> None:
    b = _board()
    c1 = b.capacitor("C1", "100nF", package="0402")
    assert c1.mpn == "C_100nF_0402"
    assert c1.package == "0402"


def test_inductor_factory() -> None:
    b = _board()
    l1 = b.inductor("L1", "10uH", package="0805")
    assert l1.mpn == "L_10uH_0805"
    assert l1.category == "inductor"


# --------------------------------------------------------------- locked_at caching
def test_locked_at_stable_across_bundle_calls() -> None:
    b = _board()
    b.module(id="a", category="mcu_module", mpn="ESP32-S3")
    b.module(id="b", category="mcu_module", mpn="ESP32-S3")
    rail = b.power("v", 3.3); rail += "a.VDD", "b.VDD"
    t1 = b.bundle.locked_at
    import time; time.sleep(0.01)
    t2 = b.bundle.locked_at
    assert t1 == t2  # bundle's locked_at must NOT advance per access
