"""Cross-component checks — used by 2+ subsystem types.

A check makes it into this file ONLY if multiple component types use it. Single-use
checks live in their component's `templates/<name>.py` file (closer to the rest of
that component's knowledge).
"""

from __future__ import annotations

from . import CheckResult, _missing, _missing_keys


# ─── Power-converter checks (LDO + buck) ───────────────────────────────────

def vin_range(actual: dict, required: dict) -> CheckResult:
    """Part's input voltage range covers the required Vin.

    Used by: LDO, buck.
    Needs:   actual.vin_min, actual.vin_max
    Reads:   required.vin
    """
    target = required.get("vin")
    if target is None:
        return _missing("Vin range", "hard", ["required.vin"])
    needed = _missing_keys(actual, ["vin_min", "vin_max"])
    if needed:
        return _missing("Vin range", "hard", [f"actual.{k}" for k in needed],
                        required_str=f"{target} V")
    vmin = actual["vin_min"]
    vmax = actual["vin_max"]
    return CheckResult(
        name="Vin range",
        severity="hard",
        status="pass" if vmin <= target <= vmax else "fail",
        actual=f"{vmin}–{vmax} V",
        required=f"{target} V",
    )


def iout_capability(actual: dict, required: dict) -> CheckResult:
    """Part's max output current exceeds required (with margin).

    Margin is read from `required.iout_margin` (default 1.2×) so engineers can
    see and override it.

    Used by: LDO, buck.
    Needs:   actual.iout_max       (A — store in Amps, convert from mA at extraction)
    Reads:   required.iout         (A)  OR required.iout_max  (A)
             required.iout_margin  (default 1.2)
    """
    if _missing_keys(actual, ["iout_max"]):
        return _missing("Iout capability", "hard", ["actual.iout_max"])
    target = required.get("iout") or required.get("iout_max")
    if target is None:
        return _missing("Iout capability", "hard", ["required.iout (or required.iout_max)"])
    margin = required.get("iout_margin") or 1.2
    iout_max_a = actual["iout_max"]
    needed = target * margin
    return CheckResult(
        name="Iout capability",
        severity="hard",
        status="pass" if iout_max_a >= needed else "fail",
        actual=f"{iout_max_a:.2f} A",
        required=f"≥ {needed:.2f} A ({margin}× margin)",
    )


# ─── Digital / IC supply checks (MCU + IMU + PWM driver) ────────────────────

def supply_voltage_compatible(actual: dict, required: dict) -> CheckResult:
    """Part's supply voltage range covers the required VDD.

    Used by: MCU, IMU, PWM driver.
    Needs:   actual.vdd_min, actual.vdd_max  (V)
    Reads:   required.vdd  (V)
    """
    target = required.get("vdd")
    if target is None:
        return _missing("Supply voltage", "hard", ["required.vdd"])
    needed = _missing_keys(actual, ["vdd_min", "vdd_max"])
    if needed:
        return _missing("Supply voltage", "hard", [f"actual.{k}" for k in needed],
                        required_str=f"{target} V")
    vmin = actual["vdd_min"]
    vmax = actual["vdd_max"]
    return CheckResult(
        name="Supply voltage",
        severity="hard",
        status="pass" if vmin <= target <= vmax else "fail",
        actual=f"{vmin}–{vmax} V",
        required=f"{target} V",
    )


# ─── Universal checks (all component types) ─────────────────────────────────

_PACKAGE_FAMILY_ALIASES: dict[str, str] = {
    # JEDEC small-outline: SOP and SOIC are equivalent
    "SOP": "SOIC",
    # QFN family: thermally/mechanically-enhanced variants share footprint
    "MLF": "QFN", "MLP": "QFN",
    "VQFN": "QFN", "WQFN": "QFN", "UQFN": "QFN", "HVQFN": "QFN",
    # DFN family
    "VDFN": "DFN", "WDFN": "DFN", "UDFN": "DFN",
}


def _normalize_package(name: str) -> str:
    """Normalize a package name so equivalent JEDEC variants compare equal.

    Splits on '-' / '_' / spaces, maps each family token to its canonical name
    (SOP→SOIC, MLF/MLP/VQFN/WQFN→QFN, VDFN/WDFN→DFN), then rejoins. Numeric
    pin-count tokens pass through unchanged.

    Examples:
        "SOP-8-EP"      → "SOIC-8-EP"
        "VQFN-32"       → "QFN-32"
        "sot 23-5"      → "SOT-23-5"
    """
    n = name.upper().replace("_", "-").replace(" ", "-")
    while "--" in n:
        n = n.replace("--", "-")
    parts = [p for p in n.split("-") if p]
    return "-".join(_PACKAGE_FAMILY_ALIASES.get(p, p) for p in parts)


