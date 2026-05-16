"""designer-mcp — file-based hardware design tools.

The "designer" half of the hw-agent. Headless-safe: every tool here
operates on `.kicad_sch` / `.kicad_pcb` files via kicad-sch-api +
kicad-cli. No KiCad GUI required, no IPC. Works in CI, in containers,
on machines without KiCad installed for review (kicad-cli alone is
enough for ERC/DRC/exports).

For LIVE editing (eeschema/pcbnew open, edits visible in real time),
use the sibling `live-edit-mcp` server. It speaks IPC and surfaces a
parallel set of `live_*` tools. The agent decides which to use:
    - "I'm in eeschema, move U1 to (80, 60)"   → live-edit-mcp
    - "generate a buck schematic from scratch"  → designer-mcp
    - automated CI / batch / headless           → designer-mcp

Run: designer-mcp
Config in .mcp.json:
    "designer-mcp": {"type": "stdio", "command": "designer-mcp"}
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, Union

from mcp.server.fastmcp import FastMCP, Image, Context
from pydantic import BaseModel, ConfigDict, Field

mcp = FastMCP(
    "designer",
    instructions=(
        "File-based hardware design tools — schematic authoring (DSL "
        "+ atomic add_* mutations on .kicad_sch), evaluation (ERC, "
        "DRC), rendering (focused PNG/SVG), BOM, decisions, datasheet "
        "research, engineering math. Headless-safe: never touches "
        "KiCad's IPC. For live editing in eeschema/pcbnew, use the "
        "sibling live-edit-mcp server."
    ),
)

# ─── State ───────────────────────────────────────────────────────────────────

_navigators: dict[str, "DatasheetNavigator"] = {}


def _svg_path_to_image(svg_path: str | Path) -> Image | None:
    """Convert an SVG file to a FastMCP Image (PNG, 2x scale).

    Returns None if cairosvg is unavailable so callers fall back to text-only.
    """
    p = Path(svg_path)
    if not p.exists():
        return None
    try:
        import cairosvg
    except ImportError:
        return None
    png_path = p.with_suffix(".png")
    cairosvg.svg2png(url=str(p), write_to=str(png_path), scale=2)
    return Image(path=str(png_path))


def _kicad_sch_to_svg(kicad_sch: Path, out_dir: Path | None = None) -> Path | None:
    """Run `kicad-cli sch export svg` on a .kicad_sch. Returns the SVG path or None."""
    import subprocess
    from hw_agent.schematics.kicad_paths import kicad_cli

    out_dir = out_dir or kicad_sch.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    cli = kicad_cli()
    subprocess.run(
        [cli, "sch", "export", "svg", "--output", str(out_dir) + "/", str(kicad_sch)],
        capture_output=True, text=True,
    )
    svg_path = out_dir / f"{kicad_sch.stem}.svg"
    return svg_path if svg_path.exists() else None


def _kicad_pcb_to_svg(kicad_pcb: Path, out_dir: Path | None = None) -> Path | None:
    """Run `kicad-cli pcb export svg` on a .kicad_pcb. Returns the SVG path or None.

    Renders all standard layers in a single multi-layer SVG so the agent can
    see footprints + traces + silkscreen + edge cuts together — the same view
    you'd see in pcbnew's GUI.
    """
    import subprocess
    from hw_agent.schematics.kicad_paths import kicad_cli

    out_dir = out_dir or kicad_pcb.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    svg_path = out_dir / f"{kicad_pcb.stem}.svg"
    cli = kicad_cli()
    # `--exclude-drawing-sheet` keeps the title block out (cleaner for PNG).
    # Single-page mode (`--page-size-mode 2`) crops to the board outline.
    subprocess.run(
        [cli, "pcb", "export", "svg", "--output", str(svg_path),
         "--page-size-mode", "2", "--exclude-drawing-sheet",
         "--layers", "F.Cu,B.Cu,F.SilkS,B.SilkS,Edge.Cuts,F.Mask,B.Mask",
         str(kicad_pcb)],
        capture_output=True, text=True,
    )
    return svg_path if svg_path.exists() else None


def _format_erc_markdown(d: dict) -> str:
    """Render an ERC result (run_eval shape OR EvalResult.to_dict()) as markdown."""
    is_eval_result = "schema" in d and "erc" in d
    ok = d.get("ok", False)
    lines = [f"## ERC: {'PASS' if ok else 'FAIL'}"]
    if d.get("duration_ms"):
        lines.append(f"*{d['duration_ms']} ms*")
    lines.append("")

    if d.get("error"):
        lines.append(f"**Error:** {d['error']}")
        return "\n".join(lines)

    if is_eval_result:
        schema = d.get("schema") or {}
        if not schema.get("ok", True):
            lines.append("### Schema issues")
            for issue in schema.get("issues", []):
                lines.append(f"- {issue}")
            lines.append("")
        erc = d.get("erc") or {}
    else:
        erc = d

    total = erc.get("total", 0)
    lines.append(f"**Total violations:** {total}")
    by_type = erc.get("by_type") or {}
    if by_type:
        lines.append("")
        lines.append("| Type | Count |")
        lines.append("|------|-------|")
        for t, c in sorted(by_type.items(), key=lambda x: -x[1]):
            lines.append(f"| {t} | {c} |")
    lines.append("")

    real_issues = erc.get("real_issues") or []
    if real_issues:
        lines.append("### Real issues (actionable)")
        for issue in real_issues:
            lines.append(f"- **{issue.get('type', '?')}** — {issue.get('desc', '')}")
            for item in (issue.get("items") or [])[:3]:
                lines.append(f"  - `{item}`")
        lines.append("")

    expected = erc.get("expected") or {}
    if expected:
        lines.append("### Expected (cross-subsystem, filtered)")
        for k, v in expected.items():
            lines.append(f"- {k}: {v}")
        lines.append("")

    artifacts = d.get("artifacts") or erc.get("artifacts") or {}
    if artifacts:
        lines.append("### Artifacts")
        for k, v in artifacts.items():
            if v:
                lines.append(f"- {k}: `{v}`")

    return "\n".join(lines)


def _format_drc_markdown(d: dict, board_label: str = "") -> str:
    """Render a DRC result (from drc_filters.classify) as markdown. Mirrors
    `_format_erc_markdown`; reuses the same buckets (real_issues vs expected
    via filter_log)."""
    real = d.get("real_issues", []) or d.get("drc_real_issues", [])
    expected = d.get("expected", {}) or d.get("drc_expected", {})
    by_type = d.get("by_type", {}) or d.get("drc_by_type", {})
    total = d.get("total", d.get("drc_total", 0))
    label = board_label or "PCB"

    ok = len(real) == 0
    lines = [f"## DRC: {'PASS' if ok else 'FAIL'}{' — ' + label if board_label else ''}"]
    lines.append(f"**Total violations:** {total}")
    if by_type:
        lines.append("")
        lines.append("| Type | Count |")
        lines.append("|------|-------|")
        for t, c in sorted(by_type.items(), key=lambda x: -x[1]):
            lines.append(f"| {t} | {c} |")
    lines.append("")
    if real:
        lines.append("### Real issues (actionable)")
        for issue in real:
            lines.append(f"- **{issue.get('type', '?')}** — {issue.get('desc', '')}")
            for item in (issue.get("items") or [])[:3]:
                lines.append(f"  - `{item}`")
        lines.append("")
    if expected:
        lines.append("### Expected (filtered as known/intentional)")
        for k, v in expected.items():
            lines.append(f"- {k}: {v}")
        lines.append("")
    return "\n".join(lines)


def _format_bom_summary_md(s: dict) -> str:
    lines = [f"## BOM Summary — `{s.get('project', '')}`"]
    if s.get("updated"):
        lines.append(f"*Updated {s['updated']}*")
    lines.append("")
    item_count = s.get("item_count", 0)
    sub_count = s.get("subsystem_count", 0)
    risk_pct = round(s.get("supply_risk_score", 0) * 100, 1)
    low = s.get("low_stock_items") or []
    lines.append(f"**{item_count}** items across **{sub_count}** subsystems")
    lines.append(f"**Supply risk:** {risk_pct}% ({len(low)} items below stock threshold)")
    lines.append("")

    # Parts table — show subsystem, part, package, manufacturer, price, stock
    parts = s.get("parts") or []
    if parts:
        lines.append("### Parts")
        lines.append("| Subsystem | Part (MPN) | LCSC | Manufacturer | Package | $/u | Stock |")
        lines.append("|-----------|------------|------|--------------|---------|-----|-------|")
        for p in parts:
            mpn = p.get("mpn", "—")
            lcsc = p.get("lcsc", "—")
            mfr = p.get("manufacturer") or "—"
            pkg = p.get("package") or "—"
            price = p.get("price", 0.0) or 0.0
            stock = p.get("stock", 0) or 0
            lines.append(f"| `{p.get('subsystem', '')}` | {mpn} | {lcsc} | {mfr} | {pkg} | ${price:.3f} | {stock:,} |")
        lines.append("")

    lines.append("### Cost")
    lines.append("| Boards | $/board | Total |")
    lines.append("|--------|---------|-------|")
    c1 = s.get("cost_per_board_qty_1", 0) or 0
    c10 = s.get("cost_per_board_qty_10", 0) or 0
    c100 = s.get("cost_per_board_qty_100", 0) or 0
    t10 = s.get("total_cost_10_boards", 0) or 0
    t100 = s.get("total_cost_100_boards", 0) or 0
    lines.append(f"| 1   | ${c1:.4f} | ${c1:.2f} |")
    lines.append(f"| 10  | ${c10:.4f} | ${t10:.2f} |")
    lines.append(f"| 100 | ${c100:.4f} | ${t100:.2f} |")
    lines.append("")
    if low:
        lines.append("### Low stock")
        for it in low[:10]:
            stock = it.get("stock", 0) or 0
            lines.append(f"- `{it.get('subsystem','')}` / {it.get('lcsc','')} — {stock:,} units")
        if len(low) > 10:
            lines.append(f"- *(+ {len(low) - 10} more)*")
        lines.append("")
    return "\n".join(lines)


def _format_decisions_md(decisions: list[dict], filter_subsystem: str | None = None) -> str:
    if not decisions:
        return "_No decisions recorded._"
    title = f"## Decisions ({len(decisions)})"
    if filter_subsystem:
        title += f" — `{filter_subsystem}`"
    lines = [title, ""]
    for d in decisions:
        sub = d.get("subsystem", "?")
        chosen = d.get("chosen") or {}
        ts = d.get("timestamp", "")
        lines.append(f"### `{sub}`")
        if ts:
            lines.append(f"*{ts}*")
        if chosen:
            lcsc = chosen.get("lcsc", "")
            mpn = chosen.get("mpn", "")
            price = chosen.get("price", "")
            price_str = f" — ${price}" if price else ""
            lines.append(f"**Chosen:** {mpn} (`{lcsc}`){price_str}")
        if d.get("rationale"):
            lines.append(f"**Rationale:** {d['rationale']}")
        rejected = d.get("rejected") or []
        if rejected:
            lines.append("**Rejected:**")
            for r in rejected[:5]:
                lines.append(f"- {r.get('mpn','')} (`{r.get('lcsc','')}`) — {r.get('reason','')}")
            if len(rejected) > 5:
                lines.append(f"- *(+ {len(rejected) - 5} more)*")
        tradeoffs = d.get("tradeoffs") or []
        if tradeoffs:
            lines.append("**Tradeoffs:**")
            for t in tradeoffs:
                lines.append(f"- {t}")
        accepted_warnings = d.get("accepted_warnings") or []
        if accepted_warnings:
            lines.append(f"**Accepted warnings:** {', '.join(accepted_warnings)}")
        if d.get("alternate_lcsc"):
            lines.append(f"**Backup:** `{d['alternate_lcsc']}`")
        lines.append("")
    return "\n".join(lines)


def _format_design_summary_md(s: dict) -> str:
    lines = [f"## Design Summary — `{s.get('project','')}`", ""]
    comps = s.get("components") or {}
    if comps:
        total = comps.get("total", 0)
        explored = comps.get("explored", 0)
        pending = comps.get("pending", 0)
        eliminated = comps.get("eliminated", 0)
        lines.append("### Components")
        lines.append(f"**{explored} explored** / {pending} pending / {eliminated} eliminated (total {total})")
        lines.append("")
    rails = s.get("rails") or {}
    if rails:
        lines.append("### Power rails")
        lines.append("| Rail | Capacity (mA) | Typ (mA) | Peak (mA) | Margin |")
        lines.append("|------|---------------|----------|-----------|--------|")
        for name, r in rails.items():
            cap = r.get("capacity_ma", 0)
            typ = r.get("total_typ_ma", 0)
            peak = r.get("total_peak_ma", 0)
            margin = r.get("margin_pct", 0)
            margin_str = f"**{margin}% (LOW)**" if isinstance(margin, (int, float)) and margin < 10 else f"{margin}%"
            lines.append(f"| {name} | {cap} | {typ} | {peak} | {margin_str} |")
        lines.append("")
    pin_pool = s.get("pin_pool") or {}
    if pin_pool:
        lines.append("### Pin budget")
        for k, v in pin_pool.items():
            lines.append(f"- {k}: {v}")
    return "\n".join(lines)


def _format_verifications_md(records: list[dict]) -> str:
    if not records:
        return "_No verifications recorded._"
    lines = [f"## Verifications ({len(records)})", ""]
    for v in records:
        sub = v.get("subsystem", "?")
        lcsc = v.get("lcsc", "?")
        mpn = v.get("mpn", "")
        passed = v.get("pass", False)
        verdict = "PASS" if passed else "FAIL"
        ts = v.get("timestamp", "")
        lines.append(f"### `{sub}` — {mpn or '(no mpn)'} (`{lcsc}`) — {verdict}")
        if ts:
            lines.append(f"*{ts}*")
        if v.get("summary"):
            lines.append(v["summary"])
        hard = v.get("hard_failures") or []
        if hard:
            lines.append("**Hard failures:**")
            for c in hard:
                lines.append(f"- {c.get('name','')} — required {c.get('required','')}, got {c.get('actual','')}")
        soft = v.get("soft_failures") or []
        if soft:
            lines.append("**Soft warnings:**")
            for c in soft:
                note = c.get("note") or f"required {c.get('required','')}, got {c.get('actual','')}"
                lines.append(f"- {c.get('name','')} — {note}")
        lines.append("")
    return "\n".join(lines)


def _get_nav(pdf_path: str) -> "DatasheetNavigator":
    from hw_agent.datasheets.navigator import DatasheetNavigator
    if pdf_path not in _navigators:
        nav = DatasheetNavigator(pdf_path)
        nav.scan()
        _navigators[pdf_path] = nav
    return _navigators[pdf_path]


# ─── Datasheet Navigation Tools ─────────────────────────────────────────────

@mcp.tool()
def ds_scan(pdf_path: str) -> str:
    """Scan a datasheet PDF — reads TOC, returns section→page map. Call this first."""
    nav = _get_nav(pdf_path)
    return nav.summary()


@mcp.tool()
def ds_find_section(pdf_path: str, query: str) -> list[int]:
    """Fuzzy-match a section name, return page numbers (1-indexed)."""
    nav = _get_nav(pdf_path)
    return [p + 1 for p in nav.find_section(query)]


@mcp.tool()
def ds_read_page(pdf_path: str, page: int) -> str:
    """Read full text of a specific page (1-indexed)."""
    nav = _get_nav(pdf_path)
    return nav.read_page(page - 1)


@mcp.tool()
def ds_find_spec(pdf_path: str, spec_name: str, pages: Optional[list[int]] = None) -> list[dict]:
    """Fuzzy-search for a spec across relevant pages. Returns matches sorted by score."""
    nav = _get_nav(pdf_path)
    page_indices = [p - 1 for p in pages] if pages else None
    results = nav.find_spec(spec_name, page_indices)
    return results[:10]


@mcp.tool()
def ds_download(part_number: str, manufacturer: Optional[str] = None) -> str:
    """Download a datasheet PDF. Returns path or error."""
    from hw_agent.datasheets.downloader import download_datasheet
    path = download_datasheet(part_number, manufacturer=manufacturer)
    return str(path) if path else f"Failed to download datasheet for {part_number}"


# ─── Engineering Calculators ─────────────────────────────────────────────────

class LDOParams(BaseModel):
    vin: float = Field(description="Input voltage (V)")
    vout: float = Field(description="Output voltage (V)")
    iout_ma: float = Field(description="Output current (mA)")
    theta_ja: float = Field(default=250.0, description="θJA in °C/W")
    ambient_c: float = Field(default=85.0, description="Ambient temperature (°C)")


@mcp.tool()
def calc_ldo_thermal(params: LDOParams) -> dict:
    """Calculate LDO power dissipation and junction temperature."""
    pdiss = (params.vin - params.vout) * (params.iout_ma / 1000)
    tj = params.ambient_c + pdiss * params.theta_ja
    return {
        "pdiss_w": round(pdiss, 4),
        "delta_t_c": round(pdiss * params.theta_ja, 1),
        "tj_c": round(tj, 1),
        "safe": tj < 125,
        "margin_c": round(125 - tj, 1),
    }


@mcp.tool()
def calc_thermal_gate(
    package: str,
    power_w: float,
    ambient_c: float = 40.0,
    theta_ja_override: Optional[float] = None,
) -> dict:
    """MANDATORY thermal check before selecting any power component.

    Returns PASS/FAIL with Tj calculation. If FAIL, the part MUST be eliminated.

    Common packages (θJA auto-looked-up if not overridden):
      SOT-23-5/6: 285°C/W | SOT-223: 60°C/W | SOIC-8-EP: 40°C/W
      QFN (thermal pad): 30°C/W | TSSOP-16-EP: 50°C/W | TO-252/DPAK: 25°C/W

    Args:
        package: Package name (e.g. "SOT-23-6", "SOIC-8-EP", "QFN-20")
        power_w: Power dissipation in watts (calculate first!)
        ambient_c: Ambient temperature (default 40°C for indoor)
        theta_ja_override: Override θJA if you know the exact value from datasheet
    """
    # θJA lookup table
    THETA_JA = {
        "SOT-23": 285, "SOT-23-5": 285, "SOT-23-6": 285, "TSOT-23-6": 250,
        "SOT-223": 60, "SOT-223-3": 60,
        "SOIC-8": 100, "SOIC-8-EP": 40, "SOP-8-EP": 40,
        "QFN": 30, "DFN": 35, "UQFN": 45,
        "TSSOP-16": 80, "TSSOP-16-EP": 50, "TSSOP-20": 75,
        "TO-252": 25, "DPAK": 25, "D2PAK": 15,
        "TO-220": 20,
    }

    # Fuzzy match package name — try longest keys first for specificity
    theta_ja = theta_ja_override
    if theta_ja is None:
        pkg_upper = package.upper().replace(" ", "").replace("_", "-")
        sorted_keys = sorted(THETA_JA.keys(), key=len, reverse=True)
        for key in sorted_keys:
            key_norm = key.upper().replace(" ", "").replace("_", "-")
            if key_norm in pkg_upper or pkg_upper in key_norm:
                theta_ja = THETA_JA[key]
                break
        if theta_ja is None:
            # Default conservative estimate
            theta_ja = 100

    tj = ambient_c + power_w * theta_ja
    passed = tj < 125.0
    margin_c = 125.0 - tj

    return {
        "package": package,
        "theta_ja_used": theta_ja,
        "power_w": round(power_w, 3),
        "ambient_c": ambient_c,
        "tj_c": round(tj, 1),
        "tj_limit_c": 125.0,
        "margin_c": round(margin_c, 1),
        "passed": passed,
        "verdict": "PASS — safe to proceed" if passed else f"FAIL — Tj={round(tj,1)}°C exceeds 125°C. ELIMINATE this package. Try larger thermal package.",
    }


class BuckParams(BaseModel):
    vin: float = Field(description="Input voltage (V)")
    vout: float = Field(description="Output voltage (V)")
    iout: float = Field(description="Output current (A)")
    fsw_khz: float = Field(default=500.0, description="Switching frequency (kHz)")
    ripple_pct: float = Field(default=30.0, description="Inductor ripple current %")


@mcp.tool()
def calc_buck_inductor(params: BuckParams) -> dict:
    """Calculate buck converter inductor value and ripple current."""
    duty = params.vout / params.vin
    fsw = params.fsw_khz * 1000
    ripple_a = params.iout * (params.ripple_pct / 100)
    l_h = (params.vout * (1 - duty)) / (fsw * ripple_a)
    l_uh = l_h * 1e6
    return {
        "duty_cycle": round(duty, 3),
        "inductor_uh": round(l_uh, 1),
        "ripple_current_a": round(ripple_a, 3),
        "peak_current_a": round(params.iout + ripple_a / 2, 3),
    }


@mcp.tool()
def calc_buck_output_cap(params: BuckParams, inductor_uh: float, target_ripple_mv: float = 50.0) -> dict:
    """Calculate buck converter output capacitor for target voltage ripple."""
    fsw = params.fsw_khz * 1000
    duty = params.vout / params.vin
    l_h = inductor_uh * 1e-6
    ripple_a = (params.vout * (1 - duty)) / (fsw * l_h)
    c_f = ripple_a / (8 * fsw * (target_ripple_mv / 1000))
    return {
        "min_cap_uf": round(c_f * 1e6, 1),
        "ripple_current_a": round(ripple_a, 3),
        "target_ripple_mv": target_ripple_mv,
    }


@mcp.tool()
def calc_voltage_divider(vin: float, r1_ohm: float, r2_ohm: float) -> dict:
    """Calculate voltage divider output: Vout = Vin × R2 / (R1 + R2)."""
    vout = vin * r2_ohm / (r1_ohm + r2_ohm)
    current_ua = (vin / (r1_ohm + r2_ohm)) * 1e6
    return {"vout": round(vout, 4), "current_ua": round(current_ua, 2)}


@mcp.tool()
def calc_feedback_resistors(vref: float, vout: float, r2_ohm: float = 10000) -> dict:
    """Calculate feedback divider R1 for a target Vout given Vref and R2."""
    r1 = r2_ohm * ((vout / vref) - 1)
    current_ua = (vout / (r1 + r2_ohm)) * 1e6
    return {
        "r1_ohm": round(r1, 0),
        "r2_ohm": r2_ohm,
        "divider_current_ua": round(current_ua, 2),
        "actual_vout": round(vref * (1 + r1 / r2_ohm), 4),
    }


@mcp.tool()
def calc_trace_width(current_a: float, temp_rise_c: float = 10.0, copper_oz: float = 1.0, external: bool = True) -> dict:
    """Calculate PCB trace width for a given current (IPC-2221)."""
    import math
    thickness_mil = copper_oz * 1.378
    k, b, c = (0.048, 0.44, 0.725) if external else (0.024, 0.44, 0.725)
    area_mil2 = (current_a / (k * (temp_rise_c ** b))) ** (1.0 / c)
    width_mil = area_mil2 / thickness_mil
    return {
        "width_mm": round(width_mil * 0.0254, 3),
        "width_mil": round(width_mil, 1),
        "layer": "external" if external else "internal",
    }


@mcp.tool()
def calc_microstrip_z0(
    width_mm: float,
    height_mm: float,
    thickness_mm: float = 0.035,
    er: float = 4.3,
) -> dict:
    """[math/read] Microstrip impedance via Hammerstad-Jensen.

    Run before routing critical traces (USB DM/DP, antenna feed, SPI clk
    on long boards) to confirm geometry hits target Z0. Pair with
    `calc_trace_width` for current handling.

    Args:
        width_mm: trace width
        height_mm: dielectric height above the reference plane
        thickness_mm: copper thickness (1oz=0.035, 2oz=0.07; default 1oz)
        er: relative permittivity (FR4≈4.3, Rogers 4350≈3.66)

    Returns Z0 (Ω), effective εr, propagation delay (ps/mm).
    """
    from hw_agent.calculators.transmission_line import microstrip_z0
    return microstrip_z0(width_mm=width_mm, height_mm=height_mm,
                         thickness_mm=thickness_mm, er=er)


@mcp.tool()
def calc_stripline_z0(
    width_mm: float,
    plane_spacing_mm: float,
    thickness_mm: float = 0.035,
    er: float = 4.3,
) -> dict:
    """[math/read] Symmetric stripline impedance (IPC-2141A).

    Use for inner-layer routes between two reference planes. Tighter
    impedance control than microstrip but slower propagation (no air
    above the trace lowers εeff for microstrip).

    Args:
        width_mm: trace width
        plane_spacing_mm: TOTAL dielectric thickness (top plane → bottom plane)
        thickness_mm: copper thickness
        er: relative permittivity

    Returns Z0 (Ω) and propagation delay.
    """
    from hw_agent.calculators.transmission_line import stripline_z0
    return stripline_z0(width_mm=width_mm, plane_spacing_mm=plane_spacing_mm,
                        thickness_mm=thickness_mm, er=er)


@mcp.tool()
def calc_via_inductance(
    height_mm: float,
    diameter_mm: float,
    return_path_mm: Optional[float] = None,
) -> dict:
    """[math/read] Through-hole via inductance — Howard Johnson, eq. 7.21.

    L (nH) ≈ 0.2·h(mm)·[ln(4h/d)+1]. A 1.6mm board with 0.3mm vias gives
    ~1.3 nH per via. Power-rail decoupling cares about this — it's the
    series L between cap and IC pin.

    Args:
        height_mm: via length (≈ board thickness for through-hole)
        diameter_mm: drilled hole diameter
        return_path_mm: distance to nearest stitching via — adds an
            approximation of loop inductance when return current can't
            follow the via directly. Optional.

    Returns L (nH) + henries.
    """
    from hw_agent.calculators.transmission_line import via_inductance
    return via_inductance(height_mm=height_mm, diameter_mm=diameter_mm,
                          return_path_mm=return_path_mm)


# ─── Template Tools ──────────────────────────────────────────────────────────

@mcp.tool()
def list_templates() -> list[str]:
    """List available component research templates."""
    from hw_agent.investigator import _load_templates, TEMPLATES
    _load_templates()
    return list(TEMPLATES.keys())


@mcp.tool()
def get_template_specs(component_type: str) -> list[dict]:
    """Get the spec definitions for a component template (what to extract)."""
    from hw_agent.investigator import _load_templates, TEMPLATES
    _load_templates()
    t = TEMPLATES.get(component_type)
    if not t:
        return [{"error": f"Unknown template: {component_type}"}]
    return [
        {"key": s.key, "name": s.name, "unit": s.unit, "required": s.required, "aliases": s.aliases}
        for s in t.specs
    ]


@mcp.tool()
def get_search_queries(component_type: str, **params) -> list[dict]:
    """Get JLCPCB search queries for a component template."""
    from hw_agent.investigator import _load_templates, TEMPLATES
    _load_templates()
    t = TEMPLATES.get(component_type)
    if not t:
        return [{"error": f"Unknown template: {component_type}"}]
    return [
        {
            "query": s.query.format(**params) if params else s.query,
            "subcategory": s.subcategory,
            "packages": s.packages,
            "sort_by": s.sort_by,
        }
        for s in t.searches
    ]


@mcp.tool()
def run_investigation(
    component_type: str,
    datasheet: Optional[str] = None,
    vin: Optional[float] = None,
    vout: Optional[float] = None,
    actual_load_ma: Optional[float] = None,
) -> str:
    """Run a full component investigation. Returns markdown report."""
    from hw_agent.investigator import investigate
    params = {}
    if vin is not None: params["vin"] = vin
    if vout is not None: params["vout"] = vout
    if actual_load_ma is not None: params["actual_load_ma"] = actual_load_ma

    result = investigate(component_type=component_type, datasheet=datasheet, params=params)
    return result.report_md


# ─── Questionnaire Tools ─────────────────────────────────────────────────────

def _resolve_type(prop: dict) -> str:
    if "type" in prop:
        return prop["type"]
    if "anyOf" in prop:
        non_null = [s.get("type") for s in prop["anyOf"]
                    if isinstance(s, dict) and s.get("type") and s.get("type") != "null"]
        if non_null:
            return non_null[0]
    return "any"


def _flatten_field_schema(model_cls) -> list[dict]:
    """Flatten a Pydantic model's JSON Schema into agent-friendly per-field dicts.
    Used for both Requirements (q_load) and Actuals (q_load.actuals_schema)."""
    schema = model_cls.model_json_schema()
    fields = []
    for key, prop in schema.get("properties", {}).items():
        entry = {
            "id": key,
            "prompt": prop.get("description", key),
            "casual_prompt": prop.get("casual_prompt", ""),
            "examples": prop.get("examples", []),
            "unit": prop.get("unit", ""),
            "default": prop.get("default"),
            "required": key in schema.get("required", []),
            "type": _resolve_type(prop),
            "infer_from": prop.get("infer_from", {}),
        }
        for k in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
            if k in prop:
                entry[k] = prop[k]
            elif "anyOf" in prop:
                for sub in prop["anyOf"]:
                    if isinstance(sub, dict) and k in sub:
                        entry[k] = sub[k]
                        break
        fields.append(entry)
    return fields


@mcp.tool()
def q_load(component_type: str) -> dict:
    """[read] Load the full questionnaire for a subsystem type.

    Returns the Pydantic JSON Schema flattened into agent-friendly fields for
    BOTH the engineer-facing `requirements` (what to ask the user) AND the
    datasheet-extracted `actuals` (what to populate at `subsystem_choose_part`
    or `subsystem_update_actuals` time).

    Each per-field entry: prompt, casual_prompt, examples, unit, type, default,
    required, infer_from, min/max bounds. Read this BEFORE filling actuals — the
    field names differ from requirements (e.g. requirements have `iout`, actuals
    have `iout_max`; requirements have `interface`, actuals have `interfaces`).
    """
    from hw_agent.templates import SUBSYSTEM_REGISTRY
    cls = SUBSYSTEM_REGISTRY.get(component_type)
    if cls is None:
        return {
            "exists": False,
            "component_type": component_type,
            "categories_available": list(SUBSYSTEM_REGISTRY.keys()),
            "hint": f"No subsystem template for `{component_type}`. To add one, create hw_agent/templates/{component_type}.py and register it in templates/__init__.py.",
        }

    return {
        "exists": True,
        "component_type": component_type,
        "description": cls.description,
        "requirements": _flatten_field_schema(cls.Requirements),
        "actuals_schema": _flatten_field_schema(cls.Actuals),
        "ai_instructions": cls.ai_instructions,
    }


@mcp.tool()
def q_list() -> list[dict]:
    """[read] List all subsystem types in the registry with their descriptions."""
    from hw_agent.templates import SUBSYSTEM_REGISTRY
    return [
        {
            "component_type": cat,
            "name": cls.__name__,
            "description": cls.description,
            "checks": [c.__name__ for c in cls.checks],
            "calculations": [c.__name__ for c in cls.calculations],
        }
        for cat, cls in SUBSYSTEM_REGISTRY.items()
    ]


@mcp.tool()
def q_save(component_type: str, questionnaire: dict) -> str:
    """[deprecated] Dynamic questionnaire creation is no longer supported.

    To add a new subsystem type, create a Pydantic-based Subsystem class in
    hw_agent/templates/<component_type>.py and register it in templates/__init__.py.
    """
    return (
        f"NOT SUPPORTED: q_save was for the old JSON-questionnaire system. "
        f"To add a `{component_type}` subsystem, write a Pydantic class in "
        f"hw_agent/templates/{component_type}.py and register it. See ldo.py for the template."
    )


@mcp.tool()
def q_add_question(component_type: str, question: dict) -> str:
    """[deprecated] Editing questions at runtime is no longer supported.

    Edit the Pydantic Requirements class in hw_agent/templates/<component_type>.py instead.
    """
    return (
        f"NOT SUPPORTED: q_add_question was for the old JSON system. "
        f"Edit `{component_type}Requirements` in hw_agent/templates/{component_type}.py."
    )


@mcp.tool()
def q_validate(component_type: str, answers: dict) -> dict:
    """[read] Validate engineer answers against the subsystem's Requirements model.

    Returns `{ok: bool, errors: list[dict]}` — Pydantic ValidationError details
    on failure, normalized values on success.
    """
    from hw_agent.templates import SUBSYSTEM_REGISTRY
    cls = SUBSYSTEM_REGISTRY.get(component_type)
    if cls is None:
        return {"ok": False, "errors": [{"msg": f"unknown component_type `{component_type}`"}]}
    try:
        validated = cls.Requirements(**answers)
        return {"ok": True, "values": validated.model_dump()}
    except Exception as e:
        details = []
        for err in getattr(e, "errors", lambda: [{"msg": str(e)}])():
            details.append({"loc": err.get("loc"), "msg": err.get("msg"), "type": err.get("type")})
        return {"ok": False, "errors": details}


@mcp.tool()
def q_searches(component_type: str, answers: dict) -> list[dict]:
    """[read] Generate JLCPCB search queries for a subsystem given the engineer's answers."""
    from hw_agent.templates import SUBSYSTEM_REGISTRY
    cls = SUBSYSTEM_REGISTRY.get(component_type)
    if cls is None:
        avail = ", ".join(SUBSYSTEM_REGISTRY.keys())
        raise ValueError(f"Unknown component_type `{component_type}`. Available: {avail}")
    queries = []
    for s in cls.searches:
        try:
            q = s.query.format(**answers) if answers else s.query
        except KeyError:
            q = s.query
        queries.append({
            "query": q, "subcategory": s.subcategory,
            "spec_filters": s.spec_filters, "packages": s.packages,
            "min_stock": s.min_stock, "sort_by": s.sort_by,
        })
    return queries


