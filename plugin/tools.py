"""Tool handlers for the gitlab-kanban plugin.

Contract: every handler takes ``(args: dict, **kwargs)`` and returns a JSON
string — for success AND for failure. A handler must never raise, because a
raising handler takes the whole agent turn down.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from . import bridge, kanban, projects
from .api import GitLabClient, GitLabError
from .config import (
    DEFAULT_ROLE_PROFILES,
    default_config,
    find_project,
    load_config,
    profile_for_role,
    save_config,
)
from .paths import config_path


def _json(payload: Any) -> str:
    try:
        return json.dumps(payload, default=str)
    except (TypeError, ValueError):
        return json.dumps({"error": "unserialisable result"})


def safe(fn: Callable[..., Any]) -> Callable[..., str]:
    """Wrap a handler so no exception escapes and the result is always JSON."""

    def wrapper(args: dict[str, Any] | None = None, **kwargs: Any) -> str:
        try:
            return _json(fn(args or {}, **kwargs))
        except GitLabError as exc:
            return _json({"error": str(exc), "status": exc.status, "body": exc.body})
        except Exception as exc:  # noqa: BLE001 - handler must never raise
            return _json({"error": f"{type(exc).__name__}: {exc}"})

    wrapper.__name__ = getattr(fn, "__name__", "handler")
    wrapper.__doc__ = fn.__doc__
    return wrapper


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _resolve_project(cfg: dict[str, Any], args: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """Resolve (project_path, host_alias, error).

    Falls back to the single onboarded project when the caller omitted one, so
    a one-project setup does not need to repeat itself on every call.
    """
    path = (args.get("project") or "").strip().strip("/")
    host = args.get("host")
    entries = cfg.get("projects") or []
    if not path:
        if len(entries) == 1:
            return entries[0].get("path"), host or entries[0].get("host"), None
        if not entries:
            return None, None, "no project given and none onboarded (use gitlab_kanban_project action=onboard)"
        return None, None, (
            "project required — onboarded: "
            + ", ".join(f"{p.get('host')}:{p.get('path')}" for p in entries)
        )
    if not host:
        entry = find_project(cfg, path)
        if entry:
            host = entry.get("host")
    return path, host, None


def _csv(value: Any) -> list[str] | None:
    if value in (None, ""):
        return None
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _board(cfg: dict[str, Any], args: dict[str, Any]) -> str:
    return str(args.get("board") or cfg.get("board_slug") or "gitlab")


def _bridge_task_for(
    cfg: dict[str, Any],
    *,
    host: str,
    kind: str,
    project: str,
    obj: dict[str, Any],
    role: str | None,
    labels: list[str],
) -> dict[str, Any]:
    """Create a kanban task for an existing GitLab object (idempotently)."""
    entry = find_project(cfg, project, host)
    from .config import board_for_project, role_for_labels

    board_slug, board_name = board_for_project(cfg, entry)
    resolved_role = role or role_for_labels(cfg, labels, entry)
    assignee = profile_for_role(cfg, resolved_role)
    iid = obj.get("iid")
    kanban.ensure_board(board_slug, board_name)
    event = {
        "kind": kind,
        "iid": iid,
        "title": obj.get("title") or "",
        "description": obj.get("description") or "",
        "url": obj.get("web_url") or "",
        "labels": labels,
        "project_path": project,
        "author": (obj.get("author") or {}).get("username", ""),
        "source_branch": obj.get("source_branch") or "",
        "target_branch": obj.get("target_branch") or "",
    }
    body = bridge.build_task_body(event, host, resolved_role, labels)
    ok, result = kanban.create_task(
        board_slug,
        event["title"] or f"{kind} {iid}",
        body=body,
        assignee=assignee,
        priority=int(cfg.get("priority", 50)),
        idem_key=kanban.idempotency_key(host, kind, project, int(iid or 0)),
        skills=bridge.ROLE_SKILLS.get(resolved_role, []),
    )
    if not ok:
        return {"error": str(result)}
    return {
        "bridged": True,
        "board": board_slug,
        "role": resolved_role,
        "assignee": assignee,
        "task": result,
        "gitlab": {"host": host, "kind": kind, "project": project, "iid": iid},
    }


# --------------------------------------------------------------------------
# gitlab_kanban_status
# --------------------------------------------------------------------------


def _status(args: dict[str, Any], **_: Any) -> dict[str, Any]:
    cfg = load_config()
    board = _board(cfg, args)
    tasks = kanban.list_tasks(board)
    counts: dict[str, int] = {}
    bridged = 0
    for task in tasks:
        counts[str(task.get("status", "?"))] = counts.get(str(task.get("status", "?")), 0) + 1
        if kanban.parse_link(task.get("body") or ""):
            bridged += 1
    state = bridge.load_sync_state()
    return {
        "config_file": str(config_path()),
        "config_exists": config_path().exists(),
        "board": board,
        "task_counts": counts,
        "bridged_tasks": bridged,
        "synced_tasks": sum(1 for v in state.values() if isinstance(v, dict) and v.get("ok")),
        "webhook_route": projects.webhook_route(cfg),
        "webhook_url": projects.public_webhook_url(cfg),
        "webhook_secret_set": bool(projects.webhook_secret()),
        "webhook": projects.webhook_status(cfg),
        "ingest": cfg.get("ingest"),
        "sync_back": cfg.get("sync_back"),
        "projects": [
            {"host": p.get("host"), "path": p.get("path"), "board": p.get("board_slug") or board}
            for p in cfg.get("projects") or []
        ],
        **projects.host_status(),
    }


# --------------------------------------------------------------------------
# gitlab_kanban_config
# --------------------------------------------------------------------------


def _config(args: dict[str, Any], **_: Any) -> dict[str, Any]:
    action = str(args.get("action") or "show")
    if action == "reset":
        save_config(default_config())
        return {"reset": True, "config_file": str(config_path())}

    cfg = load_config()
    if action == "show":
        return {"config_file": str(config_path()), "config": cfg}

    if action == "set-board":
        if args.get("board_slug"):
            cfg["board_slug"] = str(args["board_slug"])
        if args.get("board_name"):
            cfg["board_name"] = str(args["board_name"])
        save_config(cfg)
        return {"updated": "board", "board_slug": cfg["board_slug"], "board_name": cfg["board_name"]}

    if action == "add-host":
        alias = str(args.get("host") or "").strip()
        url = str(args.get("url") or "").strip().rstrip("/")
        if not alias or not url:
            return {"error": "host alias and url are required"}
        if not url.startswith(("http://", "https://")):
            return {"error": "url must start with http:// or https://"}
        cfg.setdefault("hosts", {})[alias] = {
            "url": url,
            "token_env": str(args.get("token_env") or "GITLAB_TOKEN"),
            "verify_ssl": bool(args.get("verify_ssl", True)),
        }
        save_config(cfg)
        return {
            "updated": "hosts",
            "host": alias,
            "url": url,
            "token_env": cfg["hosts"][alias]["token_env"],
            "note": f"put the token in <hermes_home>/.env as {cfg['hosts'][alias]['token_env']}=...",
        }

    if action == "remove-host":
        alias = str(args.get("host") or "")
        if alias not in (cfg.get("hosts") or {}):
            return {"error": f"unknown host alias '{alias}'"}
        if any(p.get("host") == alias for p in cfg.get("projects") or []):
            return {"error": f"host '{alias}' still has onboarded projects; remove them first"}
        cfg["hosts"].pop(alias)
        if cfg.get("default_host") == alias:
            cfg["default_host"] = next(iter(cfg["hosts"]), "gitlab.com")
        save_config(cfg)
        return {"removed_host": alias, "default_host": cfg.get("default_host")}

    if action == "set-default-host":
        alias = str(args.get("host") or "")
        if alias not in (cfg.get("hosts") or {}):
            return {"error": f"unknown host alias '{alias}'"}
        cfg["default_host"] = alias
        save_config(cfg)
        return {"default_host": alias}

    if action == "set-role-profile":
        role, profile = str(args.get("role") or ""), str(args.get("profile") or "")
        if not role or not profile:
            return {"error": "role and profile are required"}
        cfg.setdefault("role_profiles", {})[role] = profile
        save_config(cfg)
        return {"role_profiles": cfg["role_profiles"]}

    if action == "set-label-role":
        label, role = str(args.get("label") or ""), str(args.get("role") or "")
        if not label or not role:
            return {"error": "label and role are required"}
        if role not in (cfg.get("role_profiles") or DEFAULT_ROLE_PROFILES):
            return {
                "error": f"unknown role '{role}'",
                "known_roles": sorted(cfg.get("role_profiles") or DEFAULT_ROLE_PROFILES),
            }
        cfg.setdefault("label_roles", {})[label] = role
        save_config(cfg)
        return {"label_roles": cfg["label_roles"]}

    if action == "add-auto-label":
        rule = str(args.get("rule") or "")
        if "=>" not in rule:
            return {"error": "rule must look like '<title|body|label> contains <text> => label1,label2'"}
        match, _, labels = rule.partition("=>")
        cfg.setdefault("auto_label_rules", []).append(
            {"match": match.strip(), "labels": _csv(labels) or []}
        )
        save_config(cfg)
        return {"auto_label_rules": cfg["auto_label_rules"]}

    if action == "set-ingest":
        ingest = cfg.setdefault("ingest", {})
        for key in ("issues", "merge_requests", "milestones"):
            if key in args:
                ingest[key] = bool(args[key])
        save_config(cfg)
        return {"ingest": ingest}

    if action == "set-sync-back":
        sync = cfg.setdefault("sync_back", {})
        for key in ("comment", "close_issue", "close_merge_request", "backfill_missing"):
            if key in args:
                sync[key] = bool(args[key])
        if "labels_on_done" in args:
            sync["labels_on_done"] = _csv(args["labels_on_done"]) or []
        save_config(cfg)
        return {"sync_back": sync}

    if action == "set-style":
        style = cfg.setdefault("style", {})
        for key in ("tone", "format", "language"):
            if args.get(key):
                style[key] = str(args[key])
        save_config(cfg)
        return {"style": style}

    return {"error": f"unknown action '{action}'"}


# --------------------------------------------------------------------------
# gitlab_kanban_project
# --------------------------------------------------------------------------


def _project(args: dict[str, Any], **_: Any) -> dict[str, Any]:
    action = str(args.get("action") or "")
    if action == "list":
        return projects.list_projects()
    path = str(args.get("project") or "")
    if action == "onboard":
        if not path:
            return {"error": "project path required (group/repo)"}
        return projects.onboard(
            path,
            host=args.get("host"),
            board_slug=args.get("board_slug"),
            default_role=args.get("default_role"),
            url=args.get("url"),
            register_hook=bool(args.get("register_hook", True)),
        )
    if action == "remove":
        if not path:
            return {"error": "project path required (group/repo)"}
        return projects.remove(path, host=args.get("host"))
    return {"error": "action must be onboard, remove, or list"}


# --------------------------------------------------------------------------
# gitlab_kanban_sync
# --------------------------------------------------------------------------


def _sync(args: dict[str, Any], **_: Any) -> dict[str, Any]:
    cfg = load_config()
    board = _board(cfg, args)
    dry_run = bool(args.get("dry_run"))
    task_id = args.get("task_id")
    if task_id:
        task = kanban.show_task(board, str(task_id))
        if task is None:
            return {"error": f"task {task_id} not found on board {board}"}
        state = bridge.load_sync_state()
        result = bridge.sync_back_task(board, task, cfg=cfg, state=state, dry_run=dry_run)
        if result.get("synced"):
            bridge.save_sync_state(state)
        return result
    return bridge.sweep(board, dry_run=dry_run)


# --------------------------------------------------------------------------
# gitlab_kanban_task
# --------------------------------------------------------------------------


def _task(args: dict[str, Any], **_: Any) -> dict[str, Any]:
    cfg = load_config()
    board = _board(cfg, args)
    action = str(args.get("action") or "list")

    if action == "list":
        tasks = kanban.list_tasks(board, status=args.get("status"))
        return {
            "board": board,
            "count": len(tasks),
            "tasks": [
                {
                    "id": t.get("id"),
                    "title": t.get("title"),
                    "status": t.get("status"),
                    "assignee": t.get("assignee"),
                    "gitlab": kanban.parse_link(t.get("body") or ""),
                }
                for t in tasks
            ],
        }

    if action == "show":
        if not args.get("task_id"):
            return {"error": "task_id required"}
        task = kanban.show_task(board, str(args["task_id"]))
        if task is None:
            return {"error": f"task {args['task_id']} not found on board {board}"}
        return {"board": board, "task": task, "gitlab": kanban.parse_link(task.get("body") or "")}

    if action == "create":
        title = str(args.get("title") or "").strip()
        if not title:
            return {"error": "title required"}
        role = args.get("role")
        assignee = args.get("assignee") or (profile_for_role(cfg, str(role)) if role else None)
        kanban.ensure_board(board, str(cfg.get("board_name") or board))
        ok, result = kanban.create_task(
            board,
            title,
            body=str(args.get("body") or ""),
            assignee=assignee,
            priority=int(args.get("priority") or cfg.get("priority", 50)),
            skills=bridge.ROLE_SKILLS.get(str(role), []) if role else None,
        )
        return {"created": ok, "board": board, "role": role, "assignee": assignee, "task": result} if ok else {"error": str(result)}

    if action == "assign":
        if not args.get("task_id"):
            return {"error": "task_id required"}
        assignee = args.get("assignee")
        if not assignee and args.get("role"):
            assignee = profile_for_role(cfg, str(args["role"]))
        if not assignee:
            return {"error": "role or assignee required"}
        ok, message = kanban.assign_task(board, str(args["task_id"]), str(assignee))
        return {"assigned": ok, "task_id": args["task_id"], "assignee": assignee, "message": message}

    if action == "comment":
        if not args.get("task_id") or not args.get("body"):
            return {"error": "task_id and body required"}
        ok = kanban.comment_task(board, str(args["task_id"]), str(args["body"]))
        return {"commented": ok, "task_id": args["task_id"]}

    if action == "complete":
        if not args.get("task_id"):
            return {"error": "task_id required"}
        ok, message = kanban.complete_task(board, str(args["task_id"]), args.get("body"))
        return {"completed": ok, "task_id": args["task_id"], "message": message}

    return {"error": f"unknown action '{action}'"}


# --------------------------------------------------------------------------
# gitlab_issue
# --------------------------------------------------------------------------


def _issue(args: dict[str, Any], **_: Any) -> dict[str, Any]:
    cfg = load_config()
    action = str(args.get("action") or "")
    path, host, err = _resolve_project(cfg, args)
    if err or not path:
        return {"error": err or "project could not be resolved"}
    host = str(host or cfg.get("default_host") or "gitlab.com")
    client = GitLabClient.for_host(host, cfg)
    limit = int(args.get("limit") or 20)

    if action == "list":
        issues = client.list_issues(
            path,
            limit=limit,
            state=args.get("state") or "opened",
            search=args.get("search"),
            labels=",".join(_csv(args.get("labels")) or []) or None,
            milestone=args.get("milestone"),
        )
        return {
            "project": path,
            "host": host,
            "count": len(issues),
            "issues": [
                {
                    "iid": i.get("iid"),
                    "title": i.get("title"),
                    "state": i.get("state"),
                    "labels": i.get("labels"),
                    "assignees": [a.get("username") for a in i.get("assignees") or []],
                    "milestone": (i.get("milestone") or {}).get("title"),
                    "web_url": i.get("web_url"),
                }
                for i in issues
            ],
        }

    iid = args.get("iid")
    if action in ("get", "update", "close", "reopen", "comment", "delete", "notes", "to-kanban") and not iid:
        return {"error": "iid required"}

    if action == "get":
        return {"project": path, "host": host, "issue": client.get_issue(path, int(iid))}

    if action == "create":
        title = str(args.get("title") or "").strip()
        if not title:
            return {"error": "title required"}
        payload: dict[str, Any] = {"title": title}
        if args.get("description"):
            payload["description"] = str(args["description"])
        if args.get("labels"):
            payload["labels"] = ",".join(_csv(args["labels"]) or [])
        if args.get("milestone_id"):
            payload["milestone_id"] = int(args["milestone_id"])
        if args.get("due_date"):
            payload["due_date"] = str(args["due_date"])
        if args.get("weight") is not None:
            payload["weight"] = int(args["weight"])
        if args.get("assignee"):
            payload["assignee_ids"] = _lookup_user_ids(client, path, args["assignee"])
        issue = client.create_issue(path, payload)
        return {"created": True, "project": path, "host": host, "issue": _slim_issue(issue)}

    if action in ("update", "close", "reopen"):
        payload = {}
        if action == "close":
            payload["state_event"] = "close"
        elif action == "reopen":
            payload["state_event"] = "reopen"
        for key in ("title", "description", "due_date"):
            if args.get(key):
                payload[key] = str(args[key])
        if args.get("labels"):
            payload["labels"] = ",".join(_csv(args["labels"]) or [])
        if args.get("milestone_id") is not None:
            payload["milestone_id"] = int(args["milestone_id"])
        if args.get("weight") is not None:
            payload["weight"] = int(args["weight"])
        if args.get("assignee"):
            payload["assignee_ids"] = _lookup_user_ids(client, path, args["assignee"])
        if not payload:
            return {"error": "nothing to update"}
        issue = client.update_issue(path, int(iid), payload)
        return {"updated": True, "action": action, "issue": _slim_issue(issue)}

    if action == "comment":
        if not args.get("body"):
            return {"error": "body required"}
        note = client.comment_issue(path, int(iid), str(args["body"]))
        return {"commented": True, "note_id": note.get("id"), "iid": iid}

    if action == "notes":
        notes = client.issue_notes(path, int(iid), limit=limit)
        return {
            "iid": iid,
            "count": len(notes),
            "notes": [
                {
                    "id": n.get("id"),
                    "author": (n.get("author") or {}).get("username"),
                    "created_at": n.get("created_at"),
                    "body": (n.get("body") or "")[:1000],
                }
                for n in notes
            ],
        }

    if action == "delete":
        status = client.delete_issue(path, int(iid))
        return {"deleted": status in (200, 202, 204), "status": status, "iid": iid}

    if action == "to-kanban":
        issue = client.get_issue(path, int(iid))
        return _bridge_task_for(
            cfg,
            host=str(host),
            kind="issue",
            project=str(path),
            obj=issue,
            role=args.get("role"),
            labels=[str(x) for x in issue.get("labels") or []],
        )

    return {"error": f"unknown action '{action}'"}


def _slim_issue(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "iid": issue.get("iid"),
        "title": issue.get("title"),
        "state": issue.get("state"),
        "labels": issue.get("labels"),
        "milestone": (issue.get("milestone") or {}).get("title"),
        "web_url": issue.get("web_url"),
    }


def _lookup_user_ids(client: GitLabClient, project: str, username: Any) -> list[int]:
    """Resolve GitLab usernames to member ids for assignment."""
    wanted = {str(u).strip().lstrip("@").lower() for u in (_csv(username) or [])}
    ids: list[int] = []
    for member in client.project_members(project):
        if str(member.get("username", "")).lower() in wanted and member.get("id"):
            ids.append(int(member["id"]))
    return ids


# --------------------------------------------------------------------------
# gitlab_merge_request
# --------------------------------------------------------------------------


def _merge_request(args: dict[str, Any], **_: Any) -> dict[str, Any]:
    cfg = load_config()
    action = str(args.get("action") or "")
    path, host, err = _resolve_project(cfg, args)
    if err or not path:
        return {"error": err or "project could not be resolved"}
    host = str(host or cfg.get("default_host") or "gitlab.com")
    client = GitLabClient.for_host(host, cfg)
    limit = int(args.get("limit") or 20)

    if action == "list":
        mrs = client.list_merge_requests(
            path,
            limit=limit,
            state=args.get("state") or "opened",
            labels=",".join(_csv(args.get("labels")) or []) or None,
            target_branch=args.get("target_branch"),
        )
        return {
            "project": path,
            "host": host,
            "count": len(mrs),
            "merge_requests": [_slim_mr(m) for m in mrs],
        }

    iid = args.get("iid")
    if action != "create" and not iid:
        return {"error": "iid required"}

    if action == "get":
        return {"project": path, "host": host, "merge_request": client.get_merge_request(path, int(iid))}

    if action == "create":
        source = str(args.get("source_branch") or "").strip()
        title = str(args.get("title") or "").strip()
        if not source or not title:
            return {"error": "source_branch and title required"}
        target = args.get("target_branch")
        if not target:
            target = client.project(path).get("default_branch") or "main"
        payload: dict[str, Any] = {
            "source_branch": source,
            "target_branch": str(target),
            "title": ("Draft: " + title) if args.get("draft") and not title.lower().startswith("draft:") else title,
        }
        if args.get("description"):
            payload["description"] = str(args["description"])
        if args.get("labels"):
            payload["labels"] = ",".join(_csv(args["labels"]) or [])
        if args.get("milestone_id"):
            payload["milestone_id"] = int(args["milestone_id"])
        if args.get("remove_source_branch") is not None:
            payload["remove_source_branch"] = bool(args["remove_source_branch"])
        if args.get("assignee"):
            payload["assignee_ids"] = _lookup_user_ids(client, path, args["assignee"])
        if args.get("reviewer"):
            payload["reviewer_ids"] = _lookup_user_ids(client, path, args["reviewer"])
        mr = client.create_merge_request(path, payload)
        return {"created": True, "project": path, "host": host, "merge_request": _slim_mr(mr)}

    if action in ("update", "close", "reopen"):
        payload = {}
        if action == "close":
            payload["state_event"] = "close"
        elif action == "reopen":
            payload["state_event"] = "reopen"
        for key in ("title", "description", "target_branch"):
            if args.get(key):
                payload[key] = str(args[key])
        if args.get("labels"):
            payload["labels"] = ",".join(_csv(args["labels"]) or [])
        if args.get("milestone_id") is not None:
            payload["milestone_id"] = int(args["milestone_id"])
        if args.get("assignee"):
            payload["assignee_ids"] = _lookup_user_ids(client, path, args["assignee"])
        if args.get("reviewer"):
            payload["reviewer_ids"] = _lookup_user_ids(client, path, args["reviewer"])
        if not payload:
            return {"error": "nothing to update"}
        mr = client.update_merge_request(path, int(iid), payload)
        return {"updated": True, "action": action, "merge_request": _slim_mr(mr)}

    if action == "comment":
        if not args.get("body"):
            return {"error": "body required"}
        note = client.comment_merge_request(path, int(iid), str(args["body"]))
        return {"commented": True, "note_id": note.get("id"), "iid": iid}

    if action == "approve":
        result = client.approve_merge_request(path, int(iid))
        return {"approved": True, "iid": iid, "approvals": result.get("approvals_left")}

    if action == "merge":
        payload = {}
        if args.get("squash") is not None:
            payload["squash"] = bool(args["squash"])
        if args.get("remove_source_branch") is not None:
            payload["should_remove_source_branch"] = bool(args["remove_source_branch"])
        if args.get("merge_commit_message"):
            payload["merge_commit_message"] = str(args["merge_commit_message"])
        mr = client.merge_merge_request(path, int(iid), payload)
        return {"merged": mr.get("state") == "merged", "merge_request": _slim_mr(mr)}

    if action == "changes":
        data = client.merge_request_changes(path, int(iid))
        changes = data.get("changes") or []
        return {
            "iid": iid,
            "title": data.get("title"),
            "changed_files": len(changes),
            "files": [
                {
                    "old_path": c.get("old_path"),
                    "new_path": c.get("new_path"),
                    "new_file": c.get("new_file"),
                    "deleted_file": c.get("deleted_file"),
                    "renamed_file": c.get("renamed_file"),
                    "diff": (c.get("diff") or "")[:4000],
                }
                for c in changes[:40]
            ],
        }

    if action == "to-kanban":
        mr = client.get_merge_request(path, int(iid))
        return _bridge_task_for(
            cfg,
            host=str(host),
            kind="merge_request",
            project=str(path),
            obj=mr,
            role=args.get("role") or "reviewer",
            labels=[str(x) for x in mr.get("labels") or []],
        )

    return {"error": f"unknown action '{action}'"}


def _slim_mr(mr: dict[str, Any]) -> dict[str, Any]:
    return {
        "iid": mr.get("iid"),
        "title": mr.get("title"),
        "state": mr.get("state"),
        "draft": mr.get("draft", mr.get("work_in_progress")),
        "source_branch": mr.get("source_branch"),
        "target_branch": mr.get("target_branch"),
        "labels": mr.get("labels"),
        "author": (mr.get("author") or {}).get("username"),
        "reviewers": [r.get("username") for r in mr.get("reviewers") or []],
        "merge_status": mr.get("detailed_merge_status") or mr.get("merge_status"),
        "web_url": mr.get("web_url"),
    }


# --------------------------------------------------------------------------
# gitlab_milestone
# --------------------------------------------------------------------------


def _milestone(args: dict[str, Any], **_: Any) -> dict[str, Any]:
    cfg = load_config()
    action = str(args.get("action") or "")
    limit = int(args.get("limit") or 20)

    if action == "list" and args.get("group"):
        client = GitLabClient.for_host(args.get("host"), cfg)
        milestones = client.list_group_milestones(
            str(args["group"]), limit=limit, state=args.get("state")
        )
        return {"group": args["group"], "count": len(milestones), "milestones": [_slim_ms(m) for m in milestones]}

    path, host, err = _resolve_project(cfg, args)
    if err or not path:
        return {"error": err or "project could not be resolved"}
    host = str(host or cfg.get("default_host") or "gitlab.com")
    client = GitLabClient.for_host(host, cfg)

    if action == "list":
        milestones = client.list_milestones(path, limit=limit, state=args.get("state"))
        return {"project": path, "host": host, "count": len(milestones), "milestones": [_slim_ms(m) for m in milestones]}

    if action == "create":
        title = str(args.get("title") or "").strip()
        if not title:
            return {"error": "title required"}
        payload: dict[str, Any] = {"title": title}
        for key in ("description", "start_date", "due_date"):
            if args.get(key):
                payload[key] = str(args[key])
        ms = client.create_milestone(path, payload)
        return {"created": True, "milestone": _slim_ms(ms)}

    mid = args.get("milestone_id")
    if not mid:
        return {"error": "milestone_id required"}
    mid = int(mid)

    if action == "get":
        milestones = client.list_milestones(path, limit=100)
        for ms in milestones:
            if ms.get("id") == mid:
                return {"milestone": _slim_ms(ms)}
        return {"error": f"milestone {mid} not found in {path}"}

    if action in ("update", "close", "reopen"):
        payload = {}
        if action == "close":
            payload["state_event"] = "close"
        elif action == "reopen":
            payload["state_event"] = "activate"
        for key in ("title", "description", "start_date", "due_date"):
            if args.get(key):
                payload[key] = str(args[key])
        if not payload:
            return {"error": "nothing to update"}
        ms = client.update_milestone(path, mid, payload)
        return {"updated": True, "action": action, "milestone": _slim_ms(ms)}

    if action == "delete":
        status = client.delete_milestone(path, mid)
        return {"deleted": status in (200, 202, 204), "status": status, "milestone_id": mid}

    if action in ("issues", "progress"):
        issues = client.milestone_issues(path, mid, limit=200)
        if action == "issues":
            return {
                "milestone_id": mid,
                "count": len(issues),
                "issues": [_slim_issue(i) for i in issues[:limit]],
            }
        opened = [i for i in issues if i.get("state") == "opened"]
        closed = [i for i in issues if i.get("state") == "closed"]

        def weight(items: list[dict[str, Any]]) -> int:
            return sum(int(i.get("weight") or 0) for i in items)

        total = len(issues)
        return {
            "milestone_id": mid,
            "total_issues": total,
            "open": len(opened),
            "closed": len(closed),
            "percent_complete": round(100 * len(closed) / total, 1) if total else 0.0,
            "weight_total": weight(issues),
            "weight_closed": weight(closed),
            "unassigned_open": [
                _slim_issue(i) for i in opened if not (i.get("assignees") or i.get("assignee"))
            ],
            "open_issues": [_slim_issue(i) for i in opened[:limit]],
        }

    return {"error": f"unknown action '{action}'"}


def _slim_ms(ms: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": ms.get("id"),
        "iid": ms.get("iid"),
        "title": ms.get("title"),
        "state": ms.get("state"),
        "start_date": ms.get("start_date"),
        "due_date": ms.get("due_date"),
        "web_url": ms.get("web_url"),
    }


# --------------------------------------------------------------------------
# exported handlers
# --------------------------------------------------------------------------

gitlab_kanban_status = safe(_status)
gitlab_kanban_config = safe(_config)
gitlab_kanban_project = safe(_project)
gitlab_kanban_sync = safe(_sync)
gitlab_kanban_task = safe(_task)
gitlab_issue = safe(_issue)
gitlab_merge_request = safe(_merge_request)
gitlab_milestone = safe(_milestone)

HANDLERS = {
    "gitlab_kanban_status": gitlab_kanban_status,
    "gitlab_kanban_config": gitlab_kanban_config,
    "gitlab_kanban_project": gitlab_kanban_project,
    "gitlab_kanban_sync": gitlab_kanban_sync,
    "gitlab_kanban_task": gitlab_kanban_task,
    "gitlab_issue": gitlab_issue,
    "gitlab_merge_request": gitlab_merge_request,
    "gitlab_milestone": gitlab_milestone,
}
