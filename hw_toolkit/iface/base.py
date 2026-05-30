"""Iface base + Pin leaf + exceptions.

`Pin` is the leaf — one electrical node on one Module, identified by its
KiCad symbol pin name. `Iface` is the abstract bundle — subclasses declare
named Pin attributes via `_pin_names: ClassVar[tuple[str, ...]]`.

`Iface.connect_to(other)` validates type equality and yields `(self_pin,
other_pin)` pairs in declaration order. The Board's `connect_iface` method
consumes those pairs and creates / extends Nets.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Iterator

from hw_toolkit.exceptions import HwToolkitError

if TYPE_CHECKING:  # avoid runtime import cycle with board.py
    from hw_toolkit.board import Module


class IfaceTypeMismatch(HwToolkitError):
    """`connect_to()` called between incompatible Iface types.

    `a` and `b` are the two Iface instances; `a_type` / `b_type` are the
    class names for quick inspection from a test.
    """

    def __init__(self, a: "Iface", b: "Iface") -> None:
        self.a = a
        self.b = b
        self.a_type = type(a).__name__
        self.b_type = type(b).__name__
        super().__init__(
            f"IfaceTypeMismatch: cannot connect "
            f"{self.a_type}({a._desc()}) to {self.b_type}({b._desc()})"
        )


class BusReferenceMismatch(HwToolkitError):
    """Two bus Ifaces being connected declare incompatible power references.

    Raised when both sides declare a `reference: Power` whose `voltage`
    differs by more than 50 mV. Prevents accidentally tying a 1.8 V I²C
    line to a 3.3 V host with no level shifter.
    """

    def __init__(
        self, a: "Iface", b: "Iface", v_a: float, v_b: float
    ) -> None:
        self.a = a
        self.b = b
        self.v_a = v_a
        self.v_b = v_b
        super().__init__(
            f"BusReferenceMismatch: {type(a).__name__} references {v_a}V "
            f"but its peer references {v_b}V — needs a level shifter or "
            f"a matching rail"
        )


class PinNotFound(HwToolkitError, AttributeError):
    """Attribute access on an Iface for a pin name it doesn't declare.

    Inherits from AttributeError so Python's normal `hasattr` / `getattr`
    defaulting behaviour still works.
    """

    def __init__(self, iface: "Iface", name: str) -> None:
        self.iface = iface
        self.name = name
        avail = ", ".join(iface._pin_names) or "(none)"
        super().__init__(
            f"PinNotFound: {type(iface).__name__!r} has no pin {name!r}. "
            f"Available: {avail}"
        )


@dataclass(frozen=True)
class Pin:
    """A single electrical node on one Module's symbol.

    `owner_id` is the subsystem id (`Module.id`); `name` is the KiCad
    symbol pin name (`"VIN"`, `"SDA"`, etc.). `member()` produces the
    `(subsystem_id, port_name)` tuple the existing `Net` API expects.
    """

    owner_id: str
    name: str

    def member(self) -> tuple[str, str]:
        return (self.owner_id, self.name)

    def __str__(self) -> str:
        return f"{self.owner_id}.{self.name}"


class Iface:
    """Typed bundle of `Pin`s. Subclasses MUST declare `_pin_names`.

    Subclasses are dataclasses whose fields are `Pin` instances (plus
    optional numeric/string params like `voltage` or `frequency`). The
    `_pin_names` tuple lists the dataclass field names that are Pins —
    in the order they should pair up during `connect_to()`.

    The bundle is dumb on its own — it can't reach a Board / Net. The
    Module holding the bundle (set via `Module.expose(name=iface)`)
    stashes a back-pointer so `iface.connect_to(other)` can delegate to
    `board.connect_iface(a, b)`.
    """

    _pin_names: ClassVar[tuple[str, ...]] = ()
    _power_reference_attr: ClassVar[str | None] = None
    _owner_module: "Module | None" = None

    def _reference_voltage(self) -> float | None:
        """Look up the bus's `<reference>.voltage` if the subclass declares
        a power-reference attribute (`_power_reference_attr`). Returns
        None if no reference is set or the rail's voltage is unspecified.
        """
        attr = self._power_reference_attr
        if attr is None:
            return None
        ref = getattr(self, attr, None)
        if ref is None:
            return None
        return getattr(ref, "voltage", None)

    def pin_pairs(self, other: "Iface") -> Iterator[tuple[Pin, Pin]]:
        """Yield `(self.<name>, other.<name>)` for each declared pin name."""
        if type(self) is not type(other):
            raise IfaceTypeMismatch(self, other)
        for name in self._pin_names:
            yield (getattr(self, name), getattr(other, name))

    def connect_to(self, other: "Iface") -> None:
        """Wire this Iface to another of the same type, pin by pin.

        Both Ifaces must be attached to a Module on the same Board via
        `Module.expose(...)` first. Otherwise raises ValueError — the
        bundle has no way to register the resulting Nets.

        If both sides declare a power reference (`I2C.reference` etc.)
        with a known voltage, the voltages must match within 50 mV or
        `BusReferenceMismatch` is raised. Set `reference=None` on one
        side to opt out (e.g., when a level shifter sits between them).
        """
        if type(self) is not type(other):
            raise IfaceTypeMismatch(self, other)
        v_a, v_b = self._reference_voltage(), other._reference_voltage()
        if v_a is not None and v_b is not None and abs(v_a - v_b) > 0.05:
            raise BusReferenceMismatch(self, other, v_a, v_b)
        mod_a = self._owner_module
        mod_b = other._owner_module
        if mod_a is None or mod_b is None:
            raise ValueError(
                f"connect_to: both Ifaces must be attached to a Module via "
                f".expose() first. self.owner={mod_a!r}, other.owner={mod_b!r}"
            )
        if mod_a.board is not mod_b.board:
            raise ValueError(
                "connect_to: both Ifaces must belong to the same Board"
            )
        mod_a.board.connect_iface(self, other)

    def _desc(self) -> str:
        """Short human description for error messages. Subclasses override."""
        return ", ".join(f"{n}={getattr(self, n)}" for n in self._pin_names)

    def __getattr__(self, name: str):  # only fires when normal lookup fails
        if name.startswith("_"):
            raise AttributeError(name)
        raise PinNotFound(self, name)
