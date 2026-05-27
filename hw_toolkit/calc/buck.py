"""`hw.calc.Buck` — object wrapper around the buck operating-point math.

The underlying functions return dicts. The wrapper holds buck-operating-
point state (vin, vout, iout, fsw, ripple_pct), runs the calcs, and
returns typed result dataclasses so the engineer gets attribute access
in the notebook instead of dict[str].
"""
from __future__ import annotations

from dataclasses import dataclass

from hw_toolkit.calc import _buck_math as _buck


# ---------------------------------------------------------------------------
# Result dataclasses (attribute access in a notebook is nicer than dict[str])
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InductorResult:
    duty_cycle: float
    inductor_uh: float
    ripple_current_a: float
    peak_current_a: float
    iout_a: float
    iout_source: str

    def _repr_html_(self) -> str:
        return (
            "<table>"
            f"<tr><th>L</th><td>{self.inductor_uh} µH</td></tr>"
            f"<tr><th>I_ripple</th><td>{self.ripple_current_a} A</td></tr>"
            f"<tr><th>I_peak</th><td>{self.peak_current_a} A</td></tr>"
            f"<tr><th>duty</th><td>{self.duty_cycle}</td></tr>"
            "</table>"
        )


@dataclass(frozen=True)
class OutputCapResult:
    min_cap_uf: float
    target_ripple_mv: float
    iout_a: float
    iout_source: str


@dataclass(frozen=True)
class ThermalResult:
    pdiss_w: float
    tj_c: float
    tj_safe: bool
    margin_c: float
    duty_cycle: float
    iout_a: float
    iout_source: str

    def __bool__(self) -> bool:
        return self.tj_safe


# ---------------------------------------------------------------------------
# Buck object
# ---------------------------------------------------------------------------


@dataclass
class Buck:
    """Buck-converter operating-point object.

    Holds the design point; each method returns a typed result. Same
    inputs always produce the same result (the underlying calcs are
    pure). Construct once per pick; call methods as needed.

        >>> b = hw.calc.Buck(vin=24, vout=6.0, iout=5.0)
        >>> b.inductor().inductor_uh
        4.7
        >>> b.thermal(rdson_mohm=80, theta_ja=40).tj_safe
        True
    """
    vin: float
    vout: float
    iout: float
    fsw_khz: float = 1000.0
    ripple_pct: float = 30.0
    iout_typical: float | None = None

    # ---- helpers ----
    def _required(self) -> dict:
        out: dict[str, float] = {
            "vin": self.vin,
            "vout": self.vout,
            "iout_max": self.iout,
            "fsw_khz": self.fsw_khz,
            "ripple_pct": self.ripple_pct,
        }
        if self.iout_typical is not None:
            out["iout_typical"] = self.iout_typical
        return out

    # ---- calcs ----
    def inductor(self) -> InductorResult:
        d = _buck.inductor_value({}, self._required())["inductor"]
        if "error" in d:
            raise ValueError(f"buck.inductor: {d['error']}")
        return InductorResult(**d)

    def output_cap(self, *, target_ripple_mv: float = 30.0) -> OutputCapResult:
        d = _buck.output_cap_value(
            {}, self._required(), target_ripple_mv=target_ripple_mv
        )["output_cap"]
        if "error" in d:
            raise ValueError(f"buck.output_cap: {d['error']}")
        return OutputCapResult(**d)

    def thermal(self, *, rdson_mohm: float, theta_ja: float) -> ThermalResult:
        specs = {"rdson_mohm": rdson_mohm, "theta_ja": theta_ja}
        d = _buck.thermal_estimate(specs, self._required())["thermal"]
        if "error" in d:
            raise ValueError(f"buck.thermal: {d['error']}")
        return ThermalResult(**d)

    # ---- repr ----
    def __repr__(self) -> str:
        return (
            f"Buck(vin={self.vin}V, vout={self.vout}V, "
            f"iout={self.iout}A, fsw={self.fsw_khz}kHz)"
        )
