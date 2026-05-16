"""live-edit-mcp — IPC live editing of an open KiCad schematic.

Standalone MCP server. Every tool here REQUIRES KiCad nightly running
with eeschema open and Preferences → API server enabled. There is **no
fallback to file-based editing** — if KiCad isn't reachable, every
tool returns a canonical error telling the agent to either open KiCad
or switch to the file-based `designer-mcp` (which has its own `add_*`
authoring tools).

This split is intentional. The agent decides which MCP fits the task:
    user says "I'm in eeschema, move U1 to (80, 60)"        → live-edit-mcp
    user says "generate a buck schematic from scratch"       → designer-mcp
    automated CI / batch / headless                          → designer-mcp

Run:    live-edit-mcp                          # via console script
or:     python -m hw_agent.live_edit_mcp        # via module

MCP config (.mcp.json):
    {
      "live-edit-mcp": {
        "type": "stdio",
        "command": "live-edit-mcp",
        "args": []
      }
    }

Tool families:
    live_list_*      — typed reads (symbols, lines, labels, text, …)
    live_get_*       — netlist, hierarchy, page settings, title block
    live_add_*       — wire, bus, junction, no_connect, text, label
    live_move_*      — symbol position
    live_set_*       — value, footprint, page settings, title block
    lifecycle        — save_schematic, save_as, revert, remove_by_id

Known gap: live_add_symbol — needs library symbol resolution
(which lib + which symbol within); deferred until tested against a
running eeschema.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP, Image


mcp = FastMCP(
    "live-edit",
    instructions=(
        "IPC live editing of an open KiCad schematic. Every tool "
        "REQUIRES eeschema running with Preferences → API server "
        "enabled. There is no fallback — if KiCad isn't open, calls "
        "return an explicit error.\n\n"
        "**This MCP edits EXISTING items.** It cannot CREATE new "
        "symbols today (KiCad nightly 10.99 accepts the create_items "
        "request but the handler doesn't actually place the symbol). "
        "**To add new symbols, use designer-mcp's `add_capacitor`, "
        "`add_resistor`, `add_inductor`, `add_ic`, `add_power`, "
        "`add_ground` tools** — they edit the .kicad_sch file directly. "
        "After designer-mcp writes the file, ask the user to **File → "
        "Reload** in eeschema so the new symbols appear in their view.\n\n"
        "What live-edit-mcp CAN do today:\n"
        "  • Read everything (symbols, lines, labels, text, shapes, "
        "images, groups, sheets, netlist, hierarchy, page settings, "
        "title block). Reads see live in-memory state.\n"
        "  • Mutate existing symbols: move, set_value, set_footprint.\n"
        "  • Add wires, buses, junctions, no-connects, text, labels.\n"
        "  • Set page settings, title block.\n"
        "  • Remove items by KIID.\n\n"
        "**KiCad nightly 10.99 IPC handlers that aren't wired yet** "
        "(this MCP surfaces clear errors when called):\n"
        "  • SaveDocument → ask user to press Ctrl+S (⌘S on macOS).\n"
        "  • RevertDocument → ask user to press Ctrl+Z (⌘Z on macOS).\n"
        "  • SaveCopyOfDocument → ask user to use File → Save As.\n"
        "  • RunAction → no shortcut/menu actions over IPC.\n"
        "  • create_items for SchematicSymbolInstance → use "
        "designer-mcp's add_* tools instead.\n\n"
        "**After every mutation:**\n"
        "  1. Use live_list_* / live_get_netlist to verify in-memory "
        "state.\n"
        "  2. Tell the user to press Ctrl+S in eeschema before any "
        "tool that reads from disk gets called.\n"
        "  3. Tell the user to press Ctrl+Z to undo."
    ),
)


# ─── IPC reachability probe + canonical error ──────────────────────────────

def _ipc_up() -> bool:
    """Probe: kipy.schematic protos present + KiCad responds to ping.

    KiCad sets KICAD_API_SOCKET only for processes it launches as
    plugins; standalone scripts (like this MCP server) get the socket
    path from kipy's default (`/tmp/kicad/api.sock` on macOS/Linux).
    So we don't gate on the env var — only on a live ping."""
    try:
        from kipy import KiCad
        from kipy import schematic  # noqa: F401 — proves protos generated
        KiCad().ping()
        return True
    except Exception:
        return False


