"""Mutation operations on a `.schem.json` file — the agent's hands.

Used by atomic MCP tools (`add_ic`, `add_wire`, etc.). Each function:
  1. Loads the JSON
  2. Validates current state via the Pydantic schema
  3. Applies the mutation (with its own pre-condition checks)
  4. Re-validates
  5. Writes back

The daemon's file watcher picks up the write and re-runs the eval pipeline
(kicad_writer → kicad-cli ERC → KiCanvas render).

Wire-endpoint string format (agent-friendly, FastMCP-friendly):
  - `"U1.VCC"`     → pin VCC on symbol U1
  - `"VCC1"`       → net anchor (power / ground / terminal symbol)
  - `"@40.5,60"`   → explicit coordinate (mm)
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from hw_agent.schematics.schem_renderer import Schematic
from hw_agent.schematics.kicad_lib import load_symbol


# ─── Endpoint parsing ──────────────────────────────────────────────────────

_COORD_RE = re.compile(r"^@\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*$")


def _parse_endpoint(s: str) -> dict:
    """Convert agent-friendly endpoint string to JSON wire-endpoint dict."""
    s = s.strip()
    m = _COORD_RE.match(s)
    if m:
        return {"coord": [float(m.group(1)), float(m.group(2))]}
    if "." in s:
        block, pin = s.split(".", 1)
        return {"block": block.strip(), "pin": pin.strip()}
    return {"block": s}


# ─── Load / save ──────────────────────────────────────────────────────────

def _load(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"schem.json not found: {path}")
    return json.loads(path.read_text())


def _validate(data: dict) -> None:
    """Round-trip through Pydantic to catch schema violations."""
    Schematic.model_validate(data)


def _save(path: Path, data: dict) -> None:
    _validate(data)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _by_id(data: dict, ref: str) -> Optional[dict]:
    for sym in data.get("symbols", []):
        if sym.get("id") == ref:
            return sym
    return None


def _require_unique(data: dict, ref: str) -> None:
    if _by_id(data, ref) is not None:
        raise ValueError(f"Duplicate symbol id: {ref!r}")


# ─── Pin validation against KiCad lib ──────────────────────────────────────

def _validate_kicad_pin(lib_id: str, pin: str) -> None:
    """Raise KeyError with a helpful message if pin isn't on the lib symbol."""
    try:
        lib = load_symbol(lib_id)
    except Exception:
        return  # local hwagent libs etc. — let writer handle it later
    pin_keys = set()
    for p in lib.pins:
        pin_keys.add(p.name)
        pin_keys.add(p.number)
    if pin not in pin_keys:
        candidates = sorted(pin_keys)[:12]
        raise KeyError(
            f"Pin {pin!r} not found on {lib_id}. Available (first 12): {candidates}"
        )


# ─── Add: components ───────────────────────────────────────────────────────

def add_ic(path: Path, ref: str, lib_id: str, at: tuple[float, float],
           *, pin_name_size_mm: Optional[float] = None,
           pin_number_size_mm: Optional[float] = None,
           reference_size_mm: Optional[float] = None) -> dict:
    """Place a KiCad-library IC. Validates lib_id loads."""
    data = _load(path)
    _require_unique(data, ref)
    # Trigger lib load to fail fast on bogus lib_id
    try:
        load_symbol(lib_id)
    except Exception as e:
        raise ValueError(f"lib_id {lib_id!r} did not load: {e}")
    entry: dict = {"id": ref, "type": "kicad", "lib_id": lib_id, "at": [at[0], at[1]]}
    if pin_name_size_mm is not None:
        entry["pin_name_size_mm"] = pin_name_size_mm
    if pin_number_size_mm is not None:
        entry["pin_number_size_mm"] = pin_number_size_mm
    if reference_size_mm is not None:
        entry["reference_size_mm"] = reference_size_mm
    data["symbols"].append(entry)
    _save(path, data)
    return entry


def _add_passive(path: Path, ref: str, type_: str, value: str,
                 at: tuple[float, float], orient: str) -> dict:
    if orient not in ("up", "down", "left", "right"):
        raise ValueError(f"orient must be up/down/left/right, got {orient!r}")
    data = _load(path)
    _require_unique(data, ref)
    entry = {"id": ref, "type": type_, "value": value,
             "at": [at[0], at[1]], "orient": orient}
    data["symbols"].append(entry)
    _save(path, data)
    return entry


def add_capacitor(path: Path, ref: str, value: str, at: tuple[float, float],
                  orient: str = "right") -> dict:
    return _add_passive(path, ref, "capacitor", value, at, orient)


def add_resistor(path: Path, ref: str, value: str, at: tuple[float, float],
                 orient: str = "right") -> dict:
    return _add_passive(path, ref, "resistor", value, at, orient)


def add_inductor(path: Path, ref: str, value: str, at: tuple[float, float],
                 orient: str = "right") -> dict:
    return _add_passive(path, ref, "inductor", value, at, orient)


