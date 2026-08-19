"""API client and registration wiring — no network, no live Hermes."""

from __future__ import annotations

import json

import pytest


def test_project_paths_are_url_encoded(api_mod):
    assert api_mod.encode_project("group/sub/repo") == "group%2Fsub%2Frepo"
    assert api_mod.encode_project("/group/repo/") == "group%2Frepo"
    assert api_mod.encode_project(1234) == "1234"
    assert api_mod.encode_project("1234") == "1234"


def test_client_refuses_to_call_without_a_token(api_mod):
    client = api_mod.GitLabClient("https://gitlab.example.com", None)
    with pytest.raises(api_mod.GitLabError) as excinfo:
        client.get("/user")
    assert excinfo.value.status == 401


def test_client_reads_its_token_from_the_env_file(api_mod, config_mod, isolated_home):
    (isolated_home / ".env").write_text('GITLAB_WORK_TOKEN="secret-value"\n', encoding="utf-8")
    cfg = config_mod.default_config()
    cfg["hosts"]["work"] = {"url": "https://gitlab.corp", "token_env": "GITLAB_WORK_TOKEN"}
    client = api_mod.GitLabClient.for_host("work", cfg)
    assert client.token == "secret-value"
    assert client.url == "https://gitlab.corp"


def test_client_url_has_no_trailing_slash(api_mod):
    assert api_mod.GitLabClient("https://gitlab.corp/", "t").url == "https://gitlab.corp"


def test_ssl_context_is_only_relaxed_when_asked(api_mod):
    assert api_mod.GitLabClient("https://x", "t")._context() is None
    ctx = api_mod.GitLabClient("https://x", "t", verify_ssl=False)._context()
    assert ctx is not None and ctx.check_hostname is False


def test_request_builds_the_v4_path_with_params(api_mod, monkeypatch):
    captured = {}

    class FakeResponse:
        status = 200

        def read(self):
            return b"[]"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None, context=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["token"] = req.get_header("Private-token")
        return FakeResponse()

    monkeypatch.setattr(api_mod.urllib.request, "urlopen", fake_urlopen)
    client = api_mod.GitLabClient("https://gitlab.corp", "tok")
    client.get("/projects/g%2Fr/issues", state="opened", per_page=None)

    assert captured["url"] == "https://gitlab.corp/api/v4/projects/g%2Fr/issues?state=opened"
    assert captured["method"] == "GET"
    assert captured["token"] == "tok"


def test_http_error_becomes_a_gitlab_error_with_the_message(api_mod, monkeypatch):
    import io
    import urllib.error

    def fake_urlopen(req, timeout=None, context=None):
        raise urllib.error.HTTPError(
            req.full_url, 404, "Not Found", {}, io.BytesIO(b'{"message":"404 Project Not Found"}')
        )

    monkeypatch.setattr(api_mod.urllib.request, "urlopen", fake_urlopen)
    client = api_mod.GitLabClient("https://gitlab.corp", "tok")
    with pytest.raises(api_mod.GitLabError) as excinfo:
        client.get("/projects/nope")
    assert excinfo.value.status == 404
    assert "Project Not Found" in str(excinfo.value)


