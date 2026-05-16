"""kicad-python IPC implementation of schematic ops.

Loaded only when sch_backend.is_ipc_available() is True. Talks to a
running eeschema via the local IPC socket (Protocol Buffers + NNG, set
up by KiCad when the API is enabled in Preferences).

Mirrors the shape of pcb_ipc.py so the dispatcher pattern is symmetric.
The user must have eeschema running with the .kicad_sch open for live
edits to land.

Coordinate units:
    Public API uses mm. kicad-python uses internal nanometers
    (Vector2.x_nm). We convert at the boundary via kipy.util.from_mm /
    to_mm.

Coverage: every verb that `kipy.schematic.Schematic` exposes maps to a
function here, so the live_* MCP layer is symmetric with what kipy
offers. The one explicit gap is symbol creation — `add_symbol` needs
library symbol resolution (which library, which symbol within it) and
that's its own subproject, deferred until we test against eeschema.

Reads (all return dict {ok, backend, count, items}):
    list_symbols, list_lines, list_labels, list_text, list_shapes,
    list_images, list_groups, list_sheet_symbols
    get_netlist, get_hierarchy, get_page_settings, get_title_block,
    get_as_string, get_selection_as_string

Writes (return dict {ok, backend, ...}):
    move_symbol, set_value, set_footprint
    add_wire, add_bus, add_junction, add_no_connect
    add_text, add_label
    set_page_settings, set_title_block

Lifecycle:
    save_schematic, save_as, revert, remove_by_id

Deferred:
    add_symbol — lib_id resolution
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


def _connect():
    """Connect to KiCad's IPC server.

    Internal helper for `_resolve_schematic` — never call directly.
    Both this and `_resolve_schematic` raise on failure (kipy
    `ConnectionError`, `ApiError`); the callers in this module wrap
    operations in try/except and route to `_err()` for a structured
    response.
    """
    from kipy import KiCad
    return KiCad()


def _resolve_schematic(kicad_sch: Path):
    """Return (KiCad, Schematic) for the open schematic.

    Like pcb_ipc, kipy 0.8.0.dev currently exposes only the active
    schematic. If the user has multiple eeschema windows open they
    need to focus the right one. Raises on failure — caller catches.
    """
    k = _connect()
    sch = k.get_schematic()
    return k, sch


def _vector_mm(x_mm: float, y_mm: float):
    from kipy.geometry import Vector2
    from kipy.util import from_mm
    v = Vector2()
    v.x_nm = from_mm(x_mm)
    v.y_nm = from_mm(y_mm)
    return v


def _ref_of(sym) -> str:
    """Pull the reference designator off a SchematicSymbolInstance."""
    try:
        return sym.reference_field.text.value if sym.reference_field else ""
    except Exception:
        return ""


def _value_of(sym) -> str:
    try:
        return sym.value_field.text.value if sym.value_field else ""
    except Exception:
        return ""


# ─── Operations ───────────────────────────────────────────────────────────

def list_symbols(kicad_sch: Path) -> list[dict]:
    """Enumerate symbols in the open schematic — ref, position (mm), value.

    Raises on connection errors (timeout, no schematic loaded). Caller
    is expected to catch and convert to a user-friendly message — the
    MCP wrapper does that via `_no_schematic_open`."""
    from kipy.util import to_mm
    _, sch = _resolve_schematic(kicad_sch)

    out: list[dict] = []
    for sym in sch.get_symbols():
        try:
            x_mm = to_mm(sym.position.x)
            y_mm = to_mm(sym.position.y)
        except Exception:
            x_mm = y_mm = 0.0
        out.append({
            "ref": _ref_of(sym),
            "value": _value_of(sym),
            "at_mm": [x_mm, y_mm],
            "id": _id_str(sym),
        })
    return out


def move_symbol(kicad_sch: Path, ref: str, x: float, y: float) -> dict:
    """Move a symbol to a new (x, y) in mm. Live — user watches it move."""
    from kipy.geometry import Vector2

    try:
        _, sch = _resolve_schematic(kicad_sch)
    except Exception as e:
        return {"ok": False, "error": f"connect failed: {e}",
                "backend": "ipc"}

    target = next((s for s in sch.get_symbols() if _ref_of(s) == ref), None)
    if target is None:
        return {"ok": False, "error": f"symbol {ref!r} not found in open schematic",
                "backend": "ipc"}

    # The wrapper's position getter returns a fresh Vector2; mutating
    # `position.x` would hit the copy, not the proto. Assign a new
    # Vector2 through the setter, which CopyFrom's into the proto.
    target.position = Vector2.from_xy_mm(x, y)

    try:
        commit = sch.begin_commit()
        sch.update_items([target])
        sch.push_commit(commit, message=f"move {ref}")
    except Exception as e:
        return {"ok": False, "error": f"commit failed: {e}",
                "backend": "ipc"}

    return {"ok": True, "backend": "ipc", "ref": ref,
            "moved_to_mm": [x, y]}


def add_wire(kicad_sch: Path,
             x1_mm: float, y1_mm: float,
             x2_mm: float, y2_mm: float) -> dict:
    """Add an electrical wire (SLT_WIRE) from (x1,y1) to (x2,y2) in mm.
    Live — appears in eeschema immediately on commit.

    Note: KiCad expects orthogonal wires (horizontal or vertical). A
    diagonal wire renders fine but won't connect cleanly to pin
    endpoints in DRC. We don't auto-elbow here; that's the caller's
    job (or use the file-based add_wire which handles it).
    """
    from kipy.schematic_types import SchematicLine
    from kipy.proto.schematic.schematic_types_pb2 import (
        SchematicLine as PbLine, SchematicLineType,
    )
    from kipy.util import from_mm

    try:
        _, sch = _resolve_schematic(kicad_sch)
    except Exception as e:
        return {"ok": False, "error": f"connect failed: {e}",
                "backend": "ipc"}

    pb = PbLine()
    pb.start.x_nm = from_mm(x1_mm)
    pb.start.y_nm = from_mm(y1_mm)
    pb.end.x_nm = from_mm(x2_mm)
    pb.end.y_nm = from_mm(y2_mm)
    pb.type = SchematicLineType.SLT_WIRE
    line = SchematicLine(proto=pb)

    try:
        commit = sch.begin_commit()
        sch.create_items([line])
        sch.push_commit(commit,
                        message=f"add wire ({x1_mm},{y1_mm})-({x2_mm},{y2_mm})")
    except Exception as e:
        return {"ok": False, "error": f"commit failed: {e}",
                "backend": "ipc"}

    return {"ok": True, "backend": "ipc",
            "from_mm": [x1_mm, y1_mm], "to_mm": [x2_mm, y2_mm]}


def save_schematic(kicad_sch: Path) -> dict:
    """Force-save the open .kicad_sch.

    IPC's commit lifecycle updates eeschema's in-memory state but doesn't
    write the file by itself — call this after a sequence of live edits
    to persist.
    """
    try:
        _, sch = _resolve_schematic(kicad_sch)
    except Exception as e:
        return {"ok": False, "error": f"connect failed: {e}",
                "backend": "ipc"}

    try:
        sch.save()
    except Exception as e:
        return {"ok": False, "error": f"save failed: {e}",
                "backend": "ipc"}

    return {"ok": True, "backend": "ipc", "path": str(kicad_sch)}


# ─── Generic read helpers ──────────────────────────────────────────────────

def _err(stage: str, e: Exception) -> dict:
    return {"ok": False, "error": f"{stage} failed: {e}", "backend": "ipc"}


def _safe_pos(item) -> list[float]:
    """Best-effort (x_mm, y_mm) extraction. Some item types use
    `position`, others `start`/`end`, others have no position at all.

    Returns [] when the item has no recognised position attribute or
    the access raised — callers check `if pos:` before reading; an
    empty list is the explicit "no position" signal, not an error."""
    from kipy.util import to_mm
    for attr in ("position", "start"):
        if hasattr(item, attr):
            try:
                v = getattr(item, attr)
                return [to_mm(v.x), to_mm(v.y)]
            except Exception:
                continue
    return []


def _id_str(item) -> str:
    """Extract just the UUID string from a kipy item's id (KIID proto)."""
    try:
        if not hasattr(item, "id"):
            return ""
        if hasattr(item.id, "value"):
            return item.id.value
        return str(item.id)
    except Exception:
        return ""


