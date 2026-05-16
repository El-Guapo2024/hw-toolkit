"""Live-reload schematic preview daemon.

Watches the schematic source artifact (`.schem.json` OR `.kicad_sch`)
and re-runs every registered consumer on save.

Usage:
    python -m hw_agent.core.preview path/to/schematic.schem.json [--port 8765]
    python -m hw_agent.core.preview path/to/schematic.kicad_sch [--port 8765]

Then open http://localhost:8765/ — edit the source in any editor, watch
the SVG redraw.

Adding a consumer: append `(name, fn)` to CONSUMERS. Each consumer takes
`(source_path, out_dir)` and returns a JSON-serializable dict; failures
are caught and reported in `preview.eval.json`. Same trigger fires every
consumer, so visual + programmatic loops always see the same source.

Consumers that need the JSON model (validate_schem) skip themselves when
the source is a `.kicad_sch` — kicad-cli ERC will catch any structural
problems.
"""
from __future__ import annotations

import argparse
import http.server
import json
import socketserver
import sys
import threading
import time
import traceback
from functools import partial
from pathlib import Path

from hw_agent.artifacts.schematics.schem_renderer import Schematic


DEFAULT_PORT = 8765
POLL_INTERVAL_S = 0.3
SLOW_LOOP_IDLE_S = 3.0   # fire slow consumers after this much idle time


# Render-tick broadcaster. Each browser SSE connection holds a threading.Event;
# the watcher sets all of them after every successful render so the clients
# fetch the new SVG and swap it in. No polling, no reloads.
_LISTENERS: list[threading.Event] = []
_LISTENERS_LOCK = threading.Lock()


def _broadcast_tick():
    with _LISTENERS_LOCK:
        for ev in _LISTENERS:
            ev.set()


# ─── Consumers ──────────────────────────────────────────────────────────────

def validate_schem(source_path: Path, out_dir: Path) -> dict:
    """Schema-validate + count items. Skipped when source is a .kicad_sch
    (no JSON model to load — kicad-cli ERC is the validator there)."""
    if source_path.suffix != ".json":
        return {"skipped": "not a .schem.json source"}
    raw = json.loads(source_path.read_text())
    schem = Schematic.model_validate(raw)
    return {
        "symbols": len(schem.symbols),
        "wires": len(schem.wires),
        "labels": len(schem.labels),
        "canvas": {"w": schem.canvas.width, "h": schem.canvas.height,
                   "title": schem.canvas.title},
        "symbol_types": _count_types(schem),
    }


def _count_types(schem: Schematic) -> dict:
    counts: dict = {}
    for s in schem.symbols:
        counts[s.type] = counts.get(s.type, 0) + 1
    return counts