def _live_unavailable() -> dict:
    """Single canonical error every tool returns when IPC is down."""
    return {
        "ok": False,
        "error": (
            "KiCad IPC not reachable. To use a live_* tool: "
            "(1) launch KiCad nightly 10.99+, "
            "(2) open the .kicad_sch in eeschema, "
            "(3) Preferences → API server: Enable. "
            "Or, for headless authoring, switch to designer-mcp's "
            "add_* tools (add_capacitor, add_ic, add_wire, add_power, "
            "add_ground, set_value, set_footprint) which edit the "
            ".kicad_sch file directly."
        ),
        "hint": "live-edit-mcp is IPC-only by design; no fallback",
    }


def _no_schematic_open(detail: str = "") -> dict:
    """KiCad responds (IPC is up) but no schematic is loaded in
    eeschema, or the schematic isn't reachable. Returned when the
    server times out / errors on `get_schematic()`, OR when reads
    return zero items across the board."""
    return {
        "ok": False,
        "error": (
            "KiCad IPC is up but no schematic is reachable. Are you "
            "sure eeschema is open with a .kicad_sch loaded? "
            "Check: (1) eeschema window is visible (not just the "
            "KiCad project window), (2) File → Open shows the "
            "schematic you intend to edit, (3) the schematic isn't "
            "still loading."
            + (f" KiCad reported: {detail}" if detail else "")
        ),
        "hint": "open eeschema and load a .kicad_sch, then retry",
    }


def _empty_result_hint() -> dict:
    """Live read returned 0 items + IPC ping ok — likely eeschema has
    a blank/new schematic loaded, not the one the user means."""
    return {
        "ok": True, "backend": "ipc",
        "count": 0, "items": [],
        "warning": (
            "0 items found, but KiCad is reachable. eeschema may have "
            "a blank schematic loaded, or a different one than you "
            "expect. Check the eeschema title bar — does it show the "
            "schematic you meant to open? File → Open if not."
        ),
    }


_PERSIST_HINT = (
    "\n\n_⚠ press Ctrl+S in eeschema (⌘S on macOS) to persist to "
    "disk — KiCad nightly 10.99 has no SaveDocument IPC handler yet, "
    "so edits live in eeschema's memory until the user saves._"
)


def _live_sch_render_after_edit(
    kicad_sch: Path, zoom_to: Optional[str], with_render: bool,
) -> tuple[Optional[str], Optional["Image"]]:
    """Optionally render after a live edit.

    **No auto-save** in this nightly: KiCad 10.99 has no
    `SaveDocument` IPC handler. Mutations live in eeschema's memory
    until the user presses Ctrl+S. Renders read from disk, so
    `with_render=True` against an unsaved edit shows pre-edit state.

    For the agent: prefer **IPC reads** (`live_list_*`,
    `live_get_netlist`) to verify success — those see the in-memory
    state. Use `with_render=True` only after the user has saved (or
    accept staleness).
    """
    if not with_render:
        return None, None
    try:
        from hw_agent.artifacts.schematics.render_focus import render_focused_png
        r = render_focused_png(kicad_sch, zoom_to=zoom_to)
        return (
            f"\n_SVG: `{r.get('svg_path', '?')}` "
            f"(may be pre-edit; Ctrl+S in eeschema to refresh)_",
            Image(path=str(r["png_path"])),
        )
    except Exception:
        return None, None


# ─── live_list_* (reads, one per kipy.Schematic.get_X) ─────────────────────

@mcp.tool()
def live_list_symbols(kicad_sch: str) -> dict:
    """[live/ipc] Enumerate symbols in the OPEN schematic.

    REQUIRES eeschema running with the .kicad_sch open + Preferences →
    Plugins → "Enable KiCad API". Sees the user's live (possibly
    uncommitted) edits — that's the reason to use this over
    designer-mcp's file-based reader.
    """
    if not _ipc_up():
        return _live_unavailable()
    from hw_agent.artifacts.schematics import sch_ipc
    try:
        syms = sch_ipc.list_symbols(Path(kicad_sch).resolve())
    except Exception as e:
        return _no_schematic_open(str(e))
    if not syms:
        return _empty_result_hint()
    return {"ok": True, "backend": "ipc",
            "count": len(syms), "symbols": syms}


