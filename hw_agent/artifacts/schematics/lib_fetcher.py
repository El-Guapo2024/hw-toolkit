"""On-demand KiCad symbol library fetch + cache + project install.

When the agent needs a `lib_id` like `Sensor_Motion:LSM6DSL` and the
library file isn't installed (no system KiCad, custom lib, OSS hardware
project, SnapEDA-only chip), this module fetches it, caches it under
`~/.cache/hw_agent/kicad_libs/`, and copies it into a project's local
lib dir so kicad-sch-api can resolve it.

Sources:
  - "kicad-official": GitLab `kicad/libraries/kicad-symbols` at tag 9.0.0
    (single-file `.kicad_sym` per library).
  - "cse:<mpn>": pcbparts CSE — caller passes the kicad_sym text we
    already fetched via mcp__pcbparts__cse_get_kicad.
  - git URLs: shallow-clone, locate `<lib_name>.kicad_sym` inside.
  - file URLs / abs paths: copy as-is.

Design:
  - No new deps — urllib + subprocess only.
  - `find_lib(lib_id)` checks ksa's cache first (system + project paths
    already added) then walks per-project sym-lib-table entries.
  - `install_to_project` writes/updates `sym-lib-table` so KiCad picks
    up the lib next time it opens the project. Idempotent.

References to project conventions:
  - `hwagent.kicad_sym` (DIRECTION.md) is for synthesized custom symbols.
    Fetched libs are kept under `lib/<LibName>.kicad_sym` so they don't
    pollute the per-project synth file.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import kicad_sch_api as ksa
from kicad_sch_api.library.cache import get_symbol_cache

from .kicad_paths import kicad_symbol_dir


CACHE_DIR = Path.home() / ".cache" / "hw_agent" / "kicad_libs"

# KiCad official: pinned to 9.0.0 because master uses split `.kicad_symdir`
# directories with one symbol per file (not loadable as a single .kicad_sym).
# Bump when KiCad ships a new stable that we want as the default.
KICAD_OFFICIAL_REF = "9.0.0"
KICAD_OFFICIAL_RAW = (
    "https://gitlab.com/kicad/libraries/kicad-symbols/-/raw/"
    f"{KICAD_OFFICIAL_REF}/{{lib_name}}.kicad_sym"
)


@dataclass
class FoundLib:
    lib_id: str
    library: str
    symbol: str
    lib_path: Path
    source: str  # "system", "project", "cache"


# ─── Search ───────────────────────────────────────────────────────────────


def _split_lib_id(lib_id: str) -> tuple[str, str]:
    if ":" not in lib_id:
        raise ValueError(f"lib_id must be 'Library:Symbol', got {lib_id!r}")
    lib, sym = lib_id.split(":", 1)
    return lib, sym


def _system_symbol_dir() -> Optional[Path]:
    try:
        return kicad_symbol_dir()
    except FileNotFoundError:
        return None


def _read_sym_lib_table(table_path: Path) -> list[tuple[str, Path]]:
    """Return [(lib_name, resolved_path)] from a `sym-lib-table` file."""
    if not table_path.exists():
        return []
    text = table_path.read_text()
    out = []
    # (lib (name "X") (type "KiCad") (uri "...") ...)
    for m in re.finditer(
        r'\(lib\s+\(name\s+"([^"]+)"\)\s+\(type\s+"[^"]+"\)\s+\(uri\s+"([^"]+)"\)',
        text,
    ):
        name, uri = m.group(1), m.group(2)
        # Expand ${KIPRJMOD} → table_path.parent
        uri = uri.replace("${KIPRJMOD}", str(table_path.parent))
        uri = os.path.expandvars(os.path.expanduser(uri))
        out.append((name, Path(uri)))
    return out


def find_lib(
    lib_id: str, project_dir: Optional[Path] = None
) -> Optional[FoundLib]:
    """Resolve `Library:Symbol` to a `.kicad_sym` file.

    Checks (in order): system symbol dir, project sym-lib-table, cache dir.
    Returns None if not found anywhere.
    """
    library, symbol = _split_lib_id(lib_id)

    # 1. system install
    sys_dir = _system_symbol_dir()
    if sys_dir is not None:
        candidate = sys_dir / f"{library}.kicad_sym"
        if candidate.exists():
            return FoundLib(lib_id, library, symbol, candidate, "system")

    # 2. project sym-lib-table
    if project_dir is not None:
        for table in (project_dir / "sym-lib-table",
                      project_dir.parent / "sym-lib-table"):
            for name, path in _read_sym_lib_table(table):
                if name == library and path.exists():
                    return FoundLib(lib_id, library, symbol, path, "project")

    # 3. project-local lib/ dir convention
    if project_dir is not None:
        candidate = project_dir / "lib" / f"{library}.kicad_sym"
        if candidate.exists():
            return FoundLib(lib_id, library, symbol, candidate, "project")

    # 4. user cache
    candidate = CACHE_DIR / f"{library}.kicad_sym"
    if candidate.exists():
        return FoundLib(lib_id, library, symbol, candidate, "cache")

    return None


def list_installed_libs(project_dir: Optional[Path] = None) -> dict:
    """Inventory of available libraries by source."""
    sys_dir = _system_symbol_dir()
    system = (
        sorted(p.stem for p in sys_dir.glob("*.kicad_sym"))
        if sys_dir is not None
        else []
    )
    cache = (
        sorted(p.stem for p in CACHE_DIR.glob("*.kicad_sym"))
        if CACHE_DIR.exists()
        else []
    )
    project = []
    if project_dir is not None:
        for table in (project_dir / "sym-lib-table",
                      project_dir.parent / "sym-lib-table"):
            for name, path in _read_sym_lib_table(table):
                if path.exists():
                    project.append(name)
        local = project_dir / "lib"
        if local.exists():
            for p in local.glob("*.kicad_sym"):
                if p.stem not in project:
                    project.append(p.stem)
        project.sort()
    return {
        "system_dir": str(sys_dir) if sys_dir else None,
        "system": system,
        "cache_dir": str(CACHE_DIR),
        "cache": cache,
        "project": project,
    }


# ─── Fetch (network) ──────────────────────────────────────────────────────


def _http_get(url: str, dest: Path, timeout: float = 30.0) -> Path:
    """Download `url` to `dest` (atomically). Raises on non-200."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        url, headers={"User-Agent": "hw-agent/lib_fetcher"}
    )
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status} fetching {url}")
            tmp.write_bytes(resp.read())
        tmp.replace(dest)
    except urllib.error.HTTPError as e:
        if tmp.exists():
            tmp.unlink()
        raise RuntimeError(f"HTTP {e.code} fetching {url}") from e
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise
    return dest


