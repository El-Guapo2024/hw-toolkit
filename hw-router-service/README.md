# hw-router-service — ACTIVE (MVP 1)

> **Reactivated 2026-05-06.** We previously deprecated this in favor
> of api.freerouting.app, but that service requires per-user API keys
> and has been unreliable. We host FreeRouting ourselves now — same
> Docker compose, same wire format, fully under our control.
>
> Pairs with `hw-router-mcp` (in `eld-hw-agent`). The MCP client's
> `route_freerouting_hosted` engine adapter POSTs DSN to this service,
> polls for completion, gets SES back.

# hw-router-service

Hosted FreeRouting microservice. POST DSN, get SES. Stateless, runs as
a single container, deploys anywhere.

## Why a separate package

- **No `eld-hw-agent` dependency** — service ships with FastAPI +
  uvicorn + the JVM + the FreeRouting JAR. ~200 LoC of Python.
- **Independent deploy lifecycle** — version, ship, scale this service
  on its own without touching the agent package.
- **Privacy story** — users with proprietary boards run this image
  themselves; casual users hit our hosted instance.

## API

| Verb | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{ok, java, freerouting_jar}` |
| POST | `/jobs` | `{dsn, passes?, threads?}` | `{job_id, state}` |
| GET | `/jobs/{job_id}` | — | `{state, elapsed_s, log_tail, ses?}` |
| POST | `/jobs/{job_id}/cancel` | — | `{ok, state}` |

DSN and SES are plain Specctra text — no base64 wrapping needed.

## Quick start (Docker Compose)

```bash
cd hw-router-service
docker compose up --build
```

That's it. Three containers come up: `redis`, `api`, `worker`. The API
is on `http://localhost:8000`. To stop: `docker compose down`. To wipe
queue + persisted job data: `docker compose down -v`.

## Local dev (without Docker)

```bash
cd hw-router-service
pip install -e ".[dev]"

# A local Redis is needed for RQ:
#   brew install redis && brew services start redis
# Or via Docker:
#   docker run --rm -d -p 6379:6379 redis:7-alpine

# JAR + JDK:
#   brew install openjdk@17
#   curl -L -o /usr/local/lib/freerouting.jar \
#     https://github.com/freerouting/freerouting/releases/download/v2.1.0/freerouting-2.1.0-cli.jar

# Terminal 1 — API:
hw-router-service

# Terminal 2 — worker:
rq worker router
```

Then:

```bash
curl -fsS http://localhost:8000/health
# → {"ok": true, "java": "...", "freerouting_jar": "..."}

curl -X POST http://localhost:8000/jobs \
  -H 'Content-Type: application/json' \
  -d "$(jq -nR --rawfile dsn input.dsn '{dsn: $dsn, passes: 5}')"
# → {"job_id": "abc123…", "state": "queued"}

curl -fsS http://localhost:8000/jobs/abc123…
# → {"state": "running", "elapsed_s": 12.4, "log_tail": "…"}
# Eventually: {"state": "succeeded", "ses": "(specctra session …)"}
```

## Docker

```bash
docker build -t hw-router-service:0.1.0 .
docker run --rm -p 8000:8000 hw-router-service:0.1.0
```

The image bakes in OpenJDK 17 + FreeRouting 2.1.0. The version pin is
in the Dockerfile (`ARG FREEROUTING_VERSION`).

## Deploy targets

- **Fly.io / Railway / Render** — drop in the Dockerfile, point at this
  directory, deploy. ~1 min from `docker push` to live URL.
- **Kubernetes** — single deployment + service, no statefulness needed.
  Mount `/tmp/hw-router-jobs` as ephemeral.
- **Plain VPS** — `docker compose up -d` with this directory.

## Auth (deferred)

MVP has no auth — assume firewalled or local. Add an `API_KEY` env var
+ middleware in the next iteration. Don't expose this to the public
internet without it.

## Use from `hw-router-mcp`

The companion MCP client (`hw-router-mcp` in `eld-hw-agent`) supports
remote mode via `--remote=https://your-host:8000`. With that flag:

1. Client does DSN export from `.kicad_pcb` locally
2. Client POSTs DSN to the service
3. Client polls `/jobs/{id}` until done
4. Client receives SES and imports it back into `.kicad_pcb` locally

The service never sees the user's `.kicad_pcb` — only the DSN
representation of the netlist.

## Architecture

```
   ┌──────────┐   POST /jobs                ┌──────────┐
   │  client  │ ─────────────────────────►  │   api    │
   │          │   GET /jobs/{id}            │ (uvicorn)│
   └──────────┘                             └─────┬────┘
                                                  │ enqueue / fetch
                                                  ▼
                                          ┌────────────────┐
                                          │     redis      │
                                          │  (RQ queue)    │
                                          └────────┬───────┘
                                                   │ pop
                                                   ▼
                                          ┌────────────────┐
                                          │     worker     │
                                          │ (rq worker)    │
                                          │   ↓            │
                                          │  java -jar     │
                                          │  freerouting   │
                                          └────────────────┘
                          shared volume: /data/jobs (DSN, SES, log files)
                          mounted into both api and worker
```

- **Queue: RQ** (Apache 2.0 / open source) — minimal task manager,
  Python-native, requires only Redis as a sidecar
- **Persistence**: Redis stores job metadata; the shared volume holds
  DSN/SES/log files (don't pump multi-MB blobs through Redis)
- **Cancel path**: `send_stop_job_command` to the worker + best-effort
  SIGTERM to the JVM via the pid stashed in the job's `meta`

## Limitations

- **No auth** — assume firewalled or local. Add an API key middleware
  before exposing publicly.
- **In-process state for log tailing** — works because both api and
  worker mount the same volume. If you split them across hosts, replace
  with object storage (S3/MinIO).
- **No WebSocket / SSE for progress** — clients poll. Acceptable for
  routing jobs measured in seconds-to-minutes.
- **Single replica per job** — RQ binds a job to the worker that pops
  it. For parallel routes, scale the `worker` service in compose
  (`docker compose up --scale worker=N`).
