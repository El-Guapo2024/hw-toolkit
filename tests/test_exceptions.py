"""Typed exception hierarchy + accessor tests."""
from __future__ import annotations

from pathlib import Path

import pytest

import hw_toolkit as hw
from hw_toolkit.exceptions import HwToolkitError


def test_all_typed_errors_inherit_base() -> None:
    for cls in (
        hw.BundleValidationError,
        hw.CheckFailed,
        hw.DuplicateNetError,
        hw.EmptyNetError,
        hw.UnknownSubsystemError,
        hw.ERCViolation,
        hw.MultipleERCViolations,
        hw.FootprintMissingError,
        hw.RoutingFailedError,
        hw.DRCViolation,
        hw.MultipleDRCViolations,
        hw.KiCadCliMissingError,
        hw.KiCadCliRunError,
        hw.KiCadCliTimeoutError,
        hw.NoSvgProducedError,
    ):
        assert issubclass(cls, HwToolkitError), f"{cls.__name__} missed the base"


def test_bundle_validation_error_carries_errors_tuple() -> None:
    e = hw.BundleValidationError(path=Path("/tmp/x"), errors=("a", "b"))
    assert e.errors == ("a", "b")
    assert "2 error" in str(e)


def test_check_failed_carries_subsystem_id_and_label() -> None:
    e = hw.CheckFailed(subsystem_id="buck_3v3", label="thermal: 200 C")
    assert e.subsystem_id == "buck_3v3"
    assert "thermal" in str(e)


def test_unknown_subsystem_error_shows_known_keys() -> None:
    e = hw.UnknownSubsystemError(subsystem_id="missing", known=("buck", "mcu"))
    s = str(e)
    assert "missing" in s and "buck" in s and "mcu" in s


def test_duplicate_net_error_str() -> None:
    e = hw.DuplicateNetError(net_id="rail_3v3")
    assert "rail_3v3" in str(e)


def test_empty_net_error_shows_count() -> None:
    e = hw.EmptyNetError(net_id="dangling", member_count=1)
    assert "dangling" in str(e) and "1" in str(e)


def test_erc_violation_str_includes_refs() -> None:
    v = hw.ERCViolation(
        type="pin_not_connected", severity="error",
        description="Pin floating", refs=("U1.SDA", "U2.SDA"),
    )
    s = str(v)
    assert "U1.SDA" in s and "Pin floating" in s


def test_eval_report_truthy_when_no_fails() -> None:
    r = hw.EvalReport()
    assert bool(r) is True


def test_eval_report_falsy_when_fails_present() -> None:
    r = hw.EvalReport(fails=[hw.CheckFailed(subsystem_id="x", label="L")])
    assert bool(r) is False
    assert r.ok is False
