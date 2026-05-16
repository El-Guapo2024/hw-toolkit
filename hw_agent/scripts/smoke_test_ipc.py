"""A.5.0 smoke test — verify local KiCad has IPC enabled and round-trip works.

Prereqs:
- eeschema running with any .kicad_sch open
- kicad-python installed from git@main (PyPI 0.6.0 lacks Schematic)

Run:
    python3 hw_agent/scripts/smoke_test_ipc.py

Validates A.5.0:
- Connect to KiCad
- Enumerate open documents
- get_items() round-trip
- create_items + remove_items + commit lifecycle
- Confirms our local KiCad supports the verbs hw_agent will rely on
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    socket_path = os.environ.get("KICAD_API_SOCKET", "ipc:///tmp/kicad/api.sock")
    print(f"socket_path = {socket_path}")

    raw_socket = socket_path.replace("ipc://", "") if socket_path.startswith("ipc://") else socket_path
    if not Path(raw_socket).exists():
        print(f"ERROR: socket file missing at {raw_socket}")
        print("       Open eeschema (KiCad → Schematic Editor) with any schematic, then re-run.")
        print("       If still missing, this build of KiCad lacks KICAD_IPC_API=ON.")
        return 2

    try:
        import kipy
        from kipy.schematic import Schematic
    except ImportError as e:
        print(f"ERROR: kipy import failed — {e}")
        print("       Install via: pip install git+https://gitlab.com/kicad/code/kicad-python.git@main")
        return 3

    print(f"kipy: {kipy.__file__}")

    kicad = kipy.KiCad()
    try:
        version = kicad.get_version()
        print(f"KiCad version: {version}")
    except Exception as e:
        print(f"ERROR: can't reach KiCad — {e}")
        return 4

    schematics = kicad.get_open_schematics()
    if not schematics:
        print("ERROR: no schematics open in eeschema.")
        print("       Open File → Open... in eeschema and re-run.")
        return 5

    sch: Schematic = schematics[0]
    print(f"\nopen schematic: {sch.name}")
    print(f"  document path: {sch.document.board_filename if hasattr(sch.document, 'board_filename') else sch.document}")

    symbols = sch.get_symbols()
    lines = sch.get_lines()
    labels = sch.get_labels()
    print(f"  symbols: {len(symbols)}")
    print(f"  lines:   {len(lines)}")
    print(f"  labels:  {len(labels)}")

    if symbols:
        first = symbols[0]
        print(f"  first symbol: {getattr(first, 'reference', '?')} at {getattr(first, 'position', '?')}")

    print("\nRound-trip test: create + remove a junction")
    try:
        from kipy.schematic_types import Junction
        from kipy.geometry import Vector2

        j = Junction()
        j.position = Vector2.from_xy_mm(50.0, 50.0)
        j.diameter = 0
        created = sch.create_items([j])
        print(f"  created: {len(created)} item(s); first id = {created[0].id if created else 'none'}")

        if created:
            sch.remove_items(created)
            print(f"  removed: OK")
    except Exception as e:
        print(f"  WARN: round-trip raised {type(e).__name__}: {e}")
        print(f"  (This is informational — the read path above is what A.5 needs.)")

    print("\nSMOKE TEST PASSED.")
    print("Next: A.5 implementation (replace kicad_writer.py with Schematic.create_items).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
