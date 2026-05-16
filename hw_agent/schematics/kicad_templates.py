"""KiCad lib_symbol templates for the writer.

Strategy: read symbol definitions verbatim from the system KiCad libraries
at runtime, transform the parent symbol name (e.g. "R" → "Device:R"), and
re-indent for embedding inside a .kicad_sch's `(lib_symbols ...)` block.

This guarantees byte-level structural equivalence with KiCad's stock library
so kicad-cli's `lib_symbol_mismatch` check stays clean.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from .kicad_paths import kicad_symbol_dir


def _system_path(lib_name: str) -> Path:
    return kicad_symbol_dir() / f"{lib_name}.kicad_sym"


@lru_cache(maxsize=None)
def _read_lib(lib_name: str) -> str:
    p = _system_path(lib_name)
    if not p.exists():
        raise FileNotFoundError(f"KiCad library not found: {p}")
    return p.read_text()


def _extract_symbol(lib_content: str, symbol_name: str) -> str:
    """Extract a `(symbol "<name>" ... )` block from a .kicad_sym file's text.

    The system libraries indent symbols at level 1 (one tab). We find the
    starting line `\\t(symbol "<name>"` and consume until the matching
    closing `\\t)` at the same indent level.
    """
    needle = f'\t(symbol "{symbol_name}"\n'
    start = lib_content.find(needle)
    if start == -1:
        raise KeyError(f"symbol {symbol_name!r} not found in library")
    # Walk lines until we hit `\t)` at base indent.
    end = start
    lines = lib_content[start:].splitlines(keepends=True)
    out_lines = []
    for line in lines:
        out_lines.append(line)
        if line == "\t)\n" or line == "\t)":
            break
    return "".join(out_lines)


def _reindent(block: str, base_indent: str = "    ") -> str:
    """Rewrite leading tabs as `base_indent` spaces, one tab → one indent unit."""
    out = []
    for line in block.splitlines():
        n = 0
        while n < len(line) and line[n] == "\t":
            n += 1
        out.append(base_indent * n + line[n:])
    return "\n".join(out)


def _rename_parent(block: str, old_name: str, new_name: str) -> str:
    """Replace the FIRST occurrence of `(symbol "<old>"` (the parent header)."""
    needle = f'(symbol "{old_name}"'
    repl = f'(symbol "{new_name}"'
    return block.replace(needle, repl, 1)


def _find_extends(block: str) -> Optional[str]:
    """If the symbol has `(extends "<parent>")`, return the parent name."""
    import re
    m = re.search(r'\(extends\s+"([^"]+)"\)', block)
    return m.group(1) if m else None


def _extract_subsymbols(parent_block: str, parent_name: str) -> str:
    """Pull `(symbol "<parent_name>_*_*" ...)` sub-blocks (geometry+pins) from
    the parent symbol's body. Returns the raw text including leading whitespace.
    """
    import re
    out = []
    # Sub-symbols are nested two-tab-indented inside the parent: \t\t(symbol "...
    pattern = re.compile(
        rf'(\t\t\(symbol "{re.escape(parent_name)}_\d+_\d+".*?\n\t\t\))',
        re.DOTALL,
    )
    return "\n".join(pattern.findall(parent_block))


def _embeddable(lib_name: str, symbol_name: str, embed_name: Optional[str] = None) -> str:
    """Get a system symbol ready to drop into a .kicad_sch lib_symbols block.

    If the symbol uses `(extends ...)` to inherit a parent's geometry, we
    splice the parent's sub-symbols (renamed to the derived prefix) into the
    derived block and remove the `(extends ...)` line. Each emitted lib_symbol
    is then self-contained — no cross-symbol dependencies inside the schematic.
    """
    lib_content = _read_lib(lib_name)
    raw = _extract_symbol(lib_content, symbol_name)
    target = embed_name or f"{lib_name}:{symbol_name}"

    parent_name = _find_extends(raw)
    if parent_name:
        parent_raw = _extract_symbol(lib_content, parent_name)
        subsyms = _extract_subsymbols(parent_raw, parent_name)
        # Rename `(symbol "AP1117-15_0_1"` → `(symbol "AMS1117-3.3_0_1"` etc.
        subsyms = subsyms.replace(f'"{parent_name}_', f'"{symbol_name}_')
        # Strip the (extends "...") line and splice sub-symbols before the closing.
        import re
        raw = re.sub(r'\t\t\(extends\s+"[^"]+"\)\n', '', raw)
        # Insert sub-symbols before the final `\t)` that closes the parent symbol.
        raw = raw.rstrip()
        if raw.endswith('\t)'):
            raw = raw[:-2] + subsyms + '\n\t)\n'

    raw = _rename_parent(raw, symbol_name, target)
    return _reindent(raw, "    ")


# ─── Public lookup ─────────────────────────────────────────────────────────

# Maps lib_id → loader. Loaders are deferred so a missing KiCad install only
# fails when the symbol is actually used.

_PASSIVE_LOADERS = {
    "Device:R": lambda: _embeddable("Device", "R"),
    "Device:C": lambda: _embeddable("Device", "C"),
    "Device:L": lambda: _embeddable("Device", "L"),
}


def stock_passive(lib_id: str) -> str:
    return _PASSIVE_LOADERS[lib_id]()


def is_stock_passive(lib_id: str) -> bool:
    """True for non-power passives. GND/+5V/etc. are handled via stock_power_rail
    so they participate in PWR_FLAG / rail tracking instead."""
    return lib_id in _PASSIVE_LOADERS


# Stock power rails (canonical KiCad names). Anything else gets emitted as
# `hwagent:NAME` so KiCad doesn't compare against the system `power` lib.
_STOCK_POWER_RAILS = {
    "+5V", "+3V3", "+3.3V", "+12V", "+9V", "+1V8", "+1V2",
    "VCC", "VDD", "VEE", "VSS", "+BATT", "GND", "GNDA", "GNDD",
}


def is_stock_power_rail(name: str) -> bool:
    return name in _STOCK_POWER_RAILS


def stock_power_rail(name: str) -> str:
    return _embeddable("power", name)


# Custom power flag for non-stock rails (VBAT, VOUT, etc.). Embeds under the
# `hwagent:` namespace so KiCad treats it as project-local and doesn't try
# to validate against the system power lib.
def custom_power_flag(name: str) -> str:
    """Generate a hwagent-namespaced power flag with arrow glyph."""
    return f'''    (symbol "hwagent:{name}" (power) (pin_numbers (hide yes)) (pin_names (offset 0) (hide yes))
      (exclude_from_sim no)
      (in_bom yes)
      (on_board yes)
      (property "Reference" "#PWR" (at 0 -3.81 0)
        (effects (font (size 1.27 1.27)) (hide yes))
      )
      (property "Value" "{name}" (at 0 3.556 0)
        (effects (font (size 1.27 1.27)))
      )
      (property "Footprint" "" (at 0 0 0)
        (effects (font (size 1.27 1.27)) (hide yes))
      )
      (property "Datasheet" "" (at 0 0 0)
        (effects (font (size 1.27 1.27)) (hide yes))
      )
      (symbol "{name}_0_1"
        (polyline (pts (xy -0.762 1.27) (xy 0 2.54))
          (stroke (width 0) (type default)) (fill (type none))
        )
        (polyline (pts (xy 0 2.54) (xy 0.762 1.27))
          (stroke (width 0) (type default)) (fill (type none))
        )
        (polyline (pts (xy 0 0) (xy 0 2.54))
          (stroke (width 0) (type default)) (fill (type none))
        )
      )
      (symbol "{name}_1_1"
        (pin power_in line (at 0 0 90) (length 0)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27))))
        )
      )
    )'''
