#!/usr/bin/env bash
# Statusline for hw-toolkit hardware-agent.
# Reads hw_agent/.state.json and emits: [hardware-agent] | stage=X | project=Y
# Claude Code invokes this on every command refresh.

set -euo pipefail

# Find repo root by walking up to nearest .git directory.
DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
while [[ "$DIR" != "/" && ! -d "$DIR/.git" ]]; do
  DIR="$(dirname "$DIR")"
done
STATE_FILE="$DIR/hw_agent/.state.json"

if [[ -f "$STATE_FILE" ]]; then
  PROJECT="$(python3 -c "import json,sys; print(json.load(open('$STATE_FILE')).get('project','-'))" 2>/dev/null || echo "-")"
  STAGE="$(python3 -c "import json,sys; print(json.load(open('$STATE_FILE')).get('stage','-'))" 2>/dev/null || echo "-")"
else
  PROJECT="-"
  STAGE="-"
fi

printf '[hardware-agent] | stage=%s | project=%s' "$STAGE" "$PROJECT"
