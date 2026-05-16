#!/usr/bin/env bash
# SessionStart hook for hw-toolkit hardware-agent.
# Injects active project, stage, and doctrine bullets into model context.
# Output is appended as a system reminder when Claude Code starts.

set -euo pipefail

DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
while [[ "$DIR" != "/" && ! -d "$DIR/.git" ]]; do
  DIR="$(dirname "$DIR")"
done
STATE_FILE="$DIR/hw_agent/.state.json"

if [[ ! -f "$STATE_FILE" ]]; then
  exit 0
fi

PROJECT="$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('project','-'))")"
STAGE="$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('stage','-'))")"
DOCTRINE="$(python3 -c "import json; d=json.load(open('$STATE_FILE')).get('doctrine',[]); print(', '.join(d))")"

cat <<EOF
HW-TOOLKIT CONTEXT
==================
Active project: $PROJECT
Current stage: $STAGE
Active doctrines: $DOCTRINE

Pipeline: spec -> designer -> designer-math -> pcb -> router -> gtm

Doctrine reminders (do not violate):
- load-first: pick actuators/sensors/MCU BEFORE rails
- pass1-no-math: /designer picks parts using datasheet typical-app BOM only. NO inductor/feedback math at selection. Math is /designer-math job
- digikey-primary: DK first for stock+catalog. JLC secondary. Mouser tertiary

Live design view: DESIGN_LIVE.md
Build log (append-only by post_subsystem_pick hook): BUILD_LOG.md
EOF
