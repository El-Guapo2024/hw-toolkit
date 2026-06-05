"""Live render in a VS Code tab (no notebook) — KiCanvas page + auto-reload.

`board.live()` renders into a Jupyter cell. This serves the same KiCanvas view
as a tiny **localhost page** so you can open it in VS Code's **Simple Browser**
(Cmd-Shift-P → "Simple Browser: Show" → the printed URL) — a live preview tab
docked next to your code, no notebook required.

Loop: the page polls `/version` (file hash); when the agent's `write_kicad()`
changes the file, the page reloads and KiCanvas re-parses it in-browser. File
stays the source of truth; render is instant WebGL (no kicad-cli).

    srv = serve_live("board.kicad_sch")     # prints the URL
    ...
    srv.stop()
"""
from __future__ import annotations

import hashlib
import http.server
import threading
from pathlib import Path

from hw_toolkit.kicad.kicanvas import DEFAULT_CDN

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>hw_toolkit live — {name}</title>
<style>html,body{{margin:0;height:100%;background:#fff}}</style>
<script type="module" src="{cdn}"></script></head>
<body>
<kicanvas-embed controls="full" theme="kicad"
  style="display:block;height:100vh;width:100vw">
  <kicanvas-source src="/doc{ext}"></kicanvas-source>
</kicanvas-embed>
<script>
let last=null;
async function poll(){{
  try{{
    const v=await (await fetch('/version',{{cache:'no-store'}})).text();
    if(last!==null && v!==last) location.reload();
    last=v;
  }}catch(e){{}}
  setTimeout(poll,{poll_ms});
}}
poll();
</script></body></html>"""


class LiveServer:
    """A running localhost preview server for one KiCad file."""

    def __init__(self, path: str | Path, *, port: int = 8731,
                 poll_ms: int = 600, cdn: str = DEFAULT_CDN) -> None:
        self.path = Path(path).resolve()
        self.poll_ms = poll_ms
        self.cdn = cdn
        self._ext = self.path.suffix  # .kicad_sch / .kicad_pcb
        self._httpd = http.server.ThreadingHTTPServer(
            ("127.0.0.1", port), self._make_handler())
        self.port = self._httpd.server_address[1]
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def _version(self) -> str:
        try:
            return hashlib.sha1(self.path.read_bytes()).hexdigest()
        except OSError:
            return "missing"

    def _page(self) -> bytes:
        return _PAGE.format(
            name=self.path.name, cdn=self.cdn, ext=self._ext,
            poll_ms=self.poll_ms,
        ).encode("utf-8")

    def _make_handler(self):
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence access logs
                pass

            def _send(self, body: bytes, ctype: str):
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                route = self.path.split("?", 1)[0]
                if route == "/":
                    self._send(server._page(), "text/html; charset=utf-8")
                elif route == "/version":
                    self._send(server._version().encode(), "text/plain")
                elif route == f"/doc{server._ext}":
                    try:
                        self._send(server.path.read_bytes(),
                                   "text/plain; charset=utf-8")
                    except OSError:
                        self.send_error(404)
                else:
                    self.send_error(404)

        return Handler

    def start(self) -> "LiveServer":
        t = threading.Thread(target=self._httpd.serve_forever,
                             name="hwt-live-server", daemon=True)
        t.start()
        self._thread = t
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def __repr__(self) -> str:
        return f"<LiveServer {self.url} → {self.path.name}>"


def serve_live(path: str | Path, *, port: int = 8731,
               poll_ms: int = 600) -> LiveServer:
    """Start a live KiCanvas preview server for `path` and print how to open it
    in VS Code's Simple Browser. Returns the `LiveServer` (call `.stop()`)."""
    srv = LiveServer(path, port=port, poll_ms=poll_ms).start()
    print(f"Live preview: {srv.url}")
    print("VS Code: Cmd-Shift-P → 'Simple Browser: Show' → paste the URL.")
    return srv