def _item_summary(item) -> dict:
    """Type-aware item summary used by all list_* readers."""
    from kipy.util import to_mm
    out = {"id": _id_str(item), "type": type(item).__name__}
    pos = _safe_pos(item)
    if pos:
        out["at_mm"] = pos
    # SchematicLine: also expose end + line type
    if hasattr(item, "end"):
        try:
            out["end_mm"] = [to_mm(item.end.x), to_mm(item.end.y)]
        except Exception:
            pass
    if hasattr(item, "type"):
        try:
            t = item.type
            out["line_type"] = int(t) if t is not None else None
        except Exception:
            pass
    # Symbols: ref + value (each helper has its own try/except, so call once).
    ref = _ref_of(item)
    if ref:
        out["ref"] = ref
    value = _value_of(item)
    if value:
        out["value"] = value
    # Labels / text: text content
    if hasattr(item, "text"):
        try:
            out["text"] = (item.text.value if hasattr(item.text, "value")
                           else str(item.text))
        except Exception:
            pass
    return out


# ─── Typed reads ──────────────────────────────────────────────────────────

def list_lines(kicad_sch: Path) -> dict:
    """All schematic lines (wires + buses + graphic)."""
    try:
        _, sch = _resolve_schematic(kicad_sch)
    except Exception as e:
        return _err("connect", e)
    items = [_item_summary(x) for x in sch.get_lines()]
    return {"ok": True, "backend": "ipc", "count": len(items), "items": items}


