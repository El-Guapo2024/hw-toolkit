"""Parametric constraint engine — evaluates math expressions against the design state.

Each component has a constraints.yaml with expressions that reference:
  - specs.*          — the component's own specs
  - system.*         — project-level values (ambient_temp, budget, power_source)
  - rails['5V'].*    — power rail aggregates (total_typ_ma, total_peak_ma, capacity_ma)
  - pins.*           — pin pool state (total, used, remaining)
  - bom.*            — BOM aggregates (total_usd, item_count)

Constraints are evaluated on every component addition. A 10% proximity
warning fires before hard limits are hit.
"""

from __future__ import annotations

import math
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


WARN_THRESHOLD = 0.10  # warn when within 10% of limit


@dataclass
class CheckResult:
    name: str
    severity: str          # "hard" or "soft"
    expr: str
    value: float | None
    limit: str
    passed: bool
    margin_pct: float | None  # how close to limit (0 = at limit, negative = over)
    warning: bool          # within WARN_THRESHOLD of limit
    unit: str
    message: str


@dataclass
class ConstraintReport:
    component: str
    results: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.passed for r in self.results if r.severity == "hard")

    @property
    def warnings(self) -> list[CheckResult]:
        return [r for r in self.results if r.warning or (not r.passed and r.severity == "soft")]

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed and r.severity == "hard"]

    def summary(self) -> str:
        lines = [f"## Constraints: {self.component}"]
        for r in self.results:
            icon = "PASS" if r.passed else ("WARN" if r.severity == "soft" else "FAIL")
            if r.warning and r.passed:
                icon = "WARN"
            margin = f" ({r.margin_pct:+.0f}%)" if r.margin_pct is not None else ""
            val = f" = {r.value:.1f}{r.unit}" if r.value is not None else ""
            lines.append(f"  [{icon}] {r.name}{val}{margin}: {r.message}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "component": self.component,
            "ok": self.ok,
            "results": [
                {"name": r.name, "severity": r.severity, "passed": r.passed,
                 "value": r.value, "limit": r.limit, "margin_pct": r.margin_pct,
                 "warning": r.warning, "unit": r.unit, "message": r.message}
                for r in self.results
            ],
        }


def _parse_limit(limit_str: str) -> tuple[str, float]:
    """Parse '< 125' into ('<', 125.0)."""
    limit_str = limit_str.strip()
    for op in ["<=", ">=", "<", ">"]:
        if limit_str.startswith(op):
            return op, float(limit_str[len(op):].strip())
    return "<", float(limit_str)


def _check_limit(value: float, op: str, limit: float) -> bool:
    if op == "<": return value < limit
    if op == "<=": return value <= limit
    if op == ">": return value > limit
    if op == ">=": return value >= limit
    return False


def _margin_pct(value: float, op: str, limit: float) -> float:
    """How far from the limit, as a percentage. Positive = margin, negative = over."""
    if limit == 0:
        return 0.0
    if op in ("<", "<="):
        return ((limit - value) / limit) * 100
    else:  # > or >=
        return ((value - limit) / limit) * 100


def load_constraints(constraints_path: Path) -> dict:
    """Load a constraints.yaml file."""
    if not constraints_path.exists():
        return {}
    return yaml.safe_load(constraints_path.read_text()) or {}


def build_context(design: dict, component_constraints: dict) -> dict:
    """Build the evaluation context from design.yaml + component constraints.

    Returns a flat dict that expressions can reference:
        specs.vin_max, system.ambient_temp_c, rails['5V'].total_peak_ma, etc.
    """
    ctx = {"math": math}

    # Component specs
    specs = component_constraints.get("specs", {})
    ctx["specs"] = type("Specs", (), specs)()

    # System-level
    system_data = {
        "ambient_temp_c": design.get("ambient_temp_c", 40),
        "budget_usd": design.get("budget_usd", 50),
        "power_source": design.get("power_source", {}),
        "bom_total_usd": design.get("_bom_total_usd", 0),
    }
    ctx["system"] = type("System", (), system_data)()

    # Rails with computed totals
    rails_data = {}
    for rail_name, rail in design.get("rails", {}).items():
        loads = rail.get("loads", [])
        total_typ = sum(l.get("typ_ma", 0) for l in loads)
        total_peak = sum(l.get("peak_ma", 0) for l in loads)
        rails_data[rail_name] = type("Rail", (), {
            "capacity_ma": rail.get("capacity_ma", 0),
            "total_typ_ma": total_typ,
            "total_peak_ma": total_peak,
            "margin_ma": rail.get("capacity_ma", 0) - total_peak,
            "load_count": len(loads),
        })()
    ctx["rails"] = rails_data

    # Pin pool
    pin_data = design.get("pin_pool", {"total": 33, "used": 0})
    pin_data["remaining"] = pin_data["total"] - pin_data.get("used", 0)
    ctx["pins"] = type("Pins", (), pin_data)()

    return ctx


def evaluate_constraints(
    design: dict,
    component_constraints: dict,
    component_name: str = "",
) -> ConstraintReport:
    """Evaluate all constraints for a component against the current design state."""
    report = ConstraintReport(component=component_name or component_constraints.get("component", "unknown"))
    ctx = build_context(design, component_constraints)
    constraints = component_constraints.get("constraints", [])

    for c in constraints:
        name = c.get("name", "unnamed")
        expr_str = c.get("expr", "True")
        limit_str = c.get("limit", "")
        severity = c.get("severity", "hard")
        unit = c.get("unit", "")

        try:
            value = eval(expr_str, {"__builtins__": {}}, ctx)
        except Exception as e:
            report.results.append(CheckResult(
                name=name, severity=severity, expr=expr_str,
                value=None, limit=limit_str, passed=False,
                margin_pct=None, warning=False, unit=unit,
                message=f"Expression error: {e}",
            ))
            continue

        if limit_str:
            op, limit_val = _parse_limit(limit_str)
            passed = _check_limit(float(value), op, limit_val)
            margin = _margin_pct(float(value), op, limit_val)
            warning = passed and abs(margin) < (WARN_THRESHOLD * 100)

            if passed and not warning:
                msg = f"OK ({margin:+.1f}% margin)"
            elif passed and warning:
                msg = f"CLOSE — only {margin:.1f}% margin"
            else:
                msg = f"EXCEEDED — {abs(margin):.1f}% over limit ({limit_str})"

            report.results.append(CheckResult(
                name=name, severity=severity, expr=expr_str,
                value=float(value), limit=limit_str, passed=passed,
                margin_pct=round(margin, 1), warning=warning, unit=unit,
                message=msg,
            ))
        else:
            # Boolean expression (no limit — the expr itself is True/False)
            passed = bool(value)
            report.results.append(CheckResult(
                name=name, severity=severity, expr=expr_str,
                value=None, limit="", passed=passed,
                margin_pct=None, warning=False, unit=unit,
                message="OK" if passed else "FAILED",
            ))

    return report


def evaluate_all(design_path: Path) -> list[ConstraintReport]:
    """Load design.yaml and evaluate constraints for every registered component."""
    if not design_path.exists():
        return []
    design = yaml.safe_load(design_path.read_text()) or {}
    project_dir = design_path.parent
    reports = []

    for comp_name, comp_info in design.get("components", {}).items():
        folder = comp_info.get("folder", f"components/{comp_name}")
        constraints_path = project_dir / folder / "constraints.yaml"
        if constraints_path.exists():
            comp_constraints = load_constraints(constraints_path)
            report = evaluate_constraints(design, comp_constraints, comp_name)
            reports.append(report)

    return reports
