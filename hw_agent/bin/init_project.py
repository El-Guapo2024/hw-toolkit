#!/usr/bin/env python3
"""init_project: atomically create a new hw_agent project skeleton.

Usage:
    python -m hw_agent.bin.init_project <name> [--ceiling 60] [--vbat 11.1]

Creates:
    docs/projects/<name>/
    docs/projects/<name>/subsystems/
    docs/projects/<name>/profile.md   (minimal stub — human fills)

Resets:
    hw_agent/.state.json  (project=<name>, stage=intake, empty locked_mpns)

Refuses to overwrite an existing project unless --force is passed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path


PROFILE_STUB = """# {name} — project profile

> stage: intake — fill in loads first (actuators, sensors, MCU), then rails.

## Inputs (load-first)
- power source: {vbat} V (e.g. 3S LiPo nominal)
- actuators: TBD
- sensors:   TBD
- MCU:       TBD

## Constraints
- qty: TBD
- assembly: TBD
- BOM ceiling: ${ceiling}
- enclosure / mech: TBD

## Decisions
_(decisions flow into subsystems/*.json via designer-mcp tools — don't hand-edit those)_
"""


def init_state(name: str, ceiling: float) -> dict:
    return {
        "project": name,
        "stage": "intake",
        "stage_status": "in-progress",
        "doctrine": ["load-first", "pass1-no-math", "digikey-primary"],
        "spec_summary": {
            "locked_mpns": [],
            "rails_to_pick": [],
            "drivers_to_pick": [],
            "rail_targets": {},
            "constraints": {
                "qty_units": "TBD",
                "assembly": "TBD",
                "bom_ceiling_usd": ceiling,
            },
        },
        "bom_running_total_usd": 0.0,
        "bom_line_items": [],
        "updated_iso": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help="project name (snake_case)")
    ap.add_argument("--ceiling", type=float, default=60.0)
    ap.add_argument("--vbat", type=float, default=11.1)
    ap.add_argument("--force", action="store_true")
    ap.add_argument(
        "--repo",
        default=None,
        help="repo root (default: walk up from cwd)",
    )
    args = ap.parse_args()

    repo = Path(args.repo).resolve() if args.repo else _find_repo()
    proj_dir = repo / "docs" / "projects" / args.name
    state_file = repo / "hw_agent" / ".state.json"

    if proj_dir.exists() and not args.force:
        print(f"refuse: project dir already exists ({proj_dir}). pass --force to override.", file=sys.stderr)
        return 1

    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "subsystems").mkdir(exist_ok=True)

    profile = proj_dir / "profile.md"
    if not profile.exists() or args.force:
        profile.write_text(
            PROFILE_STUB.format(name=args.name, vbat=args.vbat, ceiling=args.ceiling)
        )

    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(init_state(args.name, args.ceiling), indent=2) + "\n")

    print(f"✓ project '{args.name}' initialized")
    print(f"  dir:   {proj_dir}")
    print(f"  state: {state_file}")
    print(f"  next:  edit profile.md, then lock loads via designer-mcp tools")
    return 0


def _find_repo() -> Path:
    cur = Path.cwd().resolve()
    while cur != cur.parent:
        gitp = cur / ".git"
        if gitp.is_dir() or gitp.is_file():
            return cur
        cur = cur.parent
    return Path.cwd().resolve()


if __name__ == "__main__":
    sys.exit(main())