def pcb_check(source_path: Path, out_dir: Path) -> dict:
    """Build the .kicad_pcb headlessly from JSON and run kicad-cli DRC.

    Slow consumer (~3-5s — IPC pcb_writer + DRC). Lives in
    ON_DEMAND_CONSUMERS so it doesn't fire on every keystroke. Browser
    can preview the .kicad_pcb via KiCanvas (it supports both .kicad_sch
    and .kicad_pcb files in the same embed).

    Requires the JSON source — pcb_writer reads footprints from the
    schem schema. When source is a .kicad_sch (no JSON), this consumer
    skips with a note.
    """
    if source_path.suffix != ".json":
        return {
            "skipped": "pcb_check needs .schem.json (pcb_writer reads footprints)",
            "pcb_built": False,
        }

    import subprocess
    from hw_agent.artifacts.schematics.pcb_writer import export_file as pcb_export
    from hw_agent.artifacts.schematics.validators import validate, validate_pcb
    from hw_agent.artifacts.schematics.kicad_paths import kicad_cli
    from hw_agent.artifacts.schematics.schem_renderer import Schematic

    json_path = source_path
    out_pcb = out_dir / (json_path.stem.replace(".schem", "") + ".kicad_pcb")

    # Pre-flight schema check so missing footprints get a clean error
    # instead of crashing the pcbnew subprocess.
    raw = json.loads(json_path.read_text())
    schem = Schematic.model_validate(raw)
    issues = validate(schem) + validate_pcb(schem)
    if issues:
        return {
            "schema_ok": False,
            "schema_issues": issues,
            "pcb_built": False,
            "kicad_pcb": None,
        }

    try:
        report = pcb_export(json_path, out_pcb, strict=False)
    except Exception as e:
        return {
            "schema_ok": True,
            "pcb_built": False,
            "error": f"{type(e).__name__}: {e}",
        }

    drc_path = out_dir / f"{out_pcb.stem}_drc.json"
    cli = kicad_cli()
    drc_run = subprocess.run(
        [cli, "pcb", "drc", "--output", str(drc_path),
         "--format", "json", "--severity-all", str(out_pcb)],
        capture_output=True, text=True,
    )

    # Classify DRC violations via drc-filters.yaml — same KiBot-style
    # bucketing we use for ERC. real_issues = action items; expected =
    # bucketed via filter rules.
    from hw_agent.artifacts.schematics.drc_filters import classify, load_filters
    drc_classified = {
        "total": 0, "by_type": {},
        "real_issues": [], "expected": {}, "filter_log": [],
    }
    if drc_path.exists():
        try:
            drc_data = json.loads(drc_path.read_text())
            filters = load_filters(json_path)
            drc_classified = classify(drc_data, filters)
        except json.JSONDecodeError:
            pass

    return {
        "schema_ok": True,
        "pcb_built": True,
        "components_placed": report.get("components_placed", 0),
        "components_skipped": report.get("components_skipped", 0),
        "drc_total": drc_classified["total"],
        "drc_real_issues": drc_classified["real_issues"][:10],
        "drc_expected": drc_classified["expected"],
        "drc_by_type": drc_classified["by_type"],
        "kicad_pcb": str(out_pcb),
        "drc_report": str(drc_path) if drc_path.exists() else None,
        "drc_cli_rc": drc_run.returncode,
    }


def erc_check(source_path: Path, out_dir: Path) -> dict:
    """Run kicad-cli ERC against the schematic.

    Source can be `.schem.json` (compiles to `.kicad_sch` first) or a
    `.kicad_sch` directly. Either way, kicad-cli runs ERC and we classify
    violations against `erc-filters.yaml`.

    Slow consumer (~1.5s — kicad-cli subprocess) but gives the agent the same
    structural feedback they'd get from opening eeschema. Real issues are
    surfaced explicitly; cross-subsystem expected violations (pin_not_connected,
    label_dangling, etc.) are bucketed separately so they don't add noise.
    """
    from hw_agent.artifacts.schematics.eval import eval_from_json, run_eval, EvalResult
    from datetime import datetime, timezone

    if source_path.suffix == ".json":
        out_kicad_sch = out_dir / (source_path.stem.replace(".schem", "") + ".kicad_sch")
        result = eval_from_json(source_path, out_kicad_sch=out_kicad_sch)
    else:
        # .kicad_sch direct — bypass JSON. Wrap run_eval() output in an
        # EvalResult so the consumer dict shape matches.
        erc = run_eval(source_path, schem_json=None)
        result = EvalResult(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ok=erc.get("ok", False),
            schema={"ok": True, "issues": []},
            erc=erc,
            artifacts={"kicad_sch": str(source_path),
                       **(erc.get("artifacts") or {})},
            duration_ms=0,
        )
    return {
        "schema_ok": result.schema.get("ok", False),
        "schema_issues": result.schema.get("issues", []),
        "erc_ok": result.erc.get("ok", False),
        "erc_total": result.erc.get("total", -1),
        "real_issues": result.erc.get("real_issues", []),
        "expected": result.erc.get("expected", {}),
        "kicad_sch": result.artifacts.get("kicad_sch"),
        "kicad_svg": result.artifacts.get("svg"),
        "duration_ms": result.duration_ms,
    }


