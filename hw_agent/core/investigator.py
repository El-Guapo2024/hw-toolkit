"""Hardware investigator — code drives, AI is just one tool.

This is the main entry point. It runs the full research pipeline:
1. Loads template for the component type
2. Parses datasheet with regex (no AI)
3. Identifies what's missing
4. Generates structured prompts for VLM/AI to fill gaps
5. Runs JLCPCB searches (structured API calls)
6. Runs engineering calculations (pure code)
7. Generates markdown report (template-driven)

The AI never decides what to do. The code decides.
The AI only does what code can't: read a datasheet page image.

Usage:
    from hw_agent.core.investigator import investigate

    report = investigate(
        component_type="ldo",
        datasheet="data/datasheets/AP2112.pdf",
        params={"vin": 5.0, "vout": 3.3, "actual_load_ma": 220},
        output="docs/components/ldo/exploration.md",
    )

CLI:
    python -m hw_agent.core.investigator ldo \\
        --datasheet data/datasheets/AP2112.pdf \\
        --param vin=5.0 --param vout=3.3 --param actual_load_ma=220 \\
        --output docs/components/ldo/exploration.md
"""

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hw_agent.artifacts.datasheets.parser import DatasheetParser
from hw_agent.domain.templates import SUBSYSTEM_REGISTRY


# ─── Backwards-compat shim ────────────────────────────────────────────────
# `TEMPLATES` is preserved as a name for now — points at SUBSYSTEM_REGISTRY so
# external readers don't break. New code should import SUBSYSTEM_REGISTRY directly.

TEMPLATES = SUBSYSTEM_REGISTRY


def _load_templates():
    """No-op — registry is populated at import time."""
    return


# ─── Investigation Result ────────────────────────────────────────────────────

@dataclass
class InvestigationResult:
    """Everything the investigation produced."""
    component_type: str
    params: dict
    specs: dict = field(default_factory=dict)
    specs_missing: list[str] = field(default_factory=list)
    vlm_prompts: list[dict] = field(default_factory=list)  # prompts for AI to fill gaps
    calculations: dict = field(default_factory=dict)
    search_queries: list[dict] = field(default_factory=list)  # JLCPCB queries to run
    report_md: str = ""
    flags: dict = field(default_factory=dict)
    duration_ms: float = 0


# ─── Core Investigation Pipeline ─────────────────────────────────────────────

