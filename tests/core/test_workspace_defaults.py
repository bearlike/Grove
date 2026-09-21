"""Workspace create defaults: user-first cascade, scoped persistence, and the
one resolution both a create and a create FORM read.

The second half of this file exists because those two used to be different
code. `WorkspaceDefaultsView` honoured `defaults.runtime`; the engine read
`container.enabled`, which defaults to True — so a user whose saved default was
`host` was shown Host by every form and handed a container by every create that
did not repeat the resolution client-side. Each test below pins one field of
that agreement.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grove.core import paths as paths_mod
from grove.core.config import (
    DefaultsScope,
    GroveConfig,
    WorkspaceDefaults,
    load_config,
    save_workspace_defaults,
)
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.contracts.views import WorkspaceDefaultsView
from grove.core.errors import ConfigError
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Runtime
from tests.conftest import FakeCli, FakePreflight, FakeTmux


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


# ─── the engine and the form resolve one answer ─────────────────────────────


def _cfg(tmp_path: Path, *, models: tuple[str, ...] = (), **defaults: object) -> GroveConfig:
    """A config whose CONTAINER SECTION IS ON — the shipped default, and the
    only setting under which the drift these tests pin was reachable."""
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "container": {"enabled": True},
            "init_script": {"enabled": True, "shell": "bash", "inline": "true"},
            "agents": [
                {
                    "name": "claude",
                    "command": "claude",
                    "kind": "claude_code",
                    "models": models,
                }
            ],
            "defaults": defaults,
        }
    )


def _manager(tmp_repo: Path, cfg: GroveConfig, tmp_path: Path) -> WorkspaceManager:
    cli = FakeCli()
    return WorkspaceManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=cli,
        preflight=FakePreflight(devcontainer_cli=cli),  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("saved", "expected"),
    [("host", Runtime.HOST), ("container", Runtime.CONTAINER), (None, Runtime.CONTAINER)],
)
def test_a_create_naming_no_runtime_takes_the_saved_default_over_container_enabled(
    saved: str | None, expected: Runtime, tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """`defaults.runtime` outranks `container.enabled` for an unopinionated create.

    The `None` row is the load-bearing control: with no saved answer the section
    default still decides, so this honours a preference rather than disabling
    containers-by-default.
    """
    cfg = _cfg(tmp_path, runtime=saved) if saved else _cfg(tmp_path)
    state = _manager(tmp_repo, cfg, tmp_path).create(
        CreateWorkspaceRequest(agent_name="claude", title="unopinionated")
    )

    assert state.runtime is expected
    # Not a fallback: nothing was unavailable, the user simply said so.
    assert state.runtime_fallback_reason is None


def test_an_explicit_runtime_still_beats_the_saved_default(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Precedence order is unchanged — a request that names a field wins."""
    cfg = _cfg(tmp_path, runtime="host")
    state = _manager(tmp_repo, cfg, tmp_path).create(
        CreateWorkspaceRequest(agent_name="claude", title="explicit", runtime=Runtime.CONTAINER)
    )

    assert state.runtime is Runtime.CONTAINER


