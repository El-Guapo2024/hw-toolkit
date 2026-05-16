"""KiCad .kicad_sch change detection — for live human/agent collaborative editing.

Workflow (E5 in PLAN.md):

    [human saves in eeschema] ─→ watchfiles fires ─→ parse_kicad_sch
                                                          │
                                                          ▼
                                                    snapshot diff
                                                          │
                                                          ▼
                                                 write change_log.json
                                                          │
                                                          ▼
                                            [agent reads on next turn]

The agent doesn't need the full .kicad_sch parsed — it just needs to know
what *changed* since its last view. So we extract a minimal Snapshot
(symbols by ref, wires by endpoint pair, labels by text+pos) and diff
two snapshots: added / removed / modified.

Uses sexpdata (already a project dep) — no kicad-skip required. The
schematic-side pivot uses its own parser/writer; this module deliberately
matches their data model so we don't introduce divergent views of the
same .kicad_sch.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import sexpdata


# ─── Snapshot model ─────────────────────────────────────────────────────────

@dataclass
class SymbolSnap:
    uuid: str          # KiCad's per-instance UUID — primary identity
    ref: str           # e.g. "R1", "U1" (NOT unique for #PWR/#FLG anonymous syms)
    lib_id: str        # e.g. "Device:R", "hwagent:SY8205FCC"
    value: str         # e.g. "10k", "10µF"
    at: tuple[float, float]
    rotation: float = 0.0
    footprint: str = ""

    def fingerprint(self) -> str:
        """Stable hash for change detection — ignores positions (those are layout
        cosmetics; we surface them separately as 'moved')."""
        return hashlib.md5(
            f"{self.ref}|{self.lib_id}|{self.value}|{self.footprint}".encode()
        ).hexdigest()[:12]

    def display_ref(self) -> str:
        """Human-friendly label. Anonymous power/ground refs (#PWR, #FLG)
        get a short uuid suffix so the agent log doesn't show 4 collisions."""
        if not self.ref or self.ref.startswith("#"):
            return f"{self.ref}({self.uuid[:8]})"
        return self.ref


@dataclass
class WireSnap:
    p1: tuple[float, float]
    p2: tuple[float, float]

    def normalized(self) -> tuple:
        # Order-independent — wire from A→B same as B→A
        return tuple(sorted([self.p1, self.p2]))


@dataclass
class LabelSnap:
    text: str
    at: tuple[float, float]
    kind: str = "label"  # "label" | "global_label" | "hierarchical_label"


@dataclass
class Snapshot:
    """Minimal structured view of a .kicad_sch — just enough for diffing."""
    file_path: str
    file_mtime: float
    file_sha: str  # sha256 of file contents — quick equality check
    symbols: dict = field(default_factory=dict)  # uuid → SymbolSnap (NOT ref —
                                                  # `#PWR` and `#FLG` aren't unique)
    wires: set = field(default_factory=set)       # set of normalized pair tuples
    labels: list = field(default_factory=list)    # list of LabelSnap

    def __eq__(self, other):
        if not isinstance(other, Snapshot):
            return False
        return self.file_sha == other.file_sha

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "file_mtime": self.file_mtime,
            "file_sha": self.file_sha,
            "symbols": {ref: asdict(s) for ref, s in self.symbols.items()},
            "wires_count": len(self.wires),
            "labels_count": len(self.labels),
        }


# ─── Parser ─────────────────────────────────────────────────────────────────

def _sym_value(node, key, default=None):
    """Walk an s-expression node, return the first child of `(key VAL ...)`."""
    if not isinstance(node, list):
        return default
    for item in node[1:]:
        if isinstance(item, list) and item and _name(item[0]) == key:
            if len(item) >= 2:
                return _name(item[1]) if hasattr(item[1], "value") else item[1]
    return default


def _name(token):
    """sexpdata returns Symbol(name) for bare identifiers, str for strings."""
    return token.value() if hasattr(token, "value") else token


def _children(node, key):
    """Yield every direct child whose first element is `key`."""
    if not isinstance(node, list):
        return
    for item in node[1:]:
        if isinstance(item, list) and item and _name(item[0]) == key:
            yield item


