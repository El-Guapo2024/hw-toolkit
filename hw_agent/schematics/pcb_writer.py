"""PCB pipeline glue — pure-Python helpers + kicad-cli fab export.

After the SWIG → IPC migration this file holds only:

  - `compose_spec`            pure-Python: Schematic → board spec dict
  - `apply_default_footprints` pure-Python: fill in stock 0805 + KiCad-lib
                                           default footprints into JSON
  - `export_fabrication`      kicad-cli: gerbers + drill + pos for fab
  - Compatibility shims      `run_runner` / `run_dsn_export` /
                              `run_ses_import` / `run_sync_netlist` /
                              `export_file` — these now route through
                              `pcb_backend` (IPC). Require pcbnew open.
                              Will be deleted when callers (freerouting.py,
                              preview.py, mcp_server.py) move to the
                              backend module directly.

The SWIG `pcb_runner.py` subprocess pattern is gone — all editing goes
through kicad-python IPC. Headless ERC/DRC/exports stay on kicad-cli.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from .schem_renderer import Schematic
from .validators import validate, validate_pcb


# ─── Spec composition (pure Python) ────────────────────────────────────────

DEFAULT_PASSIVE_FOOTPRINTS = {
    "resistor": "Resistor_SMD:R_0805_2012Metric",
    "capacitor": "Capacitor_SMD:C_0805_2012Metric",
    "inductor": "Inductor_SMD:L_0805_2012Metric",
}


def compose_spec(
    schem: Schematic,
    board_w_mm: float = 80.0,
    board_h_mm: float = 60.0,
    schem_to_pcb_scale: float = 1.0,
    layout_offset_mm: tuple[float, float] = (10.0, 10.0),
) -> dict:
    """Build a board spec from a schematic.

    Each physical component lands on the PCB at a position derived from its
    schematic `at` coords, scaled and offset onto the board canvas. This is
    a layout *hint*, not a final placement — the agent will tune positions
    or run an auto-placer in a follow-up step.

    Net references (vcc/ground/terminal) are skipped — they're labels, not
    physical parts.
    """
    physical_types = {"resistor", "capacitor", "inductor", "diode", "ic", "kicad"}
    ox, oy = layout_offset_mm

    components = []
    skipped_no_fp: list[str] = []
    for s in schem.symbols:
        if s.type not in physical_types:
            continue
        fp = s.footprint
        if not fp:
            skipped_no_fp.append(s.id)
            continue
        sx, sy = s.at
        components.append({
            "ref": s.id,
            "value": s.value or s.part or s.id,
            "footprint": fp,
            "at": [ox + sx * schem_to_pcb_scale,
                   oy + sy * schem_to_pcb_scale],
            "rotation": 0,
        })

    return {
        "board": {"width": board_w_mm, "height": board_h_mm, "outline": True},
        "components": components,
        "_skipped_no_footprint": skipped_no_fp,
    }


# ─── IPC pass-through shims (require pcbnew open) ──────────────────────────

def run_runner(spec: dict, out_kicad_pcb: Path) -> dict:
    """Build a .kicad_pcb from spec via IPC. Requires pcbnew open."""
    from .pcb_backend import build_pcb
    return build_pcb(spec, Path(out_kicad_pcb))


def run_dsn_export(kicad_pcb: Path, out_dsn: Path) -> dict:
    """Specctra DSN export via IPC. Requires pcbnew open."""
    from .pcb_backend import export_dsn
    return export_dsn(Path(kicad_pcb), Path(out_dsn))


def run_ses_import(kicad_pcb: Path, ses_path: Path) -> dict:
    """SES import via IPC. Requires pcbnew open."""
    from .pcb_backend import import_ses
    return import_ses(Path(kicad_pcb), Path(ses_path))


def run_sync_netlist(kicad_pcb: Path, netlist_path: Path) -> dict:
    """Apply schematic netlist to PCB pads via IPC. Requires pcbnew open."""
    from .pcb_backend import sync_netlist
    return sync_netlist(Path(kicad_pcb), Path(netlist_path))


def export_file(
    schem_json: str | Path,
    out_kicad_pcb: str | Path,
    board_w_mm: float = 80.0,
    board_h_mm: float = 60.0,
    strict: bool = True,
) -> dict:
    """End-to-end: JSON → board spec → .kicad_pcb (via IPC).

    Validates first; raises on schema or PCB-level issues before
    invoking IPC. Requires pcbnew open.
    """
    schem_json = Path(schem_json)
    out_kicad_pcb = Path(out_kicad_pcb)

    data = json.loads(schem_json.read_text())
    schem = Schematic.model_validate(data)

    if strict:
        issues = validate(schem) + validate_pcb(schem)
        if issues:
            raise ValueError(
                f"{schem_json.name} has {len(issues)} PCB schema issue(s):\n  - "
                + "\n  - ".join(issues)
            )

    spec = compose_spec(schem, board_w_mm=board_w_mm, board_h_mm=board_h_mm)
    report = run_runner(spec, out_kicad_pcb)
    report["spec"] = spec
    return report


# ─── Fabrication exports (kicad-cli, headless) ─────────────────────────────

def export_fabrication(kicad_pcb: Path, out_dir: Optional[Path] = None) -> dict:
    """Generate the fabrication file bundle for JLCPCB / PCBWay upload:

        - Gerbers (one per copper + silk + mask + edge layer)
        - Drill files (PTH + NPTH)
        - Pick-and-place (CSV, both sides, SMD only)

    Output goes to <project>/fabrication/<board_stem>/ by default.
    Headless: uses kicad-cli throughout, no pcbnew required.
    """
    from .kicad_paths import kicad_cli

    kicad_pcb = Path(kicad_pcb).resolve()
    out_dir = (out_dir or kicad_pcb.parent.parent / "fabrication" / kicad_pcb.stem).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    cli = kicad_cli()
    artifacts: dict = {"out_dir": str(out_dir)}

    gerber_dir = out_dir / "gerbers"
    gerber_dir.mkdir(exist_ok=True)
    g_proc = subprocess.run(
        [cli, "pcb", "export", "gerbers", "--output", str(gerber_dir),
         "--no-protel-ext", str(kicad_pcb)],
        capture_output=True, text=True,
    )
    artifacts["gerber_dir"] = str(gerber_dir)
    artifacts["gerber_files"] = sorted(p.name for p in gerber_dir.iterdir())
    artifacts["gerber_rc"] = g_proc.returncode
    if g_proc.returncode != 0:
        artifacts["gerber_error"] = (g_proc.stderr or g_proc.stdout).strip()[:300]

    drill_dir = out_dir / "drill"
    drill_dir.mkdir(exist_ok=True)
    d_proc = subprocess.run(
        [cli, "pcb", "export", "drill", "--output", str(drill_dir) + "/",
         str(kicad_pcb)],
        capture_output=True, text=True,
    )
    artifacts["drill_dir"] = str(drill_dir)
    artifacts["drill_files"] = sorted(p.name for p in drill_dir.iterdir())
    artifacts["drill_rc"] = d_proc.returncode

    pos_path = out_dir / f"{kicad_pcb.stem}-pos.csv"
    p_proc = subprocess.run(
        [cli, "pcb", "export", "pos", "--output", str(pos_path),
         "--format", "csv", "--units", "mm", "--side", "both", "--smd-only",
         str(kicad_pcb)],
        capture_output=True, text=True,
    )
    artifacts["pos_csv"] = str(pos_path) if pos_path.exists() else None
    artifacts["pos_rc"] = p_proc.returncode

    artifacts["ok"] = all(rc == 0 for rc in [g_proc.returncode, d_proc.returncode, p_proc.returncode])
    return artifacts


# ─── Default-footprint fill (pure Python helper) ──────────────────────────

def apply_default_footprints(schem_json: str | Path, write: bool = True) -> dict:
    """Fill in stock footprints for any physical component missing one.

    For passives, picks 0805 SMD (hand-solderable, JLCPCB-friendly). For ICs
    with a `lib_id`, looks up the symbol's default footprint from the .kicad_sym
    file. For inline ICs (no lib_id), leaves them — the agent must specify.
    """
    schem_json = Path(schem_json)
    data = json.loads(schem_json.read_text())

    assigned: list = []
    skipped: list = []

    for sym in data.get("symbols", []):
        t = sym.get("type")
        if sym.get("footprint"):
            continue
        if t in DEFAULT_PASSIVE_FOOTPRINTS:
            sym["footprint"] = DEFAULT_PASSIVE_FOOTPRINTS[t]
            assigned.append({"ref": sym["id"], "footprint": sym["footprint"],
                              "source": "passive_default"})
            continue
        if t == "kicad" and sym.get("lib_id"):
            fp = _lookup_kicad_symbol_footprint(sym["lib_id"])
            if fp:
                sym["footprint"] = fp
                assigned.append({"ref": sym["id"], "footprint": fp,
                                  "source": "kicad_lib_default"})
                continue
            skipped.append({"ref": sym["id"], "reason": "kicad lib has no Footprint property"})
            continue
        if t in ("ic", "diode"):
            skipped.append({"ref": sym["id"],
                            "reason": "custom IC/diode requires explicit footprint"})

    if write and assigned:
        schem_json.write_text(json.dumps(data, indent=2))

    return {"assigned": assigned, "skipped": skipped, "wrote_file": write and bool(assigned)}


def _lookup_kicad_symbol_footprint(lib_id: str) -> Optional[str]:
    """Read a stock KiCad symbol's `Footprint` property from the .kicad_sym file."""
    if ":" not in lib_id:
        return None
    lib_name, sym_name = lib_id.split(":", 1)
    from .kicad_paths import kicad_symbol_dir
    import re

    path = kicad_symbol_dir() / f"{lib_name}.kicad_sym"
    if not path.exists():
        return None
    text = path.read_text()
    sym_match = re.search(rf'\(symbol\s+"{re.escape(sym_name)}".*?\n\t\)',
                          text, re.DOTALL)
    if not sym_match:
        return None
    fp_match = re.search(r'\(property\s+"Footprint"\s+"([^"]*)"', sym_match.group(0))
    if fp_match and fp_match.group(1):
        return fp_match.group(1)
    return None