def list_labels(kicad_sch: Path) -> dict:
    try:
        _, sch = _resolve_schematic(kicad_sch)
    except Exception as e:
        return _err("connect", e)
    items = [_item_summary(x) for x in sch.get_labels()]
    return {"ok": True, "backend": "ipc", "count": len(items), "items": items}


def list_text(kicad_sch: Path) -> dict:
    try:
        _, sch = _resolve_schematic(kicad_sch)
    except Exception as e:
        return _err("connect", e)
    items = [_item_summary(x) for x in sch.get_text()]
    return {"ok": True, "backend": "ipc", "count": len(items), "items": items}


def list_shapes(kicad_sch: Path) -> dict:
    try:
        _, sch = _resolve_schematic(kicad_sch)
    except Exception as e:
        return _err("connect", e)
    items = [_item_summary(x) for x in sch.get_shapes()]
    return {"ok": True, "backend": "ipc", "count": len(items), "items": items}


def list_images(kicad_sch: Path) -> dict:
    try:
        _, sch = _resolve_schematic(kicad_sch)
    except Exception as e:
        return _err("connect", e)
    items = [_item_summary(x) for x in sch.get_images()]
    return {"ok": True, "backend": "ipc", "count": len(items), "items": items}


def list_groups(kicad_sch: Path) -> dict:
    try:
        _, sch = _resolve_schematic(kicad_sch)
    except Exception as e:
        return _err("connect", e)
    items = [_item_summary(x) for x in sch.get_groups()]
    return {"ok": True, "backend": "ipc", "count": len(items), "items": items}


def list_sheet_symbols(kicad_sch: Path) -> dict:
    try:
        _, sch = _resolve_schematic(kicad_sch)
    except Exception as e:
        return _err("connect", e)
    items = [_item_summary(x) for x in sch.get_sheet_symbols()]
    return {"ok": True, "backend": "ipc", "count": len(items), "items": items}


# KiCadObjectType IDs we use for typed reads via get_items().
# Source: kipy/proto/common/types/enums.proto — KiCadObjectType enum.
_KOT_SCH_JUNCTION = 0x13
_KOT_SCH_NO_CONNECT = 0x14


def list_junctions(kicad_sch: Path) -> dict:
    """List junction dots (wire-intersection markers).

    Note: KiCad may auto-prune junctions that aren't at a real wire
    intersection — `add_junction` followed by `list_junctions` may
    return fewer items than added.
    """
    try:
        _, sch = _resolve_schematic(kicad_sch)
        items = [_item_summary(x) for x in sch.get_items(_KOT_SCH_JUNCTION)]
    except Exception as e:
        return _err("connect", e)
    return {"ok": True, "backend": "ipc", "count": len(items), "items": items}


def list_no_connects(kicad_sch: Path) -> dict:
    """List no-connect markers (X) flagging unused pins."""
    try:
        _, sch = _resolve_schematic(kicad_sch)
        items = [_item_summary(x) for x in sch.get_items(_KOT_SCH_NO_CONNECT)]
    except Exception as e:
        return _err("connect", e)
    return {"ok": True, "backend": "ipc", "count": len(items), "items": items}


