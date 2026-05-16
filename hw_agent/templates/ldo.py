"""LDO Regulator subsystem.

Pydantic-based component definition. To add a new component type, copy this file,
rename, and define the component-specific fields, checks, and calculations.
"""

from __future__ import annotations

from typing import ClassVar, Optional

from pydantic import BaseModel, Field

from hw_agent.subsystem import ElectronicSubsystem
from hw_agent.checks import Check, CheckResult, _missing, _missing_keys
from hw_agent.checks.shared import (
    vin_range,
    iout_capability,
    package_suitable,
    stock_threshold,
)
from hw_agent.calculations.ldo import thermal_analysis
from hw_agent.templates.base import SpecDefinition, SearchCriteria
from hw_agent.templates._parsers import (
    parse_current,
    parse_iq_ua,
    parse_package,
    parse_temp_max,
    parse_voltage,
    parse_voltage_range,
)


# ─── LDO-specific checks (single-use; live with the component) ────────────

def dropout_headroom(actual: dict, required: dict, *, min_headroom_mv: float = 100.0) -> CheckResult:
    """Vin − Vout exceeds dropout voltage with margin.

    Needs:  actual.vdrop  (mV)
    Reads:  required.vin, required.vout (V)
    """
    if _missing_keys(actual, ["vdrop"]):
        return _missing("Dropout headroom", "hard", ["actual.vdrop"])
    needed_req = [f"required.{k}" for k in ("vin", "vout") if required.get(k) is None]
    if needed_req:
        return _missing("Dropout headroom", "hard", needed_req)
    vin = required["vin"]
    vout = required["vout"]
    vdrop_mv = actual["vdrop"]
    available_mv = (vin - vout) * 1000.0
    return CheckResult(
        name="Dropout headroom",
        severity="hard",
        status="pass" if available_mv >= vdrop_mv + min_headroom_mv else "fail",
        actual=f"{available_mv:.0f} mV available, {vdrop_mv:.0f} mV needed",
        required=f"≥ {min_headroom_mv:.0f} mV margin above dropout",
    )


def quiescent_current_reasonable(actual: dict, required: dict, *, max_iq_ua: float = 200.0) -> CheckResult:
    """Quiescent current is below threshold (battery-friendliness).

    Needs:  actual.iq  (µA)
    """
    if _missing_keys(actual, ["iq"]):
        return _missing("Quiescent current", "soft", ["actual.iq"])
    iq = actual["iq"]
    return CheckResult(
        name="Quiescent current",
        severity="soft",
        status="pass" if iq <= max_iq_ua else "fail",
        actual=f"{iq:.1f} µA",
        required=f"≤ {max_iq_ua:.0f} µA (battery-friendly)",
    )


def junction_temperature(actual: dict, required: dict, *, tj_max_c: float = 125.0) -> CheckResult:
    """Computed Tj at the required ambient stays below tj_max (LDO/linear-dissipation model).

    Needs:   actual.theta_ja  (°C/W)
    Reads:   required.vin, required.vout, required.iout, required.ambient_c
    """
    if _missing_keys(actual, ["theta_ja"]):
        return _missing("Junction temperature", "hard", ["actual.theta_ja"])
    needed_req = [k for k in ("vin", "vout", "iout") if required.get(k) is None]
    if needed_req:
        return _missing("Junction temperature", "hard", [f"required.{k}" for k in needed_req])

    vin = required["vin"]
    vout = required["vout"]
    iout = required["iout"]
    ambient = required.get("ambient_c", 40.0)
    theta_ja = actual["theta_ja"]
    pdiss = (vin - vout) * iout
    tj = ambient + pdiss * theta_ja
    margin = tj_max_c - tj
    return CheckResult(
        name="Junction temperature",
        severity="hard",
        status="pass" if tj < tj_max_c else "fail",
        actual=f"Tj = {tj:.1f} °C (Pdiss {pdiss:.2f} W, θJA {theta_ja:g} °C/W, ambient {ambient:g} °C)",
        required=f"Tj < {tj_max_c:.0f} °C",
        note=f"Margin: {margin:.1f} °C" + (" — MARGINAL" if 0 < margin < 15 else ""),
    )


