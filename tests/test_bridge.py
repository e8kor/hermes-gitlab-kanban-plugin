"""Bridge behaviour: payload normalization, ingest gating, idempotency, sync-back."""

from __future__ import annotations

import pytest

from conftest import issue_payload, mr_payload


# --------------------------------------------------------------------------
# normalization
# --------------------------------------------------------------------------


def test_normalize_issue_payload(bridge_mod):
    event = bridge_mod.normalize_event(issue_payload())
    assert event["kind"] == "issue"
    assert event["iid"] == 42
    assert event["action"] == "open"
    assert event["project_path"] == "mygroup/myrepo"
    assert event["labels"] == ["bug"]
    assert event["author"] == "alice"


def test_normalize_merge_request_payload_keeps_branches(bridge_mod):
    event = bridge_mod.normalize_event(mr_payload())
    assert event["kind"] == "merge_request"
    assert event["iid"] == 17
    assert event["source_branch"] == "42-fix-crash"
    assert event["target_branch"] == "main"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"object_kind": "push"},
        {"object_kind": "pipeline", "object_attributes": {}},
        {"object_kind": "issue"},  # no object_attributes
        "not a dict",
    ],
)
def test_normalize_rejects_unsupported_payloads(bridge_mod, payload):
    assert bridge_mod.normalize_event(payload) is None


def test_normalize_accepts_string_labels(bridge_mod):
    event = bridge_mod.normalize_event(issue_payload(labels=["bug", "urgent"]))
    assert event["labels"] == ["bug", "urgent"]


def test_host_alias_is_matched_from_the_project_url(bridge_mod, config_mod):
    cfg = config_mod.default_config()
    cfg["hosts"]["work"] = {"url": "https://gitlab.example.com", "token_env": "W"}
    assert bridge_mod.host_alias_for_url(cfg, "https://gitlab.example.com/g/r") == "work"
    assert bridge_mod.host_alias_for_url(cfg, "https://elsewhere.io/g/r") is None


# --------------------------------------------------------------------------
# ingest gating
# --------------------------------------------------------------------------


def test_should_ingest_accepts_a_configured_open_issue(bridge_mod, config_mod):
    cfg = config_mod.default_config()
    event = bridge_mod.normalize_event(issue_payload())
    ok, reason = bridge_mod.should_ingest(cfg, event)
    assert ok, reason


def test_should_ingest_rejects_an_unwatched_action(bridge_mod, config_mod):
    cfg = config_mod.default_config()
    event = bridge_mod.normalize_event(issue_payload(object_attributes={"action": "update"}))
    ok, reason = bridge_mod.should_ingest(cfg, event)
    assert not ok and "update" in reason


def test_should_ingest_respects_the_kind_toggles(bridge_mod, config_mod):
    cfg = config_mod.default_config()
    cfg["ingest"]["merge_requests"] = False
    ok, reason = bridge_mod.should_ingest(cfg, bridge_mod.normalize_event(mr_payload()))
    assert not ok and "merge request" in reason
    # issues still flow
    assert bridge_mod.should_ingest(cfg, bridge_mod.normalize_event(issue_payload()))[0]


def test_should_ingest_enforces_the_project_allow_list(bridge_mod, config_mod):
    cfg = config_mod.default_config()
    cfg["projects"] = [{"host": "gitlab.com", "path": "other/repo"}]
    ok, reason = bridge_mod.should_ingest(cfg, bridge_mod.normalize_event(issue_payload()))
    assert not ok and "not onboarded" in reason
    cfg["projects"].append({"host": "gitlab.com", "path": "mygroup/myrepo"})
    assert bridge_mod.should_ingest(cfg, bridge_mod.normalize_event(issue_payload()))[0]


def test_empty_allow_list_accepts_any_project(bridge_mod, config_mod):
    cfg = config_mod.default_config()
    assert cfg["projects"] == []
    assert bridge_mod.should_ingest(cfg, bridge_mod.normalize_event(issue_payload()))[0]


def test_should_ingest_rejects_a_payload_without_an_iid(bridge_mod, config_mod):
    cfg = config_mod.default_config()
    event = bridge_mod.normalize_event(issue_payload(object_attributes={"iid": None}))
    ok, reason = bridge_mod.should_ingest(cfg, event)
    assert not ok and "iid" in reason


# --------------------------------------------------------------------------
# task body / link marker
# --------------------------------------------------------------------------


def test_task_body_carries_a_parseable_link_marker(bridge_mod, kanban_mod):
    event = bridge_mod.normalize_event(issue_payload())
    body = bridge_mod.build_task_body(event, "work", "developer", ["bug"])
    link = kanban_mod.parse_link(body)
    assert link == {"host": "work", "kind": "issue", "project": "mygroup/myrepo", "iid": 42}
    assert "Role: developer" in body


def test_link_marker_survives_a_body_with_other_urls(kanban_mod):
    body = (
        "gitlab-link: work merge_request mygroup/myrepo 17\n"
        "see also https://gitlab.example.com/other/repo/-/issues/99\n"
    )
    assert kanban_mod.parse_link(body)["iid"] == 17
    assert kanban_mod.parse_link(body)["kind"] == "merge_request"


