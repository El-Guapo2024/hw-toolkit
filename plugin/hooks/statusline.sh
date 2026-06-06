#!/usr/bin/env bash
# hw-toolkit status line: prepend a live [HW-AGENT …] badge to the user's prior
# status line.
#
# The badge reflects the session state file (~/.claude/.hw-state) that the
# hw_toolkit kernel writes on write_kicad()/write_pcb()/check_erc():
#   [HW-AGENT <project> · <mode|phase> · ERC ✓]   (design mode)
#   [HW-AGENT <project> · planning]               (planning mode)
#
# Claude passes session JSON on stdin and prints whatever this emits on stdout.
# We capture stdin once, print our badge, then chain to the status line that was
# configured before hw-toolkit wired itself in (saved by install-statusline.sh).
set -uo pipefail

input="$(cat)"
config_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
state_file="$config_dir/.hw-state"
prev_file="$config_dir/hw-toolkit/prev_statusline"

# Build the badge from the state file (tiny flat JSON; python parse is fine).
badge="$(
  HW_STATE="$state_file" python3 - <<'PY' 2>/dev/null || printf '[HW-AGENT]'
import json, os
try:
    s = json.load(open(os.environ["HW_STATE"]))
except Exception:
    s = {}
Y = "\033[93m"   # bright yellow — the hw-toolkit color
G = "\033[92m"   # green  (ERC clean)
R = "\033[91m"   # red    (ERC failing)
X = "\033[0m"    # reset

parts = []
proj = s.get("project")
mode = s.get("mode", "design")
if proj:
    parts.append(str(proj))
    if mode == "planning":
        parts.append("planning")
    else:
        parts.append(str(s.get("phase", "schematic")))
        erc = s.get("erc_clean")
        if erc is True:
            parts.append(f"{G}ERC ✓{X}{Y}")
        elif erc is False:
            parts.append(f"{R}ERC ✗{X}{Y}")
elif mode == "planning":
    parts.append("planning")
inner = (" " + " · ".join(parts)) if parts else ""
print(f"{Y}[HW-AGENT{inner}]{X}")
PY
)"
[ -n "$badge" ] || badge="[HW-AGENT]"

# Chain through the prior status line, feeding it the same stdin.
if [ -f "$prev_file" ]; then
  prev="$(cat "$prev_file")"
  if [ -n "$prev" ]; then
    rest="$(printf '%s' "$input" | eval "$prev" 2>/dev/null)"
    if [ -n "$rest" ]; then
      printf '%s %s' "$badge" "$rest"
      exit 0
    fi
  fi
fi

printf '%s' "$badge"
