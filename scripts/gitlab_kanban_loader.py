"""Shared plugin loader for the gitlab-kanban standalone scripts.

Webhook and cron scripts run in a fresh interpreter with no Hermes packages on
``sys.path``, so they import the installed plugin by file path under the same
``hermes_plugins.gitlab_kanban`` namespace the framework loader uses.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

NAMESPACE = "hermes_plugins"
MODULE = f"{NAMESPACE}.gitlab_kanban"


def _hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".hermes"


def _candidates() -> list[Path]:
    here = Path(__file__).resolve().parent
    return [
        _hermes_home() / "plugins" / "gitlab-kanban",   # installed plugin
        here.parent / "plugin",                          # dev checkout
    ]


def load_plugin():
    """Import and return the plugin package."""
    if MODULE in sys.modules:
        return sys.modules[MODULE]
    for base in _candidates():
        init = base / "__init__.py"
        if not init.exists():
            continue
        if NAMESPACE not in sys.modules:
            ns = types.ModuleType(NAMESPACE)
            ns.__path__ = []  # type: ignore[attr-defined]
            ns.__package__ = NAMESPACE
            sys.modules[NAMESPACE] = ns
        spec = importlib.util.spec_from_file_location(
            MODULE, str(init), submodule_search_locations=[str(base)]
        )
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        module.__package__ = MODULE
        module.__path__ = [str(base)]  # type: ignore[attr-defined]
        sys.modules[MODULE] = module
        spec.loader.exec_module(module)
        return module
    raise SystemExit(
        "gitlab-kanban plugin not found — looked in: "
        + ", ".join(str(p) for p in _candidates())
    )


def load_module(name: str):
    """Import one submodule of the plugin, e.g. ``bridge`` or ``projects``."""
    load_plugin()
    return importlib.import_module(f"{MODULE}.{name}")
