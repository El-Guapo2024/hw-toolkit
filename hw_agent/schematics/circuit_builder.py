"""Python authoring layer for *.schem.json schematics.

Build a schematic in code, compile to JSON. The JSON file is the
canonical artifact (the daemon, renderer, and ERC consume it); this
module is the human/agent-friendly authoring surface that compiles to
that JSON.

Pattern learned from kicad-sch-api / circuit-synth / Diode:
- Single generic Component class — no per-MPN subclassing.
- KiCad-symbol behavior driven by lib_id strings, not class hierarchy.
- Pin lookup by brackets: `comp["VIN"]` and `comp[1]`. Pin names
  like `~RESET` and `D+` aren't valid Python attributes, so attribute
  access is intentionally avoided.
- Pin names validated against the .kicad_sym lib at compile time.

Example:
    from hw_agent.schematics.circuit_builder import Sheet

    s = Sheet("ldo", canvas=(130, 80), title="LDO 5V → 3.3V")
    v5   = s.power("VIN",  at=(22, 35), label="5V")
    v3v3 = s.power("VOUT", at=(98, 35), label="3V3")
    gnd  = s.ground("GND1", at=(60, 50))

    u1 = s.kicad("U1", "Regulator_Linear:AMS1117-3.3", at=(60, 35))
    s.wire(v5,         u1["VI"])
    s.wire(u1["VO"],   v3v3)
    s.wire(u1["GND"],  gnd)

    c1 = s.cap("C1", "10µF", at=(35, 35), orient="down")
    s.wire(c1[1], v5)
    s.wire(c1[2], gnd)

    s.write("schematic.schem.json")
"""
from __future__ import annotations

import functools
import inspect
import json
from pathlib import Path
from typing import Any, Callable, Optional, Union, get_type_hints

from hw_agent.schematics.interfaces import (
    Interface, I2C, SPI, UART, USB2, CAN, SWD, JTAG, wire_interfaces,
)
from hw_agent.schematics.schem_renderer import Schematic
from hw_agent.schematics.units import Resistance, Voltage


# ─── Module decorator ──────────────────────────────────────────────────────

def module(fn: Callable) -> Callable:
    """Mark a function as a reusable subcircuit. Validates io types at call.

    Module functions take `sheet` as the first positional arg, then typed io
    (Power/Ground/Signal/Component) and keyword config. The decorator runtime-
    checks that arguments match annotations — catches "passed gnd where vcc
    was expected" mistakes early.

    Inside the body, use `s.kicad("U?", ...)` and `s.cap("C?", ..., at="auto")`
    so refs auto-number across instantiations.

    Example:
        @module
        def ldo_3v3(s: Sheet, vin: Power, vout: Power, gnd: Ground, *,
                    mpn: str = "AMS1117-3.3"):
            u = s.kicad("U?", f"Regulator_Linear:{mpn}", at="auto")
            s.wire(vin, u["VI"]); s.wire(u["VO"], vout); s.wire(u["GND"], gnd)
            s.cap("C?", "10µF", at="auto", between=(vin, gnd))
            s.cap("C?", "10µF", at="auto", between=(vout, gnd))
    """
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            hints = get_type_hints(fn)
        except Exception:
            hints = {}
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        for name, val in bound.arguments.items():
            expected = hints.get(name)
            if expected is None or val is None:
                continue
            # Skip Sheet itself + Optionals/unions — keep checks simple.
            origin = getattr(expected, "__origin__", None)
            if origin is Union:
                continue
            if not isinstance(val, type) and isinstance(expected, type):
                if not isinstance(val, expected):
                    raise TypeError(
                        f"@module {fn.__name__}: arg {name!r} expected "
                        f"{expected.__name__}, got {type(val).__name__}"
                    )
        return fn(*args, **kwargs)
    wrapper._is_module = True  # type: ignore[attr-defined]
    return wrapper


# ─── Pin / Net references ───────────────────────────────────────────────────

