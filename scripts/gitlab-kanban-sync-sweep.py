#!/usr/bin/env python3
"""Periodic sweep: completed kanban tasks -> GitLab issues / merge requests.

The safety net for completions that finished while nothing was listening. It is
deliberately conservative: a task already recorded in the sync state is a no-op,
so re-running on a schedule cannot post duplicate notes or re-close anything.

Silent when nothing was synced, so a cron job does not spam the user.

Usage:
    gitlab-kanban-sync-sweep.py [--board <slug>] [--dry-run] [--verbose]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gitlab_kanban_loader import load_module  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", help="Board slug (default: the configured bridge board)")
    parser.add_argument("--dry-run", action="store_true", help="Report writes without performing them")
    parser.add_argument("--verbose", action="store_true", help="Print a summary even when nothing changed")
    args = parser.parse_args()

    try:
        bridge = load_module("bridge")
        result = bridge.sweep(args.board, dry_run=args.dry_run)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - cron must not crash-loop on a transient
        print(f"sweep error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    failed = result.get("failed") or []
    if args.dry_run or args.verbose or result.get("synced") or failed:
        print(json.dumps(result, indent=2, default=str))
    for entry in failed:
        print(f"sync failed for {entry.get('task_id')}: {entry.get('errors')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
