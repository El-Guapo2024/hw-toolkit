# Architecture — hw-toolkit

**This is the entry point.** Read this first. Every module has its own design doc under `modules/`.

---

## Core convictions (the 4 rules everything else follows)

These are the load-bearing design principles. Argue with these first if anything else feels off.

1. **Skills are slim.** A skill is a procedural step-list, nothing more. No doctrine, no rules, no "why." All knowledge moves to the harness injection layer.
2. **Tools give feedback.** Every meaningful action is a tool call that returns structured feedback (PASS/FAIL/EEResult/error). The harness reads the feedback and routes the next decision.
3. **Feedback drives design.** The next agent move is determined by the last tool's feedback, not by the agent's plan. Tool says FAIL → eliminate candidate. Tool says stock=0 → surface to user. Agents react to ground truth, they don't predict it.
4. **Re-inject every turn to prevent drift.** Doctrine, project state, gate status, focused subsystem — all re-emitted in a `<system-reminder>` block each turn via hooks. Borrowed from the caveman pattern. The LLM can drift; the hook can't.

These are non-negotiable. Every other convention in this file derives from them.

---

## How to read this repo

1. Start here — module map + dependency graph + conventions.
2. Jump to a module's design (`modules/<name>.md`) for purpose, contract, internals, open questions.
3. Two specific agent plans: `modules/researcher.md` + `modules/pcb-designer.md` — each is self-contained for a fresh implementer.
4. Supporting spec: `../investigations/typed-core-spec.md` — the pydantic contracts every artifact must obey + ownership / mutation rules.
5. Only then open the source.

If a module has no design doc, that's a bug — write one.

---

## Module map

```
hw-toolkit/
├── hw_agent/
│   ├── core/                         → modules/core.md           (mature, pydantic models)
│   ├── ee/                           → modules/ee.md             (planned, thin policy + adapters)
│   ├── library/                      → modules/library.md        (planned, templates/doctrine/gates)
│   ├── agents/                       → modules/agents.md         (strategy + decision rule)
│   ├── scripts/hooks/                → modules/knowledge-injection.md
│   ├── skills/                       → TBD (will slim to step-lists only)
│   ├── artifacts/                    → TBD
│   ├── domain/                       → TBD (deprecating; absorbed into ee/ + library/)
│   └── preview.py                    → flagged for removal in core.md
│
├── mcp_server/
│   ├── designer/                     → TBD (will thin as ee/ lands)
│   ├── live_edit/                    → TBD
│   └── router/                       → TBD
│
├── hw-router-service/                → TBD (external Freerouting/Orthoroute service)
│
├── docs/
│   ├── architecture/
│   │   ├── README.md                 (this file)
│   │   ├── MODULE_DESIGN_TEMPLATE.md
│   │   └── modules/
│   │       ├── core.md
│   │       ├── ee.md
│   │       ├── library.md
│   │       ├── agents.md
│   │       ├── researcher.md         (stage-1 agent plan)
│   │       ├── pcb-designer.md       (stage-2 agent plan)
│   │       └── knowledge-injection.md
│   ├── investigations/
│   │   ├── typed-core-spec.md        (pydantic contract — referenced by every module)
│   │   └── methodology-rationale.md  (why this design vs alternatives)
│   └── projects/                     (per-project truth artifacts)
│
└── .claude/                          (session hooks, agents, settings)
```

---

## Dependency graph