@mcp.tool()
def q_derive(component_type: str, answers: dict) -> dict:
    """[read] Validate answers and return the normalized requirements dict.

    Pydantic applies defaults and coerces types. Old `derived` rules (natural-language
    inference) live in the `infer_from` Field metadata — apply them client-side before
    calling this if needed.
    """
    return q_validate(component_type, answers)


# ─── Subsystem Pipeline (Phase K) ────────────────────────────────────────────
#
# Pydantic-Subsystem-backed verification flow. Each project owns a list of
# subsystem objects (LDO, buck, MCU, …) persisted as JSON. Every edit triggers
# the validation pipeline and returns the project status alongside the
# confirmation — informational, never gating.

def _format_subsystem_status_md(s) -> str:
    """One subsystem's status as markdown."""
    lines = [f"### `{s.name}` ({s.category}) — {'READY' if s.ready else 'BLOCKED' if s.failed else 'IN PROGRESS'}"]
    counts = []
    if s.passed: counts.append(f"{len(s.passed)} PASS")
    if s.failed: counts.append(f"**{len(s.failed)} FAIL**")
    accepted = getattr(s, "accepted", []) or []
    if accepted: counts.append(f"{len(accepted)} ACK")
    if s.missing: counts.append(f"{len(s.missing)} MISSING")
    if counts:
        lines.append(", ".join(counts))
    if s.failed or s.missing or accepted:
        lines.append("")
        lines.append("| Status | Check | Detail |")
        lines.append("|--------|-------|--------|")
        for c in s.failed:
            lines.append(f"| **FAIL** | {c.name} | {c.actual} (need {c.required}) |")
        for c in accepted:
            lines.append(f"| ACK | {c.name} | {c.actual} (accepted: {c.required}) |")
        for c in s.missing:
            specs = ", ".join(c.missing_specs) if c.missing_specs else "(unspecified)"
            lines.append(f"| MISSING | {c.name} | needs {specs} |")
    return "\n".join(lines)