@mcp.tool()
def live_list_lines(kicad_sch: str) -> dict:
    """[live/ipc] List all schematic lines (wires + buses + graphic).

    REQUIRES eeschema open. Each item has start_mm, end_mm, line_type
    (1=WIRE, 2=BUS, 3=GRAPHIC).
    """
    if not _ipc_up():
        return _live_unavailable()
    from hw_agent.artifacts.schematics import sch_ipc
    return sch_ipc.list_lines(Path(kicad_sch).resolve())


@mcp.tool()
def live_list_labels(kicad_sch: str) -> dict:
    """[live/ipc] List local net labels in the open schematic.

    REQUIRES eeschema open.
    """
    if not _ipc_up():
        return _live_unavailable()
    from hw_agent.artifacts.schematics import sch_ipc
    return sch_ipc.list_labels(Path(kicad_sch).resolve())


@mcp.tool()
def live_list_text(kicad_sch: str) -> dict:
    """[live/ipc] List free-text annotations + textboxes.

    REQUIRES eeschema open.
    """
    if not _ipc_up():
        return _live_unavailable()
    from hw_agent.artifacts.schematics import sch_ipc
    return sch_ipc.list_text(Path(kicad_sch).resolve())


@mcp.tool()
def live_list_shapes(kicad_sch: str) -> dict:
    """[live/ipc] List graphic shapes (rectangles, circles, polylines).

    REQUIRES eeschema open.
    """
    if not _ipc_up():
        return _live_unavailable()
    from hw_agent.artifacts.schematics import sch_ipc
    return sch_ipc.list_shapes(Path(kicad_sch).resolve())


@mcp.tool()
def live_list_images(kicad_sch: str) -> dict:
    """[live/ipc] List embedded raster images.

    REQUIRES eeschema open.
    """
    if not _ipc_up():
        return _live_unavailable()
    from hw_agent.artifacts.schematics import sch_ipc
    return sch_ipc.list_images(Path(kicad_sch).resolve())


@mcp.tool()
def live_list_groups(kicad_sch: str) -> dict:
    """[live/ipc] List groups (collections of related items).

    REQUIRES eeschema open.
    """
    if not _ipc_up():
        return _live_unavailable()
    from hw_agent.artifacts.schematics import sch_ipc
    return sch_ipc.list_groups(Path(kicad_sch).resolve())


@mcp.tool()
def live_list_sheet_symbols(kicad_sch: str) -> dict:
    """[live/ipc] List sub-sheet references (hierarchical sheet symbols).

    REQUIRES eeschema open.
    """
    if not _ipc_up():
        return _live_unavailable()
    from hw_agent.artifacts.schematics import sch_ipc
    return sch_ipc.list_sheet_symbols(Path(kicad_sch).resolve())


@mcp.tool()
def live_list_junctions(kicad_sch: str) -> dict:
    """[live/ipc] List junction dots (wire-intersection markers).

    REQUIRES eeschema open. KiCad may auto-prune junctions that aren't
    at a real wire intersection — adding one to a solo wire and then
    listing may show 0.
    """
    if not _ipc_up():
        return _live_unavailable()
    from hw_agent.artifacts.schematics import sch_ipc
    return sch_ipc.list_junctions(Path(kicad_sch).resolve())


@mcp.tool()
def live_list_no_connects(kicad_sch: str) -> dict:
    """[live/ipc] List no-connect (X) markers on unused pins.

    REQUIRES eeschema open.
    """
    if not _ipc_up():
        return _live_unavailable()
    from hw_agent.artifacts.schematics import sch_ipc
    return sch_ipc.list_no_connects(Path(kicad_sch).resolve())


# ─── live_get_* (other reads) ──────────────────────────────────────────────

@mcp.tool()
def live_get_netlist(kicad_sch: str) -> dict:
    """[live/ipc] Read the netlist from the OPEN schematic.

    REQUIRES eeschema open. **IPC-exclusive** — netlist comes from
    KiCad's in-memory model (handles hierarchical sheets, bus
    expansion, label resolution correctly). No file-based equivalent.
    """
    if not _ipc_up():
        return _live_unavailable()
    from kipy import KiCad
    try:
        sch = KiCad().get_schematic()
        return {"ok": True, "backend": "ipc",
                "netlist": str(sch.get_netlist())}
    except Exception as e:
        return _no_schematic_open(str(e))


