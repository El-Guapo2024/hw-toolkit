"""FastAPI service: POST DSN, get SES.

Job dispatch is RQ-backed (Redis sidecar). API process enqueues; a
separate worker container runs `rq worker router` and consumes jobs.

Endpoints:
    GET  /health                     — service ready check
    POST /jobs                       — enqueue a route job
    GET  /jobs/{job_id}              — poll status (SES inline when done)
    POST /jobs/{job_id}/cancel       — cancel a queued/running job
"""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .routing import (
    fetch_job,
    find_freerouting_jar,
    find_java,
    get_queue,
    job_paths,
    request_cancel,
    run_freerouting_job,
)


app = FastAPI(
    title="hw-router-service",
    version="0.1.0",
    description="FreeRouting as a service — POST DSN, get SES.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class JobRequest(BaseModel):
    dsn: str = Field(..., description="Specctra DSN text")
    passes: int = Field(5, ge=1, le=200)
    threads: int = Field(0, ge=0, le=64, description="0 = freerouting default")


class JobStatus(BaseModel):
    job_id: str
    state: str
    elapsed_s: float = 0.0
    error: Optional[str] = None
    log_tail: Optional[str] = None
    ses: Optional[str] = None


@app.get("/health")
def health() -> dict:
    out: dict = {"ok": True}
    try:
        out["java"] = find_java()
    except FileNotFoundError as e:
        out["ok"] = False
        out["java_error"] = str(e)
    try:
        out["freerouting_jar"] = str(find_freerouting_jar())
    except FileNotFoundError as e:
        out["ok"] = False
        out["jar_error"] = str(e)
    try:
        q = get_queue()
        out["queue"] = {"name": q.name, "queued": q.count}
    except Exception as e:
        out["ok"] = False
        out["queue_error"] = str(e)
    return out


@app.post("/jobs", response_model=dict)
def submit_job(req: JobRequest) -> dict:
    if not req.dsn.strip():
        raise HTTPException(status_code=400, detail="empty dsn")

    queue = get_queue()
    # Pre-allocate the job_id so the worker writes outputs to a path the
    # API can read by id later. RQ generates the id and we pass it back
    # via job_id arg.
    import uuid

    job_id = uuid.uuid4().hex[:12]
    queue.enqueue(
        run_freerouting_job,
        req.dsn, req.passes, req.threads, job_id,
        job_id=job_id,
    )
    return {"job_id": job_id, "state": "queued"}


@app.get("/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str, log_tail: int = 50, include_ses: bool = True) -> JobStatus:
    job = fetch_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job_id: {job_id}")

    rq_status = job.get_status() or "queued"
    state = _normalize_state(rq_status)
    elapsed = _elapsed(job)

    paths = job_paths(job_id)
    log = None
    if log_tail > 0 and paths["log"].exists():
        try:
            lines = paths["log"].read_text(errors="replace").splitlines()
            log = "\n".join(lines[-log_tail:])
        except Exception:
            log = None

    ses = None
    error = None

    if state == "succeeded":
        result = job.result if isinstance(job.result, dict) else {}
        if include_ses:
            ses = result.get("ses") or (paths["ses"].read_text() if paths["ses"].exists() else None)
    elif state == "failed":
        result = job.result if isinstance(job.result, dict) else {}
        error = (result or {}).get("error") or (job.exc_info or "")[:500] or "unknown error"

    return JobStatus(
        job_id=job_id,
        state=state,
        elapsed_s=elapsed,
        error=error,
        log_tail=log,
        ses=ses,
    )


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    ok, state = request_cancel(job_id)
    return {"ok": ok, "job_id": job_id, "state": state}


def _normalize_state(rq_status: str) -> str:
    return {
        "queued": "queued",
        "started": "running",
        "deferred": "queued",
        "scheduled": "queued",
        "finished": "succeeded",
        "failed": "failed",
        "stopped": "cancelled",
        "canceled": "cancelled",
        "cancelled": "cancelled",
    }.get(rq_status, rq_status)


def _elapsed(job) -> float:
    started = getattr(job, "started_at", None)
    ended = getattr(job, "ended_at", None) or getattr(job, "enqueued_at", None)
    if not started:
        return 0.0
    import datetime as dt
    end = ended or dt.datetime.now(dt.timezone.utc)
    try:
        return round((end - started).total_seconds(), 2)
    except Exception:
        return 0.0
