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
def test_usbc_returns_bundle_dict() -> None:
    b = _board()
    usb = b.usbc("conn0")
    assert set(usb.keys()) == {
        "vbus", "gnd", "cc1", "cc2", "dp", "dm", "sbu1", "sbu2",
    }
    assert usb["vbus"].voltage_v == 5.0
    assert usb["gnd"].voltage_v == 0
    assert all(usb[k].protocol == "usb" for k in ("cc1", "cc2", "dp", "dm", "sbu1", "sbu2"))


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
