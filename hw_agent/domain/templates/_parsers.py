"""Vendor string parsers — convert raw vendor JSON values to canonical units.

Used by `ActualsClass.from_digikey / from_jlc / from_mouser` classmethods.
Each parser accepts `str | None` and returns `float | None` (or `tuple` for ranges).
Returns `None` for missing/unparseable input — never raises.

Canonical units (what we store in *Actuals models):
  voltage:  V    (use parse_voltage; "600mV" -> 0.6, "4.5V" -> 4.5)
  current:  A    (use parse_current; "100mA" -> 0.1, "8A" -> 8.0)
  freq:     kHz  (use parse_freq_khz; "1.6MHz" -> 1600.0, "500kHz" -> 500.0)
  resist:   mΩ   (use parse_resistance_mohm; "30mΩ" -> 30.0, "0.03Ω" -> 30.0)
  temp:     °C   (use parse_temp_max; "-40°C ~ 85°C (TA)" -> 85.0)
"""

from __future__ import annotations

import re
from typing import Optional


_NUM = r"[-+]?\d+(?:\.\d+)?"


def _first_number(s: str) -> Optional[float]:
    m = re.search(_NUM, s)
    return float(m.group(0)) if m else None


def parse_voltage(s: Optional[str]) -> Optional[float]:
    """'4.5V' -> 4.5  ·  '600mV' -> 0.6  ·  '1.2kV' -> 1200.0"""
    if not s:
        return None
    s = s.strip()
    n = _first_number(s)
    if n is None:
        return None
    u = s.lower()
    if "mv" in u:
        return n / 1000.0
    if "kv" in u:
        return n * 1000.0
    return n  # plain V or unitless


def parse_voltage_range(s: Optional[str]) -> tuple[Optional[float], Optional[float]]:
    """'4.5V~23V' -> (4.5, 23.0)  ·  '600mV~5.1V' -> (0.6, 5.1)  ·  '4.5V' -> (4.5, None)"""
    if not s:
        return (None, None)
    # split on ~, -, –, "to"
    parts = re.split(r"\s*[~–\-]\s*|\s+to\s+", s.strip(), maxsplit=1)
    if len(parts) == 2:
        return (parse_voltage(parts[0]), parse_voltage(parts[1]))
    return (parse_voltage(parts[0]), None)


def parse_current(s: Optional[str]) -> Optional[float]:
    """'8A' -> 8.0  ·  '100mA' -> 0.1  ·  '50µA' -> 5e-5"""
    if not s:
        return None
    n = _first_number(s)
    if n is None:
        return None
    u = s.lower().replace("μ", "µ")
    if "µa" in u or "ua" in u:
        return n / 1_000_000.0
    if "ma" in u:
        return n / 1000.0
    return n  # plain A


def parse_freq_khz(s: Optional[str]) -> Optional[float]:
    """'500kHz' -> 500.0  ·  '1.6MHz' -> 1600.0  ·  '2GHz' -> 2_000_000.0"""
    if not s:
        return None
    n = _first_number(s)
    if n is None:
        return None
    u = s.lower()
    if "ghz" in u:
        return n * 1_000_000.0
    if "mhz" in u:
        return n * 1000.0
    if "khz" in u:
        return n
    if "hz" in u:
        return n / 1000.0
    return n  # bare number assumed kHz


def parse_resistance_mohm(s: Optional[str]) -> Optional[float]:
    """'30mΩ' -> 30.0  ·  '0.03Ω' -> 30.0  ·  '1kΩ' -> 1_000_000.0"""
    if not s:
        return None
    n = _first_number(s)
    if n is None:
        return None
    u = s.lower().replace("ω", "o")
    if "mo" in u:
        return n
    if "ko" in u:
        return n * 1_000_000.0
    # bare Ω
    return n * 1000.0


def parse_temp_max(s: Optional[str]) -> Optional[float]:
    """'-40°C ~ 85°C (TA)' -> 85.0  ·  '125°C' -> 125.0  ·  '-40 to 105°C' -> 105.0"""
    if not s:
        return None
    nums = re.findall(_NUM, s)
    if not nums:
        return None
    return max(float(x) for x in nums)


def parse_yes_no(s: Optional[str]) -> Optional[bool]:
    if not s:
        return None
    return s.strip().lower() in ("yes", "true", "1", "y")


def parse_package(s: Optional[str]) -> Optional[str]:
    """Pass-through with whitespace strip. Vendor strings already canonical-ish."""
    if not s:
        return None
    return s.strip()


def parse_iq_ua(s: Optional[str]) -> Optional[float]:
    """'50µA' -> 50.0  ·  '0.1mA' -> 100.0  ·  '5mA' -> 5000.0"""
    if not s:
        return None
    n = _first_number(s)
    if n is None:
        return None
    u = s.lower().replace("μ", "µ")
    if "µa" in u or "ua" in u:
        return n
    if "ma" in u:
        return n * 1000.0
    if u.endswith("a"):
        return n * 1_000_000.0
    return n  # assume µA