def test_parse_link_returns_none_without_a_marker(kanban_mod):
    assert kanban_mod.parse_link("") is None
    assert kanban_mod.parse_link("a task with no marker at all") is None


def test_idempotency_key_is_stable_and_discriminating(kanban_mod):
    a = kanban_mod.idempotency_key("work", "issue", "g/r", 42)
    assert a == kanban_mod.idempotency_key("work", "issue", "g/r", 42)
    assert a != kanban_mod.idempotency_key("work", "merge_request", "g/r", 42)
    assert a != kanban_mod.idempotency_key("other", "issue", "g/r", 42)
    assert a != kanban_mod.idempotency_key("work", "issue", "g/r", 43)


# --------------------------------------------------------------------------
# ingest end to end (kanban CLI stubbed)
# --------------------------------------------------------------------------


class FakeKanban:
    """Records kanban calls instead of shelling out to a real board."""

    def __init__(self):
        self.created: list[dict] = []
        self.boards: list[tuple[str, str]] = []

    def ensure_board(self, slug, name):
        self.boards.append((slug, name))
        return True

    def create_task(self, board, title, **kwargs):
        self.created.append({"board": board, "title": title, **kwargs})
        return True, {"id": f"t_{len(self.created)}", "title": title}


def test_ingest_creates_a_task_routed_by_label(bridge_mod, config_mod, monkeypatch):
    config_mod.save_config(
        {
            "projects": [{"host": "gitlab.com", "path": "mygroup/myrepo"}],
            "board_slug": "gl",
        }
    )
    fake = FakeKanban()
    monkeypatch.setattr(bridge_mod.kanban, "ensure_board", fake.ensure_board)
    monkeypatch.setattr(bridge_mod.kanban, "create_task", fake.create_task)

    result = bridge_mod.ingest_event(issue_payload())
    assert result["created"] is True
    assert result["role"] == "developer"
    assert result["board"] == "gl"
    call = fake.created[0]
    # The developer role must arrive with its playbook attached.
    assert "gitlab-kanban:gitlab-development" in call["skills"]
    # And with a dedup key, so a redelivery cannot double-create.
    assert call["idem_key"] == "gitlab:gitlab.com:issue:mygroup/myrepo:42"


def test_ingest_routes_a_merge_request_to_the_reviewer(bridge_mod, config_mod, monkeypatch):
    config_mod.save_config({"projects": [{"host": "gitlab.com", "path": "mygroup/myrepo"}]})
    fake = FakeKanban()
    monkeypatch.setattr(bridge_mod.kanban, "ensure_board", fake.ensure_board)
    monkeypatch.setattr(bridge_mod.kanban, "create_task", fake.create_task)

    result = bridge_mod.ingest_event(mr_payload())
    assert result["role"] == "reviewer"
    assert "gitlab-kanban:gitlab-code-review" in fake.created[0]["skills"]


def test_ingest_ignores_an_unhandled_payload_without_touching_the_board(bridge_mod, monkeypatch):
    fake = FakeKanban()
    monkeypatch.setattr(bridge_mod.kanban, "ensure_board", fake.ensure_board)
    monkeypatch.setattr(bridge_mod.kanban, "create_task", fake.create_task)
    result = bridge_mod.ingest_event({"object_kind": "push"})
    assert result["ignored"] is True
    assert fake.created == [] and fake.boards == []


def test_ingest_of_a_non_onboarded_project_creates_nothing(bridge_mod, config_mod, monkeypatch):
    config_mod.save_config({"projects": [{"host": "gitlab.com", "path": "someone/else"}]})
    fake = FakeKanban()
    monkeypatch.setattr(bridge_mod.kanban, "ensure_board", fake.ensure_board)
    monkeypatch.setattr(bridge_mod.kanban, "create_task", fake.create_task)
    result = bridge_mod.ingest_event(issue_payload())
    assert result["ignored"] is True
    assert fake.created == []


# --------------------------------------------------------------------------
# sync-back
# --------------------------------------------------------------------------


class FakeClient:
    """Captures the GitLab writes sync-back attempts."""

    def __init__(self):
        self.notes: list[tuple[str, int, str]] = []
        self.updates: list[tuple[str, int, dict]] = []

    def comment_issue(self, project, iid, body):
        self.notes.append(("issue", iid, body))
        return {"id": 1}

    def comment_merge_request(self, project, iid, body):
        self.notes.append(("merge_request", iid, body))
        return {"id": 2}

    def update_issue(self, project, iid, payload):
        self.updates.append(("issue", iid, payload))
        return {"iid": iid}

    def update_merge_request(self, project, iid, payload):
        self.updates.append(("merge_request", iid, payload))
        return {"iid": iid}


@pytest.fixture
def done_issue_task(kanban_mod):
    return {
        "id": "t_1",
        "title": "Crash on empty input",
        "status": "done",
        "result": "Fixed in !17, tests green.",
        "body": kanban_mod.link_line("work", "issue", "mygroup/myrepo", 42),
    }


