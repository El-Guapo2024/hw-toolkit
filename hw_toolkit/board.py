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
from typing import Any, Literal, Union

from hw_toolkit.kicad.planner import (
    SchematicPlan,
    parse_erc_report,
    plan_schematic,
)
from hw_toolkit.core import Interface, ResearchBundle, SubsystemPick

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
from hw_toolkit.spice import emit_spice_netlist

DEFAULT_PROJECTS_ROOT = Path("docs/projects")

Addable = Union[SubsystemPick, Interface]

NetType = Literal["power", "signal", "data"]

# ERC codes that are synthesis artifacts of the auto-generated symbol
# library, not real wiring bugs. Used as the default suppression set when
# `export_kicad(erc=True)` runs ERC. Mirrors AGENT_GUIDE.md §6.1.
ERC_BASELINE_CODES: tuple[str, ...] = (
    "pin_not_connected",          # intentional NCs (USB-C SBU, MCP73831 STAT, ...)
    "lib_symbol_issues",          # hwagent lib synthesized at runtime
    "pin_to_pin",                 # rails tied directly to pins
    "power_pin_not_driven",       # connector power pins without PWR_FLAG
    "unconnected_wire_endpoint",  # synthesized wire-layout artifact
    "footprint_link_issues",      # synthesized footprint names not in KiCad stock lib
)


_BBOX_NUM = r"-?\d+(?:\.\d+)?"


def _svg_content_bbox(svg: str) -> tuple[float, float, float, float] | None:
    """Scan a KiCad-emitted SVG for the bounding box of all drawn content.

    KiCad writes the whole A4 page (297×210 mm) into `viewBox` even when
    the circuit only fills a small corner. We sweep numeric coordinates
    out of `<rect>`, `<line>`, `<polyline>`, `<polygon>`, `<text>`,
    `<circle>`, and `<path>` elements and take the min/max so the wrapped
    SVG actually crops to the schematic. Returns `(min_x, min_y, w, h)`
    in viewBox units (mm) or None if nothing parseable was found.
    """
    import re

    xs: list[float] = []
    ys: list[float] = []

    # <rect x="" y="" width="" height="">
    for m in re.finditer(
        rf'<rect\b[^>]*?\sx="({_BBOX_NUM})"[^>]*?\sy="({_BBOX_NUM})"'
        rf'[^>]*?\swidth="({_BBOX_NUM})"[^>]*?\sheight="({_BBOX_NUM})"',
        svg,
    ):
        x, y, w, h = map(float, m.groups())
        xs.extend([x, x + w])
        ys.extend([y, y + h])

    # <line x1="" y1="" x2="" y2="">
    for m in re.finditer(
        rf'<line\b[^>]*?\sx1="({_BBOX_NUM})"[^>]*?\sy1="({_BBOX_NUM})"'
        rf'[^>]*?\sx2="({_BBOX_NUM})"[^>]*?\sy2="({_BBOX_NUM})"',
        svg,
    ):
        x1, y1, x2, y2 = map(float, m.groups())
        xs.extend([x1, x2])
        ys.extend([y1, y2])

    # <circle cx="" cy="" r="">
    for m in re.finditer(
        rf'<circle\b[^>]*?\scx="({_BBOX_NUM})"[^>]*?\scy="({_BBOX_NUM})"'
        rf'[^>]*?\sr="({_BBOX_NUM})"',
        svg,
    ):
        cx, cy, r = map(float, m.groups())
        xs.extend([cx - r, cx + r])
        ys.extend([cy - r, cy + r])

    # <text x="" y="">
    for m in re.finditer(
        rf'<text\b[^>]*?\sx="({_BBOX_NUM})"[^>]*?\sy="({_BBOX_NUM})"', svg
    ):
        xs.append(float(m.group(1)))
        ys.append(float(m.group(2)))

    # <polyline points="x,y x,y ...">  / <polygon points="...">
    for m in re.finditer(r'<(?:polyline|polygon)\b[^>]*?\spoints="([^"]+)"', svg):
        pts = re.findall(rf"({_BBOX_NUM})[ ,]+({_BBOX_NUM})", m.group(1))
        for x, y in pts:
            xs.append(float(x))
            ys.append(float(y))

    # <path d="M x y L x y ...">  — sweep all numeric pairs (cheap, ignores
    # arc/control-point geometry but gets us close enough for crop).
    for m in re.finditer(r'<path\b[^>]*?\sd="([^"]+)"', svg):
        pts = re.findall(rf"({_BBOX_NUM})[ ,]+({_BBOX_NUM})", m.group(1))
        for x, y in pts:
            xs.append(float(x))
            ys.append(float(y))

    if not xs or not ys:
        return None
    pad = 2.0  # mm of breathing room around content
    min_x = min(xs) - pad
    min_y = min(ys) - pad
    max_x = max(xs) + pad
    max_y = max(ys) + pad
    return (min_x, min_y, max_x - min_x, max_y - min_y)


