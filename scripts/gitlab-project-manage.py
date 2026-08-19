#!/usr/bin/env python3
"""Onboard / remove / list GitLab projects bridged into the kanban board.

Standalone counterpart to ``hermes gitlab-kanban project ...`` — useful from
cron, provisioning scripts, or a fresh shell without a Hermes session.

Usage:
    gitlab-project-manage.py list
    gitlab-project-manage.py onboard <group/repo> [--host alias] [--board slug]
                                     [--default-role role] [--url URL] [--no-hook]
    gitlab-project-manage.py remove  <group/repo> [--host alias] [--keep-hook]
    gitlab-project-manage.py webhook-url
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
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("list")
    sub.add_parser("webhook-url")

    p_on = sub.add_parser("onboard")
    p_on.add_argument("path")
    p_on.add_argument("--host")
    p_on.add_argument("--board", dest="board_slug")
    p_on.add_argument("--default-role")
    p_on.add_argument("--url")
    p_on.add_argument("--no-hook", action="store_true")

    p_rm = sub.add_parser("remove")
    p_rm.add_argument("path")
    p_rm.add_argument("--host")
    p_rm.add_argument("--keep-hook", action="store_true")

    args = parser.parse_args()

    try:
        projects = load_module("projects")
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.action == "list":
        result = projects.list_projects()
    elif args.action == "webhook-url":
        print(projects.public_webhook_url())
        return 0
    elif args.action == "onboard":
        result = projects.onboard(
            args.path,
            host=args.host,
            board_slug=args.board_slug,
            default_role=args.default_role,
            url=args.url,
            register_hook=not args.no_hook,
        )
    else:
        result = projects.remove(args.path, host=args.host, delete_hook=not args.keep_hook)

    print(json.dumps(result, indent=2, default=str))
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