def test_unreachable_host_is_a_gitlab_error_not_a_traceback(api_mod, monkeypatch):
    import urllib.error

    def fake_urlopen(req, timeout=None, context=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(api_mod.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(api_mod.GitLabError) as excinfo:
        api_mod.GitLabClient("https://gitlab.offline", "tok").get("/user")
    assert excinfo.value.status == 0


def test_paginate_walks_full_pages_then_stops_on_a_short_one(api_mod, monkeypatch):
    # limit=4 -> per_page=4: a full page means "there may be more", a short one ends it.
    pages = {1: [{"i": 1}, {"i": 2}, {"i": 3}, {"i": 4}], 2: [{"i": 5}]}
    calls = []

    def fake_get(self, path, **params):
        calls.append(params["page"])
        return pages.get(params["page"], [])

    monkeypatch.setattr(api_mod.GitLabClient, "get", fake_get)
    out = api_mod.GitLabClient("https://x", "t").paginate("/things", limit=4)
    assert len(out) == 4
    assert calls == [1]


def test_paginate_stops_immediately_on_a_short_first_page(api_mod, monkeypatch):
    calls = []

    def fake_get(self, path, **params):
        calls.append(params["page"])
        return [{"i": 1}, {"i": 2}]

    monkeypatch.setattr(api_mod.GitLabClient, "get", fake_get)
    out = api_mod.GitLabClient("https://x", "t").paginate("/things", limit=50)
    assert len(out) == 2
    assert calls == [1], "a page shorter than per_page means there is nothing more to fetch"


def test_paginate_respects_the_limit(api_mod, monkeypatch):
    monkeypatch.setattr(
        api_mod.GitLabClient, "get", lambda self, path, **p: [{"i": n} for n in range(p["per_page"])]
    )
    assert len(api_mod.GitLabClient("https://x", "t").paginate("/things", limit=3)) == 3


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------


class FakeCtx:
    def __init__(self):
        self.tools = {}
        self.commands = []
        self.cli = []
        self.skills = {}

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler}

    def register_command(self, name, handler, description, args_hint=""):
        self.commands.append(name)

    def register_cli_command(self, name, help, setup_fn, handler_fn=None):
        self.cli.append((name, setup_fn, handler_fn))

    def register_skill(self, name, path):
        self.skills[name] = path


def test_register_wires_every_surface(plugin):
    from hermes_plugins.gitlab_kanban.schemas import SCHEMAS

    ctx = FakeCtx()
    plugin.register(ctx)

    assert set(ctx.tools) == set(SCHEMAS)
    assert all(entry["toolset"] == plugin.TOOLSET for entry in ctx.tools.values())
    assert ctx.commands == ["gitlab-kanban"]
    assert [name for name, _, _ in ctx.cli] == ["gitlab-kanban"]
    # All four role skills ship inside the plugin and register.
    assert set(ctx.skills) == set(plugin.SKILLS)
    for path in ctx.skills.values():
        assert path.exists()


def test_registered_handlers_return_json(plugin):
    ctx = FakeCtx()
    plugin.register(ctx)
    for name, entry in ctx.tools.items():
        result = entry["handler"]({})
        assert isinstance(json.loads(result), dict), name


def test_cli_tree_builds_without_a_live_hermes(plugin):
    import argparse

    ctx = FakeCtx()
    plugin.register(ctx)
    _, setup_fn, handler_fn = ctx.cli[0]

    parser = argparse.ArgumentParser(prog="hermes")
    subparsers = parser.add_subparsers(dest="command")
    setup_fn(subparsers.add_parser("gitlab-kanban"))

    args = parser.parse_args(["gitlab-kanban", "project", "onboard", "g/r", "--host", "work"])
    assert args.gitlab_kanban_action == "project"
    assert args.project_action == "onboard"
    assert args.path == "g/r" and args.host == "work"

    args = parser.parse_args(["gitlab-kanban", "issue", "close", "42"])
    assert args.issue_action == "close" and args.iid == 42

    args = parser.parse_args(["gitlab-kanban", "mr", "merge", "17", "--squash"])
    assert args.mr_action == "merge" and args.iid == 17 and args.squash is True

    args = parser.parse_args(["gitlab-kanban", "sprint", "progress", "5"])
    assert args.milestone_action == "progress" and args.milestone_id == 5

    args = parser.parse_args(["gitlab-kanban", "sync", "--dry-run"])
    assert args.dry_run is True

    assert callable(handler_fn)


def test_every_skill_has_a_short_description_and_the_required_sections(plugin):
    import re
    from pathlib import Path

    root = Path(plugin.__file__).parent / "skills"
    for name in plugin.SKILLS:
        text = (root / name / "SKILL.md").read_text(encoding="utf-8")
        match = re.search(r'^description:\s*"?(.+?)"?\s*$', text, re.MULTILINE)
        assert match, name
        assert len(match.group(1)) <= 60, f"{name}: {len(match.group(1))} chars"
        for section in ("## When to Use", "## Prerequisites", "## Pitfalls", "## Verification"):
            assert section in text, f"{name} is missing {section}"
