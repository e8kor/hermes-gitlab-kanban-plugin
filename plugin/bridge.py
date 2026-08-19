"""Bridge logic: GitLab objects <-> kanban tasks.

Pure-ish functions over a payload and config so they can be tested without a
network or a live board. Side effects are confined to the ``ingest_*`` and
``sync_back_task`` entry points, which call into :mod:`kanban` and :mod:`api`.

Idempotency contract (both directions):

* Ingest uses a deterministic kanban ``--idempotency-key`` derived from
  host+kind+project+iid, so a redelivered webhook returns the existing task
  instead of creating a duplicate.
* Sync-back records synced task ids in a state file and refuses to act twice on
  the same task, so a cron sweep re-running is a no-op.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import kanban
from .api import GitLabClient, GitLabError
from .config import (
    apply_auto_labels,
    board_for_project,
    find_project,
    load_config,
    profile_for_role,
    resolve_host,
    role_for_labels,
)
from .paths import sync_state_path

# Role -> skills injected into the worker's session, so a dispatched agent has
# the right playbook loaded for the role it is playing.
ROLE_SKILLS: dict[str, list[str]] = {
    "developer": ["gitlab-kanban:gitlab-development"],
    "reviewer": ["gitlab-kanban:gitlab-code-review"],
    "qa": ["gitlab-kanban:gitlab-code-review"],
    "scrum-master": ["gitlab-kanban:gitlab-scrum-master"],
    "researcher": [],
    "writer": [],
}

BODY_LIMIT = 4000


# --------------------------------------------------------------------------
# sync state
# --------------------------------------------------------------------------


def load_sync_state() -> dict[str, Any]:
    path = sync_state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_sync_state(state: dict[str, Any]) -> Path:
    path = sync_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=str(path.parent), prefix=".gitlab-kanban-sync-", suffix=".tmp",
        delete=False, encoding="utf-8",
    ) as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")
        tmp = Path(fh.name)
    os.replace(tmp, path)
    return path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# payload normalization
# --------------------------------------------------------------------------


def normalize_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Reduce a GitLab webhook payload to the fields the bridge cares about.

    Returns ``None`` for payloads the bridge does not handle. Handles the
    ``issue`` and ``merge_request`` object kinds (GitLab sends both under
    ``object_kind`` with the body in ``object_attributes``).
    """
    if not isinstance(payload, dict):
        return None
    kind = payload.get("object_kind") or payload.get("event_type")
    if kind not in ("issue", "merge_request"):
        return None
    attrs = payload.get("object_attributes")
    if not isinstance(attrs, dict):
        return None

    project = payload.get("project") or {}
    project_path = (
        project.get("path_with_namespace")
        or payload.get("repository", {}).get("name")
        or ""
    )
    labels = []
    for lab in payload.get("labels") or attrs.get("labels") or []:
        if isinstance(lab, dict):
            name = lab.get("title") or lab.get("name")
        else:
            name = lab
        if name:
            labels.append(str(name))

    return {
        "kind": "merge_request" if kind == "merge_request" else "issue",
        "action": str(attrs.get("action") or "").lower(),
        "state": str(attrs.get("state") or "").lower(),
        "iid": attrs.get("iid"),
        "title": attrs.get("title") or "",
        "description": attrs.get("description") or "",
        "url": attrs.get("url") or "",
        "labels": labels,
        "project_path": str(project_path).strip("/"),
        "project_url": project.get("web_url") or "",
        "author": (payload.get("user") or {}).get("username") or "",
        "milestone_id": attrs.get("milestone_id"),
        "source_branch": attrs.get("source_branch") or "",
        "target_branch": attrs.get("target_branch") or "",
    }


def host_alias_for_url(cfg: dict[str, Any], project_url: str) -> str | None:
    """Match a project web_url against the configured hosts."""
    if not project_url:
        return None
    for alias, entry in (cfg.get("hosts") or {}).items():
        url = str(entry.get("url", "")).rstrip("/")
        if url and project_url.startswith(url):
            return alias
    return None


# --------------------------------------------------------------------------
# task body composition
# --------------------------------------------------------------------------


