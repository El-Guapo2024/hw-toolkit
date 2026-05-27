"""Calc module tests — Buck operating-point math wrappers."""
from __future__ import annotations

import pytest

import hw_toolkit as hw


def test_buck_inductor_returns_positive_uh() -> None:
    b = hw.calc.Buck(vin=11.1, vout=3.3, iout=0.5)
    r = b.inductor()
    assert r.inductor_uh > 0
    assert 0 < r.duty_cycle < 1


def test_buck_output_cap_scales_with_target_ripple() -> None:
    b = hw.calc.Buck(vin=11.1, vout=3.3, iout=0.5)
    loose = b.output_cap(target_ripple_mv=100)
    tight = b.output_cap(target_ripple_mv=10)
    # Tighter ripple requires bigger cap.
    assert tight.min_cap_uf > loose.min_cap_uf


def test_buck_thermal_safe_at_low_power() -> None:
    b = hw.calc.Buck(vin=11.1, vout=3.3, iout=0.5)
    T = b.thermal(rdson_mohm=80, theta_ja=40)
    # 0.5 A * 0.08 Ω * duty ≈ 6 mW dissipation in switch — way safe.
    assert bool(T) is True
    assert T.margin_c > 50


def test_buck_thermal_unsafe_at_high_current() -> None:
    b = hw.calc.Buck(vin=11.1, vout=3.3, iout=10)
    T = b.thermal(rdson_mohm=500, theta_ja=200)
    # 10 A * 0.5 Ω = 5 W avg + 200 °C/W = huge Tj rise → unsafe
    assert bool(T) is False
    assert T.margin_c < 0


def test_buck_thermal_result_is_truthy_iff_safe() -> None:
    safe = hw.calc.Buck(vin=11.1, vout=3.3, iout=0.5).thermal(rdson_mohm=80, theta_ja=40)
    unsafe = hw.calc.Buck(vin=11.1, vout=3.3, iout=10).thermal(rdson_mohm=500, theta_ja=200)
    assert safe and not unsafe
