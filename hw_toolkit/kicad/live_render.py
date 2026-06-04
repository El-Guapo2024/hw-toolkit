"""Live render — the OpenSCAD-studio loop for KiCad.

OpenSCAD's best agent UX (openscad-studio) isn't IPC into the GUI; it's an
embedded viewer that *watches the workspace files* and re-renders on every
change. KiCad has no in-process/wasm renderer, but it doesn't need one for the
agent loop: the agent authors `hw_toolkit` Python → `write_kicad()` rewrites the
`.kicad_sch` on disk → we watch that file → re-render SVG → refresh the pane.

So `board.live()` gives a Jupyter SVG pane that updates itself whenever the
schematic (or PCB) file changes — no manual `board.show()` re-run. The only
cost vs OpenSCAD is render latency (kicad-cli subprocess ~1-2 s, debounced).

Usage (notebook)::

    view = board.live()          # schematic; or board.live(pcb=True)
    board.write_kicad()          # pane refreshes by itself
    ...
    view.stop()                  # stop watching

There is no fallback: needs `watchdog` (file events) and an IPython display
front-end (Jupyter / VS Code). Outside a notebook the pane just won't update.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class LiveView:
    """A self-refreshing SVG pane bound to a file on disk.

    `render` returns SVG *bytes* for the current file; `to_html` wraps those
    bytes into a displayable IPython object (reuses board's responsive HTML).
    The view watches `watch_path`'s directory, and on any write to that file
    (debounced) re-renders and updates the same display slot in place.
    """

    _counter = 0

    def __init__(
        self,
        render: Callable[[], bytes],
        to_html: Callable[[bytes], object],
        watch_path: str | Path,
        *,
        debounce_s: float = 0.4,
    ) -> None:
        self._render = render
        self._to_html = to_html
        self._path = Path(watch_path).resolve()
        self._debounce_s = debounce_s
        LiveView._counter += 1
        self._display_id = f"hwt-live-{LiveView._counter}"
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._observer: Observer | None = None
        self._started = False

    # -- public ----------------------------------------------------------
    def start(self) -> "LiveView":
        """Render once, show the pane, and begin watching the file."""
        self._show(first=True)
        handler = _Handler(self._path.name, self._on_event)
        obs = Observer()
        obs.schedule(handler, str(self._path.parent), recursive=False)
        obs.daemon = True
        obs.start()
        self._observer = obs
        self._started = True
        return self

    def stop(self) -> None:
        """Stop watching (pane keeps its last render)."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None
        self._started = False

    def refresh(self) -> None:
        """Force an immediate re-render (bypass file watch)."""
        self._show(first=False)

    # -- internals -------------------------------------------------------
    def _on_event(self) -> None:
        # Debounce: KiCad writes the file in bursts; collapse to one render.
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_s, self._safe_show)
            self._timer.daemon = True
            self._timer.start()

    def _safe_show(self) -> None:
        try:
            self._show(first=False)
        except Exception as exc:  # never let a render crash kill the watcher
            self._update_text(f"⚠️ live render failed: {exc}")

    def _show(self, *, first: bool) -> None:
        from IPython.display import display, update_display

        html = self._to_html(self._render())
        if first:
            display(html, display_id=self._display_id)
        else:
            update_display(html, display_id=self._display_id)

    def _update_text(self, msg: str) -> None:
        from IPython.display import HTML, update_display

        update_display(HTML(f"<pre>{msg}</pre>"), display_id=self._display_id)

    def __repr__(self) -> str:
        state = "watching" if self._started else "stopped"
        return f"<LiveView {state} {self._path.name}>"


class _Handler(FileSystemEventHandler):
    """Fire `cb` only for writes/creates/moves touching `filename`."""

    def __init__(self, filename: str, cb: Callable[[], None]) -> None:
        self._filename = filename
        self._cb = cb

    def _hit(self, path: str) -> bool:
        return Path(path).name == self._filename

    def on_modified(self, event):  # noqa: D102
        if not event.is_directory and self._hit(event.src_path):
            self._cb()

    def on_created(self, event):  # noqa: D102
        if not event.is_directory and self._hit(event.src_path):
            self._cb()

    def on_moved(self, event):  # noqa: D102
        dst = getattr(event, "dest_path", "")
        if dst and self._hit(dst):
            self._cb()