def get_page_settings(kicad_sch: Path) -> dict:
    try:
        _, sch = _resolve_schematic(kicad_sch)
        ps = sch.get_page_settings()
    except Exception as e:
        return _err("get_page_settings", e)
    # Enums come through as ints; convert to their proto names (e.g.
    # PS_A4, PO_LANDSCAPE) for human readability.
    from kipy.proto.common.types import base_types_pb2
    out: dict = {"ok": True, "backend": "ipc"}
    if hasattr(ps, "page_size"):
        try:
            out["page_size"] = base_types_pb2.PageSize.Name(ps.page_size)
        except Exception:
            out["page_size"] = int(ps.page_size)
    if hasattr(ps, "orientation"):
        try:
            out["orientation"] = base_types_pb2.PageOrientation.Name(ps.orientation)
        except Exception:
            out["orientation"] = int(ps.orientation)
    if hasattr(ps, "drawing_sheet") and ps.drawing_sheet:
        out["drawing_sheet"] = str(ps.drawing_sheet)
    return out


def get_title_block(kicad_sch: Path) -> dict:
    try:
        _, sch = _resolve_schematic(kicad_sch)
        tb = sch.get_title_block()
    except Exception as e:
        return _err("get_title_block", e)
    # tb.comments is `dict[int, str]` — keep that shape, but stringify
    # keys for clean JSON serialization.
    raw_comments = getattr(tb, "comments", {}) or {}
    comments = {str(k): v for k, v in raw_comments.items() if v}
    return {
        "ok": True, "backend": "ipc",
        "title": getattr(tb, "title", "") or "",
        "date": getattr(tb, "date", "") or "",
        "revision": getattr(tb, "revision", "") or "",
        "company": getattr(tb, "company", "") or "",
        "comments": comments,
    }


def get_as_string(kicad_sch: Path) -> dict:
    """Full schematic as text — useful for diff or grep."""
    try:
        _, sch = _resolve_schematic(kicad_sch)
        s = sch.get_as_string()
    except Exception as e:
        return _err("get_as_string", e)
    return {"ok": True, "backend": "ipc", "schematic_text": s,
            "length": len(s)}


def get_selection_as_string(kicad_sch: Path) -> dict:
    """Whatever the user has selected in eeschema, as text."""
    try:
        _, sch = _resolve_schematic(kicad_sch)
        s = sch.get_selection_as_string()
    except Exception as e:
        return _err("get_selection_as_string", e)
    return {"ok": True, "backend": "ipc", "selection_text": s,
            "length": len(s)}


# ─── Symbol field writes ───────────────────────────────────────────────────

def set_value(kicad_sch: Path, ref: str, value: str) -> dict:
    """Set the Value field on a symbol identified by reference."""
    try:
        _, sch = _resolve_schematic(kicad_sch)
    except Exception as e:
        return _err("connect", e)
    target = next((s for s in sch.get_symbols() if _ref_of(s) == ref), None)
    if target is None:
        return {"ok": False, "backend": "ipc",
                "error": f"symbol {ref!r} not found"}
    try:
        target.value_field.text.value = value
        commit = sch.begin_commit()
        sch.update_items([target])
        sch.push_commit(commit, message=f"set {ref}.value={value}")
    except Exception as e:
        return _err("commit", e)
    return {"ok": True, "backend": "ipc", "ref": ref, "value": value}


def set_footprint(kicad_sch: Path, ref: str, footprint: str) -> dict:
    """Set the Footprint field on a symbol identified by reference."""
    try:
        _, sch = _resolve_schematic(kicad_sch)
    except Exception as e:
        return _err("connect", e)
    target = next((s for s in sch.get_symbols() if _ref_of(s) == ref), None)
    if target is None:
        return {"ok": False, "backend": "ipc",
                "error": f"symbol {ref!r} not found"}
    try:
        target.footprint_field.text.value = footprint
        commit = sch.begin_commit()
        sch.update_items([target])
        sch.push_commit(commit, message=f"set {ref}.footprint={footprint}")
    except Exception as e:
        return _err("commit", e)
    return {"ok": True, "backend": "ipc", "ref": ref, "footprint": footprint}


# ─── Item creation (typed) ─────────────────────────────────────────────────

