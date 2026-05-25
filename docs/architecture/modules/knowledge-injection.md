# Module Design — Knowledge Injection

## Purpose

Inject harness knowledge — stage, doctrine, gate status, focused subsystem context, tool ordering rules — into the LLM's context every turn via hooks. Mirrors the caveman pattern (SessionStart + UserPromptSubmit hooks emitting `<system-reminder>` blocks) because that pattern empirically produces stronger instruction-following than skill-file-based loading.

**Core thesis:** rules in skill `.md` files get read once and forgotten over long sessions. Rules injected every turn survive context pressure, summarization, and topic drift. The injection IS the contract.

This module is the harness equivalent of `caveman-activate.js` + `caveman-mode-tracker.js` — but scoped to project/stage/gate state instead of communication style.

## Public contract

Hook scripts (each is a leaf script returning JSON to stdout). **Three granularities of injection — coarse to fine.**

### Coarse — session + turn
- `hw_agent/scripts/hooks/inject_session_start.py` — fires on session boot. Reads project manifest + baselines + gate status. Emits a `<system-reminder>` block with the static-per-session facts.
- `hw_agent/scripts/hooks/inject_prompt_submit.py` — fires on every user turn. Re-emits the same block, refreshed with per-turn state (latest gate result, focused subsystem, last failed check).

### Medium — focus context
- `hw_agent/scripts/hooks/inject_focus.py` — fires when a tool call focuses on one subsystem (e.g. `subsystem_status buck_6v`). Adds a focused subsystem context block for the rest of the session, including ports, interfaces, chosen_part, last calculations.

### Fine — per-tool injection (NEW, the unique harness pattern)
- `hw_agent/scripts/hooks/inject_pre_tool.py` — fires on every `PreToolUse`. Dispatches to a per-tool snippet that injects the rules + ordering hints + invariants specific to **that one tool call**. The agent never needs to remember tool rules — the harness reminds it at the call site.

**Per-tool snippets** live in `hw_agent/library/tool_doctrine/<tool_id>.yaml`. One file per MCP tool that has non-trivial rules:

```yaml
# hw_agent/library/tool_doctrine/mcp__designer-mcp__analyze_candidate.yaml
tool: mcp__designer-mcp__analyze_candidate
inject_when: always
rules:
  - thermal_gate is hard. FAIL → eliminate this candidate, do not propose it.
  - show working — formula → substituted values → result → PASS/FAIL.
  - if package not in requirements.allowed_packages → flag as soft fail.
prerequisites:
  - subsystem requirements must be set (`subsystem_status` shows ready)
  - parts-finder must have run first to populate candidates
references:
  - doctrine: pass1_no_math
  - see also: thermal_gate snippet
```

The hook renders this into the `<system-reminder>` block right before the tool call. The LLM sees the rules + prereqs at the exact moment it's about to act.

**Side effects:** none beyond emitting stdout text. Hook scripts are pure reads.

**Side effects:** none beyond emitting stdout text. Hook scripts are pure reads.

**Configured by:** `.claude/settings.json` entries pointing at each script for the relevant event.

## The injection format

Every block follows the same shape. Caveman-style: declarative, fragments OK, hard rules + edge cases, no prose.

```
<system-reminder>
## HW-HARNESS ACTIVE — project=<name> · stage=<stage>

## Doctrine (active for this stage)
- <doctrine_id>: <one-line rule>
- <doctrine_id>: <one-line rule>

## Current state
- Baseline locked: <latest baseline>
- Open gate: <gate_id> — <N>/<M> PASS, <failures>
- Active subsystems: <name> (<status>), <name> (<status>), ...
- Last EEResult: <subsystem>.<check> — <PASS/FAIL/PENDING> @ <ts>

## Tools to call in this stage (in order)
1. mcp__designer-mcp__<tool> — <when>
2. mcp__pcbparts__<tool> — <when>

## Hard rules (do not break)
- <rule_id>: <action> | <reason if non-obvious>
- <rule_id>: <action> | <reason>

## Boundaries (when injection stops applying)
- Off when: <condition>
</system-reminder>
```

