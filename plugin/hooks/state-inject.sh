#!/usr/bin/env bash
# hw-toolkit UserPromptSubmit: keep design constraints + state in attention every
# turn (survives context compaction), and let the engineer toggle planning/design
# mode in natural language — the same flag-file the status line + kernel share.
#
# Reads the prompt from stdin JSON; prints additionalContext on stdout. Stays
# silent unless a hw session is active (state file exists), so it never pollutes
# unrelated sessions.
set -uo pipefail

config_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
state_file="$config_dir/.hw-state"
input="$(cat)"

prompt="$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("prompt",""))' 2>/dev/null | tr "[:upper:]" "[:lower:]")"

_set_mode() {  # $1 = planning|design
  HW_SF="$state_file" HW_M="$1" python3 - <<'PY' 2>/dev/null
import json, os
p, m = os.environ["HW_SF"], os.environ["HW_M"]
try:
    s = json.load(open(p))
except Exception:
    s = {}
s["mode"] = m
os.makedirs(os.path.dirname(p), exist_ok=True)
json.dump(s, open(p, "w"))
PY
}

case "$prompt" in
  *"planning mode"*|*"explore option"*|*"read only"*|*"readonly"*|*"just show"*|*"don't write"*|*"dont write"*)
    _set_mode planning ;;
  *"design mode"*|*"go ahead"*|*"author it"*|*"start building"*|*"build it"*|*"write it"*)
    _set_mode design ;;
esac

# Inject only when a hw session is active.
[ -f "$state_file" ] || exit 0

HW_SF="$state_file" python3 - <<'PY' 2>/dev/null
import json, os
try:
    s = json.load(open(os.environ["HW_SF"]))
except Exception:
    s = {}
proj = s.get("project", "(none)")
mode = s.get("mode", "design")
phase = s.get("phase", "schematic")
erc = s.get("erc_clean")
ercs = "clean" if erc is True else ("FAILING" if erc is False else "unrun")
guard = ("PLANNING MODE — do NOT call write_kicad/write_pcb or add parts; propose options only."
         if mode == "planning" else
         "DESIGN MODE — authoring allowed; ERC-gate before write_pcb.")
print(
    f"[hw-toolkit active] project={proj} · phase={phase} · ERC={ercs} · {guard} "
    "Hard rules: real KiCad symbols first; ELK orthogonal layout (no fallback); "
    "show the render every step; load-first design order; errors are feedback (one auto-retry)."
)
PY