def add_bus(kicad_sch: Path,
            x1_mm: float, y1_mm: float,
            x2_mm: float, y2_mm: float) -> dict:
    """Add a bus line. Same shape as add_wire but type=BUS."""
    from kipy.schematic_types import SchematicLine
    from kipy.proto.schematic.schematic_types_pb2 import (
        SchematicLine as PbLine, SchematicLineType,
    )
    from kipy.util import from_mm
    try:
        _, sch = _resolve_schematic(kicad_sch)
    except Exception as e:
        return _err("connect", e)
    pb = PbLine()
    pb.start.x_nm = from_mm(x1_mm)
    pb.start.y_nm = from_mm(y1_mm)
    pb.end.x_nm = from_mm(x2_mm)
    pb.end.y_nm = from_mm(y2_mm)
    pb.type = SchematicLineType.SLT_BUS
    line = SchematicLine(proto=pb)
    try:
        commit = sch.begin_commit()
        sch.create_items([line])
        sch.push_commit(commit, message=f"add bus")
    except Exception as e:
        return _err("commit", e)
    return {"ok": True, "backend": "ipc",
            "from_mm": [x1_mm, y1_mm], "to_mm": [x2_mm, y2_mm], "type": "bus"}


def add_junction(kicad_sch: Path, x_mm: float, y_mm: float) -> dict:
    """Place a junction dot at (x, y) — used at wire intersections."""
    from kipy.schematic_types import Junction
    from kipy.proto.schematic.schematic_types_pb2 import Junction as PbJ
    from kipy.util import from_mm
    try:
        _, sch = _resolve_schematic(kicad_sch)
    except Exception as e:
        return _err("connect", e)
    pb = PbJ()
    pb.position.x_nm = from_mm(x_mm)
    pb.position.y_nm = from_mm(y_mm)
    j = Junction(proto=pb)
    try:
        commit = sch.begin_commit()
        sch.create_items([j])
        sch.push_commit(commit, message=f"add junction")
    except Exception as e:
        return _err("commit", e)
    return {"ok": True, "backend": "ipc", "at_mm": [x_mm, y_mm]}


def add_no_connect(kicad_sch: Path, x_mm: float, y_mm: float) -> dict:
    """Place a no-connect (X) marker — flags an unused pin."""
    from kipy.schematic_types import NoConnectMarker
    from kipy.proto.schematic.schematic_types_pb2 import (
        NoConnectMarker as PbNC,
    )
    from kipy.util import from_mm
    try:
        _, sch = _resolve_schematic(kicad_sch)
    except Exception as e:
        return _err("connect", e)
    pb = PbNC()
    pb.position.x_nm = from_mm(x_mm)
    pb.position.y_nm = from_mm(y_mm)
    nc = NoConnectMarker(proto=pb)
    try:
        commit = sch.begin_commit()
        sch.create_items([nc])
        sch.push_commit(commit, message=f"add no_connect")
    except Exception as e:
        return _err("commit", e)
    return {"ok": True, "backend": "ipc", "at_mm": [x_mm, y_mm]}


def add_text(kicad_sch: Path, x_mm: float, y_mm: float, text: str) -> dict:
    """Add a free-text annotation. SchematicText embeds a Text message
    that owns both the position and the string."""
    from kipy.schematic_types import SchematicText
    from kipy.proto.schematic.schematic_types_pb2 import (
        SchematicText as PbText,
    )
    from kipy.util import from_mm
    try:
        _, sch = _resolve_schematic(kicad_sch)
    except Exception as e:
        return _err("connect", e)
    pb = PbText()
    pb.text.position.x_nm = from_mm(x_mm)
    pb.text.position.y_nm = from_mm(y_mm)
    pb.text.text = text
    t = SchematicText(proto=pb)
    try:
        commit = sch.begin_commit()
        sch.create_items([t])
        sch.push_commit(commit, message=f"add text {text!r}")
    except Exception as e:
        return _err("commit", e)
    return {"ok": True, "backend": "ipc", "at_mm": [x_mm, y_mm], "text": text}