**Style rules** (for the rendered output, not Python code):
- Active voice, present tense.
- Bullet > paragraph. Fragment > sentence.
- Each rule under 100 chars.
- No "please." No "you should." Direct imperative.
- Edge cases get their own bullet, not buried in prose.
- The reminder is the same every turn unless state changed — predictability beats novelty.

## Internal layout

```
hw_agent/scripts/hooks/
├── inject_session_start.py
├── inject_prompt_submit.py
├── inject_focus.py
├── _injection/
│   ├── __init__.py
│   ├── render.py              # turns state dict → markdown block
│   ├── state.py               # loads project state cheaply (cached)
│   ├── doctrine.py            # loads stage-specific doctrine from library/
│   └── templates/
│       ├── session_header.md.tmpl
│       ├── gate_status.md.tmpl
│       ├── focused_subsystem.md.tmpl
│       └── tool_order.md.tmpl
└── tests/
    └── test_injection_render.py
```

**Data flow per UserPromptSubmit invocation:**
1. Read `CLAUDE_PROJECT_DIR` env var → find active project manifest.
2. Load cached project state (manifest, baselines, gate results) — re-read only files whose mtime changed since last invocation.
3. Render templates with state values.
4. Concatenate sections, emit as a single `<system-reminder>` block on stdout.
5. Target latency: <50ms per turn.

## Dependencies

**Imports from:**
- `hw_agent.core` — for `ProjectManifest`, `Baseline` models.
- `hw_agent.library` — for stage-specific doctrine + gate definitions.
- `hw_agent.scripts.gate_runner` — for current gate status (cached).
- `pyyaml`, `jinja2` (for template rendering) — pinned.

**Imported by:** none. Hook scripts are leaves.

**Forbidden imports:**
- ❌ Anything that does network I/O — must be fast + offline.
- ❌ `from mcp_server.*` — hooks read state, do not invoke the MCP transport.

## Configuration

- `.claude/settings.json` declares which script runs on which event.
- `HW_INJECTION_VERBOSITY` env var: `minimal | full | focused` (default `full`).
  - `minimal` — stage + doctrine only.
  - `full` — + gate status + active subsystems + tool order.
  - `focused` — + current focused subsystem's full context.
- `HW_INJECTION_DISABLE` env var: `1` to short-circuit all injection (debug / measurement).

## Lifecycle / state

**Stateless processes.** Each hook invocation is a fresh subprocess. Persistent state lives in repo files; injection scripts read, never write.

**Cache layer** in `~/.cache/hw-agent/injection/<project>/state.json` — keyed on file mtime, invalidated on any change to manifest, baselines, gate results, or subsystem yamls. Avoids re-parsing on every turn.

## Failure modes

- Script error → emits `<system-reminder>HW-HARNESS injection error: <reason></system-reminder>` so the LLM still sees something useful and the user gets a signal.
- Script timeout (>2s) → harness drops the hook output silently. **Critical:** broken injection must never block tool calls.
- Missing project manifest → emit `HW-HARNESS: no active project. Run /spec or set CLAUDE_PROJECT_DIR.` Don't crash.
- Stale gate cache → if cache age > 60s, force re-run of gate before emitting.

**Critical rule:** the injection layer fails open. A broken hook is annoying; a hook that blocks the harness is unacceptable.

## Performance characteristics

| event | budget | typical |
|---|---|---|
| `SessionStart` | <200 ms | ~100 ms (full state load, no cache) |
| `UserPromptSubmit` | <50 ms | ~20 ms (cached state) |
| `inject_focus` | <100 ms | one subsystem yaml load |

Hot path on every turn must stay sub-50ms or it gets annoying fast.

## Testing

- `hw_agent/scripts/hooks/tests/test_injection_render.py` — feed synthetic state, snapshot the rendered markdown, diff against golden.
- Integration test: spin up `control_hub_v1`-like fixture project, simulate session, verify block contents.
- Regression: when injection format changes, the golden snapshots flag every block that drifted — forces deliberate format decisions.

## Open questions / known limitations

