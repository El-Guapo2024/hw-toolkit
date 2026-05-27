"""SPICE backend — emit a Berkeley-SPICE netlist from a `Board`.

The notebook flow's first artifact is a KiCad schematic; SPICE is the
**second** backend: the engineer asks `board.export_spice("foo.cir")` and
hw_toolkit walks the same `Board` state, mapping each subsystem onto an
`X<refdes>` subcircuit-call and each `Net` onto a SPICE node name.

The netlist is *topology only* — component models (`.MODEL`, `.SUBCKT`)
are NOT included. The engineer (or downstream library) injects them by
appending `.LIB` / `.INCLUDE` directives before running ngspice / LTspice.

Why no models in v1
- Picking the right model per MPN is a research task in its own right
  (vendor PSPICE blobs vary, IBIS exists for digital, sims for analog
  buck converters differ wildly).
- Without component models the netlist still has value: connectivity
  check, node naming sanity, swap-out for hand-edited models.

Typical use
    >>> board.export_spice("control_hub_v1.cir")
    PosixPath('/<cwd>/control_hub_v1.cir')

Then in ngspice:
    .INCLUDE TPS54331DR.lib
    .INCLUDE ESP32-S3.subckt
    .DC VBAT 0 12 0.1
    .END
"""
from hw_toolkit.spice.writer import emit_spice_netlist

__all__ = ["emit_spice_netlist"]
