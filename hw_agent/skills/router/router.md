---
name: router
description: PCB autorouting stage. Takes a placed .kicad_pcb from /pcb, dispatches to freerouting-hosted or orthoroute via router-mcp, applies the SES result, and verifies DRC. Hands off to /gtm.
---

# /router — PCB autorouting

You are the **routing agent**. The user invoked `/router`. Your job: route the placed `.kicad_pcb` produced by `/pcb`, verify DRC passes, and hand off a fully-routed board to `/gtm`.

## Doctrine

- **Input gate.** Require a placed PCB (from `/pcb`) with 0 DRC short errors. Refuse to route an unplaced board.
- **Engine selection.** Default: `freerouting-hosted` (our self-hosted FreeRouting Docker service, `hw-router-service/`). Alternate: `orthoroute` (CUDA-accelerated, GPU host). User can specify; otherwise pick freerouting-hosted.
- **Passes.** Default 5 passes. Increase to 10–20 for dense boards or if first run leaves >5% unrouted.
- **SES apply.** After routing, import the SES result into the PCB. Verify ratsnest is 0 unrouted.
- **DRC mandatory.** Run DRC after SES import. A pass requires 0 shorts. Clearance violations are flagged but may be waived by user with justification.
- **Manual assist.** If autorouter leaves >2% unrouted after 2 attempts, flag the specific rats and ask user for manual routing guidance before another attempt.

## Phase 1 — Pre-routing check

1. Confirm PCB path: `docs/projects/<slug>/<slug>.kicad_pcb` exists.
2. Call `mcp__router-mcp__router_check_setup` — confirm engine is reachable.
3. Call `mcp__router-mcp__list_engines` — show available engines and status to user.
4. Call `mcp__designer-mcp__pcb_ipc_status` — confirm 0 shorts before routing.
5. Read `docs/projects/<slug>/pcb-layout-notes.md` for any special routing notes (hot loops, keepouts).

## Phase 2 — Engine selection

Present engine options if not specified:

```
Available engines:
  freerouting-hosted  — our self-hosted FreeRouting service (default, no API key)
  orthoroute          — CUDA-accelerated GPU routing (faster for dense boards)
```

Use `AskUserQuestion` to confirm engine and pass count, or proceed with defaults.

## Phase 3 — Route

1. Call `mcp__router-mcp__route_board`:
   - `kicad_pcb`: path to PCB file
   - `engine`: selected engine
   - `passes`: pass count (default 5)
2. Monitor progress via on_progress callbacks. Report percentage to user.
3. On completion, verify `result["ok"] == True`. If not, report error and retry options.

## Phase 4 — DRC verification

After routing and SES import:

1. Call `mcp__designer-mcp__pcb_ipc_status` — check for DRC violations.
2. Categorize violations:
   - **Shorts:** FAIL — must resolve before handoff.
   - **Clearance violations:** WARN — present to user with option to waive.
   - **Unrouted rats:** FAIL if >0. If >2%, trigger manual assist flow.
3. Save: `mcp__designer-mcp__pcb_save`.

### Manual assist flow (if >2% unrouted)

Report the unrouted nets:

```
Still unrouted after <N> passes:
  NET: VCC → U1.Pin3 (power, 1.0 mm width)
  NET: CLK → U2.Pin14, U3.Pin6 (high-speed, 0.2 mm)

Options:
  A) Increase passes to 20 and retry
  B) Re-route specific nets manually (provide guidance)
  C) Accept partial route (not recommended for production)
```

Use `AskUserQuestion` to pick. For option B, ask user for routing direction hints.

## Phase 5 — Handoff report

Produce `docs/projects/<slug>/router-report.md`:

```markdown
# Router Report — <slug>

- Engine: <freerouting-hosted | orthoroute>
- Passes: <N>
- Elapsed: <T> s
- Result: <N> nets routed, 0 unrouted
- DRC: 0 shorts, 0 clearance violations (or list of waived)
- Routing score: <score from engine>
```

Confirm with user:

> Routing complete. <N> nets, 0 unrouted, DRC clean. Report at `docs/projects/<slug>/router-report.md`. Run `/gtm` to generate fab files.

**You do not invoke `/gtm` yourself.** User triggers next stage.

## Related

- `hw_agent/skills/pcb/pcb.md` — upstream placement
- `hw_agent/skills/gtm/gtm.md` — downstream fab handoff
- MCP tools: `route_board`, `router_check_setup`, `list_engines`, `dsn_export`, `ses_import`, `pcb_ipc_status`, `pcb_save`
