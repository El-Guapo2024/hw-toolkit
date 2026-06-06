"""hw_toolkit — Python library for hardware design (Jupyter-first).

v1 scope: schematic only. One `Board`, module-per-cell, finalized
`.kicad_sch` at the end. No BOM, no placement, no PCB, no routing yet.

Typical use:

    >>> import hw_toolkit as hw
    >>> from hw_toolkit.core import SubsystemPick
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
__version__ = "0.2.0"

# Silence kicad-sch-api's warning chatter during expected fallbacks (e.g.
# `add_power` trying `power:+PWR_FLAG` first, then `power:PWR_FLAG`).
# Engineer can re-raise verbosity via `logging.getLogger('kicad_sch_api')`.
# Must precede downstream imports — they load kicad_sch_api eagerly.
import logging as _logging  # noqa: E402  (deliberate ordering)

_logging.getLogger("kicad_sch_api").setLevel(_logging.ERROR)

from hw_toolkit import calc, iface, parts  # noqa: E402
from hw_toolkit.board import (  # noqa: E402
    ERC_BASELINE_CODES,
    ERC_REAL_SYMBOL_CODES,
    Board,
    Module,
    Net,
)
from hw_toolkit.iface import (  # noqa: E402
    BusReferenceMismatch,
    Gnd,
    I2C,
    Iface,
    IfaceTypeMismatch,
    Pin,
    PinNotFound,
    Power,
    SPI,
    UART,
    USB2,
)
from hw_toolkit.exceptions import (  # noqa: E402
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
    PlanningModeError,
    RoutingFailedError,
    UnknownSubsystemError,
)
from hw_toolkit.state import (  # noqa: E402
    current_mode,
    read_state,
    set_mode,
)
from hw_toolkit.kicad import (  # noqa: E402
    KiCadCliMissingError,
    KiCadCliRunError,
    KiCadCliTimeoutError,
    NoSvgProducedError,
)

def planning():
    """Enter planning mode — block committing writes (`write_kicad`/`write_pcb`)
    so the engineer can explore options. Rendering + ERC still work."""
    return set_mode("planning")


def design():
    """Enter design mode (default) — authoring writes allowed."""
    return set_mode("design")


__all__ = [
    "Board",
    "Module",
    "Net",
    "planning",
    "design",
    "set_mode",
    "current_mode",
    "read_state",
    "PlanningModeError",
    "ERC_BASELINE_CODES",
    "ERC_REAL_SYMBOL_CODES",
    "calc",
    "iface",
    "parts",
    "Iface",
    "Pin",
    "Power",
    "Gnd",
    "I2C",
    "SPI",
    "UART",
    "USB2",
    "BusReferenceMismatch",
    "IfaceTypeMismatch",
    "PinNotFound",
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