def _validate_kicad_sym(path: Path) -> None:
    """Quick sanity check: file starts with `(kicad_symbol_lib`."""
    try:
        head = path.read_bytes()[:64].decode("utf-8", errors="replace")
    except Exception as e:
        raise RuntimeError(f"unreadable .kicad_sym at {path}: {e}")
    if "kicad_symbol_lib" not in head:
        raise RuntimeError(
            f"file at {path} doesn't look like a KiCad symbol library "
            f"(first bytes: {head!r})"
        )


def fetch_kicad_official(
    lib_name: str, *, ref: str = KICAD_OFFICIAL_REF, force: bool = False
) -> Path:
    """Download `<lib_name>.kicad_sym` from KiCad's official symbols repo.

    Cached at `~/.cache/hw_agent/kicad_libs/<lib_name>.kicad_sym`. Skips
    network if cache hit unless `force=True`.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / f"{lib_name}.kicad_sym"
    if dest.exists() and not force:
        return dest
    url = (
        "https://gitlab.com/kicad/libraries/kicad-symbols/-/raw/"
        f"{ref}/{lib_name}.kicad_sym"
    )
    _http_get(url, dest)
    try:
        _validate_kicad_sym(dest)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return dest


def fetch_from_url(url: str, *, lib_name: Optional[str] = None,
                   force: bool = False) -> Path:
    """Download an arbitrary `.kicad_sym` URL into the cache."""
    if lib_name is None:
        lib_name = Path(url.split("?", 1)[0]).stem
    if not lib_name:
        raise ValueError(f"can't infer lib_name from url={url!r}")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / f"{lib_name}.kicad_sym"
    if dest.exists() and not force:
        return dest
    _http_get(url, dest)
    _validate_kicad_sym(dest)
    return dest


def fetch_from_git(repo_url: str, *, lib_name: str,
                   ref: str = "HEAD", force: bool = False) -> Path:
    """Shallow-clone `repo_url`, copy `<lib_name>.kicad_sym` into cache.

    Looks at repo root, then walks one level. Returns the cached path.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / f"{lib_name}.kicad_sym"
    if dest.exists() and not force:
        return dest
    with tempfile.TemporaryDirectory(prefix="hwagent_git_") as tmp:
        try:
            subprocess.run(
                ["git", "clone", "--depth=1", "--branch", ref,
                 repo_url, tmp],
                check=True, capture_output=True, timeout=120,
            )
        except subprocess.CalledProcessError:
            # Fall back to default branch if `ref` doesn't exist as a branch
            subprocess.run(
                ["git", "clone", "--depth=1", repo_url, tmp],
                check=True, capture_output=True, timeout=120,
            )
        root = Path(tmp)
        candidates = list(root.glob(f"{lib_name}.kicad_sym")) + \
                     list(root.glob(f"**/{lib_name}.kicad_sym"))
        if not candidates:
            raise RuntimeError(
                f"{lib_name}.kicad_sym not found in {repo_url}"
            )
        shutil.copy2(candidates[0], dest)
    _validate_kicad_sym(dest)
    return dest