def _responsive_svg(
    svg_bytes: bytes,
    *,
    max_width_px: int = 700,
    max_height_px: int = 400,
) -> Any:
    """Wrap a raw SVG byte-string in an HTML container that scales to fit
    a typical notebook cell, with a white background (so dark Jupyter
    themes don't render the schematic invisible) and a content-cropped
    viewBox (KiCad's A4 page is mostly empty whitespace).

    Sizing strategy:
      1. Crop viewBox to actual drawn content (KiCad emits full A4).
      2. Compute the rendered size from the bbox aspect ratio, capped by
         BOTH `max_width_px` and `max_height_px` — whichever hits first
         wins. Without the height cap, a square ~40 mm subsystem would
         render as a 700-px-tall square that fills the screen.
      3. Apply the computed width/height directly to the SVG (in px) so
         browser uses fixed pixel dimensions instead of width:100%.
    """
    import re
    from IPython.display import HTML

    svg = svg_bytes.decode("utf-8", errors="replace")
    svg = re.sub(r"<\?xml[^?]*\?>\s*", "", svg, count=1)
    svg = re.sub(r"<!DOCTYPE[^>]*>\s*", "", svg, count=1)

    # Crop viewBox to actual content if we can derive a bbox.
    bbox = _svg_content_bbox(svg)
    if bbox is not None:
        min_x, min_y, w, h = bbox
        new_vb = f"{min_x:.4f} {min_y:.4f} {w:.4f} {h:.4f}"
        svg = re.sub(
            r'viewBox="[^"]*"', f'viewBox="{new_vb}"', svg, count=1
        )
        aspect = w / h if h > 0 else 1.0
    else:
        aspect = 1.0

    # Pick display size capped by BOTH width and height.
    width_px = max_width_px
    height_px = width_px / aspect
    if height_px > max_height_px:
        height_px = max_height_px
        width_px = height_px * aspect

    # Strip hardcoded width/height on the root <svg>, then set ours.
    svg = re.sub(
        r'(<svg\b[^>]*?)\swidth="[^"]*"', r"\1", svg, count=1, flags=re.DOTALL
    )
    svg = re.sub(
        r'(<svg\b[^>]*?)\sheight="[^"]*"', r"\1", svg, count=1, flags=re.DOTALL
    )
    svg = re.sub(
        r"<svg\b",
        f'<svg width="{width_px:.0f}px" height="{height_px:.0f}px" '
        f'preserveAspectRatio="xMidYMid meet"',
        svg,
        count=1,
    )

    wrapper = (
        f'<div style="display:inline-block;background:#ffffff;'
        f'padding:8px;border-radius:4px;box-sizing:border-box;">{svg}</div>'
    )
    return HTML(wrapper)


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

    def __repr__(self) -> str:
        spec = (f"{self.voltage_v}V" if self.voltage_v is not None
                else self.protocol or "—")
        return (
            f"Net(id={self.id!r}, type={self.type!r}, spec={spec!r}, "
            f"members={len(self.members)})"
        )

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

    # ----------------------------------------------------- typed Iface surface
    def pin(self, name: str) -> "Pin":
        """Sugar for `Pin(owner_id=self.id, name=name)`.

            >>> mcu.pin("PA4")
            Pin(mcu.PA4)
        """
        from hw_toolkit.iface import Pin
        return Pin(owner_id=self.id, name=name)

    def expose(self, **ifaces: Any) -> "Module":
        """Attach typed `Iface` bundles as attributes on this Module.

            >>> from hw_toolkit.iface import Power, I2C
            >>> buck.expose(
            ...     power_in=Power(hv=buck.pin("VIN"), lv=buck.pin("GND"), voltage=12),
            ...     power_out=Power(hv=buck.pin("VOUT"), lv=buck.pin("GND"), voltage=3.3),
            ... )
            >>> mcu.expose(
            ...     vdd=Power(hv=mcu.pin("VDD"), lv=mcu.pin("VSS"), voltage=3.3),
            ...     i2c0=I2C(scl=mcu.pin("SCL"), sda=mcu.pin("SDA"), frequency=400_000),
            ... )
            >>> buck.power_out.connect_to(mcu.vdd)  # type-checked at the call site
        """
        from hw_toolkit.iface import Iface
        for name, iface in ifaces.items():
            if not isinstance(iface, Iface):
                raise TypeError(
                    f"expose: {name!r} must be an Iface subclass, got "
                    f"{type(iface).__name__}"
                )
            if hasattr(self, name):
                raise AttributeError(
                    f"Module {self.id!r} already has attr {name!r}"
                )
            # Iface uses __slots__-free dataclass — set the back-pointer
            # directly so connect_to() can reach the board.
            iface._owner_module = self
            setattr(self, name, iface)
        return self

    @property
    def svg(self) -> bytes:
        """Render this subsystem in isolation, return raw SVG bytes."""
        sub_bundle = self.board._sub_bundle_for(self.pick.id)
        tmp_sch = self.board.kicad_dir / f"_{self.pick.id}.kicad_sch"
        write_populated(sub_bundle, tmp_sch, overwrite=True)
        return render_sch_svg(tmp_sch).read_bytes()

    def show(
        self, *, max_width_px: int = 600, max_height_px: int = 400
    ) -> Any:
        """Display this subsystem inline in jupyter, scaled to fit cell."""
        return _responsive_svg(
            self.svg, max_width_px=max_width_px, max_height_px=max_height_px
        )

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
        # Cache locked_at on first `.bundle` access. Re-deriving on every
        # access invalidates any downstream hash keyed on the bundle.
        self._locked_at: datetime | None = None

    def _init_locked_at(self) -> datetime:
        """Set + return `locked_at` on first access. Subsequent calls reuse
        the cached timestamp so bundle hashes are stable across `.bundle`
        accesses."""
        if self._locked_at is None:
            self._locked_at = datetime.now(timezone.utc)
        return self._locked_at

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

    def resistor(
        self,
        refdes: str,
        value: str,
        *,
        package: str = "0603",
        price_usd: float = 0.01,
        **extra: Any,
    ) -> Module:
        """Add a discrete resistor as a first-class Module.

            >>> r1 = board.resistor("R1", "10k")
            >>> r1.show()  # if board has eeschema integration

        `refdes` becomes the subsystem `id` (lowercased) and the MPN is
        synthesized as `R_<value>_<package>` (e.g. `R_10k_0603`) so the
        BOM aggregator can group identical resistors.
        """
        return self.module(
            id=refdes.lower(),
            category="resistor",
            mpn=f"R_{value}_{package}",
            package=package,
            price_usd=price_usd,
            **extra,
        )

    def capacitor(
        self,
        refdes: str,
        value: str,
        *,
        package: str = "0603",
        price_usd: float = 0.02,
        **extra: Any,
    ) -> Module:
        """Add a discrete capacitor as a first-class Module.

            >>> c1 = board.capacitor("C1", "100nF")
        """
        return self.module(
            id=refdes.lower(),
            category="capacitor",
            mpn=f"C_{value}_{package}",
            package=package,
            price_usd=price_usd,
            **extra,
        )

    def inductor(
        self,
        refdes: str,
        value: str,
        *,
        package: str = "0603",
        price_usd: float = 0.05,
        **extra: Any,
    ) -> Module:
        """Add a discrete inductor as a first-class Module.

            >>> l1 = board.inductor("L1", "10uH", package="0805")
        """
        return self.module(
            id=refdes.lower(),
            category="inductor",
            mpn=f"L_{value}_{package}",
            package=package,
            price_usd=price_usd,
            **extra,
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

    def spi(self, id: str) -> tuple[Net, Net, Net, Net]:
        """Build a 4-wire SPI bus. Returns `(mosi, miso, sck, cs)`.

            >>> mosi, miso, sck, cs = board.spi("flash")
            >>> mosi += "mcu.MOSI", "flash.DI"
            >>> # per-device CS — declare extra nets with board.signal(...)
        """
        mosi = self.net(f"{id}_mosi", type="data", protocol="spi")
        miso = self.net(f"{id}_miso", type="data", protocol="spi")
        sck  = self.net(f"{id}_sck",  type="data", protocol="spi")
        cs   = self.net(f"{id}_cs",   type="data", protocol="spi")
        return mosi, miso, sck, cs

    def uart(self, id: str) -> tuple[Net, Net]:
        """Build a UART pair. Returns `(tx, rx)`.

            >>> tx, rx = board.uart("gps")
            >>> tx += "mcu.UART1_TX", "gps.RX"
            >>> rx += "mcu.UART1_RX", "gps.TX"
        """
        tx = self.net(f"{id}_tx", type="data", protocol="uart")
        rx = self.net(f"{id}_rx", type="data", protocol="uart")
        return tx, rx

    def i2s(self, id: str) -> tuple[Net, Net, Net]:
        """Build an I²S audio bus. Returns `(bclk, lrck, data)`.

            >>> bclk, lrck, data = board.i2s("audio0")
            >>> bclk += "mcu.BCLK", "dac.BCK"; ...
        """
        bclk = self.net(f"{id}_bclk", type="data", protocol="i2s")
        lrck = self.net(f"{id}_lrck", type="data", protocol="i2s")
        data = self.net(f"{id}_data", type="data", protocol="i2s")
        return bclk, lrck, data

    def usbc(
        self,
        id: str,
        *,
        data: bool = True,
        cc: bool = False,
        sbu: bool = False,
    ) -> dict[str, Net]:
        """Build a USB-C connector bundle.

        Only the lines you ask for are created — every net in the
        returned dict must end up with ≥2 members or bundle-time
        `EmptyNetError` fires. Creating CC/SBU nets you then leave
        unwired is the most common cause of that error, so they are
        opt-in:

        - `vbus`, `gnd` — always present (power + return).
        - `dp`, `dm` — present when `data=True` (default; a USB data
          port). Pass `data=False` for a charge-only connector.
        - `cc1`, `cc2` — present when `cc=True`. Enable when you model
          USB-C detection/PD; remember the CC lines need their `Rd`
          pulldowns (or a PD controller) wired in, or they trip
          `EmptyNetError`.
        - `sbu1`, `sbu2` — present when `sbu=True` (alt-mode / debug;
          rarely routed). When left unrouted, prefer `board.nc(...)`.

            >>> usb = board.usbc("conn0")            # vbus/gnd/dp/dm
            >>> usb["vbus"] += "conn0.VBUS", "buck.VIN"
            >>> usb["dp"]   += "conn0.DP",   "mcu.USB_DP"
            >>> usb = board.usbc("conn0", cc=True)   # + cc1/cc2
        """
        nets: dict[str, Net] = {
            "vbus": self.power(f"{id}_vbus", voltage_v=5.0),
            "gnd":  self.net(f"{id}_gnd",  type="power", voltage_v=0),
        }
        if data:
            nets["dp"] = self.net(f"{id}_dp", type="data", protocol="usb")
            nets["dm"] = self.net(f"{id}_dm", type="data", protocol="usb")
        if cc:
            nets["cc1"] = self.net(f"{id}_cc1", type="data", protocol="usb")
            nets["cc2"] = self.net(f"{id}_cc2", type="data", protocol="usb")
        if sbu:
            nets["sbu1"] = self.net(f"{id}_sbu1", type="data", protocol="usb")
            nets["sbu2"] = self.net(f"{id}_sbu2", type="data", protocol="usb")
        return nets

    def dual_supply(
        self,
        id: str,
        *,
        vpos: float,
        vneg: float,
    ) -> tuple[Net, Net]:
        """Bipolar analog supply pair (e.g. ±15V for opamps). Returns
        `(pos_net, neg_net)`.

        `vneg` is supplied as a positive magnitude (e.g. `vneg=15`
        for −15V) because the underlying voltage_v field must be ≥ 0.
        The net id encodes polarity: `<id>_pos` / `<id>_neg`.

            >>> vp, vn = board.dual_supply("analog15", vpos=15, vneg=15)
            >>> vp += "ldo_pos.VOUT", "opamp.VCC"
            >>> vn += "ldo_neg.VOUT", "opamp.VEE"
        """
        if vpos < 0 or vneg < 0:
            raise ValueError(
                "dual_supply takes positive magnitudes only — polarity is "
                "encoded in the net id (_pos / _neg)."
            )
        pos = self.power(f"{id}_pos", voltage_v=vpos)
        neg = self.power(f"{id}_neg", voltage_v=vneg)
        return pos, neg

    def signal(self, id: str, protocol: str = "analog") -> Net:
        """Sugar for `board.net(id, type="data", protocol=protocol)`.

        Default protocol is ``"analog"`` — the most common use-case for
        a bare signal net. Digital buses have dedicated factories
        (``board.spi()``, ``board.i2s()``, etc.). Override with any
        string from the supported enum: i2c, spi, uart, can, usb, swd,
        i2s, analog, gpio, pwm, onewire.
        """
        return self.net(id, type="data", protocol=protocol)

    def swd(self, id: str = "swd") -> tuple[Net, Net, Net]:
        """ARM SWD debug bus. Returns `(swdio, swdclk, nreset)`."""
        swdio  = self.net(f"{id}_swdio",  type="data", protocol="swd")
        swdclk = self.net(f"{id}_swdclk", type="data", protocol="swd")
        nreset = self.net(f"{id}_nreset", type="data", protocol="swd")
        return swdio, swdclk, nreset

    def can(self, id: str) -> tuple[Net, Net]:
        """CAN bus pair. Returns `(canh, canl)`."""
        canh = self.net(f"{id}_h", type="data", protocol="can")
        canl = self.net(f"{id}_l", type="data", protocol="can")
        return canh, canl

    def stereo(self, id: str, protocol: str = "analog") -> tuple[Net, Net]:
        """Stereo audio L/R pair. Returns `(left, right)`."""
        left = self.net(f"{id}_l", type="data", protocol=protocol)
        right = self.net(f"{id}_r", type="data", protocol=protocol)
        return left, right

    def diff_pair(
        self,
        id: str,
        protocol: str = "analog",
    ) -> tuple[Net, Net]:
        """Differential / balanced signal pair. Returns `(pos, neg)`.

        Naming convention only — `<id>_p` / `<id>_n`. Downstream layout
        tools can detect the pair by id suffix and enforce matched-length
        routing.
        """
        p = self.net(f"{id}_p", type="data", protocol=protocol)
        n = self.net(f"{id}_n", type="data", protocol=protocol)
        return p, n

    def nc(self, id: str) -> Net:
        """No-connect / dummy net. Pre-binds an `external.NC` sentinel
        so the net survives the `EmptyNetError` ≥2-member rule even when
        the engineer only attaches one real pin. Use for intentionally
        unused pins (e.g. USB-C SBU1/2 when not routed)."""
        n = self.net(id, type="signal", protocol="gpio")
        n.members.append(("external", "NC"))
        return n

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
        if type in ("signal", "data") and protocol is None:
            raise ValueError(
                f"net {id!r}: type={type!r} requires a protocol "
                f"(one of: i2c, spi, uart, can, usb, swd, i2s, analog, "
                f"gpio, pwm, onewire). Use board.signal(id, protocol=...) "
                f"or a typed helper like board.spi(id), board.i2c(id), "
                f"board.uart(id) instead."
            )
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

    def connect_iface(self, a: Any, b: Any) -> list[Net]:
        """Wire two typed `Iface` bundles pin-by-pin.

        Validates type equality, then for each named pin in declaration order
        either appends to an existing Net that already holds one side, or
        creates a new Net carrying both sides. Returns the list of Nets
        touched in declaration order (one per pin pair).

        Engineers normally call `iface_a.connect_to(iface_b)` — that
        delegates here once both sides are attached via `Module.expose()`.
        """
        from hw_toolkit.iface import Gnd, I2C, Power, SPI, UART, USB2
        type_map = {
            Power: ("power", None),
            Gnd:   ("power", None),
            I2C:   ("data",  "i2c"),
            SPI:   ("data",  "spi"),
            UART:  ("data",  "uart"),
            USB2:  ("signal", "usb"),
        }
        net_type, protocol = type_map.get(type(a), ("signal", None))
        voltage_v: float | None = None
        if isinstance(a, Power):
            voltage_v = a.voltage
        elif isinstance(a, Gnd):
            voltage_v = 0.0

        touched: list[Net] = []
        for pin_a, pin_b in a.pin_pairs(b):
            m_a, m_b = pin_a.member(), pin_b.member()
            existing = self._find_net_with_member(m_a) or \
                self._find_net_with_member(m_b)
            if existing is not None:
                for m in (m_a, m_b):
                    if m not in existing.members:
                        existing.members.append(m)
                touched.append(existing)
                continue
            net_id = self._uniquify_net_id(
                f"{m_a[0]}_{m_a[1]}".lower()
            )
            n = self.net(
                net_id,
                type=net_type,  # type: ignore[arg-type]
                voltage_v=voltage_v,
                protocol=protocol,
            )
            n.members.append(m_a)
            n.members.append(m_b)
            touched.append(n)
        return touched

    def _find_net_with_member(self, member: tuple[str, str]) -> Net | None:
        for n in self._nets:
            if member in n.members:
                return n
        return None

    def _uniquify_net_id(self, base: str) -> str:
        """Append `_2`, `_3`, … if `base` already names an existing net."""
        existing = {n.id for n in self._nets}
        if base not in existing:
            return base
        i = 2
        while f"{base}_{i}" in existing:
            i += 1
        return f"{base}_{i}"

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
        self.validate()
        ifaces = list(self._interfaces)
        for n in self._nets:
            if len(n.members) < 2:
                raise EmptyNetError(net_id=n.id, member_count=len(n.members))
            ifaces.extend(n.expand())
        return self._build_bundle(self._subsystems, ifaces)

    def validate(self) -> None:
        """Structural pre-flight: every net member must reference a real
        module (or the ``external`` no-connect sentinel from ``board.nc``).

        Runs implicitly on every ``.bundle`` access — i.e. on every
        ``write_kicad`` / ``export_kicad`` / ``export_spice`` / ``check_erc``
        — so a typo'd member like ``"mcu.VDD"`` against module id ``"mcu0"``
        is caught the moment the board is materialized instead of being
        silently dropped into the netlist. The ``+=`` operator only checks
        the ``sub.port`` string shape; this resolves ``sub`` against the
        actual modules. Also callable directly as a cheap pre-flight.

        Raises ``UnknownSubsystemError`` on the first unresolved member.
        """
        known = set(self._modules)
        for n in self._nets:
            for sub, _port in n.members:
                if sub == "external":  # board.nc() NC sentinel
                    continue
                if sub not in known:
                    raise UnknownSubsystemError(
                        subsystem_id=sub,
                        known=tuple(sorted(known)),
                    )

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
                locked_at=(self._locked_at
                           if self._locked_at is not None
                           else self._init_locked_at()),
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

    def show(
        self, *, max_width_px: int = 900, max_height_px: int = 500
    ) -> Any:
        """Display the full board schematic inline in jupyter, scaled to
        fit cell. Full board defaults to a slightly larger cap than
        Module.show() since the full schematic has more to show.
        """
        return _responsive_svg(
            self.svg, max_width_px=max_width_px, max_height_px=max_height_px
        )

    # ----------------------------------------------------- final artifact
    def export_kicad(
        self,
        zip_path: str | Path,
        *,
        unzip: bool = False,
        erc: bool = True,
        expected_codes: tuple[str, ...] = ERC_BASELINE_CODES,
    ) -> Path:
        """Bundle the full KiCad project (sch + project + lib) into one
        zip at `zip_path`. This is the only artifact the engineer keeps —
        unzip + open in eeschema for phase 2 hand-tune.

        By default (`erc=True`) ERC runs before zipping with the
        `ERC_BASELINE_CODES` synthesis-artifact suppressions, so a board
        can't ship a zip that never passed ERC. Raises
        `MultipleERCViolations` on a real violation. Pass `erc=False` to
        export without checking, or override `expected_codes` to widen /
        narrow the suppression set.

        Writes the scratch files first if they're missing/stale. If
        `unzip=True`, also drops the unpacked files alongside the zip in
        `<zip_path stem>/` so they're readable without unzipping.
        """
        # 1. Gate: ERC before zip (also writes a current .kicad_sch via
        #    check_erc's autowrite). Skipped only when erc=False.
        if erc:
            self.check_erc(expected_codes=expected_codes)
        else:
            # No ERC gate ran, so write the current .kicad_sch ourselves.
            # (When erc=True, check_erc's autowrite already emitted it.)
            self.write_kicad(overwrite=True)

        # 3. Emit project files so eeschema/pcbnew open cleanly + kicad-cli
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

        # 4. Zip only the final board files. Per-module scratch
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

        # 5. Optional: mirror the files unpacked alongside the zip.
        if unzip:
            import shutil
            unpacked = zip_path.with_suffix("")
            unpacked.mkdir(parents=True, exist_ok=True)
            for f in files:
                shutil.copy2(f, unpacked / f.name)
        return zip_path

    # ---------------------------------------------------- second backend
    def export_spice(self, path: str | Path) -> Path:
        """Write a Berkeley-SPICE netlist (`.cir`) of the current state.

        Topology only — no component models embedded. The engineer is
        expected to `.INCLUDE` their own model files before running
        ngspice / LTspice. Each subsystem maps to one `X<refdes>`
        subcircuit call referencing `<MPN>` as the subckt name.

            >>> board.export_spice("control_hub_v1.cir")
            PosixPath('/.../control_hub_v1.cir')
        """
        path = Path(path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(emit_spice_netlist(self.bundle), encoding="utf-8")
        return path

    @property
    def spice(self) -> str:
        """SPICE netlist as a string. Cheap — computes from in-memory state.

            >>> print(board.spice)
            * control_hub_v1 — netlist ...
        """
        return emit_spice_netlist(self.bundle)

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
        # Fresh boards have no parts yet; the bundle validator (`min_length=1`
        # on subsystems) would treat that as "invalid". Render the empty
        # state as a friendly placeholder so the engineer's first
        # `board` cell isn't a red error.
        if not self._subsystems:
            return (
                f"<h4>Board <code>{self.project_id}</code> "
                "<span style='color:#888'>(empty — add parts with "
                "<code>board.module(...)</code>)</span></h4>"
            )
        try:
            return self.bundle._repr_html_()
        except UnknownSubsystemError as e:
            return (
                f"<h4>Board <code>{self.project_id}</code> "
                "<span style='color:#c00'>(invalid)</span></h4>"
                f"<ul><li>{html_escape(str(e))}</li></ul>"
            )
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
