#!/usr/bin/env bash
# hw-toolkit SessionStart: wire [HW-AGENT] into the status line, non-destructively.
#
# Plugins can't ship a statusLine via their manifest — it lives in settings.json.
# So on session start we point settings.json's statusLine at our prepend script,
# saving whatever was there before so statusline.sh can chain through it. Idempotent:
# if ours is already wired, we touch nothing.
set -uo pipefail

config_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
settings="$config_dir/settings.json"
state_dir="$config_dir/hw-toolkit"
prev_file="$state_dir/prev_statusline"
# Resolve to an absolute command now (CLAUDE_PLUGIN_ROOT is set in hook context,
# but NOT when Claude later runs the statusLine command from settings.json).
ours="bash \"${CLAUDE_PLUGIN_ROOT}/hooks/statusline.sh\""

mkdir -p "$state_dir"

python3 - "$settings" "$ours" "$prev_file" <<'PY'
import json, os, sys

settings_path, ours, prev_path = sys.argv[1], sys.argv[2], sys.argv[3]

try:
    with open(settings_path) as f:
        s = json.load(f)
except FileNotFoundError:
    s = {}
except Exception:
    sys.exit(0)  # malformed settings — don't touch it

def cmd_of(v):
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return v.get("command", "")
    return ""

cur = s.get("statusLine")
curcmd = cmd_of(cur)

if curcmd == ours:
    sys.exit(0)  # already wired

# Preserve the prior status line so statusline.sh can chain through it.
if curcmd:
    with open(prev_path, "w") as f:
        f.write(curcmd)

s["statusLine"] = {"type": "command", "command": ours}
with open(settings_path, "w") as f:
    json.dump(s, f, indent=2)
print("hw-toolkit: statusLine wired — [HW-AGENT] now prepends your status line.")
PY
