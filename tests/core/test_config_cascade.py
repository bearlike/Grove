"""Cascade resolver: layer merging, agent-by-name merge, env vars, validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grove.core import paths as paths_mod
from grove.core.config import (
    GroveConfig,
    _deep_merge,
    _merge_agents,
    _parse_env_overrides,
    expand_template,
    load_config,
)
from grove.core.errors import ConfigError

# ─── deep_merge ─────────────────────────────────────────────────────────────


def test_deep_merge_last_wins_for_scalars() -> None:
    merged = _deep_merge({"x": 1}, {"x": 2})
    assert merged == {"x": 2}


def test_deep_merge_recurses_into_dicts() -> None:
    merged = _deep_merge({"a": {"b": 1, "c": 1}}, {"a": {"b": 2}})
    assert merged == {"a": {"b": 2, "c": 1}}


def test_deep_merge_replaces_non_agent_lists_wholesale() -> None:
    merged = _deep_merge({"items": [1, 2, 3]}, {"items": [9]})
    assert merged == {"items": [9]}


def test_deep_merge_agents_merge_by_name() -> None:
    base = {"agents": [{"name": "a", "command": "x"}, {"name": "b", "command": "y"}]}
    overlay = {"agents": [{"name": "b", "command": "Y2"}, {"name": "c", "command": "z"}]}
    merged = _deep_merge(base, overlay)
    assert merged["agents"] == [
        {"name": "a", "command": "x"},
        {"name": "b", "command": "Y2"},
        {"name": "c", "command": "z"},
    ]


# ─── _merge_agents directly ─────────────────────────────────────────────────


def test_merge_agents_preserves_order() -> None:
    base = [{"name": "a"}, {"name": "b"}]
    overlay = [{"name": "c"}, {"name": "a"}]
    assert _merge_agents(base, overlay) == [
        {"name": "a"},
        {"name": "b"},
        {"name": "c"},
    ]


# ─── env var parsing ────────────────────────────────────────────────────────


def test_parse_env_basic() -> None:
    assert _parse_env_overrides({"GROVE_WORKTREE__BRANCH_PREFIX": "feat/"}) == {
        "worktree": {"branch_prefix": "feat/"}
    }


def test_parse_env_ignores_unrelated_vars() -> None:
    assert _parse_env_overrides({"PATH": "/", "GROVE_": "x"}) == {}


def test_parse_env_nested_two_levels() -> None:
    out = _parse_env_overrides({"GROVE_INIT_SCRIPT__TIMEOUT_SECONDS": "60"})
    assert out == {"init_script": {"timeout_seconds": "60"}}


# ─── load_config end-to-end ─────────────────────────────────────────────────


def test_load_defaults_when_no_layers(
    tmp_state_dir: Path, tmp_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del tmp_state_dir
    monkeypatch.delenv("GROVE_WORKTREE__BRANCH_PREFIX", raising=False)
    cfg = load_config(tmp_repo, env={})
    assert cfg.worktree.branch_prefix == "grove/"
    assert {a.name for a in cfg.agents} == {"claude", "shell"}


def test_tmux_config_has_peek_refresh_defaults() -> None:
    """Two cadences for the rail: a fast pane-only tick (~250 ms) and a
    slower full-stats tick (~3 s). Defaults are the recommended values
    from the design — users can dial them up or down.
    """
    cfg = GroveConfig.model_validate({})
    assert cfg.tmux.peek_pane_refresh_seconds == 0.25
    assert cfg.tmux.peek_stats_refresh_seconds == 3.0


def test_tmux_peek_refresh_can_be_overridden_via_layer(tmp_state_dir: Path, tmp_repo: Path) -> None:
    del tmp_state_dir
    paths_mod.user_config_path().parent.mkdir(parents=True, exist_ok=True)
    paths_mod.user_config_path().write_text(
        json.dumps(
            {
                "tmux": {
                    "peek_pane_refresh_seconds": 0.5,
                    "peek_stats_refresh_seconds": 5.0,
                }
            }
        ),
        encoding="utf-8",
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.tmux.peek_pane_refresh_seconds == 0.5
    assert cfg.tmux.peek_stats_refresh_seconds == 5.0


def test_user_layer_overrides_defaults(tmp_state_dir: Path, tmp_repo: Path) -> None:
    del tmp_state_dir
    user_path = paths_mod.user_config_path()
    user_path.parent.mkdir(parents=True, exist_ok=True)
    user_path.write_text(json.dumps({"worktree": {"branch_prefix": "user/"}}), encoding="utf-8")
    cfg = load_config(tmp_repo, env={})
    assert cfg.worktree.branch_prefix == "user/"


def test_project_overrides_user(tmp_state_dir: Path, tmp_repo: Path) -> None:
    del tmp_state_dir
    paths_mod.user_config_path().parent.mkdir(parents=True, exist_ok=True)
    paths_mod.user_config_path().write_text(
        json.dumps({"worktree": {"branch_prefix": "user/"}}), encoding="utf-8"
    )
    project = paths_mod.project_config_path(tmp_repo)
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text(json.dumps({"worktree": {"branch_prefix": "team/"}}), encoding="utf-8")
    cfg = load_config(tmp_repo, env={})
    assert cfg.worktree.branch_prefix == "team/"


def test_local_overrides_project(tmp_state_dir: Path, tmp_repo: Path) -> None:
    del tmp_state_dir
    project = paths_mod.project_config_path(tmp_repo)
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text(json.dumps({"worktree": {"branch_prefix": "team/"}}), encoding="utf-8")
    local = paths_mod.project_local_config_path(tmp_repo)
    local.write_text(json.dumps({"worktree": {"branch_prefix": "me/"}}), encoding="utf-8")
    cfg = load_config(tmp_repo, env={})
    assert cfg.worktree.branch_prefix == "me/"


def test_env_overrides_all_files(tmp_state_dir: Path, tmp_repo: Path) -> None:
    del tmp_state_dir
    paths_mod.user_config_path().parent.mkdir(parents=True, exist_ok=True)
    paths_mod.user_config_path().write_text(
        json.dumps({"worktree": {"branch_prefix": "user/"}}), encoding="utf-8"
    )
    cfg = load_config(tmp_repo, env={"GROVE_WORKTREE__BRANCH_PREFIX": "envwins/"})
    assert cfg.worktree.branch_prefix == "envwins/"


def test_cli_overrides_env(tmp_state_dir: Path, tmp_repo: Path) -> None:
    del tmp_state_dir
    cfg = load_config(
        tmp_repo,
        env={"GROVE_WORKTREE__BRANCH_PREFIX": "envwins/"},
        cli_overrides={"worktree": {"branch_prefix": "cliwins/"}},
    )
    assert cfg.worktree.branch_prefix == "cliwins/"


def test_unknown_top_level_field_raises(tmp_state_dir: Path, tmp_repo: Path) -> None:
    del tmp_state_dir
    paths_mod.user_config_path().parent.mkdir(parents=True, exist_ok=True)
    paths_mod.user_config_path().write_text(json.dumps({"unknown_field": True}), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_repo, env={})


def test_repo_placeholder_left_literal_in_stored_value(tmp_state_dir: Path, tmp_repo: Path) -> None:
    del tmp_state_dir
    paths_mod.user_config_path().parent.mkdir(parents=True, exist_ok=True)
    paths_mod.user_config_path().write_text(
        json.dumps({"worktree": {"root_template": "${repo}/.trees"}}),
        encoding="utf-8",
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.worktree.root_template == "${repo}/.trees"


# ─── expand_template ────────────────────────────────────────────────────────


def test_expand_repo_placeholder() -> None:
    out = expand_template("${repo}/.trees", Path("/abs/repo"))
    assert out == Path("/abs/repo/.trees")


def test_expand_repo_name_placeholder() -> None:
    out = expand_template("~/grove/${repo_name}", Path("/abs/myproj"))
    assert out.name == "myproj"


# ─── round-trip ─────────────────────────────────────────────────────────────


def test_round_trip_dump_then_validate() -> None:
    cfg = GroveConfig()
    text = cfg.model_dump_json(indent=2, by_alias=True)
    again = GroveConfig.model_validate_json(text)
    assert again == cfg