def add_label(kicad_sch: Path, x_mm: float, y_mm: float, text: str) -> dict:
    """Add a local net label at (x, y). LocalLabel has top-level
    position but the string lives in the inner Text proto's `.text`
    field (not `.value`)."""
    from kipy.schematic_types import LocalLabel
    from kipy.proto.schematic.schematic_types_pb2 import (
        LocalLabel as PbLL,
    )
    from kipy.util import from_mm
    try:
        _, sch = _resolve_schematic(kicad_sch)
    except Exception as e:
        return _err("connect", e)
    pb = PbLL()
    pb.position.x_nm = from_mm(x_mm)
    pb.position.y_nm = from_mm(y_mm)
    pb.text.text = text
    lbl = LocalLabel(proto=pb)
    try:
        commit = sch.begin_commit()
        sch.create_items([lbl])
        sch.push_commit(commit, message=f"add label {text!r}")
    except Exception as e:
        return _err("commit", e)
    return {"ok": True, "backend": "ipc", "at_mm": [x_mm, y_mm], "text": text}


# ─── Page / title block writes ─────────────────────────────────────────────

def set_page_settings(kicad_sch: Path, paper: str) -> dict:
    """Change paper size. Accepts A4 / A3 / US_LETTER (or full enum
    name PS_A4 / PS_A3 / PS_US_LETTER)."""
    from kipy.proto.common.types import base_types_pb2
    # Accept friendly inputs ("A3") or full enum names ("PS_A3").
    name = paper.upper().strip()
    if not name.startswith("PS_"):
        name = "PS_" + name.replace(" ", "_")
    try:
        enum_value = base_types_pb2.PageSize.Value(name)
    except ValueError:
        valid = [v for v in base_types_pb2.PageSize.keys() if v != "PS_UNKNOWN"]
        return {"ok": False, "backend": "ipc",
                "error": f"unknown paper size {paper!r}; valid: {valid}"}
    try:
        _, sch = _resolve_schematic(kicad_sch)
        ps = sch.get_page_settings()
        ps.page_size = enum_value
        sch.set_page_settings(ps)
    except Exception as e:
        return _err("set_page_settings", e)
    return {"ok": True, "backend": "ipc", "paper": name}


def set_title_block(kicad_sch: Path, *, title: Optional[str] = None,
                    company: Optional[str] = None,
                    revision: Optional[str] = None,
                    date: Optional[str] = None) -> dict:
    """Update title-block fields. Only the kwargs you pass are changed.

    Field names match TitleBlockInfo's proto: title, date, revision,
    company. (Not `rev` — that was a previous bug.)"""
    changed: dict[str, str] = {}
    try:
        _, sch = _resolve_schematic(kicad_sch)
        tb = sch.get_title_block()
        if title is not None:
            tb.title = title
            changed["title"] = title
        if company is not None:
            tb.company = company
            changed["company"] = company
        if revision is not None:
            tb.revision = revision
            changed["revision"] = revision
        if date is not None:
            tb.date = date
            changed["date"] = date
        sch.set_title_block(tb)
    except Exception as e:
        return _err("set_title_block", e)
    return {"ok": True, "backend": "ipc", "changed": changed}


# ─── Lifecycle ─────────────────────────────────────────────────────────────

def save_as(kicad_sch: Path, new_path: Path,
            overwrite: bool = False, include_project: bool = True) -> dict:
    """Save under a new filename."""
    try:
        _, sch = _resolve_schematic(kicad_sch)
        sch.save_as(str(new_path), overwrite=overwrite,
                    include_project=include_project)
    except Exception as e:
        return _err("save_as", e)
    return {"ok": True, "backend": "ipc", "new_path": str(new_path)}


def revert(kicad_sch: Path) -> dict:
    """Discard live edits and reload the last-saved state."""
    try:
        _, sch = _resolve_schematic(kicad_sch)
        sch.revert()
    except Exception as e:
        return _err("revert", e)
    return {"ok": True, "backend": "ipc"}


def remove_by_id(kicad_sch: Path, item_ids: list[str]) -> dict:
    """Remove items by KIID (UUID strings). Use list_* readers to find ids."""
    from kipy.proto.common.types.base_types_pb2 import KIID
    try:
        _, sch = _resolve_schematic(kicad_sch)
        kiids = []
        for s in item_ids:
            k = KIID()
            k.value = s
            kiids.append(k)
        commit = sch.begin_commit()
        sch.remove_items_by_id(kiids)
        sch.push_commit(commit, message=f"remove {len(kiids)} item(s)")
    except Exception as e:
        return _err("remove_by_id", e)
    return {"ok": True, "backend": "ipc", "removed": len(item_ids)}
