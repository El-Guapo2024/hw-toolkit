"""KiCanvas — in-browser WebGL render of KiCad files (the openscad-wasm analog).

`kicad-cli` is the only way to get an SVG, and it's a ~1-2 s subprocess that
can render only a *saved file*. KiCanvas (https://kicanvas.org, by Thea Flowers)
is a vanilla-TS WebGL viewer that parses `.kicad_sch` / `.kicad_pcb` **in the
browser** — no KiCad, no kicad-cli, no roundtrip. That closes the speed gap for
any web host (and works in notebooks that allow module scripts).

This module emits the embed HTML with the schematic/PCB text **inline**, so the
source of truth stays the file we already wrote — KiCanvas just draws it fast.

    from hw_toolkit.kicad.kicanvas import kicanvas_html
    html = kicanvas_html(Path("board.kicad_sch").read_text())   # IPython HTML

Note: KiCanvas is alpha and parses KiCad 6+ files. In a sandboxed classic-
notebook output the module script may be blocked; VS Code / JupyterLab / a real
web host run it fine. SVG (`board.show()`) remains the universal fallback.
"""
from __future__ import annotations

import html as _html
from typing import Any, Literal

#: Hosted KiCanvas bundle. Override (e.g. self-hosted) via `cdn=` for offline.
DEFAULT_CDN = "https://kicanvas.org/kicanvas/kicanvas.js"

SourceType = Literal["schematic", "pcb"]


def kicanvas_html(
    source_text: str,
    *,
    type: SourceType = "schematic",
    controls: Literal["none", "basic", "full"] = "basic",
    theme: Literal["kicad", "witchhazel"] = "kicad",
    height_px: int = 500,
    cdn: str = DEFAULT_CDN,
) -> Any:
    """Wrap KiCad source text in a `<kicanvas-embed>` and return an IPython
    `HTML` object. `type` picks the source kind; `controls` = pan/zoom level.

    The source is embedded inline (escaped) as a `<kicanvas-source>` child, so
    nothing needs to be served — the file content travels with the HTML.
    """
    from IPython.display import HTML

    return HTML(kicanvas_html_str(
        source_text, type=type, controls=controls, theme=theme,
        height_px=height_px, cdn=cdn,
    ))


def kicanvas_html_str(
    source_text: str,
    *,
    type: SourceType = "schematic",
    controls: str = "basic",
    theme: str = "kicad",
    height_px: int = 500,
    cdn: str = DEFAULT_CDN,
) -> str:
    """Pure-string form of `kicanvas_html` (no IPython dep) — for web hosts.

    The loader script is included once per call; KiCanvas defines its custom
    elements idempotently, so repeated panes on a page are fine.
    """
    escaped = _html.escape(source_text)
    cdn_attr = _html.escape(cdn, quote=True)
    return (
        f'<script type="module" src="{cdn_attr}"></script>'
        f'<kicanvas-embed controls="{controls}" theme="{theme}" '
        f'style="display:block;height:{int(height_px)}px;width:100%">'
        f'<kicanvas-source type="{type}">{escaped}</kicanvas-source>'
        f'</kicanvas-embed>'
    )