class PinRef:
    """Reference to a pin on a Component. Returned by `comp["VIN"]` / `comp[1]`."""

    def __init__(self, comp_id: str, pin_key: Union[str, int]):
        self.comp_id = comp_id
        self.pin_key = str(pin_key)

    def __repr__(self) -> str:
        return f"<Pin {self.comp_id}.{self.pin_key}>"


class NetRef:
    """Reference to a power / ground / terminal symbol used as a net anchor.

    `s.wire(comp["VCC"], v3v3_net)` connects the chip's VCC pin to the net.
    Subclasses (Power, Ground, Signal) carry electrical metadata for type
    checking in `wire()` and for downstream tooling (decoupling planner,
    BOM, ERC).
    """

    def __init__(self, comp_id: str):
        self.comp_id = comp_id

    def __repr__(self) -> str:
        return f"<Net {self.comp_id}>"


class Power(NetRef):
    """A VCC-style power flag with optional voltage. `s.power(...)` returns this.

    `wire(power_a, power_b)` errors if both have voltages and they don't match
    (catches "wired the 5V rail to the 3V3 chip pin" mistakes).
    """

    def __init__(self, comp_id: str, voltage: Optional[Voltage] = None):
        super().__init__(comp_id)
        self.voltage = voltage

    def __repr__(self) -> str:
        v = f" {self.voltage}" if self.voltage else ""
        return f"<Power {self.comp_id}{v}>"


class Ground(NetRef):
    """A ground symbol. `s.ground(...)` returns this."""

    def __repr__(self) -> str:
        return f"<Ground {self.comp_id}>"


class Signal(NetRef):
    """A named signal (clock, data, control) with optional target impedance.

    Use for nets that want explicit name + electrical intent. `s.signal(...)`
    returns this. Impedance metadata flows to downstream SI tools.
    """

    def __init__(self, comp_id: str, impedance: Optional[Resistance] = None):
        super().__init__(comp_id)
        self.impedance = impedance

    def __repr__(self) -> str:
        z = f" {self.impedance}" if self.impedance else ""
        return f"<Signal {self.comp_id}{z}>"


# ─── Wire type checking ────────────────────────────────────────────────────

def _check_wire_types(src, dst) -> None:
    """Raise on wire() combos that are obviously wrong (rail mismatch, etc.).

    Only checks NetRef↔NetRef pairs — pin↔net and pin↔pin pass through, since
    we don't (yet) track pin electrical types. Coord endpoints are anonymous
    waypoints and never error.
    """
    a, b = src, dst
    if not (isinstance(a, NetRef) and isinstance(b, NetRef)):
        return

    # Power-vs-Ground short
    if (isinstance(a, Power) and isinstance(b, Ground)) or \
       (isinstance(a, Ground) and isinstance(b, Power)):
        raise ValueError(
            f"refusing wire {a.comp_id} → {b.comp_id}: power-to-ground short"
        )

    # Power rail mismatch (both have stated voltages, and they differ)
    if isinstance(a, Power) and isinstance(b, Power):
        va, vb = a.voltage, b.voltage
        if va is not None and vb is not None and float(va) != float(vb):
            raise ValueError(
                f"refusing wire {a.comp_id}({va}) → {b.comp_id}({vb}): "
                f"power rail mismatch"
            )

    # Ground-to-Signal would tie the signal to GND
    if (isinstance(a, Ground) and isinstance(b, Signal)) or \
       (isinstance(a, Signal) and isinstance(b, Ground)):
        raise ValueError(
            f"refusing wire {a.comp_id} → {b.comp_id}: signal tied to ground"
        )


# ─── Component (placed instance) ───────────────────────────────────────────