# ─── Engineer requirements (the questionnaire) ────────────────────────────

class LdoRequirements(BaseModel):
    """What the engineer answers when scoping an LDO."""

    vin: float = Field(
        ..., gt=0, le=40,
        description="Input voltage",
        examples=[5.0, 3.7, 7.4],
        json_schema_extra={
            "unit": "V",
            "casual_prompt": "What's feeding this regulator? Another regulator's output? A battery directly?",
            "infer_from": {
                "5v rail": {"value": 5.0}, "3.3v rail": {"value": 3.3},
                "usb": {"value": 5.0},
                "battery 1s": {"value": 3.7, "also_set": {"vin_max": 4.2}},
                "battery 2s": {"value": 7.4, "also_set": {"vin_max": 8.4}},
            },
        },
    )
    vout: float = Field(
        ..., gt=0, le=20,
        description="Output voltage",
        examples=[3.3, 1.8, 2.5],
        json_schema_extra={
            "unit": "V",
            "casual_prompt": "What are you powering? A microcontroller? Sensors?",
            "infer_from": {"mcu": {"value": 3.3}, "sensors": {"value": 3.3},
                           "logic": {"value": 3.3}, "1.8v core": {"value": 1.8}},
        },
    )
    iout: float = Field(
        ..., gt=0, le=5.0,
        description="Maximum continuous output current",
        examples=[0.150, 0.300, 0.500],
        json_schema_extra={
            "unit": "A",
            "casual_prompt": "How much current does this rail need to deliver?",
            "infer_from": {
                "mcu only": {"value": 0.150}, "mcu + sensors": {"value": 0.300},
                "mcu + ble": {"value": 0.200}, "mcu + wifi": {"value": 0.500},
                "light load": {"value": 0.100}, "heavy load": {"value": 0.500},
            },
        },
    )
    iout_margin: float = Field(
        default=1.2, ge=1.0, le=3.0,
        description="Safety margin applied to iout. Default 1.2× — a 0.220 A target needs a part rated ≥0.264 A. Override to 1.0 to disable.",
        json_schema_extra={"unit": "×"},
    )
    ambient_c: float = Field(
        default=40.0, ge=-40, le=125,
        description="Maximum ambient temperature",
        json_schema_extra={
            "unit": "°C",
            "casual_prompt": "Where does this board live? Office? Car? Outside?",
            "infer_from": {"indoor": {"value": 40}, "outdoor": {"value": 60},
                           "vehicle": {"value": 85}, "industrial": {"value": 85}},
        },
    )
    allowed_packages: list[str] = Field(
        default_factory=lambda: ["SOT-23-5", "SOT-23-6", "SOT-223", "SOT-89"],
    )


# ─── Datasheet specs (filled in over time) ────────────────────────────────

