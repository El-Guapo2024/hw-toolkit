#!/usr/bin/env bash
# hw-toolkit status line: prepend [HW-AGENT] to the user's prior status line.
#
# Claude passes session JSON on stdin and prints whatever this emits on stdout.
# We capture stdin once, print our tag, then chain to the status line that was
# configured before hw-toolkit wired itself in (saved by install-statusline.sh).
set -uo pipefail

input="$(cat)"
state_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hw-toolkit"
prev_file="$state_dir/prev_statusline"

# Live board marker (optional): hw_toolkit writes the active board name here so
# the tag becomes "[HW-AGENT <board>]". Absent → plain tag.
tag="HW-AGENT"
marker="${TMPDIR:-/tmp}/hw_toolkit_board"
if [ -f "$marker" ]; then
  name="$(head -n1 "$marker" 2>/dev/null | tr -d '\n')"
  [ -n "$name" ] && tag="HW-AGENT $name"
fi

prefix="[$tag]"

# Chain through the prior status line, feeding it the same stdin.
if [ -f "$prev_file" ]; then
  prev="$(cat "$prev_file")"
  if [ -n "$prev" ]; then
    rest="$(printf '%s' "$input" | eval "$prev" 2>/dev/null)"
    if [ -n "$rest" ]; then
      printf '%s %s' "$prefix" "$rest"
      exit 0
    fi
  fi
fi

printf '%s' "$prefix"
