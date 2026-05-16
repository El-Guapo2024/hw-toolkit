"""Analytical transmission-line + via-inductance formulas.

Closed-form approximations used as cheap pre-routing sanity checks.
Pair with `calc_trace_width` (IPC-2152) for the "did I screw up the
trace geometry?" gate that runs before any FEA solver.

References:
  - Hammerstad-Jensen microstrip (modeled per IPC-2141A § 4.2.1)
  - IPC-2141A symmetric stripline § 4.2.2
  - Standard MIT thin-wire via inductance (Howard Johnson, "High-Speed
    Digital Design," eq. 7.21)

All inputs are mm; outputs are SI base units (Ω, H, etc.) plus a small
markdown summary for tool-call legibility.
"""
from __future__ import annotations

import math
from typing import Optional


# ─── Microstrip ────────────────────────────────────────────────────────────

def microstrip_z0(
    width_mm: float,
    height_mm: float,
    thickness_mm: float = 0.035,    # 1 oz copper ≈ 0.035 mm
    er: float = 4.3,                # FR4 typical
) -> dict:
    """Microstrip impedance via Hammerstad-Jensen.

    Args:
        width_mm: trace width
        height_mm: dielectric thickness above reference plane
        thickness_mm: copper thickness (1oz=0.035mm, 2oz=0.07mm)
        er: relative permittivity (FR4≈4.3, Rogers≈3.5, polyimide≈3.5)

    Returns dict with z0_ohm, eff_er, w_eff_mm, propagation_delay_ps_per_mm.
    """
    if width_mm <= 0 or height_mm <= 0:
        raise ValueError("width and height must be > 0")

    w, h, t = width_mm, height_mm, thickness_mm
    # Effective width (corrects for non-zero copper thickness)
    if w / h >= 1.0 / (2.0 * math.pi):
        w_eff = w + (t / math.pi) * (1.0 + math.log(2.0 * h / t)) if t > 0 else w
    else:
        w_eff = w + (t / math.pi) * (1.0 + math.log(4.0 * math.pi * w / t)) if t > 0 else w

    u = w_eff / h

    # Effective permittivity (Hammerstad-Jensen)
    if u <= 1.0:
        eff_er = ((er + 1) / 2 + (er - 1) / 2 *
                  (1.0 / math.sqrt(1.0 + 12.0 / u) + 0.04 * (1.0 - u) ** 2))
    else:
        eff_er = ((er + 1) / 2 + (er - 1) / 2 *
                  (1.0 / math.sqrt(1.0 + 12.0 / u)))

    # Characteristic impedance
    if u <= 1.0:
        z0 = 60.0 / math.sqrt(eff_er) * math.log(8.0 / u + u / 4.0)
    else:
        z0 = (120.0 * math.pi) / (math.sqrt(eff_er) *
                                   (u + 1.393 + 0.667 * math.log(u + 1.444)))

    # Propagation delay: tpd = sqrt(eff_er) / c.  c ≈ 0.29979 mm/ps in vacuum.
    tpd_ps_per_mm = math.sqrt(eff_er) / 0.29979

    return {
        "z0_ohm": round(z0, 2),
        "eff_er": round(eff_er, 3),
        "w_eff_mm": round(w_eff, 4),
        "propagation_delay_ps_per_mm": round(tpd_ps_per_mm, 2),
    }


# ─── Stripline ─────────────────────────────────────────────────────────────

def stripline_z0(
    width_mm: float,
    plane_spacing_mm: float,
    thickness_mm: float = 0.035,
    er: float = 4.3,
) -> dict:
    """Symmetric stripline impedance (IPC-2141A § 4.2.2).

    Args:
        width_mm: trace width
        plane_spacing_mm: total dielectric between top and bottom planes
        thickness_mm: copper thickness
        er: relative permittivity

    For asymmetric (offset) stripline use a different formula — this is
    the centered-trace version, which is the common case.
    """
    if width_mm <= 0 or plane_spacing_mm <= 0:
        raise ValueError("width and plane spacing must be > 0")

    w, b, t = width_mm, plane_spacing_mm, thickness_mm
    # IPC-2141A § 4.2.2: Z0 = 60/sqrt(εr) · ln(4b / (0.67π(0.8w+t)))
    denom = 0.67 * math.pi * (0.8 * w + t)
    if denom <= 0:
        raise ValueError("trace too thin for stripline approximation")
    z0 = 60.0 / math.sqrt(er) * math.log(4.0 * b / denom)

    # Stripline εeff is just εr (fully embedded in dielectric).
    tpd_ps_per_mm = math.sqrt(er) / 0.29979

    return {
        "z0_ohm": round(z0, 2),
        "eff_er": round(er, 3),
        "propagation_delay_ps_per_mm": round(tpd_ps_per_mm, 2),
    }


# ─── Via inductance ────────────────────────────────────────────────────────

def via_inductance(
    height_mm: float,
    diameter_mm: float,
    return_path_mm: Optional[float] = None,
) -> dict:
    """Through-hole via inductance (Howard Johnson, eq. 7.21).

    L (nH) ≈ 0.2 · h(mm) · [ln(4h/d) + 1]

    Args:
        height_mm: via length (≈ board thickness for through-hole; smaller
                   for blind/buried)
        diameter_mm: drilled hole diameter (annular ring is ~irrelevant)
        return_path_mm: distance to nearest stitching via — adds an
                        approximation of the loop inductance contribution
                        when the return current can't follow the via
                        directly. Optional; default omits this term.

    Returns L in henries + a nH echo.
    """
    if height_mm <= 0 or diameter_mm <= 0:
        raise ValueError("height and diameter must be > 0")
    if 4 * height_mm / diameter_mm <= 1:
        raise ValueError(
            "ln domain error — via diameter too large relative to height"
        )

    l_self_nh = 0.2 * height_mm * (math.log(4.0 * height_mm / diameter_mm) + 1.0)

    extra_nh = 0.0
    if return_path_mm is not None and return_path_mm > 0:
        # Loop-inductance penalty for an offset return: ~1 nH/mm spacing
        # at typical PCB geometries (Howard Johnson approximation).
        extra_nh = 1.0 * return_path_mm

    total_nh = l_self_nh + extra_nh
    return {
        "l_nh": round(total_nh, 3),
        "l_self_nh": round(l_self_nh, 3),
        "l_return_path_nh": round(extra_nh, 3),
        "l_henry": total_nh * 1e-9,
    }