def _format_project_status_md(ps) -> str:
    """ProjectStatus aggregated as markdown."""
    soft = getattr(ps, "soft_warnings", []) or []
    acked = getattr(ps, "accepted_warnings", []) or []
    lines = [
        f"## Project status — `{ps.project}`",
        f"**{ps.ready_count}/{ps.total_count} subsystems ready**"
        + (f", {len(ps.blocking_failures)} blocking failures" if ps.blocking_failures else "")
        + (f", {len(soft)} soft warnings" if soft else "")
        + (f", {len(acked)} accepted" if acked else "")
        + (f", {len(ps.data_needed)} missing data fields" if ps.data_needed else ""),
        "",
    ]
    if not ps.subsystems:
        lines.append("_No subsystems yet. Add one with `subsystem_add(...)`._")
        return "\n".join(lines)
    for s in ps.subsystems:
        lines.append(_format_subsystem_status_md(s))
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def subsystem_add(project: str, category: str, name: str, requirements: dict,
                  overwrite: bool = False) -> list:
    """[write] Add a new subsystem to a project.

    Refuses to overwrite an existing subsystem unless `overwrite=True` is set
    explicitly (which wipes chosen_part, decisions, actuals). To revise just
    the requirements without losing history, use `subsystem_update_requirements`.

    Args:
        project, category, name: locate the subsystem
        requirements: dict matching the subsystem's Requirements model
        overwrite:    if False (default) and a subsystem with `name` already
                      exists, raises ValueError. Set True to intentionally wipe.

    Returns confirmation + full project status.
    """
    from hw_agent.templates import SUBSYSTEM_REGISTRY
    from hw_agent.project_state.subsystems import (
        subsystem_save, subsystem_load, aggregate_project_status,
    )

    cls = SUBSYSTEM_REGISTRY.get(category)
    if cls is None:
        avail = ", ".join(SUBSYSTEM_REGISTRY.keys())
        raise ValueError(f"Unknown category `{category}`. Available: {avail}")

    if not overwrite:
        existing = subsystem_load(project, name)
        if existing is not None:
            raise ValueError(
                f"Subsystem `{name}` already exists in project `{project}` "
                f"(category={existing.__class__.category}). To revise its "
                "requirements without losing history, call "
                "`subsystem_update_requirements`. To intentionally wipe and "
                "replace, re-call with `overwrite=True`."
            )

    sub = cls(name=name, requirements=cls.Requirements(**requirements))
    subsystem_save(sub, project)
    ps = aggregate_project_status(project)
    return [
        f"✓ Added subsystem `{name}` ({category}) to project `{project}`",
        _format_project_status_md(ps),
    ]


@mcp.tool()
def subsystem_update_requirements(project: str, name: str, requirements: dict) -> list:
    """[write] Update an existing subsystem's requirements WITHOUT losing history.

    Merges provided keys into existing requirements and re-validates through the
    subclass's Requirements model. Preserves chosen_part, decisions, and actuals
    — use this when scope shifts after a part has been picked. Unknown field
    names raise; bad types raise.

    Args:
        project, name: locate the subsystem
        requirements:  dict of requirement keys to merge (only the keys you
                       pass are changed; other requirements stay as they were)

    Returns confirmation + project status.
    """
    from hw_agent.project_state.subsystems import (
        subsystem_load, subsystem_save, aggregate_project_status,
    )

    sub = subsystem_load(project, name)
    if sub is None:
        raise ValueError(f"No subsystem `{name}` in project `{project}`. "
                         f"Create it first via `subsystem_add`.")

    sub = sub.with_requirements(**requirements)
    subsystem_save(sub, project)
    ps = aggregate_project_status(project)
    return [
        f"✓ Updated `{name}` requirements: {sorted(requirements.keys())}",
        _format_project_status_md(ps),
    ]


@mcp.tool()
def subsystem_update_actuals(project: str, name: str, actuals: dict) -> list:
    """[write] Merge extracted datasheet specs into a subsystem's Actuals.

    Use this as the agent learns specs from datasheet investigation. Each call
    re-runs the validation pipeline and reports the resulting project status.
    Only the keys you provide are updated; other actuals are preserved.

    Args:
        project: project slug
        name:    subsystem name (must already exist via subsystem_add)
        actuals: dict of spec keys to merge (e.g. {"theta_ja": 40, "vin_min": 4.5})

    Returns confirmation + updated project status.
    """
    from hw_agent.project_state.subsystems import subsystem_load, subsystem_save, aggregate_project_status

    sub = subsystem_load(project, name)
    if sub is None:
        raise ValueError(f"No subsystem `{name}` in project `{project}`. "
                         f"Create it first via `subsystem_add`.")

    sub = sub.with_actuals(**actuals)
    subsystem_save(sub, project)
    ps = aggregate_project_status(project)
    return [
        f"✓ Updated `{name}` actuals: {list(actuals.keys())}",
        _format_project_status_md(ps),
    ]


@mcp.tool()
def subsystem_choose_part(
    project: str,
    name: str,
    lcsc: str,
    mpn: str,
    manufacturer: str = "",
    description: str = "",
    package: str = "",
    price: float = 0.0,
    price_tiers: Optional[dict] = None,
    stock: int = 0,
    datasheet_url: str = "",
    library_type: str = "extended",
    qty_per_board: int = 1,
    notes: str = "",
    rationale: str = "",
    rejected: Optional[list[dict]] = None,
    tradeoffs: Optional[list[str]] = None,
    accepted_warnings: Optional[list[str]] = None,
    actuals: Optional[dict] = None,
    alternate_lcsc: str = "",
) -> list:
    """[write] Commit a part choice for a subsystem (atomic).

    Writes ChosenPart into the subsystem JSON, appends a Decision to its history,
    and (if `actuals` is provided) merges the verified datasheet specs into the
    subsystem's actuals in the same call — so the post-commit status reflects
    the committed part, not stale actuals from a prior pick.

    Args:
        project, name:       locate the subsystem
        lcsc, mpn, ...:      commercial details of the chosen part
        actuals:             dict of verified datasheet specs to land atomically
                             (e.g. {"vin_min": 4.5, "iout_max": 2.0, "theta_ja": 40,
                             "package": "SOIC-8-EP", "stock": 3000}). Without this,
                             checks will report MISSING for fields not previously
                             populated. Validated and re-checked after commit.
        rationale:           prose explanation of why this won
        rejected:            [{lcsc, mpn, reason}] for considered-and-rejected alternatives
        tradeoffs:           free-form prose of accepted downsides (human-readable)
        accepted_warnings:   list of soft-check names to explicitly acknowledge.
                             Matching is case-insensitive and ignores `_` vs space, so
                             both "Stock threshold" and "stock_threshold" work.
        alternate_lcsc:      backup if primary goes out of stock

    Returns confirmation + project status.
    """
    from hw_agent.project_state.subsystems import subsystem_load, subsystem_save, aggregate_project_status
    from hw_agent.subsystem import ChosenPart, Decision

    sub = subsystem_load(project, name)
    if sub is None:
        raise ValueError(f"No subsystem `{name}` in project `{project}`. "
                         f"Create it first via `subsystem_add`.")

    # Land actuals first so the post-commit status reflects this part's specs,
    # not whatever was on disk from a prior choice. Validation errors here raise
    # before any state is written.
    if actuals:
        sub = sub.with_actuals(**actuals)

    chosen = ChosenPart(
        lcsc=lcsc, mpn=mpn, manufacturer=manufacturer, description=description,
        package=package, price=price, price_tiers=price_tiers or {},
        stock=stock, datasheet_url=datasheet_url, library_type=library_type,
        qty_per_board=qty_per_board, notes=notes,
    )
    decision = Decision(
        chosen=chosen,
        rejected=rejected or [],
        rationale=rationale,
        tradeoffs=tradeoffs or [],
        accepted_warnings=accepted_warnings or [],
        alternate_lcsc=alternate_lcsc,
        requirements_snapshot=sub.requirements.model_dump(),
    )
    sub = sub.with_chosen_part(chosen, decision=decision)
    subsystem_save(sub, project)

    # Re-run validation against the just-committed state so the agent can't miss
    # hard failures or unacknowledged soft warnings. Informational only — the
    # commit already happened; agent decides whether to revisit.
    from hw_agent.checks import normalize_check_id
    post_status = sub.status()
    hard_fails = [c for c in post_status.failed if c.severity == "hard"]
    soft_fails = [c for c in post_status.failed if c.severity == "soft"]  # only the unacknowledged ones — accepted ones are now post_status.accepted
    accepted_now = post_status.accepted

    msg_lines = [f"✓ Chose **{mpn}** (`{lcsc}`) for subsystem `{name}` — ${price:.2f}/unit"]
    if hard_fails:
        msg_lines.append("")
        msg_lines.append(f"⚠️ **{len(hard_fails)} HARD failure(s)** — subsystem is committed but NOT READY:")
        for c in hard_fails:
            msg_lines.append(f"  - **{c.name}**: {c.actual} (need {c.required})")
        msg_lines.append("Pick a different part, or call `subsystem_update_actuals` if the actuals were wrong.")
    if soft_fails:
        msg_lines.append("")
        msg_lines.append(f"Note: {len(soft_fails)} soft warning(s) not acknowledged:")
        for c in soft_fails:
            msg_lines.append(f"  - **{c.name}**: {c.actual}")
        names = ", ".join(f'"{c.name}"' for c in soft_fails)
        msg_lines.append(
            f"To accept, re-call with `accepted_warnings=[{names}]` (case-insensitive; "
            "snake_case also works) and optionally `tradeoffs=[...]` for free-form rationale."
        )
    if accepted_now:
        msg_lines.append("")
        msg_lines.append(f"ACK: {len(accepted_now)} soft warning(s) accepted: " +
                         ", ".join(f"`{c.name}`" for c in accepted_now))
    # Detect ack entries that didn't match any soft fail (typo / wrong check name)
    softfail_normalized = {normalize_check_id(c.name) for c in soft_fails} | {normalize_check_id(c.name) for c in accepted_now}
    stale_acks = [a for a in (accepted_warnings or [])
                  if normalize_check_id(a) not in softfail_normalized]
    if stale_acks:
        msg_lines.append("")
        msg_lines.append(
            f"ℹ️ `accepted_warnings` contained name(s) not matching any soft check: {stale_acks}. "
            "Verify spelling (matching is case-insensitive and ignores _ vs space)."
        )

    ps = aggregate_project_status(project)
    return [
        "\n".join(msg_lines),
        _format_project_status_md(ps),
    ]


@mcp.tool()
def subsystem_remove(project: str, name: str) -> list:
    """[write] Remove a subsystem from a project. Returns updated project status."""
    from hw_agent.project_state.subsystems import subsystem_delete, aggregate_project_status
    ok = subsystem_delete(project, name)
    ps = aggregate_project_status(project)
    return [
        f"{'✓ Removed' if ok else '✗ Not found'} `{name}` in project `{project}`",
        _format_project_status_md(ps),
    ]


@mcp.tool()
def subsystem_status(project: str, name: str) -> list:
    """[read] Status for a single subsystem (markdown). Cheaper than project_status."""
    from hw_agent.project_state.subsystems import subsystem_load
    sub = subsystem_load(project, name)
    if sub is None:
        raise ValueError(f"No subsystem `{name}` in project `{project}`.")
    return [_format_subsystem_status_md(sub.status())]


@mcp.tool()
def project_status(project: str) -> list:
    """[read] Aggregated status across all subsystems in a project."""
    from hw_agent.project_state.subsystems import aggregate_project_status
    return [_format_project_status_md(aggregate_project_status(project))]


# ─── BOM (computed views over subsystems) ───────────────────────────────────

# Allowed shape for a project slug. Rules, in order of why-they-matter:
#   1. Letters/digits/underscore start — forbid leading hyphen so a slug
#      can never collide with a CLI flag if anything ever shells out.
#   2. Letters, digits, hyphen, or underscore body — keeps the slug a
#      simple filesystem name; rejects `..`, `/`, `\`, whitespace, dots.
#   3. Length cap at 64 — keeps `os.makedirs` paths reasonable.
_VALID_PROJECT_SLUG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$")


def _require_project_exists(project: str) -> str | None:
    """Return None if the project slug is safe and the dir exists; else a
    one-line error message.

    Without this guard, `bom_*` and `order_settings_*` would silently
    treat unknown projects as empty real projects, and a slug containing
    `..` or `/` could escape `docs/projects/` entirely.
    """
    if not project:
        return "project slug is empty"
    if not _VALID_PROJECT_SLUG.fullmatch(project):
        return (
            f"project slug '{project}' has invalid characters; "
            f"only letters, digits, hyphen, and underscore are allowed."
        )
    from hw_agent.project_state.paths import project_dir
    d = project_dir(project)
    if not d.exists():
        return f"project '{project}' not found at {d.relative_to(Path.cwd()) if d.is_relative_to(Path.cwd()) else d}"
    return None


@mcp.tool()
def bom_list(project: str) -> list[dict]:
    """[read] BOM derived from subsystems. One entry per subsystem with a chosen part.

    Raises `ValueError` for nonexistent or empty project slugs (so callers
    can distinguish "no parts yet" from "wrong slug").
    """
    err = _require_project_exists(project)
    if err is not None:
        raise ValueError(err)
    from hw_agent.project_state.subsystems import subsystem_list, subsystem_load
    items = []
    for name in subsystem_list(project):
        sub = subsystem_load(project, name)
        if sub is None or sub.chosen_part is None:
            continue
        items.append({
            "subsystem": name,
            "category": sub.__class__.category,
            **sub.chosen_part.model_dump(),
        })
    return items


@mcp.tool()
def bom_summary(project: str, low_stock_threshold: int = 500) -> str:
    """[read] Cost / supply / coverage roll-up — derived from subsystems. Returns markdown.

    `low_stock_threshold` defaults to 500 to match the `stock_threshold` check;
    pass a higher value for stricter "approaching stockout" warnings.

    Raises `ValueError` for nonexistent or empty project slugs.
    """
    err = _require_project_exists(project)
    if err is not None:
        raise ValueError(err)
    items = bom_list(project)
    if not items:
        return f"## BOM Summary — `{project}`\n\n_No parts chosen yet._"

    # Determine which subsystems explicitly accepted "Stock threshold" so they
    # don't count against supply risk (engineer signed off on the low-stock part).
    from hw_agent.project_state.subsystems import subsystem_load
    from hw_agent.checks import normalize_check_id
    stock_acked: set[str] = set()
    for it in items:
        sub = subsystem_load(project, it["subsystem"])
        if sub and sub.decisions:
            acked = {normalize_check_id(a) for a in sub.decisions[-1].accepted_warnings}
            if "stock_threshold" in acked:
                stock_acked.add(it["subsystem"])

    cost_per_board_qty_1 = sum(it["price"] * it["qty_per_board"] for it in items)
    def _tier_total(qty: int) -> float:
        total = 0.0
        for it in items:
            tiers = it.get("price_tiers") or {}
            applicable = [int(k) for k in tiers if int(k) <= qty]
            tier = max(applicable) if applicable else None
            unit = tiers[str(tier)] if tier is not None else it["price"]
            total += unit * it["qty_per_board"] * qty
        return round(total, 4)

    cost_10 = _tier_total(10)
    cost_100 = _tier_total(100)
    low_stock = [it for it in items
                 if (it.get("stock") or 0) < low_stock_threshold
                 and it["subsystem"] not in stock_acked]
    subsystems_count = len({it["subsystem"] for it in items})
    s = {
        "project": project,
        "item_count": len(items),
        "subsystem_count": subsystems_count,
        "parts": items,  # full part rows for the parts table (subsystem, mpn, lcsc, manufacturer, package, price, stock, …)
        "cost_per_board_qty_1": round(cost_per_board_qty_1, 4),
        "cost_per_board_qty_10": round(cost_10 / 10, 4) if items else 0.0,
        "cost_per_board_qty_100": round(cost_100 / 100, 4) if items else 0.0,
        "total_cost_10_boards": cost_10,
        "total_cost_100_boards": cost_100,
        "low_stock_items": [{"subsystem": it["subsystem"], "lcsc": it["lcsc"], "stock": it.get("stock", 0)} for it in low_stock],
        "supply_risk_score": len(low_stock) / len(items) if items else 0.0,
    }
    return _format_bom_summary_md(s)