class LdoActuals(BaseModel):
    """Specs extracted from a candidate part's datasheet. All optional."""
    vin_min: Optional[float] = Field(default=None, le=40, description="Min Vin",
                                     json_schema_extra={"unit": "V"})
    vin_max: Optional[float] = Field(default=None, le=40, description="Max Vin",
                                     json_schema_extra={"unit": "V"})
    iout_max: Optional[float] = Field(
        default=None, le=5,
        description="Max output current (Amps). LDOs above 5 A are exotic — if you're hitting this bound you likely passed mA; convert before storing.",
        json_schema_extra={"unit": "A"},
    )
    vdrop: Optional[float] = Field(default=None, description="Dropout voltage",
                                   json_schema_extra={"unit": "mV"})
    iq: Optional[float] = Field(default=None, description="Quiescent current",
                                json_schema_extra={"unit": "µA"})
    psrr: Optional[float] = Field(default=None, description="Power supply rejection ratio",
                                  json_schema_extra={"unit": "dB"})
    theta_ja: Optional[float] = Field(default=None, description="Thermal resistance, junction-to-ambient",
                                      json_schema_extra={"unit": "°C/W"})
    tsd: Optional[float] = Field(default=None, description="Thermal shutdown",
                                 json_schema_extra={"unit": "°C"})
    package: Optional[str] = None
    stock: Optional[int] = Field(default=None, description="JLCPCB stock count")

    # ── Vendor extractors ──────────────────────────────────────────────────

    @classmethod
    def from_jlc(cls, raw: dict) -> "LdoActuals":
        """Extract from pcbparts jlc_get_part response.

        JLC carries: max Vin (often as single value, not a range), iout_max,
        vdrop ('1.1V@(800mA)' → 1100 mV), iq ('standby current'), package,
        stock, PSRR. theta_ja, tsd usually need datasheet VLM.
        """
        specs = raw.get("specs", {}) or {}
        vmax_raw = specs.get("Voltage - Input") or specs.get("Voltage - Supply") or specs.get("Voltage - Input(DC)")
        vin_min, vin_max = parse_voltage_range(vmax_raw)
        # JLC frequently gives a single Vmax ("15V") rather than a range.
        # parse_voltage_range returns (val, None) in that case — treat val as vin_max.
        if vin_max is None and vin_min is not None:
            vin_min, vin_max = None, vin_min

        # Vdrop: "1.1V@(800mA)" → 1.1 V → 1100 mV
        vdrop_raw = specs.get("Voltage Dropout") or specs.get("Dropout Voltage")
        vdrop_mv = None
        if vdrop_raw and vdrop_raw != "-":
            v = parse_voltage(vdrop_raw)
            if v is not None:
                vdrop_mv = v * 1000.0

        # PSRR: "72dB@(120Hz)" → 72.0
        psrr_raw = specs.get("Power Supply Rejection Ratio (PSRR)") or specs.get("PSRR")
        psrr = None
        if psrr_raw and psrr_raw != "-":
            from hw_agent.templates._parsers import _first_number
            psrr = _first_number(psrr_raw)

        # Iq: JLC uses "standby current" (lowercase) — case insensitive lookup
        iq_raw = None
        for k in specs:
            if k.lower() in ("standby current", "quiescent current", "quiescent current (iq)",
                             "current - quiescent"):
                iq_raw = specs[k]
                break
        iq = parse_iq_ua(iq_raw) if iq_raw and iq_raw != "-" else None

        return cls(
            vin_min=vin_min,
            vin_max=vin_max,
            iout_max=parse_current(specs.get("Output Current") or specs.get("Current - Output")),
            vdrop=vdrop_mv,
            iq=iq,
            psrr=psrr,
            package=parse_package(raw.get("package")),
            stock=raw.get("stock"),
        )

    @classmethod
    def from_digikey(cls, raw: dict) -> "LdoActuals":
        """Extract from pcbparts digikey_get_part response."""
        result = raw["results"][0] if "results" in raw else raw
        p = result.get("parameters", {}) or {}
        pkg = parse_package(p.get("Supplier Device Package") or p.get("Package / Case"))
        return cls(
            vin_min=parse_voltage(p.get("Voltage - Input (Min)")),
            vin_max=parse_voltage(p.get("Voltage - Input (Max)")),
            iout_max=parse_current(p.get("Current - Output")),
            vdrop=None,
            iq=parse_iq_ua(p.get("Current - Quiescent (Iq)")),
            psrr=None,
            tsd=parse_temp_max(p.get("Operating Temperature")) if p.get("Operating Temperature") else None,
            package=pkg,
            stock=result.get("stock"),
        )

    @classmethod
    def from_mouser(cls, raw: dict) -> "LdoActuals":
        """Extract from pcbparts mouser_get_part response. Thin — mostly None."""
        result = raw["results"][0] if "results" in raw else raw
        p = result.get("parameters", {}) or {}
        vin_min = parse_voltage(p.get("Input Voltage-Min") or p.get("Operating Voltage Min"))
        vin_max = parse_voltage(p.get("Input Voltage-Max") or p.get("Operating Voltage Max"))
        return cls(
            vin_min=vin_min,
            vin_max=vin_max,
            iout_max=parse_current(p.get("Output Current") or p.get("Current - Output")),
            package=parse_package(p.get("Package / Case") or p.get("Mounting Style")),
            stock=result.get("stock"),
        )


# ─── Subsystem class ──────────────────────────────────────────────────────

