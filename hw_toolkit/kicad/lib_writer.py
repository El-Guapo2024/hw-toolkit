"""Pydantic Schematic → kicad-sch-api adapter.

The replacement for `kicad_writer.py`. Instead of emitting S-expressions
by hand (770 LOC), we walk the in-memory `Schematic` model and translate
each field into kicad-sch-api API calls. kicad-sch-api owns the file
format details — UUIDs, lib_symbol injection, instance blocks, paper
size — which means everything kicad-cli renders cleanly.

Architecture:

    circuit_builder.Sheet
            │
            ▼  to_dict() / model_validate
    Schematic (Pydantic)
            │
            ▼  this module
    kicad-sch-api Schematic instance
            │
            ▼  sch.save(path)
    .kicad_sch  (always kicad-sch-api format → kicad-cli safe)

Inline-pin ICs (`type="ic"` with `pins=[...]`) are emitted to a sibling
`hwagent.kicad_sym` library file. kicad-sch-api is told about it via
`sch.library.add_library_path(...)`. The user's authoring API
(`Sheet.ic(part, pins=[...])`) doesn't change — the IC ends up as a
real lib_symbol on disk.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import kicad_sch_api as ksa

from .schem_renderer import Schematic, Symbol, WireEndpoint


# ─── Symbol → lib_id mapping ───────────────────────────────────────────────

def _norm_rail(label: str) -> str:
    """Extract the canonical rail name (`+3V3`, `+5V`, `VBAT`) from a label."""
    if not label:
        return "VCC"
    first = label.strip().split()[0]
    upper = first.upper()
    if upper in ("5V", "+5V"):
        return "+5V"
    if upper in ("3.3V", "3V3", "+3V3", "+3.3V"):
        return "+3V3"
    if upper in ("12V", "+12V"):
        return "+12V"
    if first.startswith("+"):
        return first
    return first


_STOCK_POWER_RAILS = {
    "+3V3", "+5V", "+12V", "+24V", "+VCC", "VCC", "VCCSA", "VCCQ", "VCC_PLL",
    "+1V", "+1V0", "+1V2", "+1V5", "+1V8", "+2V5",
    "-12V", "-5V", "-VCC", "-VEE",
}


def _power_lib_id(label: str) -> str:
    """Stock rails → `power:NAME`; custom rails (VBAT, VBUS_5V) → `hwagent:NAME`."""
    name = _norm_rail(label)
    if name in _STOCK_POWER_RAILS:
        return f"power:{name}"
    return f"hwagent:{name}"


# Orient → rotation degrees for passives. KiCad Device:R/C/L pin 1 lives
# above the symbol center (Y-up local). We rotate so pin-1 lands on the
# JSON `at` coord:
#   orient="down"  → pin 1 above center → rotation 0
#   orient="up"    → pin 1 below center → rotation 180
#   orient="right" → pin 1 left of center → rotation 270
#   orient="left"  → pin 1 right of center → rotation 90
_ORIENT_ROTATION = {"down": 0, "up": 180, "right": 270, "left": 90}
_PASSIVE_PIN_OFFSET = 3.81  # KiCad standard pin distance from center

# KiCad's default schematic grid. All wire endpoints + symbol anchors must
# snap to this or kicad-cli ERC reports `endpoint_off_grid` and treats
# almost-touching wires as `wire_dangling`. The old kicad_writer.py did
# this on every coord; ksa_writer must too.
_GRID_MM = 1.27


def _snap(v: float) -> float:
    """Snap a coordinate to KiCad's 1.27mm schematic grid."""
    return round(float(v) / _GRID_MM) * _GRID_MM


def _snap_xy(p) -> tuple[float, float]:
    """Snap a 2-tuple to grid."""
    return (_snap(p[0]), _snap(p[1]))


def _passive_center(sym: Symbol) -> tuple[float, float]:
    """Compute the symbol-center coordinate so pin-1 lands on `sym.at`.
    Result is grid-snapped so kicad-cli ERC doesn't flag off-grid endpoints."""
    x, y = _snap_xy(sym.at)
    o = sym.orient
    if o == "down":
        return _snap_xy((x, y + _PASSIVE_PIN_OFFSET))
    if o == "up":
        return _snap_xy((x, y - _PASSIVE_PIN_OFFSET))
    if o == "right":
        return _snap_xy((x + _PASSIVE_PIN_OFFSET, y))
    if o == "left":
        return _snap_xy((x - _PASSIVE_PIN_OFFSET, y))
    return _snap_xy((x, y + _PASSIVE_PIN_OFFSET))