# ─── analyze_candidate — verify + calculators + persistent journey log ─────

@mcp.tool()
def analyze_candidate(
    project: str,
    name: str,
    lcsc: str,
    actuals: dict,
    *,
    mpn: str = "",
    manufacturer: str = "",
    package: str = "",
    price: float = 0.0,
    stock: int = 0,
    library_type: str = "",
    persist: bool = True,
    notes: str = "",
) -> str:
    """[write*] Examine a part candidate end-to-end against a subsystem.

    Fans out the three things `verify_candidate` + manual `calc_*` tool calls
    used to do separately:

      1. Runs the subsystem's check pipeline against the candidate's actuals
         (verify_candidate-equivalent — PASS/FAIL/MISSING per check).
      2. Runs the category's analytical calculators (Tj, Pdiss, ripple,
         dropout headroom, divider math — whatever the template registered).
      3. Persists the candidate to `subsystems/<name>.json::candidates_examined`
         (append-only) so the search journey is auditable, not just the
         winning pick. Pass `persist=False` for a dry-run inspection.

    Auto-fills `package` and `stock` into actuals from the JLC metadata
    arguments if not already provided in `actuals` — saves the agent from
    duplicating values.

    The agent is responsible for calling `mcp__pcbparts__jlc_get_part(lcsc)`
    first and passing the metadata via the kwargs (this MCP server can't
    reach across to the pcbparts MCP).

    Args:
        project, name:    locate the subsystem
        lcsc:             LCSC part code (e.g. "C2976596")
        actuals:          datasheet specs (same shape as `verify_candidate`)
        mpn, manufacturer, package, price, stock, library_type:
                          JLC metadata — pulled by the agent from `jlc_get_part`
        persist:          append to candidates_examined (default True)
        notes:            free-form note attached to this examination

    Returns markdown report: verdict + check table + calculator outputs.
    """
    from hw_agent.project_state.subsystems import subsystem_load, subsystem_save
    from hw_agent.subsystem import ExaminedCandidate

    sub = subsystem_load(project, name)
    if sub is None:
        raise ValueError(f"No subsystem `{name}` in project `{project}`.")

    cls = sub.__class__

    # Auto-fill package + stock from JLC metadata if not in actuals
    enriched = {**actuals}
    if "package" not in enriched and package:
        enriched["package"] = package
    if "stock" not in enriched and stock:
        enriched["stock"] = stock

    merged_actuals = {**sub.actuals.model_dump(), **enriched}
    new_actuals = cls.Actuals(**merged_actuals)
    hypothetical = sub.model_copy(update={"actuals": new_actuals})

    status = hypothetical.status()
    calculations = hypothetical.run_calculations()

    # ── Build report ──
    verdict = "READY" if status.ready else (
        "BLOCKED" if status.failed else "INCOMPLETE"
    )
    lines: list[str] = []
    lines.append(f"## `{name}` — analyze candidate `{lcsc}`")
    lines.append("")
    header_parts = [f"**{verdict}**"]
    if mpn:
        header_parts.append(f"`{mpn}`")
    if manufacturer:
        header_parts.append(manufacturer)
    if price:
        header_parts.append(f"${price:.4f}")
    if stock:
        header_parts.append(f"{stock:,} stock")
    if library_type:
        header_parts.append(f"JLC {library_type}")
    lines.append(" · ".join(header_parts))
    lines.append("")
    lines.append("### Verify")
    lines.append("")
    lines.append("| Status | Check | Actual | Required | Note |")
    lines.append("|--------|-------|--------|----------|------|")
    for c in status.checks:
        icon = {
            "pass": "PASS",
            "fail": "**FAIL**",
            "missing": "MISSING",
            "accepted": "ACK",
        }[c.status]
        note = c.note or ""
        lines.append(f"| {icon} | {c.name} | {c.actual} | {c.required} | {note} |")

    if status.missing:
        lines.append("")
        lines.append("**Missing inputs** — extract from datasheet to complete verification:")
        for c in status.missing:
            lines.append(f"- `{c.name}`: needs {', '.join(c.missing_specs)}")

    if calculations:
        lines.append("")
        lines.append("### Engineering math")
        lines.append("")
        notes_list = calculations.get("notes") or []
        flags = calculations.get("flags") or {}
        for k, v in calculations.items():
            if k in ("notes", "flags"):
                continue
            if isinstance(v, dict):
                lines.append(f"- **{k}**:")
                for sub_k, sub_v in v.items():
                    lines.append(f"    - `{sub_k}`: {sub_v}")
            else:
                lines.append(f"- **{k}**: {v}")
        if flags:
            lines.append("")
            lines.append("**Pass/fail flags from calculations:**")
            for fk, fv in flags.items():
                icon = "PASS" if fv else "**FAIL**"
                lines.append(f"- {fk}: {icon}")
        if notes_list:
            lines.append("")
            lines.append("**Calculator notes:**")
            for n in notes_list:
                lines.append(f"- {n}")

    if persist:
        examined = ExaminedCandidate(
            lcsc=lcsc,
            mpn=mpn,
            manufacturer=manufacturer,
            package=package,
            price=price,
            stock=stock,
            library_type=library_type,
            actuals=enriched,
            verdict=verdict,
            checks=[c.model_dump() for c in status.checks],
            calculations=calculations,
            notes=notes,
        )
        updated = sub.model_copy(
            update={"candidates_examined": sub.candidates_examined + [examined]}
        )
        subsystem_save(updated, project)
        lines.append("")
        lines.append(
            f"_Recorded to `subsystems/{name}.json::candidates_examined` "
            f"(now {len(updated.candidates_examined)} entries)._"
        )
    else:
        lines.append("")
        lines.append("_Dry-run — not persisted. Set `persist=True` to record._")

    return "\n".join(lines)


# ─── verify_candidate — hypothetical, no persistence ────────────────────────

@mcp.tool()
def verify_candidate(
    project: str,
    name: str,
    actuals: dict,
    requirements: Optional[dict] = None,
) -> list:
    """[read] Hypothetically run the check pipeline against proposed actuals.

    Use this to test "what would happen if we picked this candidate" before
    committing. Doesn't write anything — when ready to commit, call
    `subsystem_update_actuals` and `subsystem_choose_part`.

    Args:
        project:      project slug
        name:         existing subsystem (must already be created via subsystem_add)
        actuals:      proposed datasheet specs (merged over the subsystem's current actuals)
        requirements: optional override of requirements (rare; defaults to current)

    Returns markdown PASS/FAIL/MISSING table for the proposed state.
    """
    from hw_agent.project_state.subsystems import subsystem_load

    sub = subsystem_load(project, name)
    if sub is None:
        raise ValueError(f"No subsystem `{name}` in project `{project}`.")

    cls = sub.__class__
    merged_actuals = {**sub.actuals.model_dump(), **actuals}
    new_actuals = cls.Actuals(**merged_actuals)
    new_reqs = sub.requirements
    if requirements:
        merged_reqs = {**sub.requirements.model_dump(), **requirements}
        new_reqs = cls.Requirements(**merged_reqs)
    hypothetical = sub.model_copy(update={"actuals": new_actuals, "requirements": new_reqs})

    status = hypothetical.status()
    lines = [f"## Hypothetical verification — `{name}`"]
    lines.append(f"**{status.summary().split(': ', 1)[1] if ': ' in status.summary() else status.summary()}**")
    lines.append("")
    lines.append("| Status | Check | Actual | Required |")
    lines.append("|--------|-------|--------|----------|")
    for c in status.checks:
        icon = {"pass": "PASS", "fail": "**FAIL**", "missing": "MISSING", "accepted": "ACK"}[c.status]
        lines.append(f"| {icon} | {c.name} | {c.actual} | {c.required} |")
    if status.missing:
        lines.append("")
        lines.append("**Missing — extract from datasheet:**")
        for c in status.missing:
            lines.append(f"- `{c.name}`: needs {c.missing_specs}")
    if not status.failed and not status.missing:
        lines.append("")
        lines.append("_All hard checks passed — safe to commit via `subsystem_choose_part`._")
    return ["\n".join(lines)]


# ─── SVG Schematic Generators (schemdraw → SVG files) ──────────────────────

def _schematic_output(category: str, subsystem: str = "") -> str:
    """Build the per-subsystem schematic output path.

    Without `subsystem`, two subsystems of the same category (e.g. `ldo_3v3`
    and `ldo_5v`) clobber each other's schematic.{svg,png}. Pass the subsystem
    name to namespace under `components/<category>/<subsystem>/`.
    """
    if subsystem:
        return f"components/{category}/{subsystem}/schematic.svg"
    return f"components/{category}/schematic.svg"


@mcp.tool()
def svg_buck(
    project: str,
    subsystem: str = "",
    part: str = "Buck IC",
    vin_label: str = "VBAT\n7.4V",
    vout_label: str = "5V Rail",
    cin: str = "2×10µF",
    cout: str = "2×22µF",
    inductor: str = "3.3µH",
    r1: str = "73.2kΩ",
    r2: str = "10kΩ",
    vref: str = "Vfb=0.6V",
    cboot: str = "100nF",
) -> list:
    """Generate a schemdraw render of a buck converter. Saves SVG + PNG to
    components/buck_converter[/<subsystem>]/schematic.{svg,png}. Pass `subsystem`
    if your project has more than one buck so they don't clobber each other.
    Returns inline PNG plus both paths."""
    from hw_agent.schematics.svg import render_buck_schematic
    r = render_buck_schematic(
        project=project, part=part, vin_label=vin_label, vout_label=vout_label,
        cin=cin, cout=cout, inductor=inductor, r1=r1, r2=r2, vref=vref, cboot=cboot,
        output=_schematic_output("buck_converter", subsystem),
    )
    return [f"PNG: `{r['png']}` · SVG: `{r['svg']}`", Image(path=str(r["png"]))]


@mcp.tool()
def svg_ldo(
    project: str,
    subsystem: str = "",
    part: str = "LDO",
    vin_label: str = "5V Rail",
    vout_label: str = "3.3V Rail",
    cin: str = "1µF",
    cout: str = "1µF",
) -> list:
    """Generate a schemdraw render of an LDO. Saves SVG + PNG to
    components/ldo[/<subsystem>]/schematic.{svg,png}. Pass `subsystem` if your
    project has more than one LDO."""
    from hw_agent.schematics.svg import render_ldo_schematic
    r = render_ldo_schematic(
        project=project, part=part, vin_label=vin_label, vout_label=vout_label,
        cin=cin, cout=cout, output=_schematic_output("ldo", subsystem),
    )
    return [f"PNG: `{r['png']}` · SVG: `{r['svg']}`", Image(path=str(r["png"]))]


@mcp.tool()
def svg_motor_driver(
    project: str,
    subsystem: str = "",
    part: str = "DRV8833",
    vm_label: str = "5V",
    motor_a: str = "Motor A",
    motor_b: str = "Motor B",
    pwm_a: str = "PWM_A",
    dir_a: str = "DIR_A",
    pwm_b: str = "PWM_B",
    dir_b: str = "DIR_B",
) -> list:
    """Generate a schemdraw render of a dual H-bridge motor driver. Saves SVG +
    PNG to components/motor_driver[/<subsystem>]/schematic.{svg,png}. Pass
    `subsystem` if your project has more than one driver."""
    from hw_agent.schematics.svg import render_motor_driver_schematic
    r = render_motor_driver_schematic(
        project=project, part=part, vm_label=vm_label,
        motor_a=motor_a, motor_b=motor_b,
        pwm_a=pwm_a, dir_a=dir_a, pwm_b=pwm_b, dir_b=dir_b,
        output=_schematic_output("motor_driver", subsystem),
    )
    return [f"PNG: `{r['png']}` · SVG: `{r['svg']}`", Image(path=str(r["png"]))]


@mcp.tool()
def svg_voltage_divider(
    project: str,
    subsystem: str = "",
    vin: str = "Vbat",
    r1: str = "18kΩ",
    r2: str = "10kΩ",
    cfilt: str = "100nF",
    vout_label: str = "To ADC",
) -> list:
    """Generate a schemdraw render of a voltage divider. Saves SVG + PNG to
    components/voltage_divider[/<subsystem>]/schematic.{svg,png}."""
    from hw_agent.schematics.svg import render_voltage_divider
    r = render_voltage_divider(
        project=project, vin_label=vin, r1=r1, r2=r2, cfilt=cfilt,
        adc_label=vout_label, output=_schematic_output("voltage_divider", subsystem),
    )
    return [f"PNG: `{r['png']}` · SVG: `{r['svg']}`", Image(path=str(r["png"]))]


# ─── KiCad Export & Eval Loop ──────────────────────────────────────────────

@mcp.tool()
def kicad_export_schem(
    schem_path: str,
    output_path: Optional[str] = None,
    with_render: bool = False,
    zoom_to: Optional[str] = None,
) -> list:
    """[eval/write] Convert a *.schem.json into a KiCad 9 .kicad_sch file.

    Generates self-contained lib_symbols (Device:R/C/L, power flags, custom
    inline IC symbols) so kicad-cli can parse it standalone. Pin endpoints
    are placed so JSON pin-1 anchors land on JSON `at` coordinates.

    Args:
        schem_path: path to *.schem.json
        output_path: where to write the .kicad_sch (default: alongside the JSON)
        with_render: include a focused PNG render in the response so the
            agent sees the exported sheet. Costs ~1.3s cold, ~10ms cached.
        zoom_to: component reference to crop around. Only used when
            with_render=True. None → full sheet.

    Returns the absolute path, and optionally an inline PNG.
    """
    from hw_agent.schematics.ksa_writer import export_file

    in_path = Path(schem_path)
    if output_path is None:
        out_path = in_path.with_suffix("").with_suffix(".kicad_sch")
        if in_path.name.endswith(".schem.json"):
            out_path = in_path.parent / (in_path.name[:-len(".schem.json")] + ".kicad_sch")
    else:
        out_path = Path(output_path)

    sch = export_file(in_path, out_path).resolve()
    text = f"Exported: `{sch}`"

    if with_render:
        try:
            from hw_agent.schematics.render_focus import render_focused_png
            r = render_focused_png(sch, zoom_to=zoom_to)
            return [text, Image(path=str(r["png_path"]))]
        except Exception as e:
            return [f"{text}\n\n*render failed: {e}*"]
    return [text]


@mcp.tool()
def kicad_eval(kicad_sch: str, svg_dir: Optional[str] = None) -> list:
    """[eval/compute] Run kicad-cli ERC + SVG export against an existing .kicad_sch.

    Thin wrapper over hw_agent.schematics.eval.run_eval — same pipeline used
    by the file-watcher daemon. Use this when you already have a .kicad_sch
    on disk and just want the ERC report.

    Args:
        kicad_sch: path to the .kicad_sch to evaluate
        svg_dir: directory for the SVG output (default: <sch_dir>/eval_out)

    Returns markdown ERC summary + inline schematic PNG.
    """
    from hw_agent.schematics.eval import run_eval

    sch = Path(kicad_sch).resolve()
    if not sch.exists():
        return [f"## ERC: FAIL\n\n**Error:** schematic not found: `{sch}`"]
    out_dir = Path(svg_dir).resolve() if svg_dir else None
    result = run_eval(sch, svg_dir=out_dir)
    md = _format_erc_markdown(result)
    svg = (result.get("artifacts") or {}).get("svg")
    img = _svg_path_to_image(svg) if svg else None
    return [md, img] if img else [md]


@mcp.tool()
def get_render(
    kicad_sch: str,
    zoom_to: Optional[str] = None,
    padding_mm: float = 5.0,
    dpi: int = 200,
) -> list:
    """[render/compute] Render a `.kicad_sch` to PNG, optionally cropped around a component.

    The agent's eyes — call after edits to see what you just produced. Uses
    kicad-cli for ground-truth rendering (matches what the user sees in
    eeschema). Cropped renders are ~10× smaller in tokens than full sheets,
    so prefer `zoom_to=<reference>` (e.g. "U1") after placement edits.

    Args:
        kicad_sch: path to a `.kicad_sch` file
        zoom_to: component reference (e.g. "U1", "C3"). None → full sheet.
        padding_mm: extra space around the bbox. Bigger → more context.
        dpi: render resolution. Lower → fewer tokens but harder to read.

    Returns markdown header (ref + bbox) + inline PNG. Cached on (mtime,
    zoom_to, padding, dpi) so repeat calls on an unchanged sheet are ~10ms.
    """
    from hw_agent.schematics.render_focus import render_focused_png

    sch_path = Path(kicad_sch).resolve()
    if not sch_path.exists():
        return [f"## Render: FAIL\n\n**Error:** schematic not found: `{sch_path}`"]

    try:
        result = render_focused_png(sch_path, zoom_to=zoom_to,
                                    padding_mm=padding_mm, dpi=dpi)
    except Exception as e:
        return [f"## Render: FAIL\n\n**Error:** {e}"]

    bbox = result["bbox_mm"]
    lines = [f"## Render: `{sch_path.name}`"]
    if zoom_to:
        if bbox:
            lines.append(f"Zoom: **{zoom_to}** — bbox `{bbox[0]:.1f}, {bbox[1]:.1f} → {bbox[2]:.1f}, {bbox[3]:.1f}` mm")
        else:
            lines.append(f"Zoom: **{zoom_to}** — *not found, showing full sheet*")
    else:
        lines.append("Full sheet")
    if result["from_cache"]:
        lines.append("*(cached)*")

    return [
        "\n".join(lines),
        Image(path=str(result["png_path"])),
    ]


