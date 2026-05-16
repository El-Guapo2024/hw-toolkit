"""Pre-flight schema validation for `Schematic` Pydantic models.

Catches common authoring bugs (duplicate ids, dangling wires, missing
values) before kicad-cli or kicad-sch-api see the data. Used by the
eval pipeline, atomic edit tools, and PCB writer.

Originally part of `kicad_writer.py`; moved here when that module was
deleted in favor of the kicad-sch-api adapter (`ksa_writer.py`).
"""
from __future__ import annotations

from .schem_renderer import Schematic


def validate(schem: Schematic) -> list[str]:
    """Pre-flight schema check — fails fast with clear messages before
    kicad-cli sees the schematic. Returns a list of issues (empty = valid).

    Catches:
      - duplicate symbol ids
      - wire endpoints referencing unknown symbols or pins
      - passives missing a value
      - ICs without any pins
      - unknown symbol types
      - footprints not in `Library:Name` format
    """
    issues: list[str] = []

    seen_ids: set[str] = set()
    for s in schem.symbols:
        if s.id in seen_ids:
            issues.append(f"duplicate symbol id: {s.id!r}")
        seen_ids.add(s.id)

    valid_types = {"resistor", "capacitor", "inductor", "ground",
                   "vcc", "terminal", "diode", "ic", "kicad"}
    for s in schem.symbols:
        if s.type not in valid_types:
            issues.append(f"{s.id}: unknown type {s.type!r}")
        if s.type in ("resistor", "capacitor", "inductor") and not s.value:
            issues.append(f"{s.id}: passive missing 'value'")
        if s.type == "ic" and not s.pins:
            issues.append(f"{s.id}: ic has no pins defined")
        if s.type == "kicad" and not s.lib_id:
            issues.append(f"{s.id}: type=kicad requires lib_id")
        if s.footprint and ":" not in s.footprint:
            issues.append(
                f"{s.id}: footprint {s.footprint!r} should be 'Library:Name' format"
            )

    sym_by_id = {s.id: s for s in schem.symbols}
    for i, w in enumerate(schem.wires):
        for label, ep in [("from", w.from_), ("to", w.to)]:
            if ep.coord is not None:
                continue
            if not ep.block:
                issues.append(f"wire[{i}] {label}: no block or coord")
                continue
            sym = sym_by_id.get(ep.block)
            if not sym:
                issues.append(f"wire[{i}] {label}: unknown symbol {ep.block!r}")
                continue
            if ep.pin and sym.type == "ic":
                if not any(p.name == ep.pin for p in sym.pins):
                    issues.append(f"wire[{i}] {label}: pin {ep.pin!r} not on ic {ep.block}")

    return issues


def validate_pcb(schem: Schematic) -> list[str]:
    """Stricter check for PCB generation: every physical component must have
    a footprint. Run AFTER `validate()`.

    Net references (vcc/ground/terminal) don't need footprints — they're
    labels, not physical parts.
    """
    issues: list[str] = []
    physical_types = {"resistor", "capacitor", "inductor", "diode", "ic", "kicad"}
    for s in schem.symbols:
        if s.type in physical_types and not s.footprint:
            issues.append(
                f"{s.id}: physical part of type {s.type!r} needs 'footprint' "
                f"(e.g. 'Resistor_SMD:R_0805_2012Metric')"
            )
    return issues