# ─── Inline-pin IC library synthesis ──────────────────────────────────────
# When the user calls `Sheet.ic(part, pins=[...])`, the chip isn't in any
# standard KiCad library. We emit a lib_symbol entry into the project's
# `hwagent.kicad_sym` file and tell kicad-sch-api about it via
# `add_library_path`. The user's authoring API doesn't change.

_HWAGENT_LIB_HEADER = '(kicad_symbol_lib (version 20231120) (generator "hw-agent")\n'
_HWAGENT_LIB_FOOTER = ")\n"

# KiCad lib_symbol pin angles: direction the pin-tip points away from body.
# 0=right, 90=up, 180=left, 270=down.
_PIN_ANGLE_BY_SIDE = {"left": 180, "right": 0, "top": 90, "bottom": 270}


def synthesize_ic_lib_symbol(
    name: str,
    pins: list[dict],
    *,
    size: tuple[float, float] = (15, 12),
    reference_prefix: str = "U",
    footprint: str = "",
    center: tuple[float, float] = (0.0, 0.0),
) -> str:
    """Build a `(symbol "<name>" ...)` lib_symbol block as text.

    Pure synthesis — the result is ready to splice into a `hwagent.kicad_sym`
    library file. Used by ksa_writer to emit inline-pin ICs from the
    Schematic model AND by sch_ops.add_custom_ic for direct MCP-driven
    placement.

    Args:
        name: symbol name (becomes the lib_id suffix: hwagent:<name>)
        pins: list of {"name", "at": (x, y), "side"} dicts. `at` is in
              the same coord space as `center`.
        size: (width, height) of the body rectangle in mm
        reference_prefix: "U" for ICs, "RV" for trimmers, etc.
        footprint: optional footprint string
        center: pin coords are relative to this. Defaults (0, 0) — i.e.
                pin coords are already lib-local.
    """
    # Snap center to grid so absolute pin positions = snapped_center +
    # snapped_local_offset stay on grid.
    cx, cy = _snap_xy(center)
    w, h = size
    hw, hh = w / 2, h / 2

    body = (
        f'    (symbol "{name}_0_1"\n'
        f'      (rectangle (start {-hw} {hh}) (end {hw} {-hh})\n'
        f'        (stroke (width 0.254) (type default))\n'
        f'        (fill (type background))\n'
        f'      )\n'
        f'    )\n'
    )

    pin_blocks = []
    for i, p in enumerate(pins, start=1):
        px, py = _snap_xy(p["at"])  # snap absolute coord first
        local_x = _snap(px - cx)
        local_y = _snap(cy - py)  # Y-flip for lib_symbol Y-up convention
        angle = _PIN_ANGLE_BY_SIDE.get(p.get("side", "left"), 180)
        # Optional electrical_type per pin — KiCad uses it for ERC
        # "power_pin_not_driven" checks. Defaults to `passive` (back-compat).
        elec = p.get("electrical_type", "passive")
        pin_blocks.append(
            f'      (pin {elec} line (at {local_x} {local_y} {angle}) (length 2.54)\n'
            f'        (name "{p["name"]}" (effects (font (size 1.27 1.27))))\n'
            f'        (number "{i}" (effects (font (size 1.27 1.27))))\n'
            f'      )\n'
        )

    pins_unit = (
        f'    (symbol "{name}_1_1"\n'
        + "".join(pin_blocks)
        + "    )\n"
    )

    return (
        f'  (symbol "{name}"\n'
        f'    (pin_numbers (hide yes))\n'
        f'    (pin_names (offset 0.254))\n'
        f'    (in_bom yes) (on_board yes)\n'
        f'    (property "Reference" "{reference_prefix}" (at 0 {hh + 2.54} 0)\n'
        f'      (effects (font (size 1.27 1.27))))\n'
        f'    (property "Value" "{name}" (at 0 {-hh - 2.54} 0)\n'
        f'      (effects (font (size 1.27 1.27))))\n'
        f'    (property "Footprint" "{footprint}" (at 0 0 0)\n'
        f'      (effects (font (size 1.27 1.27)) (hide yes)))\n'
        f'    (property "Datasheet" "" (at 0 0 0)\n'
        f'      (effects (font (size 1.27 1.27)) (hide yes)))\n'
        + body
        + pins_unit
        + '  )\n'
    )