@mcp.tool()
async def eval_subsystem(
    schem_json: str,
    child_sheet: bool = False,
    with_render: bool = False,
    zoom_to: Optional[str] = None,
    ctx: Context = None,
) -> list:
    """[eval/compute] Full pipeline on a `.schem.json`: validate schema → export → ERC + SVG.

    One call replaces export_schem + eval. Writes a `<base>.eval.json` status
    file next to the source — same format the file-watcher daemon writes,
    so this tool gives the agent identical feedback whether triggered by
    save (watcher) or by explicit invocation.

    Streams `ctx.report_progress` at every pipeline stage (validating →
    exporting → ERC → done) so the agent sees progress instead of a 1-3s
    silence.

    Args:
        schem_json: path to *.schem.json
        child_sheet: emit as a hierarchical child (skip PWR_FLAG)
        with_render: include a focused PNG render in the response so the
            agent can see what was produced. Costs ~1.3s cold, ~10ms cached.
        zoom_to: component reference to crop around (e.g. "U1"). Only used
            when with_render=True. None → full sheet.

    Returns markdown summary (schema + ERC + artifacts), and optionally
    an inline schematic PNG if with_render=True.
    """
    import asyncio
    from hw_agent.schematics.eval import eval_from_json

    p = Path(schem_json).resolve()
    if not p.exists():
        return [f"## ERC: FAIL\n\n**Error:** json not found: `{p}`"]

    # Bridge route_board's worker thread → ctx.report_progress on the loop
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_progress(current: int, total: int, msg: str):
        loop.call_soon_threadsafe(queue.put_nowait, (current, total, msg))

    async def _drain():
        while True:
            current, total, msg = await queue.get()
            if ctx is not None:
                try:
                    await ctx.report_progress(current, total, msg)
                except Exception:
                    pass
            if current >= total:
                return

    drain_task = asyncio.create_task(_drain())
    try:
        result = await asyncio.to_thread(
            eval_from_json, p, child_sheet=child_sheet, on_progress=on_progress,
        )
    finally:
        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            pass
    d = result.to_dict()
    md = _format_erc_markdown(d)

    if with_render:
        sch_path = (d.get("artifacts") or {}).get("kicad_sch")
        if sch_path:
            try:
                from hw_agent.schematics.render_focus import render_focused_png
                r = render_focused_png(Path(sch_path), zoom_to=zoom_to)
                return [md, Image(path=str(r["png_path"]))]
            except Exception as e:
                md += f"\n\n*render failed: {e}*"

    # Fallback: legacy path returning the eval_out SVG-as-PNG
    svg = (d.get("artifacts") or {}).get("svg")
    img = _svg_path_to_image(svg) if svg else None
    return [md, img] if img else [md]


# ─── Atomic schematic edits ────────────────────────────────────────────────
#
# These tools mutate a `.kicad_sch` in place via kicad-sch-api (sch_ops.py).
# The daemon's file watcher picks up the write and fires the kicad_sch_diff
# consumer + erc_check on the slow loop. Pair with `with_render=True` or
# follow with `get_render(zoom_to=…)` to see what your edit produced.
#
# Each edit produces a 1-line structural diff (no full-file churn) because
# kicad-sch-api owns the file format. The agent\'s sch_diff sees exactly
# what changed.
#
# Wire endpoint string format:
#   "U1.VCC"     — pin name on symbol U1 (resolved via kicad_lib)
#   "VCC1"       — bare net anchor (power/ground/terminal symbol position)
#   "@40.5,60"   — explicit (x, y) mm coordinate

def _render_after_edit(
    kicad_sch: Path, zoom_to: Optional[str], with_render: bool,
) -> Optional["Image"]:
    """Render the .kicad_sch directly. No JSON compile needed."""
    if not with_render:
        return None
    try:
        from hw_agent.schematics.render_focus import render_focused_png
        r = render_focused_png(kicad_sch, zoom_to=zoom_to)
        return Image(path=str(r["png_path"]))
    except Exception:
        return None


@mcp.tool()
def add_ic(
    kicad_sch: str,
    ref: str,
    lib_id: str,
    at_x: float,
    at_y: float,
    footprint: Optional[str] = None,
    rotation: float = 0.0,
    with_render: bool = True,
) -> list:
    """[author/write] Place a KiCad-library IC on a `.kicad_sch`.

    For inline-pin / custom ICs not in any KiCad library, use the Python
    DSL (`Sheet.ic(...)`) and re-run `schematic.py` instead.

    Args:
        kicad_sch: path to the `.kicad_sch` file
        ref: reference designator (e.g. "U1")
        lib_id: KiCad lib reference (e.g. "Regulator_Linear:AMS1117-3.3")
        at_x, at_y: position in mm
        footprint: optional footprint string (e.g. "Package_SO:SOIC-8")
        rotation: rotation in degrees (0/90/180/270)
        with_render: include a focused PNG zoomed to the new IC
    """
    from hw_agent.schematics import sch_ops
    p = Path(kicad_sch).resolve()
    try:
        entry = sch_ops.add_ic(p, ref, lib_id, (at_x, at_y),
                               footprint=footprint, rotation=rotation)
    except Exception as e:
        return [f"## add_ic: FAIL\n\n**Error:** {e}"]
    md = f"Added IC `{ref}` ({lib_id}) at ({at_x}, {at_y}) — uuid={entry['uuid'][:8]}"
    img = _render_after_edit(p, zoom_to=ref, with_render=with_render)
    return [md, img] if img else [md]


@mcp.tool()
def add_custom_ic(
    kicad_sch: str,
    ref: str,
    name: str,
    at_x: float,
    at_y: float,
    pins: list,
    width: float = 15.0,
    height: float = 12.0,
    footprint: Optional[str] = None,
    rotation: float = 0.0,
    with_render: bool = True,
) -> list:
    """[author/write] Place a custom IC whose symbol isn't in any standard
    KiCad library. Synthesizes a project-local lib_symbol entry in
    `hwagent.kicad_sym` and places via kicad-sch-api.

    Use this when:
      - `mcp__pcbparts__cse_get_kicad(<mpn>)` didn't return a usable symbol
      - You have pin name + position + side data from a datasheet, JLCPCB
        DB (jlc_get_pinout), or other source
      - The chip needs to land on the schematic now

    Args:
        kicad_sch: target `.kicad_sch` file
        ref: reference designator (e.g. "U1")
        name: chip MPN, becomes lib_id `hwagent:<name>` (e.g. "SY8205FCC")
        at_x, at_y: placement in mm (the body's center)
        pins: list of dicts: each {"name": "VIN", "at": [x, y], "side": "left"}
              `at` is absolute mm in the schematic (typically `name`-pin
              positions relative to the body center)
        width, height: body rectangle size in mm (default 15 × 12)
        footprint: optional `Library:Footprint` for PCB
        rotation: degrees (0/90/180/270)
        with_render: include focused PNG showing the placed chip

    Idempotent on re-adding the same `name` (skips lib_symbol synthesis if
    already present in hwagent.kicad_sym).
    """
    from hw_agent.schematics import sch_ops
    p = Path(kicad_sch).resolve()

    # Normalize pin entries — accept "at": [x, y] or "at": (x, y)
    norm_pins = []
    for pin in pins:
        at = pin.get("at")
        if isinstance(at, (list, tuple)) and len(at) == 2:
            at = (float(at[0]), float(at[1]))
        else:
            return [f"## add_custom_ic: FAIL\n\n**Error:** pin {pin.get('name')!r} has bad `at` value (need [x, y])"]
        norm_pins.append({
            "name": pin.get("name", ""),
            "at": at,
            "side": pin.get("side", "left"),
        })

    try:
        entry = sch_ops.add_custom_ic(
            p, ref=ref, name=name, at=(at_x, at_y),
            pins=norm_pins,
            size=(width, height),
            footprint=footprint, rotation=rotation,
        )
    except Exception as e:
        return [f"## add_custom_ic: FAIL\n\n**Error:** {e}"]
    md = (f"Added custom IC `{ref}` ({name}, {entry['pin_count']} pins) "
          f"at ({at_x}, {at_y}). Symbol synthesized in `{Path(entry['lib_path']).name}`.")
    img = _render_after_edit(p, zoom_to=ref, with_render=with_render)
    return [md, img] if img else [md]


@mcp.tool()
def add_capacitor(
    kicad_sch: str, ref: str, value: str,
    at_x: float, at_y: float, orient: str = "right",
    with_render: bool = False,
) -> list:
    """[author/write] Place a capacitor (Device:C). orient: up/down/left/right."""
    from hw_agent.schematics import sch_ops
    p = Path(kicad_sch).resolve()
    try:
        sch_ops.add_capacitor(p, ref, value, (at_x, at_y), orient=orient)
    except Exception as e:
        return [f"## add_capacitor: FAIL\n\n**Error:** {e}"]
    md = f"Added capacitor `{ref}` ({value}) at ({at_x}, {at_y}) orient={orient}"
    img = _render_after_edit(p, zoom_to=ref, with_render=with_render)
    return [md, img] if img else [md]


@mcp.tool()
def add_resistor(
    kicad_sch: str, ref: str, value: str,
    at_x: float, at_y: float, orient: str = "right",
    with_render: bool = False,
) -> list:
    """[author/write] Place a resistor (Device:R). orient: up/down/left/right."""
    from hw_agent.schematics import sch_ops
    p = Path(kicad_sch).resolve()
    try:
        sch_ops.add_resistor(p, ref, value, (at_x, at_y), orient=orient)
    except Exception as e:
        return [f"## add_resistor: FAIL\n\n**Error:** {e}"]
    md = f"Added resistor `{ref}` ({value}) at ({at_x}, {at_y}) orient={orient}"
    img = _render_after_edit(p, zoom_to=ref, with_render=with_render)
    return [md, img] if img else [md]


@mcp.tool()
def add_inductor(
    kicad_sch: str, ref: str, value: str,
    at_x: float, at_y: float, orient: str = "right",
    with_render: bool = False,
) -> list:
    """[author/write] Place an inductor (Device:L). orient: up/down/left/right."""
    from hw_agent.schematics import sch_ops
    p = Path(kicad_sch).resolve()
    try:
        sch_ops.add_inductor(p, ref, value, (at_x, at_y), orient=orient)
    except Exception as e:
        return [f"## add_inductor: FAIL\n\n**Error:** {e}"]
    md = f"Added inductor `{ref}` ({value}) at ({at_x}, {at_y}) orient={orient}"
    img = _render_after_edit(p, zoom_to=ref, with_render=with_render)
    return [md, img] if img else [md]


@mcp.tool()
def add_power(
    kicad_sch: str, ref: str, at_x: float, at_y: float,
    label: str = "VCC", with_render: bool = False,
) -> list:
    """[author/write] Place a power-rail symbol. label: "3V3"/"5V"/"12V"/"VCC"."""
    from hw_agent.schematics import sch_ops
    p = Path(kicad_sch).resolve()
    try:
        entry = sch_ops.add_power(p, ref, (at_x, at_y), label=label)
    except Exception as e:
        return [f"## add_power: FAIL\n\n**Error:** {e}"]
    md = f"Added power `{ref}` ({label}) at ({at_x}, {at_y}) — lib={entry['lib_id']}"
    img = _render_after_edit(p, zoom_to=ref, with_render=with_render)
    return [md, img] if img else [md]


@mcp.tool()
def add_ground(
    kicad_sch: str, ref: str, at_x: float, at_y: float,
    with_render: bool = False,
) -> list:
    """[author/write] Place a GND symbol (power:GND)."""
    from hw_agent.schematics import sch_ops
    p = Path(kicad_sch).resolve()
    try:
        sch_ops.add_ground(p, ref, (at_x, at_y))
    except Exception as e:
        return [f"## add_ground: FAIL\n\n**Error:** {e}"]
    md = f"Added ground `{ref}` at ({at_x}, {at_y})"
    img = _render_after_edit(p, zoom_to=ref, with_render=with_render)
    return [md, img] if img else [md]


@mcp.tool()
def add_wire(
    kicad_sch: str, src: str, dst: str,
    with_render: bool = False,
) -> list:
    """[author/write] Connect two endpoints with a wire.

    Endpoint string format:
        "U1.VCC"   — pin name on a placed symbol (resolved via kicad_lib)
        "VCC1"     — bare net anchor (power/ground/terminal)
        "@40.5,60" — explicit (x, y) mm coordinate
    """
    from hw_agent.schematics import sch_ops
    p = Path(kicad_sch).resolve()
    try:
        sch_ops.add_wire(p, src, dst)
    except Exception as e:
        return [f"## add_wire: FAIL\n\n**Error:** {e}"]
    md = f"Added wire `{src}` → `{dst}`"
    img = _render_after_edit(p, zoom_to=None, with_render=with_render)
    return [md, img] if img else [md]


@mcp.tool()
def set_value(
    kicad_sch: str, ref: str, value: str, with_render: bool = False,
) -> list:
    """[author/write] Update a symbol\'s Value property."""
    from hw_agent.schematics import sch_ops
    p = Path(kicad_sch).resolve()
    try:
        sch_ops.set_value(p, ref, value)
    except Exception as e:
        return [f"## set_value: FAIL\n\n**Error:** {e}"]
    md = f"`{ref}` value → {value!r}"
    img = _render_after_edit(p, zoom_to=ref, with_render=with_render)
    return [md, img] if img else [md]


@mcp.tool()
def set_footprint(
    kicad_sch: str, ref: str, footprint: str, with_render: bool = False,
) -> list:
    """[author/write] Update a symbol\'s Footprint property — Phase 3 PCB-side."""
    from hw_agent.schematics import sch_ops
    p = Path(kicad_sch).resolve()
    try:
        sch_ops.set_footprint(p, ref, footprint)
    except Exception as e:
        return [f"## set_footprint: FAIL\n\n**Error:** {e}"]
    md = f"`{ref}` footprint → `{footprint}`"
    img = _render_after_edit(p, zoom_to=ref, with_render=with_render)
    return [md, img] if img else [md]


@mcp.tool()
def remove_symbol(kicad_sch: str, ref: str, with_render: bool = False) -> list:
    """[author/write] Remove a symbol. Wires touching it remain (ERC will flag)."""
    from hw_agent.schematics import sch_ops
    p = Path(kicad_sch).resolve()
    try:
        result = sch_ops.remove(p, ref)
    except Exception as e:
        return [f"## remove_symbol: FAIL\n\n**Error:** {e}"]
    md = f"Removed `{ref}` (uuid={result['uuid'][:8]})"
    img = _render_after_edit(p, zoom_to=None, with_render=with_render)
    return [md, img] if img else [md]


@mcp.tool()
def list_pins(kicad_sch: str, ref: str) -> list:
    """[author/read] List pins on a placed symbol — name, number, position."""
    from hw_agent.schematics import sch_ops
    p = Path(kicad_sch).resolve()
    try:
        pins = sch_ops.list_pins(p, ref)
    except Exception as e:
        return [f"## list_pins: FAIL\n\n**Error:** {e}"]
    if not pins:
        return [f"`{ref}`: no pins found"]
    lines = [f"## `{ref}` pins ({len(pins)})", "",
             "| number | name | at |", "|--------|------|-----|"]
    for pin in pins:
        at = pin["at"]
        lines.append(f"| `{pin['number']}` | `{pin['name']}` | ({at[0]:.2f}, {at[1]:.2f}) |")
    return ["\n".join(lines)]


@mcp.tool()
def list_symbols(kicad_sch: str) -> list:
    """[author/read] Compact list of placed symbols on the sheet."""
    from hw_agent.schematics import sch_ops
    p = Path(kicad_sch).resolve()
    try:
        syms = sch_ops.list_symbols(p)
    except Exception as e:
        return [f"## list_symbols: FAIL\n\n**Error:** {e}"]
    if not syms:
        return ["*(empty)*"]
    lines = [f"## Symbols on `{p.name}` ({len(syms)})", "",
             "| ref | lib_id | at | value | footprint |",
             "|-----|--------|-----|-------|-----------|"]
    for s in syms:
        at = s["at"]
        lines.append(f"| `{s['id']}` | `{s['lib_id']}` | ({at[0]:.1f}, {at[1]:.1f}) | "
                     f"{s['value']} | {s['footprint']} |")
    return ["\n".join(lines)]


# ─── End atomic schematic edits ────────────────────────────────────────────


# ─── KiCad library fetch / install ─────────────────────────────────────────
#
# When `add_ic(lib_id="X:Y")` fails because library `X` isn't installed
# (no system KiCad, custom lib, OSS hardware project, SnapEDA-only chip),
# these tools download + cache + register the lib so a retry succeeds.
# Sources: KiCad's official symbols repo, pcbparts CSE, arbitrary git URLs.
# Cache lives under `~/.cache/hw_agent/kicad_libs/`.

