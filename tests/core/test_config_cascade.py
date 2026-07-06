"""Cascade resolver: layer merging, agent-by-name merge, env vars, validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

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


def test_parse_env_ignores_non_config_grove_vars() -> None:
    """GROVE_* is a shared namespace; only real config fields are consumed (#105).

    GROVE_DEBUG (the documented debug switch) and the provider token vars
    config itself names must never reach the strict model — they used to
    hard-fail every config load with extra_forbidden.
    """
    out = _parse_env_overrides(
        {
            "GROVE_DEBUG": "1",
            "GROVE_GITEA_TOKEN": "tok",
            "GROVE_GITHUB_TOKEN": "tok",
            "GROVE_INSTALL_SPEC": "grove @ file:///src",
            "GROVE_UI__THEME": "grove-dark",
        }
    )
    assert out == {"ui": {"theme": "grove-dark"}}


def test_load_config_survives_grove_debug_env(tmp_state_dir: Path, tmp_repo: Path) -> None:
    del tmp_state_dir  # fixture used for its path-redirect side effect
    cfg = load_config(tmp_repo, env={"GROVE_DEBUG": "1", "GROVE_GITEA_TOKEN": "tok"})
    assert cfg.worktree.root_template  # defaults intact, no ConfigError


def test_project_agents_refine_builtins_instead_of_replacing(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """Built-in agents are layer 0 of the cascade (#105).

    A project config that redeclares `claude` (the `grove config init`
    scaffold's shape) must refine the built-in — keeping kind="claude_code"
    per the `_merge_agents` contract — and must NOT hide codex/shell.
    Before the fix the scaffold roster replaced the built-ins wholesale, so
    the documented first-run flow (`config init` → create with `shell`)
    failed with "unknown agent" and claude lost dashboard tracking.
    """
    del tmp_state_dir
    project = paths_mod.project_config_path(tmp_repo)
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text(
        json.dumps({"agents": [{"name": "claude", "command": "my-claude"}]}),
        encoding="utf-8",
    )
    cfg = load_config(tmp_repo, env={})
    assert {a.name for a in cfg.agents} == {"claude", "codex", "shell"}
    claude = cfg.find_agent("claude")
    assert claude is not None
    assert claude.command == "my-claude"  # the override wins field-by-field
    assert claude.kind == "claude_code"  # inherited from the built-in base


def test_user_agent_override_inherits_builtin_kind(tmp_state_dir: Path, tmp_repo: Path) -> None:
    """A USER config redeclaring a built-in agent by name inherits the built-in's
    unspecified fields field-wise (#119b) — it must not silently drop `claude`
    to kind="generic" and kill dashboard tracking. The mechanism is the layer-0
    built-in roster (#105) resolved through field-level `_merge_agents`; this
    pins the user-layer arm the issue names (the project-layer arm is covered by
    `test_project_agents_refine_builtins_instead_of_replacing`)."""
    del tmp_state_dir
    user = paths_mod.user_config_path()
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text(
        json.dumps({"agents": [{"name": "claude", "command": "my-claude"}]}),
        encoding="utf-8",
    )
    cfg = load_config(tmp_repo, env={})
    assert {a.name for a in cfg.agents} == {"claude", "codex", "shell"}
    claude = cfg.find_agent("claude")
    assert claude is not None
    assert claude.command == "my-claude"  # the override wins field-by-field
    assert claude.kind == "claude_code"  # inherited from the built-in base


# ─── load_config end-to-end ─────────────────────────────────────────────────


def test_load_defaults_when_no_layers(
    tmp_state_dir: Path, tmp_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del tmp_state_dir
    monkeypatch.delenv("GROVE_WORKTREE__BRANCH_PREFIX", raising=False)
    cfg = load_config(tmp_repo, env={})
    assert cfg.worktree.branch_prefix == "grove/"
    assert {a.name for a in cfg.agents} == {"claude", "codex", "shell"}


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


# ─── mewbo section (#35) ────────────────────────────────────────────────────


def test_mewbo_section_defaults_and_round_trip() -> None:
    """The mewbo section validates, overrides, and survives dump→validate.
    ``api_key_env`` is the env-var NAME, never a literal secret."""
    cfg = GroveConfig.model_validate({"mewbo": {"base_url": "http://127.0.0.1:9999"}})
    assert cfg.mewbo.base_url == "http://127.0.0.1:9999"
    assert cfg.mewbo.api_key_env == "MEWBO_API_KEY"
    assert cfg.mewbo.timeout_seconds == 10.0
    again = GroveConfig.model_validate_json(cfg.model_dump_json(indent=2, by_alias=True))
    assert again.mewbo == cfg.mewbo


def test_mewbo_section_forbids_unknown_fields() -> None:
    """``extra="forbid"`` holds for the new section — a literal ``api_key``
    (the typo'd or secret-leaking shape) is rejected at validation."""
    with pytest.raises(ValidationError):
        GroveConfig.model_validate({"mewbo": {"api_key": "literal-secret"}})


def test_agent_spec_accepts_mewbo_kind() -> None:
    cfg = GroveConfig.model_validate(
        {"agents": [{"name": "mewbo", "command": "true", "kind": "mewbo"}]}
    )
    spec = cfg.find_agent("mewbo")
    assert spec is not None
    assert spec.kind == "mewbo"


# ─── tickets section (#7) ────────────────────────────────────────────────────


def test_tickets_section_defaults_off_and_secret_free() -> None:
    """All providers default disabled; only token-env NAMES are stored, never
    a literal secret (the repo is published)."""
    cfg = GroveConfig()
    assert cfg.tickets.gitea.enabled is False
    assert cfg.tickets.github.enabled is False
    assert cfg.tickets.linear.enabled is False
    assert cfg.tickets.gitea.token_env == "GROVE_GITEA_TOKEN"
    assert cfg.tickets.github.token_env == "GROVE_GITHUB_TOKEN"
    assert cfg.tickets.linear.token_env == "GROVE_LINEAR_TOKEN"


def test_tickets_section_overrides_and_round_trip() -> None:
    cfg = GroveConfig.model_validate(
        {
            "tickets": {
                "gitea": {"enabled": True, "owner": "bearlike", "repo": "Grove"},
                "linear": {"enabled": True, "team_key": "ENG"},
            }
        }
    )
    assert cfg.tickets.gitea.enabled is True
    assert cfg.tickets.gitea.owner == "bearlike"
    assert cfg.tickets.linear.team_key == "ENG"
    again = GroveConfig.model_validate_json(cfg.model_dump_json(indent=2, by_alias=True))
    assert again.tickets == cfg.tickets


def test_tickets_section_forbids_unknown_fields() -> None:
    """``extra="forbid"`` rejects a literal ``token`` (the secret-leaking shape)."""
    with pytest.raises(ValidationError):
        GroveConfig.model_validate({"tickets": {"gitea": {"token": "literal-secret"}}})


def test_tickets_provider_layers_merge_per_field() -> None:
    """A project layer enabling a provider deep-merges over the global default."""
    base = {"tickets": {"gitea": {"owner": "bearlike", "repo": "Grove"}}}
    overlay = {"tickets": {"gitea": {"enabled": True}}}
    merged = _deep_merge(base, overlay)
    cfg = GroveConfig.model_validate(merged)
    assert cfg.tickets.gitea.enabled is True
    assert cfg.tickets.gitea.owner == "bearlike"  # base field survived the merge
