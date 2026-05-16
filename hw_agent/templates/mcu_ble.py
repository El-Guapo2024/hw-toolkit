"""MCU with BLE (microcontroller + integrated Bluetooth Low Energy)."""

from __future__ import annotations

from typing import ClassVar, Optional

from pydantic import BaseModel, Field

from hw_agent.subsystem import ElectronicSubsystem
from hw_agent.checks import Check, CheckResult, _missing, _missing_keys
from hw_agent.checks.shared import (
    supply_voltage_compatible,
    package_suitable,
    stock_threshold,
)
from hw_agent.templates.base import SpecDefinition, SearchCriteria
from hw_agent.templates._parsers import (
    _first_number,
    parse_freq_khz,
    parse_package,
    parse_voltage,
    parse_voltage_range,
)


# ─── MCU-specific checks (single-use; live with the component) ─────────────

def gpio_count_sufficient(actual: dict, required: dict) -> CheckResult:
    """MCU's GPIO count is at least required total.

    Needs:   actual.gpio_count
    Reads:   required.gpio_total
    """
    target = required.get("gpio_total")
    if target is None:
        return _missing("GPIO count", "hard", ["required.gpio_total"])
    if _missing_keys(actual, ["gpio_count"]):
        return _missing("GPIO count", "hard", ["actual.gpio_count"], required_str=f"≥ {target}")
    n = actual["gpio_count"]
    return CheckResult(
        name="GPIO count",
        severity="hard",
        status="pass" if n >= target else "fail",
        actual=f"{n} GPIO",
        required=f"≥ {target}",
    )


def peripherals_sufficient(actual: dict, required: dict) -> CheckResult:
    """MCU has enough I2C / SPI / UART / PWM / ADC peripherals.

    Needs:   actual.{i2c_count, spi_count, uart_count, pwm_channels, adc_channels}
    Reads:   required.{i2c_buses, spi_needed, uart_count, pwm_channels, adc_channels}
    """
    needs = {
        "i2c_count":     ("i2c_buses",     "I2C buses"),
        "spi_count":     ("spi_needed",    "SPI"),
        "uart_count":    ("uart_count",    "UARTs"),
        "pwm_channels":  ("pwm_channels",  "PWM channels"),
        "adc_channels":  ("adc_channels",  "ADC channels"),
    }
    missing_actual = _missing_keys(actual, list(needs.keys()))
    if missing_actual:
        return _missing("Peripherals", "hard", [f"actual.{k}" for k in missing_actual])

    deficits = []
    for actual_key, (req_key, label) in needs.items():
        target = required.get(req_key)
        if target is None:
            continue
        target_n = 1 if isinstance(target, bool) and target else (0 if isinstance(target, bool) else target)
        if actual[actual_key] < target_n:
            deficits.append(f"{label}: {actual[actual_key]} < {target_n}")

    return CheckResult(
        name="Peripherals",
        severity="hard",
        status="pass" if not deficits else "fail",
        actual="OK" if not deficits else "; ".join(deficits),
        required="all peripheral counts met",
    )


def memory_sufficient(actual: dict, required: dict) -> CheckResult:
    """MCU has enough flash and RAM.

    Needs:   actual.flash_kb, actual.ram_kb
    Reads:   required.flash_min, required.ram_min  (kB)
    """
    flash_target = required.get("flash_min")
    ram_target = required.get("ram_min")
    if flash_target is None and ram_target is None:
        return CheckResult(name="Memory", severity="hard", status="pass",
                           actual="(no constraint)", required="(none specified)")
    needed = _missing_keys(actual, ["flash_kb", "ram_kb"])
    if needed:
        return _missing("Memory", "hard", [f"actual.{k}" for k in needed])
    deficits = []
    if flash_target and actual["flash_kb"] < flash_target:
        deficits.append(f"flash {actual['flash_kb']}kB < {flash_target}kB")
    if ram_target and actual["ram_kb"] < ram_target:
        deficits.append(f"RAM {actual['ram_kb']}kB < {ram_target}kB")
    return CheckResult(
        name="Memory",
        severity="hard",
        status="pass" if not deficits else "fail",
        actual=f"flash={actual['flash_kb']}kB, ram={actual['ram_kb']}kB",
        required=f"flash≥{flash_target or 'any'}kB, ram≥{ram_target or 'any'}kB",
    )


