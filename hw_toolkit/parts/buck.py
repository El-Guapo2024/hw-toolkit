"""Buck — synchronous step-down converter as a full, auto-wired block.

Unlike a bare IC factory, `Buck` builds the whole regulator: the
controller IC plus its input cap, output cap, inductor, bootstrap cap,
and feedback divider — and wires the switch node, bootstrap, feedback,
and the VIN/VOUT/GND rails. The caller passes the component values; the
factory turns each into a real `Device:C/L/R` part (via the resolver) and
connects them.

Topology (switching buck — the output is the INDUCTOR node, not an IC
pin; the IC exposes SW + FB):

    VIN ─┬─ Cin ─ GND          SW ─┬─ L ─┬─ VOUT ─┬─ Cout ─ GND
         IC.VIN                IC.SW   │         ├─ Rtop ─┐
    EN ──┘ (tied on)                   └─ Cboot ─ BOOT    FB ── IC.FB
                                                          └─ Rbot ─ GND

The returned Module is the IC, with typed `power_in` (at IC.VIN) and
`power_out` (at the inductor output node) `Power` bundles exposed, so
consumers connect rails type-safely:

    >>> buck = Buck(board, id="buck_3v3", mpn="TPS54302", package="SOT-23-6",
    ...             vin=12.0, vout=3.3, l="10uH", cin="10uF", cout="22uF",
    ...             cboot="100nF", rtop="31.6k", rbot="10k")
    >>> source.power_out.connect_to(buck.power_in)
    >>> buck.power_out.connect_to(mcu.power_in)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from hw_toolkit.iface import Power

if TYPE_CHECKING:
    from hw_toolkit.board import Board, Module, Net


# Logical role → IC pin name. Override `pinout` for a chip with different
# names. A switching buck has no VOUT pin — output is the inductor node.
_DEFAULT_PINOUT = {
    "vin":  "VIN",
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
    l: str,
    cin: str,
    cout: str,
    cboot: str,
    rtop: str,
    rbot: str,
    lib_id: str | None = None,
    manufacturer: str = "",
    price_usd: float = 0.0,
    cap_package: str = "0805",
    res_package: str = "0603",
    ind_package: str = "1210",
    gnd: "Net | None" = None,
    tie_enable: bool = True,
    pinout: dict[str, str] | None = None,
) -> "Module":
    """Build a complete buck regulator and return the controller Module.

    `l`, `cin`, `cout`, `cboot`, `rtop`, `rbot` are component values (e.g.
    "10uH", "22uF", "31.6k") — each becomes a real Device:C/L/R part. The
    feedback divider (`rtop`/`rbot`) sets VOUT; the caller sizes it. Pass a
    `gnd` net to join the board's existing ground (otherwise the shared
    "gnd" net is reused or created). `tie_enable=True` ties EN to VIN for
    an always-on converter; set False to wire EN yourself via `buck.en`.
    """
    p = {**_DEFAULT_PINOUT, **(pinout or {})}
    VIN, GND, EN, SW, BOOT, FB = (
        p["vin"], p["gnd"], p["en"], p["sw"], p["boot"], p["fb"],
    )

    # All parts share group=id so the planner lays them out as one tight
    # cluster (short internal wires) instead of scattering the passives.
    ic = board.module(
        id=id, category="buck_converter", mpn=mpn, package=package,
        lib_id=lib_id, manufacturer=manufacturer, price_usd=price_usd,
        group=id,
    )

    # Support components — each auto-resolves to a real Device:C/L/R symbol.
    cin_m = board.capacitor(f"{id}_cin", cin, package=cap_package, group=id)
    cout_m = board.capacitor(f"{id}_cout", cout, package=cap_package, group=id)
    cboot_m = board.capacitor(f"{id}_cboot", cboot, package="0402", group=id)
    l_m = board.inductor(f"{id}_l", l, package=ind_package, group=id)
    rtop_m = board.resistor(f"{id}_rtop", rtop, package=res_package, group=id)
    rbot_m = board.resistor(f"{id}_rbot", rbot, package=res_package, group=id)

    # Rails + internal nodes.
    gnd_net = gnd if gnd is not None else (board.nets.get("gnd") or board.gnd())
    vin_net = board.power(f"{id}_vin", voltage_v=vin)
    vout_net = board.power(f"{id}_vout", voltage_v=vout)
    sw = board.signal(f"{id}_sw", protocol="analog")
    boot = board.signal(f"{id}_boot", protocol="analog")
    fb = board.signal(f"{id}_fb", protocol="analog")

    # Wiring (Device:C/L/R pins are numeric: "1"/"2").
    vin_net += f"{ic.id}.{VIN}", f"{cin_m.id}.1"
    gnd_net += (
        f"{ic.id}.{GND}", f"{cin_m.id}.2", f"{cout_m.id}.2", f"{rbot_m.id}.2",
    )
    sw += f"{ic.id}.{SW}", f"{l_m.id}.1", f"{cboot_m.id}.2"
    boot += f"{ic.id}.{BOOT}", f"{cboot_m.id}.1"
    vout_net += f"{l_m.id}.2", f"{cout_m.id}.1", f"{rtop_m.id}.1"
    fb += f"{ic.id}.{FB}", f"{rtop_m.id}.2", f"{rbot_m.id}.1"
    if tie_enable:
        vin_net += f"{ic.id}.{EN}"

    # Typed IO: input at the IC, output at the inductor node.
    ic.expose(
        power_in=Power(hv=ic.pin(VIN), lv=ic.pin(GND), voltage=vin),
        power_out=Power(hv=l_m.pin("2"), lv=ic.pin(GND), voltage=vout),
    )
    ic.en = ic.pin(EN)  # raw Pin for manual EN control when tie_enable=False
    return ic
