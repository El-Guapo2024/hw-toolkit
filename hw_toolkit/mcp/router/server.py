"""router-mcp — multi-engine PCB autorouting MCP server.

A thin MCP wrapper that dispatches routing jobs to one of two backends,
both **hosted by us**:

    engine="freerouting-hosted"   ← default. POSTs DSN to our self-hosted
                                    FreeRouting service (hw-router-service/).
                                    Free, OSS, no API key. Default URL:
                                    http://localhost:8000 (override via
                                    FREEROUTING_API_URL).
    engine="orthoroute"           ← POSTs .kicad_pcb to our self-hosted
                                    OrthoRoute GPU service
                                    (hw-orthoroute-service/). CUDA-
                                    accelerated. Default URL:
                                    http://localhost:8001 (override via
                                    ORTHOROUTE_API_URL).

DSN export and SES import always run locally — they need the user's
KiCad install (or pcbnew IPC). Only the *routing* step is dispatched.

Run: router-mcp                   # via console script
or:  python -m hw_toolkit.mcp.router.server

MCP config (.mcp.json):
    {"router": {"type": "stdio", "command": "router-mcp", "args": []}}
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import Context, FastMCP

from .engines import (
    DEFAULT_ENGINE,
    ENGINES,
    route_with_engine,
)


mcp = FastMCP(
    "router",
    instructions=(
        "PCB autorouter (multi-engine). Tools:\n"
        "  router_check_setup     — verify engine readiness\n"
        "  list_engines           — show available routing engines + status\n"
        "  dsn_export             — Specctra DSN export from .kicad_pcb\n"
        "  ses_import             — apply a .ses to a .kicad_pcb\n"
        "  route_board            — full pipeline (DSN export → engine → SES import)\n"
        "\n"
        "Default engine is `freerouting-hosted` — POSTs DSN to our self-hosted "
        "FreeRouting service (no API key). Pass engine='orthoroute' for "
        "CUDA-accelerated routing via our hosted OrthoRoute GPU service. "
        "Both default to localhost; override URLs via FREEROUTING_API_URL "
        "and ORTHOROUTE_API_URL env vars."
    ),
)


# ─── Setup / sanity ─────────────────────────────────────────────────────────


@mcp.tool()
def list_engines() -> dict:
    """[read] List available routing engines and their readiness.

    Returns:
        {
          "default": "freerouting-hosted",
          "engines": [
            {"name": "...", "ready": bool, "needs": [...], "notes": "..."},
            ...
          ]
        }
    """
    import os

    fr_url = os.environ.get("FREEROUTING_API_URL", "http://localhost:8000")
    or_url = os.environ.get("ORTHOROUTE_API_URL", "http://localhost:8001")
    out = [
        {
            "name": "freerouting-hosted",
            "ready": "url-only",
            "needs": [f"hw-router-service running at {fr_url}"],
            "notes": "Default. FreeRouting JAR running in our Docker "
                     "compose (hw-router-service/). No API key required.",
        },
        {
            "name": "orthoroute",
            "ready": "url-only",
            "needs": [
                f"hw-orthoroute-service running at {or_url}",
                "NVIDIA GPU on the service host (not the user's machine)",
            ],
            "notes": "Hosted GPU OrthoRoute service (hw-orthoroute-service/). "
                     "Override URL via ORTHOROUTE_API_URL env.",
        },
    ]
    return {"default": DEFAULT_ENGINE, "engines": out}


@mcp.tool()
def router_check_setup() -> dict:
    """[read] Sanity check the router toolchain.

    Probes each engine's URL to verify the backing service is reachable,
    in addition to reporting static config readiness. Surfaces concrete
    errors so users don't waste time on a slow DSN export when the
    service is down.

    Returns engines + per-engine `reachable` boolean and `probe_error`
    when reachable=false. `actionable` is a flat list of human-readable
    "what to fix" hints.
    """
    import os
    import urllib.error
    import urllib.request

    info = list_engines()

    def probe(url: str) -> tuple[bool, str | None]:
        try:
            req = urllib.request.Request(f"{url.rstrip('/')}/health", method="GET")
            with urllib.request.urlopen(req, timeout=2) as r:
                if r.status != 200:
                    return False, f"HTTP {r.status}"
                # Quick sanity: response should be JSON with ok=true. We
                # don't fail on shape mismatch (different engines may emit
                # different /health bodies); just require a 200.
                return True, None
        except urllib.error.URLError as e:
            return False, f"unreachable: {e.reason if hasattr(e, 'reason') else e}"
        except (TimeoutError, OSError) as e:
            return False, f"timeout/socket: {e}"
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"

    fr_url = os.environ.get("FREEROUTING_API_URL", "http://localhost:8002")
    or_url = os.environ.get("ORTHOROUTE_API_URL", "http://localhost:8003")

    actionable = []
    for e in info["engines"]:
        url = fr_url if e["name"] == "freerouting-hosted" else \
              or_url if e["name"] == "orthoroute" else None
        if url:
            ok, err = probe(url)
            e["reachable"] = ok
            e["url"] = url
            if not ok:
                e["probe_error"] = err
                actionable.append(
                    f"engine={e['name']}: service at {url} not reachable ({err}). "
                    f"Start with: cd hw-router-service && docker compose up -d"
                )
        if not e["ready"] and e["needs"]:
            actionable.append(
                f"engine={e['name']}: missing {', '.join(e['needs'])}"
            )

    info["actionable"] = actionable
    return info


# ─── DSN export / SES import (low-level, always local) ──────────────────────


@mcp.tool()
def dsn_export(kicad_pcb: str, output: Optional[str] = None) -> dict:
    """[compute] Export .kicad_pcb to Specctra DSN. Always local.

    Args:
        kicad_pcb: path to the .kicad_pcb
        output:    output DSN path (default: same dir, .dsn extension)

    Returns: {"ok": bool, "dsn_path": str, ...}
    """
    from .schematics import pcb_writer

    pcb = Path(kicad_pcb).resolve()
    out = Path(output).resolve() if output else pcb.with_suffix(".dsn")
    return pcb_writer.run_dsn_export(pcb, out)


@mcp.tool()
def ses_import(kicad_pcb: str, ses_path: str) -> dict:
    """[write] Apply a Specctra SES to a .kicad_pcb. Always local.

    Args:
        kicad_pcb: target board (modified in place)
        ses_path:  the .ses file produced by a router

    Returns: {"ok": bool, "tracks_added"?: int, "error"?: str}
    """
    from .schematics import pcb_writer

    return pcb_writer.run_ses_import(Path(kicad_pcb).resolve(),
                                      Path(ses_path).resolve())


# ─── route_board: full pipeline, multi-engine ───────────────────────────────


@mcp.tool()
async def route_board(
    kicad_pcb: str,
    engine: str = DEFAULT_ENGINE,
    kicad_sch: Optional[str] = None,
    passes: int = 5,
    threads: int = 4,
    timeout_s: float = 600,
    skip_netlist_sync: bool = False,
    api_key: Optional[str] = None,
    api_url: Optional[str] = None,
    ctx: Context = None,
) -> dict:
    """[write] Auto-route a .kicad_pcb end-to-end via the chosen engine.

    Pipeline (regardless of engine):
        1. kicad-cli sch export netlist  → .net           (5%)
        2. pcb_writer.run_sync_netlist (IPC)  → nets on pads (10-15%)
        3. pcb_writer.run_dsn_export (IPC)    → Specctra DSN (20%)
        4. ENGINE-SPECIFIC routing            → SES        (25-90%)
           - freerouting-hosted: POST DSN to our self-hosted
             hw-router-service (default, FREEROUTING_API_URL)
           - orthoroute:         POST .kicad_pcb to our self-hosted
             hw-orthoroute-service GPU container (ORTHOROUTE_API_URL)
        5. pcb_writer.run_ses_import (IPC)    → tracks back (95-100%)

    The `.kicad_pcb` is modified in place. On failure, return dict has
    `ok: false` plus `stage` and `error`.

    Args:
        kicad_pcb:           target board (modified in place)
        engine:              routing engine (see list_engines)
        kicad_sch:           source schematic for nets (default: same stem)
        passes:              router pass limit (more = slower, better)
        threads:             parallelism hint
        timeout_s:           give up after this many seconds
        skip_netlist_sync:   skip stages 1-2 if PCB already has nets
        api_key:             reserved (engines are open today)
        api_url:             override engine's default URL

    Returns engine-specific result dict.
    """
    if engine not in ENGINES:
        return {
            "ok": False, "engine": engine, "stage": "dispatch",
            "error": f"unknown engine: {engine!r}. Valid: {list(ENGINES)}",
        }

    pcb = Path(kicad_pcb).resolve()
    sch = Path(kicad_sch).resolve() if kicad_sch else None

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_progress(current: int, total: int, msg: str) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (current, total, msg))

    async def _drain() -> None:
        while True:
            current, total, msg = await queue.get()
            if ctx is not None:
                try:
                    await ctx.report_progress(current, total, msg)
                except Exception:
                    pass
            if current >= total:
                return

    drain = asyncio.create_task(_drain())
    try:
        result = await route_with_engine(
            engine, pcb,
            kicad_sch=sch,
            passes=passes,
            threads=threads,
            timeout_s=timeout_s,
            skip_netlist_sync=skip_netlist_sync,
            on_progress=on_progress,
            api_key=api_key,
            api_url=api_url,
        )
    finally:
        drain.cancel()
        try:
            await drain
        except asyncio.CancelledError:
            pass
    return result


# ─── Entry point ────────────────────────────────────────────────────────────


def main() -> None:
    """Console-script entry point — `router-mcp` (or
    `python -m hw_toolkit.mcp.router.server`)."""
    mcp.run()


if __name__ == "__main__":
    main()
