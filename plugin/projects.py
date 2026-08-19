"""Project onboarding: register/remove GitLab webhooks and the allow-list.

A "project" is a GitLab repo (``group/sub/repo``) on a configured host whose
issues and merge requests flow into a Hermes kanban board. Onboarding registers
a project webhook pointing at the bridge's public URL and records the project in
the config's ``projects`` allow-list. The ingest script only creates tasks for
listed projects — an empty list means accept-all.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from typing import Any

from .api import GitLabClient, GitLabError
from .config import find_project, load_config, resolve_host, save_config
from .paths import hermes_home, read_env_value

DEFAULT_LOCAL_PORT = 8644

# GitLab announces the event in the ``X-Gitlab-Event`` header using human-readable
# names ("Issue Hook"), NOT the snake_case ``object_kind`` from the body
# ("issue"). Hermes' webhook route filters on that header, so a route subscribed
# with the object_kind names matches nothing and silently ignores every delivery.
# These are the exact strings a route must be subscribed with.
GITLAB_EVENT_HEADERS: dict[str, str] = {
    "issues": "Issue Hook",
    "merge_requests": "Merge Request Hook",
}


def webhook_route(cfg: dict[str, Any] | None = None) -> str:
    cfg = cfg or load_config()
    return str(cfg.get("webhook_route") or "gitlab-to-kanban")


def webhook_events(cfg: dict[str, Any] | None = None) -> list[str]:
    """The ``X-Gitlab-Event`` header values this bridge's route must accept."""
    cfg = cfg or load_config()
    ingest = cfg.get("ingest") or {}
    return [
        header
        for key, header in GITLAB_EVENT_HEADERS.items()
        if ingest.get(key, True)
    ]


def webhook_subscribe_command(cfg: dict[str, Any] | None = None) -> list[str]:
    """The exact ``hermes webhook subscribe`` argv for this bridge."""
    cfg = cfg or load_config()
    cmd = [
        "hermes", "webhook", "subscribe", webhook_route(cfg),
        "--events", ",".join(webhook_events(cfg)),
        "--script", "gitlab-to-kanban.py",
        "--description", "GitLab issues and merge requests to kanban",
    ]
    secret = webhook_secret()
    if secret:
        cmd += ["--secret", secret]
    return cmd


