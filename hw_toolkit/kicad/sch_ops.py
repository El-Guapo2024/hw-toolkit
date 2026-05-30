"""Atomic mutations on `.kicad_sch` files via kicad-sch-api.

Replaces `json_ops.py` (which mutated `.schem.json` via our hand-rolled
writer). Each function loads the `.kicad_sch`, mutates via kicad-sch-api,
saves back. No JSON intermediate.

Why kicad-sch-api:
  - Lossless round-trip after one normalization pass — verified on FPGA
  - Single-edit diffs are 1-line (vs huge churn for re-emit-everything)
  - Pure Python, works with KiCad off
  - Active maintenance via circuit-synth project

Wire-endpoint string format (carried over from the JSON era for
back-compat with the agent-facing API):
  - `"U1.VCC"`     → pin VCC on symbol U1 (looked up via kicad_lib)
  - `"VCC1"`       → bare net anchor (power/ground/terminal symbol position)
  - `"@40.5,60"`   → explicit coordinate (mm)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import kicad_sch_api as ksa


# ─── Loader (with validation bypass) ──────────────────────────────────────

def _load(path: Path):
    """Load `.kicad_sch` via kicad-sch-api.

    Bypasses kicad-sch-api's strict reference validator — it rejects KiCad's
    own `#PWR`/`#FLG` convention (multiple symbols sharing a ref by design,
    UUIDs disambiguate). KiCad's eeschema accepts these refs without
    complaint, so we override.
    """
    sch = ksa.load_schematic(str(path))
    sch.validate = lambda: []  # type: ignore[method-assign]
    return sch


def _save(sch, path: Path) -> None:
    """Save with validation bypass already in place."""
    sch.save(str(path))


# ─── Endpoint resolution ──────────────────────────────────────────────────

_COORD_RE = re.compile(r"^@\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*$")


def _resolve_pin_number(sch, ref: str, pin_name_or_num: str) -> str:
    """Map "VCC" → "D4" using the symbol's lib pin table.

    kicad-sch-api works on pin numbers (KiCad's per-package pin IDs:
    "1", "2", "A1", "D4"). The agent-facing API takes pin *names* like
    "VCC" or "GND" because that's what's readable. Bridge via kicad_lib.
    """
    # If it already looks like a pin number that kicad-sch-api knows,
    # try it directly first — list_component_pins is authoritative.
    pins = sch.list_component_pins(ref) or []
    pin_numbers = {n for n, _ in pins}
    if pin_name_or_num in pin_numbers:
        return pin_name_or_num

    # Fall back to kicad_lib to look up by name → number.
    comp = sch.components.get(ref)
    if comp is None:
        raise ValueError(f"unknown symbol {ref!r}")
    lib_id = comp.lib_id
    try:
        from hw_toolkit.kicad.lib import load_symbol
        lib = load_symbol(lib_id)
    except Exception as e:
        raise ValueError(
            f"cannot resolve pin {pin_name_or_num!r} on {ref} "
            f"(lib_id={lib_id!r} did not load: {e})"
        )

    for p in lib.pins:
        if p.name == pin_name_or_num or p.number == pin_name_or_num:
            return p.number
    raise KeyError(
        f"pin {pin_name_or_num!r} not found on {ref} ({lib_id}). "
        f"Available pin numbers: {sorted(pin_numbers)[:8]}..."
    )


def _resolve_endpoint(sch, ep_str: str) -> tuple[float, float]:
    """Resolve agent-facing endpoint string to an absolute (x, y) in mm."""
    ep_str = ep_str.strip()
    m = _COORD_RE.match(ep_str)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    if "." in ep_str:
        ref, pin_name = ep_str.split(".", 1)
        ref = ref.strip()
        pin_name = pin_name.strip()
        pin_number = _resolve_pin_number(sch, ref, pin_name)
        pos = sch.get_component_pin_position(ref, pin_number)
        if pos is None:
            raise ValueError(
                f"pin {pin_name!r} on {ref!r} resolved to number "
                f"{pin_number!r} but kicad-sch-api couldn't position it"
            )
        return (pos.x, pos.y)
    # Bare ref → use its symbol's anchor position
    comp = sch.components.get(ep_str)
    if comp is None:
        raise ValueError(f"unknown symbol {ep_str!r}")
    pos = comp.position
    return (pos.x, pos.y)


# ─── Add: components ───────────────────────────────────────────────────────

def _register_system_lib(sch, lib_id: str) -> None:
    """Make `kicad_sch_api` aware of a system/3rd-party symbol library.

    kicad_sch_api only resolves a handful of bundled libraries out of the
    box (Device, Connector_Generic, ...). For anything else
    (Regulator_Switching, MCU_ST_STM32F0, ...) we resolve the `.kicad_sym`
    file via hw_toolkit's own lib finder and register it. No-op if the
    library file can't be located — `components.add` then raises its own
    clear LibraryError.
    """
    from hw_toolkit.kicad import lib as kicad_lib

    lib_name = lib_id.split(":", 1)[0]
    try:
        lib_path = kicad_lib._find_lib(lib_name)
    except FileNotFoundError:
        return
    sch.library.add_library_path(str(lib_path))


def add_ic(path: Path, ref: str, lib_id: str, at: tuple[float, float],
           *, value: Optional[str] = None, footprint: Optional[str] = None,
           rotation: float = 0.0) -> dict:
    """Place a KiCad-library IC."""
    sch = _load(path)
    _register_system_lib(sch, lib_id)
    kwargs = dict(
        lib_id=lib_id, reference=ref, position=at,
        footprint=footprint, rotation=rotation,
    )
    if value is not None:
        kwargs["value"] = value
    comp = sch.components.add(**kwargs)
    _save(sch, path)
    return {"id": ref, "lib_id": lib_id, "value": value,
            "at": list(at), "uuid": comp.uuid}


def add_custom_ic(
    path: Path,
    ref: str,
    name: str,
    at: tuple[float, float],
    pins: list[dict],
    *,
    size: tuple[float, float] = (15.0, 12.0),
    footprint: Optional[str] = None,
    rotation: float = 0.0,
) -> dict:
    """Place a custom IC whose symbol isn't in any KiCad standard library.

    The chip's pin layout is defined inline by the caller (typically the
    agent fetches pin data from a datasheet, JLCPCB DB, or custom source).
    A lib_symbol entry is synthesized into the project's `hwagent.kicad_sym`
    library, then placed via kicad-sch-api with `lib_id="hwagent:<name>"`.

    Use this when:
      - cse_get_kicad / standard KiCad libs don't have the chip
      - You have pin name + position + side data from another source
      - You need the chip on the schematic NOW

    Args:
        path: target `.kicad_sch`
        ref: reference designator (e.g. "U1")
        name: chip name (becomes lib_id "hwagent:<name>", e.g. "SY8205FCC")
        at: (x, y) placement in mm
        pins: list of {"name": "VIN", "at": (x, y), "side": "left"} dicts.
              `at` coords are absolute mm in the schematic; the synthesizer
              converts to lib-local Y-up.
        size: (width, height) of the body rectangle in mm
        footprint: optional `Library:Footprint` string
        rotation: degrees (0/90/180/270)
    """
    from hw_toolkit.kicad.lib_writer import (
        synthesize_ic_lib_symbol, append_to_hwagent_lib,
    )

    # 1. Synthesize lib_symbol block + append to hwagent.kicad_sym
    block = synthesize_ic_lib_symbol(
        name=name,
        pins=pins,
        size=size,
        reference_prefix=ref[0] if ref else "U",
        footprint=footprint or "",
        center=at,
    )
    lib_path = append_to_hwagent_lib(path.parent, block)

    # 2. Load the .kicad_sch, register the project library, place the IC
    sch = _load(path)
    sch.library.add_library_path(str(lib_path))
    comp = sch.components.add(
        lib_id=f"hwagent:{name}", reference=ref,
        value=name, position=at,
        rotation=rotation, footprint=footprint,
    )
    _save(sch, path)

    return {
        "id": ref, "name": name, "lib_id": f"hwagent:{name}",
        "at": list(at), "uuid": comp.uuid,
        "pin_count": len(pins),
        "lib_path": str(lib_path),
    }


def _add_passive(path: Path, ref: str, lib_id: str, value: str,
                 at: tuple[float, float], orient: str) -> dict:
    """Shared body for capacitor/resistor/inductor."""
    if orient not in ("up", "down", "left", "right"):
        raise ValueError(f"orient must be up/down/left/right, got {orient!r}")
    rotation = {"right": 0, "down": 90, "left": 180, "up": 270}[orient]
    sch = _load(path)
    comp = sch.components.add(
        lib_id=lib_id, reference=ref, value=value,
        position=at, rotation=rotation,
    )
    _save(sch, path)
    return {"id": ref, "lib_id": lib_id, "value": value,
            "at": list(at), "orient": orient, "uuid": comp.uuid}


def add_capacitor(path: Path, ref: str, value: str,
                  at: tuple[float, float], orient: str = "right") -> dict:
    return _add_passive(path, ref, "Device:C", value, at, orient)


def add_resistor(path: Path, ref: str, value: str,
                 at: tuple[float, float], orient: str = "right") -> dict:
    return _add_passive(path, ref, "Device:R", value, at, orient)


def add_inductor(path: Path, ref: str, value: str,
                 at: tuple[float, float], orient: str = "right") -> dict:
    return _add_passive(path, ref, "Device:L", value, at, orient)


def add_power(path: Path, ref: str, at: tuple[float, float],
              label: str = "VCC") -> dict:
    """Place a power-rail flag. `label` controls which power symbol gets used.

    Common labels: "3V3", "5V", "12V", "VCC". Falls back to power:VCC if
    the requested rail isn't in the standard library.
    """
    # KiCad's power library has +3V3 / +5V / +12V / +VCC / VBUS / etc.
    # Map the user's friendly label to a lib_id.
    pretty = label.upper().replace("V", "V").strip("+ ")
    candidates = [
        f"power:+{pretty}",
        f"power:{pretty}",
        "power:VCC",
    ]
    sch = _load(path)
    for lib_id in candidates:
        try:
            comp = sch.components.add(
                lib_id=lib_id, reference=ref, value=label,
                position=at,
            )
            _save(sch, path)
            return {"id": ref, "lib_id": lib_id, "label": label,
                    "at": list(at), "uuid": comp.uuid}
        except Exception:
            continue
    raise ValueError(f"could not place power symbol with label {label!r}")


def add_ground(path: Path, ref: str, at: tuple[float, float]) -> dict:
    """Place a GND symbol."""
    sch = _load(path)
    comp = sch.components.add(
        lib_id="power:GND", reference=ref, value="GND", position=at,
    )
    _save(sch, path)
    return {"id": ref, "lib_id": "power:GND", "at": list(at), "uuid": comp.uuid}


# ─── Add: wires ────────────────────────────────────────────────────────────

def add_wire(path: Path, src: str, dst: str) -> dict:
    """Connect two endpoints. Resolves "U1.VCC" / "VCC1" / "@40,60" forms."""
    sch = _load(path)
    a = _resolve_endpoint(sch, src)
    b = _resolve_endpoint(sch, dst)
    wire_uuid = sch.add_wire(start=a, end=b)
    _save(sch, path)
    return {"from": src, "to": dst, "from_xy": list(a), "to_xy": list(b),
            "uuid": wire_uuid}


# ─── Edit: canvas + symbol props ──────────────────────────────────────────

def set_canvas(path: Path, *, paper_size: Optional[str] = None,
               title: Optional[str] = None,
               company: Optional[str] = None) -> dict:
    """Update sheet metadata (paper size, title block).

    paper_size: "A4", "A3", "USLetter", etc. (KiCad's standard sizes)
    """
    sch = _load(path)
    if paper_size is not None:
        sch.set_paper_size(paper_size)
    if title is not None or company is not None:
        sch.set_title_block(title=title, company=company)
    _save(sch, path)
    return {"paper_size": paper_size, "title": title, "company": company}


def set_value(path: Path, ref: str, value: str) -> dict:
    """Update the Value property of a placed symbol."""
    sch = _load(path)
    comp = sch.components.get(ref)
    if comp is None:
        raise ValueError(f"unknown symbol {ref!r}")
    comp.value = value
    _save(sch, path)
    return {"id": ref, "value": value}


def set_footprint(path: Path, ref: str, footprint: str) -> dict:
    """Update the Footprint property of a placed symbol."""
    sch = _load(path)
    comp = sch.components.get(ref)
    if comp is None:
        raise ValueError(f"unknown symbol {ref!r}")
    comp.footprint = footprint
    _save(sch, path)
    return {"id": ref, "footprint": footprint}


# ─── Remove ────────────────────────────────────────────────────────────────

def remove(path: Path, ref: str) -> dict:
    """Drop a symbol. kicad-sch-api removes it from the components collection;
    wires touching it remain (kicad-cli ERC will flag them as dangling)."""
    sch = _load(path)
    comp = sch.components.get(ref)
    if comp is None:
        raise ValueError(f"unknown symbol {ref!r}")
    uuid = comp.uuid
    sch.components.remove(comp)
    _save(sch, path)
    return {"removed": ref, "uuid": uuid}


# ─── Introspection ─────────────────────────────────────────────────────────

def list_pins(path: Path, ref: str) -> list[dict]:
    """List pins on a placed symbol — name, number, position."""
    sch = _load(path)
    comp = sch.components.get(ref)
    if comp is None:
        raise ValueError(f"unknown symbol {ref!r}")

    # kicad-sch-api gives us number + position; pull names from kicad_lib.
    pins_pos = sch.list_component_pins(ref) or []
    name_by_number: dict[str, str] = {}
    try:
        from hw_toolkit.kicad.lib import load_symbol
        lib = load_symbol(comp.lib_id)
        name_by_number = {p.number: p.name for p in lib.pins}
    except Exception:
        pass

    return [
        {"number": num, "name": name_by_number.get(num, ""),
         "at": [pos.x, pos.y]}
        for num, pos in pins_pos
    ]


def list_symbols(path: Path) -> list[dict]:
    """Compact list of placed symbols on the sheet."""
    sch = _load(path)
    return [
        {
            "id": c.reference,
            "lib_id": c.lib_id,
            "value": c.value or "",
            "at": [c.position.x, c.position.y],
            "rotation": c.rotation,
            "footprint": c.footprint or "",
            "uuid": c.uuid,
        }
        for c in sch.components
    ]
