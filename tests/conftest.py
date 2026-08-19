"""Test fixtures: load the plugin under an isolated HERMES_HOME.

The plugin is loaded exactly the way the framework loads it — as
``hermes_plugins.gitlab_kanban`` from a file path — so the tests exercise the
real import graph rather than a copy.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugin"
MODULE = "hermes_plugins.gitlab_kanban"


def _load() -> types.ModuleType:
    if MODULE in sys.modules:
        return sys.modules[MODULE]
    if "hermes_plugins" not in sys.modules:
        ns = types.ModuleType("hermes_plugins")
        ns.__path__ = []  # type: ignore[attr-defined]
        ns.__package__ = "hermes_plugins"
        sys.modules["hermes_plugins"] = ns
    spec = importlib.util.spec_from_file_location(
        MODULE, str(PLUGIN_DIR / "__init__.py"), submodule_search_locations=[str(PLUGIN_DIR)]
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    module.__package__ = MODULE
    module.__path__ = [str(PLUGIN_DIR)]  # type: ignore[attr-defined]
    sys.modules[MODULE] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point HERMES_HOME (and HOME) at a temp dir so no real config is touched."""
    home = tmp_path / ".hermes"
    (home / "scripts").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    monkeypatch.delenv("GITLAB_WEBHOOK_SECRET", raising=False)
    return home


@pytest.fixture
def plugin():
    return _load()


@pytest.fixture
def config_mod(plugin):
    return importlib.import_module(f"{MODULE}.config")


@pytest.fixture
def bridge_mod(plugin):
    return importlib.import_module(f"{MODULE}.bridge")


@pytest.fixture
def kanban_mod(plugin):
    return importlib.import_module(f"{MODULE}.kanban")


@pytest.fixture
def api_mod(plugin):
    return importlib.import_module(f"{MODULE}.api")


@pytest.fixture
def tools_mod(plugin):
    return importlib.import_module(f"{MODULE}.tools")


@pytest.fixture
def projects_mod(plugin):
    return importlib.import_module(f"{MODULE}.projects")


def issue_payload(**overrides):
    """A synthesized GitLab issue webhook payload."""
    payload = {
        "object_kind": "issue",
        "user": {"username": "alice"},
        "project": {
            "path_with_namespace": "mygroup/myrepo",
            "web_url": "https://gitlab.example.com/mygroup/myrepo",
        },
        "object_attributes": {
            "iid": 42,
            "title": "Crash on empty input",
            "description": "Steps to reproduce...",
            "action": "open",
            "state": "opened",
            "url": "https://gitlab.example.com/mygroup/myrepo/-/issues/42",
        },
        "labels": [{"title": "bug"}],
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(payload.get(key), dict):
            payload[key] = {**payload[key], **value}
        else:
            payload[key] = value
    return payload


def mr_payload(**overrides):
    """A synthesized GitLab merge request webhook payload."""
    payload = {
        "object_kind": "merge_request",
        "user": {"username": "bob"},
        "project": {
            "path_with_namespace": "mygroup/myrepo",
            "web_url": "https://gitlab.example.com/mygroup/myrepo",
        },
        "object_attributes": {
            "iid": 17,
            "title": "Fix the crash",
            "description": "Closes #42",
            "action": "open",
            "state": "opened",
            "source_branch": "42-fix-crash",
            "target_branch": "main",
            "url": "https://gitlab.example.com/mygroup/myrepo/-/merge_requests/17",
        },
        "labels": [{"title": "review"}],
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(payload.get(key), dict):
            payload[key] = {**payload[key], **value}
        else:
            payload[key] = value
    return payload
