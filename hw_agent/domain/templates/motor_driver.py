"""H-bridge motor driver (brushed DC)."""

from __future__ import annotations

from typing import ClassVar, Optional

from pydantic import BaseModel, Field

from hw_agent.core.subsystem import ElectronicSubsystem
from hw_agent.domain.checks import Check
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


# ─── Models ─────────────────────────────────────────────────────────────────

class MotorDriverRequirements(BaseModel):
    """Engineer answers when scoping a motor driver."""
    channels: int = Field(..., ge=1, le=8, description="Motor channels needed")
    current_per_channel: float = Field(
        ..., gt=0, le=20,
        description="Max current per channel",
        json_schema_extra={"unit": "A"},
    )
    motor_voltage: float = Field(..., gt=0, le=60, description="Motor supply voltage",
                                 json_schema_extra={"unit": "V"})
    control_interface: str = Field(default="pwm_dir",
                                   description="pwm_dir, phase_enable, i2c, spi")
    ambient_c: float = Field(default=40.0, ge=-40, le=125)
    allowed_packages: list[str] = Field(
        default_factory=lambda: ["SOIC-8-EP", "TSSOP-16-EP", "QFN", "DFN"],
    )


class MotorDriverActuals(BaseModel):
    """Extracted specs from a motor driver's datasheet."""
    channels: Optional[int] = None
    iout_per_ch: Optional[float] = Field(default=None, le=50,
        description="Continuous Iout per channel — Amps. >50 A is implausible for an integrated driver; convert from mA before storing.",
        json_schema_extra={"unit": "A"})
    ipeak: Optional[float] = Field(default=None, le=100, description="Peak Iout per channel",
                                   json_schema_extra={"unit": "A"})
    vm_min: Optional[float] = Field(default=None, le=100, description="Min motor supply",
                                    json_schema_extra={"unit": "V"})
    vm_max: Optional[float] = Field(default=None, le=100, description="Max motor supply",
                                    json_schema_extra={"unit": "V"})
    vlogic_min: Optional[float] = Field(default=None, description="Min logic supply",
                                        json_schema_extra={"unit": "V"})
    vlogic_max: Optional[float] = Field(default=None, description="Max logic supply",
                                        json_schema_extra={"unit": "V"})
    rdson_mohm: Optional[float] = Field(default=None, description="High-side FET RDS(on)",
                                        json_schema_extra={"unit": "mΩ"})
    interfaces: Optional[list[str]] = Field(default=None,
        description="Control interfaces supported (e.g. ['pwm_dir', 'phase_enable']).")
    theta_ja: Optional[float] = Field(default=None, description="Thermal resistance, junction-to-ambient",
                                      json_schema_extra={"unit": "°C/W"})
    tsd: Optional[float] = Field(default=None, description="Thermal shutdown",
                                 json_schema_extra={"unit": "°C"})
    package: Optional[str] = None
    stock: Optional[int] = Field(default=None, description="JLCPCB stock count")

    # ── Vendor extractors ──────────────────────────────────────────────────

    @staticmethod
    def _parse_channels(s: Optional[str]) -> Optional[int]:
        """'Dual' -> 2  ·  'Single' -> 1  ·  'Quad' -> 4  ·  '2' -> 2"""
        if not s:
            return None
        sl = s.lower()
        m = {"single": 1, "dual": 2, "triple": 3, "quad": 4}
        for k, v in m.items():
            if k in sl:
                return v
        n = _first_number(s)
        return int(n) if n else None

    @staticmethod
    def _parse_interfaces_motor(s: Optional[str]) -> Optional[list[str]]:
        """'PWM' -> ['pwm_dir']  ·  'STEP/DIR' -> ['step_dir']  ·  'I2C' -> ['i2c']"""
        if not s:
            return None
        sl = s.lower()
        out: list[str] = []
        if "pwm" in sl:
            out.append("pwm_dir")
        if "phase" in sl and "enable" in sl:
            out.append("phase_enable")
        if "step" in sl and "dir" in sl:
            out.append("step_dir")
        if "i2c" in sl:
            out.append("i2c")
        if "spi" in sl:
            out.append("spi")
        return out or None

    @classmethod
    def from_jlc(cls, raw: dict) -> "MotorDriverActuals":
        """Extract from pcbparts jlc_get_part response.

        Real JLC keys observed (DRV8833PWPR):
          'Motor Drive Voltage(Vm)', 'Number of H-bridges', 'Output Current',
          'Drive Type', 'RDS(on)', 'Interface' (often '-').
        """
        specs = raw.get("specs", {}) or {}
        vm_raw = (specs.get("Motor Drive Voltage(Vm)")
                  or specs.get("Voltage - Load")
                  or specs.get("Voltage - Supply"))
        vm_min, vm_max = parse_voltage_range(vm_raw)
        ch = cls._parse_channels(
            specs.get("Number of H-bridges")
            or specs.get("Number of Outputs/Drivers")
            or specs.get("Channels")
        )
        # JLC often has Interface "-" meaning no I2C/SPI; brushed-DC H-bridges
        # default to PWM/DIR or PHASE/EN at the pin level.
        iface_raw = specs.get("Interface") or specs.get("Control Method")
        if iface_raw == "-":
            iface_raw = None
        rdson_raw = specs.get("RDS(on)") or specs.get("RDS(On)") or specs.get("Rdson")
        if rdson_raw == "-":
            rdson_raw = None
        return cls(
            channels=ch,
            iout_per_ch=parse_current(specs.get("Output Current") or specs.get("Current - Output (Max)")),
            ipeak=parse_current(specs.get("Peak Output Current")),
            vm_min=vm_min,
            vm_max=vm_max,
            rdson_mohm=parse_resistance_mohm(rdson_raw) if rdson_raw else None,
            interfaces=cls._parse_interfaces_motor(iface_raw),
            package=parse_package(raw.get("package")),
            stock=raw.get("stock"),
        )

    @classmethod
    def from_digikey(cls, raw: dict) -> "MotorDriverActuals":
        result = raw["results"][0] if "results" in raw else raw
        p = result.get("parameters", {}) or {}
        pkg = parse_package(p.get("Supplier Device Package") or p.get("Package / Case"))
        return cls(
            channels=cls._parse_channels(p.get("Number of Outputs/Drivers") or p.get("Outputs")),
            iout_per_ch=parse_current(p.get("Current - Output")),
            ipeak=parse_current(p.get("Output Current - Peak")),
            vm_min=parse_voltage(p.get("Voltage - Supply (Min)") or p.get("Voltage - Load (Min)")),
            vm_max=parse_voltage(p.get("Voltage - Supply (Max)") or p.get("Voltage - Load (Max)")),
            rdson_mohm=parse_resistance_mohm(p.get("RDS(On) (Typ)") or p.get("RDS(on)")),
            interfaces=cls._parse_interfaces_motor(p.get("Interface")),
            package=pkg,
            stock=result.get("stock"),
        )

    @classmethod
    def from_mouser(cls, raw: dict) -> "MotorDriverActuals":
        result = raw["results"][0] if "results" in raw else raw
        p = result.get("parameters", {}) or {}
        return cls(
            iout_per_ch=parse_current(p.get("Output Current")),
            vm_min=parse_voltage(p.get("Operating Voltage Min")),
            vm_max=parse_voltage(p.get("Operating Voltage Max")),
            package=parse_package(p.get("Package / Case") or p.get("Mounting Style")),
            stock=result.get("stock"),
        )


