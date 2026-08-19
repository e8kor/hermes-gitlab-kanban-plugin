"""Profile-aware paths and .env reading for the gitlab-kanban plugin.

Every path goes through ``hermes_home()`` so each Hermes profile owns its own
bridge config, sync state, and board. Never hardcode ``~/.hermes``.
"""

from __future__ import annotations

import os
from pathlib import Path

CONFIG_FILENAME = "gitlab-kanban-bridge-config.json"
SYNC_STATE_FILENAME = "gitlab-kanban-sync-state.json"


def hermes_home() -> Path:
    """Resolve the active Hermes home (profile-aware).

    Prefers the framework helper so a profile override applied by
    ``hermes -p <profile>`` is honored; falls back to ``$HERMES_HOME`` and
    finally ``~/.hermes``.
    """
    try:  # pragma: no cover - depends on host install
        from hermes_constants import get_hermes_home  # type: ignore

        return Path(get_hermes_home())
    except Exception:
        env = os.environ.get("HERMES_HOME")
        if env:
            return Path(env).expanduser()
        return Path.home() / ".hermes"


def config_path() -> Path:
    """Path to the bridge config JSON."""
    return hermes_home() / "scripts" / CONFIG_FILENAME


def sync_state_path() -> Path:
    """Path to the sync-back state file (which tasks were already synced)."""
    return hermes_home() / SYNC_STATE_FILENAME


def scripts_dir() -> Path:
    """Directory the bridge scripts are installed into."""
    return hermes_home() / "scripts"


def env_path() -> Path:
    """Path to the profile's ``.env`` (secrets only)."""
    return hermes_home() / ".env"


def read_env_value(name: str) -> str | None:
    """Read a secret from the environment, falling back to the profile ``.env``.

    Cron jobs and webhook scripts run in a fresh environment without the
    token, so callers must be able to source it themselves.
    """
    val = os.environ.get(name)
    if val:
        return val
    path = env_path()
    if not path.exists():
        return None
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            if key.strip() != name:
                continue
            value = raw.strip().strip('"').strip("'")
            if value:
                os.environ[name] = value
                return value
    except OSError:
        return None
    return None
