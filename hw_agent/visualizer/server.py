"""FastAPI server for the hardware-agent visualizer.

Run: `hw-vis` or `python -m hw_agent.visualizer.server`.

Routes:
  GET /                                -- dashboard
  GET /spec/<project>                  -- spec view
  GET /designer/<project>/<subsystem>  -- candidate cards + datasheets
  GET /api/state                       -- raw state JSON
  GET /events                          -- SSE stream of file-change events
  GET /static-files/<path>             -- repo file passthrough (PDFs, PNGs, SVGs)
  GET /static/<asset>                  -- visualizer static (CSS, JS)
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

try:
    from fastapi import FastAPI, Request
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.templating import Jinja2Templates
except ImportError:
    print(
        "ERROR: visualizer needs fastapi + jinja2 + watchdog. Run: pip install fastapi jinja2 watchdog uvicorn",
        file=sys.stderr,
    )
    raise

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import data_loader as dl

REPO = dl.REPO
TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


# ---------- file watcher ---------- #


class _ReloadBroadcaster(FileSystemEventHandler):
    def __init__(self):
        self.subscribers: set[asyncio.Queue] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def _notify(self, path: str) -> None:
        if self.loop is None:
            return
        for q in list(self.subscribers):
            self.loop.call_soon_threadsafe(q.put_nowait, path)

    def on_modified(self, event):
        if not event.is_directory:
            self._notify(event.src_path)

    def on_created(self, event):
        if not event.is_directory:
            self._notify(event.src_path)


_broadcaster = _ReloadBroadcaster()


# ---------- app lifecycle ---------- #


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _broadcaster.loop = asyncio.get_running_loop()
    observer = Observer()
    watch_targets = [
        REPO / "hw_agent" / ".state.json",
        REPO / "hw_agent" / ".live",
        REPO / "docs" / "projects",
    ]
    for t in watch_targets:
        if t.exists():
            observer.schedule(_broadcaster, str(t), recursive=True)
    observer.start()
    print(f"hw-vis: watching {[str(t) for t in watch_targets if t.exists()]}")
    try:
        yield
    finally:
        observer.stop()
        observer.join(timeout=1.0)


app = FastAPI(lifespan=_lifespan)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------- routes ---------- #


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    state = dl.load_state()
    project = state.get("project") or "(none)"
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "state": state,
            "projects": dl.list_projects(),
            "subsystems": dl.list_subsystems(project) if project not in ("(none)",) else [],
            "datasheets": dl.list_datasheets(project) if project not in ("(none)",) else [],
        },
    )


@app.get("/spec/{project}", response_class=HTMLResponse)
async def spec_view(request: Request, project: str):
    profile_md = dl.project_profile_md(project) or "(profile.md not found)"
    return templates.TemplateResponse(
        request,
        "spec.html",
        {
            "request": request,
            "state": dl.load_state(),
            "project": project,
            "profile_md": profile_md,
            "datasheets": dl.list_datasheets(project),
        },
    )


@app.get("/designer/{project}/{subsystem}", response_class=HTMLResponse)
async def designer_view(request: Request, project: str, subsystem: str):
    candidates = dl.load_candidates(project, subsystem)
    actuals = dl.load_actuals(project, subsystem)
    return templates.TemplateResponse(
        request,
        "designer.html",
        {
            "request": request,
            "state": dl.load_state(),
            "project": project,
            "subsystem": subsystem,
            "data": candidates,
            "actuals": actuals,
            "subsystems": dl.list_subsystems(project),
        },
    )


@app.get("/api/state")
async def api_state():
    return JSONResponse(dl.load_state())


@app.get("/static-files/{file_path:path}")
async def static_files(file_path: str):
    target = (REPO / file_path).resolve()
    try:
        target.relative_to(REPO)
    except ValueError:
        return JSONResponse({"error": "outside repo"}, status_code=400)
    if not target.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(str(target))


@app.get("/events")
async def events():
    """Server-sent events for live reload + activity notifications."""
    queue = _broadcaster.subscribe()

    async def stream():
        try:
            yield "event: ready\ndata: connected\n\n"
            while True:
                try:
                    path = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"event: change\ndata: {path}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _broadcaster.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream")


# ---------- CLI ---------- #


def main() -> int:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reload", action="store_true", help="dev mode: reload server on code change")
    args = parser.parse_args()

    print(f"hw-vis: serving http://{args.host}:{args.port}/")
    print(f"hw-vis: repo root: {REPO}")
    uvicorn.run(
        "hw_agent.visualizer.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
