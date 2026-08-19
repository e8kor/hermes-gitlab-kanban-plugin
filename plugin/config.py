"""Bridge configuration store for the gitlab-kanban plugin.

The config is a single JSON document under
``<hermes_home>/scripts/gitlab-kanban-bridge-config.json``. It is *behaviour*
configuration, so it lives here rather than in ``.env`` — only the GitLab
tokens are secrets.

Multi-host by design: ``hosts`` maps a short alias to a GitLab base URL and the
name of the environment variable holding that host's token. A self-managed
GitLab is just another alias.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .paths import config_path

DEFAULT_HOST_ALIAS = "gitlab.com"
DEFAULT_HOST_URL = "https://gitlab.com"
DEFAULT_TOKEN_ENV = "GITLAB_TOKEN"

# Role -> Hermes profile. Roles are the vocabulary the bridge speaks; profiles
# are whatever the user actually has installed, so the mapping is configurable.
DEFAULT_ROLE_PROFILES: dict[str, str] = {
    "developer": "coder",
    "reviewer": "reviewer",
    "scrum-master": "orchestrator",
    "researcher": "researcher",
    "writer": "writer",
    "qa": "reviewer",
}

# GitLab label -> role. First match wins, in label order.
DEFAULT_LABEL_ROLES: dict[str, str] = {
    "bug": "developer",
    "feature": "developer",
    "refactor": "developer",
    "chore": "developer",
    "review": "reviewer",
    "code-review": "reviewer",
    "research": "researcher",
    "spike": "researcher",
    "docs": "writer",
    "documentation": "writer",
    "test": "qa",
    "qa": "qa",
    "planning": "scrum-master",
    "epic": "scrum-master",
}


def default_config() -> dict[str, Any]:
    """The built-in default bridge config."""
    return {
        "board_slug": "gitlab",
        "board_name": "GitLab",
        "default_role": "developer",
        "default_host": DEFAULT_HOST_ALIAS,
        "hosts": {
            DEFAULT_HOST_ALIAS: {
                "url": DEFAULT_HOST_URL,
                "token_env": DEFAULT_TOKEN_ENV,
                "verify_ssl": True,
            }
        },
        # Each project: {"host": alias, "path": "group/sub/repo", "board_slug": optional,
        #                "default_role": optional, "webhook_id": int|None}
        "projects": [],
        "role_profiles": dict(DEFAULT_ROLE_PROFILES),
        "label_roles": dict(DEFAULT_LABEL_ROLES),
        "auto_label_rules": [],
        # Ingest gates: which GitLab object kinds create kanban tasks.
        "ingest": {
            "issues": True,
            "merge_requests": True,
            "milestones": False,
            "issue_actions": ["open", "reopen"],
            "merge_request_actions": ["open", "reopen"],
        },
        # Sync-back behaviour when a kanban task completes.
        "sync_back": {
            "comment": True,
            "close_issue": True,
            "close_merge_request": False,
            "backfill_missing": False,
            "backfill_project": "",
            "labels_on_done": [],
        },
        "style": {
            "tone": "concise, technical, direct",
            "format": "markdown",
            "language": "english",
        },
        "priority": 50,
        "webhook_route": "gitlab-to-kanban",
    }


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """One-level-deep merge: nested dicts are updated, everything else replaced."""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            merged = dict(out[key])
            merged.update(value)
            out[key] = merged
        else:
            out[key] = value
    return out


def load_config() -> dict[str, Any]:
    """Load the bridge config merged over the defaults."""
    cfg = default_config()
    path = config_path()
    if not path.exists():
        return cfg
    try:
        user = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return cfg
    if not isinstance(user, dict):
        return cfg
    return _merge(cfg, user)


def save_config(cfg: dict[str, Any]) -> Path:
    """Atomically write the bridge config."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=str(path.parent), prefix=".gitlab-kanban-", suffix=".tmp",
        delete=False, encoding="utf-8",
    ) as fh:
        json.dump(cfg, fh, indent=2, sort_keys=False)
        fh.write("\n")
        tmp = Path(fh.name)
    os.replace(tmp, path)
    return path


