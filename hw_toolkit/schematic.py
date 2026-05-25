"""`hw.Schematic` — active object view onto a Board.

A Schematic is a *view* — it doesn't own state. All mutations
(`.add()`, `.connect()`, `.move()`) delegate back to the Board so
there's one source of truth for what's on the schematic.

Two roles:

  1. **Notebook ergonomics.** Chainable mutation API
     (`sch.add(...).connect(...).move(...)`), inline `_repr_html_`
     showing the symbol table + plan-op counts.

  2. **KiCad backend.** `sch.write_kicad()` writes the `.kicad_sch`
     from the live state via the existing planner.

SVG render (lcapy / kicad-cli sch export svg) deferred.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from hw_agent.agents.pcb_designer.kicad_projector import refdes_map_for_bundle
from hw_agent.agents.pcb_designer.schematic import (
    SchematicPlan,
    plan_schematic,
    write_blank_schematic,
)
from hw_agent.core import Interface, SubsystemPick

if TYPE_CHECKING:
    from hw_toolkit.board import Board


@dataclass
class Schematic:
    """Active-object view of a Board's schematic state.

    Construct via `board.schematic` (cached on Board) — never directly.
    """
    board: "Board"
    _moves: dict[str, tuple[float, float]] | None = None

    def __post_init__(self) -> None:
        if self._moves is None:
            self._moves = {}

    # --------------------------------------------------- mutate (delegates)
    def add(self, item: SubsystemPick | Interface) -> "Schematic":
        """Append a SubsystemPick or Interface. Delegates to `board.add`.
        Returns self for chaining."""
        self.board.add(item)
        return self

    def connect(
        self,
        src: str,
        dst: str,
        *,
        type: str = "signal",
        voltage_v: float | None = None,
        protocol: str | None = None,
        iface_id: str | None = None,
    ) -> "Schematic":
        """Add an Interface between two `<subsystem>.<port>` endpoints.

        `iface_id` auto-derives from the connected port names if not
        supplied (e.g. `buck_3v3.VOUT → mcu.VDD` → `buck_3v3_vout_mcu_vdd`).
        """
        from_sub, _, from_port = src.partition(".")
        to_sub, _, to_port = dst.partition(".")
        if not (from_port and to_port):
            raise ValueError(
                f"connect() endpoints must be '<subsystem>.<port>'; "
                f"got src={src!r}, dst={dst!r}"
            )
        if iface_id is None:
            iface_id = f"{from_sub}_{from_port}_{to_sub}_{to_port}".lower()
        self.board.add(
            Interface(
                id=iface_id,
                type=type,  # type: ignore[arg-type]
                from_subsystem=from_sub,
                from_port=from_port,
                to_subsystem=to_sub,
                to_port=to_port,
                voltage_nominal_v=voltage_v,
                protocol=protocol,  # type: ignore[arg-type]
            )
        )
        return self

    def move(self, refdes: str, x_mm: float, y_mm: float) -> "Schematic":
        """Stash an override placement for a refdes. Applied when
        `board.place()` runs (override beats zone heuristic)."""
        assert self._moves is not None
        self._moves[refdes] = (float(x_mm), float(y_mm))
        return self

    # ------------------------------------------------------ derived plans
    @property
    def plan(self) -> SchematicPlan:
        """SchematicPlan computed from current board state."""
        return plan_schematic(self.board.bundle, self.board.sch_path)

    @property
    def symbols(self) -> list[dict[str, str]]:
        """`[{refdes, id, category, mpn, package}, ...]` — derived
        from the live bundle + refdes assignment."""
        bundle = self.board.bundle
        refmap = refdes_map_for_bundle(bundle)
        return [
            {
                "refdes": refmap[s.id],
                "id": s.id,
                "category": s.category,
                "mpn": s.mpn,
                "package": s.package or "",
            }
            for s in bundle.subsystems
        ]

    # ------------------------------------------------------- KiCad backend
    def write_kicad(self, *, overwrite: bool = True) -> SchematicPlan:
        """Write the `.kicad_sch` stub + return the SchematicPlan.

        Defaults to `overwrite=True` — the notebook is the source of
        truth, so re-running cells rewrites the file. Use
        `overwrite=False` if you've hand-edited the sch and want to
        guard it.
        """
        write_blank_schematic(self.board.sch_path, overwrite=overwrite)
        return self.plan

    # ---------------------------------------------------------------- repr
    def __repr__(self) -> str:
        n_sym = len(self.board._subsystems)
        n_iface = len(self.board._interfaces)
        n_moves = len(self._moves or {})
        return (
            f"Schematic(symbols={n_sym}, interfaces={n_iface}, "
            f"overrides={n_moves})"
        )

    def _repr_html_(self) -> str:
        symbols = self.symbols
        sym_rows = "".join(
            "<tr>"
            f"<td><code>{s['refdes']}</code></td>"
            f"<td>{s['id']}</td>"
            f"<td>{s['category']}</td>"
            f"<td><code>{s['mpn']}</code></td>"
            f"<td>{s['package'] or '&mdash;'}</td>"
            "</tr>"
            for s in symbols
        )
        from collections import Counter
        op_counts = Counter(type(op).__name__ for op in self.plan.ops)
        op_rows = "".join(
            f"<tr><td>{name}</td><td>{n}</td></tr>"
            for name, n in op_counts.most_common()
        )
        return (
            f"<h4>Schematic <code>{self.board.project_id}</code></h4>"
            "<table><caption>Symbols</caption>"
            "<thead><tr><th>ref</th><th>id</th><th>category</th>"
            "<th>mpn</th><th>pkg</th></tr></thead>"
            f"<tbody>{sym_rows}</tbody></table>"
            "<table><caption>Plan ops (queued for KiCad write)</caption>"
            "<thead><tr><th>op</th><th>n</th></tr></thead>"
            f"<tbody>{op_rows}</tbody></table>"
        )