@pytest.mark.parametrize("saved", ["host", "container"])
def test_the_form_view_reports_exactly_what_an_untouched_create_does(
    saved: str, tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The drift pin: the view a form displays and the runtime a create records
    are one answer, asserted against each other rather than each against a
    literal — a literal on both sides is what let them agree in the test and
    disagree in production."""
    cfg = _cfg(tmp_path, runtime=saved)
    state = _manager(tmp_repo, cfg, tmp_path).create(
        CreateWorkspaceRequest(agent_name="claude", title="agreement")
    )

    assert WorkspaceDefaultsView.from_config(cfg).runtime == state.runtime.value


def test_a_create_naming_no_model_forwards_the_saved_one_to_the_agent(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A saved model reaches the launch argv, which is the only place it does
    anything — omitting the field used to drop it silently."""
    cfg = _cfg(tmp_path, runtime="host", model="anthropic-opus-5[1m]")
    state = _manager(tmp_repo, cfg, tmp_path).create(
        CreateWorkspaceRequest(agent_name="claude", title="modelled")
    )

    decorations = dict(fake_tmux.launch_decorations)
    assert decorations[state.tmux_session][-2:] == ["--model", "anthropic-opus-5[1m]"]


def test_a_plain_saved_model_promotes_to_its_offered_context_variant(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Deleting default-only promotion would re-launch the smaller saved id."""
    cfg = _cfg(
        tmp_path,
        runtime="host",
        model="anthropic-opus-5",
        models=("anthropic-opus-5", "anthropic-opus-5[1m]"),
    )
    state = _manager(tmp_repo, cfg, tmp_path).create(
        CreateWorkspaceRequest(agent_name="claude", title="promoted")
    )

    assert dict(fake_tmux.launch_decorations)[state.tmux_session][-2:] == [
        "--model",
        "anthropic-opus-5[1m]",
    ]


def test_a_plain_saved_model_stays_unchanged_when_its_twin_is_not_offered(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Removing the catalog-membership guard would invent an unsupported id."""
    cfg = _cfg(
        tmp_path,
        runtime="host",
        model="anthropic-opus-5",
        models=("anthropic-opus-5",),
    )
    state = _manager(tmp_repo, cfg, tmp_path).create(
        CreateWorkspaceRequest(agent_name="claude", title="not-promoted")
    )

    assert dict(fake_tmux.launch_decorations)[state.tmux_session][-2:] == [
        "--model",
        "anthropic-opus-5",
    ]


def test_an_explicit_model_is_forwarded_verbatim_despite_an_offered_twin(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Replacing explicit input with a catalog choice would narrow the provider boundary."""
    cfg = _cfg(
        tmp_path,
        runtime="host",
        model="anthropic-opus-5",
        models=("anthropic-opus-5", "anthropic-opus-5[1m]"),
    )
    state = _manager(tmp_repo, cfg, tmp_path).create(
        CreateWorkspaceRequest(
            agent_name="claude",
            title="explicit",
            model="anthropic-opus-5",
        )
    )

    assert dict(fake_tmux.launch_decorations)[state.tmux_session][-2:] == [
        "--model",
        "anthropic-opus-5",
    ]


def test_an_already_marked_saved_model_is_unchanged(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Appending the marker unconditionally would corrupt an explicit capability choice."""
    cfg = _cfg(
        tmp_path,
        runtime="host",
        model="anthropic-opus-5[1m]",
        models=("anthropic-opus-5[1m]", "anthropic-opus-5[1m][1m]"),
    )
    state = _manager(tmp_repo, cfg, tmp_path).create(
        CreateWorkspaceRequest(agent_name="claude", title="already-marked")
    )

    assert dict(fake_tmux.launch_decorations)[state.tmux_session][-2:] == [
        "--model",
        "anthropic-opus-5[1m]",
    ]


def test_with_no_saved_model_the_agent_is_launched_with_no_model_flag_at_all(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The control for the test above, and the fact every create FORM has to
    agree with: with nothing saved and nothing requested, Grove names no model
    and the tool picks its own. A pill that displays a catalog entry here is
    naming something that will not be used."""
    cfg = _cfg(tmp_path, runtime="host")
    state = _manager(tmp_repo, cfg, tmp_path).create(
        CreateWorkspaceRequest(agent_name="claude", title="unmodelled")
    )

    assert "--model" not in dict(fake_tmux.launch_decorations)[state.tmux_session]


def test_a_create_naming_no_skip_init_takes_the_saved_one(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """`skip_init` is `None`, not `False`, precisely so this is expressible."""
    cfg = _cfg(tmp_path, runtime="host", skip_init=True)
    state = _manager(tmp_repo, cfg, tmp_path).create(
        CreateWorkspaceRequest(agent_name="claude", title="uninitialised")
    )

    assert state.init_status is not None
    assert state.init_status.value == "skipped"


def test_a_create_asking_for_the_init_script_still_gets_it(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """`skip_init=False` is a real answer, not the absence of one."""
    cfg = _cfg(tmp_path, runtime="host", skip_init=True)
    state = _manager(tmp_repo, cfg, tmp_path).create(
        CreateWorkspaceRequest(agent_name="claude", title="initialised", skip_init=False)
    )

    assert state.init_status is not None
    assert state.init_status.value != "skipped"


def test_a_saved_model_id_is_held_to_the_same_rule_as_a_requested_one() -> None:
    """The saved default now becomes an argv token, so a flag-shaped id has to
    be refused where it is DEFINED — the wire's pattern alone protected only
    callers who sent the field."""
    with pytest.raises(ValueError, match="invalid model id"):
        WorkspaceDefaults(model="--dangerously-skip-permissions")

    # And a real gateway id, brackets and all, still validates.
    assert WorkspaceDefaults(model="anthropic-opus-5[1m]").model == "anthropic-opus-5[1m]"
