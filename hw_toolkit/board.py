"""`Board` — the single object a notebook holds.

Scope (v1): schematic-only. No BOM, no placement, no PCB, no routing,
no fab. Notebook cells:

    1. `board = hw.Board("control_hub_v1")`
    2. one module per cell (`buck = board.add(SubsystemPick(...))`,
       attach math, `buck.show()`)
    3. final cell: `board.connect(...)` x N, `board.show()`, `board.check_erc()`,
       `board.write_kicad()` → finalized `.kicad_sch`.

The matplotlib analogy: `board.add(...)` returns a `Module` handle (like
`fig, ax = plt.subplots()` returns axes), and the handle has its own
`.show()` for per-cell inline render. `board.show()` shows the full
schematic.

`Module.show()` renders the subsystem in isolation by building a
sub-bundle, writing a throwaway `_<id>.kicad_sch`, and invoking
`kicad-cli sch export svg`. The returned object is an `IPython.display.SVG`
so jupyter displays it inline.
"""
from __future__ import annotations

import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Union

from hw_agent.agents.pcb_designer.schematic import (
    SchematicPlan,
    parse_erc_report,
    plan_schematic,
)
from hw_agent.core import Interface, ResearchBundle, SubsystemPick

from hw_toolkit.exceptions import (
    BundleValidationError,
    CheckFailed,
    DuplicateNetError,
    EmptyNetError,
    ERCViolation,
    MultipleERCViolations,
    UnknownSubsystemError,
)
from hw_toolkit.kicad import erc_json, mark_scratch, render_sch_svg, write_populated

DEFAULT_PROJECTS_ROOT = Path("docs/projects")

Addable = Union[SubsystemPick, Interface]

NetType = Literal["power", "signal", "data"]


@dataclass
class Net:
    """A logical net — a bucket of pins all on the same electrical node.

    Net-as-node model (SKiDL / atopile style). The board has many `Net`s;
    each holds an ordered list of `(subsystem_id, port_name)` members.
    Members join with `+=`:

        >>> rail = board.net("3v3", type="power", voltage_v=3.3)
        >>> rail += "buck_3v3.VOUT", "mcu.VDD", "imu.VDD"

    On bundle expansion, each Net w/ N members becomes N-1 star-topology
    `Interface`s (hub = first member). Common-pin merging inside KiCad
    means the resulting wires still form one logical net once rendered.
    """
    id: str
    type: NetType
    voltage_v: float | None = None
    protocol: str | None = None
    members: list[tuple[str, str]] = field(default_factory=list)

    def __iadd__(self, others: Any) -> "Net":
        """Join `<subsystem>.<port>` endpoints to this net.

        Accepts a single string, or a tuple/list of strings — Python's
        `net += "a.x", "b.y"` evaluates the RHS to a tuple.
        """
        if isinstance(others, str):
            others = [others]
        for ep in others:
            if not isinstance(ep, str):
                raise TypeError(f"net members must be 'sub.port' strings, got {ep!r}")
            sub, _, port = ep.partition(".")
            if not (sub and port):
                raise ValueError(
                    f"net member must be '<subsystem>.<port>'; got {ep!r}"
                )
            self.members.append((sub, port))
        return self

    def expand(self) -> list[Interface]:
        """Convert this net to a star of pairwise `Interface`s.

        Hub = first joined member; each subsequent member gets one
        Interface from the hub. The id pattern is `<net_id>_<i>` so the
        bundle's `unique_interface_ids` validator is satisfied.
        """
        if len(self.members) < 2:
            return []
        hub_sub, hub_port = self.members[0]
        out: list[Interface] = []
        for i, (sub, port) in enumerate(self.members[1:], 1):
            out.append(
                Interface(
                    id=f"{self.id}_{i}",
                    type=self.type,
                    from_subsystem=hub_sub,
                    from_port=hub_port,
                    to_subsystem=sub,
                    to_port=port,
                    voltage_nominal_v=self.voltage_v,
                    protocol=self.protocol,  # type: ignore[arg-type]
                )
            )
        return out

    def _repr_html_(self) -> str:
        members = ", ".join(f"<code>{s}.{p}</code>" for s, p in self.members)
        spec = (f"{self.voltage_v}V" if self.voltage_v is not None
                else self.protocol or "—")
        return (
            f"<b>Net <code>{self.id}</code></b> ({self.type}, {spec}) — "
            f"{members or '<i>empty</i>'}"
        )


