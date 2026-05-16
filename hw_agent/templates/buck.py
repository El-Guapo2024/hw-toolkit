"""Buck Converter (step-down DC-DC switching regulator)."""

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
from hw_agent.calculations.buck import inductor_value, output_cap_value, thermal_estimate
from hw_agent.templates.base import SpecDefinition, SearchCriteria
from hw_agent.templates._parsers import (
    parse_current,
    parse_freq_khz,
    parse_package,
    parse_voltage,
    parse_voltage_range,
)


# ─── Buck-specific checks (single-use; live with the component) ────────────

def switching_freq_in_range(actual: dict, required: dict) -> CheckResult:
    """Part's switching frequency range covers the required Fsw.

    Needs:   actual.fsw_min, actual.fsw_max  (kHz)
    Reads:   required.fsw_khz  (optional — passes if engineer didn't specify a preference)
    """
    target = required.get("fsw_khz")
    if target is None:
        return CheckResult(
            name="Switching frequency",
            severity="soft",
            status="pass",
            actual="(no engineer preference)",
            required="(none specified)",
        )
    needed = _missing_keys(actual, ["fsw_min", "fsw_max"])
    if needed:
        return _missing("Switching frequency", "soft", [f"actual.{k}" for k in needed],
                        required_str=f"{target} kHz")
    fmin, fmax = actual["fsw_min"], actual["fsw_max"]
    return CheckResult(
        name="Switching frequency",
        severity="soft",
        status="pass" if fmin <= target <= fmax else "fail",
        actual=f"{fmin}–{fmax} kHz",
        required=f"{target} kHz",
    )


def buck_junction_temperature(actual: dict, required: dict, *, tj_max_c: float = 125.0) -> CheckResult:
    """Estimate Tj from buck conduction loss and check it's below tj_max.

    Pdiss ≈ Iout² × RDS(on) × duty (high-side conduction loss only — switching
    loss is usually small at moderate Fsw and adds <30%, but verify on critical
    designs by reading the datasheet's efficiency vs. load curve).

    Needs:   actual.rdson_mohm, actual.theta_ja  (°C/W)
    Reads:   required.vin, required.vout, required.iout_max, required.ambient_c
    """
    needed = _missing_keys(actual, ["rdson_mohm", "theta_ja"])
    if needed:
        return _missing("Junction temperature", "hard", [f"actual.{k}" for k in needed])
    needed_req = [k for k in ("vin", "vout", "iout_max") if required.get(k) is None]
    if needed_req:
        return _missing("Junction temperature", "hard", [f"required.{k}" for k in needed_req])

    vin = required["vin"]
    vout = required["vout"]
    iout = required["iout_max"]
    ambient = required.get("ambient_c", 40.0)
    rdson = actual["rdson_mohm"] / 1000.0
    theta_ja = actual["theta_ja"]

    duty = vout / vin
    pdiss = iout * iout * rdson * duty
    tj = ambient + pdiss * theta_ja
    margin = tj_max_c - tj

    return CheckResult(
        name="Junction temperature",
        severity="hard",
        status="pass" if tj < tj_max_c else "fail",
        actual=f"Tj = {tj:.1f} °C @ Iout_max={iout:g} A (Pdiss {pdiss*1000:.0f} mW conduction, RDS(on) {actual['rdson_mohm']:g} mΩ, duty {duty:.2f}, ambient {ambient:g} °C)",
        required=f"Tj < {tj_max_c:.0f} °C",
        note=f"Margin: {margin:.1f} °C" + (" — MARGINAL" if 0 < margin < 15 else "")
             + " — worst-case at iout_max; switching loss not included.",
    )


# ─── Models ─────────────────────────────────────────────────────────────────

class BuckRequirements(BaseModel):
    """Engineer answers when scoping a buck converter."""
    vin: float = Field(..., gt=0, le=100, description="Nominal input voltage",
                       json_schema_extra={"unit": "V"})
    vin_max: Optional[float] = Field(default=None, description="Max input including transients",
                                     json_schema_extra={"unit": "V"})
    vout: float = Field(..., gt=0, le=24, description="Output voltage",
                        json_schema_extra={"unit": "V"})
    iout_max: float = Field(
        ..., gt=0, le=20,
        description="Maximum output current",
        json_schema_extra={"unit": "A"},
    )
    iout_typical: Optional[float] = Field(
        default=None,
        description=(
            "Typical (average) operating current. Optional — used to size the "
            "inductor at normal load (smaller L for given ripple %). Worst-case "
            "checks (junction temp, output cap, current rating) always use "
            "iout_max regardless."
        ),
        json_schema_extra={"unit": "A"},
    )
    iout_margin: float = Field(
        default=1.2, ge=1.0, le=3.0,
        description="Safety margin applied to iout_max. Default 1.2× — a 3 A target needs ≥3.6 A. Override to 1.0 to disable.",
        json_schema_extra={"unit": "×"},
    )
    fsw_khz: Optional[float] = Field(
        default=None, ge=100, le=5000,
        description="Preferred switching frequency. Leave unset = no constraint; the check passes for any part with a valid fsw range.",
        json_schema_extra={"unit": "kHz"},
    )
    ripple_pct: float = Field(default=30, ge=10, le=60, description="Inductor ripple as % of Iout",
                              json_schema_extra={"unit": "%"})
    ambient_c: float = Field(default=40.0, ge=-40, le=125, description="Max ambient temperature",
                             json_schema_extra={"unit": "°C"})
    allowed_packages: list[str] = Field(
        default_factory=lambda: ["SOT-23-6", "TSOT-23-6", "SOIC-8-EP", "QFN", "DFN"],
    )


