"""I2C-controlled multi-channel PWM driver (servos / LEDs)."""

from __future__ import annotations

from typing import ClassVar, Optional

from pydantic import BaseModel, Field

from hw_agent.subsystem import ElectronicSubsystem
from hw_agent.checks import Check
from hw_agent.checks.shared import (
    supply_voltage_compatible,
    interface_supported,
    channel_count_sufficient,
    package_suitable,
    stock_threshold,
)
from hw_agent.templates.base import SpecDefinition, SearchCriteria
from hw_agent.templates._parsers import (
    _first_number,
    parse_current,
    parse_freq_khz,
    parse_package,
    parse_voltage,
    parse_voltage_range,
)


class PwmServoDriverRequirements(BaseModel):
    """Engineer answers when scoping a PWM servo driver."""
    channels: int = Field(..., ge=1, le=32, description="PWM channels needed")
    interface: str = Field(default="i2c")
    output_type: str = Field(default="servo", description="servo, led_pwm, etc.")
    voltage_out: float = Field(default=5.0, gt=0, le=24, description="PWM output supply voltage",
                               json_schema_extra={"unit": "V"})
    daisy_chain_count: int = Field(default=1, ge=1, le=64,
                                   description="How many drivers daisy-chained")
    vdd: float = Field(default=3.3, gt=0, le=5.5, description="Logic VDD",
                       json_schema_extra={"unit": "V"})
    allowed_packages: list[str] = Field(
        default_factory=lambda: ["TSSOP-28", "SOIC-28", "QFN", "TQFP"],
    )


class PwmServoDriverActuals(BaseModel):
    """Extracted specs from a PWM driver's datasheet."""
    channels: Optional[int] = None
    interfaces: Optional[list[str]] = Field(default=None,
        description="Communication interfaces supported (e.g. ['i2c', 'spi']).")
    pwm_bits: Optional[int] = None
    pwm_freq_min: Optional[float] = Field(default=None, description="Min PWM frequency",
                                          json_schema_extra={"unit": "Hz"})
    pwm_freq_max: Optional[float] = Field(default=None, description="Max PWM frequency",
                                          json_schema_extra={"unit": "Hz"})
    iout_per_ch_ma: Optional[float] = Field(default=None, le=500,
        description="Max sink/source current per channel (note unit suffix `_ma`)",
        json_schema_extra={"unit": "mA"})
    vdd_min: Optional[float] = Field(default=None, le=10, description="Min logic supply",
                                     json_schema_extra={"unit": "V"})
    vdd_max: Optional[float] = Field(default=None, le=10, description="Max logic supply",
                                     json_schema_extra={"unit": "V"})
    addr_count: Optional[int] = Field(default=None, description="Number of I2C addresses (for daisy-chain)")
    package: Optional[str] = None
    stock: Optional[int] = Field(default=None, description="JLCPCB stock count")

    # ── Vendor extractors ──────────────────────────────────────────────────

    @staticmethod
    def _parse_interfaces_pwm(s: Optional[str]) -> Optional[list[str]]:
        if not s:
            return None
        sl = s.lower()
        out: list[str] = []
        if "i2c" in sl:
            out.append("i2c")
        if "spi" in sl:
            out.append("spi")
        if "uart" in sl:
            out.append("uart")
        return out or None

    @staticmethod
    def _parse_pwm_freq_hz(s: Optional[str]) -> Optional[float]:
        """'1kHz' -> 1000.0  ·  '1.6MHz' -> 1_600_000.0  ·  '40Hz' -> 40.0"""
        if not s:
            return None
        n = _first_number(s)
        if n is None:
            return None
        u = s.lower()
        if "mhz" in u:
            return n * 1_000_000.0
        if "khz" in u:
            return n * 1000.0
        return n  # plain Hz

    @classmethod
    def from_jlc(cls, raw: dict) -> "PwmServoDriverActuals":
        """Extract from pcbparts jlc_get_part response.

        Real JLC keys observed (PCA9685PW,118):
          'Number of Channels', 'Voltage - Input(DC)', 'Frequency - Switching'
          (range '100kHz~1MHz'), 'Output Current' (mA), 'Dimming' = 'PWM'.
          'Interface' key may be absent — infer from category (LED Drivers
          with I2C-style control implied).
        """
        specs = raw.get("specs", {}) or {}
        vdd_raw = (specs.get("Voltage - Input(DC)")
                   or specs.get("Voltage - Supply")
                   or specs.get("Voltage - Input"))
        vdd_min, vdd_max = parse_voltage_range(vdd_raw)
        bits = _first_number(specs.get("Resolution") or "")

        # PWM frequency may be a range "100kHz~1MHz" — parse both ends.
        freq_raw = specs.get("Frequency - Switching") or specs.get("Frequency") or specs.get("Max Frequency")
        pwm_freq_min = None
        pwm_freq_max = None
        if freq_raw:
            if "~" in freq_raw or "to" in freq_raw.lower():
                import re
                parts = re.split(r"\s*[~–\-]\s*|\s+to\s+", freq_raw.strip(), maxsplit=1)
                if len(parts) == 2:
                    pwm_freq_min = cls._parse_pwm_freq_hz(parts[0])
                    pwm_freq_max = cls._parse_pwm_freq_hz(parts[1])
            else:
                pwm_freq_max = cls._parse_pwm_freq_hz(freq_raw)

        iout_str = specs.get("Output Current") or specs.get("Current - Output")
        # JLC output current for PWM drivers is in mA directly ('25mA')
        iout_ma = None
        if iout_str and iout_str != "-":
            iout_a = parse_current(iout_str)
            if iout_a is not None:
                iout_ma = iout_a * 1000.0

        # Channels: "Number of Channels" → '16'
        ch_raw = specs.get("Number of Channels") or specs.get("Number of Outputs") or specs.get("Channels")
        channels = None
        if ch_raw:
            n = _first_number(str(ch_raw))
            if n is not None:
                channels = int(n)

        return cls(
            channels=channels,
            interfaces=cls._parse_interfaces_pwm(specs.get("Interface")),
            pwm_bits=int(bits) if bits else None,
            pwm_freq_min=pwm_freq_min,
            pwm_freq_max=pwm_freq_max,
            iout_per_ch_ma=iout_ma,
            vdd_min=vdd_min,
            vdd_max=vdd_max,
            package=parse_package(raw.get("package")),
            stock=raw.get("stock"),
        )

    @classmethod
    def from_digikey(cls, raw: dict) -> "PwmServoDriverActuals":
        result = raw["results"][0] if "results" in raw else raw
        p = result.get("parameters", {}) or {}
        pkg = parse_package(p.get("Supplier Device Package") or p.get("Package / Case"))
        bits = _first_number(p.get("Resolution") or "")
        return cls(
            channels=int(_first_number(p.get("Number of Outputs") or "") or 0) or None,
            interfaces=cls._parse_interfaces_pwm(p.get("Interface")),
            pwm_bits=int(bits) if bits else None,
            pwm_freq_max=cls._parse_pwm_freq_hz(p.get("Frequency") or p.get("Frequency - Max")),
            iout_per_ch_ma=_first_number(p.get("Current - Output / Channel") or "") or None,
            vdd_min=parse_voltage(p.get("Voltage - Supply (Vcc/Vdd)") or p.get("Voltage - Supply (Min)")),
            vdd_max=parse_voltage(p.get("Voltage - Supply (Max)")),
            package=pkg,
            stock=result.get("stock"),
        )

    @classmethod
    def from_mouser(cls, raw: dict) -> "PwmServoDriverActuals":
        result = raw["results"][0] if "results" in raw else raw
        p = result.get("parameters", {}) or {}
        return cls(
            interfaces=cls._parse_interfaces_pwm(p.get("Interface")),
            vdd_min=parse_voltage(p.get("Operating Voltage Min")),
            vdd_max=parse_voltage(p.get("Operating Voltage Max")),
            package=parse_package(p.get("Package / Case") or p.get("Mounting Style")),
            stock=result.get("stock"),
        )


