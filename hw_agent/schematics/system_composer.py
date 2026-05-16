"""System-level (root) schematic composer.

Reads the per-subsystem `.schem.json` files of a project and emits a root
`.kicad_sch` with one `(sheet ...)` block per subsystem. Each sheet block
declares pins matching the subsystem's terminals (which the writer emits
as hierarchical_labels in the child sheet) plus standard power pins.

This is the bridge between per-subsystem authoring and a complete
hierarchical KiCad project that can be opened in eeschema and pushed to
PCB layout.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .schem_renderer import Schematic
from . import kicad_templates as T


KICAD_VERSION = "20250114"
GENERATOR = "hw-agent-system"


def _u() -> str:
    return str(uuid.uuid4())


# ─── Root-level power infrastructure ────────────────────────────────────────

# Rails that need a PWR_FLAG at the root.
#   VBAT — comes from external battery, no internal driver
#   GND  — universal reference, no power_out drives it
#   +5V  — driven by buck.SW but the path goes SW → L1 → +5V, and KiCad's
#          connectivity treats the inductor as breaking the net, so +5V
#          ends up undriven without an explicit PWR_FLAG
# +3V3 stays out: LDO.VO is a direct power_out pin on the +3V3 net.
# Coords are pre-snapped to the 2.54 mm grid.
ROOT_DRIVEN_RAILS = [
    # (lib_id,           label,  x,      y)
    ("hwagent:VBAT",     "VBAT", 20.32,  15.24),
    ("power:GND",        "GND",  60.96,  15.24),
    ("power:+5V",        "+5V",  101.6,  15.24),
]


def _emit_root_pwr_flag_pair(lib_id: str, label: str, x: float, y: float) -> list[str]:
    """Emit a (power flag) + (PWR_FLAG) pair at (x, y).

    Both share the same pin position so KiCad sees them as connected.
    """
    out = []
    # Power flag (e.g. VBAT or GND) — single pin at placement origin.
    out.append(
        f'  (symbol (lib_id "{lib_id}") (at {x:.3f} {y:.3f} 0) (unit 1)\n'
        f'    (in_bom yes) (on_board yes) (dnp no)\n'
        f'    (uuid "{_u()}")\n'
        f'    (property "Reference" "#PWR" (at {x:.3f} {y - 3.81:.3f} 0)\n'
        f'      (effects (font (size 1.27 1.27)) hide)\n'
        f'    )\n'
        f'    (property "Value" "{label}" (at {x:.3f} {y - 6.35:.3f} 0)\n'
        f'      (effects (font (size 1.27 1.27)))\n'
        f'    )\n'
        f'    (pin "1" (uuid "{_u()}"))\n'
        f'  )'
    )
    # PWR_FLAG to drive the rail — pin at placement origin too.
    out.append(
        f'  (symbol (lib_id "power:PWR_FLAG") (at {x:.3f} {y:.3f} 0) (unit 1)\n'
        f'    (in_bom yes) (on_board yes) (dnp no)\n'
        f'    (uuid "{_u()}")\n'
        f'    (property "Reference" "#FLG" (at {x:.3f} {y - 2.54:.3f} 0)\n'
        f'      (effects (font (size 1.27 1.27)) hide)\n'
        f'    )\n'
        f'    (property "Value" "PWR_FLAG" (at {x:.3f} {y - 5.08:.3f} 0)\n'
        f'      (effects (font (size 1.27 1.27)))\n'
        f'    )\n'
        f'    (pin "1" (uuid "{_u()}"))\n'
        f'  )'
    )
    return out


# ─── Subsystem layout on the root sheet ──────────────────────────────────────

@dataclass
class SheetSlot:
    """Where a subsystem sheet sits on the root page."""
    name: str               # subsystem dir name, e.g. "buck_converter"
    title: str              # human-readable, e.g. "Buck Converter"
    sheet_file: str         # relative path to child .kicad_sch
    x: float                # top-left corner x (mm)
    y: float                # top-left corner y (mm)
    w: float                # box width
    h: float                # box height
    color: tuple[int, int, int]  # fill color


# Default 2-row layout on an A3 page (420×297 mm).
# Top row: power chain (battery → buck → ldo → mcu).
# Bottom row: peripherals (motor, stepper, pwm/servo, imu).
DEFAULT_LAYOUT: list[tuple] = [
    # (subsystem,           title,                x,    y,   w,   h,  color)
    ("buck_converter",      "Buck 7.4V→5V",       30,   30,  80,  60,  (255, 240, 230)),
    ("ldo",                 "LDO 5V→3.3V",        130,  30,  60,  60,  (255, 240, 230)),
    ("mcu_ble",             "MCU (ESP32-S3)",     210,  30,  100, 80,  (230, 240, 255)),
    ("motor_driver",        "Motor Driver",       30,   140, 80,  60,  (240, 255, 240)),
    ("stepper_driver",      "Stepper Driver",     130,  140, 80,  60,  (240, 255, 240)),
    ("pwm_servo_driver",    "PWM / Servo",        230,  140, 80,  60,  (240, 255, 240)),
    ("imu",                 "IMU (LSM6DS3)",      330,  140, 60,  60,  (255, 240, 240)),
]


# Power pins are intentionally NOT declared as sheet pins. Power rails (+5V,
# +3V3, GND) propagate across the hierarchy via the global `power:` lib_symbol
# instances inside each child — KiCad merges them by name automatically. Sheet
# pins are reserved for *signal* connections (terminals).
SUBSYSTEM_POWER_PINS: dict[str, list[tuple[str, str, str]]] = {}


# ─── Pin emission ───────────────────────────────────────────────────────────

def _sheet_pin(name: str, direction: str, slot: SheetSlot, idx: int, side: str) -> str:
    """Emit a single (pin ...) block inside a (sheet ...).

    Pins are placed along the box's left or right edge, vertically distributed.
    Direction: 'input', 'output', 'bidirectional', 'tri_state', 'passive'.
    Angle: 180 for left edge (text reads outward), 0 for right edge.
    """
    spacing = 2.54
    if side == "left":
        x = slot.x
        angle = 180
    elif side == "right":
        x = slot.x + slot.w
        angle = 0
    elif side == "top":
        x = slot.x + slot.w / 2 + (idx - 0.5) * spacing
        angle = 90
    else:  # bottom
        x = slot.x + slot.w / 2 + (idx - 0.5) * spacing
        angle = 270
    if side in ("left", "right"):
        y = slot.y + 7.62 + idx * spacing
    else:
        y = slot.y if side == "top" else slot.y + slot.h
    justify = "left" if side == "right" else "right" if side == "left" else "center"
    return (
        f'    (pin "{name}" {direction} (at {x:.3f} {y:.3f} {angle})\n'
        f'      (effects (font (size 1.27 1.27)) (justify {justify}))\n'
        f'      (uuid "{_u()}")\n'
        f'    )'
    )


def _sheet_block(slot: SheetSlot, sheet_uuid: str, pins: list[tuple[str, str, str]]) -> str:
    """Emit a complete (sheet ...) block with name, file, and pin list.

    pins: list of (name, direction, side) tuples.
    """
    r, g, b = slot.color
    out = []
    out.append(f'  (sheet (at {slot.x:.3f} {slot.y:.3f}) (size {slot.w:.3f} {slot.h:.3f})')
    out.append(f'    (fields_autoplaced)')
    out.append(f'    (stroke (width 0.254) (type solid))')
    out.append(f'    (fill (color {r} {g} {b} 1.0))')
    out.append(f'    (uuid "{sheet_uuid}")')
    out.append(f'    (property "Sheetname" "{slot.title}" (at {slot.x:.3f} {slot.y - 1.27:.3f} 0)')
    out.append(f'      (effects (font (size 1.524 1.524)) (justify left bottom))')
    out.append(f'    )')
    out.append(f'    (property "Sheetfile" "{slot.sheet_file}" (at {slot.x:.3f} {slot.y + slot.h + 1.27:.3f} 0)')
    out.append(f'      (effects (font (size 1.0 1.0)) (justify left top))')
    out.append(f'    )')
    # Group pins by side, emit with index
    by_side: dict[str, list[tuple[str, str]]] = {"left": [], "right": [], "top": [], "bottom": []}
    for name, direction, side in pins:
        by_side.setdefault(side, []).append((name, direction))
    for side, group in by_side.items():
        for i, (name, direction) in enumerate(group):
            out.append(_sheet_pin(name, direction, slot, i, side))
    out.append(f'  )')
    return "\n".join(out)


# ─── Discovery ──────────────────────────────────────────────────────────────

def _terminals_of_subsystem(schem_json_path: Path) -> list[tuple[str, str]]:
    """Return list of (id, label_text) for terminal symbols.

    The `id` is what the writer emits as the hierarchical_label name in the
    child sheet (used for sheet-pin matching). `label_text` is human-readable.
    """
    data = json.loads(schem_json_path.read_text())
    out = []
    for s in data.get("symbols", []):
        if s.get("type") == "terminal":
            sid = s.get("id", "?")
            label = s.get("label", sid)
            out.append((sid, label))
    return out


# ─── Top-level composer ─────────────────────────────────────────────────────

def compose_root(
    project_dir: str | Path,
    output: str | Path,
    layout: Optional[list] = None,
    re_export_children: bool = True,
) -> Path:
    """Generate a root .kicad_sch that hierarchically references all
    per-subsystem .kicad_sch files.

    Args:
        project_dir: path to the project (e.g. docs/projects/robocar-hub/).
        output: where to write the root .kicad_sch.
        layout: optional override for sheet placement; defaults to DEFAULT_LAYOUT.
        re_export_children: if True, regenerate each child .kicad_sch in
            child-sheet mode (skips PWR_FLAGs to avoid pin_to_pin conflicts).

    Returns the absolute path of the generated file.
    """
    from .ksa_writer import export_file

    project_dir = Path(project_dir)
    output = Path(output)
    layout_list = layout or DEFAULT_LAYOUT

    components_dir = project_dir / "components"

    if re_export_children:
        for entry in layout_list:
            sub_name = entry[0]
            schem_json = components_dir / sub_name / "schematic.schem.json"
            if schem_json.exists():
                export_file(
                    schem_json,
                    schem_json.parent / "schematic.kicad_sch",
                    child_sheet=True,
                )
    # Build lib_symbols for the rails we drive at the root.
    root_libs: dict[str, str] = {}
    for lib_id, _label, _x, _y in ROOT_DRIVEN_RAILS:
        if lib_id.startswith("hwagent:"):
            name = lib_id.split(":", 1)[1]
            root_libs[lib_id] = T.custom_power_flag(name)
        elif lib_id.startswith("power:"):
            name = lib_id.split(":", 1)[1]
            root_libs[lib_id] = T.stock_power_rail(name)
    if ROOT_DRIVEN_RAILS:
        root_libs["power:PWR_FLAG"] = T._embeddable("power", "PWR_FLAG")

    out: list[str] = []
    out.append(f'(kicad_sch (version {KICAD_VERSION}) (generator "{GENERATOR}")')
    out.append("")
    out.append(f'  (uuid "{_u()}")')
    out.append('  (paper "A3")')
    out.append("")
    out.append('  (lib_symbols')
    for k in sorted(root_libs):
        out.append(root_libs[k])
    out.append('  )')
    out.append("")

    # Root-level PWR_FLAG pairs to drive externally-sourced rails (VBAT, GND).
    for lib_id, label, x, y in ROOT_DRIVEN_RAILS:
        out.extend(_emit_root_pwr_flag_pair(lib_id, label, x, y))
        out.append("")

    sheet_uuids: list[str] = []

    for entry in layout_list:
        sub_name, title, x, y, w, h, color = entry
        schem_json = components_dir / sub_name / "schematic.schem.json"
        sheet_file = f"../components/{sub_name}/schematic.kicad_sch"
        if not schem_json.exists():
            out.append(f'  ; missing subsystem: {sub_name}')
            continue
        slot = SheetSlot(
            name=sub_name, title=title,
            sheet_file=sheet_file,
            x=x, y=y, w=w, h=h,
            color=color,
        )

        # Power pins from convention table.
        pins = list(SUBSYSTEM_POWER_PINS.get(sub_name, []))
        # Add terminal-driven pins (signals) on the right side. Pin name MUST
        # match the child's hierarchical_label name exactly — the writer uses
        # symbol.id (e.g. "MA_OUT1"), so we use the same here.
        for sid, _label in _terminals_of_subsystem(schem_json):
            pins.append((sid, "passive", "right"))

        sheet_uuid = _u()
        sheet_uuids.append(sheet_uuid)
        out.append(_sheet_block(slot, sheet_uuid, pins))
        out.append("")

    # sheet_instances at the very end — required.
    out.append('  (sheet_instances')
    out.append('    (path "/" (page "1"))')
    for i, uid in enumerate(sheet_uuids, start=2):
        out.append(f'    (path "/{uid}" (page "{i}"))')
    out.append('  )')
    out.append(')')

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(out) + "\n")

    # Drop the hwagent sym-lib-table stub at the project root too — KiCad
    # looks for it next to the .kicad_pro file (which sits in this dir).
    from .ksa_writer import write_hwagent_lib_stub
    write_hwagent_lib_stub(output.parent)

    return output.resolve()