```
                ┌─────────────────────────────┐
                │  Agents (researcher,        │  (talk to user, drive flow)
                │  pcb-designer)              │
                └────┬────────────────────────┘
                     │
        ┌────────────┼────────────┬─────────────────────┐
        ▼            ▼            ▼                     ▼
   ┌─────────┐ ┌──────────┐ ┌──────────┐    ┌──────────────────┐
   │ MCP     │ │ MCP      │ │ MCP      │    │ scripts/hooks    │
   │ designer│ │ live-edit│ │ router   │    │ (injection)      │
   └────┬────┘ └────┬─────┘ └────┬─────┘    └────┬─────────────┘
        │           │            │               │
        └───────────┴────┬───────┘               │
                         ▼                       │
                  ┌──────────────┐               │
                  │  hw_agent    │◄──────────────┘
                  │  core +      │
                  │  ee +        │  (pure, host-agnostic)
                  │  library     │
                  └──────┬───────┘
                         ▼
                ┌──────────────────────┐
                │ External libs        │  lcapy, scikit-rf, ngspice,
                │                      │  KiCad CLI, pydantic
                └──────────────────────┘
```

Dependencies point downward. Nothing in `hw_agent/ee/` or `hw_agent/core/` imports from `mcp_server/`, `skills/`, or `scripts/`. CI grep enforces.

---

## Conventions (project-wide rules)

### Design-first
Every module ships with `modules/<name>.md` written before or alongside the code. Touching a module = updating its design doc in the same PR. No design doc = no merge.

### Truth vs view
Every file is **truth** (canonical, hand-authored or schema-locked) or **view** (derived, regenerable). Views never hand-edited; they get an `# AUTO-GENERATED` header.

### One fact, one home
Each fact has exactly one source. Duplication forbidden; references required.

### Provenance on every value
Every actual/extracted/measured value carries a typed `ProvenanceTag`. Source tiers: `measured` > `datasheet` > `dk`/`jlc`/`mouser` > `user` > `derived` > `ai_estimated`. Defined in `../investigations/typed-core-spec.md`.

### Hexagonal core
`hw_agent/ee/`, `hw_agent/core/`, `hw_agent/library/` import nothing from `hw_agent.scripts.*`, `mcp_server.*`, or `hw_agent.skills.*`. CI grep enforces.

### Gates and baselines
Stage transitions go through gate checks (`hw_agent/library/gates/`). Locked phases are git-tagged baselines + a yaml in `docs/projects/<p>/baselines/`.

### Hooks enforce; LLM remembers nothing
Doctrine, gates, lints — all enforced by `.claude/` hooks. Pattern borrowed from caveman mode. See `modules/knowledge-injection.md`.

### Caveman in chat, normal in files
Chat responses are caveman-compressed when the mode is active. Files (code, docs, commits) always normal prose.

---

## Status of each module

| module | code? | design doc | status |
|---|---|---|---|
| `hw_agent/core/` | yes | `modules/core.md` | mature; provenance pending |
| `hw_agent/ee/` | no | `modules/ee.md` | planned, thin policy + adapters |
| `hw_agent/library/` | no | `modules/library.md` | planned, templates + doctrine + gates |
| `hw_agent/scripts/hooks/` | partial | `modules/knowledge-injection.md` | injection skeleton extends current SessionStart hook |
| `hw_agent/agents/` + `.claude/agents/` | partial (2 sub-agents exist) | `modules/agents.md` | strategy + decision rule |
| `researcher` agent | planned | `modules/researcher.md` | stage 1 — intake → spec → parts → math |
| `pcb-designer` agent | planned | `modules/pcb-designer.md` | stage 2 — sch → place → route → fab. Self-contained for fresh-Claude implementation. |
| **other existing modules** — `skills/`, `artifacts/`, `domain/` (deprecating), `mcp_server/{designer,live_edit,router}/`, `hw-router-service/` | yes | TBD | mature; design doc written next time module is touched |

**Rule:** TBD modules get a design doc the next time they're touched.

---

## How to add a new module

1. Copy `MODULE_DESIGN_TEMPLATE.md` to `modules/<your-name>.md`.
2. Fill in: purpose, public contract, internal layout, dependencies, open questions, status.
3. Add row to the status table above.
4. Update the module map ASCII tree.
5. Open PR with the design doc and the first commit of code together. Never code without design.

---

## Related

- `../investigations/typed-core-spec.md` — pydantic contracts every artifact obeys + ownership/mutation rules for agents
