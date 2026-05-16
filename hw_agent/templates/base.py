"""Base template for component research (legacy — used by orchestrator.py).

A template encodes everything the agent needs to investigate a class of part:
- What to ASK the engineer (`requirements`)
- What can be DERIVED from initial answers (`derived`)
- What datasheet SPECS to extract (`specs`)
- Where to LOOK in the datasheet (`page_hints`)
- What to SEARCH for on JLCPCB (`searches`)
- What to CALCULATE from the extracted specs (`calculations`)
- What CHECKS to run when verifying a candidate (`checks`)
- Free-form guidance for the agent (`ai_instructions`)

Note: `SpecDefinition` and `SearchCriteria` here are also used by the new
Pydantic-based `ElectronicSubsystem` classes in `hw_agent/subsystem.py`. The
`ComponentTemplate` dataclass is legacy — only `orchestrator.py` still uses it.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from hw_agent.checks import Check


@dataclass
class SpecDefinition:
    """A spec the template wants extracted from the datasheet."""
    name: str                    # Human name: "Dropout Voltage"
    key: str                     # Machine key: "vdrop"
    unit: str                    # Expected unit: "mV"
    required: bool = True        # Must find this or flag it
    aliases: list[str] = field(default_factory=list)  # Alt names in datasheets
    regex_hints: list[str] = field(default_factory=list)  # Regex patterns to try


@dataclass
class Requirement:
    """A requirement the agent should gather from the engineer.

    Replaces questionnaire-question shape. The agent iterates `template.requirements`,
    asks each prompt (using casual_prompt or prompt depending on context), validates
    answers against `rules`, and assembles a `required` dict that downstream checks
    consume.
    """
    key: str                                            # "vin"
    unit: str                                           # "V"
    prompt: str                                         # Technical phrasing
    casual_prompt: str = ""                             # Casual phrasing (fallback to prompt)
    examples: list[str] = field(default_factory=list)   # ["7.4V (2S LiPo)", "12V (car)"]
    required: bool = True
    default: Any = None                                 # Fallback if engineer skips
    why: str = ""                                       # Explanation of why this matters
    rules: list[dict] = field(default_factory=list)     # Validation: [{type:"range",min:1,max:40}]
    infer_from: dict[str, dict] = field(default_factory=dict)
    """Natural-language inference: {"battery 2s": {"value": 7.4, "also_set": {"vin_max": 8.4}}}"""


@dataclass
class SearchCriteria:
    """What to search for on JLCPCB."""
    query: str
    subcategory: str = ""
    spec_filters: list[dict] = field(default_factory=list)
    packages: list[str] = field(default_factory=list)
    min_stock: int = 100
    sort_by: str = "stock"


@dataclass
class ComponentTemplate:
    """Component research template — everything the agent needs to investigate this class of part."""
    name: str                              # "LDO Voltage Regulator"
    category: str                          # "ldo"
    description: str                       # What this component does
    specs: list[SpecDefinition]            # Datasheet specs to extract
    page_hints: dict[str, list[int]]       # Section → likely page ranges
    searches: list[SearchCriteria]         # JLCPCB searches
    design_rule_topics: list[str]          # get_design_rules() topics

    # ── Replaces questionnaire ──────────────────────────────────────────
    requirements: list[Requirement] = field(default_factory=list)
    """Inputs the agent gathers from the engineer before searching/verifying."""

    derived: list[Callable[[dict], dict]] = field(default_factory=list)
    """Functions that take answers dict, return additional/refined keys.
    Run in order. Each returns a dict to merge over the running answers."""

    ai_instructions: str = ""
    """Free-form guidance for the agent: rules of thumb, common gotchas, sensible defaults."""

    # ── Pipeline ────────────────────────────────────────────────────────
    checks: list[Check] = field(default_factory=list)
    """Verification pipeline. Each check is `(actual, required) -> CheckResult`."""

    calculations: list[Callable] = field(default_factory=list)
    """Calculation functions imported from `hw_agent.calculations.<category>`. Run after spec extraction."""

    def get_vlm_prompt(self, section: str) -> str:
        """Generate a structured VLM prompt for extracting specs from a page image.

        The prompt tells the VLM exactly what to look for and how to format output.
        """
        relevant_specs = [s for s in self.specs]

        spec_list = "\n".join(
            f'  - "{s.key}": {s.name} (unit: {s.unit})'
            + (f' — also called: {", ".join(s.aliases)}' if s.aliases else "")
            for s in relevant_specs
        )

        return f"""Extract component specifications from this datasheet page.