def clock_speed_sufficient(actual: dict, required: dict) -> CheckResult:
    """MCU clock speed meets minimum.

    Needs:   actual.clock_mhz
    Reads:   required.clock_min  (MHz)
    """
    target = required.get("clock_min")
    if target is None:
        return CheckResult(name="Clock speed", severity="soft", status="pass",
                           actual="(no constraint)", required="(none specified)")
    if _missing_keys(actual, ["clock_mhz"]):
        return _missing("Clock speed", "soft", ["actual.clock_mhz"], required_str=f"≥ {target} MHz")
    n = actual["clock_mhz"]
    return CheckResult(
        name="Clock speed",
        severity="soft",
        status="pass" if n >= target else "fail",
        actual=f"{n} MHz",
        required=f"≥ {target} MHz",
    )


def wireless_protocol_present(actual: dict, required: dict) -> CheckResult:
    """MCU supports the required wireless protocols (BLE, WiFi).

    Needs:   actual.{ble_version, wifi}
    Reads:   required.wireless  (list of protocols)
    """
    requested = required.get("wireless") or []
    if isinstance(requested, str):
        requested = [requested]
    if not requested:
        return CheckResult(name="Wireless", severity="hard", status="pass",
                           actual="(no constraint)", required="(none specified)")
    needed_actual = []
    rlow = [r.lower() for r in requested]
    if "ble" in rlow:
        needed_actual.append("ble_version")
    if "wifi" in rlow:
        needed_actual.append("wifi")
    missing = _missing_keys(actual, needed_actual)
    if missing:
        return _missing("Wireless", "hard", [f"actual.{k}" for k in missing])
    deficits = []
    if "ble" in rlow and not actual.get("ble_version"):
        deficits.append("BLE absent")
    if "wifi" in rlow and not actual.get("wifi"):
        deficits.append("WiFi absent")
    return CheckResult(
        name="Wireless",
        severity="hard",
        status="pass" if not deficits else "fail",
        actual=f"BLE={actual.get('ble_version') or 'no'}, WiFi={actual.get('wifi') or 'no'}",
        required=", ".join(requested),
    )


# ─── Models ─────────────────────────────────────────────────────────────────

class McuBleRequirements(BaseModel):
    """Engineer answers when scoping an MCU with BLE."""
    wireless: list[str] = Field(default_factory=lambda: ["ble"],
                                 description="Required wireless protocols (e.g. ble, wifi)")
    pwm_channels: int = Field(default=0, ge=0)
    i2c_buses: int = Field(default=1, ge=0)
    spi_needed: bool = Field(default=False)
    uart_count: int = Field(default=1, ge=0)
    adc_channels: int = Field(default=0, ge=0)
    gpio_total: int = Field(default=10, ge=0, description="Minimum total GPIO")
    clock_min: float = Field(default=64, gt=0, description="Minimum clock speed",
                             json_schema_extra={"unit": "MHz"})
    flash_min: int = Field(default=256, gt=0, description="Minimum flash",
                           json_schema_extra={"unit": "kB"})
    ram_min: int = Field(default=64, gt=0, description="Minimum RAM",
                         json_schema_extra={"unit": "kB"})
    vdd: float = Field(default=3.3, gt=0, le=5.5, description="Operating VDD",
                       json_schema_extra={"unit": "V"})
    allowed_packages: list[str] = Field(
        default_factory=lambda: ["QFN", "WLCSP", "BGA", "VFQFN"],
    )


