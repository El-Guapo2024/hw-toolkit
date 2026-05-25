# Module Design — Agents

## Purpose

Two main self-injecting agent harnesses, communicating only via typed artifacts. No orchestrator agent. Math/router/fab are deterministic functions called from inside the two agents, not agents themselves.

## The two agents

| agent | scope | design doc |
|---|---|---|
| **researcher** | intake Q&A → spec → interfaces → parts pick → math verify. Produces typed handoff bundle. | `researcher.md` |
| **pcb-designer** | consumes researcher bundle → schematic → place → route → DRC → fab handoff | `pcb-designer.md` |
| **parts-finder** (sub-agent, existing `.claude/agents/parts-finder.md`) | parallel MPN candidate ranking, invoked by researcher | (already exists) |

Each main agent gets its own:
- `.md` definition at `.claude/agents/<name>.md` (model, description, tools, frontmatter)
- doctrine bundle at `library/doctrine/<name>/*.yaml` (sub-phase swappable)
- tool whitelist (PreToolUse hook rejects out-of-scope calls)
- exit gate (`research_to_pcb` or `ready_to_fab`)
- self-injection (SessionStart + UserPromptSubmit + PreToolUse hooks emit `<system-reminder>` blocks)

Handoff is **artifact-only**. No direct calls between agents. Same as CI/CD jobs.

## Decision rule — when to split off a new agent

Resist the urge. Default: keep work inside an existing agent or as a deterministic function. **Split into a new agent only when AT LEAST 2 of these hold:**

1. Distinct mode or personality (intake Q&A vs research vs synthesis).
2. >20 turns of LLM work per invocation (context-window pressure on shared session).
3. Genuinely parallelizable across instances (per-subsystem fan-out).
4. Tool whitelist diverges sharply from neighbors.
5. Needs a smaller/cheaper model (haiku for trivial; sonnet/opus for hard).

Without 2+ → keep it as a function inside an existing agent.

## What is NOT an agent (and why)

| not-an-agent | what it is instead |
|---|---|
| math / verification | `ee.facade.run_check` — deterministic Python function called by researcher |
| routing | `mcp__router-mcp__route_board` — deterministic tool call by pcb-designer |
| fab export | scripts called by pcb-designer (gerbers/BOM/CPL) |
| bring-up data ingest | manual yaml + small CLI helper, run by user |
| orchestration | user invokes `/researcher` then `/pcb-designer` manually. Optional `hw-agent run` CLI can chain them. |

## Anti-pattern: distributed coordination

Only the orchestration layer (CLI or user) drives stage transitions. Per-agent code never tries to invoke the next agent. Single dispatch point, always. Same rule as GitHub Actions jobs: jobs don't manage the workflow; the engine does.

## Status

`planned` — depends on typed core, injection skeleton, doctrine library. Owner: TBD. Last updated: 2026-05-24.

## Related

- `researcher.md` — stage 1 plan
- `pcb-designer.md` — stage 2 plan (self-contained for fresh-Claude implementation)
- `knowledge-injection.md` — how self-injection works
- `../README.md` — entry point + core convictions
- `../../investigations/typed-core-spec.md` — pydantic contracts for artifact handoff
