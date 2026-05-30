"""Unit tests for the typed Iface bundle system.

Covers: Pin construction, Iface type checking, Module.expose() + .pin() sugar,
Board.connect_iface() net creation + merging, helpful error messages.
"""
from __future__ import annotations

import pytest

import hw_toolkit as hw
from hw_toolkit.iface import Gnd, I2C, Iface, IfaceTypeMismatch, Pin, PinNotFound, Power, SPI, UART, USB2


# ---------------------------------------------------------------------- Pin


def test_pin_member_tuple():
    p = Pin(owner_id="buck", name="VIN")
    assert p.member() == ("buck", "VIN")
    assert str(p) == "buck.VIN"


def test_pin_is_frozen():
    p = Pin(owner_id="buck", name="VIN")
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        p.name = "VOUT"  # type: ignore[misc]


# -------------------------------------------------------------- Iface basics


def test_power_pin_pairs_yields_in_order():
    a = Power(hv=Pin("u1", "VIN"), lv=Pin("u1", "GND"), voltage=12.0)
    b = Power(hv=Pin("u2", "VDD"), lv=Pin("u2", "VSS"), voltage=12.0)
    pairs = list(a.pin_pairs(b))
    assert pairs == [
        (Pin("u1", "VIN"), Pin("u2", "VDD")),
        (Pin("u1", "GND"), Pin("u2", "VSS")),
    ]


def test_pin_pairs_type_mismatch_raises():
    p = Power(hv=Pin("u1", "VIN"), lv=Pin("u1", "GND"))
    i = I2C(scl=Pin("u2", "SCL"), sda=Pin("u2", "SDA"))
    with pytest.raises(IfaceTypeMismatch) as exc:
        list(p.pin_pairs(i))  # type: ignore[arg-type]
    assert exc.value.a_type == "Power"
    assert exc.value.b_type == "I2C"


def test_typo_on_iface_attribute_raises_pin_not_found():
    p = Power(hv=Pin("u1", "VIN"), lv=Pin("u1", "GND"))
    with pytest.raises(PinNotFound) as exc:
        _ = p.gnd  # typo — should be .lv
    msg = str(exc.value)
    assert "PinNotFound" in msg
    assert "Available: hv, lv" in msg


# -------------------------------------------------------- Module.pin + expose


def _basic_board() -> hw.Board:
    return hw.Board("test_iface_board")


def test_module_pin_sugar_creates_pin_with_owner():
    b = _basic_board()
    m = b.module(id="buck", category="buck_converter",
                 mpn="TPS54331DR", package="SOIC-8")
    p = m.pin("VIN")
    assert isinstance(p, Pin)
    assert p == Pin("buck", "VIN")


def test_module_expose_attaches_iface():
    b = _basic_board()
    m = b.module(id="buck", category="buck_converter",
                 mpn="TPS54331DR", package="SOIC-8")
    m.expose(
        power_in=Power(hv=m.pin("VIN"), lv=m.pin("GND"), voltage=12.0),
        power_out=Power(hv=m.pin("VOUT"), lv=m.pin("GND"), voltage=3.3),
    )
    assert isinstance(m.power_in, Power)
    assert m.power_in.voltage == 12.0
    assert m.power_out.hv == Pin("buck", "VOUT")


def test_expose_rejects_non_iface():
    b = _basic_board()
    m = b.module(id="x1", category="generic", mpn="XX1", package="SOIC-8")
    with pytest.raises(TypeError):
        m.expose(power_in="not an iface")  # type: ignore[arg-type]


def test_expose_rejects_duplicate_name():
    b = _basic_board()
    m = b.module(id="x1", category="generic", mpn="XX1", package="SOIC-8")
    m.expose(p=Power(hv=m.pin("A"), lv=m.pin("B")))
    with pytest.raises(AttributeError):
        m.expose(p=Power(hv=m.pin("C"), lv=m.pin("D")))


# ------------------------------------------------------ Board.connect_iface


def test_connect_iface_creates_nets():
    b = _basic_board()
    buck = b.module(id="buck", category="buck", mpn="XX1", package="SOIC-8")
    mcu = b.module(id="mcu", category="mcu", mpn="XX1", package="SOIC-8")

    buck.expose(power_out=Power(hv=buck.pin("VOUT"), lv=buck.pin("GND"),
                                voltage=3.3))
    mcu.expose(vdd=Power(hv=mcu.pin("VDD"), lv=mcu.pin("VSS"), voltage=3.3))

    nets = buck.power_out.connect_to(mcu.vdd) or []
    # connect_to delegates to board.connect_iface — should have made 2 nets
    # (hv pair, lv pair) since neither pin pre-existed in any net
    assert len(b._nets) == 2
    hv_net = b._nets[0]
    lv_net = b._nets[1]
    assert hv_net.members == [("buck", "VOUT"), ("mcu", "VDD")]
    assert lv_net.members == [("buck", "GND"), ("mcu", "VSS")]


def test_connect_iface_extends_existing_net():
    b = _basic_board()
    buck = b.module(id="buck", category="buck", mpn="XX1", package="SOIC-8")
    mcu  = b.module(id="mcu",  category="mcu",  mpn="XX1", package="SOIC-8")
    imu  = b.module(id="imu",  category="imu",  mpn="XX1", package="SOIC-8")

    buck.expose(power_out=Power(hv=buck.pin("VOUT"), lv=buck.pin("GND"),
                                voltage=3.3))
    mcu.expose(vdd=Power(hv=mcu.pin("VDD"), lv=mcu.pin("VSS"), voltage=3.3))
    imu.expose(vdd=Power(hv=imu.pin("VDD"), lv=imu.pin("VSS"), voltage=3.3))

    buck.power_out.connect_to(mcu.vdd)
    # Second connect_to that shares the buck side should EXTEND, not duplicate
    buck.power_out.connect_to(imu.vdd)

    assert len(b._nets) == 2  # still just hv + lv nets
    hv_members = b._nets[0].members
    assert ("buck", "VOUT") in hv_members
    assert ("mcu", "VDD") in hv_members
    assert ("imu", "VDD") in hv_members