def kicad_sch_diff(json_path: Path, out_dir: Path) -> dict:
    """E5: detect what changed when a `.kicad_sch` is saved.

    Sources of saves we want to detect:
      - Human typing in eeschema → save → file mtime jumps
      - Our writer regenerates from JSON (same trigger but agent already knows)
      - Other agent's atomic edit tools (circuit_builder, json_ops)

    Either way we re-snapshot, diff against the previously-stored snapshot,
    and emit a structured change log so the agent on its next turn can read
    `preview.eval.json` and see "human added R5 (0.1µF cap), moved U1, deleted
    one wire" instead of just "the file changed."

    Snapshot is persisted at `<source_dir>/.last_kicad_sch_snap.json` —
    hidden file so it doesn't pollute project state. Pure metadata.

    Cheap (<50ms even for 100-component schematics) so it lives in the hot
    path. Skipped when source is .schem.json (.kicad_sch is downstream — diff
    will fire on the next eval anyway when our writer re-emits).
    """
    from hw_agent.artifacts.schematics.sch_diff import parse_kicad_sch, diff
    from dataclasses import asdict

    # Pick the .kicad_sch to diff: source if it's already one, else the
    # .kicad_sch our writer would emit.
    if json_path.suffix == ".kicad_sch":
        sch = json_path
    else:
        sch = json_path.parent / (json_path.stem.replace(".schem", "") + ".kicad_sch")
    if not sch.exists():
        return {"skipped": "no .kicad_sch yet — waiting on first eval"}

    try:
        new_snap = parse_kicad_sch(sch)
    except Exception as e:
        return {"ok": False, "error": f"parse failed: {type(e).__name__}: {e}"}

    snap_path = sch.parent / ".last_kicad_sch_snap.json"
    old_snap = None
    if snap_path.exists():
        try:
            from hw_agent.artifacts.schematics.sch_diff import Snapshot, SymbolSnap
            data = json.loads(snap_path.read_text())
            old_snap = Snapshot(
                file_path=data["file_path"],
                file_mtime=data["file_mtime"],
                file_sha=data["file_sha"],
            )
            for uuid, sd in data.get("symbols", {}).items():
                # Best-effort reconstruct (only fields we use for diffing).
                # Old snapshots may have keyed by ref instead of uuid;
                # `uuid=key` keeps the dict consistent either way.
                old_snap.symbols[uuid] = SymbolSnap(
                    uuid=sd.get("uuid", uuid),
                    ref=sd["ref"], lib_id=sd["lib_id"], value=sd["value"],
                    at=tuple(sd["at"]), rotation=sd.get("rotation", 0.0),
                    footprint=sd.get("footprint", ""),
                )
        except Exception:
            old_snap = None  # corrupt; treat as initial

    # Short-circuit: identical sha → no work
    if old_snap is not None and old_snap.file_sha == new_snap.file_sha:
        return {
            "ok": True, "changed": False, "sha": new_snap.file_sha,
            "symbols": len(new_snap.symbols),
        }

    d = diff(old_snap, new_snap)

    # Persist new snapshot so subsequent saves diff against this one.
    snap_path.write_text(json.dumps(new_snap.to_dict(), indent=2))

    return {
        "ok": True,
        "changed": not d.is_empty(),
        "sha": new_snap.file_sha,
        "diff": d.to_dict(),
        "symbols_now": len(new_snap.symbols),
        "wires_now": len(new_snap.wires),
        "labels_now": len(new_snap.labels),
    }


# Hot-path consumers — fire on every JSON save. Keep these strictly fast
# (single-digit ms each) so iteration latency stays imperceptible to the
# browser SSE stream. Anything >100 ms goes in ON_DEMAND_CONSUMERS instead.
#
# Browser renders .kicad_sch via KiCanvas (WebGL), agent renders via
# kicad-cli (`get_render` MCP tool with bbox crop). The retired slim C++
# renderer lives in `_trash/` for reference; not on the live path.
CONSUMERS: list[tuple[str, callable]] = [
    ("validate_schem", validate_schem),   # schema check, <1 ms
    ("kicad_sch_diff", kicad_sch_diff),   # E5: human-edit detection, <50 ms
]

# On-demand / slow-loop consumers — fire after the JSON has been idle for
# SLOW_LOOP_IDLE_S seconds, or when the agent explicitly triggers them.
# These take hundreds of ms or more and would jank the hot loop.
ON_DEMAND_CONSUMERS: list[tuple[str, callable]] = [
    ("erc_check", erc_check),           # kicad-cli ERC + .kicad_sch export, ~1 s
    ("pcb_check", pcb_check),           # pcb_writer + kicad-cli DRC, ~3-5 s
]


