"""Tool contract: handlers return JSON, never raise, and gate their inputs."""

from __future__ import annotations

import json

import pytest


def call(handler, **args):
    raw = handler(args)
    assert isinstance(raw, str), "handlers must return a JSON string"
    return json.loads(raw)


def test_every_schema_has_a_handler_and_vice_versa(plugin):
    from hermes_plugins.gitlab_kanban.schemas import SCHEMAS
    from hermes_plugins.gitlab_kanban.tools import HANDLERS

    assert set(SCHEMAS) == set(HANDLERS), "a schema without a handler is a tool that does not exist"
    for name, schema in SCHEMAS.items():
        assert schema["name"] == name
        assert schema["description"].strip()
        assert schema["parameters"]["type"] == "object"


def test_manifest_tool_list_matches_the_schemas(plugin):
    from pathlib import Path

    from hermes_plugins.gitlab_kanban.schemas import SCHEMAS

    manifest = (Path(plugin.__file__).parent / "plugin.yaml").read_text(encoding="utf-8")
    declared = {
        line.strip().lstrip("- ").strip()
        for line in manifest.splitlines()
        if line.strip().startswith("- gitlab")
    }
    assert declared == set(SCHEMAS)


def test_handlers_never_raise_even_on_garbage(tools_mod):
    for handler in tools_mod.HANDLERS.values():
        for args in ({}, {"action": "nonsense"}, {"action": None, "iid": "abc"}):
            result = json.loads(handler(args))
            assert isinstance(result, dict)


def test_handler_wraps_an_unexpected_exception_as_json(tools_mod):
    boom = tools_mod.safe(lambda args, **kw: (_ for _ in ()).throw(ValueError("kaboom")))
    result = json.loads(boom({}))
    assert "kaboom" in result["error"]


def test_config_show_and_set_board(tools_mod):
    shown = call(tools_mod.gitlab_kanban_config, action="show")
    assert shown["config"]["board_slug"]
    updated = call(tools_mod.gitlab_kanban_config, action="set-board", board_slug="sprint", board_name="Sprint")
    assert updated["board_slug"] == "sprint"
    assert call(tools_mod.gitlab_kanban_config, action="show")["config"]["board_slug"] == "sprint"


def test_add_host_requires_a_real_url(tools_mod):
    assert "error" in call(tools_mod.gitlab_kanban_config, action="add-host", host="work")
    assert "error" in call(
        tools_mod.gitlab_kanban_config, action="add-host", host="work", url="gitlab.corp"
    )
    ok = call(
        tools_mod.gitlab_kanban_config,
        action="add-host", host="work", url="https://gitlab.corp/", token_env="WORK_TOKEN",
    )
    assert ok["url"] == "https://gitlab.corp"
    assert ok["token_env"] == "WORK_TOKEN"
    # The tool must tell the user where the token goes, without printing one.
    assert "WORK_TOKEN" in ok["note"]


def test_add_host_never_stores_a_token_in_the_config(tools_mod, config_mod):
    call(tools_mod.gitlab_kanban_config, action="add-host", host="work", url="https://gitlab.corp")
    serialized = json.dumps(config_mod.load_config())
    assert "PRIVATE-TOKEN" not in serialized
    assert "glpat" not in serialized


def test_remove_host_refuses_while_projects_remain(tools_mod, config_mod):
    call(tools_mod.gitlab_kanban_config, action="add-host", host="work", url="https://gitlab.corp")
    cfg = config_mod.load_config()
    cfg["projects"] = [{"host": "work", "path": "g/r"}]
    config_mod.save_config(cfg)
    assert "error" in call(tools_mod.gitlab_kanban_config, action="remove-host", host="work")


def test_removing_the_default_host_reassigns_the_default(tools_mod, config_mod):
    call(tools_mod.gitlab_kanban_config, action="add-host", host="work", url="https://gitlab.corp")
    call(tools_mod.gitlab_kanban_config, action="set-default-host", host="work")
    call(tools_mod.gitlab_kanban_config, action="remove-host", host="work")
    cfg = config_mod.load_config()
    assert cfg["default_host"] in cfg["hosts"]


def test_set_label_role_rejects_an_unknown_role(tools_mod):
    result = call(tools_mod.gitlab_kanban_config, action="set-label-role", label="perf", role="wizard")
    assert "error" in result and result["known_roles"]
    ok = call(tools_mod.gitlab_kanban_config, action="set-label-role", label="perf", role="developer")
    assert ok["label_roles"]["perf"] == "developer"


def test_auto_label_rule_requires_the_arrow(tools_mod):
    assert "error" in call(tools_mod.gitlab_kanban_config, action="add-auto-label", rule="title contains x")
    ok = call(
        tools_mod.gitlab_kanban_config,
        action="add-auto-label", rule="title contains crash => bug, urgent",
    )
    assert ok["auto_label_rules"][-1]["labels"] == ["bug", "urgent"]


def test_sync_back_toggles_persist(tools_mod, config_mod):
    out = call(
        tools_mod.gitlab_kanban_config,
        action="set-sync-back", close_issue=False, close_merge_request=True,
        labels_on_done="done,hermes",
    )
    assert out["sync_back"]["close_issue"] is False
    assert out["sync_back"]["close_merge_request"] is True
    assert config_mod.load_config()["sync_back"]["labels_on_done"] == ["done", "hermes"]


