---
name: router
description: Auto-route a placed .kicad_pcb via the router-mcp. Wraps a self-hosted FreeRouting Docker service. Use after placement is done, before fab export.
---

# Router skill — how to auto-route a PCB

This is the agent-facing reference for the routing pipeline. The
implementation lives in `hw_agent/router_mcp.py` (MCP server) and
`hw-router-service/` (Docker stack). This doc tells you **how to use
it** — not how it's built (see `hw-router-service/PROJECT.md` for that).

## When to invoke

You're at the end of Phase 3 (PCB live edit). The board has:
- All footprints placed
- Ratsnest (airwires) showing nets-needing-routing
- Few or zero `(segment …)` tracks already drawn

If the board is *already* routed, **don't blindly re-route** — re-routing
shuffles trace geometry in non-obvious ways. Confirm with the user first.

## Prerequisites

Before calling `route_board`, the **router service must be running**:

```bash
cd hw-router-service && docker compose up -d
```

Verify with:
```python
router_check_setup()
# → engines[0].reachable should be True for "freerouting-hosted"
# If reachable=False, surface the actionable hint to the user.
```

The check is cheap (~50ms HTTP probe). Run it on first use of a session
or whenever a previous route call failed at the `preflight` stage.

## The happy path

```python
result = route_board(
    kicad_pcb="/path/to/placed-board.kicad_pcb",
    engine="freerouting-hosted",   # default; can omit
    passes=5,                       # 5 is fine for most boards
    threads=4,                      # parallelism hint, ignored by some engines
    timeout_s=600,                  # 10 min default
)
```

While the route runs, the tool streams **real progress** via
`ctx.report_progress`:

```
freerouting pass 1/5: score=867.05, 10 unrouted    [38%]
freerouting pass 2/5: score=855.05, 11 unrouted    [51%]
freerouting pass 4/5: score=952.39, 4 unrouted     [77%]
freerouting done: score=952.42, 4 unrouted         [95%]
```

Score = lower is better (FreeRouting's penalty function: shorter wires +
fewer vias + fewer crossings). `unrouted` = nets that FreeRouting
couldn't connect; if non-zero, the result PCB has airwires remaining.

## Return shape

Success:
```python
{
    "ok": True,
    "engine": "freerouting-hosted",
    "elapsed_s": 11.06,
    "remote_job_id": "abc123",
    "ses_path": "/tmp/.../board.ses",
    "tracks_added": 178,            # via pcb_writer SES import
}
```

Failure:
```python
{
    "ok": False,
    "engine": "freerouting-hosted",
    "stage": "preflight" | "submit" | "poll" | "remote" | "timeout" | "decode",
    "error": "service at http://localhost:8002 not reachable (...)",
    "elapsed_s": 0.22,
    "remote_job_id": "abc123",      # if we got far enough to submit
    "log_tail": "...",              # if available — usually most useful for "remote" failures
}
```

## After the route

Always run DRC. Routing without DRC is just track placement — you need
the gate to catch shorts and clearance violations:

```python
# Use the existing pcb_check consumer or kicad-cli directly
pcb_check(kicad_pcb="/path/to/board.kicad_pcb")
```

If DRC passes → hand off to `/fab` or call `pcb_export_fabrication`.

## Recovery patterns

### `stage="preflight"` — service is down
**The actionable message tells the user exactly what to run.** Surface
it verbatim. Don't try to start docker yourself; the user controls
their machine.

### `stage="timeout"` — route didn't converge
- Bump `passes` to 10-15 (more iterations to optimize)
- Bump `timeout_s` to 1800 (30 min) for dense boards
- If still timing out, the design itself may be undroutable — review
  the placement; tight clearances can make a board impossible to route

### `stage="remote"` — FreeRouting itself failed
Check `result["log_tail"]`. Common patterns:
- `"net X has no source"` — DSN export was incomplete; re-run
  `dsn_export` directly to inspect the file
- `"unable to fanout"` — placement is too dense; review the BGAs / fine-pitch parts
- `"timeout reached"` — same as `stage="timeout"` but reported by
  FreeRouting itself rather than the wrapper

### Some nets remain unrouted
This is **normal for dense boards**. The route still partially
succeeds; the unrouted nets become airwires the user can route
manually or address by adjusting placement.

If `unrouted > 5%` of total nets, recommend the user re-place rather
than re-route.

## Engine selection

Two engines exist; default is `freerouting-hosted`. Don't override
unless you have a reason:

| Engine | When to use |
|---|---|
| `freerouting-hosted` (default) | All boards. CPU. Free. Solid quality. ~10s-10min depending on size. |
| `orthoroute` | Backplane-class designs (>2k nets, 8+ layers). GPU. **Not yet deployed** — service returns "not implemented" until Phase 4. |

## Cancellation

If the user hits Stop or the MCP turn is otherwise cancelled, the
adapter automatically POSTs `/jobs/{id}/cancel` to the service. The
JVM stops. No orphaned routing processes.

## What NOT to do

- ❌ **Don't shell out to `freerouting` directly** — `route_board`
  already wraps the full pipeline (DSN export → service → SES import).
  Direct `java -jar` calls bypass progress streaming and cancel.
- ❌ **Don't skip the preflight check on first use** — a failed
  `route_board` after 30s of DSN export is much worse UX than a 50ms
  probe error.
- ❌ **Don't run on an unplaced board.** FreeRouting needs footprints
  with positions; without them the DSN export emits empty `(component …)`
  blocks and the route is meaningless.
- ❌ **Don't re-route a clean board "for fun".** Once you have a routed
  + DRC-clean board, leave it alone. Re-routing can shuffle traces in
  non-obvious ways; if you have to re-place, then re-route — but not
  speculatively.

## Tools available in router-mcp (full list)

Of the 5 tools in `router-mcp`, you'll mostly use `route_board`:

| Tool | When to use |
|---|---|
| `list_engines` | First call in a session or after a config change. Shows what's available, what's the default URL, what's missing |
| `router_check_setup` | Like `list_engines` but probes URLs. Use when `route_board` returns `stage="preflight"` |
| `dsn_export` | Manual DSN export for inspection or feeding to a non-FreeRouting tool. Rarely needed — `route_board` does this internally |
| `ses_import` | Apply a SES from disk (e.g., one you got from a different router) to a `.kicad_pcb`. Rarely needed |
| `route_board` | The headline. Full pipeline, progress streaming, cancel propagation |

## See also

- `.claude/commands/route-pcb.md` — slash-command-style invocation
  for users who want to type `/route-pcb path/to/board.kicad_pcb`
- `hw-router-service/PROJECT.md` — the engineering brief for the
  Docker service
- `hw-router-service/scripts/smoke_test.py` — end-to-end check; run
  it if you suspect the service has regressed
- `docs/PLAN_router.md` — long-term roadmap (MVP 2 = OrthoRoute,
  MVP 3 = next planned MCP)