def add_power(path: Path, ref: str, at: tuple[float, float],
              label: str = "VCC") -> dict:
    data = _load(path)
    _require_unique(data, ref)
    entry = {"id": ref, "type": "vcc", "at": [at[0], at[1]], "label": label}
    data["symbols"].append(entry)
    _save(path, data)
    return entry


def add_ground(path: Path, ref: str, at: tuple[float, float]) -> dict:
    data = _load(path)
    _require_unique(data, ref)
    entry = {"id": ref, "type": "ground", "at": [at[0], at[1]]}
    data["symbols"].append(entry)
    _save(path, data)
    return entry


# ─── Add: wires ────────────────────────────────────────────────────────────

def add_wire(path: Path, src: str, dst: str, elbow: str = "h") -> dict:
    """Connect two endpoints. Validates referenced symbols + pins exist."""
    if elbow not in ("h", "v"):
        raise ValueError(f"elbow must be 'h' or 'v', got {elbow!r}")
    data = _load(path)
    src_ep = _parse_endpoint(src)
    dst_ep = _parse_endpoint(dst)

    for ep, label in ((src_ep, "src"), (dst_ep, "dst")):
        if "block" in ep:
            sym = _by_id(data, ep["block"])
            if sym is None:
                raise ValueError(f"{label}: unknown symbol {ep['block']!r}")
            if "pin" in ep and sym.get("type") == "kicad" and sym.get("lib_id"):
                _validate_kicad_pin(sym["lib_id"], ep["pin"])

    entry = {"from": src_ep, "to": dst_ep, "elbow_first": elbow}
    data["wires"].append(entry)
    _save(path, data)
    return entry


# ─── Edit: knobs + canvas ──────────────────────────────────────────────────

def set_canvas(path: Path, *, width: Optional[float] = None,
               height: Optional[float] = None, title: Optional[str] = None,
               title_size_mm: Optional[float] = None) -> dict:
    data = _load(path)
    canvas = data.setdefault("canvas", {})
    if width is not None:
        canvas["width"] = width
    if height is not None:
        canvas["height"] = height
    if title is not None:
        canvas["title"] = title
    if title_size_mm is not None:
        canvas["title_size_mm"] = title_size_mm
    _save(path, data)
    return canvas


def set_knob(path: Path, ref: str, *,
             pin_name_size_mm: Optional[float] = None,
             pin_number_size_mm: Optional[float] = None,
             reference_size_mm: Optional[float] = None) -> dict:
    data = _load(path)
    sym = _by_id(data, ref)
    if sym is None:
        raise ValueError(f"unknown symbol {ref!r}")
    if pin_name_size_mm is not None:
        sym["pin_name_size_mm"] = pin_name_size_mm
    if pin_number_size_mm is not None:
        sym["pin_number_size_mm"] = pin_number_size_mm
    if reference_size_mm is not None:
        sym["reference_size_mm"] = reference_size_mm
    _save(path, data)
    return sym


# ─── Remove ────────────────────────────────────────────────────────────────

def remove(path: Path, ref: str) -> dict:
    """Drop a symbol AND every wire touching it. Returns counts."""
    data = _load(path)
    sym = _by_id(data, ref)
    if sym is None:
        raise ValueError(f"unknown symbol {ref!r}")

    data["symbols"] = [s for s in data["symbols"] if s.get("id") != ref]

    def _touches(ep: dict) -> bool:
        return ep.get("block") == ref

    before = len(data.get("wires", []))
    data["wires"] = [
        w for w in data.get("wires", [])
        if not (_touches(w.get("from", {})) or _touches(w.get("to", {})))
    ]
    wires_dropped = before - len(data["wires"])
    _save(path, data)
    return {"removed": ref, "wires_dropped": wires_dropped}


# ─── Introspection ─────────────────────────────────────────────────────────

def list_pins(path: Path, ref: str) -> list[str]:
    """List pin names available on a symbol — useful before add_wire."""
    data = _load(path)
    sym = _by_id(data, ref)
    if sym is None:
        raise ValueError(f"unknown symbol {ref!r}")
    typ = sym.get("type")
    if typ in ("capacitor", "resistor", "inductor"):
        return ["1", "2", "top", "bottom", "left", "right"]
    if typ == "kicad" and sym.get("lib_id"):
        try:
            lib = load_symbol(sym["lib_id"])
        except Exception as e:
            return [f"<lib load failed: {e}>"]
        names = sorted({p.name for p in lib.pins} | {p.number for p in lib.pins})
        return names
    return [p.get("name", "") for p in sym.get("pins", [])]


def list_symbols(path: Path) -> list[dict]:
    """Compact list of placed symbols — id, type, at, lib_id/value."""
    data = _load(path)
    out = []
    for s in data.get("symbols", []):
        entry: dict[str, Any] = {"id": s.get("id"), "type": s.get("type"),
                                 "at": s.get("at")}
        if s.get("lib_id"):
            entry["lib_id"] = s["lib_id"]
        if s.get("value"):
            entry["value"] = s["value"]
        if s.get("label"):
            entry["label"] = s["label"]
        out.append(entry)
    return out
