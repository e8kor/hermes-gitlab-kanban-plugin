"""Webhook wiring: the event-header contract and the misconfiguration diagnostic.

The bug these cover: GitLab announces the event in ``X-Gitlab-Event`` using
human-readable names ("Issue Hook"), while the payload body carries snake_case
``object_kind`` ("issue"). Hermes' webhook route filters on the header, so a
route subscribed with the body names accepts each delivery and then silently
ignores it — nothing reaches the bridge and nothing appears on the board, with
no error anywhere.
"""

from __future__ import annotations

import json


def test_event_headers_are_gitlabs_human_readable_names(projects_mod):
    assert projects_mod.GITLAB_EVENT_HEADERS == {
        "issues": "Issue Hook",
        "merge_requests": "Merge Request Hook",
    }


def test_expected_events_are_not_the_body_object_kinds(projects_mod, bridge_mod):
    """Guard the exact confusion that broke this: header names != object_kind."""
    from conftest import issue_payload, mr_payload

    expected = projects_mod.webhook_events()
    body_kinds = {
        bridge_mod.normalize_event(issue_payload())["kind"],
        bridge_mod.normalize_event(mr_payload())["kind"],
    }
    assert body_kinds == {"issue", "merge_request"}
    assert not body_kinds & set(expected), (
        "the route must be subscribed with X-Gitlab-Event header values, "
        "never the payload's object_kind"
    )


def test_expected_events_follow_the_ingest_toggles(projects_mod, config_mod):
    cfg = config_mod.default_config()
    assert projects_mod.webhook_events(cfg) == ["Issue Hook", "Merge Request Hook"]
    cfg["ingest"]["merge_requests"] = False
    assert projects_mod.webhook_events(cfg) == ["Issue Hook"]
    cfg["ingest"]["issues"] = False
    assert projects_mod.webhook_events(cfg) == []


def test_subscribe_command_is_runnable_argv(projects_mod, config_mod):
    cmd = projects_mod.webhook_subscribe_command(config_mod.default_config())
    assert cmd[:3] == ["hermes", "webhook", "subscribe"]
    events = cmd[cmd.index("--events") + 1]
    assert events == "Issue Hook,Merge Request Hook"
    assert cmd[cmd.index("--script") + 1] == "gitlab-to-kanban.py"


def test_subscribe_command_includes_the_secret_only_when_set(projects_mod, isolated_home, monkeypatch):
    assert "--secret" not in projects_mod.webhook_subscribe_command()
    (isolated_home / ".env").write_text("GITLAB_WEBHOOK_SECRET=s3cret\n", encoding="utf-8")
    monkeypatch.delenv("GITLAB_WEBHOOK_SECRET", raising=False)
    cmd = projects_mod.webhook_subscribe_command()
    assert cmd[cmd.index("--secret") + 1] == "s3cret"


def _write_subs(home, payload):
    (home / "webhook_subscriptions.json").write_text(json.dumps(payload), encoding="utf-8")


def test_status_reports_an_unsubscribed_route_with_a_fix(projects_mod, isolated_home):
    status = projects_mod.webhook_status()
    assert status["registered"] is False
    assert "not subscribed" in status["problem"]
    assert status["fix"].startswith("hermes webhook subscribe")


def test_status_flags_the_object_kind_mistake_and_offers_the_repair(projects_mod, isolated_home):
    """A route subscribed the wrong way must be reported, not silently tolerated."""
    _write_subs(
        isolated_home,
        {"gitlab-to-kanban": {"events": ["issue", "merge_request"], "script": "gitlab-to-kanban.py"}},
    )
    status = projects_mod.webhook_status()
    assert status["registered"] is True
    assert not status.get("ok")
    assert "ignored" in status["problem"]
    assert "Issue Hook" in status["problem"]
    # The fix must remove the bad route first, using a real CLI verb.
    assert status["fix"].startswith("hermes webhook remove gitlab-to-kanban &&")
    assert "'Issue Hook,Merge Request Hook'" in status["fix"]


