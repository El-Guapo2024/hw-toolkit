"""IMU (Inertial Measurement Unit) — accel + gyro [+ optional mag]."""

from __future__ import annotations

from typing import ClassVar, Optional

from pydantic import BaseModel, Field

from hw_agent.core.subsystem import ElectronicSubsystem
from hw_agent.domain.checks import Check, CheckResult, _missing, _missing_keys
from hw_agent.domain.checks.shared import (
    supply_voltage_compatible,
    interface_supported,
    package_suitable,
    stock_threshold,
)
from hw_agent.domain.templates.base import SpecDefinition, SearchCriteria
from hw_agent.domain.templates._parsers import (
    _first_number,
    parse_iq_ua,
    parse_package,
    parse_voltage,
    parse_voltage_range,
)


# ─── IMU-specific checks (single-use; live with the component) ─────────────

def accel_range_sufficient(actual: dict, required: dict) -> CheckResult:
    """Accelerometer's max range covers the required range.

    Needs:  actual.accel_range_g  (max ±g)
    Reads:  required.accel_range_g
    """
    target = required.get("accel_range_g")
    if target is None:
        return CheckResult(name="Accel range", severity="hard", status="pass",
                           actual="(no constraint)", required="(none specified)")
    if _missing_keys(actual, ["accel_range_g"]):
        return _missing("Accel range", "hard", ["actual.accel_range_g"], required_str=f"≥ ±{target} g")
    n = actual["accel_range_g"]
    return CheckResult(
        name="Accel range",
        severity="hard",
        status="pass" if n >= target else "fail",
        actual=f"±{n} g",
        required=f"≥ ±{target} g",
    )


def gyro_range_sufficient(actual: dict, required: dict) -> CheckResult:
    """Gyro's max range covers the required range.

    Needs:  actual.gyro_range_dps
    Reads:  required.gyro_range_dps
    """
    target = required.get("gyro_range_dps")
    if target is None:
        return CheckResult(name="Gyro range", severity="hard", status="pass",
                           actual="(no constraint)", required="(none specified)")
    if _missing_keys(actual, ["gyro_range_dps"]):
        return _missing("Gyro range", "hard", ["actual.gyro_range_dps"], required_str=f"≥ ±{target} dps")
    n = actual["gyro_range_dps"]
    return CheckResult(
        name="Gyro range",
        severity="hard",
        status="pass" if n >= target else "fail",
        actual=f"±{n} dps",
        required=f"≥ ±{target} dps",
    )


def axes_sufficient(actual: dict, required: dict) -> CheckResult:
    """Sensor has the required number of axes.

    Needs:  actual.axes
    Reads:  required.axes  (e.g. 6 = 3-axis accel + 3-axis gyro, 9 = +mag)
    """
    target = required.get("axes")
    if target is None:
        return CheckResult(name="Axes", severity="hard", status="pass",
                           actual="(no constraint)", required="(none specified)")
    if _missing_keys(actual, ["axes"]):
        return _missing("Axes", "hard", ["actual.axes"], required_str=f"≥ {target}-axis")
    n = actual["axes"]
    return CheckResult(
        name="Axes",
        severity="hard",
        status="pass" if n >= target else "fail",
        actual=f"{n}-axis",
        required=f"≥ {target}-axis",
    )


# ─── Models ─────────────────────────────────────────────────────────────────

class ImuRequirements(BaseModel):
    """Engineer answers when scoping an IMU."""
    axes: int = Field(default=6, ge=3, le=9,
                      description="Total axes (3=accel only, 6=accel+gyro, 9=+mag)")
    interface: str = Field(default="i2c", description="i2c, spi, or i2c_or_spi")
    accel_range_g: float = Field(default=4.0, gt=0, description="Required accel range",
                                 json_schema_extra={"unit": "±g"})
    gyro_range_dps: float = Field(default=500.0, gt=0, description="Required gyro range",
                                  json_schema_extra={"unit": "±°/s"})
    onboard_processing: bool = Field(default=False, description="DMP / sensor fusion needed")
    vdd: float = Field(default=3.3, gt=0, le=5.5, description="Operating VDD",
                       json_schema_extra={"unit": "V"})
    allowed_packages: list[str] = Field(
        default_factory=lambda: ["LGA", "QFN", "DFN", "WLCSP"],
    )


