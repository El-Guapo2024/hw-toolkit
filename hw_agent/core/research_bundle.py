"""ResearchBundle — the contract handed FROM researcher TO pcb-designer.

This is the load-bearing handoff. The researcher writes this; the pcb-designer
refuses to start until it loads cleanly. Everything else (KiCad files, gerbers)
is downstream of this bundle.

Kept intentionally flat and minimal. Sub-models only when their shape is
genuinely shared (SubsystemPick, Interface). No provenance yet — that's an
optimization for the actuals layer, added later.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SubsystemPick(BaseModel):
    """One subsystem with its locked part + the actuals pcb-designer needs."""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    category: str = Field(min_length=2, max_length=64)
    """Template id, e.g. `buck_converter`, `ldo`, `mcu_module`."""

    # Part identity — projected into KiCad symbol fields
    mpn: str = Field(min_length=2, max_length=64)
    manufacturer: str = ""
    lcsc: str | None = None
    package: str = ""
    datasheet_url: str = ""

    # BOM + stock
    qty_per_board: int = Field(default=1, ge=1)
    price_usd: float = Field(default=0.0, ge=0)
    stock: int = Field(default=0, ge=0)

    # Layout-relevant actuals (subset of full actuals; just what pcb-designer needs)
    # Free-form dict for now — typed per-category later.
    actuals: dict[str, float | int | str] = Field(default_factory=dict)

    # Port → InterfaceRecord.id binding. e.g. {"VOUT": "rail_6v", "VIN": "vbat_main"}
    port_bindings: dict[str, str] = Field(default_factory=dict)


class Interface(BaseModel):
    """One typed connection between two subsystems (or external + subsystem)."""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    type: Literal["power", "signal", "data"]

    # Endpoints — kept flat for two-party connections. Multi-party buses
    # (e.g. I²C with 5 targets) carry the bus as one Interface and identify
    # participants via subsystems[].port_bindings pointing at this interface id.
    from_subsystem: str = Field(min_length=1, max_length=64)
    from_port: str = Field(min_length=1, max_length=32)
    to_subsystem: str = Field(min_length=1, max_length=64)
    to_port: str = Field(min_length=1, max_length=32)

    # Power-specific. GND nets carry voltage_nominal_v = 0.0.
    voltage_nominal_v: float | None = Field(default=None, ge=0)
    current_continuous_max_a: float | None = Field(default=None, gt=0)
    current_peak_max_a: float | None = Field(default=None, gt=0)

    # Signal/data-specific
    protocol: Literal["i2c", "spi", "uart", "can", "usb", "swd"] | None = None
    speed_hz: int | None = Field(default=None, gt=0)

    notes: str = ""

    @model_validator(mode="after")
    def type_matches_fields(self) -> "Interface":
        if self.type == "power" and self.voltage_nominal_v is None:
            raise ValueError(f"interface {self.id}: power type requires voltage_nominal_v")
        if self.type in ("signal", "data") and self.protocol is None:
            raise ValueError(f"interface {self.id}: {self.type} type requires protocol")
        if self.current_peak_max_a is not None and self.current_continuous_max_a is not None:
            if self.current_peak_max_a < self.current_continuous_max_a:
                raise ValueError(
                    f"interface {self.id}: current_peak < current_continuous"
                )
        return self


class ResearchBundle(BaseModel):
    """The whole handoff. Researcher writes; pcb-designer reads."""
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    project_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")

    subsystems: list[SubsystemPick] = Field(min_length=1)
    interfaces: list[Interface] = Field(default_factory=list)

    # Build settings that flow through to fab
    build_qty: int = Field(default=1, ge=1)
    assembly: Literal["hand_solder", "jlc_turnkey", "mixed"] = "hand_solder"
    vendor: Literal["jlcpcb", "pcbway", "oshpark", "aisler"] = "jlcpcb"

    # Audit + lock
    research_baseline_git_tag: str = Field(min_length=4, max_length=128)
    """e.g. `control_hub_v1/research-baseline`. Proves the gate passed."""
    locked_at: datetime

    notes: str = ""

    @model_validator(mode="after")
    def unique_subsystem_ids(self) -> "ResearchBundle":
        ids = [s.id for s in self.subsystems]
        dupes = {x for x in ids if ids.count(x) > 1}
        if dupes:
            raise ValueError(f"duplicate subsystem ids: {sorted(dupes)}")
        return self

    @model_validator(mode="after")
    def unique_interface_ids(self) -> "ResearchBundle":
        ids = [i.id for i in self.interfaces]
        dupes = {x for x in ids if ids.count(x) > 1}
        if dupes:
            raise ValueError(f"duplicate interface ids: {sorted(dupes)}")
        return self

    @model_validator(mode="after")
    def interface_endpoints_exist(self) -> "ResearchBundle":
        sub_ids = {s.id for s in self.subsystems}
        # "external" is a permitted endpoint for power-source-style interfaces
        # (e.g. battery → buck). Anything else must reference a subsystem.
        for iface in self.interfaces:
            for sub in (iface.from_subsystem, iface.to_subsystem):
                if sub not in sub_ids and sub != "external":
                    raise ValueError(
                        f"interface {iface.id} references unknown subsystem `{sub}`"
                    )
        return self

    @model_validator(mode="after")
    def port_bindings_reference_interfaces(self) -> "ResearchBundle":
        iface_ids = {i.id for i in self.interfaces}
        for sub in self.subsystems:
            for port, iface_id in sub.port_bindings.items():
                if iface_id not in iface_ids:
                    raise ValueError(
                        f"subsystem {sub.id} port {port} bound to "
                        f"unknown interface `{iface_id}`"
                    )
        return self

    # ------------------------------------------------------------- repr
    def _repr_html_(self) -> str:
        """Inline render for jupyter — two tables, no pandas dep.

        Notebook cells that hold a bundle (`board.bundle`) render as
        HTML tables instead of the pydantic `__repr__` blob.
        """
        sub_rows = "".join(
            "<tr>"
            f"<td>{s.id}</td>"
            f"<td>{s.category}</td>"
            f"<td><code>{s.mpn}</code></td>"
            f"<td>{s.package or '&mdash;'}</td>"
            f"<td>${s.price_usd:.2f}</td>"
            f"<td>{s.stock}</td>"
            "</tr>"
            for s in self.subsystems
        )
        iface_rows = "".join(
            "<tr>"
            f"<td>{i.id}</td>"
            f"<td>{i.type}</td>"
            f"<td>{i.from_subsystem}.{i.from_port} &rarr; {i.to_subsystem}.{i.to_port}</td>"
            f"<td>{('%.2f V' % i.voltage_nominal_v) if i.voltage_nominal_v else (i.protocol or '')}</td>"
            "</tr>"
            for i in self.interfaces
        )
        return (
            f"<h4>ResearchBundle <code>{self.project_id}</code></h4>"
            f"<p>baseline <code>{self.research_baseline_git_tag}</code> &middot; "
            f"locked {self.locked_at:%Y-%m-%d} &middot; "
            f"build_qty={self.build_qty} &middot; assembly={self.assembly} &middot; vendor={self.vendor}</p>"
            "<table><caption>Subsystems</caption>"
            "<thead><tr><th>id</th><th>category</th><th>mpn</th><th>pkg</th><th>price</th><th>stock</th></tr></thead>"
            f"<tbody>{sub_rows}</tbody></table>"
            "<table><caption>Interfaces</caption>"
            "<thead><tr><th>id</th><th>type</th><th>edge</th><th>spec</th></tr></thead>"
            f"<tbody>{iface_rows}</tbody></table>"
        )