def test_status_is_ok_on_a_correctly_subscribed_route(projects_mod, isolated_home):
    _write_subs(
        isolated_home,
        {
            "gitlab-to-kanban": {
                "events": ["Issue Hook", "Merge Request Hook"],
                "script": "gitlab-to-kanban.py",
                "secret": "x",
            }
        },
    )
    status = projects_mod.webhook_status()
    assert status["ok"] is True
    assert status["route_secret_set"] is True
    assert "problem" not in status


def test_status_accepts_a_route_that_allows_every_event(projects_mod, isolated_home):
    """An empty events list means "accept all" in Hermes — not a misconfiguration."""
    _write_subs(
        isolated_home,
        {"gitlab-to-kanban": {"events": [], "script": "gitlab-to-kanban.py"}},
    )
    assert projects_mod.webhook_status()["ok"] is True


def test_status_flags_the_wrong_script(projects_mod, isolated_home):
    _write_subs(
        isolated_home,
        {"gitlab-to-kanban": {"events": ["Issue Hook", "Merge Request Hook"], "script": "other.py"}},
    )
    status = projects_mod.webhook_status()
    assert not status.get("ok")
    assert "other.py" in status["problem"]


def test_status_only_requires_the_events_the_bridge_ingests(projects_mod, config_mod, isolated_home):
    cfg = config_mod.default_config()
    cfg["ingest"]["merge_requests"] = False
    config_mod.save_config(cfg)
    _write_subs(
        isolated_home,
        {"gitlab-to-kanban": {"events": ["Issue Hook"], "script": "gitlab-to-kanban.py"}},
    )
    assert projects_mod.webhook_status()["ok"] is True


def test_status_survives_a_corrupt_subscriptions_file(projects_mod, isolated_home):
    (isolated_home / "webhook_subscriptions.json").write_text("{broken", encoding="utf-8")
    status = projects_mod.webhook_status()
    assert status["registered"] is False
    assert "cannot read" in status["problem"]


def test_status_never_prints_the_secret_value(projects_mod, isolated_home, monkeypatch):
    (isolated_home / ".env").write_text("GITLAB_WEBHOOK_SECRET=topsecret\n", encoding="utf-8")
    monkeypatch.delenv("GITLAB_WEBHOOK_SECRET", raising=False)
    _write_subs(
        isolated_home,
        {
            "gitlab-to-kanban": {
                "events": ["Issue Hook", "Merge Request Hook"],
                "script": "gitlab-to-kanban.py",
                "secret": "topsecret",
            }
        },
    )
    # webhook_status is a diagnostic; it reports presence, never the value.
    assert "topsecret" not in json.dumps(projects_mod.webhook_status())


def test_the_status_tool_surfaces_the_webhook_diagnostic(tools_mod, isolated_home, monkeypatch):
    monkeypatch.setattr(tools_mod.kanban, "list_tasks", lambda *a, **k: [])
    _write_subs(
        isolated_home,
        {"gitlab-to-kanban": {"events": ["issue"], "script": "gitlab-to-kanban.py"}},
    )
    result = json.loads(tools_mod.gitlab_kanban_status({}))
    assert result["webhook"]["problem"]


def test_slash_webhook_view_shows_the_problem_and_the_fix(plugin, isolated_home):
    from hermes_plugins.gitlab_kanban.slash import handle_slash

    _write_subs(
        isolated_home,
        {"gitlab-to-kanban": {"events": ["issue"], "script": "gitlab-to-kanban.py"}},
    )
    out = handle_slash("webhook")
    assert "X-Gitlab-Event" in out
    assert "Issue Hook" in out
    assert "PROBLEM" in out and "Fix:" in out


def test_install_instructions_use_the_header_names(plugin):
    """install.sh is where the wrong command originally shipped — pin it."""
    from pathlib import Path

    repo = Path(plugin.__file__).resolve().parent.parent
    text = (repo / "install.sh").read_text(encoding="utf-8")
    assert "'Issue Hook,Merge Request Hook'" in text
    assert "--events issue,merge_request" not in text
