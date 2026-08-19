"""CLI subcommand tree: ``hermes gitlab-kanban <verb>``.

Organised as the lifecycle a user actually walks:

  CONNECT   status, host, webhook
  ONBOARD   project
  CONFIGURE config, role, label
  WORK      issue, merge-request, milestone, task
  SYNC      sync, install-sweep
"""

from __future__ import annotations

import json
from typing import Any

from . import slash, tools


def setup_cli(subparser: Any) -> None:
    """Build the ``hermes gitlab-kanban`` subcommand tree."""
    sub = subparser.add_subparsers(dest="gitlab_kanban_action")

    # ---- CONNECT ----
    sub.add_parser("status", help="Bridge health: hosts, tokens, projects, board, sync state")

    p_host = sub.add_parser("host", help="Manage GitLab hosts (gitlab.com and self-managed)")
    hs = p_host.add_subparsers(dest="host_action")
    hs.add_parser("list", help="List configured hosts and token status")
    p_host_add = hs.add_parser("add", help="Add a GitLab host")
    p_host_add.add_argument("alias", help="Short name, e.g. work-selfhosted")
    p_host_add.add_argument("url", help="Base URL, e.g. https://gitlab.mycorp.io")
    p_host_add.add_argument("--token-env", default="GITLAB_TOKEN", help="Name of the .env var holding this host's token")
    p_host_add.add_argument("--no-verify-ssl", action="store_true", help="Skip TLS verification (self-signed certs)")
    p_host_rm = hs.add_parser("remove", help="Remove a GitLab host")
    p_host_rm.add_argument("alias")
    p_host_def = hs.add_parser("default", help="Set the default host")
    p_host_def.add_argument("alias")

    p_webhook = sub.add_parser(
        "webhook", help="Show/verify the webhook route (catches the silent event-name mismatch)"
    )
    p_webhook.add_argument(
        "--install", action="store_true",
        help="Subscribe (or re-subscribe) the route with the correct X-Gitlab-Event names",
    )
    p_webhook.add_argument(
        "--print-command", action="store_true",
        help="Print the subscribe command instead of running it",
    )

    # ---- ONBOARD ----
    p_project = sub.add_parser("project", help="Manage bridged GitLab projects")
    ps = p_project.add_subparsers(dest="project_action")
    ps.add_parser("list", help="List onboarded projects")
    p_on = ps.add_parser("onboard", help="Onboard a project: register its webhook + allow-list it")
    p_on.add_argument("path", help="group/subgroup/repo")
    p_on.add_argument("--host", help="Host alias (default: configured default host)")
    p_on.add_argument("--board", dest="board_slug", help="Dedicated kanban board for this project")
    p_on.add_argument("--default-role", help="Role for this project's unlabelled work")
    p_on.add_argument("--url", help="Public webhook URL override")
    p_on.add_argument("--no-hook", action="store_true", help="Config-only: do not register a GitLab webhook")
    p_off = ps.add_parser("remove", help="Remove a project and delete its webhook")
    p_off.add_argument("path")
    p_off.add_argument("--host")
    p_off.add_argument("--keep-hook", action="store_true", help="Leave the GitLab webhook in place")

    # ---- CONFIGURE ----
    p_cfg = sub.add_parser("config", help="Show or change bridge configuration")
    cs = p_cfg.add_subparsers(dest="config_action")
    cs.add_parser("show", help="Print the full config")
    p_board = cs.add_parser("board", help="Set the kanban board")
    p_board.add_argument("slug")
    p_board.add_argument("--name", help="Display name")
    p_role = cs.add_parser("role", help="Map a role to a Hermes profile")
    p_role.add_argument("role", help="developer | reviewer | scrum-master | qa | researcher | writer")
    p_role.add_argument("profile", help="Hermes profile that executes the role")
    p_label = cs.add_parser("label", help="Map a GitLab label to a role")
    p_label.add_argument("label")
    p_label.add_argument("role")
    p_auto = cs.add_parser("auto-label", help="Add an auto-label rule")
    p_auto.add_argument("rule", help="'<title|body|label> contains <text> => label1,label2'")
    p_ingest = cs.add_parser("ingest", help="Choose which GitLab events create tasks")
    p_ingest.add_argument("--issues", choices=["on", "off"])
    p_ingest.add_argument("--merge-requests", choices=["on", "off"])
    p_sync_cfg = cs.add_parser("sync-back", help="Choose what happens on task completion")
    p_sync_cfg.add_argument("--comment", choices=["on", "off"])
    p_sync_cfg.add_argument("--close-issue", choices=["on", "off"])
    p_sync_cfg.add_argument("--close-merge-request", choices=["on", "off"])
    p_sync_cfg.add_argument("--labels-on-done", help="Comma-separated labels to add on completion")
    cs.add_parser("reset", help="Reset the config to defaults")

    # ---- WORK ----
    p_issue = sub.add_parser("issue", help="Issue CRUD")
    isub = p_issue.add_subparsers(dest="issue_action")
    for verb, helptext in (
        ("list", "List issues"), ("get", "Show one issue"), ("create", "Create an issue"),
        ("update", "Update an issue"), ("close", "Close an issue"), ("reopen", "Reopen an issue"),
        ("comment", "Comment on an issue"), ("notes", "List issue notes"),
        ("delete", "Delete an issue"), ("to-kanban", "Create a bridged kanban task for an issue"),
    ):
        p = isub.add_parser(verb, help=helptext)
        p.add_argument("--project", help="group/repo (default: the single onboarded project)")
        p.add_argument("--host")
        if verb != "list":
            p.add_argument("iid", nargs="?", type=int, help="Issue number (#N)")
        p.add_argument("--title")
        p.add_argument("--description")
        p.add_argument("--labels")
        p.add_argument("--assignee")
        p.add_argument("--milestone-id", type=int)
        p.add_argument("--due-date")
        p.add_argument("--weight", type=int)
        p.add_argument("--body", help="Note body (comment)")
        p.add_argument("--state", help="opened | closed | all (list)")
        p.add_argument("--search")
        p.add_argument("--role", help="Role for the kanban task (to-kanban)")
        p.add_argument("--limit", type=int, default=20)

    p_mr = sub.add_parser("merge-request", aliases=["mr"], help="Merge request CRUD")
    msub = p_mr.add_subparsers(dest="mr_action")
    for verb, helptext in (
        ("list", "List merge requests"), ("get", "Show one merge request"),
        ("create", "Create a merge request"), ("update", "Update a merge request"),
        ("comment", "Comment on a merge request"), ("approve", "Approve a merge request"),
        ("merge", "Merge a merge request"), ("close", "Close a merge request"),
        ("reopen", "Reopen a merge request"), ("changes", "Show the changed-files diff"),
        ("to-kanban", "Create a bridged kanban review task"),
    ):
        p = msub.add_parser(verb, help=helptext)
        p.add_argument("--project")
        p.add_argument("--host")
        if verb != "list":
            p.add_argument("iid", nargs="?", type=int, help="Merge request number (!N)")
        p.add_argument("--title")
        p.add_argument("--description")
        p.add_argument("--source-branch")
        p.add_argument("--target-branch")
        p.add_argument("--labels")
        p.add_argument("--assignee")
        p.add_argument("--reviewer")
        p.add_argument("--milestone-id", type=int)
        p.add_argument("--draft", action="store_true")
        p.add_argument("--squash", action="store_true")
        p.add_argument("--remove-source-branch", action="store_true")
        p.add_argument("--merge-commit-message")
        p.add_argument("--body")
        p.add_argument("--state")
        p.add_argument("--role")
        p.add_argument("--limit", type=int, default=20)

    p_ms = sub.add_parser("milestone", aliases=["sprint"], help="Milestone / sprint management")
    mssub = p_ms.add_subparsers(dest="milestone_action")
    for verb, helptext in (
        ("list", "List milestones"), ("get", "Show one milestone"), ("create", "Create a milestone"),
        ("update", "Update a milestone"), ("close", "Close a milestone"),
        ("reopen", "Reopen a milestone"), ("delete", "Delete a milestone"),
        ("issues", "List a milestone's issues"), ("progress", "Sprint progress report"),
    ):
        p = mssub.add_parser(verb, help=helptext)
        p.add_argument("--project")
        p.add_argument("--host")
        p.add_argument("--group", help="Group path for group-level milestones (list)")
        if verb != "list" and verb != "create":
            p.add_argument("milestone_id", nargs="?", type=int)
        p.add_argument("--title")
        p.add_argument("--description")
        p.add_argument("--start-date")
        p.add_argument("--due-date")
        p.add_argument("--state", choices=["active", "closed"])
        p.add_argument("--limit", type=int, default=20)

    p_task = sub.add_parser("task", help="Kanban task operations on the bridge board")
    tsub = p_task.add_subparsers(dest="task_action")
    for verb, helptext in (
        ("list", "List tasks"), ("show", "Show one task"), ("create", "Create a task"),
        ("assign", "Assign a task to a role or profile"), ("comment", "Comment on a task"),
        ("complete", "Complete a task"),
    ):
        p = tsub.add_parser(verb, help=helptext)
        p.add_argument("--board")
        if verb != "list" and verb != "create":
            p.add_argument("task_id", nargs="?")
        p.add_argument("--title")
        p.add_argument("--body")
        p.add_argument("--role")
        p.add_argument("--assignee")
        p.add_argument("--status")
        p.add_argument("--priority", type=int)

    # ---- SYNC ----
    p_sync = sub.add_parser("sync", help="Sync completed kanban tasks back to GitLab")
    p_sync.add_argument("task_id", nargs="?", help="Sync one task instead of sweeping the board")
    p_sync.add_argument("--board")
    p_sync.add_argument("--dry-run", action="store_true", help="Report writes without performing them")

    p_sweep = sub.add_parser("install-sweep", help="Print the cron command that installs the periodic sweep")
    p_sweep.add_argument("--schedule", default="every 5m")


