"""Workspace create-form defaults: user-first cascade and scoped persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grove.core import paths as paths_mod
from grove.core.config import (
    DefaultsScope,
    WorkspaceDefaults,
    load_config,
    save_workspace_defaults,
)
from grove.core.errors import ConfigError


def test_user_default_field_outranks_project_without_hiding_other_project_default(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """A saved personal choice wins only its own field, not the whole section."""
    del tmp_state_dir
    user = paths_mod.user_config_path()
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text(json.dumps({"defaults": {"agent": "claude"}}), encoding="utf-8")

    project = paths_mod.project_config_path(tmp_repo)
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text(
        json.dumps({"defaults": {"agent": "codex", "runtime": "host"}}),
        encoding="utf-8",
    )

    cfg = load_config(tmp_repo, env={})

    assert cfg.defaults.agent == "claude"
    assert cfg.defaults.runtime == "host"


def test_user_default_field_outranks_project_local_default(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """Machine-local project config cannot replace the user's saved form answer."""
    del tmp_state_dir
    user = paths_mod.user_config_path()
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text(json.dumps({"defaults": {"brief": True}}), encoding="utf-8")

    local = paths_mod.project_local_config_path(tmp_repo)
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(
        json.dumps({"defaults": {"brief": False, "branch_mode": "existing"}}),
        encoding="utf-8",
    )

    cfg = load_config(tmp_repo, env={})

    assert cfg.defaults.brief is True
    assert cfg.defaults.branch_mode == "existing"


def test_env_and_cli_defaults_still_outrank_user_defaults(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """The inversion is only user versus project layers, never an env/CLI demotion."""
    del tmp_state_dir
    user = paths_mod.user_config_path()
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text(json.dumps({"defaults": {"agent": "claude"}}), encoding="utf-8")

    from_env = load_config(tmp_repo, env={"GROVE_DEFAULTS__AGENT": "codex"})
    from_cli = load_config(
        tmp_repo,
        cli_overrides={"defaults": {"agent": "shell"}},
        env={},
    )

    assert from_env.defaults.agent == "codex"
    assert from_cli.defaults.agent == "shell"


def test_project_still_outranks_user_outside_defaults(tmp_state_dir: Path, tmp_repo: Path) -> None:
    """The create-form exception must not invert precedence for ordinary config."""
    del tmp_state_dir
    user = paths_mod.user_config_path()
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text(json.dumps({"worktree": {"branch_prefix": "user/"}}), encoding="utf-8")

    project = paths_mod.project_config_path(tmp_repo)
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text(json.dumps({"worktree": {"branch_prefix": "project/"}}), encoding="utf-8")

    assert load_config(tmp_repo, env={}).worktree.branch_prefix == "project/"


@pytest.mark.parametrize("scope", list(DefaultsScope))
def test_save_workspace_defaults_round_trips_only_set_fields(
    scope: DefaultsScope, tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """Every scope writes a minimal raw layer that its regular cascade reads."""
    del tmp_state_dir
    defaults = WorkspaceDefaults(agent="claude", branch_mode="new")

    target = save_workspace_defaults(defaults, scope=scope, repo_root=tmp_repo)

    assert target == scope.path(tmp_repo)
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "defaults": {"agent": "claude", "branch_mode": "new"}
    }
    assert load_config(tmp_repo, env={}).defaults == defaults


def test_resaving_workspace_defaults_replaces_the_whole_defaults_object(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """Clearing a form field clears its saved value instead of retaining stale JSON."""
    del tmp_state_dir
    save_workspace_defaults(
        WorkspaceDefaults(agent="claude", runtime="container"),
        scope=DefaultsScope.USER,
        repo_root=tmp_repo,
    )

    target = save_workspace_defaults(
        WorkspaceDefaults(agent="codex"), scope=DefaultsScope.USER, repo_root=tmp_repo
    )

    assert json.loads(target.read_text(encoding="utf-8")) == {"defaults": {"agent": "codex"}}
    cfg = load_config(tmp_repo, env={})
    assert cfg.defaults.agent == "codex"
    assert cfg.defaults.runtime is None


def test_save_workspace_defaults_preserves_other_raw_layer_content(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """Saving one section must not bake or erase unrelated project configuration."""
    del tmp_state_dir
    project = paths_mod.project_config_path(tmp_repo)
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text(json.dumps({"worktree": {"branch_prefix": "team/"}}), encoding="utf-8")

    target = save_workspace_defaults(
        WorkspaceDefaults(skip_init=True), scope=DefaultsScope.PROJECT, repo_root=tmp_repo
    )

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "worktree": {"branch_prefix": "team/"},
        "defaults": {"skip_init": True},
    }


@pytest.mark.parametrize("scope", [DefaultsScope.PROJECT, DefaultsScope.PROJECT_LOCAL])
def test_project_default_scopes_require_a_repository(scope: DefaultsScope) -> None:
    with pytest.raises(ConfigError, match="repository root"):
        scope.path(None)


def test_unknown_defaults_field_fails_config_load(tmp_state_dir: Path, tmp_repo: Path) -> None:
    """Strict validation reaches nested defaults rather than accepting a typo."""
    del tmp_state_dir
    user = paths_mod.user_config_path()
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text(json.dumps({"defaults": {"not_a_default": True}}), encoding="utf-8")

    with pytest.raises(ConfigError, match="defaults"):
        load_config(tmp_repo, env={})
