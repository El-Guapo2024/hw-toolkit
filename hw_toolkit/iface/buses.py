"""Concrete Iface subclasses: Power, Gnd, I2C, SPI, UART, USB2.

Pin name conventions follow faebryk's vocabulary (`hv` / `lv` for power
high+low, `scl` / `sda` for I2C, etc.) so engineers familiar with atopile
can read these without surprise. Each bundle is a plain dataclass — no
metaclass magic, no runtime registration.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from hw_toolkit.iface.base import Iface, Pin


@dataclass
class Power(Iface):
    """Power rail: high-voltage pin + low-voltage / return pin.

    `voltage` is the nominal rail voltage in volts. Used as metadata only
    today — the underlying Net carries it onto the schematic.
    """

    _pin_names: ClassVar[tuple[str, ...]] = ("hv", "lv")

    hv: Pin
    lv: Pin
    voltage: float | None = None

    def _desc(self) -> str:
        v = f"{self.voltage}V" if self.voltage is not None else "?V"
        return f"hv={self.hv}, lv={self.lv}, {v}"


@dataclass
class Gnd(Iface):
    """Ground reference — single pin. Connects to the board-wide gnd net."""

    _pin_names: ClassVar[tuple[str, ...]] = ("pin",)

    pin: Pin

    def _desc(self) -> str:
        return f"pin={self.pin}"


@dataclass
class I2C(Iface):
    """I²C bus: open-drain SCL + SDA. `frequency` is the bus speed in Hz.

    `reference` is the Power rail SCL/SDA should idle high to (host's VDD).
    Optional — only required if you want `bus_check()` to verify both lines
    share a reference or call `add_pulls()` to add open-drain pull-ups.
    """

    _pin_names: ClassVar[tuple[str, ...]] = ("scl", "sda")
    _power_reference_attr: ClassVar[str | None] = "reference"

    scl: Pin
    sda: Pin
    frequency: float | None = None
    reference: "Power | None" = None

    def _desc(self) -> str:
        f = f"{self.frequency / 1e3:.0f}kHz" if self.frequency else "?kHz"
        return f"scl={self.scl}, sda={self.sda}, {f}"

    def add_pulls(
        self,
        power: "Power | None" = None,
        *,
        value: str = "4k7",
        refdes_prefix: str | None = None,
        package: str = "0603",
    ) -> tuple["object", "object"]:
        """Add I²C bus pull-up resistors (SCL + SDA → power.hv).

        Call ONCE per bus, on whichever device hosts the pulls. Reuses the
        Iface's `reference` if `power` not supplied. Returns the two new
        resistor Modules so the engineer can `.show()` / inspect them.

            >>> mcu.i2c0.add_pulls(power=mcu.vdd, value="4k7")

        Per-frequency value guidance (NXP UM10204 §7.1):
          - 100 kHz → 4k7
          - 400 kHz → 2k2
          - 1 MHz   → 1k0
        """
        from hw_toolkit.iface.base import Pin
        mod = self._owner_module
        if mod is None:
            raise ValueError(
                "add_pulls: I2C must be attached to a Module via "
                ".expose() first"
            )
        rail = power if power is not None else self.reference
        if rail is None:
            raise ValueError(
                "add_pulls: no power rail given and I2C.reference is None"
            )
        board = mod.board
        prefix = refdes_prefix or f"R_{mod.id}_i2c"
        r_scl = board.resistor(
            f"{prefix}_scl", value=value, package=package
        )
        r_sda = board.resistor(
            f"{prefix}_sda", value=value, package=package
        )
        # Tie one side of each resistor to power.hv, other to the bus line.
        # Reach into existing nets via connect_iface for hv side, then
        # extend the data nets with the resistor pin manually since pulls
        # are 2-pin and don't map cleanly to a typed Iface.
        scl_net = board._find_net_with_member(self.scl.member())
        if scl_net is None:
            scl_net = board.net(
                f"{mod.id}_scl", type="data", protocol="i2c"
            )
            scl_net.members.append(self.scl.member())
        scl_net.members.append((r_scl.id, "A"))

        sda_net = board._find_net_with_member(self.sda.member())
        if sda_net is None:
            sda_net = board.net(
                f"{mod.id}_sda", type="data", protocol="i2c"
            )
            sda_net.members.append(self.sda.member())
        sda_net.members.append((r_sda.id, "A"))

        hv_net = board._find_net_with_member(rail.hv.member())
        if hv_net is None:
            hv_net = board.net(
                f"{mod.id}_hv_pulls", type="power",
                voltage_v=rail.voltage,
            )
            hv_net.members.append(rail.hv.member())
        hv_net.members.append((r_scl.id, "B"))
        hv_net.members.append((r_sda.id, "B"))
        return r_scl, r_sda


@dataclass
class SPI(Iface):
    """SPI bus — 3-wire (sclk/mosi/miso). CS lines belong to the device,
    so each peripheral declares its own CS as a separate single `Pin`.
    """

    _pin_names: ClassVar[tuple[str, ...]] = ("sclk", "mosi", "miso")
    _power_reference_attr: ClassVar[str | None] = "reference"

    sclk: Pin
    mosi: Pin
    miso: Pin
    frequency: float | None = None
    reference: "Power | None" = None

    def _desc(self) -> str:
        f = f"{self.frequency / 1e6:.1f}MHz" if self.frequency else "?MHz"
        return f"sclk={self.sclk}, mosi={self.mosi}, miso={self.miso}, {f}"


@dataclass
class UART(Iface):
    """UART pair: tx + rx (perspective-neutral; connect_to swaps roles)."""

    _pin_names: ClassVar[tuple[str, ...]] = ("tx", "rx")
    _power_reference_attr: ClassVar[str | None] = "reference"

    tx: Pin
    rx: Pin
    baud: int | None = None
    reference: "Power | None" = None

    def _desc(self) -> str:
        b = f"{self.baud} bps" if self.baud else "?bps"
        return f"tx={self.tx}, rx={self.rx}, {b}"


@dataclass
class USB2(Iface):
    """USB 2.0 bundle: vbus + d+ + d- + gnd."""

    _pin_names: ClassVar[tuple[str, ...]] = ("vbus", "dp", "dm", "gnd")

    vbus: Pin
    dp: Pin
    dm: Pin
    gnd: Pin

    def _desc(self) -> str:
        return f"vbus={self.vbus}, dp={self.dp}, dm={self.dm}, gnd={self.gnd}"
