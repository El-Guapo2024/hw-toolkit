"""hw_toolkit.parts — block factories for common subsystems.

Each factory takes a Board + config and returns a Module with typed Iface
bundles exposed via `Module.expose(...)`. The block factories also build
and wire the supporting passives, so the engineer connects rails instead
of placing every cap and resistor by hand.

Today: Buck (full synchronous buck converter block — IC + Cin/Cout/L/
Cboot + feedback divider, auto-wired). Add more (LDO, MCU, IMU, charger)
as patterns stabilize.

    >>> from hw_toolkit.parts import Buck
    >>> buck = Buck(board, id="buck_3v3", mpn="TPS54302", package="SOT-23-6",
    ...             vin=12.0, vout=3.3, l="10uH", cin="10uF", cout="22uF",
    ...             cboot="100nF", rtop="31.6k", rbot="10k")
    >>> buck.power_out.connect_to(mcu.power_in)  # typed
"""
from hw_toolkit.parts.buck import Buck

__all__ = ["Buck"]