def investigate(
    component_type: str,
    datasheet: str | Path | None = None,
    params: dict | None = None,
    specs_override: dict | None = None,
    output: str | Path | None = None,
) -> InvestigationResult:
    """Run a full component investigation.

    This function is deterministic except for the VLM gap-fill step.
    It returns VLM prompts for any specs it couldn't extract via regex,
    so the caller can dispatch them to whatever AI is available.

    Args:
        component_type: Template name ("ldo", "buck", "can", etc.)
        datasheet: Path to component PDF datasheet.
        params: Design parameters (vin, vout, load, etc.)
        specs_override: Pre-known specs (skip extraction for these).
        output: Path to write markdown report.

    Returns:
        InvestigationResult with specs, calculations, report, and
        VLM prompts for any missing specs.
    """
    start = time.time()

    template_cls = SUBSYSTEM_REGISTRY.get(component_type)
    if not template_cls:
        available = ", ".join(SUBSYSTEM_REGISTRY.keys())
        raise ValueError(f"Unknown component type: {component_type}. Available: {available}")

    params = params or {}
    result = InvestigationResult(component_type=component_type, params=params)

    # ── Phase 1: Extract specs from datasheet (regex, no AI) ──

    if datasheet:
        datasheet = Path(datasheet)
        if datasheet.exists():
            parser = DatasheetParser(datasheet)
            info = parser.parse(ocr=True)

            # Match parser's extracted specs to the subsystem class's extract_specs
            import re
            for template_spec in template_cls.extract_specs:
                # Try regex patterns
                for pat in template_spec.regex_hints:
                    for page_num, text in info.text_by_page.items():
                        for m in re.finditer(pat, text, re.IGNORECASE):
                            if template_spec.key not in result.specs:
                                groups = m.groups()
                                if len(groups) >= 2:
                                    result.specs[template_spec.key] = {
                                        "min": _to_num(groups[0]),
                                        "max": _to_num(groups[1]),
                                        "unit": template_spec.unit,
                                    }
                                elif len(groups) == 1:
                                    result.specs[template_spec.key] = {
                                        "typ": _to_num(groups[0]),
                                        "unit": template_spec.unit,
                                    }

                # Try matching parser's own specs by alias
                if template_spec.key not in result.specs:
                    for parsed_spec in info.specs:
                        name_lower = parsed_spec.name.lower()
                        all_names = [template_spec.name.lower()] + [a.lower() for a in template_spec.aliases]
                        if any(alias in name_lower for alias in all_names):
                            result.specs[template_spec.key] = {
                                "min": _to_num(parsed_spec.min),
                                "typ": _to_num(parsed_spec.typ),
                                "max": _to_num(parsed_spec.max),
                                "unit": parsed_spec.unit,
                                "conditions": parsed_spec.conditions,
                            }
                            break

    # Merge overrides
    if specs_override:
        result.specs.update(specs_override)

    # ── Phase 2: Identify gaps → generate VLM prompts ──

    for spec in template_cls.extract_specs:
        if spec.key not in result.specs:
            result.specs_missing.append(spec.key)

    if result.specs_missing and datasheet:
        # Generate targeted VLM prompts for missing specs
        missing_spec_defs = [s for s in template_cls.extract_specs if s.key in result.specs_missing]

        # Which pages to scan
        pages_to_check = set()
        for section, page_list in template_cls.page_hints.items():
            pages_to_check.update(page_list)

        spec_list = "\n".join(
            f'  - "{s.key}": {s.name} ({s.unit})'
            for s in missing_spec_defs
        )

        for page_num in sorted(pages_to_check):
            result.vlm_prompts.append({
                "page": page_num,
                "prompt": (
                    f"Extract these specs from this datasheet page as JSON.\n"
                    f"Return null for any not found on this page.\n\n"
                    f"Specs needed:\n{spec_list}\n\n"
                    f"Format: {{\"<key>\": {{\"min\": <num|null>, \"typ\": <num|null>, "
                    f"\"max\": <num|null>, \"unit\": \"<unit>\", \"conditions\": \"<if stated>\"}}}}"
                ),
            })

    # ── Phase 3: Generate JLCPCB search queries ──

    for search in template_cls.searches:
        query = search.query.format(**params) if params else search.query
        result.search_queries.append({
            "query": query,
            "subcategory": search.subcategory,
            "spec_filters": search.spec_filters,
            "packages": search.packages,
            "min_stock": search.min_stock,
            "sort_by": search.sort_by,
        })

    # ── Phase 4: Run calculations ──

    if result.specs:
        # Run all calculations the template registers — unified `(specs, required) -> dict` signature.
        if template_cls.calculations:
            calc_results: dict = {}
            for calc in template_cls.calculations:
                calc_results.update(calc(result.specs, params))
            result.calculations = calc_results
            result.flags = calc_results.get("flags", {})

    # ── Phase 5: Generate report ──

    result.report_md = _generate_report(template_cls, result)

    # ── Write output ──

    if output:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(result.report_md)

    result.duration_ms = (time.time() - start) * 1000
    return result


# ─── Report Generator ────────────────────────────────────────────────────────