def run_consumers(json_path: Path, out_dir: Path,
                  consumers: list = None) -> dict:
    """Run the given consumer list; merge results into preview.eval.json.

    Caller passes either CONSUMERS (hot loop) or ON_DEMAND_CONSUMERS (slow
    loop). Results are merged so both loops' status appears in one file —
    the browser shows everything but each consumer is updated at its own
    cadence.
    """
    consumers = consumers if consumers is not None else CONSUMERS
    eval_path = out_dir / "preview.eval.json"

    # Read existing status to merge into.
    status: dict = {"timestamp": time.time(), "source": str(json_path),
                    "results": {}}
    if eval_path.exists():
        try:
            existing = json.loads(eval_path.read_text())
            status["results"] = existing.get("results", {})
        except Exception:
            pass
    status["timestamp"] = time.time()

    for name, fn in consumers:
        try:
            r = fn(json_path, out_dir)
            status["results"][name] = {"ok": True, "ran_at": time.time(), **r}
        except Exception as e:
            err_str = f"{type(e).__name__}: {e}"
            tb = traceback.format_exc()
            status["results"][name] = {
                "ok": False,
                "ran_at": time.time(),
                "error": err_str,
                "trace": tb,
            }
            _append_error_log(out_dir, name, err_str, tb)
    eval_path.write_text(json.dumps(status, indent=2))
    return status


def _append_error_log(out_dir: Path, consumer: str, err: str, tb: str) -> None:
    """Tail-friendly log of consumer failures.

    The agent reads `preview.errors.log` (or invokes /preview-errors) to
    see the most recent breaks without parsing preview.eval.json. Lines
    are timestamped + bracketed by consumer name so `tail -f` works.
    """
    log_path = out_dir / "preview.errors.log"
    ts = time.strftime("%H:%M:%S")
    block = (
        f"\n[{ts}] ✗ {consumer}\n"
        f"    {err}\n"
        + "\n".join(f"    {line}" for line in tb.rstrip().splitlines()[-8:])
        + "\n"
    )
    with log_path.open("a") as f:
        f.write(block)


# ─── Watcher ────────────────────────────────────────────────────────────────

def watch_loop(json_path: Path, out_dir: Path):
    """Hot loop: fires CONSUMERS on every JSON save.

    Uses watchfiles (Rust-backed Notify crate) instead of mtime polling.
    Reaction latency is <50 ms; debounce coalesces editor multi-write saves
    (e.g. write + rename) into a single trigger. Initial save fires once
    immediately so the first preview is populated without waiting for an edit.
    """
    from watchfiles import watch, Change

    target = str(json_path)

    def _fire(label: str = "hot") -> None:
        t0 = time.time()
        status = run_consumers(json_path, out_dir, CONSUMERS)
        ok = sum(1 for n, _ in CONSUMERS
                 if status["results"].get(n, {}).get("ok"))
        ms = (time.time() - t0) * 1000
        tag = "OK" if ok == len(CONSUMERS) else "FAIL"
        print(f"[{time.strftime('%H:%M:%S')}] {label} {tag} "
              f"{ok}/{len(CONSUMERS)} — {ms:.0f}ms")
        for name, _ in CONSUMERS:
            r = status["results"].get(name, {})
            if not r.get("ok"):
                print(f"  ✗ {name}: {r.get('error', '?')}")
        _broadcast_tick()

    if json_path.exists():
        _fire(label="init")  # first render so the page isn't blank

    try:
        for changes in watch(target, debounce=200, step=50, raise_interrupt=False):
            relevant = any(
                p == target and c in (Change.modified, Change.added)
                for c, p in changes
            )
            if relevant:
                _fire()
    except Exception as e:
        print(f"watcher: {type(e).__name__}: {e}")