@mcp.tool()
def live_get_hierarchy(kicad_sch: str) -> dict:
    """[live/ipc] Read the sheet hierarchy from the OPEN schematic.

    REQUIRES eeschema open. **IPC-exclusive**. Returns the tree of
    sheets (root → sub-sheets → ...).
    """
    if not _ipc_up():
        return _live_unavailable()
    from kipy import KiCad
    try:
        sch = KiCad().get_schematic()
        return {"ok": True, "backend": "ipc",
                "hierarchy": str(sch.get_hierarchy())}
    except Exception as e:
        return _no_schematic_open(str(e))


@mcp.tool()
def live_get_page_settings(kicad_sch: str) -> dict:
    """[live/ipc] Read page settings (paper size, orientation).

    REQUIRES eeschema open.
    """
    if not _ipc_up():
        return _live_unavailable()
    from hw_agent.artifacts.schematics import sch_ipc
    return sch_ipc.get_page_settings(Path(kicad_sch).resolve())


@mcp.tool()
def live_get_title_block(kicad_sch: str) -> dict:
    """[live/ipc] Read title-block fields (title, company, rev, date, …).

    REQUIRES eeschema open.
    """
    if not _ipc_up():
        return _live_unavailable()
    from hw_agent.artifacts.schematics import sch_ipc
    return sch_ipc.get_title_block(Path(kicad_sch).resolve())


@mcp.tool()
def live_get_as_string(kicad_sch: str) -> dict:
    """[live/ipc] Get the entire schematic as text.

    REQUIRES eeschema open. Useful for diff or grep against the live
    state, especially before saving.
    """
    if not _ipc_up():
        return _live_unavailable()
    from hw_agent.artifacts.schematics import sch_ipc
    return sch_ipc.get_as_string(Path(kicad_sch).resolve())


@mcp.tool()
def live_get_selection_as_string(kicad_sch: str) -> dict:
    """[live/ipc] Get whatever the user has selected in eeschema, as text.

    REQUIRES eeschema open. Useful for "what does this thing mean?"
    questions where the user has highlighted an item.
    """
    if not _ipc_up():
        return _live_unavailable()
    from hw_agent.artifacts.schematics import sch_ipc
    return sch_ipc.get_selection_as_string(Path(kicad_sch).resolve())


# ─── live_add_* (typed creates) ────────────────────────────────────────────

@mcp.tool()
def live_add_wire(
    kicad_sch: str,
    x1_mm: float, y1_mm: float, x2_mm: float, y2_mm: float,
    with_render: bool = True,
) -> list:
    """[live/ipc] Add an electrical wire — appears immediately in eeschema.

    REQUIRES eeschema open. Wire spans (x1,y1) → (x2,y2) in mm. Use
    orthogonal coordinates (horizontal or vertical) so it connects
    cleanly to pin endpoints; diagonals render but fail DRC.

    For headless authoring, use designer-mcp's `add_wire` which can
    take symbol pin endpoints by name and auto-elbows.
    """
    if not _ipc_up():
        return [_live_unavailable()]
    from hw_agent.artifacts.schematics import sch_ipc
    result = sch_ipc.add_wire(Path(kicad_sch).resolve(),
                              x1_mm, y1_mm, x2_mm, y2_mm)
    md = (f"live_add_wire ({x1_mm},{y1_mm}) → ({x2_mm},{y2_mm}) — "
          f"{result.get('error', 'ok')}")
    svg_md, img = _live_sch_render_after_edit(
        Path(kicad_sch).resolve(), zoom_to=None, with_render=with_render,
    )
    return [md + (svg_md or ""), img] if img else [md]


@mcp.tool()
def live_add_bus(
    kicad_sch: str,
    x1_mm: float, y1_mm: float, x2_mm: float, y2_mm: float,
    with_render: bool = True,
) -> list:
    """[live/ipc] Add a bus segment (multi-net wire bundle).

    REQUIRES eeschema open. Same shape as live_add_wire but type=BUS.
    """
    if not _ipc_up():
        return [_live_unavailable()]
    from hw_agent.artifacts.schematics import sch_ipc
    result = sch_ipc.add_bus(Path(kicad_sch).resolve(),
                             x1_mm, y1_mm, x2_mm, y2_mm)
    md = (f"live_add_bus ({x1_mm},{y1_mm}) → ({x2_mm},{y2_mm}) — "
          f"{result.get('error', 'ok')}")
    svg_md, img = _live_sch_render_after_edit(
        Path(kicad_sch).resolve(), zoom_to=None, with_render=with_render,
    )
    return [md + (svg_md or ""), img] if img else [md]