def build_task_body(event: dict[str, Any], host_alias: str, role: str, labels: list[str]) -> str:
    """Compose the kanban task body, including the machine-readable link line."""
    kind_label = "Merge request" if event["kind"] == "merge_request" else "Issue"
    lines = [
        kanban.link_line(host_alias, event["kind"], event["project_path"], event["iid"]),
        "",
        f"{kind_label} !{event['iid']}" if event["kind"] == "merge_request"
        else f"{kind_label} #{event['iid']}",
        f"Project: {event['project_path']} ({host_alias})",
        f"URL: {event['url']}" if event["url"] else "",
        f"Labels: {', '.join(labels) if labels else 'none'}",
        f"Role: {role}",
        f"Author: {event['author']}" if event.get("author") else "",
    ]
    if event["kind"] == "merge_request" and event.get("source_branch"):
        lines.append(f"Branch: {event['source_branch']} -> {event.get('target_branch', '')}")
    lines += ["", (event.get("description") or "")[:BODY_LIMIT]]
    return "\n".join(line for line in lines if line != "")


# --------------------------------------------------------------------------
# ingest
# --------------------------------------------------------------------------


def should_ingest(cfg: dict[str, Any], event: dict[str, Any]) -> tuple[bool, str]:
    """Decide whether an event creates a kanban task. Returns ``(ok, reason)``."""
    ingest = cfg.get("ingest") or {}
    if event["kind"] == "issue":
        if not ingest.get("issues", True):
            return False, "issue ingest disabled"
        allowed = [str(a).lower() for a in ingest.get("issue_actions") or ["open", "reopen"]]
    else:
        if not ingest.get("merge_requests", True):
            return False, "merge request ingest disabled"
        allowed = [
            str(a).lower() for a in ingest.get("merge_request_actions") or ["open", "reopen"]
        ]
    if event["action"] and event["action"] not in allowed:
        return False, f"action '{event['action']}' not in {allowed}"
    if not event.get("iid"):
        return False, "payload has no iid"

    projects = cfg.get("projects") or []
    if projects and not find_project(cfg, event["project_path"]):
        return False, f"project {event['project_path']} not onboarded"
    return True, "ok"


