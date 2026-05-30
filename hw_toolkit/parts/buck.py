"""Buck — synchronous step-down converter factory with typed power IO.

Defaults to the TPS54331-style pinout (`VIN`, `VOUT`, `GND`, `EN`, `SW`,
`BOOT`, `FB`). Override `pinout` to map a different chip's pin names.

The factory exposes `power_in` + `power_out` as typed `Power` bundles
so consumers connect rails type-safely. Remaining pins (EN, SW, BOOT,
FB) are attached as raw `Pin` attributes — engineers wire them with
the existing `net += "<id>.PIN"` API or future typed `Logic` bundles.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from hw_toolkit.iface import Power

if TYPE_CHECKING:
    from hw_toolkit.board import Board, Module


_DEFAULT_PINOUT = {
    "vin":  "VIN",
    "vout": "VOUT",
    "gnd":  "GND",
    "en":   "EN",
    "sw":   "SW",
    "boot": "BOOT",
    "fb":   "FB",
}


def Buck(
    board: "Board",
    *,
    id: str,
    mpn: str,
    package: str,
    vin: float,
    vout: float,
    price_usd: float = 0.0,
    manufacturer: str = "",
    pinout: dict[str, str] | None = None,
) -> "Module":
    """Add a buck converter to the board with typed `power_in`/`power_out`.

        >>> buck = Buck(board, id="buck_3v3", mpn="TPS54331DR",
        ...             package="SOIC-8", vin=12.0, vout=3.3,
        ...             price_usd=1.85, manufacturer="Texas Instruments")
        >>> buck.power_in
        Power(hv=Pin(buck_3v3.VIN), lv=Pin(buck_3v3.GND), 12.0V)
        >>> buck.power_out
        Power(hv=Pin(buck_3v3.VOUT), lv=Pin(buck_3v3.GND), 3.3V)
        >>> buck.en, buck.sw, buck.boot, buck.fb   # raw Pins for now
    """
    p = {**_DEFAULT_PINOUT, **(pinout or {})}
    mod = board.module(
        id=id,
        category="buck_converter",
        mpn=mpn,
        package=package,
        price_usd=price_usd,
        manufacturer=manufacturer,
    )
    mod.expose(
        power_in=Power(
            hv=mod.pin(p["vin"]),
            lv=mod.pin(p["gnd"]),
            voltage=vin,
        ),
        power_out=Power(
            hv=mod.pin(p["vout"]),
            lv=mod.pin(p["gnd"]),
            voltage=vout,
        ),
    )
    # Raw Pin attrs for the secondary nets — wire with legacy `+=` API
    # until a typed Logic bundle lands.
    mod.en   = mod.pin(p["en"])
    mod.sw   = mod.pin(p["sw"])
    mod.boot = mod.pin(p["boot"])
    mod.fb   = mod.pin(p["fb"])
    return mod
