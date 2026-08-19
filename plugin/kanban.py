"""Kanban side of the bridge: board/task operations via the ``hermes kanban`` CLI.

The plugin never touches ``kanban.db`` directly — it drives the framework CLI so
board isolation, claim atomicity, and dispatch stay the framework's job.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

TIMEOUT = 45
# Marker line embedded in every bridged task body. It is the join key between a
# kanban task and its GitLab object, and it is what makes sync-back possible
# without a second database.
LINK_PREFIX = "gitlab-link:"
LINK_RE = re.compile(
    r"gitlab-link:\s*(?P<host>\S+)\s+(?P<kind>issue|merge_request|milestone)\s+"
    r"(?P<project>\S+)\s+(?P<iid>\d+)"
)


def link_line(host: str, kind: str, project: str, iid: int | str) -> str:
    """Build the machine-readable link marker for a task body."""
    return f"{LINK_PREFIX} {host} {kind} {project} {iid}"


def parse_link(body: str) -> dict[str, Any] | None:
    """Extract the GitLab link from a task body, if present."""
    if not body:
        return None
    m = LINK_RE.search(body)
    if not m:
        return None
    return {
        "host": m.group("host"),
        "kind": m.group("kind"),
        "project": m.group("project"),
        "iid": int(m.group("iid")),
    }


def idempotency_key(host: str, kind: str, project: str, iid: int | str) -> str:
    """Stable dedup key so a redelivered webhook cannot create a second task."""
    return f"gitlab:{host}:{kind}:{project}:{iid}"


def run(args: list[str], *, board: str | None = None) -> tuple[int, str, str]:
    """Run ``hermes kanban [--board <slug>] <args>``."""
    cmd = ["hermes", "kanban"]
    if board:
        cmd += ["--board", board]
    cmd += args
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)
    return res.returncode, res.stdout.strip(), res.stderr.strip()


def run_json(args: list[str], *, board: str | None = None) -> tuple[int, Any, str]:
    """Run a kanban command with ``--json`` and parse the result."""
    code, out, err = run(args + ["--json"], board=board)
    if code != 0:
        return code, None, err or out
    try:
        return code, json.loads(out), ""
    except json.JSONDecodeError:
        return code, None, f"unparseable kanban output: {out[:300]}"


def ensure_board(slug: str, name: str) -> bool:
    """Create the board if it is missing. Idempotent."""
    code, data, _ = run_json(["boards", "list"])
    if code == 0 and data is not None:
        boards = data if isinstance(data, list) else data.get("boards", [])
        for board in boards:
            if isinstance(board, dict) and board.get("slug") == slug:
                return True
    code, _, _ = run(["boards", "create", slug, "--name", name])
    return code == 0


def list_tasks(board: str, status: str | None = None, include_archived: bool = False) -> list[dict[str, Any]]:
    """List tasks on a board."""
    args = ["list"]
    if status:
        args += ["--status", status]
    if include_archived:
        args.append("--archived")
    code, data, _ = run_json(args, board=board)
    if code != 0 or data is None:
        return []
    tasks = data if isinstance(data, list) else data.get("tasks", [])
    return [t for t in tasks if isinstance(t, dict)]


def show_task(board: str, task_id: str) -> dict[str, Any] | None:
    """Fetch one task."""
    code, data, _ = run_json(["show", task_id], board=board)
    if code != 0 or data is None:
        return None
    if isinstance(data, dict) and "task" in data:
        return data["task"]
    return data if isinstance(data, dict) else None


def create_task(
    board: str,
    title: str,
    *,
    body: str = "",
    assignee: str | None = None,
    priority: int = 50,
    idem_key: str | None = None,
    skills: list[str] | None = None,
    triage: bool = False,
) -> tuple[bool, dict[str, Any] | str]:
    """Create a task. Returns ``(ok, task_dict_or_error)``."""
    args = ["create", title, "--body", body, "--priority", str(priority)]
    if assignee:
        args += ["--assignee", assignee]
    if idem_key:
        args += ["--idempotency-key", idem_key]
    for skill in skills or []:
        args += ["--skill", skill]
    if triage:
        args.append("--triage")
    code, data, err = run_json(args, board=board)
    if code != 0:
        return False, err or "kanban create failed"
    if isinstance(data, dict):
        return True, data.get("task", data)
    return True, {"raw": data}


def comment_task(board: str, task_id: str, body: str) -> bool:
    code, _, _ = run(["comment", task_id, body], board=board)
    return code == 0


def assign_task(board: str, task_id: str, assignee: str) -> tuple[bool, str]:
    code, out, err = run(["assign", task_id, assignee], board=board)
    return code == 0, err or out


def complete_task(board: str, task_id: str, result: str | None = None) -> tuple[bool, str]:
    args = ["complete", task_id]
    if result:
        args += ["--result", result]
    code, out, err = run(args, board=board)
    return code == 0, err or out


def find_task_by_link(
    board: str, host: str, kind: str, project: str, iid: int | str
) -> dict[str, Any] | None:
    """Find an existing bridged task for a GitLab object."""
    want = (host, kind, str(project).strip("/"), int(iid))
    for task in list_tasks(board, include_archived=True):
        link = parse_link(task.get("body") or "")
        if not link:
            continue
        if (link["host"], link["kind"], link["project"].strip("/"), link["iid"]) == want:
            return task
    return None
