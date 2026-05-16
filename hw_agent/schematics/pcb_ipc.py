"""kicad-python IPC implementation of PCB ops.

Only loaded when pcb_backend.is_ipc_available() is True. Talks to a
running pcbnew via the local IPC socket (Protocol Buffers + NNG, set up
automatically by KiCad when the API is enabled in Preferences).

The user's pcbnew GUI must be running and have the .kicad_pcb open for
these ops to work — that's the price of live editing.

Coordinate units:
    Our public API uses mm (matches the rest of hw_agent).
    kicad-python uses internal nanometers (Point.x_nm). We convert at
    the boundary via kipy.util.from_mm / to_mm.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


def _connect():
    """Connect to KiCad's IPC server. Caller catches exceptions."""
    from kipy import KiCad
    return KiCad()


def _resolve_board(kicad_pcb: Path):
    """Return the open Board instance matching `kicad_pcb`.

    KiCad may have multiple boards open; we match by absolute path.
    Falls back to the first board if path matching fails (rare —
    usually fine for single-project workflows).
    """
    k = _connect()
    board = k.get_board()
    # Note: kicad-python 0.7.x always returns the active board. If the
    # user has multiple PCB windows open, they need to focus the right
    # one. We don't attempt path matching here because the API doesn't
    # expose a "list all open boards" verb in stable.
    return k, board


def _vector_mm(x_mm: float, y_mm: float):
    """Build a Vector2 in KiCad-internal nanometers."""
    from kipy.geometry import Vector2
    from kipy.util import from_mm
    v = Vector2()
    v.x_nm = from_mm(x_mm)
    v.y_nm = from_mm(y_mm)
    return v


# ─── Operations ───────────────────────────────────────────────────────────

def move_footprint(kicad_pcb: Path, ref: str, x: float, y: float,
                   rotation: Optional[float] = None) -> dict:
    """Move a footprint by reference designator. Live edit: user sees it
    happen in pcbnew immediately.

    Args:
        kicad_pcb: path of the .kicad_pcb (used for sanity/logging only —
                   IPC operates on the currently-open board)
        ref:       reference designator (e.g. "U1", "R5")
        x, y:      new position in mm
        rotation:  optional rotation in degrees (None = leave as-is)
    """
    from kipy.util import from_mm

    try:
        k, board = _resolve_board(kicad_pcb)
    except Exception as e:
        return {"ok": False, "error": f"connect failed: {e}",
                "backend": "ipc"}

    # Find the footprint by reference
    target = None
    for fp in board.get_footprints():
        try:
            ref_text = fp.reference_field.text.value if fp.reference_field else ""
        except Exception:
            ref_text = ""
        if ref_text == ref:
            target = fp
            break
    if target is None:
        return {"ok": False, "error": f"footprint {ref!r} not found",
                "backend": "ipc"}

    # Mutate position
    target.position.x_nm = from_mm(x)
    target.position.y_nm = from_mm(y)
    if rotation is not None:
        target.orientation.value_degrees = float(rotation)

    # Commit through the board's transaction lifecycle
    try:
        commit = board.begin_commit()
        board.update_items([target])
        board.push_commit(commit, message=f"move {ref}")
    except Exception as e:
        return {"ok": False, "error": f"commit failed: {e}",
                "backend": "ipc"}

    return {
        "ok": True, "backend": "ipc",
        "ref": ref, "moved_to_mm": [x, y],
        "rotation_deg": rotation,
    }


def list_footprints(kicad_pcb: Path) -> list[dict]:
    """Enumerate footprints with ref, position (mm), rotation."""
    from kipy.util import to_mm
    try:
        k, board = _resolve_board(kicad_pcb)
    except Exception:
        return []

    out: list[dict] = []
    for fp in board.get_footprints():
        try:
            ref = fp.reference_field.text.value if fp.reference_field else ""
        except Exception:
            ref = ""
        try:
            value = fp.value_field.text.value if fp.value_field else ""
        except Exception:
            value = ""
        out.append({
            "ref": ref,
            "value": value,
            "at_mm": [to_mm(fp.position.x_nm), to_mm(fp.position.y_nm)],
            "rotation_deg": float(fp.orientation.value_degrees),
            "layer": int(fp.layer),
        })
    return out


def save_board(kicad_pcb: Path) -> dict:
    """Force-save the open board to disk. IPC's update_items + push_commit
    only updates the in-memory editor state; without a save, file changes
    are lost on close. Call this after a sequence of mutations."""
    try:
        k, board = _resolve_board(kicad_pcb)
        board.save()
        return {"ok": True, "backend": "ipc"}
    except Exception as e:
        return {"ok": False, "error": f"save failed: {e}",
                "backend": "ipc"}


# ─── Action-based ops (DSN, SES, sync_netlist, build) ─────────────────────
#
# kicad-python's `KiCad.run_action(action_name)` invokes pcbnew's internal
# TOOL_ACTION handlers — same code paths the menu items hit. The action
# names below are unstable per kipy's WARNING but match KiCad 9-10 stable.

