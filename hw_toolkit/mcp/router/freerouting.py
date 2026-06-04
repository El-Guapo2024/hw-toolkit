"""FreeRouting integration — async TaskManager + high-level route_board().

Pattern adapted from bunnyf/pcb-mcp's TaskManager (GPL-3.0). This file is
a clean-room reimplementation in our own style; we copy the *pattern* (job
IDs, status polling, log tailing, ttl cleanup) without taking their code.

Architecture:

    route_board(kicad_pcb_path) → submits a task
        │
        ├─ pcb_writer.run_dsn_export (IPC)   → pcbnew exports Specctra DSN
        │
        ├─ TaskManager.start       → java -jar freerouting.jar -de in.dsn -do out.ses
        │   (background subprocess, JVM ~3s startup, real route 5s-30min)
        │
        ├─ poll TaskManager.status until done
        │
        └─ pcb_writer.run_ses_import (IPC)   → pcbnew imports the SES, saves .kicad_pcb

The TaskManager is in-process (lives inside the hw-agent MCP server). To
promote to a real microservice later, lift TaskManager into a FastAPI
service — the public API stays the same; only the transport changes.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


# ─── Path detection ─────────────────────────────────────────────────────────

_FREEROUTING_JAR_CANDIDATES = [
    Path(__file__).resolve().parents[1] / ".tools" / "freerouting.jar",
    Path("/usr/local/lib/freerouting.jar"),
    Path("/opt/freerouting/freerouting.jar"),
]


_JAVA_CANDIDATES = [
    "/usr/local/opt/openjdk/bin/java",       # brew openjdk (current)
    "/usr/local/opt/openjdk@25/bin/java",
    "/usr/local/opt/openjdk@21/bin/java",
    "/opt/homebrew/opt/openjdk/bin/java",    # apple silicon brew
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
    raise FileNotFoundError(
        "Java runtime not found. Install with `brew install openjdk` and ensure "
        "it's in PATH, or set JAVA_BIN to the absolute path of the java binary."
    )


def find_freerouting_jar() -> Path:
    env = os.environ.get("FREEROUTING_JAR")
    if env and Path(env).exists():
        return Path(env)
    for c in _FREEROUTING_JAR_CANDIDATES:
        if c.exists():
            return c
    raise FileNotFoundError(
        "freerouting.jar not found. Drop it in .tools/freerouting.jar or "
        "set FREEROUTING_JAR env var."
    )


# ─── Task state ─────────────────────────────────────────────────────────────

TaskState = str  # "queued" | "running" | "succeeded" | "failed" | "cancelled"


@dataclass
class Task:
    task_id: str
    dsn_path: Path
    out_ses_path: Path
    log_path: Path
    state: TaskState = "queued"
    started_at: float = 0.0
    finished_at: float = 0.0
    error: Optional[str] = None
    process: Optional[subprocess.Popen] = field(default=None, repr=False)
    pass_limit: int = 5
    threads: int = 0  # 0 = freerouting default (auto)

    def elapsed_s(self) -> float:
        if self.state in ("queued",) or self.started_at == 0:
            return 0.0
        end = self.finished_at if self.finished_at else time.time()
        return round(end - self.started_at, 2)

    def to_dict(self) -> dict:
        # Skip Popen (contains thread lock, can't deepcopy via asdict).
        return {
            "task_id": self.task_id,
            "state": self.state,
            "dsn_path": str(self.dsn_path),
            "out_ses_path": str(self.out_ses_path),
            "log_path": str(self.log_path),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "pass_limit": self.pass_limit,
            "threads": self.threads,
            "elapsed_s": self.elapsed_s(),
        }

    def log_tail(self, n: int = 20) -> list[str]:
        if not self.log_path.exists():
            return []
        text = self.log_path.read_text(errors="replace")
        return text.splitlines()[-n:]


# ─── TaskManager ────────────────────────────────────────────────────────────

class TaskManager:
    """Tracks FreeRouting subprocesses by task_id. In-process, single-threaded
    bookkeeping. Replace with a FastAPI service later if scaling needed.
    """

    def __init__(self, work_dir: Optional[Path] = None):
        self.work_dir = (work_dir or Path("/tmp/hw_toolkit_routes")).resolve()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()

    def start(
        self,
        dsn_path: Path,
        out_ses_path: Optional[Path] = None,
        passes: int = 5,
        threads: int = 0,
    ) -> Task:
        """Spawn freerouting in the background. Returns immediately with a Task."""
        dsn_path = Path(dsn_path).resolve()
        if not dsn_path.exists():
            raise FileNotFoundError(f"DSN file not found: {dsn_path}")

        task_id = uuid.uuid4().hex[:8]
        out_ses = out_ses_path or self.work_dir / f"{task_id}.ses"
        out_ses = Path(out_ses).resolve()
        log_path = self.work_dir / f"{task_id}.log"

        java = find_java()
        jar = find_freerouting_jar()

        # `-da` disables FreeRouting's analytics HTTP calls. Without it the
        # JVM keeps a daemon thread alive trying to reach api.freerouting.app
        # every 60 minutes, which on a sandboxed runner means the JVM never
        # exits cleanly even after routing finishes.
        cmd = [
            java, "-jar", str(jar),
            "-de", str(dsn_path),
            "-do", str(out_ses),
            "-mp", str(passes),
            "-da",
        ]
        if threads > 0:
            cmd.extend(["-mt", str(threads)])

        log_fh = open(log_path, "w")
        proc = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # so cancel() can SIGTERM the whole group
        )

        task = Task(
            task_id=task_id,
            dsn_path=dsn_path,
            out_ses_path=out_ses,
            log_path=log_path,
            state="running",
            started_at=time.time(),
            process=proc,
            pass_limit=passes,
            threads=threads,
        )
        with self._lock:
            self._tasks[task_id] = task
        return task

    def status(self, task_id: str) -> Optional[dict]:
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            return None
        # Poll subprocess if still running.
        if task.state == "running" and task.process is not None:
            rc = task.process.poll()
            if rc is not None:
                task.finished_at = time.time()
                if rc == 0 and task.out_ses_path.exists():
                    task.state = "succeeded"
                else:
                    task.state = "failed"
                    task.error = f"freerouting exited rc={rc}"
        d = task.to_dict()
        d["log_tail"] = task.log_tail(15)
        return d

    def result(self, task_id: str) -> Optional[Path]:
        s = self.status(task_id)
        if s and s.get("state") == "succeeded":
            return Path(s["out_ses_path"])
        return None

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
        if not task or task.process is None:
            return False
        if task.state != "running":
            return False
        try:
            os.killpg(os.getpgid(task.process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return False
        task.finished_at = time.time()
        task.state = "cancelled"
        return True

    def list(self) -> list[dict]:
        # Refresh state for any tasks we haven't polled.
        with self._lock:
            ids = list(self._tasks.keys())
        return [self.status(tid) for tid in ids]

    def cleanup(self, older_than_s: float = 3600) -> list[str]:
        """Remove tasks finished more than older_than_s seconds ago."""
        now = time.time()
        removed: list[str] = []
        with self._lock:
            for tid, task in list(self._tasks.items()):
                if task.state in ("succeeded", "failed", "cancelled"):
                    if task.finished_at and (now - task.finished_at) > older_than_s:
                        del self._tasks[tid]
                        removed.append(tid)
        return removed


# Module-level singleton — convenient for MCP tool calls.
_default_manager: Optional[TaskManager] = None


def manager() -> TaskManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = TaskManager()
    return _default_manager


# ─── High-level route_board: DSN export → freerouting → SES import ─────────

def route_board(
    kicad_pcb: Path,
    kicad_sch: Optional[Path] = None,
    passes: int = 5,
    threads: int = 0,
    timeout_s: float = 600,
    skip_netlist_sync: bool = False,
    on_progress=None,  # type: Optional[Callable[[int, int, str], None]]
) -> dict:
    """Synchronous high-level: route an entire .kicad_pcb in place.

    Pipeline:
        0. sync netlist from kicad_sch → assign nets to PCB pads
        1. pcb_writer.run_dsn_export (IPC) → writes <board>.dsn
        2. TaskManager.start         → java -jar freerouting → <board>.ses
        3. poll until done           → wait up to timeout_s
        4. pcb_writer.run_ses_import (IPC) → reads SES, updates .kicad_pcb in place

    kicad_sch: source schematic for netlist export. Defaults to
    <kicad_pcb_stem>.kicad_sch in the same directory. Required unless
    skip_netlist_sync=True (e.g., when the PCB already has nets assigned).

    on_progress: optional callback (current_pct, total_pct, message). Fires
    at every major milestone PLUS per FreeRouting pass when log lines update.
    Used by the MCP tool to stream progress via ctx.report_progress.
    """
    import re
    import subprocess
    from .schematics import pcb_writer
    from .schematics.kicad_paths import kicad_cli

    def _emit(pct: int, msg: str):
        if on_progress:
            try:
                on_progress(pct, 100, msg)
            except Exception:
                pass  # progress is best-effort; never break the route

    _emit(0, "starting route pipeline")

    kicad_pcb = Path(kicad_pcb).resolve()
    work = manager().work_dir / kicad_pcb.stem
    work.mkdir(parents=True, exist_ok=True)
    dsn_path = work / f"{kicad_pcb.stem}.dsn"
    ses_path = work / f"{kicad_pcb.stem}.ses"
    net_path = work / f"{kicad_pcb.stem}.net"

    # 0. Net sync — without this, FreeRouting routes nothing because the PCB
    # has footprints but no connections. kicad-cli exports the netlist from
    # the schematic, then pcb_writer.run_sync_netlist (IPC) applies it to the pads.
    if not skip_netlist_sync:
        sch = Path(kicad_sch) if kicad_sch else kicad_pcb.with_suffix(".kicad_sch")
        if not sch.exists():
            return {
                "ok": False,
                "stage": "netlist_sync",
                "error": f"schematic not found: {sch}. "
                         "Pass kicad_sch=<path> or set skip_netlist_sync=True.",
            }
        _emit(5, "exporting netlist from schematic")
        net_proc = subprocess.run(
            [kicad_cli(), "sch", "export", "netlist", "--output",
             str(net_path), str(sch)],
            capture_output=True, text=True,
        )
        if net_proc.returncode != 0 or not net_path.exists():
            return {
                "ok": False,
                "stage": "netlist_export",
                "error": (net_proc.stderr or net_proc.stdout).strip()[:300],
            }
        _emit(10, "syncing nets to PCB pads")
        sync_report = pcb_writer.run_sync_netlist(kicad_pcb, net_path)
        if not sync_report.get("ok"):
            return {
                "ok": False,
                "stage": "netlist_sync",
                "error": sync_report.get("error", "sync_netlist failed"),
                "report": sync_report,
            }
        _emit(15,
              f"netlist sync done: "
              f"{sync_report.get('nets_total', 0)} nets, "
              f"{sync_report.get('pads_assigned', 0)} pads")

    # 1. Export DSN — runs under KiCad's Python (subprocess via pcb_writer).
    _emit(20, "exporting Specctra DSN")
    try:
        export_report = pcb_writer.run_dsn_export(kicad_pcb, dsn_path)
    except Exception as e:
        return {"ok": False, "stage": "dsn_export", "error": f"{type(e).__name__}: {e}"}
    if not dsn_path.exists():
        return {
            "ok": False,
            "stage": "dsn_export",
            "error": export_report.get("error", "DSN export failed"),
        }

    # 2. Start FreeRouting.
    task = manager().start(dsn_path, ses_path, passes=passes, threads=threads)

    _emit(25, f"freerouting started (task {task.task_id}, {passes} passes)")

    # 3. Poll. Tail the FreeRouting log for per-pass updates so the agent
    # sees progress instead of a 60-second silence.
    #
    # Quirk: FreeRouting v2.2 in headless CLI mode prints "Auto-router
    # session completed" but the JVM does NOT exit on its own — it leaves
    # daemon threads (analytics, JavaFX) alive forever. The shutdown hook
    # is what writes the SES, so we have to detect "session completed" and
    # SIGTERM the JVM ourselves to force the SES write.
    pass_re = re.compile(
        r"Auto-router pass #(\d+)[^(]*\(([^)]+)\)"
    )
    completion_re = re.compile(r"Auto-router session completed")
    last_pass_seen = 0
    completion_seen_at: Optional[float] = None
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        s = manager().status(task.task_id)
        if s["state"] in ("succeeded", "failed", "cancelled"):
            break
        # Parse the latest pass number from the log so we can stream updates.
        tail_text = ""
        if task.log_path.exists():
            try:
                tail_text = task.log_path.read_text(errors="replace")
                for m in pass_re.finditer(tail_text):
                    cur = int(m.group(1))
                    if cur > last_pass_seen:
                        last_pass_seen = cur
                        # 25-90% range allocated to routing passes
                        pct = 25 + min(int(65 * (cur / max(passes, 1))), 65)
                        unrouted = m.group(2).strip()
                        _emit(pct, f"pass {cur}/{passes} — {unrouted}")
            except OSError:
                pass
        # Early-out: routing produced output even though subprocess still alive.
        if ses_path.exists() and ses_path.stat().st_size > 0:
            time.sleep(1)
            break
        # Detected the FreeRouting completion line. Wait briefly for the SES
        # to appear; if it doesn't, send SIGTERM to trigger the JVM's
        # shutdown hook (which writes SES on its way out).
        if completion_seen_at is None and completion_re.search(tail_text):
            completion_seen_at = time.time()
            _emit(92, "freerouting reports session complete; awaiting SES")
        if completion_seen_at is not None:
            since = time.time() - completion_seen_at
            if ses_path.exists() and ses_path.stat().st_size > 0:
                break
            if since > 5:
                # JVM never wrote SES on its own — terminate to force
                # shutdown-hook flush.
                _emit(93, "forcing JVM termination to flush SES")
                manager().cancel(task.task_id)
                # Give the shutdown hook a moment to write.
                for _ in range(20):
                    if ses_path.exists() and ses_path.stat().st_size > 0:
                        break
                    time.sleep(0.5)
                break
        time.sleep(1)
    final = manager().status(task.task_id)
    # If the subprocess is still running but SES is on disk, consider it done.
    if final["state"] == "running" and ses_path.exists() and ses_path.stat().st_size > 0:
        manager().cancel(task.task_id)
        final = manager().status(task.task_id)
    # Either way, if SES exists and is non-empty, we count this as success.
    if ses_path.exists() and ses_path.stat().st_size > 0:
        with manager()._lock:
            t = manager()._tasks.get(task.task_id)
            if t and t.state != "succeeded":
                t.state = "succeeded"
                if not t.finished_at:
                    t.finished_at = time.time()
        final = manager().status(task.task_id)

    if final["state"] != "succeeded":
        return {
            "ok": False,
            "stage": "freerouting",
            "task_id": task.task_id,
            "state": final["state"],
            "error": final.get("error"),
            "elapsed_s": final["elapsed_s"],
            "log_tail": final["log_tail"],
        }

    # 4. Import SES back into the board.
    _emit(95, "importing routes back into .kicad_pcb")
    try:
        import_report = pcb_writer.run_ses_import(kicad_pcb, ses_path)
    except Exception as e:
        return {"ok": False, "stage": "ses_import", "error": f"{type(e).__name__}: {e}"}
    if not import_report.get("ok"):
        return {
            "ok": False,
            "stage": "ses_import",
            "error": import_report.get("error", "SES import failed"),
            "import_report": import_report,
        }
    tracks = import_report.get("tracks_after", 0)
    nets = import_report.get("nets_routed", 0)
    vias = import_report.get("vias", 0)
    length_mm = import_report.get("total_length_mm", 0.0)
    summary = (
        f"Routed {nets} net{'s' if nets != 1 else ''}, "
        f"{vias} via{'s' if vias != 1 else ''}, "
        f"{length_mm:.1f} mm total trace length "
        f"({tracks} track segments)"
    )
    _emit(100, f"done — {tracks} tracks in {final['elapsed_s']:.1f}s")

    return {
        "ok": True,
        "task_id": task.task_id,
        "elapsed_s": final["elapsed_s"],
        "wall_seconds": final["elapsed_s"],
        "dsn_path": str(dsn_path),
        "ses_path": str(ses_path),
        "kicad_pcb": str(kicad_pcb),
        "summary": summary,
        "import_report": import_report,
    }
