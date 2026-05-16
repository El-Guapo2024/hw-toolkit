"""Pure-function eval pipeline — callable from the file watcher.

The watcher daemon (other agent) invokes `eval_from_json(json_path)` whenever
a `.schem.json` file is saved. We export to KiCad, run kicad-cli ERC + SVG,
classify violations, and write a small `<base>.eval.json` status file next
to the source. The agent reads that status file on its next turn — one read
replaces the previous 3 tool calls.

Status file format (intentionally small):
    {
      "timestamp": "...",
      "ok": bool,                    # overall pass/fail
      "schema": {"ok": bool, "issues": [...]},
      "erc": {
        "total": int,
        "by_type": {type: count},
        "real_issues": [{type, desc, items}],   # filtered, actionable
        "expected": {"pin_not_connected": int, ...}  # cross-subsystem signals
      },
      "artifacts": {"kicad_sch": "...", "svg": "..."},
      "duration_ms": int
    }
"""

from __future__ import annotations

import json
import subprocess
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .kicad_paths import kicad_cli
from .ksa_writer import export_file
from .schem_renderer import Schematic
from .validators import validate
from .erc_filters import classify, load_filters, Filter


@dataclass
class EvalResult:
    timestamp: str
    ok: bool
    schema: dict
    erc: dict
    artifacts: dict
    duration_ms: int
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ─── Helpers ────────────────────────────────────────────────────────────────
# Classification logic moved to erc_filters.classify() — driven by an
# erc-filters.yaml config (KiBot-style) rather than a hardcoded set.


# ─── Public API ─────────────────────────────────────────────────────────────

def run_eval(
    kicad_sch: Path,
    svg_dir: Optional[Path] = None,
    filters: Optional[list[Filter]] = None,
    schem_json: Optional[Path] = None,
    skip_svg: bool = False,
) -> dict:
    """Run kicad-cli ERC (and optionally SVG export) against an existing .kicad_sch.

    Args:
        kicad_sch: schematic to evaluate.
        svg_dir: where to drop ERC report + SVG (default <sch_dir>/eval_out).
        filters: pre-loaded filter list. If None, loads from erc-filters.yaml
                 next to schem_json or its parent project, falling back to
                 built-in defaults.
        schem_json: source JSON (used to locate erc-filters.yaml).
        skip_svg: True to skip the kicad-cli SVG export — faster eval pass when
                  the browser uses KiCanvas to render the .kicad_sch directly.

    Returns the classified ERC summary.
    """
    out_dir = svg_dir or kicad_sch.parent / "eval_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    erc_path = out_dir / f"{kicad_sch.stem}_erc.json"
    svg_path = out_dir / f"{kicad_sch.stem}.svg"

    if filters is None and schem_json is not None:
        filters = load_filters(schem_json)
    if filters is None:
        filters = load_filters(kicad_sch)  # uses parent dir as fallback root

    cli = kicad_cli()
    erc_proc = subprocess.run(
        [cli, "sch", "erc", "--output", str(erc_path),
         "--format", "json", "--severity-all", str(kicad_sch)],
        capture_output=True, text=True,
    )
    if not skip_svg:
        subprocess.run(
            [cli, "sch", "export", "svg", "--output", str(out_dir) + "/", str(kicad_sch)],
            capture_output=True, text=True,
        )

    if not erc_path.exists():
        return {
            "ok": False,
            "total": -1,
            "by_type": {},
            "real_issues": [{
                "type": "kicad_cli_error",
                "desc": "ERC failed to produce output",
                "items": [(erc_proc.stdout or erc_proc.stderr).strip()[:200]],
            }],
            "expected": {},
            "filter_log": [],
            "artifacts": {
                "erc_report": str(erc_path) if erc_path.exists() else None,
                "svg": str(svg_path) if svg_path.exists() else None,
            },
        }

    erc_data = json.loads(erc_path.read_text())
    classified = classify(erc_data, filters)
    classified["ok"] = len(classified["real_issues"]) == 0
    classified["artifacts"] = {
        "erc_report": str(erc_path),
        "svg": str(svg_path) if svg_path.exists() else None,
    }
    return classified


