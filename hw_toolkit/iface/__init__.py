"""hw_toolkit.iface — typed electrical interface bundles.

Borrowed from atopile/faebryk's `ElectricPower` / `I2C` / `SPI` pattern but
trimmed to a Python-only, notebook-friendly shape — no Zig backend, no
metaclass field lifecycle, no ANTLR DSL. The goal is to give Module a typed
IO surface so consumers connect bundles instead of free-form pin strings:

    >>> buck.power_out.connect_to(mcu.vdd)   # type-checked
    >>> sensor.i2c.connect_to(mcu.i2c0)      # type-checked

Falsey type mismatches raise `IfaceTypeMismatch` at the `.connect_to()` call
site — not 200 lines later at ERC time. Typoed pin access raises
`PinNotFound` listing the available pins on the bundle.

The existing `Net += "sub.PIN"` API still works and remains the escape valve
for one-off / mixed-protocol connections. `Iface.connect_to` is the typed
default for well-modeled subsystems.
"""
from hw_toolkit.iface.base import (
    BusReferenceMismatch,
    Iface,
    IfaceTypeMismatch,
    Pin,
    PinNotFound,
)
from hw_toolkit.iface.buses import (
    Gnd,
    I2C,
    Power,
    SPI,
    UART,
    USB2,
)

__all__ = [
    "BusReferenceMismatch",
    "Iface",
    "IfaceTypeMismatch",
    "Pin",
    "PinNotFound",
    "Gnd",
    "I2C",
    "Power",
    "SPI",
    "UART",
    "USB2",
]
