"""LDO calculations — power dissipation, thermal, dropout headroom, current margin.

Pure-function math library. Stateless. Called by the orchestrator with the
unified (specs, required) signature shared with checks.
"""

from __future__ import annotations


def thermal_analysis(specs: dict, required: dict) -> dict:
    """Run all LDO calculations given extracted datasheet specs and engineer requirements.

    Args:
        specs:    Extracted specs dict from datasheet/cache. Each value may be
                  either a flat number or a sub-dict with `min`/`typ`/`max` fields.
                  iout_max is in Amps; vdrop is in mV (datasheet convention).
        required: Engineer requirements. Reads vin, vout, iout (A), ambient_c (°C).

    Returns:
        Dict with `power_dissipation`, `junction_temperature`, `margins`, `flags`,
        `quiescent_power_uw`, and `notes` (human-readable warnings).
    """
    # Normalize: spec values may be a number or a {min,typ,max} dict
    def _spec(key: str, default=None):
        v = specs.get(key)
        if isinstance(v, dict):
            return v.get("typ") or v.get("max") or v.get("min") or default
        return v if v is not None else default

    iout_max_a = _spec("iout_max", 0.6)        # Amps
    vdrop_typ_mv = _spec("vdrop", 250)         # mV
    theta_ja = _spec("theta_ja", 250)          # °C/W
    iq_typ = _spec("iq", 55)                   # µA
    tsd = _spec("tsd", 150)                    # °C

    vin = required["vin"]
    vout = required["vout"]
    actual_load_a = required.get("iout") or 0.220
    ambient = required.get("ambient_c", 40.0)

    vdrop_v = vdrop_typ_mv / 1000.0

    pdiss = (vin - vout) * actual_load_a
    pdiss_max = (vin - vout) * iout_max_a
    tj_85 = 85 + pdiss * theta_ja
    tj_70 = 70 + pdiss * theta_ja
    tj_max_load = 85 + pdiss_max * theta_ja
    headroom = vin - vout - vdrop_v
    current_margin = iout_max_a / actual_load_a if actual_load_a > 0 else float("inf")

    results = {
        "power_dissipation": {
            "at_actual_load_w": round(pdiss, 4),
            "at_max_load_w": round(pdiss_max, 4),
        },
        "junction_temperature": {
            "at_85c_ambient": round(tj_85, 1),
            "at_70c_ambient": round(tj_70, 1),
            "at_max_load_85c": round(tj_max_load, 1),
            "thermal_shutdown": tsd,
        },
        "margins": {
            "dropout_headroom_v": round(headroom, 3),
            "current_margin_x": round(current_margin, 1),
            "thermal_margin_85c": round(tsd - tj_85, 1),
            "thermal_margin_70c": round(tsd - tj_70, 1),
        },
        "flags": {
            "thermal_ok_85c": tj_85 < 125,
            "thermal_ok_70c": tj_70 < 125,
            "dropout_ok": headroom > 0.1,
            "current_ok": current_margin > 1.5,
            "thermal_marginal": 125 <= tj_85 < tsd,
        },
        "quiescent_power_uw": round(vin * iq_typ, 1),
    }

    notes = []
    if not results["flags"]["thermal_ok_85c"]:
        notes.append(f"FAIL: Tj={tj_85:.1f}°C at 85°C ambient exceeds 125°C. Consider copper pour or larger package.")
    elif results["flags"]["thermal_marginal"]:
        notes.append(f"WARNING: Tj={tj_85:.1f}°C is marginal. Add thermal pad or reduce load.")
    if not results["flags"]["dropout_ok"]:
        notes.append(f"FAIL: Only {headroom*1000:.0f}mV headroom above dropout. Increase Vin or pick lower dropout part.")
    if not results["flags"]["current_ok"]:
        notes.append(f"WARNING: Only {current_margin:.1f}x current margin. Consider higher current part.")
    if pdiss > 0.5:
        notes.append(f"High dissipation ({pdiss*1000:.0f}mW). Consider switching regulator instead of LDO.")

    results["notes"] = notes
    return results