class MotorDriverSubsystem(ElectronicSubsystem):
    """An H-bridge motor driver subsystem."""

    category: ClassVar[str] = "motor_driver"
    description: ClassVar[str] = "H-bridge driver for brushed DC motors"

    Requirements: ClassVar[type[BaseModel]] = MotorDriverRequirements
    Actuals: ClassVar[type[BaseModel]] = MotorDriverActuals

    ai_instructions: ClassVar[str] = (
        "UNITS: every requirement has an `unit` in q_load — read it. If the engineer "
        "states a number without a unit (e.g. '600' instead of '600 mA' or '0.6 A'), "
        "ALWAYS confirm the unit in conversation before calling subsystem_add. "
        "current_per_channel stores Amps; convert mA → A before commit. "
        "Self-heating dominates: Pdiss ≈ I² × RDS(on) × duty per channel; multiply by channel count. "
        "Above ~1A continuous, package thermal pad is mandatory. "
        "Above ~5A or ~30V, integrated H-bridges run out of headroom — external FETs with a gate driver "
        "typically beat integrated parts. Verify peak current rating ≥ stall current of the motor, not just continuous."
    )

    checks: ClassVar[list[Check]] = [
        vm_range_covers,                # shared (motor family)
        channel_count_sufficient,       # shared (motor family + PWM driver)
        per_channel_current_capable,    # shared (motor family)
        package_suitable,               # shared (universal)
        stock_threshold,                # shared (universal)
    ]

    calculations: ClassVar[list] = [thermal_estimate]

    extract_specs: ClassVar[list[SpecDefinition]] = [
        SpecDefinition(name="Channels", key="channels", unit=""),
        SpecDefinition(name="Iout per channel", key="iout_per_ch", unit="A"),
        SpecDefinition(name="Peak Iout", key="ipeak", unit="A"),
        SpecDefinition(name="Min VM", key="vm_min", unit="V"),
        SpecDefinition(name="Max VM", key="vm_max", unit="V"),
        SpecDefinition(name="RDS(on)", key="rdson_mohm", unit="mΩ"),
        SpecDefinition(name="Interface", key="interface", unit=""),
        SpecDefinition(name="Theta-JA", key="theta_ja", unit="°C/W"),
    ]

    page_hints: ClassVar[dict[str, list[int]]] = {
        "absolute_maximum_ratings": [1, 2],
        "electrical_characteristics": [2, 3, 4],
        "thermal_data": [1, 2, 5],
        "application_circuit": [0, 1, 6, 7],
    }

    searches: ClassVar[list[SearchCriteria]] = [
        SearchCriteria(query="dual H-bridge {motor_voltage}V {current_per_channel}A",
                       subcategory="Motor Driver ICs", sort_by="stock"),
        SearchCriteria(query="brushed DC motor driver {current_per_channel}A",
                       subcategory="Motor Driver ICs", sort_by="stock"),
    ]

    design_rule_topics: ClassVar[list[str]] = ["motor_driver", "thermal", "decoupling"]

    requirements: MotorDriverRequirements
    actuals: MotorDriverActuals = Field(default_factory=MotorDriverActuals)