class Component:
    """A placed component. Pin access via brackets (`comp["VIN"]`, `comp[1]`).

    Pin validation is lazy: the .kicad_sym lib only loads on the first
    `comp[name]` call, so building a sheet stays fast even with many
    KiCad-library symbols.
    """

    # Pseudo-pins for 2-terminal passives (cap/resistor/inductor). The
    # renderer's _resolve_endpoint accepts "top"/"left" (start anchor) and
    # "bot"/"bottom"/"right" (end anchor); we accept those plus 1/2 for
    # ergonomics, mapping numeric → side.
    _PASSIVE_START = {"top", "left", "1", 1}
    _PASSIVE_END   = {"bot", "bottom", "right", "2", 2}

    def __init__(self, sheet: "Sheet", entry: dict):
        self.sheet = sheet
        self.entry = entry
        self.id: str = entry["id"]
        self._lib_pin_keys: Optional[set[str]] = None

    def __getitem__(self, key: Union[str, int]) -> PinRef:
        # Passive pseudo-pins — normalize 1/2 to the renderer's vocabulary.
        if self.entry.get("type") in ("capacitor", "resistor", "inductor"):
            if key in self._PASSIVE_START:
                return PinRef(self.id, "top")
            if key in self._PASSIVE_END:
                return PinRef(self.id, "bottom")
            raise KeyError(
                f"{self.entry['type']} {self.id} pin {key!r}: expected "
                f"1/2 or top/bottom/left/right"
            )

        # KiCad library symbols — validate against the lib
        if self.entry.get("type") == "kicad" and self.entry.get("lib_id"):
            self._ensure_lib_loaded()
            if str(key) not in self._lib_pin_keys:
                raise KeyError(
                    f"Pin {key!r} not found on {self.entry['lib_id']}. "
                    f"Available pins: {sorted(self._lib_pin_keys)[:12]}..."
                )
            return PinRef(self.id, key)

        # Inline-defined pins (type="ic" with explicit pins[])
        for p in self.entry.get("pins", []) or []:
            if p.get("name") == str(key):
                return PinRef(self.id, key)

        raise KeyError(f"Pin {key!r} not found on {self.id}")

    def _ensure_lib_loaded(self) -> None:
        if self._lib_pin_keys is not None:
            return
        from hw_agent.schematics.kicad_lib import load_symbol
        lib = load_symbol(self.entry["lib_id"])
        self._lib_pin_keys = set()
        for pin in lib.pins:
            self._lib_pin_keys.add(pin.name)
            self._lib_pin_keys.add(pin.number)

    def pin(self, key: Union[str, int]) -> PinRef:
        """Method form of `comp[key]` for callers who prefer it."""
        return self[key]


# ─── Sheet (the entry point) ───────────────────────────────────────────────