def _patch_client(bridge_mod, monkeypatch, client):
    monkeypatch.setattr(bridge_mod.GitLabClient, "for_host", classmethod(lambda cls, *a, **k: client))


def test_sync_back_comments_and_closes_an_issue(bridge_mod, config_mod, monkeypatch, done_issue_task):
    client = FakeClient()
    _patch_client(bridge_mod, monkeypatch, client)
    state: dict = {}
    result = bridge_mod.sync_back_task("gl", done_issue_task, cfg=config_mod.default_config(), state=state)
    assert result["synced"] is True
    assert client.notes and "Fixed in !17" in client.notes[0][2]
    assert client.updates[0][2]["state_event"] == "close"
    assert state["t_1"]["ok"] is True


def test_sync_back_is_idempotent_on_replay(bridge_mod, config_mod, monkeypatch, done_issue_task):
    client = FakeClient()
    _patch_client(bridge_mod, monkeypatch, client)
    cfg = config_mod.default_config()
    state: dict = {}
    bridge_mod.sync_back_task("gl", done_issue_task, cfg=cfg, state=state)
    second = bridge_mod.sync_back_task("gl", done_issue_task, cfg=cfg, state=state)
    assert second["skipped"] is True and second["reason"] == "already synced"
    assert len(client.notes) == 1 and len(client.updates) == 1


def test_sync_back_skips_a_task_without_a_link(bridge_mod, config_mod, monkeypatch):
    client = FakeClient()
    _patch_client(bridge_mod, monkeypatch, client)
    result = bridge_mod.sync_back_task(
        "gl", {"id": "t_9", "body": "manual task"}, cfg=config_mod.default_config(), state={}
    )
    assert result["skipped"] is True
    assert client.notes == [] and client.updates == []


def test_sync_back_does_not_close_a_merge_request_by_default(bridge_mod, config_mod, monkeypatch, kanban_mod):
    client = FakeClient()
    _patch_client(bridge_mod, monkeypatch, client)
    task = {
        "id": "t_2",
        "result": "Reviewed: approved.",
        "body": kanban_mod.link_line("work", "merge_request", "mygroup/myrepo", 17),
    }
    result = bridge_mod.sync_back_task("gl", task, cfg=config_mod.default_config(), state={})
    assert result["synced"] is True
    assert client.notes[0][0] == "merge_request"
    # Closing an MR is opt-in — a completed review must not close someone's MR.
    assert client.updates == []


def test_sync_back_can_be_configured_to_label_instead_of_close(bridge_mod, config_mod, monkeypatch, done_issue_task):
    client = FakeClient()
    _patch_client(bridge_mod, monkeypatch, client)
    cfg = config_mod.default_config()
    cfg["sync_back"].update({"close_issue": False, "labels_on_done": ["done-by-hermes"]})
    bridge_mod.sync_back_task("gl", done_issue_task, cfg=cfg, state={})
    payload = client.updates[0][2]
    assert "state_event" not in payload
    assert payload["add_labels"] == "done-by-hermes"


def test_dry_run_writes_nothing_and_records_nothing(bridge_mod, config_mod, monkeypatch, done_issue_task):
    client = FakeClient()
    _patch_client(bridge_mod, monkeypatch, client)
    state: dict = {}
    result = bridge_mod.sync_back_task(
        "gl", done_issue_task, cfg=config_mod.default_config(), state=state, dry_run=True
    )
    assert result["dry_run"] is True and result["actions"]
    assert client.notes == [] and client.updates == []
    assert state == {}


def test_sync_back_records_errors_and_does_not_mark_synced(bridge_mod, config_mod, monkeypatch, done_issue_task, api_mod):
    class Failing(FakeClient):
        def comment_issue(self, project, iid, body):
            raise api_mod.GitLabError(403, "insufficient scope")

        def update_issue(self, project, iid, payload):
            raise api_mod.GitLabError(403, "insufficient scope")

    _patch_client(bridge_mod, monkeypatch, Failing())
    state: dict = {}
    result = bridge_mod.sync_back_task("gl", done_issue_task, cfg=config_mod.default_config(), state=state)
    assert result["synced"] is False and result["errors"]
    # A failure must stay retryable on the next sweep.
    assert state == {}


def test_sync_state_round_trips(bridge_mod):
    bridge_mod.save_sync_state({"t_1": {"ok": True}})
    assert bridge_mod.load_sync_state()["t_1"]["ok"] is True


def test_sweep_only_syncs_unsynced_done_tasks(bridge_mod, config_mod, monkeypatch, done_issue_task):
    client = FakeClient()
    _patch_client(bridge_mod, monkeypatch, client)
    config_mod.save_config({"board_slug": "gl"})
    monkeypatch.setattr(bridge_mod.kanban, "list_tasks", lambda *a, **k: [done_issue_task])

    first = bridge_mod.sweep("gl")
    assert first["synced"] == 1
    second = bridge_mod.sweep("gl")
    assert second["synced"] == 0 and second["skipped"] == 1
    assert len(client.notes) == 1
