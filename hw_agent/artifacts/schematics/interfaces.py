"""Typed bus interfaces — I2C, SPI, UART, USB2, CAN, SWD, JTAG.

A bundle of related PinRefs/NetRefs treated as one connection. Pattern
borrowed from Atopile's `Interface` and Zener's `io()` declarations.

The leverage:
    # Without bundles — 6 wires for an I2C link with bus power:
    s.wire(mcu["PB7"], sensor["SDA"])
    s.wire(mcu["PB6"], sensor["SCL"])
    s.wire(v3v3, sensor["VDD"])
    s.wire(gnd,  sensor["GND"])
    # ...and a pull-up pair (separate)

    # With bundles — 1 wire:
    mcu_i2c    = I2C(sda=mcu["PB7"], scl=mcu["PB6"])
    sensor_i2c = I2C(sda=sensor["SDA"], scl=sensor["SCL"])
    s.wire(mcu_i2c, sensor_i2c)

Wiring two interfaces of the same type connects matching pin names
(sda↔sda, scl↔scl). Different types or missing pins → static error.

UART note: pins are NOT auto-crossed. Build each side with explicit
direction so the cross is auditable in user code:
    mcu_uart   = UART(tx=mcu["PA9"],   rx=mcu["PA10"])
    modem_uart = UART(tx=modem["RXD"], rx=modem["TXD"])  # cross is here
    s.wire(mcu_uart, modem_uart)
"""
from __future__ import annotations

from typing import Any, ClassVar


class Interface:
    """Bundle of named electrical refs forming a typed bus.

    Concrete subclasses declare `PINS` — a tuple of required pin names.
    Extra named endpoints (e.g. shared `vcc`, `gnd`) are accepted as
    optional metadata but not wired automatically.
    """

    PINS: ClassVar[tuple[str, ...]] = ()

    def __init__(self, **endpoints: Any):
        missing = [p for p in self.PINS if p not in endpoints]
        if missing:
            raise ValueError(
                f"{type(self).__name__} missing required pin(s): "
                f"{', '.join(missing)}"
            )
        self._endpoints: dict[str, Any] = {p: endpoints[p] for p in self.PINS}
        self._extras: dict[str, Any] = {
            k: v for k, v in endpoints.items() if k not in self.PINS
        }

    def __getattr__(self, name: str) -> Any:
        # Dunder + private — fall through to default lookup
        if name.startswith("_"):
            raise AttributeError(name)
        ep = self.__dict__.get("_endpoints", {})
        if name in ep:
            return ep[name]
        ex = self.__dict__.get("_extras", {})
        if name in ex:
            return ex[name]
        raise AttributeError(
            f"{type(self).__name__} has no pin {name!r}; "
            f"declared: {self.PINS}, extras: {tuple(ex.keys())}"
        )

    def __repr__(self) -> str:
        eps = ", ".join(f"{k}={v!r}" for k, v in self._endpoints.items())
        return f"{type(self).__name__}({eps})"


# ─── Concrete interfaces ───────────────────────────────────────────────────

class I2C(Interface):
    """Two-wire bus. Pull-ups + power are user's responsibility (separate)."""
    PINS = ("sda", "scl")


class SPI(Interface):
    """4-wire SPI. `cs` per peripheral; multi-target SPI uses one bundle per CS."""
    PINS = ("sck", "mosi", "miso", "cs")


class UART(Interface):
    """Async serial. NOT auto-crossed — build each side with intended direction."""
    PINS = ("tx", "rx")


class USB2(Interface):
    """USB 2.0 differential pair. VBUS/GND wired separately."""
    PINS = ("dp", "dm")


class CAN(Interface):
    """CAN differential pair (transceiver-side). Termination is per-bus."""
    PINS = ("canh", "canl")


class SWD(Interface):
    """ARM Serial Wire Debug. SWO/RESET are optional extras."""
    PINS = ("swdio", "swclk")


class JTAG(Interface):
    """Standard JTAG. TRST/SRST optional extras."""
    PINS = ("tck", "tms", "tdi", "tdo")


# ─── Wiring ────────────────────────────────────────────────────────────────

def wire_interfaces(sheet: "Sheet", src: Interface, dst: Interface,
                    *, elbow: str = "h") -> int:
    """Connect matching named pins on two interfaces. Returns wire count.

    Raises if types differ. Used by `Sheet.wire()` when both endpoints are
    Interface instances — most callers won't call this directly.
    """
    if type(src) is not type(dst):
        raise ValueError(
            f"interface mismatch: {type(src).__name__} ↔ {type(dst).__name__}"
        )
    for pin in src.PINS:
        sheet.wire(src._endpoints[pin], dst._endpoints[pin], elbow=elbow)
    return len(src.PINS)