def python_watch_loop(py_path: Path, out_dir: Path):
    """Watch a `.py` source file. On save, run it as a subprocess so it
    can write its sibling `.schem.json`. The JSON watcher then picks up
    that write and fires the normal hot-loop pipeline.

    This closes the authoring loop: edit Python → save → JSON regenerates
    → SVG re-renders → browser SSE-updates. No manual `python` invocation.
    """
    import subprocess
    from watchfiles import watch, Change

    target = str(py_path)

    def _compile() -> None:
        t0 = time.time()
        try:
            r = subprocess.run(
                [sys.executable, str(py_path)],
                cwd=str(py_path.parent),
                capture_output=True, text=True, timeout=15,
            )
            ms = (time.time() - t0) * 1000
            if r.returncode == 0:
                print(f"[{time.strftime('%H:%M:%S')}] py compile OK — {ms:.0f}ms")
            else:
                print(f"[{time.strftime('%H:%M:%S')}] py compile FAIL — {ms:.0f}ms")
                stderr_tail = r.stderr.rstrip() if r.stderr else "(no stderr)"
                for line in stderr_tail.splitlines()[-10:]:
                    print(f"  {line}")
                # Also surface to the agent-readable error log
                _append_error_log(
                    out_dir, "py_compile",
                    f"subprocess exit {r.returncode}",
                    stderr_tail,
                )
        except subprocess.TimeoutExpired:
            print(f"[{time.strftime('%H:%M:%S')}] py compile TIMEOUT")
            _append_error_log(out_dir, "py_compile", "TimeoutExpired",
                              "subprocess exceeded 15s timeout")
        except Exception as e:
            print(f"py compile: {type(e).__name__}: {e}")
            _append_error_log(out_dir, "py_compile",
                              f"{type(e).__name__}: {e}",
                              traceback.format_exc())

    if py_path.exists():
        _compile()  # initial compile so the JSON is fresh on startup

    try:
        for changes in watch(target, debounce=200, step=50, raise_interrupt=False):
            if any(p == target and c in (Change.modified, Change.added)
                   for c, p in changes):
                _compile()
    except Exception as e:
        print(f"py watcher: {type(e).__name__}: {e}")


def slow_loop(json_path: Path, out_dir: Path):
    """Slow loop: fires ON_DEMAND_CONSUMERS once the JSON has been idle for
    SLOW_LOOP_IDLE_S seconds. Each idle window only triggers once — we
    track the mtime that was processed so we don't re-run on a stable file.
    """
    last_processed_mtime = -1.0
    while True:
        try:
            mtime = json_path.stat().st_mtime
            quiet_for = time.time() - mtime
            if (quiet_for >= SLOW_LOOP_IDLE_S
                    and mtime != last_processed_mtime
                    and ON_DEMAND_CONSUMERS):
                last_processed_mtime = mtime
                t0 = time.time()
                status = run_consumers(json_path, out_dir, ON_DEMAND_CONSUMERS)
                ok = sum(1 for n, _ in ON_DEMAND_CONSUMERS
                         if status["results"].get(n, {}).get("ok"))
                ms = (time.time() - t0) * 1000
                tag = "OK" if ok == len(ON_DEMAND_CONSUMERS) else "FAIL"
                print(f"[{time.strftime('%H:%M:%S')}] slow {tag} "
                      f"{ok}/{len(ON_DEMAND_CONSUMERS)} — {ms:.0f}ms")
                _broadcast_tick()
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"slow loop: {type(e).__name__}: {e}")
        time.sleep(0.5)


# ─── HTTP server ────────────────────────────────────────────────────────────

