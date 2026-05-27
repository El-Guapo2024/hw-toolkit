"""Pure-function SPICE netlist emission for a `ResearchBundle`.

Translates one validated `ResearchBundle` into a Berkeley-SPICE netlist
string. Each `SubsystemPick` becomes an `X<refdes>` subcircuit call
referencing `<MPN>` as the subckt name. Each `Net` becomes a SPICE node;
GND nets are mapped to node `0` per SPICE convention.

The emitter does NOT validate that the user has provided component
models — `.INCLUDE` / `.LIB` directives are the engineer's
responsibility. Without them, ngspice will fail at simulation time with
"unknown subckt", which is a clearer error than us silently dropping
the subsystem.
"""
from __future__ import annotations

import math

from hw_agent.core import Interface, ResearchBundle


def emit_spice_netlist(bundle: ResearchBundle, *, title: str | None = None) -> str:
    """Render `bundle` as SPICE netlist text. Returns the full file body.

    Args:
        bundle: validated `ResearchBundle` from `board.bundle`.
        title: optional title line (SPICE convention: the first line is
            always the title, regardless of content). Defaults to the
            project_id.

    Returns:
        Newline-joined SPICE source. Ends with `.END`.
    """
    title_line = title or f"* {bundle.project_id} — netlist exported by hw_toolkit"
    lines: list[str] = [title_line, "*"]
    lines.append("* Node naming: each declared net becomes a SPICE node.")
    lines.append("* GND-typed nets (voltage_v=0) collapse to node `0`.")
    lines.append("*")

    node_map = _build_node_map(bundle)

    lines.append("* Subsystem instantiations (one X<refdes> per pick)")
    refdes_map = _refdes_map(bundle)
    for sub in bundle.subsystems:
        ref = refdes_map[sub.id]
        # Collect ordered (port → node) bindings for this subsystem.
        port_nodes = sorted(_ports_for(sub.id, bundle, node_map).items())
        if not port_nodes:
            lines.append(
                f"* X{ref} {_sanitize(sub.mpn)} — no ports bound; skipped"
            )
            continue
        nodes_str = " ".join(node for _port, node in port_nodes)
        lines.append(f"X{ref} {nodes_str} {_sanitize(sub.mpn)}")
        # Inline a port-order comment so engineer can verify against datasheet.
        order = ", ".join(f"{p}={n}" for p, n in port_nodes)
        lines.append(f"* port order: {order}")

    lines.append("*")
    lines.append("* Net inventory (for hand inspection)")
    for net_id, node in sorted(node_map.items()):
        lines.append(f"* net `{net_id}` → node `{node}`")

    lines.append("*")
    lines.append("* MODELS / SUBCKTS:")
    lines.append("* Engineer must `.INCLUDE` or `.LIB` the relevant SPICE")
    lines.append("* models for each MPN above. Without them ngspice errors at")
    lines.append("* `unknown subckt`. See README for sourcing pointers.")
    lines.append(".END")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _refdes_map(bundle: ResearchBundle) -> dict[str, str]:
    """Deterministic `subsystem_id → refdes` (U1, U2, ...) for SPICE
    instance names. Stable across runs because we sort by `id`."""
    out: dict[str, str] = {}
    for i, sub in enumerate(sorted(bundle.subsystems, key=lambda s: s.id), start=1):
        out[sub.id] = f"U{i}"
    return out


def _build_node_map(bundle: ResearchBundle) -> dict[str, str]:
    """`net_id → SPICE node name`. GND collapses to `0`.

    Nets are derived from interfaces (each interface = one edge of a star
    expanded from a `Net` object on the board). We collect unique net
    identifiers by interface's `id` prefix (everything before the final
    `_N` star-expansion suffix).
    """
    seen: set[str] = set()
    out: dict[str, str] = {}
    for iface in bundle.interfaces:
        net_id = _net_id_from_interface(iface)
        if net_id in seen:
            continue
        seen.add(net_id)
        # GND nets get node `0` (SPICE convention).
        if (iface.type == "power"
                and iface.voltage_nominal_v is not None
                and math.isclose(iface.voltage_nominal_v, 0.0, abs_tol=1e-6)):
            out[net_id] = "0"
        else:
            out[net_id] = _sanitize(net_id)
    return out


def _net_id_from_interface(iface: Interface) -> str:
    """Strip the trailing `_N` star-expansion index from an iface id to
    recover the original `Net.id` declared by the engineer."""
    parts = iface.id.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return iface.id


def _ports_for(
    subsystem_id: str,
    bundle: ResearchBundle,
    node_map: dict[str, str],
) -> dict[str, str]:
    """`port_name → SPICE node` for one subsystem.

    Aggregates across all interfaces the subsystem participates in. If
    the same port is bound to multiple interfaces, the last one wins —
    a configuration that's usually a user error (same pin on two nets)
    and would already have been rejected by net expansion uniqueness.
    """
    out: dict[str, str] = {}
    for iface in bundle.interfaces:
        net_id = _net_id_from_interface(iface)
        node = node_map.get(net_id, _sanitize(net_id))
        if iface.from_subsystem == subsystem_id:
            out[iface.from_port] = node
        if iface.to_subsystem == subsystem_id:
            out[iface.to_port] = node
    return out


def _sanitize(name: str) -> str:
    """Replace characters SPICE dislikes (spaces, hyphens) with underscores."""
    cleaned = []
    for ch in name:
        if ch.isalnum() or ch in "_":
            cleaned.append(ch)
        else:
            cleaned.append("_")
    return "".join(cleaned)
