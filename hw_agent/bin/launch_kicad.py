#!/usr/bin/env python3
"""launch_kicad: open KiCad nightly + wait until IPC API server responds.

Usage:
    python -m hw_agent.bin.launch_kicad [--sch path/to/file.kicad_sch] [--timeout 30]

Behavior:
  1. If KiCad IPC already up → return immediately ("already running").
  2. Else, run `open -a "KiCad"` (macOS) to launch the app.
  3. If --sch given, also `open <path>` so eeschema loads that file.
  4. Poll kipy ping every 1 s for up to --timeout seconds.
  5. Exit 0 + "ready" once API responds; exit 1 + hint on timeout.

If KiCad launches but ping times out, the most common cause is the API
server preference being disabled. We print that hint explicitly.
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
import time
from pathlib import Path


def _ipc_up() -> tuple[bool, str]:
    """Return (ok, detail). Probes core IPC ping only.

    We deliberately do NOT import kipy.schematic here — newer KiCad nightlies
    may have proto-binding mismatches with the installed kicad-python (e.g.
    missing BusEntryType enum). That's a live-edit-mcp concern, not a
    launcher concern. Core ping is enough to confirm KiCad is reachable.
    """
    try:
        from kipy import KiCad  # type: ignore
    except Exception as e:
        return False, f"kipy import failed: {e}"
    try:
        KiCad().ping()
        return True, "ipc responding"
    except Exception as e:
        return False, f"ping failed: {type(e).__name__}: {e}"


def _schematic_protos_ok() -> tuple[bool, str]:
    """Secondary probe: live-edit tools need this to load. Optional."""
    try:
        from kipy import schematic  # noqa: F401
        return True, "schematic protos ok"
    except Exception as e:
        return False, f"schematic protos mismatch: {e}"


def _launch(app: str, sch: Path | None) -> None:
    sysname = platform.system()
    if sysname == "Darwin":
        subprocess.Popen(["open", "-a", app], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if sch is not None:
            time.sleep(0.5)  # let app come up before opening doc
            subprocess.Popen(["open", str(sch)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif sysname == "Linux":
        subprocess.Popen(["kicad"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if sch is not None:
            time.sleep(0.5)
            subprocess.Popen(["xdg-open", str(sch)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        print(f"unsupported platform: {sysname}. Open KiCad manually.", file=sys.stderr)
        sys.exit(2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sch", type=Path, default=None, help="schematic file to open")
    ap.add_argument("--app", default="KiCad", help="application name (macOS)")
    ap.add_argument("--timeout", type=int, default=30, help="seconds to wait for IPC")
    ap.add_argument("--silent", action="store_true", help="suppress progress dots")
    args = ap.parse_args()

    if args.sch is not None and not args.sch.exists():
        print(f"schematic not found: {args.sch}", file=sys.stderr)
        return 2

    ok, detail = _ipc_up()
    if ok:
        print(f"✓ KiCad IPC already up ({detail})")
        sch_ok, sch_detail = _schematic_protos_ok()
        if not sch_ok:
            print(
                f"⚠ schematic protos: {sch_detail}\n"
                "  live-edit-mcp tools will fail. Either:\n"
                "    - downgrade KiCad to stable 9.x (matches kicad-python 0.7.1)\n"
                "    - build kicad-python from KiCad nightly source\n"
                "  (kipy.KiCad().ping() still works; designer-mcp render/export OK.)",
                file=sys.stderr,
            )
        if args.sch is not None:
            print(f"  open this in eeschema: {args.sch}")
        return 0 if sch_ok else 3

    print(f"launching {args.app} …", file=sys.stderr)
    _launch(args.app, args.sch)

    deadline = time.time() + args.timeout
    last_detail = detail
    while time.time() < deadline:
        time.sleep(1)
        if not args.silent:
            print(".", end="", flush=True, file=sys.stderr)
        ok, last_detail = _ipc_up()
        if ok:
            if not args.silent:
                print("", file=sys.stderr)
            print(f"✓ KiCad ready ({last_detail})")
            return 0

    if not args.silent:
        print("", file=sys.stderr)
    print(f"✗ KiCad IPC did not come up within {args.timeout}s.", file=sys.stderr)
    print(f"  last probe: {last_detail}", file=sys.stderr)
    print(
        "  fix:\n"
        "    1. KiCad → Preferences → API server → enable\n"
        "    2. restart KiCad\n"
        "    3. re-run this command",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