@dataclass
class Module:
    """Matplotlib-style handle for one subsystem on the board.

    Returned by `Board.add(SubsystemPick(...))`. Carries a slot for
    attaching engineering math results (`module.math = hw.calc.Buck(...)`)
    so each notebook cell stays self-contained.

    `module.show()` renders only this subsystem.
    """
    pick: SubsystemPick
    board: "Board"
    math: Any = None
    notes: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.pick.id

    @property
    def mpn(self) -> str:
        return self.pick.mpn

    @property
    def category(self) -> str:
        return self.pick.category

    @property
    def package(self) -> str:
        return self.pick.package

    @property
    def lcsc(self) -> str | None:
        return self.pick.lcsc

    @property
    def price_usd(self) -> float:
        return self.pick.price_usd

    @property
    def manufacturer(self) -> str:
        return self.pick.manufacturer

    # -------------------------------------------------- late-mutation setters
    # Matplotlib-style chainable setters. Each returns self.
    def set_value(self, value: str) -> "Module":
        """Override the displayed Value field (defaults to mpn)."""
        self.pick = self.pick.model_copy(update={"mpn": value})
        return self

    def set_footprint(self, footprint: str) -> "Module":
        """Override the Footprint field (`Library:Footprint` form)."""
        self.pick = self.pick.model_copy(update={"package": footprint})
        return self

    def attach(self, math: Any) -> "Module":
        """Attach a math/calc result (chainable form of `.math = ...`)."""
        self.math = math
        return self

    def check(self, condition: Any, *, label: str = "") -> "Module":
        """Assert + record. Raises `CheckFailed` on falsy condition.

        Allows `module.check(buck.thermal(...))` to fail-loud while
        tracking that the check ran. `condition.__bool__` is what counts
        (so a `ThermalResult` with custom `__bool__` works).
        """
        ok = bool(condition)
        msg = label or str(condition)
        self.notes.append(f"{'✓' if ok else '✗'} {msg}")
        if not ok:
            raise CheckFailed(subsystem_id=self.pick.id, label=msg)
        return self

    @property
    def svg(self) -> bytes:
        """Render this subsystem in isolation, return raw SVG bytes."""
        sub_bundle = self.board._sub_bundle_for(self.pick.id)
        tmp_sch = self.board.kicad_dir / f"_{self.pick.id}.kicad_sch"
        write_populated(sub_bundle, tmp_sch, overwrite=True)
        return render_sch_svg(tmp_sch).read_bytes()

    def show(self) -> Any:
        """Display this subsystem inline in jupyter."""
        from IPython.display import SVG
        return SVG(data=self.svg)

    def _repr_html_(self) -> str:
        rows = "".join(f"<li>{html_escape(n)}</li>" for n in self.notes)
        return (
            f"<h4>Module <code>{self.pick.id}</code> "
            f"({self.pick.category})</h4>"
            f"<p><code>{self.pick.mpn}</code> · {self.pick.package or '—'} · "
            f"${self.pick.price_usd:.2f}</p>"
            + (f"<ul>{rows}</ul>" if rows else "")
            + (f"<p><b>math:</b> {html_escape(repr(self.math))}</p>"
               if self.math is not None else "")
        )


