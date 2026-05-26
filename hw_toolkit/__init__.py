"""hw_toolkit — Python library for hardware design (Jupyter-first).

v1 scope: schematic only. One `Board`, module-per-cell, finalized
`.kicad_sch` at the end. No BOM, no placement, no PCB, no routing yet.

Typical use:

    >>> import hw_toolkit as hw
    >>> from hw_agent.core import SubsystemPick
    >>>
    >>> board = hw.Board("control_hub_v1")
    >>> buck = board.add(SubsystemPick(id="buck_3v3",
    ...                                 category="buck_converter",
    ...                                 mpn="TPS54331DR", package="SOIC-8"))
    >>> buck.math = hw.calc.Buck(vin=11.1, vout=3.3, iout=0.5)
    >>> buck.check(buck.math.thermal(rdson_mohm=80, theta_ja=40))
    >>> buck.show()                       # inline SVG of just this module
    >>>
    >>> board.connect("buck_3v3.VOUT", "mcu.VDD", type="power", voltage_v=3.3)
    >>> board.check_erc()
    >>> board.show()                      # inline SVG of full schematic
    >>> board.write_kicad()               # final .kicad_sch ready to hand-tune

Errors are typed exceptions, not strings. See `hw_toolkit.exceptions`.
"""
from hw_toolkit import calc
from hw_toolkit.board import Board, Module, Net
from hw_toolkit.exceptions import (
    BundleValidationError,
    CheckFailed,
    DRCViolation,
    DuplicateNetError,
    EmptyNetError,
    ERCViolation,
    EvalReport,
    FootprintMissingError,
    HwToolkitError,
    MultipleDRCViolations,
    MultipleERCViolations,
    RoutingFailedError,
    UnknownSubsystemError,
)
from hw_toolkit.kicad import (
    KiCadCliMissingError,
    KiCadCliRunError,
    KiCadCliTimeoutError,
    NoSvgProducedError,
)

__all__ = [
    "Board",
    "Module",
    "Net",
    "calc",
    "HwToolkitError",
    "BundleValidationError",
    "CheckFailed",
    "DuplicateNetError",
    "EmptyNetError",
    "UnknownSubsystemError",
    "ERCViolation",
    "MultipleERCViolations",
    "FootprintMissingError",
    "RoutingFailedError",
    "DRCViolation",
    "MultipleDRCViolations",
    "EvalReport",
    "KiCadCliMissingError",
    "KiCadCliRunError",
    "KiCadCliTimeoutError",
    "NoSvgProducedError",
]
