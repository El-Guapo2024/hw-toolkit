"""Buck converter calculations — inductor, output cap, switching loss, junction temp."""

from __future__ import annotations


def _pick_iout(required: dict, *, prefer_typical: bool) -> tuple[float | None, str]:
    """Choose which iout to use in a calc and report which key was hit.

    `prefer_typical=True`  → use iout_typical if present (sizing for normal load).
    `prefer_typical=False` → use iout_max  if present (worst-case sizing/thermal).

    Returns (value, source_key). source_key is one of
    'iout_typical' | 'iout_max' | 'iout' | None.
    """
    if prefer_typical and required.get("iout_typical") is not None:
        return required["iout_typical"], "iout_typical"
    if required.get("iout_max") is not None:
        return required["iout_max"], "iout_max"
    if required.get("iout") is not None:
        return required["iout"], "iout"
    if required.get("iout_typical") is not None:  # last-resort fallback
        return required["iout_typical"], "iout_typical"
    return None, ""


def inductor_value(specs: dict, required: dict) -> dict:
    """Compute target inductor value and ripple current.

    L = Vout × (1 − duty) / (Fsw × Iripple)

    Sized at the **typical** operating point (iout_typical → iout_max fallback)
    because L drives ripple at normal load; peak ripple at iout_max is then
    proportionally higher and OK so long as it stays under the saturation rating.

    `fsw_khz` is optional in the engineer's requirements. If unset, default to
    1 MHz (modern integrated-buck norm — small passives, OK efficiency).
    """
    vin = required.get("vin_nom") or required.get("vin")
    vout = required.get("vout")
    iout, iout_src = _pick_iout(required, prefer_typical=True)
    fsw_khz = required.get("fsw_khz") or 1000
    ripple_pct = required.get("ripple_pct") or 30
    if not (vin and vout and iout):
        return {"inductor": {"error": "missing vin/vout/iout"}}

    fsw = fsw_khz * 1000
    duty = vout / vin
    ripple_a = iout * (ripple_pct / 100)
    if ripple_a == 0:
        return {"inductor": {"error": "ripple_a=0"}}
    l_h = (vout * (1 - duty)) / (fsw * ripple_a)
    l_uh = l_h * 1e6
    return {
        "inductor": {
            "duty_cycle": round(duty, 3),
            "inductor_uh": round(l_uh, 1),
            "ripple_current_a": round(ripple_a, 3),
            "peak_current_a": round(iout + ripple_a / 2, 3),
            "iout_a": iout,
            "iout_source": iout_src,
        }
    }


def output_cap_value(specs: dict, required: dict, *, target_ripple_mv: float = 30.0) -> dict:
    """Compute minimum output cap for target ripple.

    C ≥ Iripple / (8 × Fsw × Vripple). Sized at **iout_max** so the cap holds
    the ripple target at peak load (typical-load sizing would under-spec the
    cap). Defaults Fsw to 1 MHz when not specified.
    """
    vin = required.get("vin_nom") or required.get("vin")
    vout = required.get("vout")
    iout, iout_src = _pick_iout(required, prefer_typical=False)
    fsw_khz = required.get("fsw_khz") or 1000
    ripple_pct = required.get("ripple_pct") or 30
    if not (vin and vout and iout):
        return {"output_cap": {"error": "missing vin/vout/iout"}}
    fsw = fsw_khz * 1000
    ripple_a = iout * (ripple_pct / 100)
    c_f = ripple_a / (8 * fsw * (target_ripple_mv / 1000))
    return {
        "output_cap": {
            "min_cap_uf": round(c_f * 1e6, 1),
            "target_ripple_mv": target_ripple_mv,
            "iout_a": iout,
            "iout_source": iout_src,
        }
    }


def thermal_estimate(specs: dict, required: dict) -> dict:
    """Rough buck self-heating from RDS(on) conduction loss at **iout_max**.

    Pdiss ≈ Iout² × Rdson × duty  (ignores switching loss; OK first-order)
    """
    rdson_mohm = specs.get("rdson_mohm") or specs.get("rdson")
    theta_ja = specs.get("theta_ja")
    vin = required.get("vin_nom") or required.get("vin")
    vout = required.get("vout")
    iout, iout_src = _pick_iout(required, prefer_typical=False)
    ambient = required.get("ambient_c", 40.0)
    if rdson_mohm is None or theta_ja is None or not (vin and vout and iout):
        return {"thermal": {"error": "missing rdson_mohm / theta_ja / vin / vout / iout"}}

    rdson = rdson_mohm / 1000.0
    duty = vout / vin
    pdiss = iout * iout * rdson * duty
    tj = ambient + pdiss * theta_ja
    return {
        "thermal": {
            "pdiss_w": round(pdiss, 3),
            "tj_c": round(tj, 1),
            "tj_safe": tj < 125,
            "margin_c": round(125 - tj, 1),
            "duty_cycle": round(duty, 3),
            "iout_a": iout,
            "iout_source": iout_src,
        }
    }