@mcp.tool()
def live_add_junction(
    kicad_sch: str, x_mm: float, y_mm: float, with_render: bool = True,
) -> list:
    """[live/ipc] Place a junction dot at (x, y) — used at wire intersections.

    REQUIRES eeschema open. Without a junction, two crossing wires
    don't connect electrically (KiCad treats them as flying-over).
    """
    if not _ipc_up():
        return [_live_unavailable()]
    from hw_agent.artifacts.schematics import sch_ipc
    result = sch_ipc.add_junction(Path(kicad_sch).resolve(), x_mm, y_mm)
    md = f"live_add_junction ({x_mm},{y_mm}) — {result.get('error', 'ok')}"
    svg_md, img = _live_sch_render_after_edit(
        Path(kicad_sch).resolve(), zoom_to=None, with_render=with_render,
    )
    return [md + (svg_md or ""), img] if img else [md]


@mcp.tool()
def live_add_no_connect(
    kicad_sch: str, x_mm: float, y_mm: float, with_render: bool = True,
) -> list:
    """[live/ipc] Place a no-connect (X) marker at (x, y).

    REQUIRES eeschema open. Marks an unused pin so ERC stops flagging
    it as "unconnected pin." Place at the pin endpoint.
    """
    if not _ipc_up():
        return [_live_unavailable()]
    from hw_agent.artifacts.schematics import sch_ipc
    result = sch_ipc.add_no_connect(Path(kicad_sch).resolve(), x_mm, y_mm)
    md = f"live_add_no_connect ({x_mm},{y_mm}) — {result.get('error', 'ok')}"
    svg_md, img = _live_sch_render_after_edit(
        Path(kicad_sch).resolve(), zoom_to=None, with_render=with_render,
    )
    return [md + (svg_md or ""), img] if img else [md]


@mcp.tool()
def live_add_text(
    kicad_sch: str, x_mm: float, y_mm: float, text: str,
    with_render: bool = True,
) -> list:
    """[live/ipc] Place free-text annotation at (x, y).

    REQUIRES eeschema open. Plain text — not connected to any net.
    For net labels, use live_add_label instead.
    """
    if not _ipc_up():
        return [_live_unavailable()]
    from hw_agent.artifacts.schematics import sch_ipc
    result = sch_ipc.add_text(Path(kicad_sch).resolve(), x_mm, y_mm, text)
    md = f"live_add_text ({x_mm},{y_mm}) {text!r} — {result.get('error', 'ok')}"
    svg_md, img = _live_sch_render_after_edit(
        Path(kicad_sch).resolve(), zoom_to=None, with_render=with_render,
    )
    return [md + (svg_md or ""), img] if img else [md]


@mcp.tool()
def live_add_label(
    kicad_sch: str, x_mm: float, y_mm: float, text: str,
    with_render: bool = True,
) -> list:
    """[live/ipc] Place a local net label at (x, y).

    REQUIRES eeschema open. Net labels create electrical connections —
    same name = same net. Use for naming nets clearly (e.g. `VOUT`,
    `SCK`) and for connecting wires that aren't visually adjacent.
    """
    if not _ipc_up():
        return [_live_unavailable()]
    from hw_agent.artifacts.schematics import sch_ipc
    result = sch_ipc.add_label(Path(kicad_sch).resolve(), x_mm, y_mm, text)
    md = f"live_add_label ({x_mm},{y_mm}) {text!r} — {result.get('error', 'ok')}"
    svg_md, img = _live_sch_render_after_edit(
        Path(kicad_sch).resolve(), zoom_to=None, with_render=with_render,
    )
    return [md + (svg_md or ""), img] if img else [md]


# ─── live_move_* / live_set_* (mutations on existing items) ────────────────

@mcp.tool()
def live_move_symbol(
    kicad_sch: str, ref: str, x_mm: float, y_mm: float,
    with_render: bool = True,
) -> list:
    """[live/ipc] Move a symbol — user watches it move in eeschema.

    REQUIRES eeschema open. The symbol moves in the user's view via
    an IPC commit. The .kicad_sch file is NOT updated until
    `live_save_schematic` runs (or the user saves manually).

    Args:
        kicad_sch: path of the .kicad_sch (logging only)
        ref: reference designator ("U1", "R5", etc.)
        x_mm, y_mm: new position in mm
        with_render: include a focused PNG so the agent sees the
            result. Forces a save first so the render isn't stale.
    """
    if not _ipc_up():
        return [_live_unavailable()]
    from hw_agent.artifacts.schematics import sch_ipc
    result = sch_ipc.move_symbol(Path(kicad_sch).resolve(), ref, x_mm, y_mm)
    md = (f"live_move_symbol `{ref}` → ({x_mm}, {y_mm}) — "
          f"{result.get('error', 'ok')}")
    svg_md, img = _live_sch_render_after_edit(
        Path(kicad_sch).resolve(), zoom_to=ref, with_render=with_render,
    )
    return [md + (svg_md or ""), img] if img else [md]