@mcp.tool()
def find_kicad_lib(lib_id: str, project_dir: Optional[str] = None) -> dict:
    """[read] Resolve a `Library:Symbol` lib_id to a `.kicad_sym` file.

    Checks (in order): system KiCad install, project sym-lib-table, project
    `lib/` dir, user cache. Returns location + source, or `installed: False`
    if not found anywhere.

    Use this BEFORE `add_ic` if you suspect a library may be missing — or
    after `add_ic` fails with a "lib_id not found" error.
    """
    from hw_agent.schematics import lib_fetcher
    pdir = Path(project_dir) if project_dir else None
    found = lib_fetcher.find_lib(lib_id, project_dir=pdir)
    if found is None:
        return {
            "lib_id": lib_id,
            "installed": False,
            "hint": "call install_kicad_lib(lib_name, source) to fetch",
        }
    return {
        "lib_id": lib_id,
        "installed": True,
        "library": found.library,
        "symbol": found.symbol,
        "lib_path": str(found.lib_path),
        "source": found.source,
    }


@mcp.tool()
def install_kicad_lib(
    lib_name: str,
    source: str = "kicad-official",
    project_dir: Optional[str] = None,
    cse_text: Optional[str] = None,
    force: bool = False,
) -> dict:
    """[write] Download + cache a KiCad symbol library so subsequent
    `add_ic(lib_id=...)` calls resolve.

    Args:
        lib_name: library file name without extension (e.g. "Sensor_Motion",
                  "MCU_Microchip_PIC32"). Becomes `<lib_name>.kicad_sym`.
        source:   one of:
                  - "kicad-official" — KiCad's official symbols repo
                  - "cse:<mpn>"      — pcbparts Component Search Engine; you
                                        must first call cse_get_kicad and pass
                                        the kicad_sym text via `cse_text=...`
                  - "git:<url>"      — clone repo, find <lib_name>.kicad_sym
                  - "<https://...>"  — raw HTTP download
        project_dir: if provided, also copies the lib into
                     `<project_dir>/lib/` and registers it in the project's
                     `sym-lib-table`. Skip for cache-only installs.
        cse_text:    raw .kicad_sym body when source starts with "cse:".
        force:       re-download even if already cached.
    """
    from hw_agent.schematics import lib_fetcher
    pdir = Path(project_dir) if project_dir else None
    try:
        result = lib_fetcher.install(
            lib_name, source, project_dir=pdir,
            cse_text=cse_text, force=force,
        )
    except Exception as e:
        return {"ok": False, "lib_name": lib_name, "source": source,
                "error": str(e)}
    result["ok"] = True
    return result


@mcp.tool()
def list_installed_libs(project_dir: Optional[str] = None) -> dict:
    """[read] Inventory of available KiCad symbol libraries.

    Buckets:
      - `system` — found in the OS-wide KiCad install
      - `cache`  — fetched into `~/.cache/hw_agent/kicad_libs/`
      - `project`— registered in this project's `sym-lib-table` or under
                   `<project_dir>/lib/`
    """
    from hw_agent.schematics import lib_fetcher
    pdir = Path(project_dir) if project_dir else None
    return lib_fetcher.list_installed_libs(project_dir=pdir)


# ─── End KiCad library fetch / install ─────────────────────────────────────


@mcp.tool()
def design_view(
    project: str,
    view: str = "system",
    subsystem: Optional[str] = None,
) -> list:
    """[read] Look at the current design state visually + structurally.

    The agent's "eyes" — Phase B / Connector pattern #1 (screenshots as a
    first-class read primitive). Returns `[markdown_report, Image(png)]` so
    the agent can reason both visually and semantically about the design.

    Views:
      - "schematic"            — a single subsystem's .kicad_sch (requires `subsystem`)
      - "subsystem:<name>"     — same as above with name embedded
      - "system"               — the composed root .kicad_sch (default)
      - "pcb"                  — a single subsystem's .kicad_pcb (requires `subsystem`)
      - "pcb:<name>"           — same with name embedded
      - "system_pcb"           — system-level .kicad_pcb (when one exists)

    Returns:
      [markdown_summary, Image(png)] — markdown reports ERC pass/fail or DRC
      pass/fail with bucketed violations; PNG is a 2x-scale render of the
      current state.

    Args:
        project:    project root, e.g. "docs/projects/robocar-hub"
        view:       see views above
        subsystem:  required for schematic / pcb views; e.g. "buck_converter"
    """
    pdir = Path(project)
    if not pdir.exists():
        return [f"# design_view: project not found\n`{pdir}` does not exist"]

    # Decode "subsystem:<name>" or "pcb:<name>" shorthand
    if ":" in view:
        view, subsystem = view.split(":", 1)

    # ── Schematic views ────────────────────────────────────────────────
    if view in ("schematic", "subsystem"):
        if not subsystem:
            return ["# design_view\n`schematic` view requires `subsystem` arg"]
        sch_dir = pdir / "components" / subsystem
        sch = sch_dir / "schematic.kicad_sch"
        if not sch.exists():
            return [f"# design_view: not built yet\n`{sch}` doesn't exist — run `eval_subsystem` first"]
        # Reuse eval_subsystem so we get current ERC status alongside the render
        from hw_agent.schematics.eval import eval_from_json
        json_path = sch_dir / "schematic.schem.json"
        out = []
        if json_path.exists():
            result = eval_from_json(json_path)
            md = _format_erc_markdown(result.to_dict())
            md = f"# design_view — schematic / `{subsystem}`\n\n" + md
            out.append(md)
        else:
            out.append(f"# design_view — schematic / `{subsystem}`\n\n(no JSON source — rendering existing .kicad_sch)")
        svg = _kicad_sch_to_svg(sch)
        img = _svg_path_to_image(svg) if svg else None
        if img:
            out.append(img)
        return out

    if view == "system":
        sch = pdir / "kicad" / "system.kicad_sch"
        if not sch.exists():
            return [f"# design_view: system not composed\nrun `system_export_kicad('{project}')` first"]
        svg = _kicad_sch_to_svg(sch)
        img = _svg_path_to_image(svg) if svg else None
        out = [f"# design_view — system\n\n`{sch}`"]
        if img:
            out.append(img)
        return out

    # ── PCB views ──────────────────────────────────────────────────────
    if view == "pcb":
        if not subsystem:
            return ["# design_view\n`pcb` view requires `subsystem` arg"]
        sub_dir = pdir / "components" / subsystem
        pcb = sub_dir / "schematic.kicad_pcb"
        if not pcb.exists():
            return [f"# design_view: PCB not built yet\nrun `pcb_check` consumer (slow loop) or save the JSON to trigger build"]
        # Build DRC summary via the same pipeline preview.pcb_check uses
        import json
        from hw_agent.schematics.drc_filters import classify, load_filters
        import subprocess as sp
        from hw_agent.schematics.kicad_paths import kicad_cli
        json_path = sub_dir / "schematic.schem.json"
        drc_path = sub_dir / "eval_out" / f"{pcb.stem}_drc.json"
        drc_path.parent.mkdir(parents=True, exist_ok=True)
        sp.run([kicad_cli(), "pcb", "drc", "--output", str(drc_path),
                "--format", "json", "--severity-all", str(pcb)],
               capture_output=True, text=True)
        drc_classified = {}
        if drc_path.exists():
            try:
                drc_classified = classify(json.loads(drc_path.read_text()),
                                          load_filters(json_path) if json_path.exists() else [])
            except Exception:
                pass
        md = f"# design_view — pcb / `{subsystem}`\n\n"
        md += _format_drc_markdown(drc_classified, board_label=subsystem) if drc_classified else f"`{pcb}`\n\n(no DRC report)"
        svg = _kicad_pcb_to_svg(pcb)
        img = _svg_path_to_image(svg) if svg else None
        out = [md]
        if img:
            out.append(img)
        return out

    if view == "system_pcb":
        pcb = pdir / "kicad" / "system.kicad_pcb"
        if not pcb.exists():
            return [f"# design_view: system PCB not built\n(system-level PCB pipeline not yet wired; build per-subsystem PCBs for now)"]
        svg = _kicad_pcb_to_svg(pcb)
        img = _svg_path_to_image(svg) if svg else None
        out = [f"# design_view — system_pcb\n\n`{pcb}`"]
        if img:
            out.append(img)
        return out

    return [f"# design_view: unknown view `{view}`\nValid: schematic, system, pcb, system_pcb"]


@mcp.tool()
def design_state(project: str) -> dict:
    """[read] Pure-data snapshot of the project's current state. The peer to
    `design_view` — no images, no markdown, just structured fields cheap
    enough to poll between operations.

    Reads cached status files (`schematic.eval.json`, `preview.eval.json`)
    rather than re-running ERC/DRC, so it's <50ms even for a 7-subsystem
    project. Re-run `eval_subsystem` or wait for the watcher's slow loop
    to refresh the underlying caches.

    Args:
        project: project root, e.g. "docs/projects/robocar-hub"

    Returns:
        {
          "project":   <name>,
          "design":    {...design.yaml content...},
          "bom":       {total_lines, total_cost_usd, out_of_stock, ...},
          "subsystems": {
            "<name>": {
              "files": {schem_json, kicad_sch, kicad_pcb, present: bool},
              "footprints": {assigned: int, missing: int, refs_missing: [...]},
              "erc": {ok, real_issues, expected, total, ts},   # from schematic.eval.json
              "pcb": {ok, components_placed, drc_real_issues,
                      drc_expected, tracks_routed, fab_ready, ts},
            }
          },
          "system": { kicad_sch: bool, kicad_pcb: bool, ts },
          "ready_for_fabrication": bool,    # rollup: all subsystems ERC+DRC clean
        }
    """
    import json
    import os
    pdir = Path(project)
    if not pdir.exists():
        return {"error": f"project not found: {pdir}"}

    state: dict = {"project": pdir.name}

    # ── design.yaml (project-level metadata) ─────────────────────────────
    design_yaml = pdir / "design.yaml"
    if design_yaml.exists():
        try:
            import yaml
            state["design"] = yaml.safe_load(design_yaml.read_text()) or {}
        except Exception as e:
            state["design"] = {"_error": str(e)}
    else:
        state["design"] = None

    # ── BOM rollup (cheap; reads bom.json directly) ──────────────────────
    bom_path = pdir / "bom.json"
    if bom_path.exists():
        try:
            bom = json.loads(bom_path.read_text())
            entries = bom.get("entries", []) if isinstance(bom, dict) else bom
            total_cost = 0.0
            out_of_stock: list = []
            for e in entries:
                price = e.get("unit_price_usd") or 0
                qty = e.get("quantity") or 1
                total_cost += float(price) * float(qty)
                if e.get("stock") is not None and e.get("stock") < 1:
                    out_of_stock.append(e.get("lcsc") or e.get("part") or "?")
            state["bom"] = {
                "total_lines": len(entries),
                "total_cost_usd": round(total_cost, 2),
                "out_of_stock_count": len(out_of_stock),
                "out_of_stock": out_of_stock[:10],
            }
        except Exception as e:
            state["bom"] = {"_error": str(e)}
    else:
        state["bom"] = None

    # ── Per-subsystem state (reads cached eval files; no subprocess) ─────
    subsystems_dir = pdir / "components"
    subs: dict = {}
    all_subs_clean = True
    if subsystems_dir.exists():
        for sub_dir in sorted(subsystems_dir.iterdir()):
            if not sub_dir.is_dir():
                continue
            sub_name = sub_dir.name
            schem_json = sub_dir / "schematic.schem.json"
            kicad_sch = sub_dir / "schematic.kicad_sch"
            kicad_pcb = sub_dir / "schematic.kicad_pcb"

            entry: dict = {
                "files": {
                    "schem_json": schem_json.exists(),
                    "kicad_sch": kicad_sch.exists(),
                    "kicad_pcb": kicad_pcb.exists(),
                },
            }

            # Footprint stats — cheap parse of the schem.json
            if schem_json.exists():
                try:
                    sj = json.loads(schem_json.read_text())
                    physical_types = {"resistor", "capacitor", "inductor",
                                       "diode", "ic", "kicad"}
                    assigned = 0
                    missing: list = []
                    for s in sj.get("symbols", []):
                        if s.get("type") in physical_types:
                            if s.get("footprint"):
                                assigned += 1
                            else:
                                missing.append(s.get("id", "?"))
                    entry["footprints"] = {
                        "assigned": assigned,
                        "missing": len(missing),
                        "refs_missing": missing[:10],
                    }
                except Exception as e:
                    entry["footprints"] = {"_error": str(e)}

            # ERC from schematic.eval.json (written by eval_from_json)
            erc_eval = sub_dir / "schematic.eval.json"
            if erc_eval.exists():
                try:
                    e = json.loads(erc_eval.read_text())
                    erc = e.get("erc") or {}
                    entry["erc"] = {
                        "ok": e.get("ok", False),
                        "total": erc.get("total", 0),
                        "real_issues": len(erc.get("real_issues", [])),
                        "expected": erc.get("expected", {}),
                        "duration_ms": e.get("duration_ms"),
                        "ts": e.get("timestamp"),
                    }
                except Exception:
                    entry["erc"] = None
            else:
                entry["erc"] = None

            # PCB from preview.eval.json (written by watcher.pcb_check)
            preview_eval = sub_dir / "preview.eval.json"
            if preview_eval.exists():
                try:
                    p = json.loads(preview_eval.read_text())
                    pcb_check = (p.get("results") or {}).get("pcb_check") or {}
                    if pcb_check:
                        real = len(pcb_check.get("drc_real_issues", []))
                        entry["pcb"] = {
                            "ok": pcb_check.get("ok", False) and real == 0,
                            "components_placed": pcb_check.get("components_placed", 0),
                            "components_skipped": pcb_check.get("components_skipped", 0),
                            "drc_total": pcb_check.get("drc_total", 0),
                            "drc_real_issues": real,
                            "drc_expected": pcb_check.get("drc_expected", {}),
                            "ran_at": pcb_check.get("ran_at"),
                        }
                except Exception:
                    entry["pcb"] = None

            # Subsystem-clean check for the rollup
            erc_clean = (entry.get("erc") or {}).get("ok") is True
            pcb_clean = (entry.get("pcb") or {}).get("ok") is True or entry.get("pcb") is None
            if not (erc_clean and pcb_clean):
                all_subs_clean = False

            subs[sub_name] = entry

    state["subsystems"] = subs

    # ── System level ─────────────────────────────────────────────────────
    sys_sch = pdir / "kicad" / "system.kicad_sch"
    sys_pcb = pdir / "kicad" / "system.kicad_pcb"
    state["system"] = {
        "kicad_sch": sys_sch.exists(),
        "kicad_pcb": sys_pcb.exists(),
        "modified_ts": (
            os.path.getmtime(sys_sch) if sys_sch.exists() else None
        ),
    }

    state["ready_for_fabrication"] = all_subs_clean and bool(subs)
    return state


# ─── PCB live edits (require pcbnew running with API server) ──────────────

@mcp.tool()
def pcb_ipc_status() -> dict:
    """[read] Is pcbnew running with IPC reachable?

    Live PCB edits (`move_footprint`, `pcb_save`, `list_pcb_footprints`)
    require pcbnew open with API server enabled (Preferences → API server).
    Headless ERC/DRC/exports go through kicad-cli — those don't need this.
    """
    import os as _os
    from hw_agent.schematics import pcb_backend
    available = pcb_backend.is_ipc_available()
    return {
        "ipc_available": available,
        "kicad_api_socket": "set" if _os.environ.get("KICAD_API_SOCKET") else "unset",
        "note": None if available else (
            "Open pcbnew with API server enabled to use live PCB edits."
        ),
    }


@mcp.tool()
def list_pcb_footprints(kicad_pcb: str) -> list:
    """[read] Live list of footprints on the open `.kicad_pcb`.

    Reads from pcbnew via IPC — positions reflect any unsaved edits the
    user has made. Returns an "open pcbnew" error if IPC isn't reachable.
    """
    from hw_agent.schematics import pcb_backend
    if not pcb_backend.is_ipc_available():
        return ["## list_pcb_footprints: pcbnew not open\n\n"
                "Open pcbnew with API server enabled, then retry."]
    fps = pcb_backend.list_footprints(Path(kicad_pcb).resolve())
    if not fps:
        return ["*(no footprints found)*"]
    lines = [f"## Footprints on `{Path(kicad_pcb).name}` ({len(fps)})", "",
             "| ref | value | at (mm) | rot | layer |",
             "|-----|-------|---------|-----|-------|"]
    for f in fps:
        at = f["at_mm"]
        lines.append(
            f"| `{f['ref']}` | {f['value']} | "
            f"({at[0]:.2f}, {at[1]:.2f}) | {f['rotation_deg']:.0f}° "
            f"| {f['layer']} |"
        )
    return ["\n".join(lines)]


