"""hw_toolkit.parts — typed-iface factories for common chip categories.

Each factory takes a Board + minimal config and returns a Module with
typed Iface bundles already exposed via `Module.expose(...)`. Engineers
get autocomplete on `.power_in` / `.power_out` etc. without authoring
the Iface boilerplate per project.

Today: Buck (synchronous buck converter). Add more (LDO, MCU, IMU,
charger) as patterns stabilize.

    >>> from hw_toolkit.parts import Buck
    >>> buck = Buck(board, id="buck_3v3", mpn="TPS54331DR", package="SOIC-8",
    ...             vin=12.0, vout=3.3, price_usd=1.85,
    ...             manufacturer="Texas Instruments")
    >>> buck.power_out.connect_to(mcu.vdd)  # typed
"""
from hw_toolkit.parts.buck import Buck

__all__ = ["Buck"]