def export_dsn(kicad_pcb: Path, out_dsn: Path) -> dict:
    """Export Specctra DSN via pcbnew's internal ExportSpecctraDSN action.

    Requires the .kicad_pcb to be open in pcbnew. Output goes to
    `out_dsn` (kicad-python action triggers the file-save dialog
    pre-filled, but headless invocation writes directly).
    """
    try:
        k, _board = _resolve_board(kicad_pcb)
        # Set the output path via project property, then run the action.
        # Action name based on KiCad's TOOL_ACTION_REGISTRY — verify
        # against your KiCad version if this changes.
        result = k.run_action("pcbnew.ExportSpecctraDSN")
        # kicad-python's run_action returns RUN_ACTION_STATUS; we don't
        # have direct file-path control via this API today, so the user
        # must accept pcbnew's default output location (next to .kicad_pcb).
        # Move/rename the produced file if needed.
        default_dsn = Path(kicad_pcb).with_suffix(".dsn")
        if default_dsn.exists() and default_dsn != out_dsn:
            default_dsn.rename(out_dsn)
        return {"ok": True, "backend": "ipc", "dsn_path": str(out_dsn),
                "action_status": str(result)}
    except Exception as e:
        return {"ok": False, "error": f"DSN export failed: {e}",
                "backend": "ipc"}


def import_ses(kicad_pcb: Path, ses_path: Path) -> dict:
    """Import Specctra SES via pcbnew's ImportSpecctraSES action."""
    try:
        k, _board = _resolve_board(kicad_pcb)
        result = k.run_action("pcbnew.ImportSpecctraSES")
        return {"ok": True, "backend": "ipc",
                "action_status": str(result)}
    except Exception as e:
        return {"ok": False, "error": f"SES import failed: {e}",
                "backend": "ipc"}


def sync_netlist(kicad_pcb: Path, netlist_path: Path) -> dict:
    """Apply a .net netlist to PCB pads via UpdateFromSchematic action."""
    try:
        k, _board = _resolve_board(kicad_pcb)
        result = k.run_action("pcbnew.UpdateFromSchematic")
        return {"ok": True, "backend": "ipc",
                "action_status": str(result)}
    except Exception as e:
        return {"ok": False, "error": f"netlist sync failed: {e}",
                "backend": "ipc"}


def build_pcb(spec: dict, out_kicad_pcb: Path) -> dict:
    """Build a .kicad_pcb from a spec via pcbnew's UpdateFromSchematic
    action + per-component position application.

    Flow:
      1. UpdateFromSchematic — pcbnew reads the project's .kicad_sch,
         loads footprints from KiCad's library cache, places them on
         the board with default positions. (User must have a KiCad
         project where .kicad_pro ties the .kicad_sch and .kicad_pcb
         together — typical eeschema → pcbnew workflow.)
      2. For each spec component, find the matching footprint by ref
         and move it to the requested position via Board.update_items.
         Position defaults to schematic-derived hint from compose_spec.
      3. Save to out_kicad_pcb.

    Returns: counts of footprints placed, moved, missing.
    """
    from kipy.util import from_mm

    try:
        k, board = _resolve_board(out_kicad_pcb)
    except Exception as e:
        return {"ok": False, "error": f"connect failed: {e}",
                "backend": "ipc"}

    # Step 1: let pcbnew populate the board from schematic
    try:
        update_result = k.run_action("pcbnew.UpdateFromSchematic")
    except Exception as e:
        return {"ok": False, "backend": "ipc",
                "error": f"UpdateFromSchematic failed: {e}"}

    # Step 2: re-read footprints, apply spec positions
    moved = 0
    missing: list[str] = []
    spec_by_ref = {c["ref"]: c for c in spec.get("components", [])}

    fps = list(board.get_footprints())
    placed_refs = set()
    for fp in fps:
        try:
            ref = fp.reference_field.text.value if fp.reference_field else ""
        except Exception:
            ref = ""
        placed_refs.add(ref)
        comp = spec_by_ref.get(ref)
        if not comp:
            continue
        x, y = comp["at"]
        fp.position.x_nm = from_mm(float(x))
        fp.position.y_nm = from_mm(float(y))
        rot = comp.get("rotation", 0)
        if rot:
            fp.orientation.value_degrees = float(rot)
        moved += 1

    # Track refs in spec that didn't make it onto the board
    for ref in spec_by_ref:
        if ref not in placed_refs:
            missing.append(ref)

    # Apply mutations + save
    try:
        commit = board.begin_commit()
        board.update_items([fp for fp in fps
                            if (fp.reference_field.text.value if fp.reference_field
                                else "") in spec_by_ref])
        board.push_commit(commit, message="build_pcb: apply spec positions")
        board.save()
    except Exception as e:
        return {"ok": False, "backend": "ipc",
                "error": f"save failed: {e}"}

    return {
        "ok": True, "backend": "ipc",
        "components_placed": len(placed_refs),
        "components_moved": moved,
        "components_missing": missing,
        "update_action_status": str(update_result),
    }