def append_to_hwagent_lib(dir_path: Path, lib_symbol_block: str) -> Path:
    """Append a synthesized lib_symbol block to project's `hwagent.kicad_sym`.

    Creates the file (with sym-lib-table) if missing. Idempotent on
    re-adding the same block (skips duplicates by symbol name).
    """
    dir_path = Path(dir_path)
    dir_path.mkdir(parents=True, exist_ok=True)

    lib_path = dir_path / "hwagent.kicad_sym"
    if lib_path.exists():
        existing = lib_path.read_text()
    else:
        existing = _HWAGENT_LIB_HEADER + _HWAGENT_LIB_FOOTER

    # Extract the symbol name from the new block to check for dupes
    import re
    m = re.search(r'\(symbol\s+"([^"]+)"', lib_symbol_block)
    if m and f'(symbol "{m.group(1)}"' in existing:
        return lib_path  # already present; no-op

    # Splice the new block in just before the closing paren
    if existing.rstrip().endswith(")"):
        body = existing.rstrip()[:-1].rstrip() + "\n" + lib_symbol_block + ")\n"
    else:
        body = existing + lib_symbol_block

    lib_path.write_text(body)

    sym_lib_table = dir_path / "sym-lib-table"
    if not sym_lib_table.exists():
        sym_lib_table.write_text(
            '(sym_lib_table\n'
            '  (lib (name "hwagent")(type "KiCad")(uri "${KIPRJMOD}/hwagent.kicad_sym")'
            '(options "")(descr ""))\n'
            ')\n'
        )

    return lib_path


def _ic_lib_symbol_block(sym: Symbol) -> str:
    """Pydantic Symbol → lib_symbol block. Wraps `synthesize_ic_lib_symbol`."""
    pins_dicts = [{"name": p.name, "at": p.at, "side": p.side} for p in sym.pins]
    return synthesize_ic_lib_symbol(
        name=sym.part or sym.id,
        pins=pins_dicts,
        size=sym.size or (15, 12),
        reference_prefix=sym.id[0] if sym.id else "U",
        footprint=sym.footprint or "",
        center=sym.at,
    )


def _power_flag_lib_symbol_block(rail: str) -> str:
    """Synthesize a power-flag lib_symbol block for a custom rail (VBAT, VOUT…).

    Symbol name in the .kicad_sym file is the bare rail (no `hwagent:` prefix —
    the prefix is implied by the file location).
    """
    return f'''  (symbol "{rail}" (power) (pin_numbers (hide yes)) (pin_names (offset 0) (hide yes))
    (exclude_from_sim no) (in_bom yes) (on_board yes)
    (property "Reference" "#PWR" (at 0 -3.81 0)
      (effects (font (size 1.27 1.27)) (hide yes)))
    (property "Value" "{rail}" (at 0 3.556 0)
      (effects (font (size 1.27 1.27))))
    (property "Footprint" "" (at 0 0 0)
      (effects (font (size 1.27 1.27)) (hide yes)))
    (property "Datasheet" "" (at 0 0 0)
      (effects (font (size 1.27 1.27)) (hide yes)))
    (symbol "{rail}_0_1"
      (polyline (pts (xy -0.762 1.27) (xy 0 2.54))
        (stroke (width 0) (type default)) (fill (type none)))
      (polyline (pts (xy 0 2.54) (xy 0.762 1.27))
        (stroke (width 0) (type default)) (fill (type none)))
      (polyline (pts (xy 0 0) (xy 0 2.54))
        (stroke (width 0) (type default)) (fill (type none))))
    (symbol "{rail}_1_1"
      (pin power_in line (at 0 0 90) (length 0)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))))
  )
'''