1. **Verbosity levels.** Caveman has `lite/full/ultra`. Harness should match: minimal/full/focused. Need user feedback on which is the right default.
2. **Per-stage doctrine.** Today SessionStart injects two doctrines (`load_first`, `live_visual_only`). When more stages exist with their own doctrine, the injection must swap based on `stage`. Lookup goes through `library/doctrine/<stage>.yaml` (new sub-folder).
3. **Per-subsystem focus.** How does the harness know which subsystem the LLM is focused on? Heuristics: most-recent `subsystem_status` call, or explicit `/focus buck_6v` command. Defer.
4. **Injection budget.** Caveman block is ~30 lines. Harness block could grow large with many subsystems. Cap at ~50 lines; if more, summarize ("12 subsystems: 8 ready, 3 pending, 1 blocked") and let LLM ask for detail.
5. **What NOT to inject.** Tempting to push everything; resist. Anything not actionable for the current turn doesn't belong. Test: "would this change what the LLM does on the next response?" If no, exclude.
6. **Drift detection.** If the LLM's output contradicts the active doctrine despite injection, that's a signal the injection failed or wasn't strong enough. Add post-hoc check (Phase >8).
7. **Multi-project sessions.** Currently assumes one project at a time. If a session touches multiple, injection needs explicit switching.

## How this differs from caveman

| | caveman | hw-harness |
|---|---|---|
| What's injected | communication style (terseness mode) | project + stage + doctrine + gate state |
| Granularity | session-global | per-turn varies with state |
| Trigger format | `<system-reminder>` block | `<system-reminder>` block (same) |
| Hot path | mode reminder | full project state (cached) |
| Boundary | "off when X" rule | stage transitions, gate locks |
| State source | flag in settings.json | project manifest + baselines on disk |

Pattern is identical; payload is harness-specific.

## Why this beats skill `.md` files

| problem with skill `.md` | hook injection fix |
|---|---|
| Read once at skill invocation, forgotten 20 turns later | Re-emitted every turn |
| Survives until context compression, then dropped | Hook fires fresh after compression |
| Doctrine + process tangled together | Cleanly separated by section |
| Static — same .md regardless of project state | Dynamic — gate status, focused subsystem |
| User has to know to invoke the skill | Always active when project loaded |
| No enforcement — LLM might ignore | Combined with `PreToolUse` hooks for hard blocks |
| Rules apply globally even when irrelevant | Per-tool injection delivers rules **at the call site** |

## Post-migration skill role — procedural only

After the injection layer absorbs all knowledge, skills become **trivial step-lists**. No doctrine. No rules. No "why." Just an ordered pipeline the agent follows.

Example of what a post-migration skill looks like:

```markdown
---
name: designer
description: Pick parts for every subsystem with no chosen_part
---

# /designer pipeline

1. Verify gate `spec_to_design` PASS (else block).
2. For each subsystem where `chosen_part` is null:
   a. Call `parts-finder` agent → candidate MPN list.
   b. For each candidate: call `analyze_candidate`.
   c. Call `subsystem_choose_part` on the winner.
3. Run gate `design_to_math`.
4. Hand off to `/designer-math` when ready.

(All rules, doctrine, and tool-specific behavior are injected by the harness — see `docs/architecture/modules/knowledge-injection.md`.)
```

That's the full skill. Maybe 10 lines. No knowledge — only ordering.

The split:
- **Skills** = "what steps, in what order."
- **Harness** = "what each step must obey, given current state."

Skills become user-discoverable menus + pipeline scripts. Knowledge is the harness's job.

## Related

- `../../investigations/gradual-implementation-plan.md` — hook table (cross-cutting) lists every hook event.
- Sibling module: `library/doctrine/<stage>.yaml` (new sub-folder, drives what gets injected per stage).
- Sibling module: `library/gates/` — gate definitions feed the gate-status section.
- Pattern source: caveman plugin's `caveman-activate.js` + `caveman-mode-tracker.js` (third-party, ~/.claude/hooks/).

## Status

`planned` — formalizes the pattern already partially in use by the existing SessionStart hook. Phase 3-4 of gradual-implementation-plan adds the gate-status injection; Phases 1, 5 add provenance + EEResult sections. Owner: TBD. Last updated: 2026-05-24.