def install_from_cse_text(lib_name: str, kicad_sym_text: str) -> Path:
    """Persist a CSE-fetched .kicad_sym body into the cache.

    `mcp__pcbparts__cse_get_kicad(<mpn>)` returns the raw library text;
    callers pass it here so the file ends up cached + indexable.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / f"{lib_name}.kicad_sym"
    dest.write_text(kicad_sym_text)
    _validate_kicad_sym(dest)
    return dest


# ─── Install into a project ───────────────────────────────────────────────


_SYM_LIB_TABLE_HEADER = "(sym_lib_table\n"
_SYM_LIB_TABLE_FOOTER = ")\n"


def _ensure_sym_lib_table(project_dir: Path) -> Path:
    """Create `sym-lib-table` with empty body if missing. Returns the path."""
    table = project_dir / "sym-lib-table"
    if not table.exists():
        table.write_text(_SYM_LIB_TABLE_HEADER + _SYM_LIB_TABLE_FOOTER)
    return table


def _add_table_entry(table_path: Path, lib_name: str, uri: str) -> bool:
    """Append a `(lib …)` entry to sym-lib-table if not already present.

    Returns True if a new entry was written, False if it already existed.
    """
    text = table_path.read_text()
    if re.search(rf'\(lib\s+\(name\s+"{re.escape(lib_name)}"\)', text):
        return False

    entry = (
        f'  (lib (name "{lib_name}")(type "KiCad")(uri "{uri}")'
        '(options "")(descr ""))\n'
    )
    if text.rstrip().endswith(")"):
        body = text.rstrip()[:-1].rstrip() + "\n" + entry + ")\n"
    else:
        body = text + entry
    table_path.write_text(body)
    return True


def install_to_project(
    lib_path: Path,
    project_dir: Path,
    *,
    lib_name: Optional[str] = None,
    use_kiprjmod: bool = True,
) -> Path:
    """Copy a fetched `.kicad_sym` into `<project_dir>/lib/` and register
    it in `sym-lib-table`.

    Idempotent: re-running with the same lib is a no-op except for the file
    overwrite (always copies the latest cache contents).
    """
    project_dir = Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    lib_path = Path(lib_path)
    if not lib_path.exists():
        raise FileNotFoundError(lib_path)

    if lib_name is None:
        lib_name = lib_path.stem

    local_dir = project_dir / "lib"
    local_dir.mkdir(parents=True, exist_ok=True)
    dest = local_dir / f"{lib_name}.kicad_sym"
    shutil.copy2(lib_path, dest)

    table = _ensure_sym_lib_table(project_dir)
    uri = (
        f"${{KIPRJMOD}}/lib/{lib_name}.kicad_sym"
        if use_kiprjmod
        else str(dest)
    )
    _add_table_entry(table, lib_name, uri)

    # Make ksa pick it up immediately for in-process resolves.
    register_with_ksa(dest)
    return dest


def register_with_ksa(lib_path: Path) -> bool:
    """Tell kicad-sch-api's global symbol cache about `lib_path`.

    Lets a subsequent `add_ic(lib_id="<Library>:<Symbol>")` resolve in the
    same Python process without restarting the MCP server.
    """
    cache = get_symbol_cache()
    return cache.add_library_path(Path(lib_path))


# ─── High-level entry points ──────────────────────────────────────────────


def install(
    lib_name: str,
    source: str,
    *,
    project_dir: Optional[Path] = None,
    cse_text: Optional[str] = None,
    force: bool = False,
) -> dict:
    """One-call: fetch from `source`, cache, optionally install to project.

    `source` forms:
      - "kicad-official"           — KiCad official symbols repo
      - "cse:<mpn>"                — caller has already fetched the kicad_sym
                                      text via cse_get_kicad and passes it
                                      via cse_text=...
      - "git:<url>"                — git clone, find the .kicad_sym
      - "url:<https...>"           — raw HTTP file
      - "<https...>"               — same as "url:..."
    """
    if source == "kicad-official":
        cached = fetch_kicad_official(lib_name, force=force)
    elif source.startswith("cse:"):
        if cse_text is None:
            raise ValueError(
                "source 'cse:<mpn>' requires cse_text=... — fetch via "
                "mcp__pcbparts__cse_get_kicad and pass the raw kicad_sym text"
            )
        cached = install_from_cse_text(lib_name, cse_text)
    elif source.startswith("git:"):
        cached = fetch_from_git(source[len("git:"):], lib_name=lib_name,
                                force=force)
    elif source.startswith("url:") or source.startswith("http"):
        url = source[len("url:"):] if source.startswith("url:") else source
        cached = fetch_from_url(url, lib_name=lib_name, force=force)
    else:
        raise ValueError(
            f"unknown source {source!r}. Use 'kicad-official', "
            "'cse:<mpn>', 'git:<url>', or a raw https URL."
        )

    register_with_ksa(cached)
    out = {
        "lib_name": lib_name,
        "source": source,
        "cache_path": str(cached),
        "installed_to_project": False,
    }
    if project_dir is not None:
        installed = install_to_project(cached, Path(project_dir),
                                       lib_name=lib_name)
        out["project_path"] = str(installed)
        out["installed_to_project"] = True
    return out
