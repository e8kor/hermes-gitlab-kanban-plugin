"""Slash command handler for /gitlab-kanban.

Renders aligned plain-text tables, not the raw JSON the tool handlers return —
the chat surface is for humans.
"""

from __future__ import annotations

import json
from typing import Any

from . import bridge, projects, tools
from .config import load_config

HELP = """/gitlab-kanban <action>

  status                    Bridge health: hosts, tokens, projects, board, sync
  projects                  List onboarded GitLab projects
  board                     Kanban tasks on the bridge board
  issues [project]          Open issues on a project
  mrs [project]             Open merge requests on a project
  sprints [project]         Milestones (sprints) on a project
  sync [--dry-run]          Sync completed tasks back to GitLab
  config                    Show the bridge configuration
  webhook                   Webhook route, public URL, secret presence
  help                      This help

CRUD (create/update/close/merge) goes through the chat tools or the CLI:
  hermes gitlab-kanban issue|merge-request|milestone ...
"""


def _table(rows: list[list[str]], headers: list[str]) -> str:
    if not rows:
        return "(none)"
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    out = [line, "  ".join("-" * widths[i] for i in range(len(headers)))]
    for row in rows:
        out.append("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)))
    return "\n".join(out)


def _call(handler: Any, args: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(handler(args))
    except (json.JSONDecodeError, TypeError):
        return {"error": "unparseable tool result"}


def _render_status(data: dict[str, Any]) -> str:
    if data.get("error"):
        return f"error: {data['error']}"
    lines = [f"Board: {data.get('board')}  tasks={data.get('task_counts') or {}}"]
    lines.append(
        f"Bridged tasks: {data.get('bridged_tasks', 0)}   synced: {data.get('synced_tasks', 0)}"
    )
    hook = data.get("webhook") or {}
    lines.append(
        f"Webhook: {data.get('webhook_route')} -> {data.get('webhook_url')}"
        f"  secret={'set' if data.get('webhook_secret_set') else 'MISSING'}"
    )
    if hook.get("ok"):
        lines.append(f"         route registered, events {hook.get('configured_events')}")
    elif hook.get("problem"):
        lines.append(f"         PROBLEM: {hook['problem']}")
        if hook.get("fix"):
            lines.append(f"         fix: {hook['fix']}")
    lines.append("")
    lines.append("Hosts")
    rows = []
    for host in data.get("hosts") or []:
        state = host.get("authenticated_as") or ("token missing" if not host.get("token_present") else host.get("error", "?"))
        rows.append([host.get("alias", ""), host.get("url", ""), host.get("token_env", ""), str(state)])
    lines.append(_table(rows, ["ALIAS", "URL", "TOKEN_ENV", "AUTH"]))
    lines.append("")
    lines.append("Projects")
    rows = [[p.get("host", ""), p.get("path", ""), p.get("board", "")] for p in data.get("projects") or []]
    lines.append(_table(rows, ["HOST", "PROJECT", "BOARD"]))
    return "\n".join(lines)


def handle_slash(raw_args: str = "") -> str:
    """Handle ``/gitlab-kanban <action>`` in-session."""
    parts = (raw_args or "").strip().split()
    action = parts[0].lower() if parts else "status"
    rest = parts[1:]

    if action in ("help", "-h", "--help"):
        return HELP

    if action == "status":
        return _render_status(_call(tools.gitlab_kanban_status, {}))

    if action in ("projects", "project"):
        data = _call(tools.gitlab_kanban_project, {"action": "list"})
        rows = [
            [p.get("host", ""), p.get("path", ""), p.get("board_slug", "(default)"), str(p.get("webhook_id") or "-")]
            for p in data.get("projects") or []
        ]
        return _table(rows, ["HOST", "PROJECT", "BOARD", "HOOK"]) + f"\n\nWebhook URL: {data.get('webhook_url')}"

    if action == "board":
        data = _call(tools.gitlab_kanban_task, {"action": "list"})
        rows = []
        for task in data.get("tasks") or []:
            link = task.get("gitlab") or {}
            ref = f"{link.get('project','')}#{link.get('iid','')}" if link else "-"
            rows.append([task.get("id", ""), task.get("status", ""), task.get("assignee") or "-", ref, (task.get("title") or "")[:50]])
        return f"Board: {data.get('board')}  ({data.get('count', 0)} tasks)\n\n" + _table(
            rows, ["ID", "STATUS", "ASSIGNEE", "GITLAB", "TITLE"]
        )

    if action in ("issues", "issue"):
        args: dict[str, Any] = {"action": "list", "limit": 20}
        if rest:
            args["project"] = rest[0]
        data = _call(tools.gitlab_issue, args)
        if data.get("error"):
            return f"error: {data['error']}"
        rows = [
            [str(i.get("iid")), i.get("state", ""), ",".join(i.get("labels") or []) or "-",
             i.get("milestone") or "-", (i.get("title") or "")[:50]]
            for i in data.get("issues") or []
        ]
        return f"{data.get('project')} ({data.get('host')}) — {data.get('count', 0)} open issues\n\n" + _table(
            rows, ["IID", "STATE", "LABELS", "MILESTONE", "TITLE"]
        )

    if action in ("mrs", "mr", "merge-requests"):
        args = {"action": "list", "limit": 20}
        if rest:
            args["project"] = rest[0]
        data = _call(tools.gitlab_merge_request, args)
        if data.get("error"):
            return f"error: {data['error']}"
        rows = [
            [str(m.get("iid")), m.get("state", ""), m.get("author") or "-",
             f"{m.get('source_branch','')}->{m.get('target_branch','')}", (m.get("title") or "")[:45]]
            for m in data.get("merge_requests") or []
        ]
        return f"{data.get('project')} — {data.get('count', 0)} open merge requests\n\n" + _table(
            rows, ["IID", "STATE", "AUTHOR", "BRANCHES", "TITLE"]
        )

    if action in ("sprints", "milestones"):
        args = {"action": "list", "limit": 20}
        if rest:
            args["project"] = rest[0]
        data = _call(tools.gitlab_milestone, args)
        if data.get("error"):
            return f"error: {data['error']}"
        rows = [
            [str(m.get("id")), m.get("state", ""), m.get("start_date") or "-", m.get("due_date") or "-", m.get("title", "")]
            for m in data.get("milestones") or []
        ]
        return _table(rows, ["ID", "STATE", "START", "DUE", "TITLE"])

    if action == "sync":
        dry = any(flag in ("--dry-run", "-n") for flag in rest)
        data = _call(tools.gitlab_kanban_sync, {"dry_run": dry})
        if data.get("error"):
            return f"error: {data['error']}"
        head = "DRY RUN — nothing written\n" if dry else ""
        return (
            f"{head}Board {data.get('board')}: {data.get('done_tasks', 0)} done, "
            f"{data.get('synced', 0)} synced, {data.get('skipped', 0)} skipped, "
            f"{len(data.get('failed') or [])} failed"
        )

    if action == "config":
        cfg = load_config()
        lines = [
            f"Config: {tools.config_path()}",
            f"Board: {cfg.get('board_slug')} ({cfg.get('board_name')})",
            f"Default host: {cfg.get('default_host')}",
            f"Default role: {cfg.get('default_role')}",
            "",
            "Role -> profile",
            _table([[k, v] for k, v in (cfg.get("role_profiles") or {}).items()], ["ROLE", "PROFILE"]),
            "",
            "Label -> role",
            _table([[k, v] for k, v in (cfg.get("label_roles") or {}).items()], ["LABEL", "ROLE"]),
            "",
            f"Ingest: {cfg.get('ingest')}",
            f"Sync back: {cfg.get('sync_back')}",
        ]
        return "\n".join(lines)

    if action == "webhook":
        cfg = load_config()
        hook = projects.webhook_status(cfg)
        lines = [
            f"Route:  {hook.get('route')}",
            f"URL:    {hook.get('url')}",
            f"Secret: {'set (GITLAB_WEBHOOK_SECRET)' if hook.get('secret_set') else 'MISSING — set GITLAB_WEBHOOK_SECRET in .env'}",
            "",
            "GitLab sends the event in the X-Gitlab-Event header, so the Hermes",
            "route must be subscribed with these exact values:",
            f"  {', '.join(hook.get('expected_events') or [])}",
            "",
            f"Registered: {hook.get('registered')}",
        ]
        if hook.get("registered"):
            lines.append(f"Configured events: {hook.get('configured_events')}")
            lines.append(f"Script: {hook.get('script')}")
        if hook.get("ok"):
            lines.append("\nStatus: OK")
        elif hook.get("problem"):
            lines.append(f"\nPROBLEM: {hook['problem']}")
            if hook.get("fix"):
                lines.append(f"\nFix:\n  {hook['fix']}")
        lines.append(f"\nSync state: {bridge.sync_state_path()}")
        return "\n".join(lines)

    return f"Unknown action: {action}\n\n{HELP}"
