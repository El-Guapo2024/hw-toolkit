"""Mode B live render — mirror an OPEN KiCad over IPC (source of truth = GUI).

`LiveView` (live_render.py) is Mode A: the agent writes the file, we watch the
file. That's render-on-write, not "live editing." Mode B is the real thing —
a human (or agent) edits **through eeschema** and we mirror the **unsaved
in-memory** document.

`kicad-cli` can't see unsaved state, but the IPC API can: live-edit-mcp's
`live_get_as_string` returns the current in-memory `.kicad_sch` text without a
Ctrl+S. So Mode B = **poll that string**, hash it, and on change re-render
(KiCanvas in-browser, or kicad-cli via a temp file). No file event exists for
in-memory edits, so this is poll-based, not watchdog-based.

    from hw_toolkit.kicad.live_ipc import LiveIpcView
    from hw_toolkit.kicad.kicanvas import kicanvas_html

    view = LiveIpcView(
        get_doc_string=client.live_get_as_string,   # IPC pull
        to_html=lambda b: kicanvas_html(b.decode()),
        poll_s=1.0,
    )                                                # auto-starts + displays
    ...
    view.stop()

Needs KiCad running with Preferences → API server enabled. If the pull raises
(KiCad closed / IPC off) the pane shows the error and keeps polling, so it
reconnects when KiCad comes back.
"""
from __future__ import annotations

import hashlib
import threading
from typing import Callable

from hw_toolkit.kicad.live_render import _LivePane


class LiveIpcView(_LivePane):
    """Self-refreshing pane mirroring an open KiCad document over IPC.

    `get_doc_string` is the IPC pull (e.g. `live_get_as_string`) returning the
    current in-memory document text. `to_html` turns that text's bytes into a
    displayable object (KiCanvas or SVG). The view polls every `poll_s`, hashes
    the text, and only re-renders when it changes.
    """

    def __init__(
        self,
        get_doc_string: Callable[[], str],
        to_html: Callable[[bytes], object],
        *,
        poll_s: float = 1.0,
        debounce_s: float = 0.2,
        autostart: bool = True,
    ) -> None:
        # The base render just hands back the last-polled text; the poll loop
        # is what fetches over IPC (so a render never triggers a second pull).
        super().__init__(self._current_bytes, to_html, debounce_s=debounce_s)
        self._get = get_doc_string
        self._poll_s = poll_s
        self._last_text: str = ""
        self._last_hash: str | None = None
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        if autostart:
            self.start()

    # -- render source ---------------------------------------------------
    def _current_bytes(self) -> bytes:
        return self._last_text.encode("utf-8")

    # -- lifecycle -------------------------------------------------------
    def start(self) -> "LiveIpcView":
        """Pull once, show, then poll for in-memory changes."""
        self._pull(initial=True)
        self._stop_evt.clear()
        t = threading.Thread(target=self._loop, name="hwt-live-ipc", daemon=True)
        t.start()
        self._thread = t
        self._started = True
        return self

    def stop(self) -> None:
        """Stop polling (pane keeps its last render)."""
        self._stop_evt.set()
        self._cancel_timer()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self._started = False

    # -- internals -------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop_evt.wait(self._poll_s):
            self._pull(initial=False)

    def _pull(self, *, initial: bool) -> None:
        """Fetch the doc over IPC; on change, (re)render. Errors surface in the
        pane but never stop the poll loop."""
        try:
            text = self._get()
        except Exception as exc:
            msg = f"⚠️ KiCad IPC unreachable: {exc}"
            if initial:
                self._show_text_first(msg)
            else:
                self._update_text(msg)
            self._last_hash = None  # force a render once it reconnects
            return

        h = hashlib.sha1(text.encode("utf-8")).hexdigest()
        if h == self._last_hash:
            return
        self._last_hash = h
        self._last_text = text
        if initial:
            self._show(first=True)
        else:
            self._on_trigger()  # debounced re-render

    def _show_text_first(self, msg: str) -> None:
        from IPython.display import HTML, display

        display(HTML(f"<pre>{msg}</pre>"), display_id=self._display_id)

    def __repr__(self) -> str:
        state = "polling" if self._started else "stopped"
        return f"<LiveIpcView {state} every {self._poll_s}s>"


def watch_kicad_ipc(
    kicad_sch: str,
    *,
    render: str = "kicanvas",
    poll_s: float = 1.0,
    height_px: int = 520,
) -> LiveIpcView:
    """Mirror an OPEN eeschema document in a live pane (notebook convenience).

    Pulls the **unsaved in-memory** `.kicad_sch` text over KiCad IPC
    (`sch_ipc.get_as_string`) every `poll_s` and re-renders on change. Needs
    KiCad running with Preferences → API server enabled.

    `render="kicanvas"` (default) draws in-browser (instant, interactive);
    `render="svg"` writes a temp file and uses kicad-cli (universal fallback).
    """
    from pathlib import Path

    from hw_toolkit.kicad import sch_ipc

    path = Path(kicad_sch).resolve()

    def get_doc() -> str:
        res = sch_ipc.get_as_string(path)
        if not res.get("ok"):
            raise RuntimeError(res.get("error", "get_as_string failed"))
        return res["schematic_text"]

    if render == "kicanvas":
        from hw_toolkit.kicad.kicanvas import kicanvas_html

        to_html = lambda b: kicanvas_html(  # noqa: E731
            b.decode("utf-8"), type="schematic", height_px=height_px
        )
    elif render == "svg":
        import tempfile

        from hw_toolkit.kicad import render_sch_svg

        def to_html(b: bytes):
            with tempfile.NamedTemporaryFile(
                suffix=".kicad_sch", delete=False
            ) as f:
                f.write(b)
                tmp = Path(f.name)
            from IPython.display import HTML

            return HTML(render_sch_svg(tmp).read_bytes().decode("utf-8",
                                                                "replace"))
    else:
        raise ValueError(f"render must be 'kicanvas' or 'svg', got {render!r}")

    return LiveIpcView(get_doc, to_html, poll_s=poll_s)
