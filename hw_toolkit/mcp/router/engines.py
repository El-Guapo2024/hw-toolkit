"""Routing engine adapters.

Each engine takes a `.kicad_pcb` and applies routes in place. **Both
engines are hosted by us** — we don't depend on third-party services.

The MCP layer (`hw_toolkit.mcp.router`) is a thin dispatcher over these.

Engines:

    freerouting-hosted   POST DSN to our self-hosted FreeRouting service
                         (`hw-router-service/` Docker compose). Default.
                         Free, OSS engine, runs on our infrastructure.
                         No API key required.
    orthoroute           POST .kicad_pcb to our self-hosted OrthoRoute
                         GPU service (`hw-orthoroute-service/` Docker
                         compose). CUDA-accelerated. Runs on our GPU
                         host.

URLs:
    FREEROUTING_API_URL  — defaults to http://localhost:8000
    ORTHOROUTE_API_URL   — defaults to http://localhost:8001

For production, point these at our deployed instances.

History:
- We previously had `engine="deeppcb"` (paid, api.deeppcb.ai) and used
  `api.freerouting.app` for FreeRouting. Both dropped 2026-05-06: the
  api.freerouting.app service required per-user API keys and was
  unreliable; DeepPCB is closed-source and paid. **We host what we
  ship.**
- A `freerouting-local` (java -jar locally) engine briefly existed but
  was dropped — users should not need Java + JAR; the hosted service
  handles it. This freerouting manager (moved into hw_toolkit/mcp/router) is
  retained only for the existing `pcb_route` tool in
  a thin local fallback.

Common contract:

    async def route(kicad_pcb: Path, *, kicad_sch=None, passes=5,
                    threads=4, timeout_s=600, on_progress=None,
                    **engine_kwargs) -> dict
        returns {"ok": bool, "engine": str, "stage"?: str, "error"?: str,
                 "elapsed_s": float, ...}

DSN export and SES import always run locally — they need the user's
KiCad install (or pcbnew IPC). Only the routing step itself is dispatched.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

import httpx


ENGINES = (
    "freerouting-hosted",
    "orthoroute",
)

DEFAULT_ENGINE = "freerouting-hosted"

# Both engines are hosted by us — production URL TBD, defaults to localhost
# so a developer running the Docker compose can test end-to-end immediately.
# Ports chosen to avoid clashes with common dev backends on 8000/8001.
FREEROUTING_API_DEFAULT = "http://localhost:8002"
ORTHOROUTE_API_DEFAULT = "http://localhost:8003"


# ─── Common helpers ─────────────────────────────────────────────────────────


def _emit(on_progress: Optional[Callable[[int, int, str], None]], pct: int, msg: str) -> None:
    if on_progress is None:
        return
    try:
        on_progress(pct, 100, msg)
    except Exception:
        pass


async def _export_dsn(kicad_pcb: Path, kicad_sch: Optional[Path], skip_netlist_sync: bool,
                     on_progress) -> tuple[Path, Path]:
    """Local DSN export. Returns (work_dir, dsn_path)."""
    import subprocess
    from hw_toolkit.mcp.router.freerouting import manager
    from hw_toolkit.kicad import pcb_writer
    from hw_toolkit.kicad.kicad_paths import kicad_cli

    work = manager().work_dir / kicad_pcb.stem
    work.mkdir(parents=True, exist_ok=True)
    dsn_path = work / f"{kicad_pcb.stem}.dsn"
    net_path = work / f"{kicad_pcb.stem}.net"

    if not skip_netlist_sync:
        sch = kicad_sch if kicad_sch else kicad_pcb.with_suffix(".kicad_sch")
        if not sch.exists():
            raise FileNotFoundError(
                f"schematic not found: {sch}. Pass kicad_sch=<path> or set skip_netlist_sync=True."
            )
        _emit(on_progress, 5, "exporting netlist from schematic")
        await asyncio.to_thread(
            subprocess.run,
            [kicad_cli(), "sch", "export", "netlist", "--output", str(net_path), str(sch)],
            check=True, capture_output=True,
        )
        _emit(on_progress, 10, "syncing netlist to PCB")
        result = await asyncio.to_thread(pcb_writer.run_sync_netlist, kicad_pcb, net_path)
        if not result.get("ok"):
            raise RuntimeError(f"netlist sync failed: {result}")

    _emit(on_progress, 20, "exporting Specctra DSN")
    result = await asyncio.to_thread(pcb_writer.run_dsn_export, kicad_pcb, dsn_path)
    if not result.get("ok"):
        raise RuntimeError(f"DSN export failed: {result}")
    return work, dsn_path


async def _import_ses(kicad_pcb: Path, ses_path: Path, on_progress) -> dict:
    from hw_toolkit.kicad import pcb_writer

    _emit(on_progress, 95, "importing routes back into PCB")
    return await asyncio.to_thread(pcb_writer.run_ses_import, kicad_pcb, ses_path)


# ─── Engine: freerouting-hosted (our self-hosted instance) ──────────────────


async def route_freerouting_hosted(
    kicad_pcb: Path,
    *,
    kicad_sch: Optional[Path] = None,
    passes: int = 5,
    threads: int = 4,
    timeout_s: float = 3600,
    skip_netlist_sync: bool = False,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    api_key: Optional[str] = None,  # reserved for future auth; service is open today
    api_url: Optional[str] = None,
    poll_interval_s: float = 5.0,
    **_: Any,
) -> dict:
    """Route via our self-hosted FreeRouting service.

    Talks to `hw-router-service/` (Docker compose: FastAPI + RQ +
    JAR worker). DSN→SES contract over plain HTTP, no auth keys
    required, no third-party dependency.

    Default URL: http://localhost:8002 (override with FREEROUTING_API_URL).
    For production, point at our deployed instance.

    Wire format (defined by `hw-router-service/src/hw_router_service/server.py`):
        POST /jobs   body={dsn, passes, threads}   → {job_id, state}
        GET  /jobs/{id}   → {state, elapsed_s, log_tail, ses?}
        POST /jobs/{id}/cancel   → {ok, state}
    """
    base = api_url or os.environ.get("FREEROUTING_API_URL") or FREEROUTING_API_DEFAULT
    started = time.time()

    # Fail-fast: probe service before spending 5-30s on DSN export.
    # If the user forgot to bring up docker compose, surface that
    # immediately rather than after pcbnew IPC churns through the export.
    _emit(on_progress, 1, f"probing {base}/health")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as probe:
            r = await probe.get(f"{base}/health")
            r.raise_for_status()
    except httpx.HTTPError as e:
        return _err("freerouting-hosted", "preflight", started,
                    f"service at {base} not reachable ({e}). "
                    f"Start with: cd hw-router-service && docker compose up -d")

    work, dsn_path = await _export_dsn(kicad_pcb, kicad_sch, skip_netlist_sync, on_progress)
    ses_path = work / f"{kicad_pcb.stem}.ses"

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    _emit(on_progress, 25, f"submitting to {base}")
    job_id: Optional[str] = None
    async with httpx.AsyncClient(base_url=base, headers=headers,
                                  timeout=httpx.Timeout(60.0, read=timeout_s)) as cli:
        try:
            resp = await cli.post(
                "/jobs",
                json={"dsn": dsn_path.read_text(), "passes": passes, "threads": threads},
            )
            resp.raise_for_status()
            payload = resp.json()
            job_id = payload.get("job_id")
            if not job_id:
                return _err("freerouting-hosted", "submit", started,
                            f"no job_id in response: {payload}")
        except httpx.HTTPError as e:
            return _err("freerouting-hosted", "submit", started,
                        f"submit failed: {e}. Is hw-router-service running at {base}?")

        try:
            deadline = time.time() + timeout_s
            last_pass_logged = 0
            while time.time() < deadline:
                await asyncio.sleep(poll_interval_s)
                try:
                    r = await cli.get(f"/jobs/{job_id}?include_ses=false&log_tail=20")
                    r.raise_for_status()
                    d = r.json()
                except httpx.HTTPError as e:
                    return _err("freerouting-hosted", "poll", started,
                                f"poll failed: {e}", remote_job_id=job_id)

                state = d.get("state", "running")

                # Real progress: parse FreeRouting's per-pass log lines.
                # Each successful pass logs "Auto-router pass #N on board ..."
                # and we map pass N → 25 + N/passes * 65 to interpolate
                # 25%-90% across all passes.
                pct, msg = _parse_freerouting_progress(
                    d.get("log_tail") or "", state, passes, last_pass_logged,
                )
                if pct is not None:
                    last_pass_logged = max(last_pass_logged, pct[1])
                    _emit(on_progress, pct[0], msg)
                else:
                    _emit(on_progress, 25, f"freerouting state={state}")

                if state == "succeeded":
                    # Re-fetch with SES included this time
                    r = await cli.get(f"/jobs/{job_id}?include_ses=true&log_tail=0")
                    r.raise_for_status()
                    d = r.json()
                    ses_text = d.get("ses")
                    if not ses_text:
                        return _err("freerouting-hosted", "result", started,
                                    f"no ses in response: keys={list(d.keys())}",
                                    remote_job_id=job_id)
                    ses_path.write_text(ses_text)
                    break
                if state in ("failed", "cancelled"):
                    return _err("freerouting-hosted", "remote", started,
                                d.get("error") or "remote job failed",
                                remote_job_id=job_id,
                                log_tail=d.get("log_tail"))
            else:
                # Best-effort cancel on the server before reporting timeout,
                # so the JVM doesn't keep burning CPU after we give up.
                try:
                    await cli.post(f"/jobs/{job_id}/cancel")
                except Exception:  # noqa: BLE001
                    pass
                return _err("freerouting-hosted", "timeout", started,
                            f"timed out after {timeout_s}s", remote_job_id=job_id)
        except asyncio.CancelledError:
            # MCP turn was cancelled (user hit stop, or context expired).
            # Propagate cancel to the service so the JVM stops too.
            try:
                await cli.post(f"/jobs/{job_id}/cancel")
            except Exception:  # noqa: BLE001
                pass
            raise

    imp = await _import_ses(kicad_pcb, ses_path, on_progress)
    _emit(on_progress, 100, "done")

    return {
        "ok": bool(imp.get("ok", True)),
        "engine": "freerouting-hosted",
        "elapsed_s": round(time.time() - started, 2),
        "remote_job_id": job_id,
        "ses_path": str(ses_path),
        **{k: v for k, v in imp.items() if k != "ok"},
    }


# ─── Engine: orthoroute (hosted GPU service we operate or user runs) ───────


async def route_orthoroute(
    kicad_pcb: Path,
    *,
    kicad_sch: Optional[Path] = None,  # noqa: ARG001 — unused, OrthoRoute reads PCB only
    passes: int = 5,
    threads: int = 4,  # noqa: ARG001 — unused, GPU not threaded
    timeout_s: float = 1800,
    skip_netlist_sync: bool = False,  # noqa: ARG001 — OrthoRoute requires PCB to have nets already
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    api_key: Optional[str] = None,
    api_url: Optional[str] = None,
    poll_interval_s: float = 5.0,
    **_: Any,
) -> dict:
    """Proxy to a hosted OrthoRoute service (CUDA + KiCad IPC GPU router).

    Unlike the FreeRouting / DeepPCB engines (DSN→SES), OrthoRoute
    operates on a `.kicad_pcb` directly (uses KiCad IPC). The wire
    format here is multipart upload + multipart download.

    Default target is `http://localhost:8000` (self-hosted via
    `hw-orthoroute-service/` Docker compose). Override with `api_url`
    or `ORTHOROUTE_API_URL` env to point at a public instance.

    Pipeline:
      1. Read the user's .kicad_pcb (no DSN export needed)
      2. POST as multipart to /jobs along with options
      3. Poll /jobs/{id} until state is succeeded/failed
      4. GET /jobs/{id}/result to download the routed .kicad_pcb
      5. Write it back over the user's file (with a .bak backup)
    """
    base = api_url or os.environ.get("ORTHOROUTE_API_URL") or ORTHOROUTE_API_DEFAULT
    started = time.time()

    if not kicad_pcb.exists():
        return _err("orthoroute", "input", started, f"input pcb not found: {kicad_pcb}")

    _emit(on_progress, 5, "reading kicad_pcb")
    pcb_bytes = await asyncio.to_thread(kicad_pcb.read_bytes)

    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    _emit(on_progress, 15, f"uploading to OrthoRoute service ({base})")
    async with httpx.AsyncClient(base_url=base, headers=headers,
                                  timeout=httpx.Timeout(60.0, read=timeout_s)) as cli:
        try:
            files = {"pcb": (kicad_pcb.name, pcb_bytes, "application/octet-stream")}
            data = {"passes": str(passes), "timeout_s": str(int(timeout_s))}
            resp = await cli.post("/jobs", files=files, data=data)
            resp.raise_for_status()
            payload = resp.json()
            job_id = payload.get("job_id")
            if not job_id:
                return _err("orthoroute", "submit", started,
                            f"no job_id in response: {payload}")
        except httpx.HTTPError as e:
            return _err("orthoroute", "submit", started, f"submit failed: {e}")

        deadline = time.time() + timeout_s
        last_pct = 15
        while time.time() < deadline:
            await asyncio.sleep(poll_interval_s)
            try:
                r = await cli.get(f"/jobs/{job_id}")
                r.raise_for_status()
                d = r.json()
            except httpx.HTTPError as e:
                return _err("orthoroute", "poll", started, f"poll failed: {e}",
                            remote_job_id=job_id)

            state = d.get("state", "running")
            pct = min(85, last_pct + 4)
            last_pct = pct
            _emit(on_progress, pct, f"orthoroute state={state}")

            if state == "succeeded":
                break
            if state in ("failed", "cancelled"):
                return _err("orthoroute", "remote", started,
                            d.get("error") or "remote job failed",
                            stage_remote=d.get("stage"),
                            remote_job_id=job_id,
                            log_tail=d.get("log_tail"))
        else:
            return _err("orthoroute", "timeout", started,
                        f"timed out after {timeout_s}s", remote_job_id=job_id)

        _emit(on_progress, 90, "downloading routed pcb")
        try:
            r = await cli.get(f"/jobs/{job_id}/result")
            r.raise_for_status()
            routed_bytes = r.content
        except httpx.HTTPError as e:
            return _err("orthoroute", "download", started,
                        f"result download failed: {e}", remote_job_id=job_id)

    if not routed_bytes:
        return _err("orthoroute", "download", started,
                    "empty result body", remote_job_id=job_id)

    _emit(on_progress, 95, "writing routed pcb back")
    backup = kicad_pcb.with_suffix(kicad_pcb.suffix + ".bak")
    try:
        await asyncio.to_thread(backup.write_bytes, pcb_bytes)
        await asyncio.to_thread(kicad_pcb.write_bytes, routed_bytes)
    except OSError as e:
        return _err("orthoroute", "writeback", started,
                    f"failed writing routed pcb: {e}", remote_job_id=job_id)

    _emit(on_progress, 100, "done")
    return {
        "ok": True,
        "engine": "orthoroute",
        "elapsed_s": round(time.time() - started, 2),
        "remote_job_id": job_id,
        "kicad_pcb_path": str(kicad_pcb),
        "backup_path": str(backup),
        "input_size": len(pcb_bytes),
        "output_size": len(routed_bytes),
    }


# ─── Dispatcher ─────────────────────────────────────────────────────────────


_DISPATCH = {
    "freerouting-hosted": route_freerouting_hosted,
    "orthoroute": route_orthoroute,
}


async def route_with_engine(engine: str, kicad_pcb: Path, **kwargs: Any) -> dict:
    fn = _DISPATCH.get(engine)
    if fn is None:
        return {
            "ok": False, "engine": engine, "stage": "dispatch",
            "error": f"unknown engine: {engine!r}. Valid: {ENGINES}",
        }
    return await fn(kicad_pcb, **kwargs)


# ─── Helpers ───────────────────────────────────────────────────────────────


def _err(engine: str, stage: str, started: float, msg: str, **extra: Any) -> dict:
    return {
        "ok": False,
        "engine": engine,
        "stage": stage,
        "error": msg,
        "elapsed_s": round(time.time() - started, 2),
        **extra,
    }


# ─── FreeRouting log parser (for real progress) ─────────────────────────────


_FR_PASS_RE = re.compile(
    r"Auto-router pass\s+#?(\d+).+score of\s+([\d.]+)\s+\((\d+)\s+unrouted\)",
    re.IGNORECASE,
)
_FR_DONE_RE = re.compile(
    r"Auto-router session completed.+final score:\s*([\d.]+).+\((\d+)\s+unrouted\)",
    re.IGNORECASE,
)


def _parse_freerouting_progress(
    log_tail: str, state: str, total_passes: int, last_pass_logged: int,
) -> tuple[tuple[int, int] | None, str]:
    """Extract real progress from FreeRouting's log_tail.

    Returns ((pct, pass_num), message) when a new pass milestone or
    completion has appeared since last check. Returns (None, fallback)
    otherwise — caller falls back to a generic state message.
    """
    if not log_tail:
        return None, f"freerouting state={state}"

    done = _FR_DONE_RE.search(log_tail)
    if done:
        score, unrouted = done.group(1), done.group(2)
        return (95, total_passes), (
            f"freerouting done: score={score}, {unrouted} unrouted"
        )

    # Find the highest-numbered pass mentioned in the log tail
    passes = list(_FR_PASS_RE.finditer(log_tail))
    if not passes:
        return None, f"freerouting state={state}"
    last = passes[-1]
    pass_n, score, unrouted = int(last.group(1)), last.group(2), last.group(3)
    if pass_n <= last_pass_logged:
        return None, f"freerouting state={state}"
    # Map pass N of total → 25-90% range
    span = max(1, total_passes)
    pct = min(89, 25 + int(65 * pass_n / span))
    return (pct, pass_n), (
        f"freerouting pass {pass_n}/{total_passes}: score={score}, {unrouted} unrouted"
    )