@mcp.tool()
def live_set_value(
    kicad_sch: str, ref: str, value: str, with_render: bool = True,
) -> list:
    """[live/ipc] Set the Value field on a symbol.

    REQUIRES eeschema open. Updates the visible Value text for ref
    (e.g., "10kΩ" → "12kΩ"). Use this rather than rebuilding from
    file when the user wants a single-symbol tweak.
    """
    if not _ipc_up():
        return [_live_unavailable()]
    from hw_agent.artifacts.schematics import sch_ipc
    result = sch_ipc.set_value(Path(kicad_sch).resolve(), ref, value)
    md = f"live_set_value `{ref}`.value={value!r} — {result.get('error', 'ok')}"
    svg_md, img = _live_sch_render_after_edit(
        Path(kicad_sch).resolve(), zoom_to=ref, with_render=with_render,
    )
    return [md + (svg_md or ""), img] if img else [md]


@mcp.tool()
def live_set_footprint(
    kicad_sch: str, ref: str, footprint: str, with_render: bool = True,
) -> list:
    """[live/ipc] Set the Footprint field on a symbol.

    REQUIRES eeschema open. Footprint is the PCB-side packaging (e.g.,
    "Resistor_SMD:R_0603_1608Metric"). Update propagates to the PCB
    on next sync_netlist.
    """
    if not _ipc_up():
        return [_live_unavailable()]
    from hw_agent.artifacts.schematics import sch_ipc
    result = sch_ipc.set_footprint(Path(kicad_sch).resolve(), ref, footprint)
    md = (f"live_set_footprint `{ref}`.footprint={footprint!r} — "
          f"{result.get('error', 'ok')}")
    svg_md, img = _live_sch_render_after_edit(
        Path(kicad_sch).resolve(), zoom_to=ref, with_render=with_render,
    )
    return [md + (svg_md or ""), img] if img else [md]


@mcp.tool()
def live_set_page_settings(kicad_sch: str, paper: str) -> dict:
    """[live/ipc] Change the schematic's paper size (A4, A3, USLetter, ...).

    REQUIRES eeschema open.
    """
    if not _ipc_up():
        return _live_unavailable()
    from hw_agent.artifacts.schematics import sch_ipc
    return sch_ipc.set_page_settings(Path(kicad_sch).resolve(), paper)


@mcp.tool()
def live_set_title_block(
    kicad_sch: str, title: Optional[str] = None,
    company: Optional[str] = None, revision: Optional[str] = None,
    date: Optional[str] = None,
) -> dict:
    """[live/ipc] Update title-block fields. Pass only the fields you
    want to change.

    REQUIRES eeschema open. Field names: title, date, revision,
    company. The change is in eeschema's memory — ask the user to
    press Ctrl+S to write it to disk.
    """
    if not _ipc_up():
        return _live_unavailable()
    from hw_agent.artifacts.schematics import sch_ipc
    return sch_ipc.set_title_block(
        Path(kicad_sch).resolve(),
        title=title, company=company, revision=revision, date=date,
    )


# ─── lifecycle ─────────────────────────────────────────────────────────────

