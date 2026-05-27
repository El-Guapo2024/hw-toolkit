"""SubsystemPick → KiCad symbol-field projection.

KiCad symbol fields are a DERIVED VIEW of the locked SubsystemPick.
Never hand-edit them in eeschema. Regenerate from the bundle.

Field names follow KiCad's KLC (KiCad Library Convention):
  - Reference, Value: standard.
  - MPN, Manufacturer, LCSC, Datasheet, Package: project convention,
    matches what BOM exporters look for.

Reference designators follow KiCad's `<letter><number>` rule (U1, R7, J3).
Stable numbering is keyed off SubsystemPick.id, ordered as it appears
in the bundle, so a regen doesn't shuffle refdes assignments unless the
bundle itself reorders.
"""
from __future__ import annotations

from hw_toolkit.core import ResearchBundle, SubsystemPick


def chosen_part_to_kicad_fields(pick: SubsystemPick, refdes: str) -> dict[str, str]:
    """Return the KiCad symbol field map for a SubsystemPick.

    Empty strings are emitted (not omitted) so the field is created in
    the symbol; an absent value is visible to the user instead of being
    silently dropped. Caller supplies the refdes from
    `refdes_map_for_bundle` so numbering is consistent across the build.
    """
    return {
        "Reference":    refdes,
        "Value":        pick.mpn,
        "MPN":          pick.mpn,
        "Manufacturer": pick.manufacturer,
        "LCSC":         pick.lcsc or "",
        "Datasheet":    pick.datasheet_url,
        "Package":      pick.package,
        "PriceUSD":     f"{pick.price_usd:.4f}",
    }


def refdes_map_for_bundle(bundle: ResearchBundle) -> dict[str, str]:
    """Return {SubsystemPick.id: "U1"} per the bundle's order.

    Numbers are assigned per refdes prefix in order of first appearance,
    starting at 1. Two buck converters → U1, U2; first connector → J1.
    """
    counters: dict[str, int] = {}
    out: dict[str, str] = {}
    for pick in bundle.subsystems:
        prefix = _prefix_for_category(pick.category)
        counters[prefix] = counters.get(prefix, 0) + 1
        out[pick.id] = f"{prefix}{counters[prefix]}"
    return out


_CATEGORY_PREFIX: dict[str, str] = {
    "buck_converter":   "U",
    "ldo":              "U",
    "mcu_module":       "U",
    "mcu":              "U",
    "sensor_i2c":       "U",
    "sensor":           "U",
    "motor_driver":     "U",
    "connector":        "J",
    "fuse":             "F",
    "led":              "D",
    "crystal":          "Y",
}


def _prefix_for_category(category: str) -> str:
    """Map a SubsystemPick.category to a KiCad refdes prefix.

    Unknown categories fall back to "U" — every subsystem-level pick is
    treated as an IC unless explicitly known otherwise. Discretes (R/C/L)
    are not subsystem picks; they live inside subsystem templates.
    """
    return _CATEGORY_PREFIX.get(category, "U")
