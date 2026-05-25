"""hw_toolkit — Python library for hardware design (Jupyter-first).

Replaces the agent-pipeline + MCP-wrapper model. Engineer drives the
flow from notebook cells calling methods on a `Board`. Live-edit-mcp
is the only MCP kept, for KiCad IPC.

Typical use:

    >>> import hw_toolkit as hw
    >>> board = hw.Board.load("control_hub_v1")
    >>> board                              # _repr_ summary
    Board(project_id='control_hub_v1', subsystems=7, interfaces=11)
    >>> plan = board.write_schematic()     # writes blank sch + returns ops
    >>> for op in plan.ops: ...            # dispatch via designer-mcp
    >>> board.check_erc()                  # raises MultipleERCViolations
    >>> moves = board.place()              # MoveSymbol ops for live-edit-mcp

Errors are typed exceptions, not strings. See `hw_toolkit.exceptions`.
"""
from hw_toolkit import calc
from hw_toolkit.board import Board
from hw_toolkit.schematic import Schematic
from hw_toolkit.exceptions import (
    BundleValidationError,
    DRCViolation,
    ERCViolation,
    EvalReport,
    FootprintMissingError,
    HwToolkitError,
    MultipleDRCViolations,
    MultipleERCViolations,
    RoutingFailedError,
)

__all__ = [
    "Board",
    "Schematic",
    "calc",
    "HwToolkitError",
    "BundleValidationError",
    "ERCViolation",
    "MultipleERCViolations",
    "FootprintMissingError",
    "RoutingFailedError",
    "DRCViolation",
    "MultipleDRCViolations",
    "EvalReport",
]
