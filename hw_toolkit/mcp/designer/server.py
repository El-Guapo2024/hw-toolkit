"""designer-mcp — file-based hardware design tools (slim, on hw_toolkit).

Headless-safe review/gate tools that operate on `.kicad_sch` / `.kicad_pcb`
files via `hw_toolkit` + kicad-cli — no KiCad GUI, no IPC. For LIVE editing
(eeschema/pcbnew open) use the sibling `live-edit-mcp` server.

This is the SLIM rebuild on the `hw_toolkit` library (consolidation M4): the
agent authors schematics by writing `hw_toolkit` Python (board.module/net/…),
so the old file-mutation/datasheet/research tool sprawl is gone. What remains
is the handful of things the agent can't trivially do inline:

  gates    — check_erc, check_drc
  render   — render_sch, render_pcb   (SVG→PNG)
  bom      — bom
  math     — calc_buck_inductor / _output_cap / _thermal,
             calc_voltage_divider, calc_feedback_resistors

Run: designer-mcp   ·   .mcp.json: {"designer-mcp": {"type":"stdio","command":"designer-mcp"}}
"""
from __future__ import annotations

import csv
import dataclasses
import json
import subprocess
import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

from hw_toolkit.calc import Buck
from hw_toolkit.kicad import erc_json, render_pcb_svg, render_sch_svg
from hw_toolkit.kicad.cli import find_cli
from hw_toolkit.kicad.planner import parse_erc_report

mcp = FastMCP(
    "designer",
    instructions=(
        "File-based hardware review tools on .kicad_sch/.kicad_pcb — ERC/DRC "
        "gates, render (SVG→PNG), BOM, and converter math. Headless "
        "(kicad-cli + hw_toolkit), never touches KiCad IPC. Author schematics "
        "by writing hw_toolkit Python; use these to gate/render/cost the "
        "result. For live eeschema/pcbnew editing use live-edit-mcp."
    ),
)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _svg_to_image(svg_path: str | Path) -> Image | None:
    """SVG file → FastMCP Image (PNG, 2x). None if cairosvg missing."""
    p = Path(svg_path)
    if not p.exists():
        return None
    try:
        import cairosvg
    except ImportError:
        return None
    png = p.with_suffix(".png")
    cairosvg.svg2png(url=str(p), write_to=str(png), scale=2)
    return Image(path=str(png))


def _kicad_drc_json(kicad_pcb: Path) -> dict:
    out = kicad_pcb.with_suffix(".drc.json")
    proc = subprocess.run(
        [str(find_cli()), "pcb", "drc", "--format", "json",
         "--severity-error", "--severity-warning", "-o", str(out), str(kicad_pcb)],
        capture_output=True, text=True, timeout=180,
    )
    if not out.exists():
        raise RuntimeError(f"kicad-cli pcb drc failed: {proc.stderr[:400]}")
    return json.loads(out.read_text())


# ─── gates ───────────────────────────────────────────────────────────────────

@mcp.tool()
def check_erc(kicad_sch: str, expected_codes: list[str] | None = None) -> str:
    """Run ERC on a `.kicad_sch`. `expected_codes` are violation `type`s to
    pre-acknowledge (e.g. hw_toolkit's ERC_REAL_SYMBOL_CODES). Returns a
    markdown report: clean / N real / M expected."""
    rep = erc_json(kicad_sch)
    res = parse_erc_report(rep, expected_codes=tuple(expected_codes or ()))
    lines = [
        f"**ERC** `{Path(kicad_sch).name}` — "
        f"{'✅ CLEAN' if res.clean else '❌ ' + str(res.real_count) + ' REAL'}"
        f" · {res.expected_count} expected (suppressed)",
    ]
    for v in res.real_violations:
        lines.append(f"- ❌ `{v.type}` {v.description}")
        for it in v.items[:3]:
            lines.append(f"    - {it}")
    return "\n".join(lines)


@mcp.tool()
def check_drc(kicad_pcb: str) -> str:
    """Run DRC on a `.kicad_pcb` (kicad-cli). Returns a markdown count + the
    first violations."""
    d = _kicad_drc_json(Path(kicad_pcb))
    vios = d.get("violations", [])
    unconnected = d.get("unconnected_items", [])
    head = (f"**DRC** `{Path(kicad_pcb).name}` — "
            f"{'✅ CLEAN' if not vios and not unconnected else '❌'} "
            f"{len(vios)} violations, {len(unconnected)} unconnected")
    lines = [head]
    for v in vios[:15]:
        lines.append(f"- `{v.get('type','?')}` ({v.get('severity','?')}) "
                     f"{v.get('description','')[:90]}")
    return "\n".join(lines)


