"""Doctrine validators — pure functions, testable in isolation.

Each validator signature: (args: dict, state: dict) -> None.
- args  = parsed tool-call inputs.
- state = current .state.json contents (project state).

Returns None on pass. Raises ValidationError(msg) on fail; msg becomes the
rejection text returned to the agent by the PreToolUse hook.
"""

from __future__ import annotations

from typing import Any, Callable


class ValidationError(Exception):
    """Raised by a validator when a doctrine rule is violated."""


# ---------- helpers ---------- #


def _locked_mpns(state: dict[str, Any]) -> list[str]:
    spec = state.get("spec_summary") or {}
    return list(spec.get("locked_mpns") or [])


def _bom_ceiling(state: dict[str, Any]) -> float:
    spec = state.get("spec_summary") or {}
    constraints = spec.get("constraints") or {}
    ceiling = constraints.get("bom_ceiling_usd")
    if ceiling is None:
        return float("inf")
    if isinstance(ceiling, str) and "-" in ceiling:
        return float(ceiling.split("-")[-1].strip())
    return float(ceiling)


# ---------- validators ---------- #


def assert_all_loads_locked(args: dict[str, Any], state: dict[str, Any]) -> None:
    """load_first: cannot add a rail subsystem until at least one load is MPN-locked.

    Rail filtering is handled by the rule's `when` guard (category in buck/ldo).
    Validator just enforces the locked-loads precondition.
    """
    locked = _locked_mpns(state)
    if not locked:
        name = args.get("name") or args.get("category") or "rail"
        raise ValidationError(
            f"load_first: cannot add rail subsystem '{name}' before any loads are locked. "
            "Lock actuators, sensors, and MCU MPNs first (intake/spec stage)."
        )


def assert_datasheet_section_ref(args: dict[str, Any], state: dict[str, Any]) -> None:
    """pass1_no_math: design_pass=1 picks must cite a datasheet page reference."""
    if args.get("design_pass") != 1:
        return
    ref = (args.get("datasheet_section_ref") or "").strip().lower()
    if not ref.startswith("p."):
        raise ValidationError(
            "pass1_no_math: design_pass=1 requires datasheet_section_ref in 'p.<page>' form "
            f"(e.g. 'p.34 typical application'). Got: {args.get('datasheet_section_ref')!r}."
        )


def assert_sourcing_dk_first(args: dict[str, Any], state: dict[str, Any]) -> None:
    """digikey_primary: Digi-Key stock checked first; JLC fallback needs justification."""
    dk_qty = args.get("digikey_stock_qty")
    jlc_just = (args.get("jlc_fallback_justification") or "").strip()
    if dk_qty is None and not jlc_just:
        raise ValidationError(
            "digikey_primary: digikey_stock_qty is missing AND no jlc_fallback_justification. "
            "Check Digi-Key first; if OOS, justify JLC fallback in writing."
        )
    if isinstance(dk_qty, (int, float)) and dk_qty == 0 and not jlc_just:
        raise ValidationError(
            "digikey_primary: Digi-Key stock=0 requires jlc_fallback_justification."
        )


def assert_no_prose_blocks(args: dict[str, Any], state: dict[str, Any]) -> None:
    """live_visual_only: hw_agent/.live/*.md should be visual artifacts, not prose.

    Heuristic: flag a run of >=3 consecutive non-empty non-markup lines with >=5 words.
    """
    content = args.get("content") or args.get("new_string") or ""
    if not content:
        return

    prose_run = 0
    max_run = 0
    in_code_fence = False
    for raw in content.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_code_fence = not in_code_fence
            prose_run = 0
            continue
        if in_code_fence or not line:
            prose_run = 0
            continue
        if line[0] in "#-*|>!" or line.startswith("```"):
            prose_run = 0
            continue
        if len(line.split()) >= 5:
            prose_run += 1
            if prose_run > max_run:
                max_run = prose_run
        else:
            prose_run = 0

    if max_run >= 3:
        raise ValidationError(
            f"live_visual_only: {max_run} consecutive prose lines in .live/*.md. "
            "Move text reasoning to chat. .live/ accepts mermaid, images, and tables only."
        )


def assert_bom_under_ceiling(args: dict[str, Any], state: dict[str, Any]) -> None:
    """bom_ceiling: projected running BOM total must stay under ceiling.

    designer-mcp's subsystem_choose_part uses `price` (per-unit USD), so we read
    that. `qty_per_board` is multiplied in to compute the line contribution.
    """
    unit = float(args.get("price") or args.get("unit_price_usd") or 0.0)
    qty = int(args.get("qty_per_board") or 1)
    line_cost = unit * qty
    running = float(state.get("bom_running_total_usd") or 0.0)
    ceiling = _bom_ceiling(state)
    projected = running + line_cost
    if projected > ceiling:
        raise ValidationError(
            f"bom_ceiling: projected BOM ${projected:.2f} exceeds ceiling ${ceiling:.2f} "
            f"(running ${running:.2f} + line ${line_cost:.2f} = ${unit:.2f} x {qty}). "
            "Pick a cheaper part or raise ceiling explicitly in spec_summary.constraints."
        )


# ---------- registry ---------- #


Validator = Callable[[dict[str, Any], dict[str, Any]], None]

REGISTRY: dict[str, Validator] = {
    "assert_all_loads_locked": assert_all_loads_locked,
    "assert_datasheet_section_ref": assert_datasheet_section_ref,
    "assert_sourcing_dk_first": assert_sourcing_dk_first,
    "assert_no_prose_blocks": assert_no_prose_blocks,
    "assert_bom_under_ceiling": assert_bom_under_ceiling,
}