class LdoSubsystem(ElectronicSubsystem):
    """A low-dropout linear voltage regulator subsystem."""

    category: ClassVar[str] = "ldo"
    description: ClassVar[str] = "Low-dropout linear voltage regulator"

    Requirements: ClassVar[type[BaseModel]] = LdoRequirements
    Actuals: ClassVar[type[BaseModel]] = LdoActuals

    ai_instructions: ClassVar[str] = (
        "UNITS: every requirement has an `unit` in q_load — read it. If the engineer "
        "states a number without a unit (e.g. '220' instead of '220 mA' or '0.22 A'), "
        "ALWAYS confirm the unit in conversation before calling subsystem_add. Convert "
        "to the field's expected unit before commit (iout stores Amps, so 220 mA → 0.22). "
        "Iout requirement is multiplied by `iout_margin` (default 1.2× — a 0.220 A target needs ≥0.264 A). "
        "Override to 1.0 only if there's a separate margin justification. "
        "LDO efficiency = Vout/Vin, so the (Vin−Vout) drop dissipates as heat. "
        "If Pdiss > ~0.5 W, switch topologies (a buck dissipates much less). "
        "Package θJA matters: small packages without thermal pads (e.g. SOT-23-5 ≈ 250 °C/W) "
        "overheat fast at >100 mA load. Iq matters for battery-powered loads. "
        "Dropout headroom = (Vin − Vout) − Vdrop must stay positive at minimum input voltage."
    )

    checks: ClassVar[list[Check]] = [
        vin_range,                     # shared
        iout_capability,               # shared
        dropout_headroom,              # local
        junction_temperature,          # local
        quiescent_current_reasonable,  # local
        package_suitable,              # shared
        stock_threshold,               # shared
    ]

    calculations: ClassVar[list] = [thermal_analysis]

    extract_specs: ClassVar[list[SpecDefinition]] = [
        SpecDefinition(name="Min Vin", key="vin_min", unit="V",
                       aliases=["VIN min", "Min input voltage"]),
        SpecDefinition(name="Max Vin", key="vin_max", unit="V",
                       aliases=["VIN max", "Max input voltage"]),
        SpecDefinition(name="Maximum Output Current", key="iout_max", unit="A",
                       aliases=["IOUT", "Output Current", "Load Current", "IOUT(MAX)"]),
        SpecDefinition(name="Dropout Voltage", key="vdrop", unit="mV",
                       aliases=["VDROP", "VDO", "Dropout"],
                       regex_hints=[r"dropout\s+voltage.*?([\d\.]+)\s*mV"]),
        SpecDefinition(name="Quiescent Current", key="iq", unit="µA",
                       aliases=["IQ", "Ground Current", "Supply Current"]),
        SpecDefinition(name="PSRR", key="psrr", unit="dB", required=False,
                       aliases=["Power Supply Rejection", "Ripple Rejection"]),
        SpecDefinition(name="Theta-JA", key="theta_ja", unit="°C/W",
                       aliases=["θJA", "Thermal Resistance"]),
        SpecDefinition(name="Thermal Shutdown", key="tsd", unit="°C", required=False),
    ]

    page_hints: ClassVar[dict[str, list[int]]] = {
        "absolute_maximum_ratings": [1, 2],
        "electrical_characteristics": [2, 3, 4, 5],
        "thermal_data": [1, 2, 3],
        "application_circuit": [0, 1, 7, 8],
        "pinout": [0, 1],
    }

    searches: ClassVar[list[SearchCriteria]] = [
        SearchCriteria(query="{vout}V LDO",
                       subcategory="Linear Voltage Regulators(LDO)",
                       packages=["SOT-23-5", "SOT-23", "SOT-23-6"], sort_by="stock"),
        SearchCriteria(query="{vout}V LDO 1A",
                       subcategory="Linear Voltage Regulators(LDO)",
                       packages=["SOT-23-5", "SOT-223", "SOT-89"], sort_by="stock"),
        SearchCriteria(query="{vout}V LDO",
                       subcategory="Linear Voltage Regulators(LDO)", sort_by="price"),
    ]

    design_rule_topics: ClassVar[list[str]] = ["ldo", "power", "decoupling"]

    requirements: LdoRequirements
    actuals: LdoActuals = Field(default_factory=LdoActuals)
