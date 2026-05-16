#!/usr/bin/env bash
# UserPromptSubmit hook. One-line stage reminder injected before each prompt.
# Light touch -- only fires if .state.json exists.

set -euo pipefail

DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
while [[ "$DIR" != "/" && ! -d "$DIR/.git" ]]; do
  DIR="$(dirname "$DIR")"
done
STATE_FILE="$DIR/hw_agent/.state.json"

if [[ ! -f "$STATE_FILE" ]]; then
  exit 0
fi

STAGE="$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('stage','-'))")"
PROJECT="$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('project','-'))")"

echo "HW-AGENT: stage=$STAGE project=$PROJECT -- respect stage scope (e.g. /spec does NOT pick MPNs, /designer-math does NOT pick parts)"