class PwmServoDriverSubsystem(ElectronicSubsystem):
    """A multi-channel PWM driver subsystem."""

    category: ClassVar[str] = "pwm_servo_driver"
    description: ClassVar[str] = "I2C multi-channel PWM driver for servos/LEDs"

    Requirements: ClassVar[type[BaseModel]] = PwmServoDriverRequirements
    Actuals: ClassVar[type[BaseModel]] = PwmServoDriverActuals

    ai_instructions: ClassVar[str] = (
        "External I2C PWM drivers are the canonical pattern when channel count exceeds what the MCU "
        "natively offers (typically 6–8). For fewer channels, MCU PWM is usually sufficient and saves a part. "
        "PWM resolution (in bits) drives positioning accuracy: 12-bit covers most servo + LED-dimming use. "
        "Verify the I2C address space supports daisy-chaining if multiple drivers will share the bus."
    )

    checks: ClassVar[list[Check]] = [
        channel_count_sufficient,       # shared (motor family + PWM driver)
        interface_supported,            # shared (with IMU)
        supply_voltage_compatible,      # shared (digital ICs)
        package_suitable,               # shared (universal)
        stock_threshold,                # shared (universal)
    ]

    calculations: ClassVar[list] = []

    extract_specs: ClassVar[list[SpecDefinition]] = [
        SpecDefinition(name="Channels", key="channels", unit=""),
        SpecDefinition(name="Interfaces", key="interfaces", unit=""),
        SpecDefinition(name="PWM bits", key="pwm_bits", unit="bits"),
        SpecDefinition(name="Min PWM freq", key="pwm_freq_min", unit="Hz"),
        SpecDefinition(name="Max PWM freq", key="pwm_freq_max", unit="Hz"),
        SpecDefinition(name="Iout per channel", key="iout_per_ch_ma", unit="mA"),
        SpecDefinition(name="Min VDD", key="vdd_min", unit="V"),
        SpecDefinition(name="Max VDD", key="vdd_max", unit="V"),
        SpecDefinition(name="Address count", key="addr_count", unit=""),
    ]

    page_hints: ClassVar[dict[str, list[int]]] = {
        "features": [0, 1],
        "electrical_characteristics": [2, 3, 4],
    }

    searches: ClassVar[list[SearchCriteria]] = [
        SearchCriteria(query="{channels} channel PWM I2C", subcategory="LED Drivers", sort_by="stock"),
        SearchCriteria(query="multi-channel PWM driver I2C", subcategory="LED Drivers", sort_by="stock"),
    ]

    design_rule_topics: ClassVar[list[str]] = ["pwm_driver", "i2c", "decoupling"]

    requirements: PwmServoDriverRequirements
    actuals: PwmServoDriverActuals = Field(default_factory=PwmServoDriverActuals)