def _ensure_hwagent_lib(
    out_dir: Path,
    custom_ics: list[Symbol],
    custom_rails: set[str],
) -> Optional[Path]:
    """Read/create `hwagent.kicad_sym` containing every custom IC + power rail.

    Idempotent — re-emitting with the same set produces the same file.
    Returns the lib path if anything was written, else None.
    """
    if not custom_ics and not custom_rails:
        return None

    lib_path = out_dir / "hwagent.kicad_sym"
    parts = []
    for rail in sorted(custom_rails):
        parts.append(_power_flag_lib_symbol_block(rail))
    for sym in custom_ics:
        parts.append(_ic_lib_symbol_block(sym))

    text = _HWAGENT_LIB_HEADER + "".join(parts) + _HWAGENT_LIB_FOOTER
    lib_path.write_text(text)

    sym_lib_table = out_dir / "sym-lib-table"
    if not sym_lib_table.exists():
        sym_lib_table.write_text(
            '(sym_lib_table\n'
            '  (lib (name "hwagent")(type "KiCad")(uri "${KIPRJMOD}/hwagent.kicad_sym")'
            '(options "")(descr ""))\n'
            ')\n'
        )

    return lib_path


# ─── Wire endpoint resolution ──────────────────────────────────────────────

def _passive_pin_pos(sym: Symbol, which: str) -> tuple[float, float]:
    """Pin position for a 2-terminal passive — `which` is "1"/"top" or "2"/"bottom".
    Snapped to KiCad's grid so wires connect cleanly at ERC time."""
    x, y = _snap_xy(sym.at)
    L = 2 * _PASSIVE_PIN_OFFSET  # 7.62 mm = 6 grid cells, already on grid
    o = sym.orient
    if which in ("1", "top", "left"):
        return (x, y)
    if o == "down":
        return _snap_xy((x, y + L))
    if o == "up":
        return _snap_xy((x, y - L))
    if o == "right":
        return _snap_xy((x + L, y))
    if o == "left":
        return _snap_xy((x - L, y))
    return _snap_xy((x, y + L))


def _resolve_endpoint(ep: WireEndpoint, schem: Schematic,
                      sch: "ksa.Schematic") -> tuple[float, float]:
    """Resolve a Pydantic WireEndpoint to absolute (x, y) coords.

    Returns are always grid-snapped — kicad-cli ERC fails any wire
    endpoint that's off the 1.27mm schematic grid.
    """
    if ep.coord is not None:
        return _snap_xy(tuple(ep.coord))

    sym = next((s for s in schem.symbols if s.id == ep.block), None)
    if not sym:
        raise ValueError(f"unknown symbol: {ep.block!r}")

    if ep.pin:
        # Passive pseudo-pins (already snapped by _passive_pin_pos)
        if sym.type in ("resistor", "capacitor", "inductor"):
            return _passive_pin_pos(sym, ep.pin)

        # KiCad-lib symbol — pin position from kicad-sch-api (already
        # grid-aligned by KiCad's lib data; snap defensively).
        if sym.type == "kicad" and sym.lib_id:
            from hw_toolkit.kicad.lib import load_symbol
            lib = load_symbol(sym.lib_id)
            for p in lib.pins:
                if p.name == ep.pin or p.number == ep.pin:
                    pos = sch.get_component_pin_position(sym.id, p.number)
                    if pos is not None:
                        return _snap_xy((pos.x, pos.y))
            raise ValueError(f"pin {ep.pin!r} not found on {sym.lib_id}")

        # Inline-pin IC (type="ic") — snap to grid
        if sym.type == "ic":
            for p in sym.pins:
                if p.name == ep.pin:
                    return _snap_xy(tuple(p.at))
            raise ValueError(f"pin {ep.pin!r} not in inline pins of {sym.id}")

        raise ValueError(f"can't resolve pin {ep.pin!r} on {sym.id} (type={sym.type})")

    # Bare ref — net anchor (vcc/ground/terminal) → snapped position
    return _snap_xy(tuple(sym.at))


# ─── Public entry point ───────────────────────────────────────────────────