def ingest_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Turn one GitLab webhook payload into a kanban task (idempotently)."""
    cfg = load_config()
    event = normalize_event(payload)
    if event is None:
        return {"ignored": True, "reason": "unsupported payload"}

    ok, reason = should_ingest(cfg, event)
    if not ok:
        return {"ignored": True, "reason": reason}

    project = find_project(cfg, event["project_path"])
    host_alias = (
        (project or {}).get("host")
        or host_alias_for_url(cfg, event.get("project_url", ""))
        or cfg.get("default_host")
        or "gitlab.com"
    )
    labels = apply_auto_labels(cfg, event["title"], event["description"], event["labels"])
    role = role_for_labels(cfg, labels, project)
    assignee = profile_for_role(cfg, role)
    board_slug, board_name = board_for_project(cfg, project)

    kanban.ensure_board(board_slug, board_name)
    body = build_task_body(event, host_alias, role, labels)
    created, result = kanban.create_task(
        board_slug,
        event["title"],
        body=body,
        assignee=assignee,
        priority=int(cfg.get("priority", 50)),
        idem_key=kanban.idempotency_key(
            host_alias, event["kind"], event["project_path"], event["iid"]
        ),
        skills=ROLE_SKILLS.get(role, []),
    )
    if not created:
        return {"error": str(result)}
    return {
        "created": True,
        "board": board_slug,
        "role": role,
        "assignee": assignee,
        "labels": labels,
        "host": host_alias,
        "kind": event["kind"],
        "project": event["project_path"],
        "iid": event["iid"],
        "task": result,
    }


# --------------------------------------------------------------------------
# sync-back
# --------------------------------------------------------------------------


def build_sync_comment(task: dict[str, Any], style: dict[str, Any] | None = None) -> str:
    """Compose the GitLab note posted when a kanban task completes."""
    task_id = task.get("id") or "?"
    summary = (task.get("result") or task.get("summary") or "").strip()
    if not summary:
        summary = "_No result summary recorded._"
    return (
        f"**Completed by Hermes kanban** (task `{task_id}`)\n\n"
        f"{summary[:8000]}"
    )


def sync_back_task(
    board: str,
    task: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Push a completed kanban task's outcome back to its GitLab object.

    Idempotent: a task already recorded in the sync state is skipped. Never
    reopens or re-closes; only acts on the first successful sync.
    """
    cfg = cfg if cfg is not None else load_config()
    state = state if state is not None else load_sync_state()
    task_id = str(task.get("id") or "")
    if not task_id:
        return {"skipped": True, "reason": "task has no id"}
    if state.get(task_id, {}).get("ok"):
        return {"skipped": True, "reason": "already synced", "task_id": task_id}

    link = kanban.parse_link(task.get("body") or "")
    if not link:
        return {"skipped": True, "reason": "no gitlab-link marker in task body", "task_id": task_id}

    behaviour = cfg.get("sync_back") or {}
    actions: list[str] = []
    if dry_run:
        if behaviour.get("comment", True):
            actions.append("would comment")
        if link["kind"] == "issue" and behaviour.get("close_issue", True):
            actions.append("would close issue")
        if link["kind"] == "merge_request" and behaviour.get("close_merge_request", False):
            actions.append("would close merge request")
        return {"dry_run": True, "task_id": task_id, "link": link, "actions": actions}

    client = GitLabClient.for_host(link["host"], cfg)
    project, iid = link["project"], link["iid"]
    errors: list[str] = []

    if behaviour.get("comment", True):
        comment = build_sync_comment(task, cfg.get("style"))
        try:
            if link["kind"] == "issue":
                client.comment_issue(project, iid, comment)
            else:
                client.comment_merge_request(project, iid, comment)
            actions.append("commented")
        except GitLabError as exc:
            errors.append(f"comment: {exc}")

    labels_on_done = [str(x) for x in behaviour.get("labels_on_done") or []]
    close_issue = link["kind"] == "issue" and behaviour.get("close_issue", True)
    close_mr = link["kind"] == "merge_request" and behaviour.get("close_merge_request", False)

    if link["kind"] == "issue" and (close_issue or labels_on_done):
        payload: dict[str, Any] = {}
        if close_issue:
            payload["state_event"] = "close"
        if labels_on_done:
            payload["add_labels"] = ",".join(labels_on_done)
        try:
            client.update_issue(project, iid, payload)
            actions.append("closed issue" if close_issue else "labelled issue")
        except GitLabError as exc:
            errors.append(f"update issue: {exc}")
    elif link["kind"] == "merge_request" and (close_mr or labels_on_done):
        payload = {}
        if close_mr:
            payload["state_event"] = "close"
        if labels_on_done:
            payload["add_labels"] = ",".join(labels_on_done)
        try:
            client.update_merge_request(project, iid, payload)
            actions.append("closed merge request" if close_mr else "labelled merge request")
        except GitLabError as exc:
            errors.append(f"update merge request: {exc}")

    ok = bool(actions) and not errors
    if ok:
        state[task_id] = {"ok": True, "synced_at": _now(), "link": link, "actions": actions}
    return {
        "synced": ok,
        "task_id": task_id,
        "link": link,
        "actions": actions,
        "errors": errors,
    }


def sweep(board: str | None = None, *, dry_run: bool = False, limit: int = 100) -> dict[str, Any]:
    """Sync every not-yet-synced done task on the board back to GitLab."""
    cfg = load_config()
    board_slug = board or cfg.get("board_slug") or "gitlab"
    state = load_sync_state()
    done = kanban.list_tasks(board_slug, status="done")[:limit]
    results = []
    changed = False
    for task in done:
        res = sync_back_task(board_slug, task, cfg=cfg, state=state, dry_run=dry_run)
        results.append(res)
        if res.get("synced"):
            changed = True
    if changed and not dry_run:
        save_sync_state(state)
    return {
        "board": board_slug,
        "done_tasks": len(done),
        "synced": sum(1 for r in results if r.get("synced")),
        "skipped": sum(1 for r in results if r.get("skipped")),
        "failed": [r for r in results if r.get("errors")],
        "dry_run": dry_run,
        "results": results if dry_run else None,
    }
