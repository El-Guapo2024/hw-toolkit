"""KiCad backend — subprocess wrappers around `kicad-cli`.

Only entry point engineers should touch from notebooks is `Board.show()` /
`Board.check_erc()` / `Board.write_kicad()`. These call into `cli.py`.

The `kicad-cli` binary path is resolved (in order) from:
  1. `HW_TOOLKIT_KICAD_CLI` env var
  2. `which kicad-cli` on PATH
  3. macOS default: `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`

Raises `KiCadCliMissingError` if none resolve.
"""
from hw_toolkit.kicad.cli import (
    KiCadCliMissingError,
    KiCadCliRunError,
    KiCadCliTimeoutError,
    NoSvgProducedError,
    erc_json,
    find_cli,
    render_sch_svg,
)
from hw_toolkit.kicad.write import apply_plan, mark_scratch, write_populated

__all__ = [
    "KiCadCliMissingError",
    "KiCadCliRunError",
    "KiCadCliTimeoutError",
    "NoSvgProducedError",
    "apply_plan",
    "erc_json",
    "find_cli",
    "render_sch_svg",
    "write_populated",
]