class Board:
    """Board project — incrementally built up in a notebook.

    State lives in the Board instance. No JSON intermediate. Re-running the
    notebook recreates state from scratch — code is the source of truth.
    """

    def __init__(
        self,
        project_id: str,
        *,
        build_qty: int = 1,
        assembly: str = "hand_solder",
        vendor: str = "jlcpcb",
        scratch_dir: str | Path | None = None,
    ) -> None:
        """Construct a Board. By default, scratch artifacts (KiCad files,
        renders) live in a tempdir under `/tmp`. The engineer calls
        `board.export_kicad("foo.zip")` at the end to produce the one
        artifact that matters. Override `scratch_dir` only for debugging."""
        self.project_id = project_id
        self._build_qty = build_qty
        self._assembly = assembly
        self._vendor = vendor
        if scratch_dir is None:
            scratch_dir = Path(tempfile.mkdtemp(prefix=f"hw_{project_id}_"))
        self.scratch_dir = Path(scratch_dir)
        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        self._subsystems: list[SubsystemPick] = []
        self._interfaces: list[Interface] = []
        self._nets: list[Net] = []
        self._modules: dict[str, Module] = {}

    # ----------------------------------------------------------- builder API
    def add(self, item: Addable) -> Module | "Board":
        """Append a `SubsystemPick` (returns `Module`) or `Interface`
        (returns self). Engineers normally use `board.module(...)` and
        `board.net(...)` instead of `add()` directly."""
        if isinstance(item, SubsystemPick):
            self._subsystems.append(item)
            mod = Module(pick=item, board=self)
            self._modules[item.id] = mod
            return mod
        if isinstance(item, Interface):
            self._interfaces.append(item)
            return self
        raise TypeError(
            f"Board.add() takes SubsystemPick or Interface, "
            f"not {type(item).__name__}"
        )

    def module(self, **fields: Any) -> Module:
        """Add a module to this board. Validates fields via pydantic.

        Replaces `board.add(SubsystemPick(...))`. Returns the `Module`
        handle for chaining math / checks / show.

            >>> buck = board.module(id="buck_3v3", category="buck_converter",
            ...                     mpn="TPS54331DR", package="SOIC-8")
            >>> buck.math = hw.calc.Buck(vin=11.1, vout=3.3, iout=0.5)
            >>> buck.check(buck.math.thermal(rdson_mohm=80, theta_ja=40))
            >>> buck.show()
        """
        pick = SubsystemPick(**fields)
        result = self.add(pick)
        # add() returns Module for SubsystemPick — cast for the type checker.
        assert isinstance(result, Module)
        return result

    # ---------------------------------------------- factory shortcuts
    def power(self, id: str, voltage_v: float) -> Net:
        """Sugar for `board.net(id, type="power", voltage_v=voltage_v)`.

            >>> v3v3 = board.power("3v3", 3.3)
            >>> v3v3 += "buck.VOUT", "mcu.VDD"
        """
        return self.net(id, type="power", voltage_v=voltage_v)

    def gnd(self, id: str = "gnd") -> Net:
        """Sugar for a 0V GND net.

            >>> g = board.gnd()
            >>> g += "buck.GND", "mcu.GND"
        """
        return self.net(id, type="power", voltage_v=0)

    def i2c(self, id: str) -> tuple[Net, Net]:
        """Build a paired SDA/SCL bus. Returns `(sda, scl)`.

            >>> sda, scl = board.i2c("bus0")
            >>> sda += "mcu.SDA", "imu.SDA"; scl += "mcu.SCL", "imu.SCL"
        """
        sda = self.net(f"{id}_sda", type="data", protocol="i2c")
        scl = self.net(f"{id}_scl", type="data", protocol="i2c")
        return sda, scl

    def signal(self, id: str, protocol: str = "uart") -> Net:
        """Sugar for `board.net(id, type="data", protocol=protocol)`."""
        return self.net(id, type="data", protocol=protocol)

    def net(
        self,
        id: str,
        *,
        type: NetType,
        voltage_v: float | None = None,
        protocol: str | None = None,
    ) -> Net:
        """Create + register a new `Net`. Members joined via `+=`.

            >>> sda = board.net("sda", type="data", protocol="i2c")
            >>> sda += "mcu.SDA", "imu.SDA", "eeprom.SDA"
        """
        if any(existing.id == id for existing in self._nets):
            raise DuplicateNetError(net_id=id)
        n = Net(id=id, type=type, voltage_v=voltage_v, protocol=protocol)
        self._nets.append(n)
        return n

    def connect(
        self,
        src: str,
        dst: str,
        *,
        type: str = "signal",
        voltage_v: float | None = None,
        protocol: str | None = None,
        iface_id: str | None = None,
    ) -> "Board":
        """Connect two `<subsystem>.<port>` endpoints with an `Interface`.

        `iface_id` auto-derives from port names if not supplied.
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
        self.add(
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

    def __getitem__(self, subsystem_id: str) -> Module:
        """`board["buck_3v3"]` → `Module`. Convenience for late access."""
        try:
            return self._modules[subsystem_id]
        except KeyError:
            raise UnknownSubsystemError(
                subsystem_id=subsystem_id,
                known=tuple(self._modules.keys()),
            ) from None

    # ----------------------------------------------------- introspection
    @property
    def parts(self) -> dict[str, Module]:
        """Read-only mapping `subsystem_id → Module`. Use for iteration."""
        return dict(self._modules)

    @property
    def nets(self) -> dict[str, Net]:
        """Read-only mapping `net_id → Net`."""
        return {n.id: n for n in self._nets}

    def summary(self) -> str:
        """One-screen text overview of the board: parts, nets, checks."""
        lines = [f"Board {self.project_id!r}"]
        lines.append(f"  parts ({len(self._modules)}):")
        for m in self._modules.values():
            math = f"  math={type(m.math).__name__}" if m.math is not None else ""
            checks = f"  checks={len(m.notes)}" if m.notes else ""
            lines.append(
                f"    {m.id:14} {m.mpn:30} {m.package or '-':14}"
                f"  ${m.price_usd:>5.2f}{math}{checks}"
            )
        lines.append(f"  nets ({len(self._nets)}):")
        for n in self._nets:
            spec = (f"{n.voltage_v}V" if n.voltage_v is not None
                    else n.protocol or "")
            members = ", ".join(f"{s}.{p}" for s, p in n.members)
            lines.append(f"    {n.id:14} [{n.type:5} {spec:5}]  {members}")
        return "\n".join(lines)

    # ----------------------------------------------------- pydantic view
    @property
    def bundle(self) -> ResearchBundle:
        """Validate + snapshot current state. Raises
        `BundleValidationError` on cross-field issues.

        Nets are expanded to star-topology Interfaces here, then appended
        to the engineer's manual `.connect()` interfaces. Result is one
        flat interface list the planner consumes.
        """
        ifaces = list(self._interfaces)
        for n in self._nets:
            if len(n.members) < 2:
                raise EmptyNetError(net_id=n.id, member_count=len(n.members))
            ifaces.extend(n.expand())
        return self._build_bundle(self._subsystems, ifaces)

    def _sub_bundle_for(self, subsystem_id: str) -> ResearchBundle:
        """Bundle slice containing only one subsystem (no interfaces).

        Used by `Module.show()` to render a single module without the rest
        of the board around it. Interfaces are dropped because connections
        haven't typically been declared yet at module-cell time.
        """
        subs = [s for s in self._subsystems if s.id == subsystem_id]
        if not subs:
            raise KeyError(f"unknown subsystem `{subsystem_id}`")
        return self._build_bundle(subs, [])

    def _build_bundle(
        self,
        subs: list[SubsystemPick],
        ifaces: list[Interface],
    ) -> ResearchBundle:
        try:
            return ResearchBundle(
                schema_version=1,
                project_id=self.project_id,
                subsystems=list(subs),
                interfaces=list(ifaces),
                build_qty=self._build_qty,
                assembly=self._assembly,  # type: ignore[arg-type]
                vendor=self._vendor,  # type: ignore[arg-type]
                research_baseline_git_tag=f"{self.project_id}/live",
                locked_at=datetime.now(timezone.utc),
            )
        except Exception as e:
            errors = getattr(e, "errors", None)
            err_list = (
                tuple(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
                      for err in e.errors())
                if callable(errors) else (str(e),)
            )
            raise BundleValidationError(
                path=Path(f"<live:{self.project_id}>"),
                errors=err_list,
            ) from e

    # ------------------------------------------------------------- paths
    @property
    def kicad_dir(self) -> Path:
        """Scratch dir for KiCad files. Lives under /tmp by default — no
        artifacts in the engineer's project tree until `.export_kicad()`."""
        d = self.scratch_dir / "kicad"
        mark_scratch(d)
        return d

    @property
    def sch_path(self) -> Path:
        return self.kicad_dir / f"{self.project_id}.kicad_sch"

    @property
    def pro_path(self) -> Path:
        return self.kicad_dir / f"{self.project_id}.kicad_pro"

    # ------------------------------------------------------------ schematic
    def write_kicad(self, *, overwrite: bool = True) -> SchematicPlan:
        """Populate the `.kicad_sch` from current state. Returns the plan
        that was applied (useful for inspection)."""
        write_populated(self.bundle, self.sch_path, overwrite=overwrite)
        # Sync the project-local lib tables so kicad-cli ERC + eeschema can
        # resolve `hwagent:<symbol>` references in this sheet.
        (self.kicad_dir / "sym-lib-table").write_text(
            _PROJECT_SYM_LIB_TABLE, encoding="utf-8",
        )
        flt = self.kicad_dir / "fp-lib-table"
        if not flt.exists():
            flt.write_text(_PROJECT_FP_LIB_TABLE, encoding="utf-8")
        return plan_schematic(self.bundle, self.sch_path)

    @property
    def svg(self) -> bytes:
        """Render the full schematic to SVG bytes (in scratch). Pure
        property — kept in /tmp, no engineer-visible folder writes."""
        self.write_kicad(overwrite=True)
        path = render_sch_svg(self.sch_path)
        return path.read_bytes()

    def show(self) -> Any:
        """Display the full board schematic inline in jupyter."""
        from IPython.display import SVG
        return SVG(data=self.svg)

    # ----------------------------------------------------- final artifact
    def export_kicad(
        self,
        zip_path: str | Path,
        *,
        unzip: bool = False,
    ) -> Path:
        """Bundle the full KiCad project (sch + project + lib) into one
        zip at `zip_path`. This is the only artifact the engineer keeps —
        unzip + open in eeschema for phase 2 hand-tune.

        Writes the scratch files first if they're missing/stale. If
        `unzip=True`, also drops the unpacked files alongside the zip in
        `<zip_path stem>/` so they're readable without unzipping.
        """
        # 1. Ensure the .kicad_sch exists + is current.
        self.write_kicad(overwrite=True)

        # 2. Emit project files so eeschema/pcbnew open cleanly + kicad-cli
        #    resolves the project-local symbol library on ERC.
        if not self.pro_path.exists():
            self.pro_path.write_text(_MINIMAL_KICAD_PRO, encoding="utf-8")
        slt = self.kicad_dir / "sym-lib-table"
        # Always rewrite — `sch_ops.add_custom_ic` may have written a stale
        # absolute-path version on first add.
        slt.write_text(_PROJECT_SYM_LIB_TABLE, encoding="utf-8")
        flt = self.kicad_dir / "fp-lib-table"
        if not flt.exists():
            flt.write_text(_PROJECT_FP_LIB_TABLE, encoding="utf-8")

        # 3. Zip only the final board files. Per-module scratch
        #    (`_<id>.kicad_sch`, `_<id>.svg`) and ERC reports are excluded —
        #    the engineer only needs the deliverables.
        zip_path = Path(zip_path).expanduser().resolve()
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        keep_names = {
            self.sch_path.name,
            self.pro_path.name,
            f"{self.project_id}.svg",
            "hwagent.kicad_sym",
            "sym-lib-table",
            "fp-lib-table",
        }
        files = sorted(
            f for f in self.kicad_dir.iterdir()
            if f.is_file() and f.name in keep_names
        )
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                zf.write(f, arcname=f.name)

        # 4. Optional: mirror the files unpacked alongside the zip.
        if unzip:
            import shutil
            unpacked = zip_path.with_suffix("")
            unpacked.mkdir(parents=True, exist_ok=True)
            for f in files:
                shutil.copy2(f, unpacked / f.name)
        return zip_path

    def check_erc(
        self,
        *,
        expected_codes: tuple[str, ...] = (),
        autowrite: bool = True,
    ) -> None:
        """Run `kicad-cli sch erc` and raise `MultipleERCViolations` on any
        real violations. `expected_codes` lets the engineer pre-acknowledge
        known false-positives.

        If `autowrite=True` (default) and the .kicad_sch is missing or
        stale, it's regenerated first.
        """
        if autowrite or not self.sch_path.exists():
            self.write_kicad(overwrite=True)
        report_path = erc_json(self.sch_path)
        erc = parse_erc_report(report_path, expected_codes=expected_codes)
        if erc.clean:
            return
        raise MultipleERCViolations(
            report_path=erc.report_path,
            violations=tuple(
                ERCViolation(
                    type=v.type, severity=v.severity,
                    description=v.description, refs=v.items,
                )
                for v in erc.real_violations
            ),
            expected=tuple(
                ERCViolation(
                    type=v.type, severity=v.severity,
                    description=v.description, refs=v.items,
                )
                for v in erc.expected_violations
            ),
        )

    # ------------------------------------------------------------------ repr
    def __repr__(self) -> str:
        return (
            f"Board(project_id={self.project_id!r}, "
            f"subsystems={len(self._subsystems)}, "
            f"interfaces={len(self._interfaces)})"
        )

    def _repr_html_(self) -> str:
        try:
            return self.bundle._repr_html_()
        except BundleValidationError as e:
            errs = "".join(f"<li>{html_escape(err)}</li>" for err in e.errors)
            return (
                f"<h4>Board <code>{self.project_id}</code> "
                "<span style='color:#c00'>(invalid)</span></h4>"
                f"<ul>{errs}</ul>"
            )


