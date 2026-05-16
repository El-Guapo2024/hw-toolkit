"""Stepper motor driver IC."""

from __future__ import annotations

from typing import ClassVar, Optional

from pydantic import BaseModel, Field

from hw_agent.core.subsystem import ElectronicSubsystem
from hw_agent.domain.checks import Check, CheckResult, _missing, _missing_keys
from hw_agent.domain.checks.shared import (
    vm_range_covers,
    channel_count_sufficient,
    per_channel_current_capable,
    package_suitable,
    stock_threshold,
)
from hw_agent.domain.calculations.motor_driver import thermal_estimate
from hw_agent.domain.templates.base import SpecDefinition, SearchCriteria
from hw_agent.domain.templates._parsers import (
    _first_number,
    parse_current,
    parse_package,
    parse_resistance_mohm,
    parse_voltage,
    parse_voltage_range,
)


# ─── Stepper-specific checks (single-use; live with the component) ─────────

def microstep_capable(actual: dict, required: dict) -> CheckResult:
    """Stepper driver supports the required microstep resolution.

    Needs:  actual.microstep_max
    Reads:  required.microstepping  (int — 1, 8, 16, 32, etc.)
    """
    target = required.get("microstepping")
    if target is None:
        return CheckResult(name="Microstep", severity="soft", status="pass",
                           actual="(no constraint)", required="(none specified)")
    if _missing_keys(actual, ["microstep_max"]):
        return _missing("Microstep", "soft", ["actual.microstep_max"], required_str=f"≥ 1/{target}")
    n = actual["microstep_max"]
    return CheckResult(
        name="Microstep",
        severity="soft",
        status="pass" if n >= target else "fail",
        actual=f"1/{n}",
        required=f"≥ 1/{target}",
    )


# ─── Models ─────────────────────────────────────────────────────────────────

class StepperDriverRequirements(BaseModel):
    """Engineer answers when scoping a stepper driver."""
    channels: int = Field(default=1, ge=1, le=8, description="Stepper channels")
    current_per_phase: float = Field(
        ..., gt=0, le=5,
        description="Max RMS current per phase",
        json_schema_extra={"unit": "A"},
    )
    motor_voltage: float = Field(..., gt=0, le=48, description="Motor supply voltage",
                                 json_schema_extra={"unit": "V"})
    microstepping: int = Field(default=16, ge=1,
                               description="Microsteps per full step (1, 2, 4, 8, 16, 32, …)")
    control_interface: str = Field(default="step_dir", description="step_dir, spi, or uart")
    ambient_c: float = Field(default=40.0, ge=-40, le=125)
    allowed_packages: list[str] = Field(
        default_factory=lambda: ["TSSOP-16-EP", "QFN", "DFN", "HTSSOP-28"],
    )