def test_connect_to_without_expose_raises():
    p_a = Power(hv=Pin("u1", "VIN"), lv=Pin("u1", "GND"))
    p_b = Power(hv=Pin("u2", "VDD"), lv=Pin("u2", "VSS"))
    with pytest.raises(ValueError):
        p_a.connect_to(p_b)  # no Module/Board context


def test_connect_to_type_mismatch_raises_at_call_site():
    b = _basic_board()
    mcu = b.module(id="mcu", category="mcu", mpn="XX1", package="SOIC-8")
    sensor = b.module(id="s1", category="sensor", mpn="XX1", package="SOIC-8")
    mcu.expose(vdd=Power(hv=mcu.pin("VDD"), lv=mcu.pin("VSS")))
    sensor.expose(i2c=I2C(scl=sensor.pin("SCL"), sda=sensor.pin("SDA")))
    with pytest.raises(IfaceTypeMismatch):
        mcu.vdd.connect_to(sensor.i2c)  # type: ignore[arg-type]


def test_i2c_net_protocol_is_i2c():
    b = _basic_board()
    mcu = b.module(id="mcu", category="mcu", mpn="XX1", package="SOIC-8")
    sensor = b.module(id="s1", category="sensor", mpn="XX1", package="SOIC-8")
    mcu.expose(i2c0=I2C(scl=mcu.pin("SCL"), sda=mcu.pin("SDA"),
                        frequency=400_000))
    sensor.expose(i2c=I2C(scl=sensor.pin("SCL"), sda=sensor.pin("SDA")))
    mcu.i2c0.connect_to(sensor.i2c)
    assert all(n.protocol == "i2c" for n in b._nets)
    assert all(n.type == "data" for n in b._nets)


def test_spi_three_wire_bundle_makes_three_nets():
    b = _basic_board()
    mcu = b.module(id="mcu", category="mcu", mpn="XX1", package="SOIC-8")
    flash = b.module(id="flash", category="flash", mpn="XX1", package="SOIC-8")
    mcu.expose(spi=SPI(sclk=mcu.pin("SCK"), mosi=mcu.pin("MOSI"),
                       miso=mcu.pin("MISO")))
    flash.expose(spi=SPI(sclk=flash.pin("CLK"), mosi=flash.pin("DI"),
                         miso=flash.pin("DO")))
    mcu.spi.connect_to(flash.spi)
    assert len(b._nets) == 3
    assert all(n.protocol == "spi" for n in b._nets)


def test_uart_pair_makes_two_nets():
    b = _basic_board()
    mcu = b.module(id="mcu", category="mcu", mpn="XX1", package="SOIC-8")
    gps = b.module(id="gps", category="gps", mpn="XX1", package="SOIC-8")
    mcu.expose(uart1=UART(tx=mcu.pin("PA9"), rx=mcu.pin("PA10"), baud=9600))
    gps.expose(uart=UART(tx=gps.pin("TXD"), rx=gps.pin("RXD")))
    mcu.uart1.connect_to(gps.uart)
    assert len(b._nets) == 2
    assert all(n.protocol == "uart" for n in b._nets)


def test_gnd_single_pin_iface():
    b = _basic_board()
    chip_a = b.module(id="a1", category="generic", mpn="XX1", package="SOIC-8")
    chip_b = b.module(id="b1", category="generic", mpn="XX1", package="SOIC-8")
    chip_a.expose(gnd=Gnd(pin=chip_a.pin("GND")))
    chip_b.expose(gnd=Gnd(pin=chip_b.pin("GND")))
    chip_a.gnd.connect_to(chip_b.gnd)
    assert len(b._nets) == 1
    assert b._nets[0].voltage_v == 0.0


def test_usb2_four_wire_bundle_makes_four_signal_nets():
    b = _basic_board()
    mcu = b.module(id="mcu", category="mcu", mpn="XX1", package="SOIC-8")
    conn = b.module(id="usbc", category="connector", mpn="XX1", package="SOIC-8")
    mcu.expose(usb=USB2(vbus=mcu.pin("VBUS"), dp=mcu.pin("DP"),
                        dm=mcu.pin("DM"), gnd=mcu.pin("GND")))
    conn.expose(usb=USB2(vbus=conn.pin("VBUS"), dp=conn.pin("D+"),
                         dm=conn.pin("D-"), gnd=conn.pin("GND")))
    mcu.usb.connect_to(conn.usb)
    assert len(b._nets) == 4
    assert all(n.type == "signal" for n in b._nets)
    assert all(n.protocol == "usb" for n in b._nets)


def test_usb2_type_mismatch_raises():
    b = _basic_board()
    mcu = b.module(id="mcu", category="mcu", mpn="XX1", package="SOIC-8")
    conn = b.module(id="usbc", category="connector", mpn="XX1", package="SOIC-8")
    mcu.expose(usb=USB2(vbus=mcu.pin("VBUS"), dp=mcu.pin("DP"),
                        dm=mcu.pin("DM"), gnd=mcu.pin("GND")))
    conn.expose(uart=UART(tx=conn.pin("TX"), rx=conn.pin("RX")))
    with pytest.raises(IfaceTypeMismatch):
        mcu.usb.connect_to(conn.uart)  # type: ignore[arg-type]