# ─── render ──────────────────────────────────────────────────────────────────

@mcp.tool()
def render_sch(kicad_sch: str):
    """Render a `.kicad_sch` to PNG (via SVG). Returns the path + the image."""
    svg = render_sch_svg(kicad_sch)
    img = _svg_to_image(svg)
    text = f"Schematic `{Path(kicad_sch).name}` → `{svg}`"
    return [text, img] if img else text


@mcp.tool()
def render_pcb(kicad_pcb: str):
    """Render a placed `.kicad_pcb` (copper+silk+courtyard+ratsnest) to PNG."""
    svg = render_pcb_svg(kicad_pcb)
    img = _svg_to_image(svg)
    text = f"PCB `{Path(kicad_pcb).name}` → `{svg}`"
    return [text, img] if img else text


# ─── BOM ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def bom(kicad_sch: str) -> str:
    """Grouped BOM for a `.kicad_sch` (kicad-cli sch export bom), one line per
    Value/Footprint with quantity and reference list."""
    src = Path(kicad_sch)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "bom.csv"
        proc = subprocess.run(
            [str(find_cli()), "sch", "export", "bom",
             "--fields", "Reference,Value,Footprint",
             "--group-by", "Value,Footprint",
             "--exclude-dnp", "-o", str(out), str(src)],
            capture_output=True, text=True, timeout=120,
        )
        if not out.exists():
            return f"BOM export failed: {proc.stderr[:300]}"
        rows = list(csv.DictReader(out.read_text().splitlines()))
    if not rows:
        return f"BOM `{src.name}` — empty"
    lines = [f"**BOM** `{src.name}` — {len(rows)} line items"]
    total = 0
    for r in rows:
        refs = r.get("Reference", "")
        qty = len([x for x in refs.replace(";", ",").split(",") if x.strip()]) or 1
        total += qty
        lines.append(f"- {qty}× **{r.get('Value','?')}** "
                     f"`{r.get('Footprint','') or '—'}`  ({refs})")
    lines.insert(1, f"total parts: {total}")
    return "\n".join(lines)


# ─── converter / divider math ────────────────────────────────────────────────

@mcp.tool()
def calc_buck_inductor(vin: float, vout: float, iout: float,
                       fsw_khz: float = 1000.0, ripple_pct: float = 30.0) -> dict:
    """Buck inductor sizing (hw_toolkit.calc.Buck)."""
    return dataclasses.asdict(
        Buck(vin=vin, vout=vout, iout=iout, fsw_khz=fsw_khz,
             ripple_pct=ripple_pct).inductor()
    )


@mcp.tool()
def calc_buck_output_cap(vin: float, vout: float, iout: float,
                         fsw_khz: float = 1000.0, ripple_pct: float = 30.0,
                         target_ripple_mv: float = 30.0) -> dict:
    """Buck output-cap sizing for a target output ripple (mV)."""
    return dataclasses.asdict(
        Buck(vin=vin, vout=vout, iout=iout, fsw_khz=fsw_khz,
             ripple_pct=ripple_pct).output_cap(target_ripple_mv=target_ripple_mv)
    )


@mcp.tool()
def calc_buck_thermal(vin: float, vout: float, iout: float,
                      rdson_mohm: float, theta_ja: float) -> dict:
    """Buck IC junction-temperature estimate; `tj_safe` gates the design."""
    return dataclasses.asdict(
        Buck(vin=vin, vout=vout, iout=iout).thermal(
            rdson_mohm=rdson_mohm, theta_ja=theta_ja)
    )


@mcp.tool()
def calc_voltage_divider(vin: float, r1_ohm: float, r2_ohm: float) -> dict:
    """Resistive divider: Vout = Vin·R2/(R1+R2)."""
    vout = vin * r2_ohm / (r1_ohm + r2_ohm)
    return {"vout": round(vout, 4), "ratio": round(r2_ohm / (r1_ohm + r2_ohm), 4),
            "i_ua": round(vin / (r1_ohm + r2_ohm) * 1e6, 2)}


@mcp.tool()
def calc_feedback_resistors(vref: float, vout: float, r2_ohm: float = 10000.0) -> dict:
    """Feedback divider for a regulator: pick R1 so Vout = Vref·(1 + R1/R2)."""
    if vout <= vref:
        return {"error": f"vout {vout} must exceed vref {vref}"}
    r1 = r2_ohm * (vout / vref - 1)
    return {"r1_ohm": round(r1, 1), "r2_ohm": r2_ohm,
            "vout_check": round(vref * (1 + r1 / r2_ohm), 4)}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
