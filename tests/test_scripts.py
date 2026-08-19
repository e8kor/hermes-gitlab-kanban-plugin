"""The standalone scripts: loader resolution and the webhook contract."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import issue_payload

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"


def _run(script: str, *args: str, stdin: str = "", env_extra: dict | None = None):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        input=stdin, capture_output=True, text=True, timeout=60, env=env,
    )


def test_every_script_is_syntactically_valid(tmp_path):
    for script in SCRIPTS.glob("*.py"):
        res = subprocess.run(
            [sys.executable, "-m", "py_compile", str(script)],
            capture_output=True, text=True, cwd=tmp_path,
        )
        assert res.returncode == 0, f"{script.name}: {res.stderr}"


def test_loader_finds_the_dev_checkout_plugin(isolated_home):
    """The loader must resolve the plugin from the repo when nothing is installed."""
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from gitlab_kanban_loader import load_module\n"
        "cfg = load_module('config')\n"
        "print(cfg.default_config()['board_slug'])\n" % str(SCRIPTS)
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "gitlab"


def test_webhook_script_ignores_a_non_json_body(isolated_home):
    res = _run("gitlab-to-kanban.py", stdin="not json at all",
               env_extra={"HERMES_HOME": str(isolated_home)})
    assert res.returncode == 0
    assert res.stdout.strip() == "", "an ignored payload must not emit the silent token"


def test_webhook_script_ignores_an_empty_body(isolated_home):
    res = _run("gitlab-to-kanban.py", stdin="", env_extra={"HERMES_HOME": str(isolated_home)})
    assert res.returncode == 0 and res.stdout.strip() == ""


def test_webhook_script_ignores_an_unsupported_event(isolated_home):
    res = _run(
        "gitlab-to-kanban.py",
        stdin=json.dumps({"object_kind": "push"}),
        env_extra={"HERMES_HOME": str(isolated_home)},
    )
    assert res.returncode == 0
    assert res.stdout.strip() == ""
    # The reason goes to stderr, never stdout.
    assert "ignored" in res.stderr.lower() or "unsupported" in res.stderr.lower()


def test_webhook_script_ignores_a_non_onboarded_project(isolated_home):
    """A repo outside the allow-list must not create a task.

    The script reads its config from HERMES_HOME, so the sandbox writes a config
    there with a different project onboarded.
    """
    config = isolated_home / "scripts" / "gitlab-kanban-bridge-config.json"
    config.write_text(
        json.dumps({"projects": [{"host": "gitlab.com", "path": "someone/else"}]}),
        encoding="utf-8",
    )
    res = _run(
        "gitlab-to-kanban.py",
        stdin=json.dumps(issue_payload()),
        env_extra={"HERMES_HOME": str(isolated_home)},
    )
    assert res.returncode == 0
    assert res.stdout.strip() == "", "no [SILENT] token means no task was created"
    # The real outcome is on stderr for a [SILENT] script.
    assert "not onboarded" in res.stderr


def test_project_manage_prints_a_webhook_url(isolated_home):
    res = _run("gitlab-project-manage.py", "webhook-url",
               env_extra={"HERMES_HOME": str(isolated_home)})
    assert res.returncode == 0
    assert "/webhooks/gitlab-to-kanban" in res.stdout


def test_project_manage_list_is_json(isolated_home):
    res = _run("gitlab-project-manage.py", "list",
               env_extra={"HERMES_HOME": str(isolated_home)})
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert data["projects"] == []


def test_sweep_is_quiet_when_nothing_is_due(isolated_home, monkeypatch):
    """A cron sweep with nothing to do must not print — otherwise it spams."""
    config = isolated_home / "scripts" / "gitlab-kanban-bridge-config.json"
    config.write_text(json.dumps({"board_slug": "no-such-board"}), encoding="utf-8")
    # A nonexistent board yields no done tasks, so the sweep has nothing to sync.
    fake_bin = isolated_home / "bin"
    fake_bin.mkdir()
    hermes_stub = fake_bin / "hermes"
    hermes_stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hermes_stub.chmod(0o755)
    res = _run(
        "gitlab-kanban-sync-sweep.py",
        env_extra={"HERMES_HOME": str(isolated_home), "PATH": f"{fake_bin}:{os.environ['PATH']}"},
    )
    assert res.returncode == 0
    assert res.stdout.strip() == ""


def test_sweep_verbose_reports_even_when_idle(isolated_home):
    config = isolated_home / "scripts" / "gitlab-kanban-bridge-config.json"
    config.write_text(json.dumps({"board_slug": "no-such-board"}), encoding="utf-8")
    fake_bin = isolated_home / "bin"
    fake_bin.mkdir()
    hermes_stub = fake_bin / "hermes"
    hermes_stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hermes_stub.chmod(0o755)
    res = _run(
        "gitlab-kanban-sync-sweep.py", "--verbose",
        env_extra={"HERMES_HOME": str(isolated_home), "PATH": f"{fake_bin}:{os.environ['PATH']}"},
    )
    assert res.returncode == 0
    assert json.loads(res.stdout)["done_tasks"] == 0