class Sheet:
    """A schematic sheet under construction. Compile to JSON with `.write()`."""

    def __init__(
        self,
        name: str = "schematic",
        canvas: tuple[float, float] = (100.0, 60.0),
        title: Optional[str] = None,
        title_size_mm: float = 2.8,
    ):
        self.name = name
        self.canvas: dict = {
            "width": canvas[0],
            "height": canvas[1],
            "title": title,
            "title_size_mm": title_size_mm,
        }
        self.symbols: list[dict] = []
        self.wires: list[dict] = []
        self.labels: list[dict] = []
        self._by_id: dict[str, dict] = {}
        # Auto-numbered refs ("U?" → U1, U2, …) + simple grid-walk auto-placement.
        self._ref_counters: dict[str, int] = {}
        self._auto_cursor: list[float] = [10.0, 10.0]   # next auto-place position
        self._auto_step_x: float = 20.0                  # column pitch
        self._auto_step_y: float = 15.0                  # row pitch when wrapping

    # ── Component factories ──────────────────────────────────────────

    def kicad(
        self,
        ref: str,
        lib_id: str,
        at=None,
        *,
        pin_name_size_mm: Optional[float] = None,
        pin_number_size_mm: Optional[float] = None,
        reference_size_mm: Optional[float] = None,
    ) -> Component:
        """Place a KiCad library symbol (e.g. AMS1117, ESP32, …).

        `ref` accepts auto-number form like "U?" (next U-slot is allocated).
        `at` accepts "auto" or None for grid-walk placement.
        """
        ref = self._resolve_ref(ref)
        x, y = self._resolve_at(at)
        entry: dict = {
            "id": ref,
            "type": "kicad",
            "lib_id": lib_id,
            "at": [x, y],
        }
        if pin_name_size_mm is not None:
            entry["pin_name_size_mm"] = pin_name_size_mm
        if pin_number_size_mm is not None:
            entry["pin_number_size_mm"] = pin_number_size_mm
        if reference_size_mm is not None:
            entry["reference_size_mm"] = reference_size_mm
        return self._add(entry)

    def ic(self, ref: str, part: str, at,
           *, size: Optional[tuple[float, float]] = None,
           footprint: Optional[str] = None,
           pins: Optional[list[dict]] = None) -> Component:
        """Place a custom IC with inline-defined pins (no KiCad lib lookup).

        Use when the chip isn't in any KiCad symbol library (one-off parts
        like vendor-specific buck/LDO controllers). Pin positions are
        absolute mm coordinates. For KiCad-lib symbols use `kicad()`.

        `pins` is a list of dicts: each must have name, at (x, y), and
        optionally side ("left"/"right"/"top"/"bottom").
        """
        ref = self._resolve_ref(ref)
        x, y = self._resolve_at(at)
        entry: dict = {"id": ref, "type": "ic", "part": part, "at": [x, y]}
        if size is not None:
            entry["size"] = [size[0], size[1]]
        if footprint is not None:
            entry["footprint"] = footprint
        if pins is not None:
            entry["pins"] = [
                {"name": p["name"], "at": list(p["at"]),
                 "side": p.get("side", "left")}
                for p in pins
            ]
        return self._add(entry)

    def cap(self, ref: str, value: str, at=None,
            orient: str = "right",
            between: Optional[tuple] = None) -> Component:
        """Place a capacitor.

        `between=(net_a, net_b)` shorthand auto-wires both terminals — handy
        for decoupling/bypass caps where both endpoints are nets.
        """
        ref = self._resolve_ref(ref)
        x, y = self._resolve_at(at)
        comp = self._add({"id": ref, "type": "capacitor", "value": value,
                          "at": [x, y], "orient": orient})
        if between is not None:
            self._wire_between(comp, between)
        return comp

    def resistor(self, ref: str, value: str, at=None,
                 orient: str = "right",
                 between: Optional[tuple] = None) -> Component:
        """Place a resistor. `between=(a, b)` auto-wires both terminals."""
        ref = self._resolve_ref(ref)
        x, y = self._resolve_at(at)
        comp = self._add({"id": ref, "type": "resistor", "value": value,
                          "at": [x, y], "orient": orient})
        if between is not None:
            self._wire_between(comp, between)
        return comp

    def inductor(self, ref: str, value: str, at=None,
                 orient: str = "right",
                 between: Optional[tuple] = None) -> Component:
        """Place an inductor. `between=(a, b)` auto-wires both terminals."""
        ref = self._resolve_ref(ref)
        x, y = self._resolve_at(at)
        comp = self._add({"id": ref, "type": "inductor", "value": value,
                          "at": [x, y], "orient": orient})
        if between is not None:
            self._wire_between(comp, between)
        return comp

    def _wire_between(self, comp: "Component", endpoints: tuple) -> None:
        if len(endpoints) != 2:
            raise ValueError(f"between=(a, b) needs exactly 2 endpoints, got {len(endpoints)}")
        a, b = endpoints
        self.wire(comp[1], a)
        self.wire(comp[2], b)

    def power(self, ref: str, at=None,
              label: str = "VCC",
              voltage: Optional[Union[str, float, Voltage]] = None) -> Power:
        """Place a VCC/power-rail flag.

        `voltage` is metadata used by `wire()` to catch rail mismatches and
        by downstream tools. Accepts shorthand: "3.3V", "5V", "3V3", or a
        bare number (assumed volts). Falls through if not provided.
        """
        ref = self._resolve_ref(ref)
        x, y = self._resolve_at(at)
        v = Voltage.parse(voltage) if voltage is not None else None
        entry: dict = {"id": ref, "type": "vcc", "at": [x, y], "label": label}
        if v is not None:
            entry["voltage_v"] = float(v)
        self._add(entry)
        return Power(ref, voltage=v)

    def ground(self, ref: str, at=None) -> Ground:
        ref = self._resolve_ref(ref)
        x, y = self._resolve_at(at)
        self._add({"id": ref, "type": "ground", "at": [x, y]})
        return Ground(ref)

    # ── Bus interface factories (re-exports for ergonomic call sites) ─

    @staticmethod
    def i2c(*, sda, scl, **extras) -> I2C:
        """Build an I2C bundle. `s.wire(mcu_i2c, sensor_i2c)` connects matching pins."""
        return I2C(sda=sda, scl=scl, **extras)

    @staticmethod
    def spi(*, sck, mosi, miso, cs, **extras) -> SPI:
        return SPI(sck=sck, mosi=mosi, miso=miso, cs=cs, **extras)

    @staticmethod
    def uart(*, tx, rx, **extras) -> UART:
        """Build a UART. NOT auto-crossed — the cross is per-side. See interfaces.py."""
        return UART(tx=tx, rx=rx, **extras)

    @staticmethod
    def usb2(*, dp, dm, **extras) -> USB2:
        return USB2(dp=dp, dm=dm, **extras)

    @staticmethod
    def can(*, canh, canl, **extras) -> CAN:
        return CAN(canh=canh, canl=canl, **extras)

    @staticmethod
    def swd(*, swdio, swclk, **extras) -> SWD:
        return SWD(swdio=swdio, swclk=swclk, **extras)

    @staticmethod
    def jtag(*, tck, tms, tdi, tdo, **extras) -> JTAG:
        return JTAG(tck=tck, tms=tms, tdi=tdi, tdo=tdo, **extras)

    def signal(self, ref: str, at=None, *,
               label: Optional[str] = None,
               impedance: Optional[Union[str, float, Resistance]] = None) -> Signal:
        """Place a named signal anchor (clock/data/control). Optional impedance.

        Renders as a terminal symbol with the label. `impedance` flows to
        downstream SI tooling and is stored in JSON.
        """
        ref = self._resolve_ref(ref)
        x, y = self._resolve_at(at)
        z = Resistance.parse(impedance) if impedance is not None else None
        entry: dict = {"id": ref, "type": "terminal", "at": [x, y]}
        if label is not None:
            entry["label"] = label
        if z is not None:
            entry["impedance_ohm"] = float(z)
        self._add(entry)
        return Signal(ref, impedance=z)

    def terminal(self, ref: str, at=None,
                 label: Optional[str] = None) -> NetRef:
        ref = self._resolve_ref(ref)
        x, y = self._resolve_at(at)
        entry: dict = {"id": ref, "type": "terminal", "at": [x, y]}
        if label is not None:
            entry["label"] = label
        self._add(entry)
        return NetRef(ref)

    def _add(self, entry: dict) -> Component:
        if entry["id"] in self._by_id:
            raise ValueError(f"Duplicate symbol id: {entry['id']!r}")
        self.symbols.append(entry)
        self._by_id[entry["id"]] = entry
        return Component(self, entry)

    # ── Auto-numbering + auto-placement helpers ──────────────────────

    def _resolve_ref(self, ref: str) -> str:
        """Expand "U?" → "U1", "U2", … by per-prefix counter.

        Counter is seeded from existing symbols on first use, so adding to
        a partially-built sheet finds the next free slot.
        """
        if not ref.endswith("?"):
            return ref
        prefix = ref[:-1]
        # Lazy seed: scan existing IDs sharing the prefix on first use.
        if prefix not in self._ref_counters:
            highest = 0
            for sid in self._by_id:
                if sid.startswith(prefix) and sid[len(prefix):].isdigit():
                    highest = max(highest, int(sid[len(prefix):]))
            self._ref_counters[prefix] = highest
        # Find next free index (handles holes from manual placements).
        while True:
            self._ref_counters[prefix] += 1
            candidate = f"{prefix}{self._ref_counters[prefix]}"
            if candidate not in self._by_id:
                return candidate

    def _resolve_at(self, at) -> tuple[float, float]:
        """Pass-through (x, y) tuples; advance grid cursor for "auto"/None."""
        if at == "auto" or at is None:
            x, y = self._auto_cursor
            # Wrap when we'd go past canvas right edge.
            if x + self._auto_step_x > self.canvas["width"]:
                self._auto_cursor[0] = 10.0
                self._auto_cursor[1] += self._auto_step_y
                x, y = self._auto_cursor
            self._auto_cursor[0] = x + self._auto_step_x
            return (x, y)
        return (float(at[0]), float(at[1]))

    # ── Wires + labels ───────────────────────────────────────────────

    def wire(self, src, dst, *, elbow: str = "h") -> None:
        """Connect two endpoints. Each can be a PinRef, NetRef, (x, y), or Interface.

        Interface↔Interface dispatches to `wire_interfaces` and connects
        each matching named pin (sda↔sda, scl↔scl, …) — type mismatch raises.

        For single-endpoint pairs, type-checks NetRef↔NetRef combos:
        power-to-ground shorts and power-rail mismatches raise. Pin/coord
        endpoints pass through.
        """
        if isinstance(src, Interface) or isinstance(dst, Interface):
            if not (isinstance(src, Interface) and isinstance(dst, Interface)):
                raise TypeError(
                    f"wire(Interface, …) requires both ends be interfaces, "
                    f"got {type(src).__name__} and {type(dst).__name__}"
                )
            wire_interfaces(self, src, dst, elbow=elbow)
            return
        _check_wire_types(src, dst)
        self.wires.append({
            "from": _endpoint(src),
            "to":   _endpoint(dst),
            "elbow_first": elbow,
        })

    def label(self, text: str, at, *, offset: tuple[float, float] = (0, -1.5),
              fontsize: float = 10, style: str = "normal") -> None:
        """Standalone text label. `at` can be a PinRef ('U1.VIN') or (x, y)."""
        if isinstance(at, PinRef):
            at_field: Union[str, list] = f"{at.comp_id}.{at.pin_key}"
        elif isinstance(at, NetRef):
            at_field = at.comp_id
        else:
            at_field = [at[0], at[1]]
        self.labels.append({
            "at": at_field, "text": text,
            "offset": [offset[0], offset[1]],
            "fontsize": fontsize, "style": style,
        })

    # ── Compile / write ──────────────────────────────────────────────

    def to_dict(self) -> dict:
        canvas = {k: v for k, v in self.canvas.items() if v is not None}
        return {
            "canvas": canvas,
            "symbols": self.symbols,
            "wires": self.wires,
            "labels": self.labels,
        }

    def validate(self) -> Schematic:
        """Round-trip through Pydantic to catch schema bugs before writing."""
        return Schematic.model_validate(self.to_dict())

    def write(self, path: Union[str, Path]) -> Path:
        """Compile the sheet and write to disk. Format inferred from suffix.

        - `.kicad_sch` → direct via kicad-sch-api (`ksa_writer`). The agent
          and human both edit this file; kicad-cli renders it cleanly. The
          preferred path for human-authored sheets.
        - `.schem.json` (or any other suffix) → Pydantic-validated JSON.
          Used by atomic edit tools that mutate JSON in place. Legacy path —
          will be removed once the atomic tools migrate to kicad-sch-api.

        Returns the absolute path written.
        """
        out = Path(path)
        if out.suffix == ".kicad_sch":
            from hw_agent.schematics.ksa_writer import export_from_schematic
            schem = self.validate()
            return export_from_schematic(schem, out)

        # Legacy: write JSON (atomic-tool edit format)
        self.validate()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.to_dict(), indent=2,
                                  ensure_ascii=False) + "\n")
        return out


# ─── Helpers ────────────────────────────────────────────────────────────────

def _endpoint(ep) -> dict:
    if isinstance(ep, PinRef):
        return {"block": ep.comp_id, "pin": ep.pin_key}
    if isinstance(ep, NetRef):
        return {"block": ep.comp_id}
    if isinstance(ep, (list, tuple)) and len(ep) == 2:
        return {"coord": [float(ep[0]), float(ep[1])]}
    raise TypeError(
        f"wire endpoint must be PinRef / NetRef / (x, y); got {type(ep).__name__}"
    )
