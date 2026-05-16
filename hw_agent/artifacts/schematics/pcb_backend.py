"""PCB editing backend — IPC-only.

Live PCB editing (move footprints, add tracks, change properties)
goes through kicad-python IPC. The user must have pcbnew open with
the API server enabled (Preferences → API server).

This module is a thin probe + pass-through. The real work is in
`pcb_ipc.py`. MCP tools call functions here; if IPC isn't reachable,
they get a clear "open pcbnew first" error.

Headless workflows (CI, Docker, scripts without a GUI) are handled
by kicad-cli for ERC/DRC/exports and by FreeRouting for routing —
neither involves this module. Headless PCB *editing* isn't supported
until KiCad 11 ships `kicad-cli pcb api-server` (~Feb 2027).

Schematic editing is NOT routed here — it stays on kicad-sch-api
file-based via `sch_ops.py` and `ksa_writer.py`.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def is_ipc_available() -> bool:
    """Probe whether KiCad's IPC server is reachable.

    Returns True only when both:
      1. `KICAD_API_SOCKET` env var is set (KiCad sets it on startup
         when API server is enabled in Preferences)
      2. `KiCad().ping()` succeeds (process is alive + listening)
    """
    if not os.environ.get("KICAD_API_SOCKET"):
        return False
    try:
        from kipy import KiCad
        KiCad().ping()
        return True
    except Exception:
        return False


def _require_ipc() -> dict:
    """Return a clear error dict when IPC is required but unavailable."""
    return {
        "ok": False,
        "error": (
            "PCB live edits require pcbnew running with the IPC server "
            "enabled (Preferences → API server). Open pcbnew, then retry."
        ),
    }


# ─── Op dispatchers — pure IPC pass-through ───────────────────────────────

def move_footprint(kicad_pcb: Path, ref: str, x: float, y: float,
                   rotation: Optional[float] = None) -> dict:
    """Move a footprint live in pcbnew. User watches it happen."""
    if not is_ipc_available():
        return _require_ipc()
    from . import pcb_ipc
    return pcb_ipc.move_footprint(Path(kicad_pcb), ref, x, y, rotation=rotation)


def list_footprints(kicad_pcb: Path) -> list[dict]:
    """Live read of footprint positions from open pcbnew."""
    if not is_ipc_available():
        return []
    from . import pcb_ipc
    return pcb_ipc.list_footprints(Path(kicad_pcb))


def save_board(kicad_pcb: Path) -> dict:
    """Force-save the open .kicad_pcb. IPC's commit lifecycle updates
    pcbnew's in-memory state but doesn't write the file by itself."""
    if not is_ipc_available():
        return _require_ipc()
    from . import pcb_ipc
    return pcb_ipc.save_board(Path(kicad_pcb))


def export_dsn(kicad_pcb: Path, out_dsn: Path) -> dict:
    """Specctra DSN export via pcbnew's IPC action. Requires pcbnew open."""
    if not is_ipc_available():
        return _require_ipc()
    from . import pcb_ipc
    return pcb_ipc.export_dsn(Path(kicad_pcb), Path(out_dsn))


def import_ses(kicad_pcb: Path, ses_path: Path) -> dict:
    """SES import (FreeRouting output) via pcbnew's IPC action."""
    if not is_ipc_available():
        return _require_ipc()
    from . import pcb_ipc
    return pcb_ipc.import_ses(Path(kicad_pcb), Path(ses_path))


def sync_netlist(kicad_pcb: Path, netlist_path: Path) -> dict:
    """Push schematic netlist to PCB via UpdateFromSchematic IPC action."""
    if not is_ipc_available():
        return _require_ipc()
    from . import pcb_ipc
    return pcb_ipc.sync_netlist(Path(kicad_pcb), Path(netlist_path))


def build_pcb(spec: dict, out_kicad_pcb: Path) -> dict:
    """Build a fresh .kicad_pcb from a spec. Requires pcbnew open;
    IPC build is currently a stub — see pcb_ipc.build_pcb."""
    if not is_ipc_available():
        return _require_ipc()
    from . import pcb_ipc
    return pcb_ipc.build_pcb(spec, Path(out_kicad_pcb))