class ImuActuals(BaseModel):
    """Extracted specs from an IMU candidate's datasheet."""
    sensor_type: Optional[str] = None
    axes: Optional[int] = Field(default=None, le=9, description="3 = accel only, 6 = +gyro, 9 = +mag")
    interfaces: Optional[list[str]] = Field(default=None,
        description="Communication interfaces supported (e.g. ['i2c', 'spi']).")
    accel_range_g: Optional[float] = Field(default=None, le=200,
        description="Max selectable accelerometer range",
        json_schema_extra={"unit": "±g"})
    gyro_range_dps: Optional[float] = Field(default=None, le=4000,
        description="Max selectable gyro range",
        json_schema_extra={"unit": "±°/s"})
    has_dmp: Optional[bool] = None
    vdd_min: Optional[float] = Field(default=None, le=10, description="Min supply voltage",
                                     json_schema_extra={"unit": "V"})
    vdd_max: Optional[float] = Field(default=None, le=10, description="Max supply voltage",
                                     json_schema_extra={"unit": "V"})
    idd_ua: Optional[float] = Field(default=None, description="Active-mode current",
                                    json_schema_extra={"unit": "µA"})
    package: Optional[str] = None
    stock: Optional[int] = Field(default=None, description="JLCPCB stock count")

    # ── Vendor extractors ──────────────────────────────────────────────────

    @staticmethod
    def _parse_interfaces(s: Optional[str]) -> Optional[list[str]]:
        """'I2C/SPI' -> ['i2c', 'spi']  ·  'SPI, I2C' -> ['spi', 'i2c']  ·  'SPI、I2C' -> ['spi', 'i2c']"""
        if not s:
            return None
        import re
        # split on whitespace, slash, comma, semicolon, or the CJK ideographic comma '、'
        tokens = re.split(r"[\s/,;、]+", s.lower())
        known = {"i2c", "spi", "uart", "i3c"}
        out = [t for t in tokens if t in known]
        return out or None

    @staticmethod
    def _axes_from_sensor_type(s: Optional[str]) -> Optional[int]:
        """'IMU (accelerometer + gyroscope)' -> 6  ·  '+magnetometer' -> 9  ·  'accelerometer' -> 3"""
        if not s:
            return None
        sl = s.lower()
        has_acc = "accel" in sl
        has_gyro = "gyro" in sl
        has_mag = "magnet" in sl
        if has_acc and has_gyro and has_mag:
            return 9
        if has_acc and has_gyro:
            return 6
        if has_acc:
            return 3
        return None

    @staticmethod
    def _parse_axes(s: Optional[str]) -> Optional[int]:
        """'6-Axis' -> 6  ·  '3 Axis Accelerometer + 3 Axis Gyroscope' -> 6"""
        if not s:
            return None
        n = _first_number(s)
        return int(n) if n else None

    @classmethod
    def from_jlc(cls, raw: dict) -> "ImuActuals":
        """Extract from pcbparts jlc_get_part response.

        Real JLC keys observed (LSM6DS3TR-C):
          'Sensor Type', 'Acceleration Measurement Range (MAX)',
          'Gyroscope Measurement Range (MAX)', 'Interface', 'Voltage - Supply',
          'Standby Current'.
        """
        specs = raw.get("specs", {}) or {}
        vdd_min, vdd_max = parse_voltage_range(
            specs.get("Voltage - Supply") or specs.get("Operating Supply Voltage")
        )
        accel_raw = (specs.get("Acceleration Measurement Range (MAX)")
                     or specs.get("Acceleration Range")
                     or specs.get("Sensing Range")
                     or specs.get("Sensitivity Range"))
        gyro_raw = (specs.get("Gyroscope Measurement Range (MAX)")
                    or specs.get("Gyroscope Range")
                    or specs.get("Angular Velocity (Max)"))
        sensor_type = specs.get("Sensor Type")
        return cls(
            sensor_type=sensor_type,
            axes=cls._axes_from_sensor_type(sensor_type),
            interfaces=cls._parse_interfaces(specs.get("Interface")),
            accel_range_g=_first_number(accel_raw) if accel_raw else None,
            gyro_range_dps=_first_number(gyro_raw) if gyro_raw else None,
            vdd_min=vdd_min,
            vdd_max=vdd_max,
            idd_ua=parse_iq_ua(specs.get("Standby Current") or specs.get("Current - Supply") or specs.get("Supply Current")),
            package=parse_package(raw.get("package")),
            stock=raw.get("stock"),
        )

    @classmethod
    def from_digikey(cls, raw: dict) -> "ImuActuals":
        """Extract from pcbparts digikey_get_part response."""
        result = raw["results"][0] if "results" in raw else raw
        p = result.get("parameters", {}) or {}
        pkg = parse_package(p.get("Supplier Device Package") or p.get("Package / Case"))
        accel = p.get("Acceleration Range") or p.get("Sensing Range")
        gyro = p.get("Angular Velocity (Max)") or p.get("Gyroscope Range")
        return cls(
            sensor_type=p.get("Sensor Type"),
            axes=cls._parse_axes(p.get("Axis")),
            interfaces=cls._parse_interfaces(p.get("Interface")),
            accel_range_g=_first_number(accel) if accel else None,
            gyro_range_dps=_first_number(gyro) if gyro else None,
            vdd_min=parse_voltage(p.get("Voltage - Supply (Min)")),
            vdd_max=parse_voltage(p.get("Voltage - Supply (Max)")),
            idd_ua=parse_iq_ua(p.get("Current - Supply")),
            package=pkg,
            stock=result.get("stock"),
        )

    @classmethod
    def from_mouser(cls, raw: dict) -> "ImuActuals":
        result = raw["results"][0] if "results" in raw else raw
        p = result.get("parameters", {}) or {}
        return cls(
            interfaces=cls._parse_interfaces(p.get("Interface")),
            vdd_min=parse_voltage(p.get("Operating Voltage Min")),
            vdd_max=parse_voltage(p.get("Operating Voltage Max")),
            package=parse_package(p.get("Package / Case") or p.get("Mounting Style")),
            stock=result.get("stock"),
        )