def parse_kicad_sch(path: Path) -> Snapshot:
    """Parse a .kicad_sch into a Snapshot. Errors raise ValueError with context."""
    path = Path(path)
    raw = path.read_text()
    sha = hashlib.sha256(raw.encode()).hexdigest()[:16]

    try:
        tree = sexpdata.loads(raw)
    except Exception as e:
        raise ValueError(f"sexp parse error for {path}: {e}") from e

    snap = Snapshot(
        file_path=str(path),
        file_mtime=path.stat().st_mtime,
        file_sha=sha,
    )

    # Symbol instances (top-level (symbol (lib_id ...) (at ...) ...))
    for sym in _children(tree, "symbol"):
        # Skip the (lib_symbols ...) block's children — those are symbol
        # *definitions*, not placements. They live nested deeper.
        lib_id = None
        at_xyrot = None
        uuid = ""
        for sub in sym[1:]:
            if isinstance(sub, list) and sub:
                head = _name(sub[0])
                if head == "lib_id" and len(sub) >= 2:
                    lib_id = _name(sub[1])
                elif head == "at" and len(sub) >= 3:
                    at_xyrot = sub[1:]
                elif head == "uuid" and len(sub) >= 2:
                    uuid = str(_name(sub[1]))
        if not lib_id or at_xyrot is None:
            continue  # not a placed instance

        ref = ""
        value = ""
        footprint = ""
        for prop in _children(sym, "property"):
            if len(prop) >= 3:
                pname = _name(prop[1])
                pval = _name(prop[2])
                if pname == "Reference":
                    ref = str(pval)
                elif pname == "Value":
                    value = str(pval)
                elif pname == "Footprint":
                    footprint = str(pval)
        if not uuid:
            # Fallback for legacy schematics with no UUIDs — fabricate one
            # from ref + position so the dict key stays unique.
            uuid = f"{ref}@{at_xyrot[0]},{at_xyrot[1]}"
        snap.symbols[uuid] = SymbolSnap(
            uuid=uuid, ref=ref, lib_id=lib_id, value=value,
            at=(float(at_xyrot[0]), float(at_xyrot[1])),
            rotation=float(at_xyrot[2]) if len(at_xyrot) >= 3 else 0.0,
            footprint=footprint,
        )

    # Wires
    for w in _children(tree, "wire"):
        pts = next(_children(w, "pts"), None)
        if pts is None:
            continue
        coords = [c for c in pts[1:] if isinstance(c, list) and c and _name(c[0]) == "xy"]
        if len(coords) >= 2:
            p1 = (float(coords[0][1]), float(coords[0][2]))
            p2 = (float(coords[-1][1]), float(coords[-1][2]))
            snap.wires.add(WireSnap(p1, p2).normalized())

    # Labels (local + global + hierarchical)
    for kind in ("label", "global_label", "hierarchical_label"):
        for lbl in _children(tree, kind):
            if len(lbl) < 2:
                continue
            text = str(_name(lbl[1]))
            at = next(_children(lbl, "at"), None)
            if at and len(at) >= 3:
                snap.labels.append(LabelSnap(
                    text=text, at=(float(at[1]), float(at[2])), kind=kind,
                ))

    return snap


# ─── Diff ───────────────────────────────────────────────────────────────────

@dataclass
class Diff:
    """What changed between two snapshots. Empty Diff = no semantic change."""
    file_path: str
    from_sha: str
    to_sha: str
    symbols_added: list = field(default_factory=list)    # list of SymbolSnap
    symbols_removed: list = field(default_factory=list)
    symbols_modified: list = field(default_factory=list)  # list of {ref, before, after}
    symbols_moved: list = field(default_factory=list)     # list of {ref, from, to}
    wires_added: int = 0
    wires_removed: int = 0
    labels_added: int = 0
    labels_removed: int = 0

    def is_empty(self) -> bool:
        return not (self.symbols_added or self.symbols_removed
                    or self.symbols_modified or self.symbols_moved
                    or self.wires_added or self.wires_removed
                    or self.labels_added or self.labels_removed)

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "from_sha": self.from_sha,
            "to_sha": self.to_sha,
            "is_empty": self.is_empty(),
            "symbols_added": [asdict(s) for s in self.symbols_added],
            "symbols_removed": [asdict(s) for s in self.symbols_removed],
            "symbols_modified": self.symbols_modified,
            "symbols_moved": self.symbols_moved,
            "wires_delta": {"added": self.wires_added, "removed": self.wires_removed},
            "labels_delta": {"added": self.labels_added, "removed": self.labels_removed},
        }


def diff(old: Optional[Snapshot], new: Snapshot) -> Diff:
    """Compute a semantic diff between two .kicad_sch snapshots.

    `old=None` means "initial snapshot" — diff reports everything as added.
    Symbols are keyed by UUID; the diff surfaces ref-based display labels
    so the agent log stays human-readable when there are anonymous power
    flags / ground symbols sharing `#PWR` / `#FLG`.
    """
    if old is None:
        return Diff(
            file_path=new.file_path,
            from_sha="",
            to_sha=new.file_sha,
            symbols_added=list(new.symbols.values()),
            wires_added=len(new.wires),
            labels_added=len(new.labels),
        )

    d = Diff(file_path=new.file_path,
             from_sha=old.file_sha, to_sha=new.file_sha)

    old_uuids = set(old.symbols.keys())
    new_uuids = set(new.symbols.keys())

    for u in new_uuids - old_uuids:
        d.symbols_added.append(new.symbols[u])
    for u in old_uuids - new_uuids:
        d.symbols_removed.append(old.symbols[u])

    for u in old_uuids & new_uuids:
        a, b = old.symbols[u], new.symbols[u]
        label = b.display_ref()
        if a.fingerprint() != b.fingerprint():
            d.symbols_modified.append({
                "ref": label,
                "uuid": u,
                "before": asdict(a),
                "after": asdict(b),
            })
        elif a.at != b.at:
            d.symbols_moved.append({
                "ref": label, "uuid": u,
                "from": list(a.at), "to": list(b.at),
            })

    d.wires_added = len(new.wires - old.wires)
    d.wires_removed = len(old.wires - new.wires)

    # Labels — match by (text, at) since labels don't have refs
    old_labels = {(l.text, l.at, l.kind) for l in old.labels}
    new_labels = {(l.text, l.at, l.kind) for l in new.labels}
    d.labels_added = len(new_labels - old_labels)
    d.labels_removed = len(old_labels - new_labels)

    return d
