"""Config store behaviour: defaults, merging, host resolution, routing."""

from __future__ import annotations


def test_default_config_has_a_reachable_default_host(config_mod):
    cfg = config_mod.default_config()
    host = config_mod.resolve_host(cfg)
    assert host["url"].startswith("https://")
    assert host["token_env"]
    # Every configured host alias must resolve to itself, not silently fall back.
    for alias in cfg["hosts"]:
        assert config_mod.resolve_host(cfg, alias)["alias"] == alias


def test_save_then_load_round_trips(config_mod):
    cfg = config_mod.default_config()
    cfg["board_slug"] = "myboard"
    config_mod.save_config(cfg)
    assert config_mod.load_config()["board_slug"] == "myboard"


def test_load_merges_user_over_defaults_without_dropping_keys(config_mod):
    config_mod.save_config({"board_slug": "partial", "hosts": {"work": {"url": "https://git.corp"}}})
    cfg = config_mod.load_config()
    assert cfg["board_slug"] == "partial"
    # Nested merge keeps the default host alongside the new one.
    assert "work" in cfg["hosts"] and config_mod.DEFAULT_HOST_ALIAS in cfg["hosts"]
    # Untouched sections still carry their defaults.
    assert cfg["role_profiles"]["developer"]
    assert cfg["ingest"]["issues"] is True


def test_corrupt_config_falls_back_to_defaults(config_mod):
    from hermes_plugins.gitlab_kanban.paths import config_path

    config_path().write_text("{not json", encoding="utf-8")
    assert config_mod.load_config()["board_slug"] == config_mod.default_config()["board_slug"]


def test_unknown_alias_that_is_a_url_becomes_an_adhoc_host(config_mod):
    cfg = config_mod.default_config()
    host = config_mod.resolve_host(cfg, "https://gitlab.internal.lan/")
    assert host["url"] == "https://gitlab.internal.lan"


def test_unknown_alias_that_is_not_a_url_falls_back_to_default(config_mod):
    cfg = config_mod.default_config()
    host = config_mod.resolve_host(cfg, "typo-alias")
    assert host["alias"] == cfg["default_host"]


def test_self_managed_host_can_disable_ssl_verification(config_mod):
    cfg = config_mod.default_config()
    cfg["hosts"]["lab"] = {"url": "https://gitlab.lab", "token_env": "LAB_TOKEN", "verify_ssl": False}
    host = config_mod.resolve_host(cfg, "lab")
    assert host["verify_ssl"] is False
    assert host["token_env"] == "LAB_TOKEN"


def test_role_selection_prefers_first_matching_label(config_mod):
    cfg = config_mod.default_config()
    assert config_mod.role_for_labels(cfg, ["bug", "review"]) == "developer"
    assert config_mod.role_for_labels(cfg, ["review", "bug"]) == "reviewer"


def test_role_selection_falls_back_project_then_global(config_mod):
    cfg = config_mod.default_config()
    assert config_mod.role_for_labels(cfg, ["unknown-label"]) == cfg["default_role"]
    project = {"path": "g/r", "default_role": "qa"}
    assert config_mod.role_for_labels(cfg, ["unknown-label"], project) == "qa"


def test_role_maps_to_a_profile(config_mod):
    cfg = config_mod.default_config()
    assert config_mod.profile_for_role(cfg, "reviewer") == cfg["role_profiles"]["reviewer"]
    # An unmapped role degrades to the developer profile rather than crashing.
    assert config_mod.profile_for_role(cfg, "nonexistent-role")


def test_project_lookup_is_host_aware(config_mod):
    cfg = config_mod.default_config()
    cfg["projects"] = [
        {"host": "gitlab.com", "path": "g/r"},
        {"host": "work", "path": "g/r"},
    ]
    assert config_mod.find_project(cfg, "g/r", "work")["host"] == "work"
    assert config_mod.find_project(cfg, "g/r", "nope") is None
    assert config_mod.find_project(cfg, "/g/r/") is not None


def test_project_board_override_wins_over_global(config_mod):
    cfg = config_mod.default_config()
    assert config_mod.board_for_project(cfg, None)[0] == cfg["board_slug"]
    slug, name = config_mod.board_for_project(cfg, {"board_slug": "repo-board"})
    assert slug == "repo-board" and name


def test_auto_label_rules_match_by_field(config_mod):
    cfg = config_mod.default_config()
    cfg["auto_label_rules"] = [
        {"match": "title contains crash", "labels": ["bug"]},
        {"match": "body contains regression", "labels": ["urgent"]},
        {"match": "label contains sec", "labels": ["security"]},
    ]
    out = config_mod.apply_auto_labels(cfg, "Crash on save", "a regression", ["security-review"])
    assert set(out) >= {"bug", "urgent", "security", "security-review"}


def test_auto_label_rules_do_not_duplicate_or_crash_on_garbage(config_mod):
    cfg = config_mod.default_config()
    cfg["auto_label_rules"] = [
        {"match": "title contains crash", "labels": ["bug"]},
        {"match": "nonsense rule", "labels": ["ignored"]},
        {"match": "", "labels": ["ignored"]},
    ]
    out = config_mod.apply_auto_labels(cfg, "crash", "", ["bug"])
    assert out.count("bug") == 1
    assert "ignored" not in out
