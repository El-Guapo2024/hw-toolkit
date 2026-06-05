"""Mode B IPC live render — poll/hash/change-detect logic.

Deterministic: a fake `get_doc_string` stands in for the KiCad IPC pull, and
renders are captured instead of displayed. `autostart=False` keeps the poll
thread out of the way so we drive `_pull` by hand.
"""
from __future__ import annotations

from hw_toolkit.kicad.live_ipc import LiveIpcView


def _view(get, renders):
    v = LiveIpcView(get, lambda b: b, poll_s=0.05, debounce_s=0.05,
                    autostart=False)
    # Capture renders instead of touching IPython.
    v._show = lambda *, first: renders.append(("first" if first else "upd",
                                               v._last_text))
    v._on_trigger = lambda: renders.append(("upd", v._last_text))
    return v


def test_initial_pull_renders_once():
    renders: list = []
    v = _view(lambda: "doc-A", renders)
    v._pull(initial=True)
    assert renders == [("first", "doc-A")]


def test_unchanged_doc_does_not_rerender():
    renders: list = []
    v = _view(lambda: "same", renders)
    v._pull(initial=True)
    v._pull(initial=False)   # identical hash → skip
    v._pull(initial=False)
    assert renders == [("first", "same")]


def test_changed_doc_rerenders():
    seq = iter(["v1", "v1", "v2", "v2", "v3"])
    renders: list = []
    v = _view(lambda: next(seq), renders)
    v._pull(initial=True)    # v1 → first
    v._pull(initial=False)   # v1 → skip
    v._pull(initial=False)   # v2 → upd
    v._pull(initial=False)   # v2 → skip
    v._pull(initial=False)   # v3 → upd
    assert renders == [("first", "v1"), ("upd", "v2"), ("upd", "v3")]


def test_ipc_error_surfaces_and_keeps_polling():
    state = {"fail": True}
    msgs: list = []

    def get():
        if state["fail"]:
            raise RuntimeError("API server off")
        return "recovered"

    renders: list = []
    v = _view(get, renders)
    v._update_text = lambda m: msgs.append(m)
    v._show_text_first = lambda m: msgs.append(m)
    v._pull(initial=True)               # error path, initial
    assert msgs and "API server off" in msgs[0]
    assert v._last_hash is None         # forces a render on reconnect
    state["fail"] = False
    v._pull(initial=False)              # reconnects → renders
    assert renders and renders[-1][1] == "recovered"


def test_stop_idempotent_without_start():
    v = LiveIpcView(lambda: "x", lambda b: b, autostart=False)
    v.stop(); v.stop()
    assert v._started is False