def _generate_report(template_cls, result: InvestigationResult) -> str:
    lines = []
    lines.append(f"# {template_cls.description} Exploration")
    lines.append("")
    lines.append(f"*Generated by hw-agent investigator | Subsystem: {template_cls.category}*")
    lines.append("")

    # Params
    if result.params:
        lines.append("## Design Parameters")
        for k, v in result.params.items():
            lines.append(f"- **{k}**: {v}")
        lines.append("")

    # Specs table
    lines.append("## Extracted Specifications")
    lines.append("")
    lines.append("| Parameter | Min | Typ | Max | Unit | Conditions | Source |")
    lines.append("|-----------|-----|-----|-----|------|------------|--------|")
    for spec_def in template_cls.extract_specs:
        s = result.specs.get(spec_def.key)
        if s:
            lines.append(
                f"| {spec_def.name} "
                f"| {s.get('min', '—') or '—'} "
                f"| {s.get('typ', '—') or '—'} "
                f"| {s.get('max', '—') or '—'} "
                f"| {s.get('unit', spec_def.unit)} "
                f"| {s.get('conditions', '') or ''} "
                f"| {'regex' if spec_def.key not in result.specs_missing else 'vlm'} |"
            )
        else:
            status = "**MISSING**" if spec_def.required else "—"
            lines.append(
                f"| {spec_def.name} | — | — | — | {spec_def.unit} | | {status} |"
            )
    lines.append("")

    # Missing specs warning
    if result.specs_missing:
        lines.append("## Missing Specs")
        lines.append("")
        lines.append("These specs could not be extracted via regex. VLM prompts generated:")
        for key in result.specs_missing:
            spec_def = next((s for s in template_cls.extract_specs if s.key == key), None)
            req = " (required)" if spec_def and spec_def.required else ""
            lines.append(f"- **{key}**{req}")
        lines.append(f"- VLM prompts: {len(result.vlm_prompts)} pages to process")
        lines.append("")

    # JLCPCB search queries
    if result.search_queries:
        lines.append("## JLCPCB Search Queries")
        lines.append("")
        lines.append("Run these to find candidates:")
        lines.append("```python")
        for i, sq in enumerate(result.search_queries):
            lines.append(f"# Search {i+1}")
            lines.append(f"jlc_search(")
            lines.append(f'    query="{sq["query"]}",')
            if sq["subcategory"]:
                lines.append(f'    subcategory_name="{sq["subcategory"]}",')
            if sq["packages"]:
                lines.append(f'    packages={sq["packages"]},')
            lines.append(f'    sort_by="{sq["sort_by"]}",')
            lines.append(f")")
            lines.append("")
        lines.append("```")
        lines.append("")

    # Calculations
    if result.calculations:
        lines.append("## Engineering Analysis")
        lines.append("")

        pdiss = result.calculations.get("power_dissipation", {})
        if pdiss:
            lines.append("### Power Dissipation")
            vin = result.params.get("vin", "?")
            vout = result.params.get("vout", "?")
            load = result.params.get("actual_load_ma", "?")
            lines.append(f"- At actual load ({load}mA): P = ({vin}V - {vout}V) × {load}mA = **{pdiss.get('at_actual_load_w', '?')}W**")
            lines.append(f"- At max rated load: **{pdiss.get('at_max_load_w', '?')}W**")
            lines.append("")

        tj = result.calculations.get("junction_temperature", {})
        if tj:
            theta = result.specs.get("theta_ja", {}).get("typ", "?")
            lines.append("### Thermal Analysis")
            lines.append(f"- θJA: {theta} °C/W")
            lines.append(f"- Tj at 85°C ambient: **{tj.get('at_85c_ambient', '?')}°C**")
            lines.append(f"- Tj at 70°C ambient: **{tj.get('at_70c_ambient', '?')}°C**")
            lines.append(f"- Thermal shutdown: {tj.get('thermal_shutdown', '?')}°C")
            lines.append("")

        margins = result.calculations.get("margins", {})
        if margins:
            lines.append("### Margins")
            lines.append(f"- Dropout headroom: **{margins.get('dropout_headroom_v', '?')}V**")
            lines.append(f"- Current margin: **{margins.get('current_margin_x', '?')}×**")
            lines.append(f"- Thermal margin at 85°C: **{margins.get('thermal_margin_85c', '?')}°C**")
            lines.append("")

        # Pass/fail
        flags = result.flags
        if flags:
            lines.append("### Pass/Fail")
            lines.append("")
            for flag, passed in flags.items():
                icon = "PASS" if passed else "**FAIL**"
                lines.append(f"- {flag}: {icon}")
            lines.append("")

        # Warnings
        notes = result.calculations.get("notes", [])
        if notes:
            lines.append("### Warnings")
            lines.append("")
            for note in notes:
                lines.append(f"- {note}")
            lines.append("")

    # Footer
    lines.append("---")
    lines.append(f"*Duration: {result.duration_ms:.0f}ms | "
                 f"Specs: {len(result.specs)}/{len(template_cls.extract_specs)} extracted | "
                 f"Missing: {len(result.specs_missing)}*")

    return "\n".join(lines)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _to_num(val: str | None) -> float | str | None:
    """Try to convert to number, return as-is if not possible."""
    if val is None or val == "" or val in ("—", "–", "-"):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return val


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    """CLI entry point: python -m hw_agent.core.investigator <type> [options]"""
    import argparse

    _load_templates()

    parser = argparse.ArgumentParser(
        description="Hardware component investigator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Available templates: {', '.join(TEMPLATES.keys())}",
    )
    parser.add_argument("type", help="Component type (ldo, buck, can, etc.)")
    parser.add_argument("--datasheet", "-d", help="Path to PDF datasheet")
    parser.add_argument("--param", "-p", action="append", default=[],
                        help="Design param as key=value (e.g., --param vin=5.0)")
    parser.add_argument("--output", "-o", help="Output markdown path")
    parser.add_argument("--json", action="store_true", help="Output specs as JSON")

    args = parser.parse_args()

    # Parse params
    params = {}
    for p in args.param:
        key, val = p.split("=", 1)
        try:
            params[key] = float(val)
        except ValueError:
            params[key] = val

    # Run investigation
    result = investigate(
        component_type=args.type,
        datasheet=args.datasheet,
        params=params,
        output=args.output,
    )

    if args.json:
        print(json.dumps({
            "specs": result.specs,
            "missing": result.specs_missing,
            "calculations": result.calculations,
            "flags": result.flags,
        }, indent=2))
    else:
        print(result.report_md)

    # Print summary to stderr
    print(f"\n--- {result.duration_ms:.0f}ms | "
          f"{len(result.specs)}/{len(TEMPLATES[args.type].specs)} specs | "
          f"{len(result.specs_missing)} missing | "
          f"{len(result.vlm_prompts)} VLM prompts pending",
          file=sys.stderr)


if __name__ == "__main__":
    main()
