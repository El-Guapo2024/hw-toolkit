"""Persistent per-MPN profile — PyTorch-style stateful builder.

A `Part` accumulates information about one MPN across sources:

    p = Part("RT6228AGQUF", "buck_converter")
    p.add_jlc(jlc_raw).add_digikey(dk_raw).save()
    # ... later, fresh process
    p = Part.load("RT6228AGQUF")
    if p.missing():
        fills = subagent_vlm(p.commercial["datasheet_url"], p.missing())
        p.add_datasheet(fills).save()
    verdict = p.validate(BuckRequirements(vin=7.4, vout=5.0, iout_max=6.0))

State lives on disk at `HW_AGENT_PARTS_DIR/<mpn>.profile.json` (default
`~/.hw-agent/parts/`), so MCP tool calls stay stateless: each call loads,
mutates, saves.

Merge precedence is JLC > DigiKey > Mouser > Datasheet — JLC's commercial
naming (package, stock, LCSC) is canonical for turnkey assembly, DigiKey
fills technical gaps JLC doesn't cover, Mouser is parameters-thin (treat
as lifecycle xcheck only), and datasheet VLM fills whatever distributors
miss (typically rdson, theta_ja, iq, ilim, tsd).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Optional

from pydantic import BaseModel

from hw_agent.templates._actuals_ops import merge as _merge_actuals
from hw_agent.templates._actuals_ops import missing_fields
from hw_agent.templates.buck import BuckActuals
from hw_agent.templates.imu import ImuActuals
from hw_agent.templates.ldo import LdoActuals
from hw_agent.templates.mcu_ble import McuBleActuals
from hw_agent.templates.motor_driver import MotorDriverActuals
from hw_agent.templates.pwm_servo_driver import PwmServoDriverActuals
from hw_agent.templates.stepper_driver import StepperDriverActuals


# Registry maps category → Actuals class. Extend as new categories add
# from_<vendor> classmethods. Keep this list aligned with templates/*.py.
ACTUALS_REGISTRY: dict[str, type[BaseModel]] = {
    "buck_converter": BuckActuals,
    "ldo": LdoActuals,
    "imu": ImuActuals,
    "mcu_ble": McuBleActuals,
    "motor_driver": MotorDriverActuals,
    "stepper_driver": StepperDriverActuals,
    "pwm_servo_driver": PwmServoDriverActuals,
}


def parts_dir() -> Path:
    """Where profile JSONs live. Override via HW_AGENT_PARTS_DIR env."""
    root = os.environ.get("HW_AGENT_PARTS_DIR")
    if root:
        return Path(root).expanduser()
    return Path.home() / ".hw-agent" / "parts"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _commercial_from_jlc(raw: dict) -> dict:
    return {
        "lcsc": raw.get("lcsc"),
        "mpn": raw.get("model"),
        "manufacturer": raw.get("manufacturer"),
        "package": raw.get("package"),
        "price_usd": raw.get("price"),
        "stock_jlc": raw.get("stock"),
        "library_type": raw.get("library_type"),
        "datasheet_url": raw.get("datasheet"),
        "lcsc_url": raw.get("lcsc_url"),
    }


def _commercial_from_dk(raw: dict) -> dict:
    r = raw["results"][0] if "results" in raw else raw
    return {
        "manufacturer_dk": r.get("manufacturer"),
        "price_usd_dk": r.get("price"),
        "stock_dk": r.get("stock"),
        "lifecycle": r.get("lifecycle"),
        "datasheet_url_dk": r.get("datasheet_url"),
        "product_url_dk": r.get("product_url"),
        "rohs": r.get("rohs"),
    }


def _commercial_from_mouser(raw: dict) -> dict:
    r = raw["results"][0] if "results" in raw else raw
    return {
        "manufacturer_mouser": r.get("manufacturer"),
        "price_usd_mouser": r.get("price"),
        "stock_mouser": r.get("stock"),
        "lifecycle_mouser": r.get("lifecycle"),
        "product_url_mouser": r.get("product_url"),
    }


class Part:
    """Stateful builder that accumulates actuals + commercial info per MPN."""

    PROFILE_VERSION: ClassVar[int] = 1

    def __init__(self, mpn: str, category: str):
        if category not in ACTUALS_REGISTRY:
            raise ValueError(
                f"Unknown category {category!r}. Known: {sorted(ACTUALS_REGISTRY)}"
            )
        self.mpn = mpn
        self.category = category
        self.actuals_cls: type[BaseModel] = ACTUALS_REGISTRY[category]
        self.actuals: BaseModel = self.actuals_cls()
        self.commercial: dict[str, Any] = {}
        self.sources: list[str] = []
        self.created_at: str = _utcnow()
        self.updated_at: str = self.created_at

    # ── Source layers (chainable) ──────────────────────────────────────────

    def add_jlc(self, raw: dict) -> "Part":
        new = self.actuals_cls.from_jlc(raw)
        self.actuals = _merge_actuals(self.actuals, new)
        self.commercial.update({k: v for k, v in _commercial_from_jlc(raw).items() if v is not None})
        if "jlc" not in self.sources:
            self.sources.append("jlc")
        self.updated_at = _utcnow()
        return self

    def add_digikey(self, raw: dict) -> "Part":
        new = self.actuals_cls.from_digikey(raw)
        # JLC > DK precedence: only fill fields still None
        self.actuals = _merge_actuals(self.actuals, new)
        self.commercial.update({k: v for k, v in _commercial_from_dk(raw).items() if v is not None})
        if "digikey" not in self.sources:
            self.sources.append("digikey")
        self.updated_at = _utcnow()
        return self

    def add_mouser(self, raw: dict) -> "Part":
        new = self.actuals_cls.from_mouser(raw)
        self.actuals = _merge_actuals(self.actuals, new)
        self.commercial.update({k: v for k, v in _commercial_from_mouser(raw).items() if v is not None})
        if "mouser" not in self.sources:
            self.sources.append("mouser")
        self.updated_at = _utcnow()
        return self

    def add_datasheet(self, fills: dict) -> "Part":
        """Apply VLM-extracted datasheet values. Only fills None fields."""
        new = self.actuals_cls(**{
            k: v for k, v in fills.items() if k in self.actuals_cls.model_fields
        })
        self.actuals = _merge_actuals(self.actuals, new)
        if "datasheet" not in self.sources:
            self.sources.append("datasheet")
        self.updated_at = _utcnow()
        return self

    # ── Inspection ─────────────────────────────────────────────────────────

    def missing(self) -> list[str]:
        return missing_fields(self.actuals)

    def validate(self, requirements: BaseModel | dict) -> list[dict]:
        """Run category-specific checks. Returns list of CheckResult dicts."""
        # Import lazily to avoid pulling subsystem modules at import time
        subsystem_cls, requirements_cls = self._resolve_category()
        req = (
            requirements
            if isinstance(requirements, requirements_cls)
            else requirements_cls(
                **(requirements if isinstance(requirements, dict) else requirements.model_dump())
            )
        )
        actual_d = self.actuals.model_dump()
        req_d = req.model_dump()
        results = []
        for check in subsystem_cls.checks:
            r = check(actual_d, req_d)
            results.append({
                "name": r.name,
                "severity": r.severity,
                "status": r.status,
                "actual": r.actual,
                "required": r.required,
                "missing_specs": r.missing_specs,
                "note": r.note,
            })
        return results

    def _resolve_category(self) -> tuple[type, type]:
        """Map category → (SubsystemClass, RequirementsClass). Lazy imports."""
        cat = self.category
        if cat == "buck_converter":
            from hw_agent.templates.buck import BuckSubsystem, BuckRequirements
            return BuckSubsystem, BuckRequirements
        if cat == "ldo":
            from hw_agent.templates.ldo import LdoSubsystem, LdoRequirements
            return LdoSubsystem, LdoRequirements
        if cat == "imu":
            from hw_agent.templates.imu import ImuSubsystem, ImuRequirements
            return ImuSubsystem, ImuRequirements
        if cat == "mcu_ble":
            from hw_agent.templates.mcu_ble import McuBleSubsystem, McuBleRequirements
            return McuBleSubsystem, McuBleRequirements
        if cat == "motor_driver":
            from hw_agent.templates.motor_driver import (
                MotorDriverSubsystem, MotorDriverRequirements,
            )
            return MotorDriverSubsystem, MotorDriverRequirements
        if cat == "stepper_driver":
            from hw_agent.templates.stepper_driver import (
                StepperDriverSubsystem, StepperDriverRequirements,
            )
            return StepperDriverSubsystem, StepperDriverRequirements
        if cat == "pwm_servo_driver":
            from hw_agent.templates.pwm_servo_driver import (
                PwmServoDriverSubsystem, PwmServoDriverRequirements,
            )
            return PwmServoDriverSubsystem, PwmServoDriverRequirements
        raise NotImplementedError(f"validate() not wired for category {cat}")

    # ── Persistence ────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "version": self.PROFILE_VERSION,
            "mpn": self.mpn,
            "category": self.category,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "sources": self.sources,
            "commercial": self.commercial,
            "actuals": self.actuals.model_dump(),
            "missing": self.missing(),
        }

    def save(self, root: Optional[Path] = None) -> Path:
        root = root or parts_dir()
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{self.mpn}.profile.json"
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str))
        return path

    @classmethod
    def load(cls, mpn: str, root: Optional[Path] = None) -> "Part":
        root = root or parts_dir()
        path = root / f"{mpn}.profile.json"
        if not path.exists():
            raise FileNotFoundError(f"No profile at {path}")
        d = json.loads(path.read_text())
        p = cls(d["mpn"], d["category"])
        p.actuals = p.actuals_cls(**d.get("actuals", {}))
        p.commercial = d.get("commercial", {})
        p.sources = d.get("sources", [])
        p.created_at = d.get("created_at", _utcnow())
        p.updated_at = d.get("updated_at", p.created_at)
        return p

    @classmethod
    def exists(cls, mpn: str, root: Optional[Path] = None) -> bool:
        root = root or parts_dir()
        return (root / f"{mpn}.profile.json").exists()

    @classmethod
    def delete(cls, mpn: str, root: Optional[Path] = None) -> bool:
        root = root or parts_dir()
        path = root / f"{mpn}.profile.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def __repr__(self) -> str:
        return (
            f"Part(mpn={self.mpn!r}, category={self.category!r}, "
            f"sources={self.sources}, missing={self.missing()})"
        )
