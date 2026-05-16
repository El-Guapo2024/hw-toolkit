"""Declarative schematic schema — Pydantic data model for `*.schem.json` files.

Used by `circuit_builder.py` (authoring), `kicad_writer.py` (compile to
`.kicad_sch`), `eval.py` (validate before ERC), and `json_ops.py` (atomic
edit tools).

The slim C++ renderer (`render_core/`) and its bridge code is no longer
on the live path — KiCad's own kicad-cli renders the `.kicad_sch` for
the agent (`get_render` in `mcp_server.py`) and KiCanvas renders it for
the browser. The retired code lives in `_trash/` for reference.

Coordinates throughout are in mm, Y growing downward (KiCad convention).
"""

from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator


# ─── Schema (Pydantic) ──────────────────────────────────────────────────────

class Pin(BaseModel):
    """A pin on an IC or symbol. Coordinates are absolute (not symbol-relative)."""
    name: str
    at: tuple[float, float]
    side: Literal["left", "right", "top", "bottom"] = "left"


class Symbol(BaseModel):
    """A schematic symbol — IC, passive, power flag, ground, terminal, or KiCad lib."""
    id: str
    type: Literal[
        "ic", "resistor", "capacitor", "inductor",
        "ground", "vcc", "terminal", "diode",
        "kicad",  # Reference KiCad symbol library by lib_id
    ] = "ic"
    at: tuple[float, float]
    # KiCad symbol reference (when type="kicad" or lib_id is set)
    lib_id: Optional[str] = None  # e.g. "Regulator_Linear:AMS1117-3.3"
    # IC-specific (used when type="ic" and no lib_id)
    part: Optional[str] = None
    size: Optional[tuple[float, float]] = None  # width, height in mm
    pins: list[Pin] = Field(default_factory=list)
    # Passive-specific
    value: Optional[str] = None
    orient: Literal["up", "down", "left", "right"] = "right"
    # Label-specific (for vcc/ground/terminal)
    label: Optional[str] = None
    # Net metadata — used by typed-net checks in circuit_builder + downstream
    # tooling (decoupling planner, ERC, BOM, SI sim). Renderer ignores these.
    voltage_v: Optional[float] = None      # for type="vcc"
    impedance_ohm: Optional[float] = None  # for type="terminal" used as Signal
    # PCB-specific: KiCad footprint library reference for the physical
    # component on the board. Required for physical types (R/C/L/IC/diode/
    # kicad-typed). Ignored for ground/vcc/terminal — those are net
    # references, not physical parts. Format: "<Library>:<Footprint>",
    # e.g. "Resistor_SMD:R_0805_2012Metric".
    footprint: Optional[str] = None
    # KiCad-symbol render knobs — agent overrides these when iterating to
    # fix overlap / readability without renderer logic.
    pin_name_size_mm: float = 1.27
    pin_number_size_mm: float = 0.9
    reference_size_mm: float = 1.5


class WireEndpoint(BaseModel):
    """A wire endpoint. Either an absolute coord, or a reference like 'U1.VIN'."""
    block: Optional[str] = None
    pin: Optional[str] = None
    coord: Optional[tuple[float, float]] = None

    @classmethod
    def parse(cls, value: Union[str, list, tuple, dict]) -> "WireEndpoint":
        """Accept multiple shorthand forms in YAML."""
        if isinstance(value, str):
            if "." in value:
                block, pin = value.split(".", 1)
                return cls(block=block, pin=pin)
            return cls(block=value)
        if isinstance(value, (list, tuple)):
            return cls(coord=tuple(value))
        if isinstance(value, dict):
            return cls(**value)
        raise ValueError(f"Cannot parse WireEndpoint from {value!r}")


class Wire(BaseModel):
    """A wire connecting two endpoints. Routes as straight or L-shape."""
    from_: WireEndpoint
    to: WireEndpoint
    label: Optional[str] = None
    elbow_first: Literal["h", "v"] = "h"


class Label(BaseModel):
    at: Union[tuple[float, float], str]  # coord or "U1.VIN" reference
    text: str
    offset: tuple[float, float] = (0, -1.5)  # mm offset from anchor
    fontsize: float = 10
    style: Literal["normal", "title", "small"] = "normal"


class Canvas(BaseModel):
    width: float = 100.0   # mm
    height: float = 60.0
    grid: float = 2.54
    title: Optional[str] = None
    title_size_mm: float = 2.8   # title text height; agent shrinks if it overflows


class Schematic(BaseModel):
    canvas: Canvas
    symbols: list[Symbol] = Field(default_factory=list)
    wires: list[Wire] = Field(default_factory=list)
    labels: list[Label] = Field(default_factory=list)

    @field_validator("wires", mode="before")
    @classmethod
    def _normalize_wires(cls, v):
        """Allow YAML shorthand: { from: U1.VIN, to: U2.OUT }."""
        if not isinstance(v, list):
            return v
        out = []
        for w in v:
            if isinstance(w, dict):
                w = dict(w)
                if "from" in w:
                    w["from_"] = WireEndpoint.parse(w.pop("from"))
                if "to" in w and not isinstance(w["to"], WireEndpoint):
                    w["to"] = WireEndpoint.parse(w["to"])
            out.append(w)
        return out