@mcp.tool()
def move_footprint(
    kicad_pcb: str, ref: str, x_mm: float, y_mm: float,
    rotation_deg: Optional[float] = None,
    with_render: bool = True,
) -> list:
    """[author/write] Move a footprint live in pcbnew via IPC.

    The footprint moves immediately and the user sees it. Save with
    `pcb_save` after a sequence of edits, or save manually in pcbnew.

    Args:
        kicad_pcb: path (used for logging; IPC operates on the active board)
        ref: reference designator (e.g. "U1", "R5")
        x_mm, y_mm: new position in mm
        rotation_deg: optional rotation; None leaves unchanged
        with_render: include an inline PNG zoomed to the moved
            footprint. Requires pcb_save to have run (or pcbnew has
            saved manually) for the on-disk file to reflect the edit.
    """
    from hw_agent.schematics import pcb_backend
    result = pcb_backend.move_footprint(
        Path(kicad_pcb).resolve(), ref, x_mm, y_mm, rotation=rotation_deg,
    )
    md = (f"Moved `{ref}` to ({x_mm}, {y_mm})"
          + (f" rot={rotation_deg}°" if rotation_deg is not None else "")
          + f" — {result.get('error', 'ok')}")
    svg_md, img = _pcb_render_after_edit(Path(kicad_pcb).resolve(),
                                         zoom_to=ref, with_render=with_render)
    return [md + (svg_md or ""), img] if img else [md]


@mcp.tool()
def pcb_save(kicad_pcb: str, with_render: bool = False) -> list:
    """[author/write] Force-save the open `.kicad_pcb` to disk.

    IPC's commit lifecycle updates pcbnew's in-memory state but doesn't
    write the file. Call this after a sequence of live edits to persist.

    `with_render=True` returns a full-sheet PCB PNG so the agent sees
    the saved state (useful as a "checkpoint" view).
    """
    from hw_agent.schematics import pcb_backend
    result = pcb_backend.save_board(Path(kicad_pcb).resolve())
    md = f"pcb_save: {'ok' if result.get('ok') else result.get('error', 'failed')}"
    svg_md, img = _pcb_render_after_edit(Path(kicad_pcb).resolve(),
                                         zoom_to=None, with_render=with_render)
    return [md + (svg_md or ""), img] if img else [md]


def _pcb_render_after_edit(
    kicad_pcb: Path, zoom_to: Optional[str], with_render: bool,
) -> tuple[Optional[str], Optional["Image"]]:
    """Render the .kicad_pcb. Returns (svg_link_md, png_image) so the
    caller surfaces the PNG inline (VLM-visible) and the SVG path as
    text (for grep/coord lookups when the agent needs them).

    Read can't render SVG visually, so the rasterized PNG is what the
    VLM actually sees. The SVG is the structural source of truth.
    """
    if not with_render:
        return None, None
    try:
        from hw_agent.schematics.pcb_render import render_pcb
        r = render_pcb(kicad_pcb, zoom_to=zoom_to)
        return f"\n_SVG: `{r['svg_path']}`_", Image(path=str(r["png_path"]))
    except Exception:
        return None, None


# ─── End PCB live edits ────────────────────────────────────────────────────


@mcp.tool()
def pcb_export_fabrication(kicad_pcb: str, out_dir: Optional[str] = None) -> dict:
    """[compute] Export the JLCPCB / PCBWay fabrication bundle from a routed
    .kicad_pcb.

    Generates gerbers, drill files (PTH+NPTH), and pick-and-place CSV.
    Output goes to <project>/fabrication/<board_stem>/ by default.

    Run AFTER the board is routed and DRC-clean. Returns paths to all
    generated files; on success the directory is ready to zip and upload.

    Args:
        kicad_pcb: path to the .kicad_pcb (must have routes already)
        out_dir:   override default output location
    """
    from hw_agent.schematics.pcb_writer import export_fabrication

    pcb = Path(kicad_pcb).resolve()
    if not pcb.exists():
        return {"ok": False, "error": f"not found: {pcb}"}
    od = Path(out_dir).resolve() if out_dir else None
    return export_fabrication(pcb, od)


@mcp.tool()
async def pcb_route(
    kicad_pcb: str,
    kicad_sch: Optional[str] = None,
    passes: int = 5,
    threads: int = 4,
    timeout_s: float = 300,
    ctx: Context = None,
) -> dict:
    """[write] Auto-route a .kicad_pcb via FreeRouting (headless), with
    progress streaming.

    Pipeline:
        1. kicad-cli sch export netlist  → .net           (5%)
        2. pcb_writer.run_sync_netlist (IPC)        → nets on pads  (10-15%)
        3. pcb_writer.run_dsn_export (IPC)          → Specctra DSN  (20%)
        4. java -jar freerouting.jar      → routes        (25-90%, per-pass)
        5. pcb_writer.run_ses_import (IPC)          → tracks back   (95-100%)

    Streams `ctx.report_progress(pct, 100, msg)` at every milestone plus
    once per FreeRouting pass — agent watches the route progress instead
    of seeing a 30-60s silence.

    Args:
        kicad_pcb:  the board to route (modified in place)
        kicad_sch:  source schematic for net info (default: same dir as PCB)
        passes:     freerouting pass limit (more = more thorough, default 5)
        threads:    parallelism (default 4)
        timeout_s:  give up after this many seconds (default 300)
    """
    import asyncio
    from hw_agent.freerouting import route_board

    pcb = Path(kicad_pcb).resolve()
    sch = Path(kicad_sch).resolve() if kicad_sch else None

    # Bridge: route_board's worker thread pushes progress tuples here;
    # the main coroutine drains the queue and awaits ctx.report_progress.
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_progress(current: int, total: int, msg: str):
        # Called from route_board's thread; schedule the put on our loop.
        loop.call_soon_threadsafe(queue.put_nowait, (current, total, msg))

    async def _drain_progress():
        while True:
            current, total, msg = await queue.get()
            if ctx is not None:
                try:
                    await ctx.report_progress(current, total, msg)
                except Exception:
                    pass  # progress streaming is best-effort
            if current >= total:  # final tick
                return

    drain_task = asyncio.create_task(_drain_progress())
    try:
        result = await asyncio.to_thread(
            route_board, pcb, kicad_sch=sch, passes=passes,
            threads=threads, timeout_s=timeout_s, on_progress=on_progress,
        )
    finally:
        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            pass
    return result


@mcp.tool()
def system_export_kicad(project_dir: str, output: Optional[str] = None) -> list:
    """[write] Generate a root hierarchical .kicad_sch that wires all per-subsystem
    .kicad_sch files together via (sheet ...) blocks.

    The root sheet is placed at <project_dir>/kicad/system.kicad_sch by
    default. Each subsystem becomes a sheet block on an A3 page with sheet
    pins for its power rails (5V, 3V3, GND in/out) and signal terminals.

    Args:
        project_dir: project root, e.g. docs/projects/robocar-hub
        output: optional output path; defaults to <project_dir>/kicad/system.kicad_sch

    Returns confirmation text + an inline PNG of the composed system.
    """
    from hw_agent.schematics.system_composer import compose_root

    pdir = Path(project_dir)
    out = Path(output) if output else pdir / "kicad" / "system.kicad_sch"
    sch = Path(compose_root(pdir, out))
    text = f"Composed system schematic: `{sch}`"
    svg = _kicad_sch_to_svg(sch)
    img = _svg_path_to_image(svg) if svg else None
    return [text, img] if img else [text]


# ─── System Schematic ────────────────────────────────────────────────────────

@mcp.tool()
def schem_system(
    project: str = "robocar-hub",
    output: str = "system_schematic.svg",
) -> list:
    """Generate a full system-level schemdraw SVG from design.yaml.

    Reads design.yaml, draws all explored components (buck, LDO, MCU,
    motor drivers) with power rails, GPIO connections, and I2C buses.
    Auto-updates as new components are explored.
    """
    from hw_agent.schematics.svg import render_system_schematic
    r = render_system_schematic(project_slug=project, output=output)
    return [f"PNG: `{r['png']}` · SVG: `{r['svg']}`", Image(path=str(r["png"]))]


# ─── Constraints Engine ──────────────────────────────────────────────────────

@mcp.tool()
def constraints_check(project: str, component: Optional[str] = None) -> dict:
    """Evaluate parametric constraints for one or all components.

    Reads design.yaml + each component's constraints.yaml, runs math
    expressions against the current design state (rail loads, pin budget,
    thermal, budget), returns pass/warn/fail with margins.

    The 10% proximity warning catches issues BEFORE they become hard failures.
    """
    from pathlib import Path
    from hw_agent.schematics.constraints import evaluate_all, evaluate_constraints, load_constraints
    import yaml

    design_path = Path("docs/projects") / project / "design.yaml"
    if not design_path.exists():
        return {"error": f"No design.yaml found at {design_path}"}

    if component:
        design = yaml.safe_load(design_path.read_text()) or {}
        comp_info = design.get("components", {}).get(component, {})
        folder = comp_info.get("folder", f"components/{component}")
        constraints_path = design_path.parent / folder / "constraints.yaml"
        if not constraints_path.exists():
            return {"error": f"No constraints.yaml for {component}"}
        comp_constraints = load_constraints(constraints_path)
        report = evaluate_constraints(design, comp_constraints, component)
        return report.to_dict()
    else:
        reports = evaluate_all(design_path)
        return {
            "project": project,
            "components_checked": len(reports),
            "all_ok": all(r.ok for r in reports),
            "reports": [r.to_dict() for r in reports],
            "summary": "\n\n".join(r.summary() for r in reports),
        }


# ─── Vendor seed resources + pcborder tools ─────────────────────────────────
#
# Hand-curated PCB-vendor specs (capabilities, BOM/CPL column maps, lead
# times) live in hw_agent/data/vendors/<slug>.json. They're exposed as
# MCP resources so the agent can read them as context, and as typed
# tools for programmatic compatibility checks.

import json as _json

from pcborder import (
    Settings,
    ValidateResult,
    ValidationIssue,
    VendorSeed,
    list_vendor_slugs,
    load_vendor_seed,
    validate_for_vendor,
)

# The on-disk path that backs the resource handlers. Defined once here
# so they can read raw JSON text (preserving the file's exact formatting)
# while typed code uses `load_vendor_seed` from pcborder.
_VENDOR_DATA_DIR = Path(__file__).parent / "data" / "vendors"


def _envelope_with_single_error(issue: ValidationIssue, summary: str) -> dict:
    """MCP-friendly envelope carrying a single ValidationIssue.

    Delegates to `ValidateResult.from_single_issue` so the wire shape is
    structurally identical to a normal validation result. The agent can
    always read `errors[0].code`, `.field`, etc., regardless of where
    the error originated.
    """
    return ValidateResult.from_single_issue(
        issue, summary=summary,
    ).model_dump(mode="json")


def _validate_settings_or_envelope(
    settings: Optional[dict],
) -> tuple[Optional["Settings"], Optional[dict]]:
    """Try to validate `settings`; on schema failure return a ready-to-emit
    error envelope instead. Saves boilerplate in tools that begin with
    `try: Settings.model_validate(...)`.
    """
    try:
        return Settings.model_validate(settings or {}), None
    except Exception as exc:  # noqa: BLE001 — surface schema message
        envelope = _envelope_with_single_error(
            ValidationIssue(
                code="SETTINGS_SCHEMA_INVALID",
                field="settings",
                severity="error",
                current_value=settings,
                message=f"settings failed schema validation: {exc}",
            ),
            summary="invalid settings",
        )
        return None, envelope


@mcp.resource("vendor://list", mime_type="application/json")
def vendor_list_resource() -> str:
    """JSON array of available vendor slugs (one per `vendor://<slug>` resource)."""
    return _json.dumps(list_vendor_slugs())


@mcp.resource("vendor://{slug}", mime_type="application/json")
def vendor_seed_resource(slug: str) -> str:
    """Full JSON spec for a vendor: capabilities, column maps, lead times.

    Returns the raw seed file contents so clients see the verbatim,
    human-edited JSON (including any extra fields the Pydantic model
    ignores).
    """
    path = _VENDOR_DATA_DIR / f"{slug}.json"
    if not path.exists():
        available = ", ".join(list_vendor_slugs()) or "(none)"
        raise FileNotFoundError(
            f"unknown vendor '{slug}'. available: {available}"
        )
    return path.read_text()


@mcp.tool()
def pcborder_validate_for_vendor(
    settings: Optional[dict] = None,
    vendor: str = "",
    bom: Optional[list[dict]] = None,
) -> dict:
    """[read] Check whether order settings are compatible with a vendor.

    Cross-checks `settings` against the vendor's published capabilities
    (layers, surface finish, mask/silk colors, min trace/via/hole,
    thickness, copper weight, quantity floors, castellated/via-in-pad/
    edge-plating/impedance, assembly availability, lead-time tiers) plus
    the universal physical-floor checks from `pcborder.validate`.

    Args:
        settings: dict matching `pcborder.Settings`. `null` and omitted
            both behave as "use defaults".
        vendor:   vendor slug (`jlcpcb`, `pcbway`, `oshpark`).
        bom:      optional list of BOM-line dicts. Accepts canonical
            BomLine shape (refdes/value/...) or `bom_list`-tool shape
            (subsystem/mpn/package/...) — auto-coerced.

    Returns:
        Structured result: `{is_clean, summary, errors, warnings,
        suggestions}`. `errors`/`warnings` are lists of `ValidationIssue`
        dicts (`code, field, severity, current_value, expected, vendor,
        message`). `suggestions` is a list of `ValidationFix` dicts
        (`field, current_value, suggested_value, reason, auto_apply_safe`)
        the orchestrator can mechanically apply.
    """
    try:
        vendor_seed = load_vendor_seed(vendor)
    except FileNotFoundError:
        available = ", ".join(list_vendor_slugs()) or "(none)"
        return _envelope_with_single_error(
            ValidationIssue(
                code="VENDOR_NOT_FOUND",
                field="vendor",
                severity="error",
                current_value=vendor,
                message=f"unknown vendor '{vendor}'. available: {available}",
            ),
            summary="unknown vendor",
        )

    validated_settings, envelope = _validate_settings_or_envelope(settings)
    if envelope is not None:
        return envelope

    result = validate_for_vendor(validated_settings, vendor_seed, bom=bom)
    return result.model_dump(mode="json")


@mcp.tool()
def compare_vendors(
    settings: Optional[dict] = None,
    bom: Optional[list[dict]] = None,
) -> dict:
    """[read] Run vendor compatibility validation against all known vendors.

    One call returns a comparison table — saves the orchestrator three
    round-trips when the agent doesn't know which vendor is right.

    Args:
        settings: dict matching `pcborder.Settings`. `null` and omitted
            both behave as "use defaults".
        bom:      optional list of BOM-line dicts. Accepts canonical
            BomLine shape (`refdes`/`value`/...) or `bom_list`-tool shape
            (`subsystem`/`mpn`/`package`/...) — auto-coerced.

    Returns:
        On success: `{compatible_vendors: [slug, ...], vendors: {slug:
        ValidateResult-envelope, ...}}`. On schema failure: same shape
        but with `compatible_vendors=[]`, `vendors={}`, and a top-level
        `settings_error` carrying the same envelope shape.
    """
    # Validate settings ONCE up front — if the schema is invalid, the
    # error is vendor-independent and we shouldn't surface it per-vendor.
    validated_settings, envelope = _validate_settings_or_envelope(settings)
    if envelope is not None:
        # `settings_error` carries the SAME envelope shape as a per-vendor
        # entry would — `{is_clean, summary, errors, warnings, suggestions}`
        # — so consumers can read `settings_error["errors"][0]["code"]`
        # exactly like they read `vendors["jlcpcb"]["errors"][0]["code"]`.
        return {
            "compatible_vendors": [],
            "vendors": {},
            "settings_error": envelope,
        }

    per_vendor: dict = {}
    compatible: list[str] = []
    for slug in list_vendor_slugs():
        # `load_vendor_seed` is lru_cache'd; first call loads from disk,
        # subsequent calls are dict lookups.
        vendor_seed = load_vendor_seed(slug)
        result = validate_for_vendor(
            validated_settings, vendor_seed, bom=bom,
        )
        per_vendor[slug] = result.model_dump(mode="json")
        if result.is_clean:
            compatible.append(slug)

    return {
        "compatible_vendors": compatible,
        "vendors": per_vendor,
    }


@mcp.tool()
def pcborder_settings_schema() -> dict:
    """[read] JSON Schema for `pcborder.Settings`.

    Lets an agent introspect what fields are valid (and their types,
    enums, defaults) without reading source. Same shape FastAPI/OpenAPI
    consumers expect.
    """
    return Settings.model_json_schema()


@mcp.tool()
def pcborder_vendor_seed_schema() -> dict:
    """[read] JSON Schema for `pcborder.VendorSeed` — one vendor's seed file."""
    return VendorSeed.model_json_schema()


# ─── Project order-settings persistence ─────────────────────────────────────


def _project_order_settings_path(project: str):
    from hw_agent.project_state.paths import project_dir
    return project_dir(project) / "order_settings.json"


