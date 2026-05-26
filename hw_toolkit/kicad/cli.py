"""Subprocess wrappers around `kicad-cli`.

Headless KiCad operations only. No IPC, no second window. Pure
file-in/file-out so notebook cells stay reproducible.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

from hw_toolkit.exceptions import HwToolkitError

DEFAULT_MAC_CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"

# kicad-cli sometimes hangs on malformed projects. Cap at 60s; engineer
# can override via HW_TOOLKIT_KICAD_CLI_TIMEOUT for slow CI hosts.
_DEFAULT_TIMEOUT_S = float(os.environ.get("HW_TOOLKIT_KICAD_CLI_TIMEOUT", "60"))


class KiCadCliMissingError(HwToolkitError):
    """`kicad-cli` not found on PATH / env / macOS default."""
    def __init__(self) -> None:
        super().__init__(
            "kicad-cli not found. Set HW_TOOLKIT_KICAD_CLI env var, "
            "add kicad-cli to PATH, or install KiCad in the default macOS location."
        )


class KiCadCliRunError(HwToolkitError):
    """`kicad-cli` exited non-zero."""
    def __init__(self, cmd: list[str], returncode: int, stderr: str) -> None:
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"kicad-cli failed (exit {returncode}): {shlex.join(cmd)}\n{stderr}"
        )


class KiCadCliTimeoutError(HwToolkitError):
    """`kicad-cli` hung past the configured timeout."""
    def __init__(self, cmd: list[str], timeout_s: float) -> None:
        self.cmd = cmd
        self.timeout_s = timeout_s
        super().__init__(
            f"kicad-cli timed out after {timeout_s:.0f}s: {shlex.join(cmd)}"
        )


class NoSvgProducedError(HwToolkitError):
    """`kicad-cli sch export svg` returned 0 but wrote no SVG file."""
    def __init__(self, sch: Path, out_dir: Path) -> None:
        self.sch = sch
        self.out_dir = out_dir
        super().__init__(
            f"kicad-cli sch export svg produced no SVG file in {out_dir} "
            f"for input {sch}"
        )


def find_cli() -> Path:
    """Resolve the `kicad-cli` binary. Raises `KiCadCliMissingError` if absent."""
    env = os.environ.get("HW_TOOLKIT_KICAD_CLI")
    if env and Path(env).exists():
        return Path(env)
    which = shutil.which("kicad-cli")
    if which:
        return Path(which)
    default = Path(DEFAULT_MAC_CLI)
    if default.exists():
        return default
    raise KiCadCliMissingError()


def _run(args: list[str], *, timeout_s: float | None = None) -> subprocess.CompletedProcess:
    cli = find_cli()
    cmd = [str(cli), *args]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout_s if timeout_s is not None else _DEFAULT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as e:
        raise KiCadCliTimeoutError(cmd, e.timeout) from e
    if proc.returncode != 0:
        raise KiCadCliRunError(cmd, proc.returncode, proc.stderr)
    return proc


def render_sch_svg(sch_path: str | Path, out_dir: str | Path | None = None) -> Path:
    """Export the schematic as SVG. Returns the path to the generated SVG.

    `kicad-cli sch export svg` writes one SVG per page to `out_dir`, named
    after the sheet. For single-sheet projects this is `<project>.svg`.
    """
    sch = Path(sch_path)
    out = Path(out_dir) if out_dir else sch.parent
    out.mkdir(parents=True, exist_ok=True)
    _run([
        "sch", "export", "svg",
        "-o", str(out),
        "--no-background-color",
        "--exclude-drawing-sheet",
        str(sch),
    ])
    svg = out / f"{sch.stem}.svg"
    if not svg.exists():
        # Fall back to any svg produced (multi-sheet projects)
        matches = sorted(out.glob(f"{sch.stem}*.svg"))
        if not matches:
            raise NoSvgProducedError(sch=sch, out_dir=out)
        svg = matches[0]
    return svg


def erc_json(sch_path: str | Path, out_path: str | Path | None = None) -> Path:
    """Run ERC and write JSON report. Returns the report path.

    Passes `KIPRJMOD=<sch.parent>` via `--define-var` so the project-local
    `sym-lib-table` (which interpolates `${KIPRJMOD}/hwagent.kicad_sym`)
    resolves correctly. Without this, kicad-cli warns about
    `lib_symbol_issues` even though the lib file is sitting next to the
    .kicad_sch.

    Does NOT raise on violations — parsing is the caller's job
    (`Board.check_erc()` parses + raises typed errors).
    """
    sch = Path(sch_path)
    report = Path(out_path) if out_path else sch.parent / "erc.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "sch", "erc",
        "-o", str(report),
        "-D", f"KIPRJMOD={sch.parent}",
        "--format", "json",
        "--severity-all",
        str(sch),
    ])
    return report
