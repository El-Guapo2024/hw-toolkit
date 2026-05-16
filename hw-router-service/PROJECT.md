# hw-router-service

> Self-hosted FreeRouting microservice. POST a Specctra DSN, get back a
> Specctra SES (routing session). Stateless API in front of an RQ-backed
> FreeRouting JAR worker. Deploys with one command via Docker Compose.

## Status — ✅ verified working (2026-05-06)

End-to-end smoke test passes. A real Specctra DSN routes via FreeRouting
v2.2.2 in ~17s on commodity CPU and returns valid SES output.

```
$ python3 scripts/smoke_test.py
smoke test: http://localhost:8002
  /health … ok (java=/opt/java/openjdk/bin/java…)
  fetch fixture DSN (Issue026-J2_reference.dsn) … ok (7925 chars)
  POST /jobs (passes=3) … job_id=d8a90671faa9
  state=succeeded elapsed_s=17.09
  GET full result … ok (8387 chars)
✅ all checks passed
```

## What ships

| Layer | Detail |
|---|---|
| Container image | Multi-stage Docker: Eclipse Temurin 25 JRE + python:3.12-slim |
| FreeRouting | v2.2.2 (pinned via `FREEROUTING_VERSION` ARG) |
| Job queue | RQ on Redis 7 |
| API | FastAPI, single uvicorn worker, port 8000 inside container |
| Default host port | 8002 (override via `HW_ROUTER_PORT` env) |
| Image size | ~1 GB compressed (CUDA-free, JRE-25 baked in) |

## Architecture

```
   client ──POST /jobs──► api  ──RQ enqueue──► redis ──RQ pop──► worker
   (DSN text)             │                                       │
                          │                                       ▼
   client ──GET /jobs/X──►│                              java -jar freerouting.jar
   (SES text)             │                                       │
                          ◄────── /data/jobs (shared volume) ─────┘
```

The api process and worker process share the `hw-router-service:0.1.0`
image. The api enqueues; a separate `gpu-worker` container (well, a
plain `worker` here — no GPU needed) consumes the queue, runs
FreeRouting, and writes DSN/SES files to a shared volume that the api
reads when serving `/jobs/{id}`.

## API

| Verb | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{ok, java, freerouting_jar, queue}` |
| POST | `/jobs` | `{dsn, passes?, threads?}` | `{job_id, state}` |
| GET | `/jobs/{id}` | `?include_ses=true` `?log_tail=N` | `{state, elapsed_s, log_tail?, ses?, error?}` |
| POST | `/jobs/{id}/cancel` | — | `{ok, state}` |

States: `queued` → `running` → `succeeded` / `failed` / `cancelled`.

## How to run

```bash
cd hw-router-service
docker compose up --build      # ~3-4 min cached, ~5-10 min cold
curl http://localhost:8002/health        # verify
python3 scripts/smoke_test.py            # end-to-end check
```

To stop: `docker compose down`. To wipe queue + jobs volume:
`docker compose down -v`.

## How the MCP client uses it

The `hw_agent.router_engines.route_freerouting_hosted` adapter:
1. Exports DSN locally from `.kicad_pcb` via pcbnew IPC (kipy)
2. POSTs DSN to this service
3. Polls `/jobs/{id}` until `succeeded`
4. Fetches SES, applies it back to `.kicad_pcb` via pcbnew IPC

Default URL: `http://localhost:8002`. Override via
`FREEROUTING_API_URL` env when targeting a remote deployment.

## Files

```
hw-router-service/
├── PROJECT.md                       ← you are here
├── README.md                        ← user-facing setup + ops
├── Dockerfile                       ← multi-stage Temurin 25 + Python 3.12
├── docker-compose.yml               ← redis + api + worker
├── pyproject.toml                   ← deps: fastapi, uvicorn, rq, redis
├── scripts/
│   └── smoke_test.py                ← end-to-end /health + /jobs verification
└── src/hw_router_service/
    ├── __init__.py
    ├── __main__.py                  ← uvicorn entry: `hw-router-service`
    ├── server.py                    ← FastAPI app, 4 endpoints
    └── routing.py                   ← RQ job, FreeRouting CLI invocation
```

## Decisions that bit us (recorded so we don't repeat)

| Surface | Trap | Fix |
|---|---|---|
| Java version | FreeRouting v2.2.2 needs Java 25 (class version 69). Debian's `default-jre-headless` is Java 21 | Multi-stage Dockerfile: copy Eclipse Temurin 25 JRE from `eclipse-temurin:25-jre-noble` |
| JAR URL | v2.1.x asset was `freerouting-X.Y.Z-cli.jar`; v2.2.x dropped the `-cli` suffix | URL points at `freerouting-${VERSION}.jar` |
| Host port | Port 8000 commonly taken (e.g. user's `freightflowai_backend`) | Default to 8002, configurable via `HW_ROUTER_PORT` |
| apt package availability | `openjdk-17-jre-headless` no longer in Debian apt for python:3.12-slim base | Don't rely on distro Java packages — pull JRE from upstream Temurin image |
| Worker healthcheck | Worker inherited image's API healthcheck (`curl /health`); always failed | Per-service healthcheck in compose: `pgrep -f "rq worker"` |

## TODO

### Must-have to ship MVP 1 publicly
- [ ] **Live MCP test through an orchestrator.** I've smoke-tested the HTTP service directly; the `router-mcp` → service round-trip via stdio MCP hasn't been exercised. Owner: user (needs KiCad open + an orchestrator with `router-mcp` configured).
- [ ] **Verify behavior on a modern KiCad-9-exported DSN.** Smoke test uses an older fixture (KiCad 5.1.5) and FreeRouting logged a "exported from old KiCad" warning. Should still produce valid output but worth confirming on a current board.

### Nice-to-have soon
- [ ] **Publish the image to a registry** (GHCR or Docker Hub) so users don't have to build locally. Tag as `hw-router-service:0.1.0` + `:latest`.
- [ ] **Pin FreeRouting JAR by SHA256** in the Dockerfile, not just version, so a malicious release tag swap doesn't go unnoticed.
- [ ] **Add an E2E test in CI**: spin up the compose stack, run `scripts/smoke_test.py`, fail the build on regression.

### Production hardening (later)
- [ ] **API key auth.** Optional `Authorization: Bearer ...` middleware. Off by default for local; required when behind a public DNS.
- [ ] **Rate limiting.** Per-key job count per minute.
- [ ] **Persistent job storage.** Currently in-memory + on-disk volume; restart loses queued metadata. Switch to a small Postgres or use Redis persistence.
- [ ] **Multi-replica scaling.** Each `worker` container handles one job at a time. `docker compose up --scale worker=N` works for a single host; for multi-host we'd need a real orchestrator.
- [ ] **Streaming progress.** Clients poll. Could add SSE or WebSocket for live route progress (per-pass updates).
- [ ] **Cancel race.** `request_cancel` SIGTERMs the JVM via the pid stashed in job meta — works but has a small TOCTOU window. Acceptable.

### Code-quality
- [ ] **Type-check** `routing.py` and `server.py` with mypy strict.
- [ ] **Test the cancel path** with a long-running route. Currently smoke-tested only on the happy path.
- [ ] **Better log structure**: emit JSON logs so they parse cleanly in any log aggregator.

## Acceptance for "MVP 1 done"

- ✅ `docker compose up --build` produces a working stack on a fresh machine
- ✅ `scripts/smoke_test.py` passes end-to-end
- ⬜ A real `.kicad_pcb` round-trips via the MCP path (orchestrator → router-mcp → service → routed PCB)

Once the third item is verified, MVP 1 is genuinely shipped.