PREVIEW_HTML = """<!DOCTYPE html>
<html><head>
<title>preview — {name}</title>
<!-- KiCanvas: pure-JS WebGL renderer for .kicad_sch files. Replaces the
     2.7s `kicad-cli sch export svg` subprocess with browser-side rendering. -->
<script type="module" src="https://kicanvas.org/kicanvas/kicanvas.js"></script>
<script>
  // ── Quick-render SVG (Python pipeline) ───────────────────────────────────
  async function refreshSvg(slot) {{
    const target = slot === 'prev' ? '/preview.prev.svg' : '/preview.svg';
    try {{
      const r = await fetch(target + '?t=' + Date.now(), {{cache:'no-store'}});
      if (!r.ok) return;
      const svg = await r.text();
      const host = document.getElementById(slot + '-svg');
      if (host) host.innerHTML = svg;
    }} catch (e) {{ /* swallow */ }}
  }}
  // ── KiCad-faithful render (KiCanvas, browser-side) ───────────────────────
  // Fetch the .kicad_sch text and embed it inline as <kicanvas-source>. This
  // sidesteps two flakes of the src-URL approach: the http.server returns
  // application/octet-stream for the file (KiCanvas wants text), and our
  // cache-buster query string can confuse its URL → file-type detection.
  // Inline source is also faster — no extra HTTP round trip from KiCanvas.
  async function refreshKicad() {{
    const host = document.getElementById('kicanvas-host');
    if (!host) return;
    try {{
      const r = await fetch('/{kicad_name}?t=' + Date.now(),
                            {{cache:'no-store'}});
      if (!r.ok) {{
        host.innerHTML = `<div style="padding:1em;color:#888">`
          + `<em>{kicad_name}</em> not generated yet — `
          + `run <code>/trigger?name=erc_check</code> or wait for the slow loop`
          + `</div>`;
        return;
      }}
      const sch = await r.text();
      // Wrap KiCad source in a <kicanvas-embed>/<kicanvas-source> pair.
      // Re-create the element on every tick so KiCanvas re-parses cleanly
      // (the embed caches per-instance and won't re-render an existing one).
      const pre = document.createElement('div');
      const embed = document.createElement('kicanvas-embed');
      embed.setAttribute('controls', 'basic');
      embed.setAttribute('controlslist', 'nooverlay');
      embed.style.display = 'block';
      embed.style.width = '100%';
      embed.style.height = '520px';
      const src = document.createElement('kicanvas-source');
      src.textContent = sch;
      embed.appendChild(src);
      pre.appendChild(embed);
      host.replaceChildren(pre);
    }} catch (e) {{
      host.innerHTML = `<div style="padding:1em;color:#f66">`
        + `kicanvas refresh failed: ${{e.message}}</div>`;
    }}
  }}
  // ── PCB render (KiCanvas, browser-side, headless-built .kicad_pcb) ──────
  async function refreshKicadPcb() {{
    const host = document.getElementById('kicanvas-pcb-host');
    if (!host) return;
    try {{
      const r = await fetch('/{kicad_pcb_name}?t=' + Date.now(),
                            {{cache:'no-store'}});
      if (!r.ok) {{
        host.innerHTML = `<div style="padding:1em;color:#888">`
          + `<em>{kicad_pcb_name}</em> not built yet — `
          + `add a footprint to each physical part and the slow loop will `
          + `produce it on the next idle window`
          + `</div>`;
        return;
      }}
      const pcb = await r.text();
      const pre = document.createElement('div');
      const embed = document.createElement('kicanvas-embed');
      embed.setAttribute('controls', 'basic');
      embed.setAttribute('controlslist', 'nooverlay');
      embed.style.display = 'block';
      embed.style.width = '100%';
      embed.style.height = '520px';
      const src = document.createElement('kicanvas-source');
      src.textContent = pcb;
      embed.appendChild(src);
      pre.appendChild(embed);
      host.replaceChildren(pre);
    }} catch (e) {{
      host.innerHTML = `<div style="padding:1em;color:#f66">`
        + `pcb refresh failed: ${{e.message}}</div>`;
    }}
  }}
  async function refreshEval() {{
    try {{
      const r = await fetch('/eval.html?t=' + Date.now(), {{cache:'no-store'}});
      if (!r.ok) return;
      document.getElementById('eval-block').innerHTML = await r.text();
    }} catch (e) {{ /* swallow */ }}
  }}
  function connectEvents() {{
    const es = new EventSource('/events');
    es.onmessage = (e) => {{
      refreshSvg('current'); refreshSvg('prev');
      refreshKicad();
      refreshKicadPcb();
      refreshEval();
      const ts = document.getElementById('ts');
      if (ts) ts.textContent = new Date().toLocaleTimeString();
    }};
    es.onerror = () => {{ es.close(); setTimeout(connectEvents, 800); }};
  }}
  document.addEventListener('DOMContentLoaded', () => {{
    refreshSvg('current'); refreshSvg('prev');
    refreshKicad();
    refreshKicadPcb();
    refreshEval();
    connectEvents();
  }});
</script>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 0; padding: 1em;
          background: #1a1a1a; color: #eee; }}
  header {{ display: flex; justify-content: space-between; align-items: baseline; }}
  h1 {{ font-size: 1.1em; margin: 0; color: #fff; }}
  .ts {{ color: #888; font-size: 0.85em; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1em; margin: 1em 0; }}
  .grid.solo {{ grid-template-columns: 1fr; }}
  .col h2 {{ font-size: 0.85em; color: #aaa; margin: 0 0 0.3em; }}
  .col h2 .tag {{ display: inline-block; padding: 0 0.5em; border-radius: 3px;
                  font-size: 0.85em; margin-left: 0.4em; }}
  .col h2 .before {{ background: #553; color: #ffd; }}
  .col h2 .after {{ background: #353; color: #dfd; }}
  .col h2 .kicad {{ background: #335; color: #ddf; }}
  .frame {{ background: white; border-radius: 6px; padding: 0.6em; }}
  .frame object {{ width: 100%; height: auto; display: block; }}
  pre {{ background: #2a2a2a; padding: 0.6em 0.8em; border-radius: 4px;
         font-size: 0.85em; overflow-x: auto; line-height: 1.4; }}
  .ok {{ color: #6f6; }}
  .bad {{ color: #f66; }}
  .err {{ background: #3a1010; border-left: 4px solid #f66;
          padding: 0.6em 0.8em; margin: 0.4em 0; }}
</style>
</head><body>
<header>
  <h1>{name}</h1>
  <span class="ts">last update <span id="ts">{ts}</span></span>
</header>

<h2 style="font-size:0.85em;color:#aaa;margin:0.5em 0 0.3em">
  KiCad-faithful render <span class="kicad" style="display:inline-block;padding:0 0.5em;border-radius:3px;font-size:0.85em;margin-left:0.4em;background:#335;color:#ddf">via KiCanvas</span>
</h2>
<div class="frame" id="kicanvas-host"></div>

<h2 style="font-size:0.85em;color:#aaa;margin:1em 0 0.3em">
  PCB layout <span class="kicad" style="display:inline-block;padding:0 0.5em;border-radius:3px;font-size:0.85em;margin-left:0.4em;background:#335;color:#ddf">via KiCanvas (.kicad_pcb)</span>
</h2>
<div class="frame" id="kicanvas-pcb-host"></div>

<h2 style="font-size:0.85em;color:#aaa;margin:1em 0 0.3em">
  Quick render <span style="display:inline-block;padding:0 0.5em;border-radius:3px;font-size:0.85em;margin-left:0.4em;background:#444;color:#ddd">Python SVG</span>
</h2>
<div class="grid {grid_class}">
  {prev_col}
  <div class="col">
    <h2>After<span class="tag after">latest</span></h2>
    <div class="frame"><div id="current-svg"></div></div>
  </div>
</div>

<h2 style="font-size:0.95em;color:#aaa;margin:1em 0 0.3em">consumers</h2>
<div id="eval-block">{eval_block}</div>
</body></html>
"""

