"""Engineering value types — Voltage, Current, Resistance, Capacitance, etc.

Each carries a normalized SI float and parses common shorthand:
    Voltage("3.3V") → Voltage(3.3)
    Voltage("3V3")  → Voltage(3.3)        # KiCad/PCB convention
    Capacitance("100nF") → Capacitance(1e-7)
    Resistance("4.7kΩ")  → Resistance(4700)

Used by typed nets (`Power(voltage="3.3V")`) and component values to give
the agent type-checked reasoning over electrical quantities.

Design choices:
  - Strings, ints, and floats all coerce — `Power(voltage=3.3)` works.
  - Comparison + arithmetic yields plain floats (no inheritance gymnastics).
  - Unicode µ is normalized to u; Ω is optional. Suffix order is permissive.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar, Optional, Union


# ─── Multiplier prefixes ───────────────────────────────────────────────────

_PREFIXES: dict[str, float] = {
    "":  1.0,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "M": 1e6,  # case-sensitive: M=1e6 (mega), m=1e-3 (milli)
    "G": 1e9,
    "T": 1e12,
}

# Numeric pattern: integer or decimal (with optional sign)
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?")


def _parse_value(text: str, unit_chars: str) -> float:
    """Parse a string like "10kΩ", "100nF", "3V3" → SI float.

    Args:
        text: shorthand string
        unit_chars: trailing unit letters to strip (e.g. "VΩFHA")
    """
    s = str(text).strip()
    # Strip trailing units and Ω. Also strip the SI letter (V/F/H/A) if present.
    s = re.sub(rf"[{unit_chars}Ω]+$", "", s)

    # KiCad-style "3V3" was already stripped of V — leaves "3" + leftover "3".
    # That case: text was "3V3", unit_chars="V" → s = "3" then "3" → wait,
    # the "V" in middle wasn't stripped because regex anchors to end.
    # Handle "3V3" / "4k7" pattern: `<num><prefix-or-unit><num>` = decimal point.
    inner_re = re.compile(rf"^(-?\d+)([a-zA-ZµΩ])(\d+)$")
    m = inner_re.match(s)
    if m:
        whole, mid, frac = m.groups()
        # If `mid` is a known prefix → "4k7" = 4.7k → 4700
        if mid in _PREFIXES:
            return float(f"{whole}.{frac}") * _PREFIXES[mid]
        # If `mid` is a unit letter → "3V3" = 3.3 (decimal point)
        return float(f"{whole}.{frac}")

    # Standard <num><prefix?> form, e.g. "100n", "4.7k", "10"
    m = re.match(rf"^({_NUM_RE.pattern})\s*([a-zA-Zµ]?)\s*$", s)
    if not m:
        raise ValueError(f"cannot parse value {text!r}")
    num, prefix = m.groups()
    if prefix and prefix not in _PREFIXES:
        raise ValueError(f"unknown SI prefix {prefix!r} in {text!r}")
    return float(num) * _PREFIXES.get(prefix, 1.0)


# ─── Base value class ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Value:
    """Engineering value in SI base units (V, A, Ω, F, H, Hz)."""
    value: float

    # Subclass overrides
    UNIT_CHARS: ClassVar[str] = ""
    UNIT_LABEL: ClassVar[str] = ""

    @classmethod
    def parse(cls, x: Union[str, int, float, "_Value", None]) -> Optional["_Value"]:
        if x is None:
            return None
        if isinstance(x, cls):
            return x
        if isinstance(x, _Value):
            raise TypeError(f"cannot convert {type(x).__name__} to {cls.__name__}")
        if isinstance(x, (int, float)):
            return cls(float(x))
        return cls(_parse_value(x, cls.UNIT_CHARS))

    def __float__(self) -> float:
        return self.value

    def __str__(self) -> str:
        return _format_si(self.value, self.UNIT_LABEL)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.value!r})"


# ─── Concrete value types ──────────────────────────────────────────────────

class Voltage(_Value):
    UNIT_CHARS = "V"
    UNIT_LABEL = "V"


class Current(_Value):
    UNIT_CHARS = "A"
    UNIT_LABEL = "A"


class Resistance(_Value):
    UNIT_CHARS = "RrΩ"
    UNIT_LABEL = "Ω"


class Capacitance(_Value):
    UNIT_CHARS = "F"
    UNIT_LABEL = "F"


class Inductance(_Value):
    UNIT_CHARS = "H"
    UNIT_LABEL = "H"


class Frequency(_Value):
    UNIT_CHARS = "Hz"
    UNIT_LABEL = "Hz"


# ─── Pretty-printing ───────────────────────────────────────────────────────

_PREFIX_TABLE = [
    (1e9,   "G"),
    (1e6,   "M"),
    (1e3,   "k"),
    (1.0,   ""),
    (1e-3,  "m"),
    (1e-6,  "µ"),
    (1e-9,  "n"),
    (1e-12, "p"),
]


def _format_si(value: float, unit: str) -> str:
    """Pretty-print a SI value with the appropriate prefix."""
    if value == 0:
        return f"0{unit}"
    abs_v = abs(value)
    for scale, prefix in _PREFIX_TABLE:
        if abs_v >= scale:
            scaled = value / scale
            if scaled == int(scaled):
                return f"{int(scaled)}{prefix}{unit}"
            return f"{scaled:g}{prefix}{unit}"
    return f"{value:g}{unit}"