class BuckActuals(BaseModel):
    """Specs extracted from a buck candidate's datasheet."""
    vin_min: Optional[float] = Field(default=None, le=100, description="Min input voltage",
                                     json_schema_extra={"unit": "V"})
    vin_max: Optional[float] = Field(default=None, le=100, description="Max input voltage",
                                     json_schema_extra={"unit": "V"})
    iout_max: Optional[float] = Field(default=None, le=30, description="Max continuous output current",
                                      json_schema_extra={"unit": "A"})
    fsw_min: Optional[float] = Field(default=None, description="Min switching frequency",
                                     json_schema_extra={"unit": "kHz"})
    fsw_max: Optional[float] = Field(default=None, description="Max switching frequency",
                                     json_schema_extra={"unit": "kHz"})
    iq: Optional[float] = Field(default=None, description="Quiescent current",
                                json_schema_extra={"unit": "µA"})
    rdson_mohm: Optional[float] = Field(default=None, description="High-side FET RDS(on)",
                                        json_schema_extra={"unit": "mΩ"})
    ilim: Optional[float] = Field(default=None, le=30, description="Current limit",
                                  json_schema_extra={"unit": "A"})
    theta_ja: Optional[float] = Field(default=None, description="Thermal resistance, junction-to-ambient",
                                      json_schema_extra={"unit": "°C/W"})
    tsd: Optional[float] = Field(default=None, description="Thermal shutdown",
                                 json_schema_extra={"unit": "°C"})
    package: Optional[str] = None
    stock: Optional[int] = Field(default=None, description="JLCPCB stock count")

    # ── Vendor extractors ──────────────────────────────────────────────────
    # Each `from_<vendor>` consumes a raw vendor JSON dict (as returned by
    # pcbparts MCP tools) and returns a BuckActuals with whatever fields the
    # vendor surfaces. Fields the vendor does not carry stay None — caller
    # merges multiple sources + VLM datasheet fills via `_actuals_ops.merge`.

    @classmethod
    def from_jlc(cls, raw: dict) -> "BuckActuals":
        """Extract from pcbparts jlc_get_part response.

        JLC carries: vin range, iout_max, fsw (single value), package, stock.
        Missing (datasheet-only): iq, rdson_mohm, ilim, theta_ja, tsd.
        """
        specs = raw.get("specs", {}) or {}
        vin_min, vin_max = parse_voltage_range(specs.get("Voltage - Supply"))
        fsw = parse_freq_khz(specs.get("Frequency - Switching"))
        return cls(
            vin_min=vin_min,
            vin_max=vin_max,
            iout_max=parse_current(specs.get("Output Current")),
            fsw_min=fsw,
            fsw_max=fsw,
            package=parse_package(raw.get("package")),
            stock=raw.get("stock"),
        )

    @classmethod
    def from_digikey(cls, raw: dict) -> "BuckActuals":
        """Extract from pcbparts digikey_get_part response.

        Accepts either the wrapped `{results: [{parameters: {...}}]}` shape
        or a single result dict. DigiKey carries: vin min/max (split fields),
        iout_max, fsw, package. Missing (datasheet-only): rdson, theta_ja, iq, tsd, ilim.
        """
        result = raw["results"][0] if "results" in raw else raw
        p = result.get("parameters", {}) or {}
        fsw = parse_freq_khz(p.get("Frequency - Switching"))
        # Prefer the more descriptive "Supplier Device Package" when present
        pkg = parse_package(p.get("Supplier Device Package") or p.get("Package / Case"))
        return cls(
            vin_min=parse_voltage(p.get("Voltage - Input (Min)")),
            vin_max=parse_voltage(p.get("Voltage - Input (Max)")),
            iout_max=parse_current(p.get("Current - Output")),
            fsw_min=fsw,
            fsw_max=fsw,
            package=pkg,
            stock=result.get("stock"),
        )

    @classmethod
    def from_mouser(cls, raw: dict) -> "BuckActuals":
        """Extract from pcbparts mouser_get_part response.

        Mouser ProductAttributes are thin — typically only Packaging /
        Standard Pack Qty. We try a few keys defensively; most fields end up
        None and get filled by DigiKey/JLC merge or datasheet VLM.
        """
        result = raw["results"][0] if "results" in raw else raw
        p = result.get("parameters", {}) or {}
        fsw = parse_freq_khz(p.get("Switching Frequency") or p.get("Frequency - Switching"))
        vin_min = parse_voltage(p.get("Input Voltage-Min") or p.get("Operating Voltage Min"))
        vin_max = parse_voltage(p.get("Input Voltage-Max") or p.get("Operating Voltage Max"))
        return cls(
            vin_min=vin_min,
            vin_max=vin_max,
            iout_max=parse_current(p.get("Output Current")),
            fsw_min=fsw,
            fsw_max=fsw,
            package=parse_package(p.get("Package / Case") or p.get("Mounting Style")),
            stock=result.get("stock"),
        )