Return a JSON object with these fields. Use null for values not found on this page.
For each spec, provide min/typ/max where available.

Specs to extract:
{spec_list}

Output format:
{{
  "specs": {{
    "<key>": {{
      "min": <number or null>,
      "typ": <number or null>,
      "max": <number or null>,
      "unit": "<unit>",
      "conditions": "<test conditions if stated>"
    }},
    ...
  }},
  "section": "<what section of the datasheet this is>",
  "notes": ["<any warnings, footnotes, or important context>"]
}}

Rules:
- Only extract values you can actually see on this page
- Include test conditions when stated (e.g., "VIN=5V, IOUT=100mA")
- Use standard units (mV not V for small voltages, µA not mA for small currents)
- If a value appears as "—" or "–" it means not specified, use null
"""

    def get_regex_patterns(self) -> dict[str, list[str]]:
        """Get regex patterns for each spec, for fast text-based extraction."""
        patterns = {}
        for spec in self.specs:
            spec_patterns = list(spec.regex_hints)
            # Auto-generate patterns from name and aliases
            names = [spec.name] + spec.aliases
            for name in names:
                # "Dropout Voltage" → r"dropout\s+voltage\s+.*?([\d\.]+)\s*mV"
                escaped = r"\s+".join(name.lower().split())
                spec_patterns.append(
                    rf"{escaped}\s+.*?([\d\.]+)\s*{spec.unit}"
                )
            patterns[spec.key] = spec_patterns
        return patterns

    def get_research_plan(self) -> list[dict]:
        """Return a step-by-step research plan for this component.

        Each step can be run as a parallel sub-agent task.
        """
        steps = []

        # Step 1: Extract specs from datasheet
        steps.append({
            "id": "extract_specs",
            "name": "Extract datasheet specs",
            "type": "vlm_extraction",
            "parallel_group": "A",
            "description": f"Extract {len(self.specs)} specs from datasheet",
            "specs": [s.key for s in self.specs],
        })

        # Step 2: Search JLCPCB (can run in parallel with extraction)
        for i, search in enumerate(self.searches):
            steps.append({
                "id": f"jlcpcb_search_{i}",
                "name": f"JLCPCB search: {search.query}",
                "type": "parts_search",
                "parallel_group": "A",  # same group = parallel with extraction
                "search": {
                    "query": search.query,
                    "subcategory": search.subcategory,
                    "spec_filters": search.spec_filters,
                    "packages": search.packages,
                },
            })

        # Step 3: Design rules (parallel)
        for topic in self.design_rule_topics:
            steps.append({
                "id": f"design_rules_{topic}",
                "name": f"Design rules: {topic}",
                "type": "design_rules",
                "parallel_group": "A",
                "topic": topic,
            })

        # Step 4: Calculations (needs extracted specs first)
        for calc in self.calculations:
            doc_first_line = (calc.__doc__ or "").strip().split("\n")[0]
            steps.append({
                "id": f"calc_{calc.__name__}",
                "name": doc_first_line or calc.__name__,
                "type": "calculation",
                "parallel_group": "B",  # group B = after group A completes
                "fn": f"{calc.__module__}.{calc.__name__}",
            })

        # Step 5: Generate report (needs everything)
        steps.append({
            "id": "generate_report",
            "name": "Generate exploration report",
            "type": "report",
            "parallel_group": "C",
        })

        return steps