def resolve_host(cfg: dict[str, Any], alias: str | None = None) -> dict[str, Any]:
    """Resolve a host alias to ``{alias, url, token_env, verify_ssl}``.

    An unknown alias that looks like a URL is accepted as an ad-hoc host so a
    one-off self-managed instance works without editing the config first.
    """
    hosts = cfg.get("hosts") or {}
    name = alias or cfg.get("default_host") or DEFAULT_HOST_ALIAS
    entry = hosts.get(name)
    if entry is None and re.match(r"^https?://", str(name)):
        entry = {"url": str(name).rstrip("/"), "token_env": DEFAULT_TOKEN_ENV, "verify_ssl": True}
    if entry is None:
        entry = hosts.get(DEFAULT_HOST_ALIAS) or {
            "url": DEFAULT_HOST_URL,
            "token_env": DEFAULT_TOKEN_ENV,
            "verify_ssl": True,
        }
        name = cfg.get("default_host") or DEFAULT_HOST_ALIAS
    return {
        "alias": name,
        "url": str(entry.get("url", DEFAULT_HOST_URL)).rstrip("/"),
        "token_env": entry.get("token_env") or DEFAULT_TOKEN_ENV,
        "verify_ssl": bool(entry.get("verify_ssl", True)),
    }


def find_project(cfg: dict[str, Any], path: str, host: str | None = None) -> dict[str, Any] | None:
    """Find an onboarded project by its path (and optionally host alias)."""
    target = (path or "").strip().strip("/")
    for proj in cfg.get("projects") or []:
        if proj.get("path", "").strip("/") != target:
            continue
        if host and proj.get("host") != host:
            continue
        return proj
    return None


def board_for_project(cfg: dict[str, Any], project: dict[str, Any] | None) -> tuple[str, str]:
    """Board (slug, display name) for a project, falling back to the global board."""
    slug = cfg.get("board_slug") or "gitlab"
    name = cfg.get("board_name") or "GitLab"
    if project and project.get("board_slug"):
        slug = project["board_slug"]
        name = project.get("board_name") or slug.replace("-", " ").title()
    return slug, name


def role_for_labels(cfg: dict[str, Any], labels: list[str], project: dict[str, Any] | None = None) -> str:
    """Pick a role from labels, first match in label order."""
    label_roles = {k.lower(): v for k, v in (cfg.get("label_roles") or {}).items()}
    for label in labels:
        role = label_roles.get(str(label).lower())
        if role:
            return role
    if project and project.get("default_role"):
        return str(project["default_role"])
    return str(cfg.get("default_role") or "developer")


def profile_for_role(cfg: dict[str, Any], role: str) -> str:
    """Map a role to the Hermes profile that should execute it."""
    profiles = cfg.get("role_profiles") or {}
    return str(profiles.get(role) or profiles.get("developer") or role)


def apply_auto_labels(
    cfg: dict[str, Any], title: str, body: str, labels: list[str]
) -> list[str]:
    """Apply configured auto-label rules to an incoming object.

    A rule is ``{"match": "<field> contains <text>", "labels": [...]}`` where
    field is ``title``, ``body``, or ``label``.
    """
    out = list(labels)
    title_l = (title or "").lower()
    body_l = (body or "").lower()
    existing = [str(x).lower() for x in labels]
    for rule in cfg.get("auto_label_rules") or []:
        expr = str(rule.get("match", ""))
        add = [str(x) for x in rule.get("labels", [])]
        m = re.match(r"\s*(title|body|label)\s+(?:contains|includes)\s+(.+)$", expr, re.I)
        if not m:
            continue
        field, text = m.group(1).lower(), m.group(2).strip().strip('"').strip("'").lower()
        if not text:
            continue
        hit = (
            (field == "title" and text in title_l)
            or (field == "body" and text in body_l)
            or (field == "label" and any(text in lab for lab in existing))
        )
        if hit:
            for label in add:
                if label not in out:
                    out.append(label)
    return out