def package_suitable(actual: dict, required: dict) -> CheckResult:
    """Part's package is on the allow-list (if specified).

    Match rules (after JEDEC family normalization via `_normalize_package`):
      - Exact match: SOIC-8 ↔ SOIC-8
      - Family aliases: SOP-8-EP ↔ SOIC-8-EP (SOP and SOIC are JEDEC-equivalent)
      - Suffix variants: SOIC-8 ↔ SOIC-8-EP (exposed-pad / thermal-pad variants)
        and SOT-23 ↔ SOT-23-5 (when allow-list omits pin count)

    Used by: all subsystem types.
    Needs:   actual.package
    Reads:   required.allowed_packages (list[str], optional — passes if absent)
    """
    if _missing_keys(actual, ["package"]):
        return _missing("Package suitable", "soft", ["actual.package"])
    pkg = actual["package"]
    allowed = required.get("allowed_packages")
    if not allowed:
        return CheckResult(
            name="Package suitable", severity="soft", status="pass",
            actual=pkg, required="(no constraint)",
        )
    pkg_norm = _normalize_package(pkg)
    matched = False
    for a in allowed:
        a_norm = _normalize_package(a)
        if pkg_norm == a_norm:
            matched = True
            break
        if pkg_norm.startswith(a_norm + "-") or a_norm.startswith(pkg_norm + "-"):
            matched = True
            break
    return CheckResult(
        name="Package suitable",
        severity="soft",
        status="pass" if matched else "fail",
        actual=pkg,
        required=" / ".join(allowed),
    )


def stock_threshold(actual: dict, required: dict, *, min_stock: int = 500) -> CheckResult:
    """Part stock exceeds threshold for production reliability.

    Used by: all subsystem types.
    Needs:   actual.stock
    """
    if _missing_keys(actual, ["stock"]):
        return _missing("Stock threshold", "soft", ["actual.stock"])
    stock = actual["stock"]
    return CheckResult(
        name="Stock threshold",
        severity="soft",
        status="pass" if stock >= min_stock else "fail",
        actual=f"{stock:,} units",
        required=f"≥ {min_stock:,} units",
    )


# ─── Interface / sensor checks (IMU + PWM driver) ───────────────────────────

def interface_supported(actual: dict, required: dict) -> CheckResult:
    """Part's communication interface includes the requested one.

    Used by: IMU, PWM driver.
    Needs:   actual.interfaces  (list[str])
    Reads:   required.interface (str like "i2c" or "spi" or "i2c_or_spi")
    """
    target = required.get("interface")
    if target is None:
        return CheckResult(
            name="Interface", severity="hard", status="pass",
            actual="(no constraint)", required="(none specified)",
        )
    if _missing_keys(actual, ["interfaces"]):
        return _missing("Interface", "hard", ["actual.interfaces"], required_str=target)
    interfaces = [s.lower() for s in actual["interfaces"]]
    accepted = [t.strip().lower() for t in target.replace("_or_", ",").split(",")]
    found = next((a for a in accepted if a in interfaces), None)
    return CheckResult(
        name="Interface",
        severity="hard",
        status="pass" if found else "fail",
        actual=", ".join(interfaces),
        required=target,
    )


# ─── Motor / multi-channel checks (motor_driver + stepper + PWM driver) ─────

def channel_count_sufficient(actual: dict, required: dict) -> CheckResult:
    """Driver has enough independent channels.

    Used by: motor_driver, stepper, PWM driver.
    Needs:   actual.channels
    Reads:   required.channels
    """
    target = required.get("channels")
    if target is None:
        return _missing("Channel count", "hard", ["required.channels"])
    if _missing_keys(actual, ["channels"]):
        return _missing("Channel count", "hard", ["actual.channels"], required_str=f"≥ {target}")
    n = actual["channels"]
    return CheckResult(
        name="Channel count",
        severity="hard",
        status="pass" if n >= target else "fail",
        actual=f"{n} channels",
        required=f"≥ {target}",
    )


def vm_range_covers(actual: dict, required: dict) -> CheckResult:
    """Driver's motor-supply range covers the required motor voltage.

    Used by: motor_driver, stepper.
    Needs:   actual.vm_min, actual.vm_max  (V)
    Reads:   required.motor_voltage  (V)
    """
    target = required.get("motor_voltage")
    if target is None:
        return _missing("VM range", "hard", ["required.motor_voltage"])
    needed = _missing_keys(actual, ["vm_min", "vm_max"])
    if needed:
        return _missing("VM range", "hard", [f"actual.{k}" for k in needed],
                        required_str=f"{target} V")
    vmin, vmax = actual["vm_min"], actual["vm_max"]
    return CheckResult(
        name="VM range",
        severity="hard",
        status="pass" if vmin <= target <= vmax else "fail",
        actual=f"{vmin}–{vmax} V",
        required=f"{target} V",
    )


def per_channel_current_capable(actual: dict, required: dict, *, margin: float = 1.2) -> CheckResult:
    """Driver's per-channel current rating exceeds required (with margin).

    Used by: motor_driver, stepper.
    Needs:   actual.iout_per_ch  (A)
    Reads:   required.current_per_channel  (A)  OR required.current_per_phase (stepper)
    """
    target = required.get("current_per_channel") or required.get("current_per_phase")
    if target is None:
        return _missing("Per-channel current", "hard", ["required.current_per_channel"])
    if _missing_keys(actual, ["iout_per_ch"]):
        return _missing("Per-channel current", "hard", ["actual.iout_per_ch"],
                        required_str=f"≥ {target * margin:.2f} A")
    iout = actual["iout_per_ch"]
    needed = target * margin
    return CheckResult(
        name="Per-channel current",
        severity="hard",
        status="pass" if iout >= needed else "fail",
        actual=f"{iout:.2f} A/ch",
        required=f"≥ {needed:.2f} A/ch ({margin}× margin)",
    )