PREV_COL_HTML = """  <div class="col">
    <h2>Previous<span class="tag before">before</span></h2>
    <div class="frame"><div id="prev-svg"></div></div>
  </div>
"""


def _format_eval(status: dict) -> tuple[str, str]:
    """Render the consumer-status block as HTML; return (block, ts)."""
    if not status:
        return "<pre>(no render yet — save the JSON to trigger)</pre>", "—"
    results = status.get("results", {})
    parts = []
    for name, r in results.items():
        if r.get("ok"):
            details = {k: v for k, v in r.items() if k != "ok"}
            parts.append(
                f'<pre><span class="ok">✓ {name}</span> '
                f'{json.dumps(details)}</pre>'
            )
        else:
            err = r.get("error", "")
            trace = r.get("trace", "")
            parts.append(
                f'<div class="err"><span class="bad">✗ {name}</span> '
                f'{err}<pre>{trace}</pre></div>'
            )
    ts = time.strftime("%H:%M:%S",
                       time.localtime(status.get("timestamp", time.time())))
    return "\n".join(parts), ts


class PreviewHandler(http.server.SimpleHTTPRequestHandler):
    """Serves files from out_dir; renders / dynamically as the auto-refresh page."""

    json_path: Path = None  # type: ignore
    out_dir: Path = None    # type: ignore

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send_index()
            return
        if self.path == "/events":
            self._serve_sse()
            return
        if self.path.startswith("/eval.html"):
            self._send_eval_partial()
            return
        super().do_GET()

    def _serve_sse(self):
        """Hold the connection open; emit a `data: tick` line on every render."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        ev = threading.Event()
        with _LISTENERS_LOCK:
            _LISTENERS.append(ev)
        try:
            # Heartbeat keeps the connection alive even on idle.
            while True:
                fired = ev.wait(timeout=15)
                if fired:
                    ev.clear()
                    self.wfile.write(b"data: tick\n\n")
                else:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _LISTENERS_LOCK:
                if ev in _LISTENERS:
                    _LISTENERS.remove(ev)

    def _send_eval_partial(self):
        """Just the consumer-status block — fetched by JS on every tick."""
        eval_path = self.out_dir / "preview.eval.json"
        status = {}
        if eval_path.exists():
            try:
                status = json.loads(eval_path.read_text())
            except Exception:
                pass
        block, _ts = _format_eval(status)
        body = block.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_index(self):
        eval_path = self.out_dir / "preview.eval.json"
        status = {}
        if eval_path.exists():
            try:
                status = json.loads(eval_path.read_text())
            except Exception:
                pass
        block, ts = _format_eval(status)
        svg_path = self.out_dir / "preview.svg"
        cache = int(svg_path.stat().st_mtime) if svg_path.exists() else int(time.time())
        prev_path = self.out_dir / "preview.prev.svg"
        if prev_path.exists():
            prev_col = PREV_COL_HTML.format(cache=cache)
            grid_class = ""
        else:
            prev_col = ""
            grid_class = "solo"
        # KiCanvas embed target: the .kicad_sch the erc_check consumer wrote
        # next to the JSON. Filename derives from <stem>.kicad_sch (the
        # `.schem` infix is stripped to match how kicad_writer names the file).
        stem = self.json_path.stem.replace(".schem", "")
        kicad_name = stem + ".kicad_sch"
        kicad_pcb_name = stem + ".kicad_pcb"
        body = PREVIEW_HTML.format(
            name=self.json_path.name,
            ts=ts,
            cache=cache,
            prev_col=prev_col,
            grid_class=grid_class,
            eval_block=block,
            kicad_name=kicad_name,
            kicad_pcb_name=kicad_pcb_name,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args, **kwargs):
        pass  # silence access logs


def serve(out_dir: Path, json_path: Path, port: int):
    PreviewHandler.json_path = json_path
    PreviewHandler.out_dir = out_dir
    handler = partial(PreviewHandler, directory=str(out_dir))
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("", port), handler) as httpd:
        httpd.daemon_threads = True
        url = f"http://localhost:{port}/"
        print(f"preview: {url}")
        print(f"watching: {json_path}")
        httpd.serve_forever()


# ─── Entry point ────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument("source", type=Path,
                   help="path to a *.schem.json or *.kicad_sch file")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = p.parse_args()

    source_path = args.source.resolve()
    if not source_path.parent.exists():
        sys.exit(f"directory does not exist: {source_path.parent}")
    if source_path.suffix not in (".json", ".kicad_sch"):
        sys.exit(f"source must end in .schem.json or .kicad_sch, got "
                 f"{source_path.suffix}")

    out_dir = source_path.parent

    threading.Thread(target=watch_loop,
                     args=(source_path, out_dir), daemon=True).start()
    threading.Thread(target=slow_loop,
                     args=(source_path, out_dir), daemon=True).start()

    # If a sibling `.py` source file exists, watch it too. Saves recompile
    # the source artifact which then fires the hot loop above.
    if source_path.name.endswith(".schem.json"):
        py_sibling = source_path.parent / source_path.name.replace(".schem.json", ".py")
    else:
        py_sibling = source_path.with_suffix(".py")
    if py_sibling.exists():
        threading.Thread(target=python_watch_loop,
                         args=(py_sibling, out_dir), daemon=True).start()
        print(f"py source: {py_sibling.name}")

    serve(out_dir, source_path, args.port)


if __name__ == "__main__":
    main()
