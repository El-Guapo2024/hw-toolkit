"""Doctrine loader: parse doctrine.yaml, filter rules for soft/hard layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "harness loader needs pyyaml. Add 'pyyaml' to pyproject.toml dependencies."
    ) from e

HARNESS_DIR = Path(__file__).parent
DOCTRINE_PATH = HARNESS_DIR / "doctrine.yaml"


@dataclass(frozen=True)
class Rule:
    id: str
    text: str
    soft_stages: tuple[str, ...] = ()
    hard_tools: tuple[str, ...] = ()
    hard_when: str | None = None
    hard_validator: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Rule":
        soft = d.get("soft") or {}
        hard = d.get("hard") or {}
        tools = hard.get("tool") or []
        if isinstance(tools, str):
            tools = [tools]
        return cls(
            id=d["id"],
            text=d["text"],
            soft_stages=tuple(soft.get("stages") or ()),
            hard_tools=tuple(tools),
            hard_when=hard.get("when"),
            hard_validator=hard.get("validator"),
        )


def load_doctrine(path: Path = DOCTRINE_PATH) -> list[Rule]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return [Rule.from_dict(r) for r in (data.get("rules") or [])]


def rules_for_stage(rules: Iterable[Rule], stage: str) -> list[Rule]:
    """Soft layer, mode B (main agent): rules matching the current stage."""
    return [r for r in rules if "*" in r.soft_stages or stage in r.soft_stages]


def rules_for_tools(rules: Iterable[Rule], allowed_tools: Iterable[str]) -> list[Rule]:
    """Soft layer, mode C (sub-agent): rules whose hard.tool overlaps with allow-list."""
    allow = set(allowed_tools)
    return [r for r in rules if r.hard_tools and any(t in allow for t in r.hard_tools)]


def rules_for_tool_call(rules: Iterable[Rule], tool_name: str) -> list[Rule]:
    """Hard layer: rules whose hard.tool matches the tool being called."""
    return [r for r in rules if tool_name in r.hard_tools]


def evaluate_when(when: str | None, args: dict[str, Any]) -> bool:
    """Evaluate the optional `when` guard against the tool args.

    Restricted eval: only tool-arg names + len/any/all are exposed. No builtins, no imports.
    Any evaluation error (missing key, type error) is treated as "rule does not apply".
    """
    if not when:
        return True
    safe_globals: dict[str, Any] = {"__builtins__": {}, "len": len, "any": any, "all": all}
    try:
        return bool(eval(when, safe_globals, dict(args)))  # noqa: S307
    except Exception:
        return False


def soft_inject_text(rules: Iterable[Rule], heading: str = "Active doctrine") -> str:
    """Render a list of rules as a markdown injection block for the soft layer."""
    items = [f"- **{r.id}**: {r.text}" for r in rules]
    if not items:
        return ""
    return "## " + heading + "\n" + "\n".join(items)