def test_reset_restores_defaults(tools_mod, config_mod):
    call(tools_mod.gitlab_kanban_config, action="set-board", board_slug="temp")
    call(tools_mod.gitlab_kanban_config, action="reset")
    assert config_mod.load_config()["board_slug"] == config_mod.default_config()["board_slug"]


def test_project_action_is_validated(tools_mod):
    assert "error" in call(tools_mod.gitlab_kanban_project, action="explode")
    assert "error" in call(tools_mod.gitlab_kanban_project, action="onboard")


def test_onboard_rejects_a_path_without_a_group(tools_mod):
    assert "error" in call(tools_mod.gitlab_kanban_project, action="onboard", project="just-a-repo")


def test_crud_tools_explain_themselves_when_no_project_is_resolvable(tools_mod):
    for handler in (tools_mod.gitlab_issue, tools_mod.gitlab_merge_request, tools_mod.gitlab_milestone):
        result = call(handler, action="list")
        assert "onboard" in result["error"] or "project" in result["error"]


def test_crud_tools_list_the_candidates_when_ambiguous(tools_mod, config_mod):
    cfg = config_mod.default_config()
    cfg["projects"] = [
        {"host": "gitlab.com", "path": "a/one"},
        {"host": "work", "path": "b/two"},
    ]
    config_mod.save_config(cfg)
    result = call(tools_mod.gitlab_issue, action="list")
    assert "a/one" in result["error"] and "b/two" in result["error"]


def test_a_single_onboarded_project_is_used_implicitly(tools_mod, config_mod, monkeypatch, api_mod):
    cfg = config_mod.default_config()
    cfg["projects"] = [{"host": "gitlab.com", "path": "only/repo"}]
    config_mod.save_config(cfg)

    seen = {}

    class Stub:
        def list_issues(self, project, **kwargs):
            seen["project"] = project
            return []

    monkeypatch.setattr(api_mod.GitLabClient, "for_host", classmethod(lambda cls, *a, **k: Stub()))
    result = call(tools_mod.gitlab_issue, action="list")
    assert seen["project"] == "only/repo"
    assert result["count"] == 0


def test_issue_write_actions_require_an_iid(tools_mod, config_mod, monkeypatch, api_mod):
    cfg = config_mod.default_config()
    cfg["projects"] = [{"host": "gitlab.com", "path": "only/repo"}]
    config_mod.save_config(cfg)
    monkeypatch.setattr(api_mod.GitLabClient, "for_host", classmethod(lambda cls, *a, **k: object()))
    for action in ("get", "update", "close", "comment", "delete", "to-kanban"):
        assert "iid" in call(tools_mod.gitlab_issue, action=action)["error"]


def test_merge_request_create_requires_branch_and_title(tools_mod, config_mod, monkeypatch, api_mod):
    cfg = config_mod.default_config()
    cfg["projects"] = [{"host": "gitlab.com", "path": "only/repo"}]
    config_mod.save_config(cfg)
    monkeypatch.setattr(api_mod.GitLabClient, "for_host", classmethod(lambda cls, *a, **k: object()))
    result = call(tools_mod.gitlab_merge_request, action="create", title="x")
    assert "source_branch" in result["error"]


def test_milestone_progress_computes_from_issue_states(tools_mod, config_mod, monkeypatch, api_mod):
    cfg = config_mod.default_config()
    cfg["projects"] = [{"host": "gitlab.com", "path": "only/repo"}]
    config_mod.save_config(cfg)

    issues = [
        {"iid": 1, "state": "closed", "weight": 3, "assignees": [{"username": "a"}]},
        {"iid": 2, "state": "opened", "weight": 5, "assignees": []},
        {"iid": 3, "state": "opened", "weight": 2, "assignees": [{"username": "b"}]},
    ]

    class Stub:
        def milestone_issues(self, project, mid, limit=100):
            return issues

    monkeypatch.setattr(api_mod.GitLabClient, "for_host", classmethod(lambda cls, *a, **k: Stub()))
    result = call(tools_mod.gitlab_milestone, action="progress", milestone_id=7)
    assert result["total_issues"] == 3
    assert result["open"] == 2 and result["closed"] == 1
    assert result["weight_total"] == 10 and result["weight_closed"] == 3
    assert round(result["percent_complete"], 1) == 33.3
    assert [i["iid"] for i in result["unassigned_open"]] == [2]


def test_progress_of_an_empty_milestone_does_not_divide_by_zero(tools_mod, config_mod, monkeypatch, api_mod):
    cfg = config_mod.default_config()
    cfg["projects"] = [{"host": "gitlab.com", "path": "only/repo"}]
    config_mod.save_config(cfg)

    class Stub:
        def milestone_issues(self, project, mid, limit=100):
            return []

    monkeypatch.setattr(api_mod.GitLabClient, "for_host", classmethod(lambda cls, *a, **k: Stub()))
    result = call(tools_mod.gitlab_milestone, action="progress", milestone_id=7)
    assert result["percent_complete"] == 0.0


def test_status_reports_without_a_token_and_without_leaking_one(tools_mod, monkeypatch):
    monkeypatch.setattr(tools_mod.kanban, "list_tasks", lambda *a, **k: [])
    result = call(tools_mod.gitlab_kanban_status)
    assert result["hosts"][0]["token_present"] is False
    assert "token" not in json.dumps(result).replace("token_env", "").replace("token_present", "")
