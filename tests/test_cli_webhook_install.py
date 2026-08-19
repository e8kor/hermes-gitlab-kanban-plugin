"""The ``hermes gitlab-kanban webhook --install`` repair path.

This is the verb users run when the bridge is silently dead, so its decision
logic matters: it must replace a misconfigured route, leave a healthy one alone,
and never claim success it did not verify. ``subprocess.run`` is mocked — the
tests assert which commands would be issued, not that a gateway exists.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def cli_mod(plugin):
    from hermes_plugins.gitlab_kanban import cli as cli_module

    return cli_module


@pytest.fixture()
def projects_stub():
    """A stand-in for the projects module with a scriptable webhook_status."""
    stub = MagicMock()
    stub.webhook_subscribe_command.return_value = [
        "hermes", "webhook", "subscribe", "gitlab-to-kanban",
        "--events", "Issue Hook,Merge Request Hook",
    ]
    stub._shell_quote.side_effect = lambda s: s if " " not in s else f"'{s}'"
    return stub


def _ok(returncode=0, stdout="done", stderr=""):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def test_healthy_route_is_left_completely_alone(cli_mod, projects_stub, capsys):
    projects_stub.webhook_status.return_value = {
        "route": "gitlab-to-kanban",
        "registered": True,
        "ok": True,
        "configured_events": ["Issue Hook", "Merge Request Hook"],
    }
    with patch("subprocess.run") as run:
        cli_mod._install_webhook(projects_stub)
    run.assert_not_called()
    assert "already subscribed correctly" in capsys.readouterr().out


def test_unsubscribed_route_is_subscribed_without_a_remove(cli_mod, projects_stub, capsys):
    projects_stub.webhook_status.side_effect = [
        {"route": "gitlab-to-kanban", "registered": False, "problem": "not subscribed"},
        {"ok": True, "configured_events": ["Issue Hook", "Merge Request Hook"]},
    ]
    with patch("subprocess.run", return_value=_ok()) as run:
        cli_mod._install_webhook(projects_stub)
    issued = [c.args[0] for c in run.call_args_list]
    assert len(issued) == 1, "nothing to remove when the route does not exist"
    assert issued[0][:3] == ["hermes", "webhook", "subscribe"]
    assert "verified" in capsys.readouterr().out


def test_misconfigured_route_is_removed_then_resubscribed(cli_mod, projects_stub, capsys):
    """The actual bug repair: a route on object_kind names must be replaced."""
    projects_stub.webhook_status.side_effect = [
        {
            "route": "gitlab-to-kanban",
            "registered": True,
            "problem": "the route's --events do not match the X-Gitlab-Event header",
        },
        {"ok": True, "configured_events": ["Issue Hook", "Merge Request Hook"]},
    ]
    with patch("subprocess.run", return_value=_ok()) as run:
        cli_mod._install_webhook(projects_stub)
    issued = [c.args[0] for c in run.call_args_list]
    assert issued[0] == ["hermes", "webhook", "remove", "gitlab-to-kanban"]
    assert issued[1][:3] == ["hermes", "webhook", "subscribe"]
    out = capsys.readouterr().out
    assert "Replacing misconfigured route" in out
    assert "verified" in out


def test_a_failed_remove_aborts_before_subscribing(cli_mod, projects_stub, capsys):
    """Never subscribe a duplicate on top of a route we failed to remove."""
    projects_stub.webhook_status.return_value = {
        "route": "gitlab-to-kanban",
        "registered": True,
        "problem": "mismatched events",
    }
    with patch("subprocess.run", return_value=_ok(returncode=1, stderr="boom")) as run:
        cli_mod._install_webhook(projects_stub)
    assert len(run.call_args_list) == 1, "must not proceed to subscribe"
    assert "error removing" in capsys.readouterr().out


def test_a_failed_subscribe_prints_the_manual_command(cli_mod, projects_stub, capsys):
    projects_stub.webhook_status.return_value = {
        "route": "gitlab-to-kanban", "registered": False, "problem": "not subscribed",
    }
    with patch("subprocess.run", return_value=_ok(returncode=1, stderr="gateway down")):
        cli_mod._install_webhook(projects_stub)
    out = capsys.readouterr().out
    assert "error subscribing" in out
    assert "hermes webhook subscribe gitlab-to-kanban" in out


def test_a_subscribe_that_does_not_take_is_reported_as_a_warning(cli_mod, projects_stub, capsys):
    """Exit code 0 is not proof — the verb re-reads status and says so if bad."""
    projects_stub.webhook_status.side_effect = [
        {"route": "gitlab-to-kanban", "registered": False, "problem": "not subscribed"},
        {"ok": False, "problem": "still mismatched"},
    ]
    with patch("subprocess.run", return_value=_ok()):
        cli_mod._install_webhook(projects_stub)
    out = capsys.readouterr().out
    assert "WARNING" in out and "still mismatched" in out
    assert "verified" not in out


def test_the_webhook_parser_exposes_install_and_print_command(cli_mod):
    import argparse

    parser = argparse.ArgumentParser()
    cli_mod.setup_cli(parser)
    args = parser.parse_args(["webhook", "--install"])
    assert args.install is True and args.print_command is False
    args = parser.parse_args(["webhook", "--print-command"])
    assert args.print_command is True and args.install is False
    args = parser.parse_args(["webhook"])
    assert args.install is False and args.print_command is False
