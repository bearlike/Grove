"""Labelled agent working directories — validation, and the engine-side default.

The mechanism underneath (``project_cwd`` → ``project_subpath`` → ``agent_cwd``)
is already pinned by ``tests/core/test_nested_projects.py``. What is new here is
the labelled CATALOG and, more importantly, that a create naming nothing
resolves the repo's configured default IN THE ENGINE — the one thing a client
must not own, because a client-side default is silently discarded by every
other client (the `defaults.branch_mode` precedent).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from grove.core import build
from grove.core.config import AgentCwdsConfig, GroveConfig
from grove.core.contracts import CreateWorkspaceRequest
from grove.core.contracts.views import WorkspaceDefaultsView
from tests.conftest import FakeTmux

# ─── config validation: refused at the point of DEFINITION ───────────────────


def test_an_absolute_path_is_refused() -> None:
    """A portable set describes the repo's layout, never one machine's disks."""
    with pytest.raises(ValidationError, match="must be relative"):
        AgentCwdsConfig(entries={"Web app": "/srv/webapp"})


def test_a_home_relative_path_is_refused() -> None:
    with pytest.raises(ValidationError, match="must be relative"):
        AgentCwdsConfig(entries={"Web app": "~/webapp"})


def test_a_path_climbing_out_of_the_repo_is_refused() -> None:
    """Containment is still the engine's refusal at create; catching it here
    means the operator learns at the moment they write it, not at the create
    that would have used it."""
    with pytest.raises(ValidationError, match="climb out"):
        AgentCwdsConfig(entries={"Escape": "../secrets"})


def test_a_blank_label_is_refused() -> None:
    with pytest.raises(ValidationError, match="may not be blank"):
        AgentCwdsConfig(entries={"  ": "webapp"})


def test_a_default_naming_no_entry_is_refused() -> None:
    with pytest.raises(ValidationError, match="not one of"):
        AgentCwdsConfig(entries={"Web app": "webapp"}, default="Backend")


def test_default_path_maps_the_label_to_its_path() -> None:
    """The engine consumes the PATH, never the label — which is what keeps the
    label a presentation concern nothing downstream learns about."""
    cfg = AgentCwdsConfig(entries={"Web app": "webapp"}, default="Web app")
    assert cfg.default_path() == "webapp"


def test_no_default_resolves_to_nothing() -> None:
    assert AgentCwdsConfig(entries={"Web app": "webapp"}).default_path() is None


# ─── the wire catalog ────────────────────────────────────────────────────────


def test_defaults_view_carries_the_catalog_in_declaration_order() -> None:
    cfg = GroveConfig(
        agent_cwds=AgentCwdsConfig(
            entries={"Web app": "webapp", "Engine": "src/grove"}, default="Engine"
        )
    )
    view = WorkspaceDefaultsView.from_config(cfg)
    assert [(c.label, c.path) for c in view.agent_cwds] == [
        ("Web app", "webapp"),
        ("Engine", "src/grove"),
    ]
    # The resolved answer, so a form pre-selects rather than guessing.
    assert view.agent_cwd == "src/grove"


def test_defaults_view_is_empty_for_a_repo_that_declares_none() -> None:
    view = WorkspaceDefaultsView.from_config(GroveConfig())
    assert view.agent_cwds == ()
    assert view.agent_cwd is None


# ─── the engine applies the default ──────────────────────────────────────────


def _write_project_config(repo: Path, payload: dict[str, object]) -> None:
    grove_dir = repo / ".grove"
    grove_dir.mkdir(exist_ok=True)
    (grove_dir / "config.json").write_text(json.dumps(payload))


def test_a_create_naming_nothing_lands_in_the_configured_default(
    tmp_state_dir: Path, tmp_repo: Path, fake_tmux: FakeTmux
) -> None:
    del tmp_state_dir, fake_tmux
    (tmp_repo / "webapp").mkdir()
    _write_project_config(
        tmp_repo, {"agent_cwds": {"entries": {"Web app": "webapp"}, "default": "Web app"}}
    )

    manager = build(tmp_repo)
    state = manager.create(CreateWorkspaceRequest(title="defaulted", agent_name="claude"))

    assert state.project_subpath == "webapp"
    assert state.agent_cwd == Path(state.worktree_path) / "webapp"


def test_an_explicit_cwd_still_wins_over_the_configured_default(
    tmp_state_dir: Path, tmp_repo: Path, fake_tmux: FakeTmux
) -> None:
    del tmp_state_dir, fake_tmux
    (tmp_repo / "webapp").mkdir()
    (tmp_repo / "docs").mkdir()
    _write_project_config(
        tmp_repo, {"agent_cwds": {"entries": {"Web app": "webapp"}, "default": "Web app"}}
    )

    manager = build(tmp_repo)
    state = manager.create(
        CreateWorkspaceRequest(title="explicit", agent_name="claude", project_cwd=Path("docs"))
    )

    assert state.project_subpath == "docs"


def test_a_repo_declaring_no_default_still_starts_at_the_worktree_root(
    tmp_state_dir: Path, tmp_repo: Path, fake_tmux: FakeTmux
) -> None:
    """The opt-in guarantee: every existing repo and caller is unaffected."""
    del tmp_state_dir, fake_tmux
    manager = build(tmp_repo)
    state = manager.create(CreateWorkspaceRequest(title="rootish", agent_name="claude"))

    assert state.project_subpath == ""
    assert state.agent_cwd == Path(state.worktree_path)


def test_the_configured_default_moves_the_agent_but_not_the_init_script(
    tmp_state_dir: Path, tmp_repo: Path, fake_tmux: FakeTmux
) -> None:
    """The whole promise of this feature in one assertion.

    Only the agent session's cwd moves. The init script keeps running at the
    worktree root, so a project's setup is not silently re-rooted into whichever
    subdirectory somebody set as the default.
    """
    del tmp_state_dir
    (tmp_repo / "webapp").mkdir()
    _write_project_config(
        tmp_repo,
        {
            "agent_cwds": {"entries": {"Web app": "webapp"}, "default": "Web app"},
            "init_script": {"enabled": True, "inline": "true"},
        },
    )

    manager = build(tmp_repo)
    state = manager.create(CreateWorkspaceRequest(title="split", agent_name="claude"))

    assert state.agent_cwd == Path(state.worktree_path) / "webapp"
    assert fake_tmux.init_calls, "the init script should have run"
    worktree_arg, _env = fake_tmux.init_calls[-1]
    assert worktree_arg == Path(state.worktree_path)