@mcp.tool()
def order_settings_get(project: str) -> dict:
    """[read] Read saved order Settings for a project.

    Returns the contents of `docs/projects/<project>/order_settings.json`
    if present, else `pcborder.Settings()` defaults.

    Args:
        project: project slug — letters/digits/underscore start, then
            letters/digits/hyphen/underscore (max 64 chars). Path
            traversal sequences are rejected up front.

    Returns:
        `{project, source, path?, settings, error?}` where `source` is
        one of `"default"` (no file yet), `"saved"` (file loaded OK), or
        `"corrupt"` (file exists but failed to parse — defaults still
        returned in `settings` so the caller recovers without crashing).
        Raises `ValueError` only for nonexistent / unsafe project slug.
    """
    err = _require_project_exists(project)
    if err is not None:
        raise ValueError(err)
    path = _project_order_settings_path(project)
    if not path.exists():
        return {
            "project": project,
            "source": "default",
            "settings": Settings().model_dump(mode="json"),
        }
    try:
        saved = Settings.model_validate_json(path.read_text())
    except Exception as exc:  # noqa: BLE001 — surface in response, don't crash
        return {
            "project": project,
            "source": "corrupt",
            "path": str(path),
            "error": (
                f"saved order_settings.json failed to load: {exc}. "
                f"Falling back to defaults; call order_settings_set to repair."
            ),
            "settings": Settings().model_dump(mode="json"),
        }
    return {
        "project": project,
        "source": "saved",
        "path": str(path),
        "settings": saved.model_dump(mode="json"),
    }


@mcp.tool()
def order_settings_set(project: str, settings: dict) -> dict:
    """[write] Persist order Settings for a project.

    Args:
        project:  project slug (same shape as `order_settings_get`).
        settings: dict matching `pcborder.Settings`. Validated against
            the schema before any write happens — bad input rejects.

    Returns:
        `{ok: True, project, path, settings}` after the write.
        Raises `pydantic.ValidationError` on schema failure (no partial
        write); raises `ValueError` for nonexistent / unsafe project slug.

    Writes pretty-printed JSON to
    `docs/projects/<project>/order_settings.json`.
    """
    err = _require_project_exists(project)
    if err is not None:
        raise ValueError(err)
    validated = Settings.model_validate(settings)
    path = _project_order_settings_path(project)
    path.write_text(validated.model_dump_json(indent=2))
    return {
        "ok": True,
        "project": project,
        "path": str(path),
        "settings": validated.model_dump(mode="json"),
    }


# ─── PCB-derived BOM extraction ─────────────────────────────────────────────


_KICAD_PCB_HEADER = "(kicad_pcb"


def _refdes_sort_key(refdes: str) -> tuple:
    """Natural-sort key: split into (alpha-prefix, int-suffix, tail).

    Without this, lexical sort gives R1, R10, R2; we want R1, R2, R10.
    """
    match = re.match(r"^([A-Za-z_]+)(\d+)?(.*)$", refdes)
    if match is None:
        return (refdes, 0, "")
    prefix, number, tail = match.groups()
    return (prefix, int(number) if number else 0, tail or "")


def _iter_footprint_blocks(pcb_text: str):
    """Yield the raw `(footprint ...)` substring for each footprint in a
    `.kicad_pcb` file.

    Walks character-by-character with paren-depth tracking and string
    awareness so an unbalanced `)` inside a quoted property doesn't
    confuse the slicer.
    """
    cursor = 0
    while True:
        match = re.search(r"\(footprint\s+", pcb_text[cursor:])
        if match is None:
            return
        block_start = cursor + match.start()
        depth = 0
        in_string = False
        prev_char = ""
        end = block_start
        for offset, char in enumerate(pcb_text[block_start:], start=block_start):
            if char == '"' and prev_char != "\\":
                in_string = not in_string
            elif not in_string:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        end = offset
                        break
            prev_char = char
        yield pcb_text[block_start:end + 1]
        cursor = end + 1


def _extract_footprint_properties(block: str) -> dict[str, str]:
    """Pull the `(property "X" "Y")` pairs out of one footprint block.

    Returns canonical KiCad property names as keys (`Reference`, `Value`,
    `Footprint`, optional `MPN`, `LCSC`, `Manufacturer`, …).
    """
    properties: dict[str, str] = {}
    for prop_match in re.finditer(
        r'\(property\s+"([^"]+)"\s+"([^"]*)"', block,
    ):
        properties[prop_match.group(1)] = prop_match.group(2)
    return properties


def _first_property(properties: dict[str, str], *names: str) -> str:
    """Return the first non-empty property among `names`, else "".

    KiCad fields drift across schematic styles — `MPN`, `Manufacturer
    Part Number`, `Mfr_PN` all show up. This collapses the fallback chain.
    """
    for name in names:
        value = properties.get(name)
        if value:
            return value
    return ""


def _is_dnp(block: str, properties: dict[str, str]) -> bool:
    """Whether a footprint block is marked do-not-place.

    KiCad 9 emits `(attr ... dnp ...)`; older boards may set a `DNP`
    property to a truthy string.
    """
    dnp_attr = bool(re.search(r"\(attr[^)]*\bdnp\b", block))
    dnp_prop = properties.get("DNP", "").strip().lower()
    return dnp_attr or dnp_prop in {"true", "yes", "1", "x"}


def _extract_kicad_pcb_footprints(pcb_text: str) -> list[dict]:
    """Walk a `.kicad_pcb` and return one canonical row per footprint.

    Skips library placeholders with bare reference designators
    (`REF**`). Returned dicts use canonical snake_case keys; downstream
    code maps to vendor column names.
    """
    rows: list[dict] = []
    for block in _iter_footprint_blocks(pcb_text):
        properties = _extract_footprint_properties(block)
        reference = properties.get("Reference", "")
        if not reference or reference.startswith("REF**"):
            continue
        rows.append({
            "refdes": reference,
            "value": properties.get("Value", ""),
            "footprint": properties.get("Footprint", ""),
            "mpn": _first_property(
                properties, "MPN", "Manufacturer Part Number", "Mfr_PN",
            ),
            "lcsc": _first_property(
                properties, "LCSC", "LCSC Part #", "JLCPCB Part #",
            ),
            "manufacturer": _first_property(
                properties, "Manufacturer", "Mfr",
            ),
            "dnp": _is_dnp(block, properties),
        })
    return rows


_CANONICAL_TO_KICAD_COL = {
    "refdes": "Reference",
    "value": "Value",
    "footprint": "Footprint",
    "mpn": "MPN",
    "lcsc": "LCSC",
    "manufacturer": "Manufacturer",
}


# ── pcb_export_bom: typed result models ────────────────────────────────────


class PcbExportBomError(BaseModel):
    """`pcb_export_bom` failure variant — carries just an error message."""

    model_config = ConfigDict(extra="forbid")

    ok: Literal[False] = False
    error: str


class PcbExportBomOk(BaseModel):
    """`pcb_export_bom` success variant — full per-component BOM."""

    model_config = ConfigDict(extra="forbid")

    ok: Literal[True] = True
    path: str = Field(description="Absolute path of the parsed `.kicad_pcb`.")
    total_components: int = Field(
        description="Footprints found in the file (before DNP filter).",
    )
    dnp_count: int = Field(description="Number of footprints flagged DNP.")
    included_count: int = Field(
        description=(
            "Rows in `rows` after DNP filter and (when `vendor` is set) "
            "after column-map filtering."
        ),
    )
    vendor: Optional[str] = Field(
        default=None,
        description="Vendor slug if vendor-mapping was applied; else None.",
    )
    rows: list[dict[str, Any]] = Field(
        description=(
            "BOM rows. Without `vendor`: canonical snake_case keys "
            "(`refdes`, `value`, `footprint`, `mpn`, `lcsc`, "
            "`manufacturer`, `dnp`). With `vendor`: keys renamed via the "
            "seed's `columns.bom` map; only columns the vendor expects."
        ),
    )


PcbExportBomResult = Annotated[
    Union[PcbExportBomOk, PcbExportBomError],
    Field(discriminator="ok"),
]


@mcp.tool()
def pcb_export_bom(
    kicad_pcb: str,
    vendor: Optional[str] = None,
    include_dnp: bool = False,
) -> dict:
    """[read] Extract a per-component BOM from a `.kicad_pcb` file.

    Closes the gap left by `pcb_export_fabrication` (which emits
    gerbers/drill/POS but no BOM). Reads each footprint's properties —
    `Reference`, `Value`, `Footprint`, `MPN`, `LCSC`, `Manufacturer`, and
    DNP attribute — and returns one row per refdes.

    Args:
        kicad_pcb:    path to the `.kicad_pcb` file.
        vendor:       optional slug. When set, row keys are renamed via
            the vendor seed's `columns.bom` map and rows are limited to
            the columns the vendor expects. DNP rows are dropped unless
            `include_dnp=True`. Without `vendor`, rows use canonical
            snake_case keys (`refdes`, `value`, `mpn`, …).
        include_dnp:  include rows marked do-not-place. Default False.

    Returns:
        Discriminated by `ok`. On success: `PcbExportBomOk(path,
        total_components, dnp_count, included_count, vendor, rows)`. On
        failure: `PcbExportBomError(error)`. Result is dumped to dict
        for the MCP wire; the Pydantic models document the shape.
    """
    pcb_path = Path(kicad_pcb).resolve()
    if not pcb_path.exists():
        return PcbExportBomError(error=f"not found: {pcb_path}").model_dump()
    pcb_text = pcb_path.read_text()
    if not pcb_text.lstrip().startswith(_KICAD_PCB_HEADER):
        return PcbExportBomError(
            error=(
                f"{pcb_path} doesn't look like a .kicad_pcb file "
                f"(missing '(kicad_pcb' header). Got first 60 chars: "
                f"{pcb_text[:60]!r}"
            ),
        ).model_dump()

    all_footprints = _extract_kicad_pcb_footprints(pcb_text)
    # Stable, human-friendly order: R1, R2, R10, U1, U2…
    all_footprints.sort(key=lambda row: _refdes_sort_key(row["refdes"]))
    total = len(all_footprints)
    dnp_count = sum(1 for f in all_footprints if f["dnp"])

    if include_dnp:
        kept = all_footprints
    else:
        kept = [f for f in all_footprints if not f["dnp"]]

    if vendor:
        try:
            seed = load_vendor_seed(vendor)
        except FileNotFoundError as exc:
            return PcbExportBomError(error=str(exc)).model_dump()
        column_map = seed.columns.bom
        if column_map is None:
            return PcbExportBomError(
                error=(
                    f"vendor '{vendor}' does not consume a BOM "
                    f"(bare-board only, e.g. OSH Park)."
                ),
            ).model_dump()
        # Map canonical keys → KiCad column name → vendor column name.
        # Drop fields the vendor's column map doesn't reference.
        vendor_rows: list[dict] = []
        for row in kept:
            vendor_row: dict = {}
            for canonical, kicad_col in _CANONICAL_TO_KICAD_COL.items():
                if kicad_col in column_map:
                    vendor_row[column_map[kicad_col]] = row.get(canonical, "")
            vendor_rows.append(vendor_row)
        rows = vendor_rows
    else:
        rows = kept

    return PcbExportBomOk(
        path=str(pcb_path),
        total_components=total,
        dnp_count=dnp_count,
        included_count=len(rows),
        vendor=vendor,
        rows=rows,
    ).model_dump()


# ─── Part profiles (PyTorch-style persistent builder) ────────────────────────
#
# Each MPN gets one `<mpn>.profile.json` at HW_AGENT_PARTS_DIR (default
# ~/.hw-agent/parts/). Tools below are stateless: each call loads, mutates,
# saves. The agent calls these in any order, across sessions — disk is the
# source of truth.
#
# Typical flow per candidate:
#   1. part_init(mpn, category="buck_converter")
#   2. parallel: pcbparts.jlc_get_part, pcbparts.digikey_get_part
#   3. part_add_jlc(mpn, raw=<jlc response>)
#   4. part_add_digikey(mpn, raw=<dk response>)
#   5. part_status(mpn) → inspect `missing` list
#   6. if missing: subagent reads datasheet via VLM → part_add_datasheet(mpn, fills={...})
#   7. part_validate(mpn, requirements={"vin": 7.4, ...}) → check verdicts
#
# Merge precedence: JLC > DigiKey > Mouser > Datasheet. JLC's commercial
# naming (package, stock_jlc, LCSC) is canonical for turnkey assembly;
# DigiKey fills technical gaps JLC doesn't surface; Mouser is parameters-thin
# (lifecycle xcheck only); datasheet VLM fills rdson, theta_ja, iq, ilim, tsd.


from hw_agent.templates.part import ACTUALS_REGISTRY, Part


@mcp.tool()
def part_init(mpn: str, category: str) -> dict:
    """Create an empty profile for an MPN. Idempotent — returns existing
    profile if one already exists. Use `part_delete` first to start fresh.

    Args:
      mpn:      Manufacturer part number, e.g. "RT6228AGQUF"
      category: One of: """ + ", ".join(sorted(ACTUALS_REGISTRY)) + "."
    if Part.exists(mpn):
        return Part.load(mpn).to_dict()
    p = Part(mpn, category)
    p.save()
    return p.to_dict()


@mcp.tool()
def part_add_jlc(mpn: str, raw: dict) -> dict:
    """Apply a JLC `jlc_get_part` response to the part profile.

    Pulls vin range, iout_max, fsw, package, stock from raw['specs'] +
    commercial metadata (LCSC, manufacturer, price, datasheet_url) into
    `commercial`. Returns updated profile.
    """
    p = Part.load(mpn) if Part.exists(mpn) else None
    if p is None:
        raise ValueError(f"No profile for {mpn}. Call part_init first.")
    p.add_jlc(raw).save()
    return p.to_dict()


@mcp.tool()
def part_add_digikey(mpn: str, raw: dict) -> dict:
    """Apply a DigiKey `digikey_get_part` response to the part profile.

    Accepts the wrapped `{results: [{...}]}` shape returned by pcbparts MCP.
    Fills technical fields not provided by JLC + commercial metadata
    (lifecycle, stock_dk, datasheet_url_dk).
    """
    p = Part.load(mpn) if Part.exists(mpn) else None
    if p is None:
        raise ValueError(f"No profile for {mpn}. Call part_init first.")
    p.add_digikey(raw).save()
    return p.to_dict()


@mcp.tool()
def part_add_mouser(mpn: str, raw: dict) -> dict:
    """Apply a Mouser `mouser_get_part` response to the part profile.

    Mouser parameters are thin — use this mainly as a lifecycle cross-check.
    Most spec fields stay unfilled; rely on JLC + DigiKey + datasheet for
    technical data.
    """
    p = Part.load(mpn) if Part.exists(mpn) else None
    if p is None:
        raise ValueError(f"No profile for {mpn}. Call part_init first.")
    p.add_mouser(raw).save()
    return p.to_dict()


@mcp.tool()
def part_add_datasheet(mpn: str, fills: dict) -> dict:
    """Apply VLM-extracted datasheet values to the part profile.

    `fills` should be a flat dict keyed by canonical field names (e.g.
    `{"rdson_mohm": 30.0, "theta_ja": 40.0, "iq": 50.0}`). Only None
    fields in actuals are filled — existing values are not overwritten.
    Unknown keys are ignored. Use `part_status` first to read the `missing`
    list and the datasheet URL from `commercial.datasheet_url`.
    """
    p = Part.load(mpn) if Part.exists(mpn) else None
    if p is None:
        raise ValueError(f"No profile for {mpn}. Call part_init first.")
    p.add_datasheet(fills).save()
    return p.to_dict()


@mcp.tool()
def part_status(mpn: str) -> dict:
    """Return the current profile for an MPN, including `missing` list."""
    if not Part.exists(mpn):
        raise FileNotFoundError(f"No profile for {mpn}. Call part_init first.")
    return Part.load(mpn).to_dict()


@mcp.tool()
def part_validate(mpn: str, requirements: dict) -> dict:
    """Run category-specific checks against the part's actuals.

    `requirements` is a flat dict matching the category's Requirements model
    (e.g. for buck_converter: vin, vout, iout_max, fsw_khz, ripple_pct, ...).
    Returns {actuals, missing, checks: [...], verdict: "READY"|"BLOCKED"}.
    """
    if not Part.exists(mpn):
        raise FileNotFoundError(f"No profile for {mpn}. Call part_init first.")
    p = Part.load(mpn)
    checks = p.validate(requirements)
    hard_fail = any(c["status"] == "fail" and c["severity"] == "hard" for c in checks)
    has_missing = any(c["missing_specs"] for c in checks)
    verdict = "BLOCKED" if (hard_fail or has_missing) else "READY"
    return {
        "mpn": p.mpn,
        "category": p.category,
        "actuals": p.actuals.model_dump(),
        "missing": p.missing(),
        "checks": checks,
        "verdict": verdict,
    }


@mcp.tool()
def part_delete(mpn: str) -> dict:
    """Remove a part profile from disk. Returns {removed: bool}."""
    return {"mpn": mpn, "removed": Part.delete(mpn)}


@mcp.tool()
def part_list() -> dict:
    """List all stored part profiles with their categories and status."""
    from hw_agent.templates.part import parts_dir
    root = parts_dir()
    if not root.exists():
        return {"parts": [], "count": 0, "root": str(root)}
    items = []
    for f in sorted(root.glob("*.profile.json")):
        try:
            p = Part.load(f.stem.replace(".profile", ""))
            items.append({
                "mpn": p.mpn,
                "category": p.category,
                "sources": p.sources,
                "missing": p.missing(),
                "updated_at": p.updated_at,
            })
        except Exception as e:
            items.append({"path": str(f), "error": str(e)})
    return {"parts": items, "count": len(items), "root": str(root)}


# ─── Entry Point ─────────────────────────────────────────────────────────────

def main() -> None:
    """Console-script entrypoint. Invoked by orchestrators via `designer-mcp`."""
    mcp.run()


if __name__ == "__main__":
    main()