class BuckSubsystem(ElectronicSubsystem):
    """A buck converter subsystem."""

    category: ClassVar[str] = "buck_converter"
    description: ClassVar[str] = "Step-down DC-DC switching regulator"

    Requirements: ClassVar[type[BaseModel]] = BuckRequirements
    Actuals: ClassVar[type[BaseModel]] = BuckActuals

    ai_instructions: ClassVar[str] = (
        "UNITS: every requirement has an `unit` in q_load — read it. If the engineer "
        "states a number without a unit (e.g. '500' instead of '500 mA' or '0.5 A'), "
        "ALWAYS confirm the unit in conversation before calling subsystem_add. Convert "
        "to the field's expected unit before commit (iout_max stores Amps, fsw_khz stores kHz). "
        "Iout requirement is multiplied by `iout_margin` (default 1.2× — a 3 A target requires ≥3.6 A). "
        "This protects against thermal runaway, datasheet rating optimism, and transient peaks; override to 1.0 only if you have a separate margin justification. "
        "iout_max drives all worst-case sizing: current rating, output cap (peak ripple), thermal junction temp. "
        "iout_typical (optional) is only used to size the inductor at normal load — it never relaxes a worst-case check. "
        "Conduction thermal: Pdiss ≈ Iout² × RDS(on) × duty (evaluated at iout_max). Switching loss adds to that. "
        "Above ~1 W dissipation, package thermal pad is mandatory. "
        "Synchronous (integrated FET) bucks have lower BOM count than non-sync at the cost of slightly higher Iq. "
        "Higher Fsw = smaller passives but more EMI and switching loss."
    )

    checks: ClassVar[list[Check]] = [
        vin_range,                     # shared
        iout_capability,               # shared
        switching_freq_in_range,       # local
        buck_junction_temperature,     # local — buck-specific thermal model
        package_suitable,              # shared
        stock_threshold,               # shared
    ]

    calculations: ClassVar[list] = [inductor_value, output_cap_value, thermal_estimate]

    extract_specs: ClassVar[list[SpecDefinition]] = [
        SpecDefinition(name="Min Vin", key="vin_min", unit="V"),
        SpecDefinition(name="Max Vin", key="vin_max", unit="V"),
        SpecDefinition(name="Max Iout", key="iout_max", unit="A"),
        SpecDefinition(name="Min Fsw", key="fsw_min", unit="kHz"),
        SpecDefinition(name="Max Fsw", key="fsw_max", unit="kHz"),
        SpecDefinition(name="RDS(on) high-side", key="rdson_mohm", unit="mΩ",
                       aliases=["RDS_on", "RDS(ON)", "Rdson"]),
        SpecDefinition(name="Quiescent current", key="iq", unit="µA"),
        SpecDefinition(name="Theta-JA", key="theta_ja", unit="°C/W"),
    ]

    page_hints: ClassVar[dict[str, list[int]]] = {
        "absolute_maximum_ratings": [1, 2],
        "electrical_characteristics": [3, 4, 5, 6],
        "thermal_data": [1, 2, 7],
        "application_circuit": [0, 1, 8, 9],
    }

    searches: ClassVar[list[SearchCriteria]] = [
        SearchCriteria(query="buck {vout}V {iout_max}A", subcategory="DC-DC Converters",
                       packages=["SOT-23-6", "TSOT-23-6"], sort_by="stock"),
        SearchCriteria(query="buck synchronous {vout}V", subcategory="DC-DC Converters",
                       sort_by="stock"),
    ]

    design_rule_topics: ClassVar[list[str]] = ["buck", "switching_regulator", "power", "decoupling"]

    requirements: BuckRequirements
    actuals: BuckActuals = Field(default_factory=BuckActuals)
