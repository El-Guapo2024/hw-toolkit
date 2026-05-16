#!/usr/bin/env bash
# setup_kicad_python.sh — install kicad-python (kipy) matching installed KiCad nightly.
#
# Why: PyPI's kicad-python lags KiCad nightly. Nightly users get proto-binding
# mismatch (e.g. missing BusEntryType). This script does a sparse + shallow
# clone of just the .proto files from KiCad master, builds locally, and
# installs editable into the repo's .venv.
#
# Idempotent. Re-run when KiCad nightly updates protos.

set -euo pipefail

TARGET_DIR="${KICAD_PYTHON_DIR:-$HOME/tools/kicad-python}"
VENV_PIP="${VENV_PIP:-$(git rev-parse --show-toplevel)/.venv/bin/pip}"

if [[ ! -x "$VENV_PIP" ]]; then
  echo "✗ venv pip not found at $VENV_PIP" >&2
  echo "  set VENV_PIP env var or activate the repo venv first." >&2
  exit 1
fi

echo "→ target: $TARGET_DIR"
echo "→ pip:    $VENV_PIP"

if [[ -d "$TARGET_DIR/.git" ]]; then
  echo "→ existing clone; pulling latest"
  git -C "$TARGET_DIR" fetch --depth 1 origin
  git -C "$TARGET_DIR" reset --hard origin/HEAD
  git -C "$TARGET_DIR/kicad" fetch --depth 1 origin
  git -C "$TARGET_DIR/kicad" reset --hard origin/HEAD
else
  echo "→ fresh shallow clone of kicad-python"
  mkdir -p "$(dirname "$TARGET_DIR")"
  git clone --depth 1 https://gitlab.com/kicad/code/kicad-python.git "$TARGET_DIR"

  echo "→ sparse + shallow clone of KiCad protos (api/proto only)"
  rm -rf "$TARGET_DIR/kicad"
  git clone --depth=1 --filter=blob:none --sparse \
    https://gitlab.com/kicad/code/kicad.git "$TARGET_DIR/kicad"
  git -C "$TARGET_DIR/kicad" sparse-checkout set api/proto
fi

echo "→ installing editable"
"$VENV_PIP" install -e "$TARGET_DIR" --force-reinstall --no-deps

echo "→ verify"
"$(dirname "$VENV_PIP")/python" -c "
from kipy import KiCad, schematic  # noqa
print('✓ kipy + schematic protos load')
" || { echo "✗ import failed" >&2; exit 1; }

echo "✓ done. kicad-python at $TARGET_DIR"