class ImuSubsystem(ElectronicSubsystem):
    """An IMU sensor subsystem."""

    category: ClassVar[str] = "imu"
    description: ClassVar[str] = "Accelerometer + gyroscope sensor IC"

    Requirements: ClassVar[type[BaseModel]] = ImuRequirements
    Actuals: ClassVar[type[BaseModel]] = ImuActuals

    ai_instructions: ClassVar[str] = (
        "Match accel and gyro range to the actual application — over-specced ranges sacrifice resolution. "
        "Modern (post-~2018) parts typically have <1µVrms noise and <1mA current. "
        "On-chip DMP / sensor fusion simplifies firmware but adds dependency on vendor library. "
        "9-DOF (accel + gyro + mag) only if heading reference matters; mag is noisy near motors and metal."
    )

    checks: ClassVar[list[Check]] = [
        axes_sufficient,            # local
        accel_range_sufficient,     # local
        gyro_range_sufficient,      # local
        interface_supported,        # shared (with PWM driver)
        supply_voltage_compatible,  # shared
        package_suitable,           # shared
        stock_threshold,            # shared
    ]

    calculations: ClassVar[list] = []

    extract_specs: ClassVar[list[SpecDefinition]] = [
        SpecDefinition(name="Sensor type", key="sensor_type", unit=""),
        SpecDefinition(name="Axes", key="axes", unit=""),
        SpecDefinition(name="Interfaces", key="interfaces", unit=""),
        SpecDefinition(name="Accel range", key="accel_range_g", unit="g"),
        SpecDefinition(name="Gyro range", key="gyro_range_dps", unit="dps"),
        SpecDefinition(name="Has DMP", key="has_dmp", unit=""),
        SpecDefinition(name="Min VDD", key="vdd_min", unit="V"),
        SpecDefinition(name="Max VDD", key="vdd_max", unit="V"),
        SpecDefinition(name="Supply current", key="idd_ua", unit="µA"),
    ]

    page_hints: ClassVar[dict[str, list[int]]] = {
        "features": [0, 1],
        "electrical_characteristics": [2, 3, 4],
        "register_map": [5, 6, 7, 8],
    }

    searches: ClassVar[list[SearchCriteria]] = [
        SearchCriteria(query="6-DOF IMU I2C", subcategory="Accelerometers", sort_by="stock"),
        SearchCriteria(query="9-axis IMU", subcategory="Accelerometers", sort_by="stock"),
    ]

    design_rule_topics: ClassVar[list[str]] = ["imu", "decoupling", "i2c"]

    requirements: ImuRequirements
    actuals: ImuActuals = Field(default_factory=ImuActuals)
