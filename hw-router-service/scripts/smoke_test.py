#!/usr/bin/env python3
"""End-to-end smoke test for hw-router-service.

Submits a known-good DSN to a running instance, polls until done,
validates the SES output. Returns exit 0 on success, non-zero on
any failure.

Usage:
    python3 scripts/smoke_test.py                  # default localhost:8002
    python3 scripts/smoke_test.py --url https://router.example/

Prerequisites:
    docker compose up -d   (in hw-router-service/)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────
# Tiny Specctra DSN used for the smoke test. Pulled from FreeRouting's
# own public fixtures (Issue026-J2_reference) — small enough to route in
# under 30 seconds on commodity CPU. Embedded here so the test has zero
# network deps beyond the service URL itself.
# ──────────────────────────────────────────────────────────────────────

DSN_FIXTURE_URL = (
    "https://raw.githubusercontent.com/freerouting/freerouting/master/"
    "fixtures/Issue026-J2_reference.dsn"
)


def fetch_dsn(cache: Path) -> str:
    """Download the test DSN once, cache locally."""
    if cache.exists():
        return cache.read_text()
    with urllib.request.urlopen(DSN_FIXTURE_URL, timeout=15) as r:
        text = r.read().decode("utf-8", errors="replace")
    cache.write_text(text)
    return text


def http_json(method: str, url: str, body: dict | None = None,
              timeout: float = 10) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main(base: str, timeout_s: float, passes: int) -> int:
    print(f"smoke test: {base}")

    # 1) /health
    print("  /health …", end=" ", flush=True)
    try:
        h = http_json("GET", f"{base}/health")
    except Exception as e:
        print(f"FAIL: {e}")
        return 2
    if not h.get("ok"):
        print(f"FAIL: {h}")
        return 2
    print(f"ok (java={h.get('java')!s:.40s}…)")

    # 2) cache + load fixture DSN
    cache = Path("/tmp/hw-router-smoke.dsn")
    print(f"  fetch fixture DSN ({DSN_FIXTURE_URL.split('/')[-1]}) …", end=" ",
          flush=True)
    try:
        dsn = fetch_dsn(cache)
    except Exception as e:
        print(f"FAIL: {e}")
        return 3
    print(f"ok ({len(dsn)} chars)")

    # 3) submit
    print(f"  POST /jobs (passes={passes}) …", end=" ", flush=True)
    try:
        sub = http_json("POST", f"{base}/jobs",
                        {"dsn": dsn, "passes": passes, "threads": 0})
    except Exception as e:
        print(f"FAIL: {e}")
        return 4
    job_id = sub.get("job_id")
    if not job_id:
        print(f"FAIL: no job_id in {sub}")
        return 4
    print(f"job_id={job_id}")

    # 4) poll
    deadline = time.time() + timeout_s
    last_state = None
    while time.time() < deadline:
        time.sleep(2)
        try:
            st = http_json("GET", f"{base}/jobs/{job_id}?include_ses=false")
        except Exception as e:
            print(f"  poll FAIL: {e}")
            return 5
        state = st.get("state")
        elapsed = st.get("elapsed_s", 0)
        if state != last_state:
            print(f"  state={state} elapsed_s={elapsed}")
            last_state = state
        if state in ("succeeded", "failed", "cancelled"):
            break
    else:
        print(f"FAIL: timeout after {timeout_s}s")
        return 6

    if state != "succeeded":
        print(f"FAIL: state={state} error={st.get('error')}")
        return 7

    # 5) fetch SES
    print("  GET full result …", end=" ", flush=True)
    try:
        full = http_json("GET", f"{base}/jobs/{job_id}?log_tail=0",
                          timeout=30)
    except Exception as e:
        print(f"FAIL: {e}")
        return 8
    ses = full.get("ses") or ""
    if not ses.strip().startswith("(session"):
        print(f"FAIL: SES does not start with '(session' (got: {ses[:60]!r})")
        return 9
    print(f"ok ({len(ses)} chars)")

    print()
    print("✅ all checks passed")
    print(f"   job:        {job_id}")
    print(f"   elapsed:    {st.get('elapsed_s')}s")
    print(f"   ses size:   {len(ses)} chars")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://localhost:8002",
                    help="hw-router-service base URL")
    ap.add_argument("--timeout", type=float, default=120,
                    help="seconds to wait for routing to finish")
    ap.add_argument("--passes", type=int, default=3,
                    help="FreeRouting pass count (lower = faster smoke test)")
    args = ap.parse_args()
    sys.exit(main(args.url.rstrip("/"), args.timeout, args.passes))
