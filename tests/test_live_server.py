"""LiveServer HTTP endpoints (VS Code live preview, no notebook).

Binds an ephemeral port (port=0), hits the routes over real HTTP, and checks
the version hash tracks file changes (the auto-reload signal).
"""
from __future__ import annotations

import urllib.request

from hw_toolkit.kicad.live_server import LiveServer


def _get(url: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=3) as r:
        return r.status, r.read()


def test_serves_page_doc_and_version(tmp_path):
    sch = tmp_path / "board.kicad_sch"
    sch.write_text("(kicad_sch (version 20230121))")
    srv = LiveServer(sch, port=0).start()
    try:
        base = srv.url
        # Page: KiCanvas embed + the doc source URL + poll loop.
        st, page = _get(base)
        assert st == 200
        text = page.decode()
        assert "<kicanvas-embed" in text
        assert "/doc.kicad_sch" in text
        assert "/version" in text

        # Doc route returns the file bytes verbatim.
        st, doc = _get(base + "doc.kicad_sch")
        assert st == 200 and doc == sch.read_bytes()

        # Version is stable until the file changes, then differs.
        _, v1 = _get(base + "version")
        _, v1b = _get(base + "version")
        assert v1 == v1b
        sch.write_text("(kicad_sch (version 20230121) (changed))")
        _, v2 = _get(base + "version")
        assert v2 != v1
    finally:
        srv.stop()


def test_unknown_route_404(tmp_path):
    sch = tmp_path / "b.kicad_sch"
    sch.write_text("(kicad_sch)")
    srv = LiveServer(sch, port=0).start()
    try:
        import urllib.error
        try:
            _get(srv.url + "nope")
            assert False, "expected 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.stop()


def test_pcb_ext_routes(tmp_path):
    pcb = tmp_path / "b.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    srv = LiveServer(pcb, port=0).start()
    try:
        _, page = _get(srv.url)
        assert "/doc.kicad_pcb" in page.decode()
        st, doc = _get(srv.url + "doc.kicad_pcb")
        assert st == 200 and doc == pcb.read_bytes()
    finally:
        srv.stop()
