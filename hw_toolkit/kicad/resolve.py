"""Resolve a part to a real KiCad symbol `lib_id` + footprint.

The planner places a real library symbol when a SubsystemPick carries a
`lib_id` (see Phase 1). This module decides that lib_id automatically by
**querying the installed KiCad libraries** — not a hardcoded catalog. For
most stock parts the KiCad symbol name IS the MPN ("AP2112K-3.3",
"TPS54302", "SHT31-DIS"), so an exact index lookup resolves thousands of
parts with zero maintenance.

A thin normalization layer handles the common mismatch: distributor MPNs
carry a packing/temperature suffix that KiCad collapses to "x" (e.g.
"STM32F042K6T6" → symbol "STM32F042K6Tx"). `_ALIASES` covers the few
remaining quirks where the distributor name and the symbol name diverge
in a way normalization can't derive.

Anything still unresolved returns `(None, None)` → the planner synthesizes
a placeholder (historical behavior). Every match goes through the live
index, so a stale mapping can't place a symbol that isn't installed.
"""
from __future__ import annotations

import re

from hw_toolkit.kicad import lib as kicad_lib

# Passive category → KiCad generic device symbol.
_PASSIVE_LIB: dict[str, str] = {
    "resistor":  "Device:R",
    "capacitor": "Device:C",
    "inductor":  "Device:L",
}

# (passive family, package) → SMD footprint. Two-terminal chip packages.
_PASSIVE_FOOTPRINT: dict[tuple[str, str], str] = {
    ("resistor", "0402"):  "Resistor_SMD:R_0402_1005Metric",
    ("resistor", "0603"):  "Resistor_SMD:R_0603_1608Metric",
    ("resistor", "0805"):  "Resistor_SMD:R_0805_2012Metric",
    ("resistor", "1206"):  "Resistor_SMD:R_1206_3216Metric",
    ("capacitor", "0402"): "Capacitor_SMD:C_0402_1005Metric",
    ("capacitor", "0603"): "Capacitor_SMD:C_0603_1608Metric",
    ("capacitor", "0805"): "Capacitor_SMD:C_0805_2012Metric",
    ("capacitor", "1206"): "Capacitor_SMD:C_1206_3216Metric",
    ("inductor", "0603"):  "Inductor_SMD:L_0603_1608Metric",
    ("inductor", "0805"):  "Inductor_SMD:L_0805_2012Metric",
    ("inductor", "1206"):  "Inductor_SMD:L_1206_3216Metric",
    ("inductor", "1210"):  "Inductor_SMD:L_1210_3225Metric",
}

# Distributor MPN → KiCad symbol name, only for cases normalization can't
# derive. Keep this SMALL — most parts resolve by exact name or the ST
# suffix rule below. (Not a catalog: values are symbol names, validated
# against the live index like everything else.)
_ALIASES: dict[str, str] = {
    "TCAN330GD": "TCAN330G",   # -D package code dropped in the symbol
    "AS5047P":   "AS5047D",    # KiCad ships the D variant only
}

# IC package → KiCad footprint. Best-effort; an unknown package just
# yields no footprint (the real symbol still places + ERCs clean).
_IC_FOOTPRINT: dict[str, str] = {
    "SOIC-8":   "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
    "SOIC-14":  "Package_SO:SOIC-14_3.9x8.7mm_P1.27mm",
    "SOIC-16":  "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm",
    "TSSOP-8":  "Package_SO:TSSOP-8_3x3mm_P0.65mm",
    "TSSOP-14": "Package_SO:TSSOP-14_4.4x5mm_P0.65mm",
    "TSSOP-16": "Package_SO:TSSOP-16_4.4x5mm_P0.65mm",
    "SOT-23":   "Package_TO_SOT_SMD:SOT-23",
    "SOT-23-5": "Package_TO_SOT_SMD:SOT-23-5",
    "SOT-23-6": "Package_TO_SOT_SMD:SOT-23-6",
    "SOT-223":  "Package_TO_SOT_SMD:SOT-223",
    "DFN-8":    "Package_DFN_QFN:DFN-8-1EP_3x3mm_P0.65mm_EP1.5x2.4mm",
    "QFN-16":   "Package_DFN_QFN:QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm",
    "QFN-32":   "Package_DFN_QFN:QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm",
    "LQFP-32":  "Package_QFP:LQFP-32_7x7mm_P0.8mm",
    "LQFP-48":  "Package_QFP:LQFP-48_7x7mm_P0.5mm",
}

# ST packing/temperature suffix → "x": STM32F042K6T6 → STM32F042K6Tx.
_ST_SUFFIX_RE = re.compile(r"([TUVHJ])\d$")


def _symbol_candidates(mpn: str):
    """Yield symbol-name guesses for an MPN, most specific first."""
    mpn = mpn.strip()
    yield mpn
    if mpn in _ALIASES:
        yield _ALIASES[mpn]
    normalized = _ST_SUFFIX_RE.sub(r"\1x", mpn)
    if normalized != mpn:
        yield normalized


def resolve_kicad_part(
    mpn: str,
    category: str,
    package: str,
) -> tuple[str | None, str | None]:
    """Return `(lib_id, footprint)` for a part, or `(None, None)`.

    Passives resolve by category to Device:R/C/L with an SMD footprint.
    ICs resolve by looking up the MPN (and a couple of normalized forms)
    in the live installed-library index. Unknown parts return
    `(None, None)` so the planner synthesizes a placeholder.
    """
    if category in _PASSIVE_LIB:
        lib_id = _PASSIVE_LIB[category]
        if kicad_lib.find_symbol_lib_id(lib_id.split(":", 1)[1]) is None:
            return None, None
        return lib_id, _PASSIVE_FOOTPRINT.get((category, package))

    for name in _symbol_candidates(mpn):
        lib_id = kicad_lib.find_symbol_lib_id(name)
        if lib_id:
            return lib_id, _IC_FOOTPRINT.get(package)
    return None, None