# --------------------------------------------------------------------------


def _print(payload: str) -> None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        print(payload)
        return
    print(json.dumps(data, indent=2, default=str))


def _flag(value: Any) -> Any:
    """Translate an ``on``/``off`` choice into a bool, leaving None alone."""
    if value is None:
        return None
    return value == "on"


def _collect(args: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in keys:
        value = getattr(args, key, None)
        if value not in (None, False):
            out[key] = value
    return out


def _install_webhook(projects_mod) -> None:
    """Subscribe the route with the correct event names, replacing a bad one.

    Re-subscribing is the documented repair for a route registered with the
    payload's ``object_kind`` values instead of the ``X-Gitlab-Event`` header
    names, which otherwise fails silently.
    """
    import subprocess

    status = projects_mod.webhook_status()
    route = status["route"]
    if status.get("registered"):
        if status.get("ok"):
            print(f"Route '{route}' is already subscribed correctly:")
            print(f"  events: {status.get('configured_events')}")
            print("Nothing to do. Re-run with --print-command to see the command.")
            return
        print(f"Replacing misconfigured route '{route}' ({status.get('problem')})")
        removed = subprocess.run(
            ["hermes", "webhook", "remove", route], capture_output=True, text=True, timeout=30
        )
        if removed.returncode != 0:
            print(f"error removing the existing route: {removed.stderr.strip()[:300]}")
            return

    cmd = projects_mod.webhook_subscribe_command()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f"error subscribing the route: {result.stderr.strip()[:300]}")
        print("Run this manually:")
        print("  " + " ".join(projects_mod._shell_quote(x) for x in cmd))
        return
    print(result.stdout.strip() or f"subscribed route '{route}'")

    verify = projects_mod.webhook_status()
    if verify.get("ok"):
        print(f"verified: events {verify.get('configured_events')}")
    else:
        print(f"WARNING: still not healthy — {verify.get('problem')}")


