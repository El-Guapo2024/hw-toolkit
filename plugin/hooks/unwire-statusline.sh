#!/usr/bin/env bash
# hw-toolkit SessionEnd: if the [HW-AGENT] status line was enabled for THIS
# session only, restore the prior status line so it doesn't leak to other
# agents/sessions. No-op unless the session_only flag is set.
#
# Note: statusLine is global config, so "session-only" is implemented as
# wire-on-enable + restore-here. With multiple concurrent sessions the first to
# end restores; that's acceptable for the single-session opt-in case.
set -uo pipefail

config_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
settings="$config_dir/settings.json"
state_dir="$config_dir/hw-toolkit"
flag="$state_dir/statusline.session_only"
prev_file="$state_dir/prev_statusline"
ours="bash \"${CLAUDE_PLUGIN_ROOT}/hooks/statusline.sh\""

[ -f "$flag" ] || exit 0

HW_SET="$settings" HW_OURS="$ours" HW_PREV="$prev_file" python3 - <<'PY' 2>/dev/null
import json, os
sp, ours, prevf = os.environ["HW_SET"], os.environ["HW_OURS"], os.environ["HW_PREV"]
try:
    s = json.load(open(sp))
except Exception:
    raise SystemExit(0)

def cmd_of(v):
    if isinstance(v, str): return v
    if isinstance(v, dict): return v.get("command", "")
    return ""

cur = s.get("statusLine")
# Only restore if OUR line is currently active (don't stomp a user's later change).
if cmd_of(cur) != ours:
    raise SystemExit(0)

prev = ""
try:
    prev = open(prevf).read().strip()
except Exception:
    prev = ""

if prev:
    s["statusLine"] = {"type": "command", "command": prev}
else:
    s.pop("statusLine", None)
json.dump(s, open(sp, "w"), indent=2)
PY

rm -f "$flag" "$config_dir/.hw-state"