def write_hwagent_lib_stub(dir_path: Path) -> None:
    """Drop an empty `hwagent.kicad_sym` + `sym-lib-table` so KiCad's
    library check passes for projects that reference `hwagent:` lib_ids.

    Used by `system_composer` for the project root. Idempotent.
    """
    dir_path = Path(dir_path)
    dir_path.mkdir(parents=True, exist_ok=True)
    lib_path = dir_path / "hwagent.kicad_sym"
    if not lib_path.exists():
        lib_path.write_text(_HWAGENT_LIB_HEADER + _HWAGENT_LIB_FOOTER)
    sym_lib_table = dir_path / "sym-lib-table"
    if not sym_lib_table.exists():
        sym_lib_table.write_text(
            '(sym_lib_table\n'
            '  (lib (name "hwagent")(type "KiCad")(uri "${KIPRJMOD}/hwagent.kicad_sym")'
            '(options "")(descr ""))\n'
            ')\n'
        )


def export_file(
    schem_json: Path | str, out_path: Path | str,
    *, child_sheet: bool = False, strict: bool = True,
) -> Path:
    """JSON path → .kicad_sch via kicad-sch-api.

    Compatibility shim for callers that previously used
    `kicad_writer.export_file(json_path, sch_path)`. New code should call
    `export_from_schematic` directly with an in-memory Schematic.
    """
    import json as _json
    schem_json = Path(schem_json)
    out_path = Path(out_path)
    data = _json.loads(schem_json.read_text())
    schem = Schematic.model_validate(data)
    return export_from_schematic(schem, out_path,
                                 child_sheet=child_sheet, strict=strict)