class StepperDriverActuals(BaseModel):
    """Extracted specs from a stepper driver's datasheet."""
    channels: Optional[int] = None
    iout_per_ch: Optional[float] = Field(default=None, le=10,
        description="Continuous current per phase — Amps. >10 A is unusual for a stepper IC; convert from mA before storing.",
        json_schema_extra={"unit": "A"})
    ipeak: Optional[float] = Field(default=None, le=20, description="Peak per-phase current",
                                   json_schema_extra={"unit": "A"})
    vm_min: Optional[float] = Field(default=None, le=80, description="Min motor supply",
                                    json_schema_extra={"unit": "V"})
    vm_max: Optional[float] = Field(default=None, le=80, description="Max motor supply",
                                    json_schema_extra={"unit": "V"})
    microstep_max: Optional[int] = Field(default=None, description="Max microsteps per full step")
    rdson_mohm: Optional[float] = Field(default=None, description="FET RDS(on)",
                                        json_schema_extra={"unit": "mΩ"})
    ilim_method: Optional[str] = Field(default=None, description="Current-limit method (sense_resistor, internal, etc.)")
    has_thermal_pad: Optional[bool] = None
    interfaces: Optional[list[str]] = Field(default=None,
        description="Control interfaces supported (e.g. ['step_dir', 'spi', 'uart']).")
    theta_ja: Optional[float] = Field(default=None, description="Thermal resistance, junction-to-ambient",
                                      json_schema_extra={"unit": "°C/W"})
    package: Optional[str] = None
    stock: Optional[int] = Field(default=None, description="JLCPCB stock count")

    # ── Vendor extractors ──────────────────────────────────────────────────

    @staticmethod
    def _parse_microstep(s: Optional[str]) -> Optional[int]:
        """'1/32 step' -> 32  ·  '32 microsteps' -> 32  ·  '1/16, 1/32' -> 32 (max)"""
        if not s:
            return None
        import re
        # match fractions like 1/32, or bare ints
        fracs = re.findall(r"1/(\d+)", s)
        if fracs:
            return max(int(x) for x in fracs)
        nums = re.findall(r"[-+]?\d+", s)
        return max(int(x) for x in nums) if nums else None

    @staticmethod
    def _parse_interfaces_stepper(s: Optional[str]) -> Optional[list[str]]:
        if not s:
            return None
        sl = s.lower()
        out: list[str] = []
        if "step" in sl and "dir" in sl:
            out.append("step_dir")
        if "spi" in sl:
            out.append("spi")
        if "uart" in sl:
            out.append("uart")
        return out or None

    @classmethod
    def from_jlc(cls, raw: dict) -> "StepperDriverActuals":
        """Extract from pcbparts jlc_get_part response.

        Real JLC keys observed (DRV8834RGER, DRV8846RGER):
          'Motor Drive Voltage(Vm)', 'Step Resolution', 'Output Current',
          'RDS(on)', 'Interface' ('STEP/DIR').
        """
        specs = raw.get("specs", {}) or {}
        vm_raw = (specs.get("Motor Drive Voltage(Vm)")
                  or specs.get("Voltage - Load")
                  or specs.get("Voltage - Supply"))
        vm_min, vm_max = parse_voltage_range(vm_raw)
        rdson_raw = specs.get("RDS(on)") or specs.get("RDS(On)") or specs.get("Rdson")
        if rdson_raw == "-":
            rdson_raw = None
        return cls(
            channels=1,  # stepper drivers are single-channel by definition
            iout_per_ch=parse_current(specs.get("Output Current") or specs.get("Current - Output (Max)")),
            ipeak=parse_current(specs.get("Peak Output Current")),
            vm_min=vm_min,
            vm_max=vm_max,
            microstep_max=cls._parse_microstep(specs.get("Step Resolution") or specs.get("Microstep") or specs.get("Microstepping")),
            rdson_mohm=parse_resistance_mohm(rdson_raw) if rdson_raw else None,
            interfaces=cls._parse_interfaces_stepper(specs.get("Interface") or specs.get("Control Method")),
            package=parse_package(raw.get("package")),
            stock=raw.get("stock"),
        )

    @classmethod
    def from_digikey(cls, raw: dict) -> "StepperDriverActuals":
        result = raw["results"][0] if "results" in raw else raw
        p = result.get("parameters", {}) or {}
        pkg = parse_package(p.get("Supplier Device Package") or p.get("Package / Case"))
        return cls(
            channels=1,
            iout_per_ch=parse_current(p.get("Current - Output")),
            ipeak=parse_current(p.get("Output Current - Peak")),
            vm_min=parse_voltage(p.get("Voltage - Supply (Min)") or p.get("Voltage - Load (Min)")),
            vm_max=parse_voltage(p.get("Voltage - Supply (Max)") or p.get("Voltage - Load (Max)")),
            microstep_max=cls._parse_microstep(p.get("Step Resolution") or p.get("Microstepping")),
            rdson_mohm=parse_resistance_mohm(p.get("RDS(On) (Typ)") or p.get("RDS(on)")),
            interfaces=cls._parse_interfaces_stepper(p.get("Interface")),
            package=pkg,
            stock=result.get("stock"),
        )

    @classmethod
    def from_mouser(cls, raw: dict) -> "StepperDriverActuals":
        result = raw["results"][0] if "results" in raw else raw
        p = result.get("parameters", {}) or {}
        return cls(
            iout_per_ch=parse_current(p.get("Output Current")),
            vm_min=parse_voltage(p.get("Operating Voltage Min")),
            vm_max=parse_voltage(p.get("Operating Voltage Max")),
            package=parse_package(p.get("Package / Case") or p.get("Mounting Style")),
            stock=result.get("stock"),
        )