def webhook_status(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Check the Hermes webhook route against what GitLab actually sends.

    The failure this catches: a route subscribed with the wrong ``--events``
    values accepts the delivery at the HTTP layer and then silently ignores it,
    so nothing reaches the bridge and nothing appears on the board. Comparing the
    route's configured events against ``GITLAB_EVENT_HEADERS`` turns that silent
    dead end into a stated problem with a fix.
    """
    cfg = cfg or load_config()
    route = webhook_route(cfg)
    expected = webhook_events(cfg)
    out: dict[str, Any] = {
        "route": route,
        "url": public_webhook_url(cfg),
        "expected_events": expected,
        "secret_set": bool(webhook_secret()),
    }

    subs_path = hermes_home() / "webhook_subscriptions.json"
    if not subs_path.exists():
        out["registered"] = False
        out["problem"] = "no webhook subscriptions file — the route is not subscribed"
        out["fix"] = " ".join(_shell_quote(x) for x in webhook_subscribe_command(cfg))
        return out
    try:
        subs = json.loads(subs_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        out["registered"] = False
        out["problem"] = f"cannot read {subs_path}: {exc}"
        return out

    entry = (subs or {}).get(route)
    if not isinstance(entry, dict):
        out["registered"] = False
        out["problem"] = f"route '{route}' is not subscribed"
        out["fix"] = " ".join(_shell_quote(x) for x in webhook_subscribe_command(cfg))
        return out

    configured = [str(e) for e in entry.get("events") or []]
    out["registered"] = True
    out["configured_events"] = configured
    out["script"] = entry.get("script")
    out["route_secret_set"] = bool(entry.get("secret"))

    missing = [e for e in expected if e not in configured]
    if configured and missing:
        out["problem"] = (
            "the route's --events do not match the X-Gitlab-Event header GitLab "
            f"sends, so deliveries are accepted and then ignored. Missing: {missing}"
        )
        out["fix"] = (
            f"hermes webhook remove {route} && "
            + " ".join(_shell_quote(x) for x in webhook_subscribe_command(cfg))
        )
    elif entry.get("script") != "gitlab-to-kanban.py":
        out["problem"] = (
            f"the route runs script '{entry.get('script')}', not gitlab-to-kanban.py"
        )
    else:
        out["ok"] = True
    return out


def _shell_quote(value: str) -> str:
    """Quote an argv element for display in a copy-pasteable command."""
    return shlex.quote(str(value))


def public_webhook_url(cfg: dict[str, Any] | None = None) -> str:
    """Resolve the bridge's public webhook URL.

    Preference order: an explicit ``webhook_url`` in the config, then a zrok
    share detected from the systemd journal, then localhost (useful for
    curl-based testing).
    """
    cfg = cfg or load_config()
    route = webhook_route(cfg)
    explicit = str(cfg.get("webhook_url") or "").strip()
    if explicit:
        return explicit.rstrip("/") if explicit.endswith(route) else f"{explicit.rstrip('/')}/webhooks/{route}"
    try:
        res = subprocess.run(
            ["journalctl", "--user", "-u", "zrok2-share.service", "--no-pager"],
            capture_output=True, text=True, timeout=10,
        )
        urls = re.findall(r"([a-z0-9]+\.shares\.zrok\.io)", res.stdout)
        if urls:
            return f"https://{urls[-1]}/webhooks/{route}"
    except (OSError, subprocess.SubprocessError):
        pass
    return f"http://localhost:{DEFAULT_LOCAL_PORT}/webhooks/{route}"


def webhook_secret() -> str | None:
    """The shared secret GitLab sends as ``X-Gitlab-Token``."""
    return read_env_value("GITLAB_WEBHOOK_SECRET")


def _hook_payload(url: str, secret: str | None, cfg: dict[str, Any]) -> dict[str, Any]:
    ingest = cfg.get("ingest") or {}
    payload: dict[str, Any] = {
        "url": url,
        "push_events": False,
        "tag_push_events": False,
        "issues_events": bool(ingest.get("issues", True)),
        "merge_requests_events": bool(ingest.get("merge_requests", True)),
        "note_events": False,
        "pipeline_events": False,
        "enable_ssl_verification": url.startswith("https://"),
    }
    if secret:
        payload["token"] = secret
    return payload


def find_hook(client: GitLabClient, project: str, route: str) -> int | None:
    """Find an existing bridge hook id on a project."""
    try:
        hooks = client.list_hooks(project)
    except GitLabError:
        return None
    for hook in hooks:
        if str(hook.get("url", "")).rstrip("/").endswith(f"/webhooks/{route}"):
            return hook.get("id")
    return None


def onboard(
    path: str,
    *,
    host: str | None = None,
    board_slug: str | None = None,
    default_role: str | None = None,
    url: str | None = None,
    register_hook: bool = True,
) -> dict[str, Any]:
    """Onboard a GitLab project into the bridge."""
    cfg = load_config()
    clean = (path or "").strip().strip("/")
    if "/" not in clean:
        return {"error": "expected group/project path, e.g. mygroup/myrepo"}

    host_info = resolve_host(cfg, host)
    alias = host_info["alias"]
    if find_project(cfg, clean, alias):
        return {"already_onboarded": True, "project": clean, "host": alias}

    client = GitLabClient.for_host(alias, cfg)
    try:
        project_info = client.project(clean)
    except GitLabError as exc:
        return {"error": f"cannot read project {clean} on {alias}: {exc}"}

    hook_id = None
    hook_error = None
    if register_hook:
        route = webhook_route(cfg)
        hook_url = url or public_webhook_url(cfg)
        existing = find_hook(client, clean, route)
        if existing is not None:
            hook_id = existing
        else:
            try:
                hook = client.create_hook(clean, _hook_payload(hook_url, webhook_secret(), cfg))
                hook_id = hook.get("id")
            except GitLabError as exc:
                hook_error = str(exc)

    entry: dict[str, Any] = {
        "host": alias,
        "path": clean,
        "project_id": project_info.get("id"),
        "web_url": project_info.get("web_url"),
        "webhook_id": hook_id,
    }
    if board_slug:
        entry["board_slug"] = board_slug
    if default_role:
        entry["default_role"] = default_role
    cfg.setdefault("projects", []).append(entry)
    save_config(cfg)

    out: dict[str, Any] = {"onboarded": True, "project": entry}
    if hook_error:
        out["webhook_warning"] = hook_error
    return out


def remove(path: str, *, host: str | None = None, delete_hook: bool = True) -> dict[str, Any]:
    """Remove a project from the bridge and delete its webhook."""
    cfg = load_config()
    clean = (path or "").strip().strip("/")
    alias = resolve_host(cfg, host)["alias"]
    entry = find_project(cfg, clean, alias) or find_project(cfg, clean)
    if entry is None:
        return {"not_onboarded": True, "project": clean}

    warning = None
    if delete_hook:
        client = GitLabClient.for_host(entry.get("host") or alias, cfg)
        hook_id = entry.get("webhook_id") or find_hook(client, clean, webhook_route(cfg))
        if hook_id is not None:
            try:
                client.delete_hook(clean, int(hook_id))
            except GitLabError as exc:
                warning = f"could not delete webhook {hook_id}: {exc}"

    cfg["projects"] = [p for p in cfg.get("projects", []) if p is not entry]
    save_config(cfg)
    out: dict[str, Any] = {"removed": True, "project": clean, "host": entry.get("host")}
    if warning:
        out["warning"] = warning
    return out


def list_projects() -> dict[str, Any]:
    """List onboarded projects."""
    cfg = load_config()
    return {
        "default_host": cfg.get("default_host"),
        "board": cfg.get("board_slug"),
        "webhook_url": public_webhook_url(cfg),
        "projects": cfg.get("projects") or [],
    }


def host_status() -> dict[str, Any]:
    """Reachability + token status per configured host. Never prints a token."""
    cfg = load_config()
    out = []
    for alias in (cfg.get("hosts") or {}):
        info = resolve_host(cfg, alias)
        token = read_env_value(info["token_env"])
        entry: dict[str, Any] = {
            "alias": alias,
            "url": info["url"],
            "token_env": info["token_env"],
            "token_present": bool(token),
            "verify_ssl": info["verify_ssl"],
        }
        if token:
            try:
                user = GitLabClient.for_host(alias, cfg).whoami()
                entry["authenticated_as"] = user.get("username")
            except GitLabError as exc:
                entry["error"] = str(exc)
        out.append(entry)
    return {"hermes_home": str(hermes_home()), "hosts": out}
