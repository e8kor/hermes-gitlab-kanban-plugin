#!/usr/bin/env python3
"""Sync one completed kanban task back to its GitLab issue / merge request.

Posts the task result as a note and closes or labels the object per the bridge
config's ``sync_back`` section. Idempotent — a task already synced is skipped.

Usage:
    gitlab-kanban-sync-back.py <task_id> [--board <slug>] [--dry-run]
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
    parser.add_argument("task_id")
    parser.add_argument("--board")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        bridge = load_module("bridge")
        kanban = load_module("kanban")
        config = load_module("config")
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1

    cfg = config.load_config()
    board = args.board or cfg.get("board_slug") or "gitlab"
    task = kanban.show_task(board, args.task_id)
    if task is None:
        print(f"task {args.task_id} not found on board {board}", file=sys.stderr)
        return 1

    state = bridge.load_sync_state()
    result = bridge.sync_back_task(board, task, cfg=cfg, state=state, dry_run=args.dry_run)
    if result.get("synced"):
        bridge.save_sync_state(state)
    print(json.dumps(result, indent=2, default=str))
    return 0 if not result.get("errors") else 1


if __name__ == "__main__":
    sys.exit(main())
