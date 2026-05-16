"""FreeRouting job: runs in an RQ worker process.

Architecture:
    POST /jobs        ─►  enqueues `run_freerouting_job` to RQ
    GET  /jobs/{id}   ─►  RQ Job.fetch() + read SES/log from shared volume
    POST /jobs/{id}/cancel  ─►  RQ send_stop_job_command + SIGTERM the JVM

The job function lives here so RQ can pickle a reference. The shared
volume (default /data/jobs) is mounted into both the API container and
the worker container in docker-compose.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from redis import Redis
from rq import Queue
from rq.job import Job


# ─── JVM + JAR detection ────────────────────────────────────────────────────

_FREEROUTING_JAR_CANDIDATES = [
    Path("/app/freerouting.jar"),
    Path("/usr/local/lib/freerouting.jar"),
    Path("/opt/freerouting/freerouting.jar"),
]

_JAVA_CANDIDATES = [
    "/usr/local/openjdk/bin/java",
    "/usr/local/opt/openjdk/bin/java",
    "/opt/homebrew/opt/openjdk/bin/java",
    "/usr/bin/java",
]


def find_java() -> str:
    env = os.environ.get("JAVA_BIN")
    if env and Path(env).exists():
        return env
    for c in _JAVA_CANDIDATES:
        if Path(c).exists():
            return c
    found = shutil.which("java")
    if found:
        return found
    raise FileNotFoundError("Java runtime not found. Set JAVA_BIN or install OpenJDK.")


def find_freerouting_jar() -> Path:
    env = os.environ.get("FREEROUTING_JAR")
    if env and Path(env).exists():
        return Path(env)
    for c in _FREEROUTING_JAR_CANDIDATES:
        if c.exists():
            return c
    raise FileNotFoundError(
        "freerouting.jar not found. Set FREEROUTING_JAR or drop it at /app/freerouting.jar."
    )


# ─── Shared volume + Redis connection ───────────────────────────────────────

JOB_DATA_DIR = Path(os.environ.get("JOB_DATA_DIR", "/data/jobs"))


def _redis() -> Redis:
    return Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))


def get_queue() -> Queue:
    return Queue("router", connection=_redis(), default_timeout=int(os.environ.get("JOB_TIMEOUT", "1800")))


def fetch_job(job_id: str) -> Job | None:
    try:
        return Job.fetch(job_id, connection=_redis())
    except Exception:
        return None


def job_paths(job_id: str) -> dict[str, Path]:
    work = JOB_DATA_DIR / job_id
    return {
        "work": work,
        "dsn": work / "in.dsn",
        "ses": work / "out.ses",
        "log": work / "freerouting.log",
    }


# ─── The job function (runs in RQ worker) ───────────────────────────────────


def run_freerouting_job(dsn_text: str, pass_limit: int, threads: int, job_id: str) -> dict:
    """Worker-side: write DSN, run FreeRouting, return SES + log text.

    The job_id is passed in so the worker can use the same dirs the API
    polls. RQ's job.id matches what we stash in JOB_DATA_DIR.
    """
    paths = job_paths(job_id)
    paths["work"].mkdir(parents=True, exist_ok=True)
    paths["dsn"].write_text(dsn_text)

    java = find_java()
    jar = find_freerouting_jar()

    cmd = [
        java, "-jar", str(jar),
        "-de", str(paths["dsn"]),
        "-do", str(paths["ses"]),
        "-mp", str(pass_limit),
    ]
    if threads > 0:
        cmd += ["-mt", str(threads)]

    started = time.time()
    log_f = paths["log"].open("w")
    try:
        proc = subprocess.Popen(
            cmd, stdout=log_f, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        # Stash pid so /cancel can SIGTERM the JVM via the job meta.
        try:
            from rq import get_current_job
            current = get_current_job()
            if current is not None:
                current.meta["pid"] = proc.pid
                current.save_meta()
        except Exception:
            pass
        rc = proc.wait()
    finally:
        try:
            log_f.close()
        except Exception:
            pass

    elapsed = round(time.time() - started, 2)

    if rc == 0 and paths["ses"].exists():
        return {
            "state": "succeeded",
            "elapsed_s": elapsed,
            "ses": paths["ses"].read_text(),
            "log_tail": _tail(paths["log"], 50),
        }

    return {
        "state": "failed",
        "elapsed_s": elapsed,
        "error": f"freerouting exit={rc}, ses_exists={paths['ses'].exists()}",
        "log_tail": _tail(paths["log"], 100),
    }


def _tail(path: Path, n: int) -> str:
    if not path.exists():
        return ""
    try:
        lines = path.read_text(errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except Exception:
        return ""


# ─── Cancel ────────────────────────────────────────────────────────────────


def request_cancel(job_id: str) -> tuple[bool, str]:
    """Try to cancel a job by job_id. Returns (ok, current_state).

    Three cases:
        1. Job is still queued       → simply remove from queue
        2. Job is running in worker  → SIGTERM the JVM via its meta pid;
           worker's wait() returns non-zero, job marked failed
        3. Job already finished      → no-op, ok=False
    """
    from rq.command import send_stop_job_command

    job = fetch_job(job_id)
    if job is None:
        return False, "unknown"

    status = job.get_status()
    if status in ("queued",):
        try:
            job.cancel()
            return True, "cancelled"
        except Exception:
            return False, status

    if status in ("started", "deferred"):
        # 1. Stop the worker via RQ's built-in stop command
        try:
            send_stop_job_command(_redis(), job_id)
        except Exception:
            pass
        # 2. Best-effort: SIGTERM the JVM directly using the pid we stashed
        pid = (job.meta or {}).get("pid")
        if pid:
            try:
                os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
            except Exception:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except Exception:
                    pass
        return True, "stopping"

    return False, status
