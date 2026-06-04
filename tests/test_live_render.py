"""LiveView debounce + event-filter logic (the OpenSCAD-studio loop for KiCad).

Deterministic: drives `_on_event` / `_Handler` directly with a fake renderer,
so no kicad-cli, no IPython front-end, and no dependence on OS file-event
delivery timing (which is flaky under load).
"""
from __future__ import annotations

import time
import types
from pathlib import Path

from hw_toolkit.kicad.live_render import LiveView, _Handler


def _view(renders: list, *, debounce_s: float = 0.1) -> LiveView:
    v = LiveView(lambda: b"", lambda b: b, "/tmp/hwt_unused.sch",
                 debounce_s=debounce_s)
    # Replace the render slot with a recorder.
    v._safe_show = lambda: renders.append("R")
    return v


def test_single_event_renders_once():
    renders: list = []
    v = _view(renders)
    v._on_event()
    time.sleep(0.25)
    assert renders == ["R"]


def test_burst_debounced_to_one():
    renders: list = []
    v = _view(renders)
    # Three events inside the debounce window collapse to one render.
    v._on_event(); v._on_event(); v._on_event()
    time.sleep(0.25)
    assert renders == ["R"]


def test_separated_events_render_each():
    renders: list = []
    v = _view(renders, debounce_s=0.05)
    v._on_event(); time.sleep(0.15)
    v._on_event(); time.sleep(0.15)
    assert renders == ["R", "R"]


def test_handler_filters_by_filename():
    hits: list = []
    h = _Handler("doc.kicad_sch", lambda: hits.append(1))
    mod = types.SimpleNamespace(is_directory=False, src_path="/x/doc.kicad_sch")
    other = types.SimpleNamespace(is_directory=False, src_path="/x/noise.txt")
    adir = types.SimpleNamespace(is_directory=True, src_path="/x/doc.kicad_sch")
    h.on_modified(other)   # wrong name → ignored
    h.on_modified(adir)    # directory → ignored
    h.on_modified(mod)     # match → fires
    assert hits == [1]


def test_handler_on_moved_uses_dest():
    hits: list = []
    h = _Handler("doc.kicad_sch", lambda: hits.append(1))
    ev = types.SimpleNamespace(is_directory=False, src_path="/x/tmp",
                               dest_path="/x/doc.kicad_sch")
    h.on_moved(ev)
    assert hits == [1]


def test_render_error_does_not_kill_watcher():
    boom: list = []

    def bad_render():
        raise RuntimeError("render boom")

    v = LiveView(bad_render, lambda b: b, "/tmp/hwt_err.sch", debounce_s=0.1)
    v._update_text = lambda msg: boom.append(msg)
    v._safe_show()  # must swallow, not raise
    assert boom and "render boom" in boom[0]


def test_stop_is_idempotent():
    v = LiveView(lambda: b"", lambda b: b, "/tmp/hwt_x.sch")
    v.stop(); v.stop()  # no observer started → must not raise
    assert v._started is False
