"""KiCanvas embed HTML (Mode A in-browser render)."""
from __future__ import annotations

from hw_toolkit.kicad.kicanvas import DEFAULT_CDN, kicanvas_html_str


def test_embeds_source_inline_and_escaped():
    src = '(kicad_sch (version 20230121) (symbol "R&D <x>"))'
    html = kicanvas_html_str(src, type="schematic")
    # Loader + custom elements present.
    assert "<kicanvas-embed" in html and "<kicanvas-source" in html
    assert DEFAULT_CDN in html
    assert 'type="schematic"' in html
    # Source is inline, HTML-escaped (no raw < > & from the file).
    assert "&amp;" in html and "&lt;x&gt;" in html
    assert "R&D <x>" not in html  # raw unescaped must not leak


def test_pcb_type_and_controls_theme():
    html = kicanvas_html_str("(kicad_pcb)", type="pcb",
                             controls="full", theme="witchhazel")
    assert 'type="pcb"' in html
    assert 'controls="full"' in html
    assert 'theme="witchhazel"' in html


def test_custom_cdn_override():
    html = kicanvas_html_str("(kicad_sch)", cdn="/local/kicanvas.js")
    assert "/local/kicanvas.js" in html
    assert DEFAULT_CDN not in html
