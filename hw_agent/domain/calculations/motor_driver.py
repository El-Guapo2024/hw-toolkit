"""Motor-driver calculations — RDS(on) loss, junction temperature."""

from __future__ import annotations


def thermal_estimate(specs: dict, required: dict) -> dict:
    """Estimate driver self-heating from per-channel current and RDS(on).

    Pdiss/ch ≈ I² × Rdson  (continuous, ignoring switching loss)

    Reads:  specs.rdson_mohm, specs.theta_ja, required.current_per_channel,
            required.channels, required.ambient_c
    """
    rdson_mohm = specs.get("rdson_mohm") or specs.get("rdson")
    theta_ja = specs.get("theta_ja")
    if rdson_mohm is None or theta_ja is None:
        return {"thermal": {"error": "missing rdson_mohm or theta_ja"}}

    iout = required.get("current_per_channel", 1.0)
    channels = required.get("channels", 1)
    ambient = required.get("ambient_c", 40.0)
    rdson = rdson_mohm / 1000.0

    pdiss_per_ch = iout * iout * rdson
    pdiss_total = pdiss_per_ch * channels
    tj = ambient + pdiss_total * theta_ja
    return {
        "thermal": {
            "pdiss_per_ch_w": round(pdiss_per_ch, 3),
            "pdiss_total_w": round(pdiss_total, 3),
            "tj_c": round(tj, 1),
            "tj_safe": tj < 125,
            "margin_c": round(125 - tj, 1),
        }
    }