def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_MINIMAL_KICAD_PRO = """\
{
  "board": {"design_settings": {}},
  "boards": [],
  "cvpcb": {"equivalence_files": []},
  "erc": {"erc_exclusions": [], "meta": {"version": 0}, "pin_map": [], "rule_severities": {}},
  "libraries": {
    "pinned_footprint_libs": [],
    "pinned_symbol_libs": ["hwagent"]
  },
  "meta": {"filename": "project.kicad_pro", "version": 1},
  "net_settings": {},
  "pcbnew": {},
  "schematic": {"legacy_lib_dir": "", "legacy_lib_list": []},
  "sheets": [["00000000-0000-0000-0000-000000000000", "Root"]],
  "text_variables": {}
}
"""

# Project-local sym-lib-table that points at hwagent.kicad_sym, so kicad-cli
# and eeschema both resolve `hwagent:<symbol>` lib_ids on this project.
_PROJECT_SYM_LIB_TABLE = """\
(sym_lib_table
  (version 7)
  (lib (name "hwagent")(type "KiCad")(uri "${KIPRJMOD}/hwagent.kicad_sym")(options "")(descr "Project-local synthesized custom ICs"))
)
"""

# Project-local fp-lib-table referencing KiCad's stock footprint libs.
# `${KICAD9_FOOTPRINT_DIR}` is set by KiCad install; eeschema resolves it.
# kicad-cli evaluates env vars too, so ERC's `footprint_link_issues`
# warning quiets when at least one library is registered.
_PROJECT_FP_LIB_TABLE = """\
(fp_lib_table
  (version 7)
  (lib (name "Package_SO")(type "KiCad")(uri "${KICAD9_FOOTPRINT_DIR}/Package_SO.pretty")(options "")(descr ""))
  (lib (name "Package_TO_SOT_SMD")(type "KiCad")(uri "${KICAD9_FOOTPRINT_DIR}/Package_TO_SOT_SMD.pretty")(options "")(descr ""))
  (lib (name "Package_DFN_QFN")(type "KiCad")(uri "${KICAD9_FOOTPRINT_DIR}/Package_DFN_QFN.pretty")(options "")(descr ""))
  (lib (name "Package_LGA")(type "KiCad")(uri "${KICAD9_FOOTPRINT_DIR}/Package_LGA.pretty")(options "")(descr ""))
  (lib (name "Module")(type "KiCad")(uri "${KICAD9_FOOTPRINT_DIR}/Module.pretty")(options "")(descr ""))
)
"""