def export_from_schematic(
    schem: Schematic, out_path: Path,
    *, child_sheet: bool = False, strict: bool = True,
) -> Path:
    """Convert in-memory `Schematic` to a `.kicad_sch` via kicad-sch-api.

    Args:
        schem: validated Pydantic Schematic
        out_path: where to write the .kicad_sch
        child_sheet: emit as hierarchical child (skips PWR_FLAG markers).
            Currently unused — kept for kicad_writer parity.
        strict: validate before write. kicad-sch-api's strict validator
            rejects KiCad's own `#PWR`/`#FLG` convention; we override.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Custom symbol library: hwagent.kicad_sym holds every type="ic"
    #    chip + every non-stock power rail (VBAT, VOUT, etc.)
    custom_ics = [s for s in schem.symbols if s.type == "ic"]
    custom_rails: set[str] = set()
    for s in schem.symbols:
        if s.type == "vcc":
            rail = _norm_rail(s.label or "")
            if rail not in _STOCK_POWER_RAILS:
                custom_rails.add(rail)
    lib_path = _ensure_hwagent_lib(out_path.parent, custom_ics, custom_rails)

    # 2. Create the kicad-sch-api Schematic
    sch = ksa.create_schematic(str(out_path))
    sch.validate = lambda: []  # type: ignore[method-assign]
    sch.name = out_path.stem
    if lib_path:
        sch.library.add_library_path(str(lib_path))

    # 3. Canvas / paper / title
    if schem.canvas.title:
        try:
            sch.set_title_block(title=schem.canvas.title)
        except Exception:
            pass  # not all kicad-sch-api versions accept all args

    # 4. Place every symbol. Power flags + grounds need numbered #PWR refs
    #    (kicad-sch-api's validator wants `#PWR1`, `#PWR2`, etc.). We track a
    #    counter; the user's sym.id stays as the in-memory wire-resolution key.
    pwr_counter = 0
    for sym in schem.symbols:
        if sym.type in ("vcc", "ground"):
            pwr_counter += 1
            ref_for_kicad = f"#PWR{pwr_counter}"
        else:
            ref_for_kicad = sym.id

        try:
            _place_symbol(sch, sym, ref_for_kicad)
        except Exception as e:
            raise RuntimeError(
                f"failed to place {sym.id} (type={sym.type}, lib={sym.lib_id}): {e}"
            ) from e

    # 5. Wires — resolve endpoints AFTER all components are placed
    for w in schem.wires:
        try:
            a = _resolve_endpoint(w.from_, schem, sch)
            b = _resolve_endpoint(w.to,   schem, sch)
            sch.add_wire(start=a, end=b)
        except Exception as e:
            raise RuntimeError(f"failed to add wire {w.from_} → {w.to}: {e}") from e

    # 6. Labels (free-text annotations on the sheet)
    for label in schem.labels:
        # `label.at` may be a coord or a ref-string like "U1.VIN" — for now
        # only handle coord form. Ref form is an authoring-side convenience
        # not yet supported on the kicad-sch-api path.
        if isinstance(label.at, (list, tuple)) and len(label.at) == 2:
            try:
                sch.add_label(text=label.text, at=tuple(label.at))
            except Exception:
                pass

    # 7. PWR_FLAG markers — KiCad's ERC needs every power net to have at
    #    least one output (driven) source. Standard ICs declare some pins
    #    as `power_out`, but inline-pin ICs (Sheet.ic) emit `passive` pins
    #    by default, leaving rails undriven. Auto-emit ONE PWR_FLAG per
    #    unique rail (multiple symbols with the same label share a flag
    #    to avoid the `power output to power output` ERC violation).
    if not child_sheet:
        flagged_nets: set[str] = set()
        for sym in schem.symbols:
            if sym.type == "vcc":
                net_key = _norm_rail(sym.label or "")
            elif sym.type == "ground":
                net_key = "GND"
            else:
                continue
            if net_key in flagged_nets:
                continue
            flagged_nets.add(net_key)
            pwr_counter += 1
            x, y = _snap_xy(sym.at)
            try:
                sch.components.add(
                    lib_id="power:PWR_FLAG",
                    reference=f"#FLG{pwr_counter}",
                    value="PWR_FLAG",
                    position=(x, y),
                )
            except Exception:
                # If kicad-sch-api can't place the flag (lib not loaded,
                # etc.) skip silently — ERC violation is informational.
                pass

    # 8. Save
    sch.save(str(out_path))
    return out_path


def _place_symbol(sch, sym: Symbol, ref: str) -> None:
    """Map one Pydantic Symbol to a kicad-sch-api components.add() call."""
    t = sym.type

    if t in ("resistor", "capacitor", "inductor"):
        lib_id = {"resistor": "Device:R",
                  "capacitor": "Device:C",
                  "inductor": "Device:L"}[t]
        cx, cy = _passive_center(sym)
        rotation = _ORIENT_ROTATION.get(sym.orient, 0)
        sch.components.add(
            lib_id=lib_id, reference=ref,
            value=sym.value or t[0].upper(),
            position=(cx, cy), rotation=rotation,
            footprint=sym.footprint,
        )
        return

    if t == "ground":
        # ref is already numbered (#PWR<n>) by the caller.
        sch.components.add(
            lib_id="power:GND", reference=ref, value="GND",
            position=_snap_xy(tuple(sym.at)),
        )
        return

    if t == "vcc":
        lib_id = _power_lib_id(sym.label or "")
        rail_name = _norm_rail(sym.label or "")
        sch.components.add(
            lib_id=lib_id, reference=ref, value=rail_name,
            position=_snap_xy(tuple(sym.at)),
        )
        return

    if t == "kicad" and sym.lib_id:
        sch.components.add(
            lib_id=sym.lib_id, reference=ref,
            value=sym.value or sym.lib_id.split(":", 1)[-1],
            position=_snap_xy(tuple(sym.at)),
            footprint=sym.footprint,
        )
        return

    if t == "ic":
        # Custom IC — the lib_symbol is in hwagent.kicad_sym which we
        # registered earlier. lib_id is "hwagent:<part>".
        part = sym.part or sym.id
        sch.components.add(
            lib_id=f"hwagent:{part}", reference=ref,
            value=part,
            position=_snap_xy(tuple(sym.at)),
            footprint=sym.footprint,
        )
        return

    if t == "terminal":
        # A named anchor — emit as a label, not a placed symbol.
        if sym.label:
            try:
                sch.add_label(text=sym.label, at=_snap_xy(tuple(sym.at)))
            except Exception:
                pass
        return

    if t == "diode":
        sch.components.add(
            lib_id="Device:D", reference=ref,
            value=sym.value or "D",
            position=_snap_xy(tuple(sym.at)),
            footprint=sym.footprint,
        )
        return

    raise ValueError(f"unsupported symbol type: {t!r}")
