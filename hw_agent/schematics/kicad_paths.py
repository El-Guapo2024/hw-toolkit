"""KiCad install path detection — works on macOS, Linux, Windows.

Override defaults via env vars KICAD_CLI and KICAD_SYMBOL_DIR.
"""

from __future__ import annotations
import os
import shutil
from functools import lru_cache
from pathlib import Path


_CLI_CANDIDATES = [
    # macOS
    "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
    "/Applications/KiCad/Kicad.app/Contents/MacOS/kicad-cli",
    # Linux
    "/usr/bin/kicad-cli",
    "/usr/local/bin/kicad-cli",
    "/snap/bin/kicad-cli",
    # Windows
    r"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe",
    r"C:\Program Files\KiCad\8.0\bin\kicad-cli.exe",
]

_SYMBOL_DIR_CANDIDATES = [
    # macOS
    Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols"),
    # Linux
    Path("/usr/share/kicad/symbols"),
    Path("/usr/local/share/kicad/symbols"),
    Path("/snap/kicad/current/usr/share/kicad/symbols"),
    # Windows
    Path(r"C:\Program Files\KiCad\9.0\share\kicad\symbols"),
    Path(r"C:\Program Files\KiCad\8.0\share\kicad\symbols"),
]


@lru_cache(maxsize=1)
def kicad_cli() -> str:
    """Return the path to kicad-cli. Honors KICAD_CLI env var, then PATH,
    then a list of common install locations per platform."""
    env = os.environ.get("KICAD_CLI")
    if env and Path(env).exists():
        return env
    found = shutil.which("kicad-cli")
    if found:
        return found
    for c in _CLI_CANDIDATES:
        if Path(c).exists():
            return c
    raise FileNotFoundError(
        "kicad-cli not found. Set the KICAD_CLI environment variable "
        "to the absolute path of the kicad-cli binary."
    )


@lru_cache(maxsize=1)
def kicad_symbol_dir() -> Path:
    """Return the directory containing KiCad's stock .kicad_sym libraries.
    Honors KICAD_SYMBOL_DIR env var, then a list of common install locations."""
    env = os.environ.get("KICAD_SYMBOL_DIR")
    if env and Path(env).exists():
        return Path(env)
    for c in _SYMBOL_DIR_CANDIDATES:
        if c.exists():
            return c
    raise FileNotFoundError(
        "KiCad stock symbol dir not found. Set the KICAD_SYMBOL_DIR "
        "environment variable to the absolute path."
    )