class McuBleActuals(BaseModel):
    """Extracted specs from an MCU candidate's datasheet."""
    core: Optional[str] = Field(default=None, description="CPU core (e.g. 'Cortex-M4', 'RISC-V')")
    clock_mhz: Optional[float] = Field(default=None, le=2000, description="Max clock speed",
                                       json_schema_extra={"unit": "MHz"})
    flash_kb: Optional[int] = Field(default=None, description="Internal flash",
                                    json_schema_extra={"unit": "kB"})
    ram_kb: Optional[int] = Field(default=None, description="Internal RAM",
                                  json_schema_extra={"unit": "kB"})
    gpio_count: Optional[int] = Field(default=None, le=200, description="Total usable GPIO")
    pwm_channels: Optional[int] = None
    i2c_count: Optional[int] = None
    spi_count: Optional[int] = None
    uart_count: Optional[int] = None
    adc_channels: Optional[int] = None
    usb_type: Optional[str] = Field(default=None, description="'none', 'fs', 'hs', etc.")
    ble_version: Optional[str] = Field(default=None, description="BLE version string (e.g. '5.0', '5.4')")
    wifi: Optional[str] = Field(default=None,
        description="Wi-Fi standard if present (e.g. 'wifi4', 'wifi6'); leave None / unset if no Wi-Fi.")
    vdd_min: Optional[float] = Field(default=None, le=10, description="Min supply",
                                     json_schema_extra={"unit": "V"})
    vdd_max: Optional[float] = Field(default=None, le=10, description="Max supply",
                                     json_schema_extra={"unit": "V"})
    package: Optional[str] = None
    stock: Optional[int] = Field(default=None, description="JLCPCB stock count")

    # ── Vendor extractors ──────────────────────────────────────────────────

    @staticmethod
    def _parse_clock_mhz(s: Optional[str]) -> Optional[float]:
        """'240MHz' -> 240.0  ·  '64 MHz' -> 64.0  ·  '1.2GHz' -> 1200.0"""
        if not s:
            return None
        n = _first_number(s)
        if n is None:
            return None
        u = s.lower()
        if "ghz" in u:
            return n * 1000.0
        return n

    @staticmethod
    def _parse_memory_kb(s: Optional[str]) -> Optional[int]:
        """'16MB' -> 16384  ·  '512kB' -> 512  ·  '256k' -> 256"""
        if not s:
            return None
        n = _first_number(s)
        if n is None:
            return None
        u = s.lower()
        if "mb" in u or "m " in u or u.endswith("m"):
            return int(n * 1024)
        if "gb" in u:
            return int(n * 1024 * 1024)
        return int(n)  # assume kB

    @staticmethod
    def _parse_ble_version(s: Optional[str]) -> Optional[str]:
        """'BLE 5.0' -> '5.0'  ·  'Bluetooth 5.4' -> '5.4'"""
        if not s:
            return None
        sl = s.lower()
        if "bluetooth" not in sl and "ble" not in sl and "bt" not in sl:
            return None
        import re
        m = re.search(r"(\d+\.\d+)", s)
        return m.group(1) if m else "yes"

    @staticmethod
    def _parse_wifi(s: Optional[str]) -> Optional[str]:
        """'Wi-Fi 4' -> 'wifi4'  ·  '802.11n' -> 'wifi4'  ·  '802.11ax' -> 'wifi6'"""
        if not s:
            return None
        sl = s.lower()
        if "wifi 6" in sl or "wi-fi 6" in sl or "802.11ax" in sl:
            return "wifi6"
        if "wifi 5" in sl or "wi-fi 5" in sl or "802.11ac" in sl:
            return "wifi5"
        if "wifi 4" in sl or "wi-fi 4" in sl or "802.11n" in sl or "802.11b/g/n" in sl:
            return "wifi4"
        if "wifi" in sl or "wi-fi" in sl or "802.11" in sl:
            return "wifi"
        return None

    @classmethod
    def from_jlc(cls, raw: dict) -> "McuBleActuals":
        """Extract from pcbparts jlc_get_part response.

        JLC modules (ESP32-S3-WROOM-1, NRF52840 modules, …) carry: supply
        voltage range, antenna/interface list, RF frequency band (NOT CPU
        clock — `Frequency` is the radio band on modules). CPU clock, flash,
        peripheral counts come from datasheet VLM.
        Wireless inferred from category ('WiFi Modules', 'Bluetooth Modules')
        and description text.
        """
        specs = raw.get("specs", {}) or {}
        vdd_min, vdd_max = parse_voltage_range(
            specs.get("Voltage - Supply") or specs.get("Operating Supply Voltage")
        )
        desc = raw.get("description", "") or ""
        category = (raw.get("category") or "") + " " + (raw.get("subcategory") or "")
        wireless_hint = category + " " + desc + " " + (specs.get("Wireless") or "")

        # On bare-chip MCU parts (not modules) 'CPU Speed' / 'Speed' is valid;
        # 'Frequency' is the radio band on modules — skip when value contains 'GHz'.
        freq_raw = specs.get("CPU Speed") or specs.get("Core Speed") or specs.get("Speed")
        if not freq_raw:
            f = specs.get("Frequency")
            if f and "ghz" not in f.lower():
                freq_raw = f

        return cls(
            core=specs.get("Core Architecture") or specs.get("Core Processor") or specs.get("Core"),
            clock_mhz=cls._parse_clock_mhz(freq_raw),
            flash_kb=cls._parse_memory_kb(specs.get("Program Memory Size") or specs.get("Memory Size") or specs.get("Flash")),
            ram_kb=cls._parse_memory_kb(specs.get("RAM Size") or specs.get("RAM")),
            ble_version=cls._parse_ble_version(wireless_hint),
            wifi=cls._parse_wifi(wireless_hint),
            vdd_min=vdd_min,
            vdd_max=vdd_max,
            package=parse_package(raw.get("package")),
            stock=raw.get("stock"),
        )

    @classmethod
    def from_digikey(cls, raw: dict) -> "McuBleActuals":
        result = raw["results"][0] if "results" in raw else raw
        p = result.get("parameters", {}) or {}
        pkg = parse_package(p.get("Supplier Device Package") or p.get("Package / Case"))
        return cls(
            core=p.get("Core Processor") or p.get("Core Architecture"),
            clock_mhz=cls._parse_clock_mhz(p.get("Speed") or p.get("Core Speed")),
            flash_kb=cls._parse_memory_kb(p.get("Program Memory Size")),
            ram_kb=cls._parse_memory_kb(p.get("RAM Size")),
            ble_version=cls._parse_ble_version(
                (p.get("Wireless Connectivity") or "") + " " + (p.get("RF Family/Standard") or "")
            ),
            wifi=cls._parse_wifi(
                (p.get("Wireless Connectivity") or "") + " " + (p.get("RF Family/Standard") or "")
            ),
            vdd_min=parse_voltage(p.get("Voltage - Supply (Vcc/Vdd)") or p.get("Voltage - Supply, Digital")),
            vdd_max=None,
            package=pkg,
            stock=result.get("stock"),
        )

    @classmethod
    def from_mouser(cls, raw: dict) -> "McuBleActuals":
        result = raw["results"][0] if "results" in raw else raw
        p = result.get("parameters", {}) or {}
        return cls(
            clock_mhz=cls._parse_clock_mhz(p.get("CPU Speed") or p.get("Maximum Clock Frequency")),
            package=parse_package(p.get("Package / Case") or p.get("Mounting Style")),
            stock=result.get("stock"),
        )


