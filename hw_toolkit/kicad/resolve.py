"""Resolve a part to a real KiCad symbol `lib_id` + footprint.

The planner places a real library symbol when a SubsystemPick carries a
`lib_id` (see Phase 1). This module decides that lib_id automatically
from the part's category / mpn / package, so the common parts resolve to
real symbols without the caller spelling out the lib_id. Anything we
can't resolve returns `(None, None)` and the planner falls back to
synthesizing a placeholder — the historical behavior.

Every IC lib_id is validated against the actual library file via
`lib.load_symbol` before being returned: `find_kicad_lib`-style checks
only prove the `.kicad_sym` FILE exists, not that the symbol lives inside
it (e.g. "TPS54331D" resolves the Regulator_Switching file but is not a
symbol in it). A catalog typo therefore degrades to synthesis, never to a
broken placement.
"""
from __future__ import annotations

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
}

# Curated MPN → KiCad symbol lib_id. Seed set; extend as parts recur.
# Validated at resolve time, so an entry that doesn't actually exist in
# the installed libs is silently ignored (falls back to synthesis).
_IC_CATALOG: dict[str, str] = {
    "TPS54302":      "Regulator_Switching:TPS54302",
    "TPS54308":      "Regulator_Switching:TPS54308",
    "STM32F042K6Tx": "MCU_ST_STM32F0:STM32F042K6Tx",
    "STM32F042C6Tx": "MCU_ST_STM32F0:STM32F042C6Tx",
}

# IC package → KiCad footprint. Shared with the planner's older map; kept
# here so the resolver is self-contained.
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
}


def _symbol_exists(lib_id: str) -> bool:
    """True iff `lib_id` resolves to a real symbol in an installed library.

    Validates the SYMBOL, not just the library file — load_symbol raises
    FileNotFoundError (no lib) or KeyError/ValueError (no such symbol).
    """
    try:
        kicad_lib.load_symbol(lib_id)
        return True
    except Exception:
        return False


def resolve_kicad_part(
    mpn: str,
    category: str,
    package: str,
) -> tuple[str | None, str | None]:
    """Return `(lib_id, footprint)` for a part, or `(None, None)`.

    Passives resolve by category to Device:R/C/L with an SMD footprint.
    ICs resolve via the curated MPN catalog, validated against the
    installed libraries. Unknown parts return `(None, None)` so the
    planner synthesizes a placeholder.
    """
    if category in _PASSIVE_LIB:
        lib_id = _PASSIVE_LIB[category]
        # Device symbols are always installed; guard anyway.
        if not _symbol_exists(lib_id):
            return None, None
        return lib_id, _PASSIVE_FOOTPRINT.get((category, package))

    lib_id = _IC_CATALOG.get(mpn)
    if lib_id and _symbol_exists(lib_id):
        return lib_id, _IC_FOOTPRINT.get(package)
    return None, None
