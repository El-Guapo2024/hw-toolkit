"""Research orchestrator — dispatches typed tasks, collects results, produces reports.

The orchestrator is the spine. It:
1. Reads a research plan from a component template
2. Groups tasks by parallel group (A runs together, B waits for A, etc.)
3. Dispatches each task to the right worker
4. Collects typed results into a ResearchContext
5. Runs assertions to validate results
6. Generates reproducible markdown output

The AI is just one worker among many. Most work is deterministic code.

Usage:
    from hw_agent.core.orchestrator import Orchestrator
    from hw_agent.domain.templates.ldo import LDO_TEMPLATE

    orch = Orchestrator()
    result = orch.run(
        template=LDO_TEMPLATE,
        datasheet_path="data/datasheets/AP2112.pdf",
        params={"vin": 5.0, "vout": 3.3, "actual_load_ma": 220},
    )

    print(result.report_md)     # Full markdown exploration doc
    print(result.flags)         # Pass/fail assertions
    print(result.cached_specs)  # What got cached for next time
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from hw_agent.cache.store import AnalysisCache
from hw_agent.artifacts.datasheets.parser import DatasheetParser
from hw_agent.domain.templates.base import ComponentTemplate


@dataclass
class TaskResult:
    """Result from a single worker task."""
    task_id: str
    success: bool
    data: Any = None
    error: str = ""
    source: str = ""        # "regex", "vlm", "cache", "api", "calculator"
    duration_ms: float = 0


@dataclass
class ResearchContext:
    """Accumulates results across all tasks."""
    template: ComponentTemplate
    params: dict
    specs: dict = field(default_factory=dict)
    search_results: list[dict] = field(default_factory=list)
    design_rules: list[dict] = field(default_factory=list)
    calculations: dict = field(default_factory=dict)
    task_results: list[TaskResult] = field(default_factory=list)
    flags: dict = field(default_factory=dict)
    report_md: str = ""
    total_duration_ms: float = 0

    def merge_specs(self, new_specs: dict, source: str):
        """Merge extracted specs, preferring more complete entries."""
        for key, value in new_specs.items():
            if key not in self.specs:
                self.specs[key] = value
            elif isinstance(value, dict) and isinstance(self.specs[key], dict):
                # Merge, filling in missing min/typ/max
                for field_name in ("min", "typ", "max", "unit", "conditions"):
                    if value.get(field_name) and not self.specs[key].get(field_name):
                        self.specs[key][field_name] = value[field_name]


# ─── Workers ─────────────────────────────────────────────────────────────────

def worker_regex_extract(
    template: ComponentTemplate,
    parser: DatasheetParser,
    cache: AnalysisCache,
) -> TaskResult:
    """Extract specs using regex patterns from template. No AI needed."""
    import re
    start = time.time()

    # Check cache first
    cache_key = parser.pdf_path.stem
    cached = cache.get("datasheet_specs", cache_key, f"regex_{template.category}")
    if cached:
        return TaskResult(
            task_id="extract_specs_regex",
            success=True,
            data=cached["result"],
            source="cache",
            duration_ms=(time.time() - start) * 1000,
        )

    # Parse PDF
    if not parser._parsed:
        parser.parse(ocr=True)

    specs = {}
    patterns = template.get_regex_patterns()

    for spec_key, pats in patterns.items():
        for pat in pats:
            for page_num, text in parser.info.text_by_page.items():
                for m in re.finditer(pat, text, re.IGNORECASE):
                    if spec_key not in specs:
                        groups = m.groups()
                        if len(groups) >= 2:
                            specs[spec_key] = {
                                "min": groups[0], "max": groups[1],
                                "unit": next((s.unit for s in template.specs if s.key == spec_key), ""),
                            }
                        elif len(groups) == 1:
                            specs[spec_key] = {
                                "typ": groups[0],
                                "unit": next((s.unit for s in template.specs if s.key == spec_key), ""),
                            }

    # Also use the parser's own extracted specs
    for spec in parser.info.specs:
        for template_spec in template.specs:
            name_lower = spec.name.lower()
            matches = any(
                alias.lower() in name_lower
                for alias in [template_spec.name] + template_spec.aliases
            )
            if matches and template_spec.key not in specs:
                specs[template_spec.key] = {
                    "min": spec.min or None,
                    "typ": spec.typ or None,
                    "max": spec.max or None,
                    "unit": spec.unit,
                    "conditions": spec.conditions,
                }

    # Cache results
    cache.put("datasheet_specs", cache_key, f"regex_{template.category}", specs, "regex")

    return TaskResult(
        task_id="extract_specs_regex",
        success=True,
        data=specs,
        source="regex",
        duration_ms=(time.time() - start) * 1000,
    )


def worker_vlm_extract(
    template: ComponentTemplate,
    page_images: list[tuple[int, bytes]],
    vlm_call: Callable | None = None,
) -> TaskResult:
    """Extract specs from page images using VLM (Claude vision, Qwen, etc.).

    Args:
        template: Component template with VLM prompt.
        page_images: List of (page_num, png_bytes) tuples.
        vlm_call: Function that takes (prompt, image_bytes) → JSON string.
                  If None, returns a placeholder (caller provides VLM).
    """
    start = time.time()

    if vlm_call is None:
        # Return the prompts so the caller can dispatch to their VLM
        prompts = []
        for page_num, _ in page_images:
            prompts.append({
                "page": page_num,
                "prompt": template.get_vlm_prompt("electrical_characteristics"),
            })
        return TaskResult(
            task_id="extract_specs_vlm",
            success=True,
            data={"needs_vlm": True, "prompts": prompts},
            source="vlm_pending",
            duration_ms=(time.time() - start) * 1000,
        )

    # Call VLM for each page
    all_specs = {}
    for page_num, img_bytes in page_images:
        prompt = template.get_vlm_prompt("electrical_characteristics")
        try:
            response = vlm_call(prompt, img_bytes)
            parsed = json.loads(response) if isinstance(response, str) else response
            if "specs" in parsed:
                all_specs.update(parsed["specs"])
        except (json.JSONDecodeError, Exception) as e:
            continue

    return TaskResult(
        task_id="extract_specs_vlm",
        success=bool(all_specs),
        data=all_specs,
        source="vlm",
        duration_ms=(time.time() - start) * 1000,
    )


def worker_calculation(
    template: ComponentTemplate,
    specs: dict,
    params: dict,
) -> TaskResult:
    """Run all template calculations on extracted specs."""
    start = time.time()

    # Run the template's calculations. Unified signature: `(specs, required) -> dict`.
    # Each calc picks what it needs from each dict; orchestrator stays generic.
    results = {}
    for calc in template.calculations:
        calc_result = calc(specs, params)
        results.update(calc_result)

    return TaskResult(
        task_id="calculations",
        success="error" not in results,
        data=results,
        source="calculator",
        duration_ms=(time.time() - start) * 1000,
    )


def worker_report(ctx: ResearchContext) -> TaskResult:
    """Generate markdown exploration report from all collected results."""
    start = time.time()

    lines = []
    lines.append(f"# {ctx.template.name} Exploration")
    lines.append("")
    lines.append(f"*Auto-generated by hw-agent | Template: {ctx.template.category}*")
    lines.append("")

    # Parameters
    lines.append("## Design Parameters")
    for k, v in ctx.params.items():
        lines.append(f"- **{k}**: {v}")
    lines.append("")

    # Extracted specs table
    lines.append("## Extracted Specifications")
    lines.append("")
    lines.append("| Parameter | Min | Typ | Max | Unit | Conditions |")
    lines.append("|-----------|-----|-----|-----|------|------------|")
    for spec_def in ctx.template.specs:
        s = ctx.specs.get(spec_def.key, {})
        if s:
            lines.append(
                f"| {spec_def.name} "
                f"| {s.get('min', '—')} "
                f"| {s.get('typ', '—')} "
                f"| {s.get('max', '—')} "
                f"| {s.get('unit', spec_def.unit)} "
                f"| {s.get('conditions', '')} |"
            )
        elif spec_def.required:
            lines.append(f"| **{spec_def.name}** | — | — | — | {spec_def.unit} | **NOT FOUND** |")
    lines.append("")

    # Calculations
    if ctx.calculations:
        lines.append("## Analysis Results")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(ctx.calculations, indent=2))
        lines.append("```")
        lines.append("")

        # Flags
        flags = ctx.calculations.get("flags", {})
        if flags:
            lines.append("## Pass/Fail Checks")
            lines.append("")
            for flag, passed in flags.items():
                icon = "PASS" if passed else "**FAIL**"
                lines.append(f"- {flag}: {icon}")
            lines.append("")

        # Notes/warnings
        notes = ctx.calculations.get("notes", [])
        if notes:
            lines.append("## Warnings")
            lines.append("")
            for note in notes:
                lines.append(f"- {note}")
            lines.append("")

    # Task summary
    lines.append("## Research Summary")
    lines.append("")
    lines.append(f"- Total tasks: {len(ctx.task_results)}")
    lines.append(f"- Total time: {ctx.total_duration_ms:.0f}ms")
    for tr in ctx.task_results:
        icon = "ok" if tr.success else "FAIL"
        lines.append(f"- [{icon}] {tr.task_id} ({tr.source}, {tr.duration_ms:.0f}ms)")
    lines.append("")

    report = "\n".join(lines)
    return TaskResult(
        task_id="generate_report",
        success=True,
        data=report,
        source="template",
        duration_ms=(time.time() - start) * 1000,
    )


# ─── Orchestrator ────────────────────────────────────────────────────────────

class Orchestrator:
    """Runs a component research pipeline end-to-end."""

    def __init__(self, cache: AnalysisCache | None = None):
        self.cache = cache or AnalysisCache()

    def run(
        self,
        template: ComponentTemplate,
        datasheet_path: str | Path | None = None,
        params: dict | None = None,
        specs_override: dict | None = None,
        vlm_call: Callable | None = None,
    ) -> ResearchContext:
        """Execute the full research pipeline.

        Args:
            template: Component template (e.g., LDO_TEMPLATE).
            datasheet_path: Path to component PDF datasheet.
            params: Design parameters (vin, vout, load, etc.).
            specs_override: Pre-extracted specs (skip extraction).
            vlm_call: VLM function (prompt, image_bytes) → JSON.

        Returns:
            ResearchContext with all results, report, and flags.
        """
        overall_start = time.time()
        ctx = ResearchContext(template=template, params=params or {})

        # ── Group A: Parallel extraction + search ──

        # A1: Regex extraction from datasheet
        if datasheet_path and not specs_override:
            parser = DatasheetParser(datasheet_path)
            regex_result = worker_regex_extract(template, parser, self.cache)
            ctx.task_results.append(regex_result)
            if regex_result.success:
                ctx.merge_specs(regex_result.data, regex_result.source)

        # A2: VLM extraction (if we have a VLM and missing specs)
        if datasheet_path and vlm_call:
            missing = [
                s for s in template.specs
                if s.required and s.key not in ctx.specs
            ]
            if missing:
                parser = DatasheetParser(datasheet_path)
                if not parser._parsed:
                    parser.parse()
                # Render relevant pages as images
                import fitz
                doc = fitz.open(str(datasheet_path))
                pages_to_scan = set()
                for section, page_list in template.page_hints.items():
                    pages_to_scan.update(page_list)

                page_images = []
                for p in sorted(pages_to_scan):
                    if p < len(doc):
                        mat = fitz.Matrix(300/72, 300/72)
                        pix = doc[p].get_pixmap(matrix=mat)
                        page_images.append((p, pix.tobytes("png")))
                doc.close()

                vlm_result = worker_vlm_extract(template, page_images, vlm_call)
                ctx.task_results.append(vlm_result)
                if vlm_result.success and isinstance(vlm_result.data, dict):
                    if not vlm_result.data.get("needs_vlm"):
                        ctx.merge_specs(vlm_result.data, "vlm")

        # Override with pre-supplied specs
        if specs_override:
            ctx.merge_specs(specs_override, "manual")

        # ── Group B: Calculations (need specs from A) ──

        if ctx.specs:
            calc_result = worker_calculation(template, ctx.specs, ctx.params)
            ctx.task_results.append(calc_result)
            if calc_result.success:
                ctx.calculations = calc_result.data
                ctx.flags = calc_result.data.get("flags", {})

        # ── Group C: Report generation ──

        report_result = worker_report(ctx)
        ctx.task_results.append(report_result)
        if report_result.success:
            ctx.report_md = report_result.data

        ctx.total_duration_ms = (time.time() - overall_start) * 1000

        # Cache the full analysis
        part_key = Path(datasheet_path).stem if datasheet_path else template.category
        self.cache.put(
            "full_analysis",
            part_key,
            json.dumps(params or {}, sort_keys=True),
            {
                "specs": ctx.specs,
                "calculations": ctx.calculations,
                "flags": ctx.flags,
            },
            source="orchestrator",
        )

        return ctx