class StepperDriverSubsystem(ElectronicSubsystem):
    """A stepper motor driver subsystem."""

    category: ClassVar[str] = "stepper_driver"
    description: ClassVar[str] = "Dedicated stepper motor driver IC with current limiting"

    Requirements: ClassVar[type[BaseModel]] = StepperDriverRequirements
    Actuals: ClassVar[type[BaseModel]] = StepperDriverActuals

    ai_instructions: ClassVar[str] = (
        "UNITS: every requirement has an `unit` in q_load — read it. If the engineer "
        "states a number without a unit (e.g. '800' instead of '800 mA' or '0.8 A'), "
        "ALWAYS confirm the unit in conversation before calling subsystem_add. "
        "current_per_phase stores Amps; convert mA → A before commit. "
        "Microstepping affects audible noise more than position accuracy — at high microstep counts, "
        "torque per step decreases and effective step accuracy is bounded by motor mechanics anyway. "
        "Quiet variants (e.g. StealthChop-style PWM modes) trade some torque for very low audible noise. "
        "Sense resistors set the current limit on chips without internal sensing — size for the chosen Iphase. "
        "Always thermal-pad ground at the manufacturer's recommended copper area; derating is steep otherwise."
    )

    checks: ClassVar[list[Check]] = [
        vm_range_covers,                # shared (motor family)
        channel_count_sufficient,       # shared (motor family + PWM driver)
        per_channel_current_capable,    # shared (motor family)
        microstep_capable,              # local
        package_suitable,               # shared (universal)
        stock_threshold,                # shared (universal)
    ]

    calculations: ClassVar[list] = [thermal_estimate]

    extract_specs: ClassVar[list[SpecDefinition]] = [
        SpecDefinition(name="Channels", key="channels", unit=""),
        SpecDefinition(name="Iout per phase", key="iout_per_ch", unit="A"),
        SpecDefinition(name="Peak Iout", key="ipeak", unit="A"),
        SpecDefinition(name="Min VM", key="vm_min", unit="V"),
        SpecDefinition(name="Max VM", key="vm_max", unit="V"),
        SpecDefinition(name="Max microstep", key="microstep_max", unit=""),
        SpecDefinition(name="RDS(on)", key="rdson_mohm", unit="mΩ"),
        SpecDefinition(name="Theta-JA", key="theta_ja", unit="°C/W"),
    ]

    page_hints: ClassVar[dict[str, list[int]]] = {
        "absolute_maximum_ratings": [1, 2],
        "electrical_characteristics": [2, 3, 4],
        "thermal_data": [1, 2, 5],
    }

    searches: ClassVar[list[SearchCriteria]] = [
        SearchCriteria(query="stepper driver {current_per_phase}A {motor_voltage}V",
                       subcategory="Motor Driver ICs", sort_by="stock"),
        SearchCriteria(query="silent stepper driver microstepping",
                       subcategory="Motor Driver ICs", sort_by="stock"),
    ]

    design_rule_topics: ClassVar[list[str]] = ["stepper", "motor_driver", "thermal", "decoupling"]

    requirements: StepperDriverRequirements
    actuals: StepperDriverActuals = Field(default_factory=StepperDriverActuals)