@mcp.tool()
def live_save_schematic(kicad_sch: str, with_render: bool = False) -> list:
    """[live/ipc] Force-save the open .kicad_sch.

    REQUIRES eeschema open. **WARNING:** KiCad nightly 10.99 has no
    `SaveDocument` IPC handler, so this tool currently always fails.
    Until that handler ships, the canonical persist path is **the
    user pressing Ctrl+S in eeschema** (⌘S on macOS).

    Tell the user something like: "press Ctrl+S in eeschema to save
    the changes I just made — the IPC save isn't wired in this
    nightly yet."
    """
    if not _ipc_up():
        return [_live_unavailable()]
    from hw_agent.artifacts.schematics import sch_ipc
    result = sch_ipc.save_schematic(Path(kicad_sch).resolve())
    if result.get("ok"):
        md = "live_save_schematic: ok"
    else:
        err = result.get("error", "failed")
        md = (
            f"live_save_schematic: {err}"
            "\n\n_⚠ programmatic save isn't wired in this KiCad "
            "nightly. **Ask the user to press Ctrl+S** (⌘S on macOS) "
            "in eeschema — that's the only way to persist live edits "
            "right now._"
        )
    svg_md, img = _live_sch_render_after_edit(
        Path(kicad_sch).resolve(), zoom_to=None, with_render=with_render,
    )
    return [md + (svg_md or ""), img] if img else [md]


@mcp.tool()
def live_save_as(
    kicad_sch: str, new_path: str,
    overwrite: bool = False, include_project: bool = True,
) -> dict:
    """[live/ipc] Save the open schematic under a new filename.

    REQUIRES eeschema open. **WARNING:** KiCad nightly 10.99 has no
    `SaveCopyOfDocument` IPC handler, so this tool currently always
    fails. Tell the user to use **File → Save As** in eeschema, or
    `cp` the file from a shell.
    """
    if not _ipc_up():
        return _live_unavailable()
    from hw_agent.artifacts.schematics import sch_ipc
    result = sch_ipc.save_as(
        Path(kicad_sch).resolve(), Path(new_path).resolve(),
        overwrite=overwrite, include_project=include_project,
    )
    if not result.get("ok") and "no handler" in result.get("error", ""):
        result["hint"] = (
            "SaveCopyOfDocument isn't wired in this KiCad nightly. "
            "Tell the user to use File → Save As in eeschema, or "
            "copy the file from a shell (cp source.kicad_sch dest.kicad_sch)."
        )
    return result


@mcp.tool()
def live_undo_instructions(kicad_sch: str) -> dict:
    """[live/ipc] Tell the user how to undo recent edits.

    There is no programmatic revert — the live_* tools auto-save on
    every mutation, so the on-disk file matches eeschema state. To
    undo, the user presses **Ctrl+Z** (or ⌘Z on macOS) in eeschema.
    Each Ctrl+Z reverses one mutation (one symbol move, one wire add,
    one value change, etc.).

    Tell the user something like:
        "Press Ctrl+Z in eeschema N times to undo my last N edits.
         Each press undoes one operation."

    For surgical removal of specific items added by the agent, use
    `live_remove_by_id` instead (collect IDs from the live_list_*
    readers, pass to remove).

    This tool returns a string the agent can paraphrase; it does
    NOT call any IPC verb (no KiCad RevertDocument handler exists
    in current nightlies, so trying would just error).
    """
    return {
        "ok": True,
        "instructions": (
            "Press Ctrl+Z in eeschema (⌘Z on macOS) to undo the most "
            "recent edit. Each press reverses one mutation. To undo "
            "a sequence of N agent edits, press Ctrl+Z N times."
        ),
        "alternatives": [
            "live_remove_by_id with specific KIIDs (collect via "
            "live_list_lines / live_list_labels / live_list_symbols / etc.)",
            "Close eeschema without saving and reopen — drops every "
            "in-memory edit. Useful when undo history is too long to "
            "step through manually.",
        ],
    }


@mcp.tool()
def live_remove_by_id(
    kicad_sch: str, item_ids: list[str], with_render: bool = True,
) -> list:
    """[live/ipc] Remove items by KIID (UUID strings).

    REQUIRES eeschema open. Find IDs first via live_list_* readers
    (each item has an `id` field). Pass one or more.
    """
    if not _ipc_up():
        return [_live_unavailable()]
    from hw_agent.artifacts.schematics import sch_ipc
    result = sch_ipc.remove_by_id(Path(kicad_sch).resolve(), item_ids)
    md = (f"live_remove_by_id removed={result.get('removed', 0)} — "
          f"{result.get('error', 'ok')}")
    svg_md, img = _live_sch_render_after_edit(
        Path(kicad_sch).resolve(), zoom_to=None, with_render=with_render,
    )
    return [md + (svg_md or ""), img] if img else [md]


# ─── Entry Point ───────────────────────────────────────────────────────────

def main() -> None:
    """Console-script entrypoint. Invoked by orchestrators via `live-edit-mcp`."""
    mcp.run()


if __name__ == "__main__":
    main()