class McuBleSubsystem(ElectronicSubsystem):
    """An MCU + BLE subsystem."""

    category: ClassVar[str] = "mcu_ble"
    description: ClassVar[str] = "Microcontroller with integrated Bluetooth Low Energy"

    Requirements: ClassVar[type[BaseModel]] = McuBleRequirements
    Actuals: ClassVar[type[BaseModel]] = McuBleActuals

    ai_instructions: ClassVar[str] = (
        "Match peripheral count to actual needs (UART, I2C, SPI, ADC, PWM). "
        "GPIO budget = total pins − pins dedicated to peripherals. Verify after picking, not before. "
        "WiFi+BLE radios add cost and current draw — only choose both if both are required. "
        "Verify flash and RAM headroom against application size + RTOS + radio stack overhead. "
        "Toolchain / SDK / debugger support is part of the choice — verify before committing."
    )

    checks: ClassVar[list[Check]] = [
        supply_voltage_compatible,    # shared
        wireless_protocol_present,    # local
        gpio_count_sufficient,        # local
        peripherals_sufficient,       # local
        memory_sufficient,            # local
        clock_speed_sufficient,       # local
        package_suitable,             # shared
        stock_threshold,              # shared
    ]

    calculations: ClassVar[list] = []

    extract_specs: ClassVar[list[SpecDefinition]] = [
        SpecDefinition(name="Core", key="core", unit=""),
        SpecDefinition(name="Clock", key="clock_mhz", unit="MHz"),
        SpecDefinition(name="Flash", key="flash_kb", unit="kB"),
        SpecDefinition(name="RAM", key="ram_kb", unit="kB"),
        SpecDefinition(name="GPIO count", key="gpio_count", unit=""),
        SpecDefinition(name="PWM channels", key="pwm_channels", unit=""),
        SpecDefinition(name="I2C count", key="i2c_count", unit=""),
        SpecDefinition(name="SPI count", key="spi_count", unit=""),
        SpecDefinition(name="UART count", key="uart_count", unit=""),
        SpecDefinition(name="ADC channels", key="adc_channels", unit=""),
        SpecDefinition(name="BLE version", key="ble_version", unit=""),
        SpecDefinition(name="WiFi", key="wifi", unit=""),
        SpecDefinition(name="Min VDD", key="vdd_min", unit="V"),
        SpecDefinition(name="Max VDD", key="vdd_max", unit="V"),
    ]

    page_hints: ClassVar[dict[str, list[int]]] = {
        "features": [0, 1, 2],
        "block_diagram": [1, 2, 3],
        "electrical_characteristics": [4, 5, 6, 7],
        "pinout": [2, 3, 4],
    }

    searches: ClassVar[list[SearchCriteria]] = [
        SearchCriteria(query="microcontroller BLE", subcategory="Microcontrollers(MCU/MPU/SOC)", sort_by="stock"),
        SearchCriteria(query="microcontroller BLE WiFi", subcategory="Microcontrollers(MCU/MPU/SOC)", sort_by="stock"),
    ]

    design_rule_topics: ClassVar[list[str]] = ["mcu", "ble", "decoupling", "antenna_layout"]

    requirements: McuBleRequirements
    actuals: McuBleActuals = Field(default_factory=McuBleActuals)