def eval_from_json(
    schem_json: Path,
    out_kicad_sch: Optional[Path] = None,
    child_sheet: bool = False,
    skip_svg: bool = False,
    on_progress=None,  # Optional[Callable[[int, int, str], None]]
) -> EvalResult:
    """End-to-end: validate JSON → export to .kicad_sch → run ERC + SVG.

    Writes a `<base>.eval.json` status file next to the source. Designed to be
    invoked by the file watcher on save — one call per `.schem.json` change.

    on_progress: optional callback (current, total, message) — fires at every
    pipeline stage so the agent can stream feedback via ctx.report_progress
    instead of seeing a 1-3s silence.
    """
    schem_json = Path(schem_json).resolve()
    started = time.time()
    out_kicad_sch = Path(out_kicad_sch).resolve() if out_kicad_sch else (
        schem_json.parent / (schem_json.stem.replace(".schem", "") + ".kicad_sch")
    )

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _emit(pct: int, msg: str):
        if on_progress:
            try:
                on_progress(pct, 100, msg)
            except Exception:
                pass

    _emit(0, f"validating {schem_json.name}")

    # 1. Schema validation
    try:
        data = json.loads(schem_json.read_text())
        schem = Schematic.model_validate(data)
        issues = validate(schem)
    except Exception as e:
        _emit(100, f"schema parse error: {e}")
        return _finish(EvalResult(
            timestamp=timestamp, ok=False,
            schema={"ok": False, "issues": [f"parse error: {e}"]},
            erc={}, artifacts={},
            duration_ms=int((time.time() - started) * 1000),
            error=str(e),
        ), schem_json)

    if issues:
        _emit(100, f"schema invalid: {len(issues)} issues")
        return _finish(EvalResult(
            timestamp=timestamp, ok=False,
            schema={"ok": False, "issues": issues},
            erc={}, artifacts={},
            duration_ms=int((time.time() - started) * 1000),
        ), schem_json)

    _emit(15, f"exporting .kicad_sch ({len(schem.symbols)} symbols, {len(schem.wires)} wires)")

    # 2. Export to .kicad_sch (skip strict re-validate since we just did it)
    try:
        export_file(schem_json, out_kicad_sch, child_sheet=child_sheet, strict=False)
    except Exception as e:
        return _finish(EvalResult(
            timestamp=timestamp, ok=False,
            schema={"ok": True, "issues": []},
            erc={}, artifacts={},
            duration_ms=int((time.time() - started) * 1000),
            error=f"writer failed: {e}",
        ), schem_json)

    _emit(40, "running kicad-cli sch erc")

    # 3. Run kicad-cli ERC + SVG (filters loaded from erc-filters.yaml)
    erc_summary = run_eval(out_kicad_sch, schem_json=schem_json, skip_svg=skip_svg)
    erc_summary["artifacts"]["kicad_sch"] = str(out_kicad_sch)

    real = len(erc_summary.get("real_issues", []))
    expected = sum(erc_summary.get("expected", {}).values())
    _emit(95, f"ERC done — {real} real issues, {expected} expected")

    duration_ms = int((time.time() - started) * 1000)
    _emit(100, f"done in {duration_ms} ms")

    return _finish(EvalResult(
        timestamp=timestamp,
        ok=erc_summary.get("ok", False),
        schema={"ok": True, "issues": []},
        erc=erc_summary,
        artifacts=erc_summary.pop("artifacts", {}),
        duration_ms=duration_ms,
    ), schem_json)


def _finish(result: EvalResult, schem_json: Path) -> EvalResult:
    """Write the status file next to the source JSON."""
    status_path = schem_json.parent / (schem_json.stem.replace(".schem", "") + ".eval.json")
    status_path.write_text(json.dumps(result.to_dict(), indent=2))
    return result