def handle_cli(args: Any) -> None:
    """Handle ``hermes gitlab-kanban <verb>``."""
    action = getattr(args, "gitlab_kanban_action", None)
    if not action:
        print(slash.HELP)
        return

    if action == "status":
        print(slash.handle_slash("status"))
        return

    if action == "webhook":
        from . import projects as projects_mod

        if getattr(args, "print_command", False):
            print(" ".join(projects_mod._shell_quote(x) for x in projects_mod.webhook_subscribe_command()))
            return
        if getattr(args, "install", False):
            _install_webhook(projects_mod)
            return
        print(slash.handle_slash("webhook"))
        return

    if action == "host":
        sub = getattr(args, "host_action", None) or "list"
        if sub == "list":
            _print(tools.gitlab_kanban_status({}))
        elif sub == "add":
            _print(tools.gitlab_kanban_config({
                "action": "add-host", "host": args.alias, "url": args.url,
                "token_env": args.token_env, "verify_ssl": not args.no_verify_ssl,
            }))
        elif sub == "remove":
            _print(tools.gitlab_kanban_config({"action": "remove-host", "host": args.alias}))
        elif sub == "default":
            _print(tools.gitlab_kanban_config({"action": "set-default-host", "host": args.alias}))
        return

    if action == "project":
        sub = getattr(args, "project_action", None) or "list"
        if sub == "list":
            print(slash.handle_slash("projects"))
        elif sub == "onboard":
            _print(tools.gitlab_kanban_project({
                "action": "onboard", "project": args.path, "host": args.host,
                "board_slug": args.board_slug, "default_role": args.default_role,
                "url": args.url, "register_hook": not args.no_hook,
            }))
        elif sub == "remove":
            _print(tools.gitlab_kanban_project({
                "action": "remove", "project": args.path, "host": args.host,
            }))
        return

    if action == "config":
        sub = getattr(args, "config_action", None) or "show"
        if sub == "show":
            print(slash.handle_slash("config"))
        elif sub == "board":
            _print(tools.gitlab_kanban_config({"action": "set-board", "board_slug": args.slug, "board_name": args.name}))
        elif sub == "role":
            _print(tools.gitlab_kanban_config({"action": "set-role-profile", "role": args.role, "profile": args.profile}))
        elif sub == "label":
            _print(tools.gitlab_kanban_config({"action": "set-label-role", "label": args.label, "role": args.role}))
        elif sub == "auto-label":
            _print(tools.gitlab_kanban_config({"action": "add-auto-label", "rule": args.rule}))
        elif sub == "ingest":
            payload = {"action": "set-ingest"}
            if _flag(args.issues) is not None:
                payload["issues"] = _flag(args.issues)
            if _flag(args.merge_requests) is not None:
                payload["merge_requests"] = _flag(args.merge_requests)
            _print(tools.gitlab_kanban_config(payload))
        elif sub == "sync-back":
            payload = {"action": "set-sync-back"}
            for key in ("comment", "close_issue", "close_merge_request"):
                val = _flag(getattr(args, key, None))
                if val is not None:
                    payload[key] = val
            if args.labels_on_done:
                payload["labels_on_done"] = args.labels_on_done
            _print(tools.gitlab_kanban_config(payload))
        elif sub == "reset":
            _print(tools.gitlab_kanban_config({"action": "reset"}))
        return

    if action == "issue":
        sub = getattr(args, "issue_action", None)
        if not sub:
            print("usage: hermes gitlab-kanban issue <list|get|create|update|close|reopen|comment|notes|delete|to-kanban>")
            return
        payload = {"action": sub}
        payload.update(_collect(args, (
            "project", "host", "iid", "title", "description", "labels", "assignee",
            "milestone_id", "due_date", "weight", "body", "state", "search", "role", "limit",
        )))
        _print(tools.gitlab_issue(payload))
        return

    if action in ("merge-request", "mr"):
        sub = getattr(args, "mr_action", None)
        if not sub:
            print("usage: hermes gitlab-kanban merge-request <list|get|create|update|comment|approve|merge|close|reopen|changes|to-kanban>")
            return
        payload = {"action": sub}
        payload.update(_collect(args, (
            "project", "host", "iid", "title", "description", "source_branch", "target_branch",
            "labels", "assignee", "reviewer", "milestone_id", "draft", "squash",
            "remove_source_branch", "merge_commit_message", "body", "state", "role", "limit",
        )))
        _print(tools.gitlab_merge_request(payload))
        return

    if action in ("milestone", "sprint"):
        sub = getattr(args, "milestone_action", None)
        if not sub:
            print("usage: hermes gitlab-kanban milestone <list|get|create|update|close|reopen|delete|issues|progress>")
            return
        payload = {"action": sub}
        payload.update(_collect(args, (
            "project", "host", "group", "milestone_id", "title", "description",
            "start_date", "due_date", "state", "limit",
        )))
        _print(tools.gitlab_milestone(payload))
        return

    if action == "task":
        sub = getattr(args, "task_action", None)
        if not sub:
            print("usage: hermes gitlab-kanban task <list|show|create|assign|comment|complete>")
            return
        payload = {"action": sub}
        payload.update(_collect(args, (
            "board", "task_id", "title", "body", "role", "assignee", "status", "priority",
        )))
        _print(tools.gitlab_kanban_task(payload))
        return

    if action == "sync":
        payload = {"dry_run": bool(args.dry_run)}
        if args.task_id:
            payload["task_id"] = args.task_id
        if args.board:
            payload["board"] = args.board
        _print(tools.gitlab_kanban_sync(payload))
        return

    if action == "install-sweep":
        print(
            "Install the periodic sync sweep with:\n\n"
            f"  hermes cron add --name gitlab-kanban-sync --schedule '{args.schedule}' \\\n"
            "      --script gitlab-kanban-sync-sweep.py --no-agent\n\n"
            "Then confirm with: hermes cron list"
        )
        return

    print(slash.HELP)
