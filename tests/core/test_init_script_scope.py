"""`init_script.applies_to` — which runtimes a project's setup script is for.

A host-side setup step can be redundant, or actively wrong, once the
devcontainer's own lifecycle hooks do that work (and vice versa), so a project
can scope its script to `all` (the default), `host`, or `container`.
Not-applicable is `InitStatus.SKIPPED` — the same outcome `enabled: false` and
a per-create `skip_init` produce, deliberately not a new status.

Runs the real manager against a real git repo, the FakeTmux fixture, and the
in-memory container boundaries (`FakeCli` + `FakePreflight`), so what is pinned
is the gate the three lifecycle verbs share — including that the gate reads the
*effective* runtime, so a workspace that asked for a container and fell back to
the host is a host workspace here.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from grove.core import paths as paths_mod
from grove.core.config import GroveConfig, InitScriptConfig, load_config
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import InitStatus, Runtime, WorkspaceState
from tests.conftest import FakeCli, FakePreflight, FakeTmux

Scope = Literal["all", "host", "container"]


def _manager(
    tmp_repo: Path,
    tmp_path: Path,
    *,
    applies_to: Scope,
    container_enabled: bool = False,
    cli: FakeCli | None = None,
    preflight: FakePreflight | None = None,
    run_on_resume: bool = False,
) -> WorkspaceManager:
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "container": {"enabled": container_enabled},
            "agents": [{"name": "claude", "command": "claude", "kind": "claude_code"}],
            "init_script": {
                "enabled": True,
                "inline": "true",
                "applies_to": applies_to,
                "run_on_resume": run_on_resume,
            },
        }
    )
    resolved_cli = cli if cli is not None else FakeCli()
    return WorkspaceManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=resolved_cli,
        preflight=(
            preflight if preflight is not None else FakePreflight(devcontainer_cli=resolved_cli)  # type: ignore[arg-type]
        ),
    )


def _create(mgr: WorkspaceManager, title: str) -> WorkspaceState:
    return mgr.create(CreateWorkspaceRequest(agent_name="claude", title=title))


# ─── the predicate itself: pure, and takes the bare fact, not a Runtime ──────


@pytest.mark.parametrize(
    ("applies_to", "container", "host"),
    [("all", True, True), ("host", False, True), ("container", True, False)],
)
def test_applies_answers_each_scope_for_both_runtimes(
    applies_to: Scope, container: bool, host: bool
) -> None:
    cfg = InitScriptConfig(enabled=True, inline="true", applies_to=applies_to)
    assert cfg.applies(is_container=True) is container
    assert cfg.applies(is_container=False) is host


def test_the_default_scope_is_todays_behavior() -> None:
    """No migration needed: an unset `applies_to` scopes to `all`."""
    cfg = InitScriptConfig()
    assert cfg.applies_to == "all"


# ─── create: the gate against the workspace's own runtime ────────────────────


@pytest.mark.parametrize("containerized", [False, True])
def test_default_scope_runs_on_create_for_either_runtime(
    containerized: bool, tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    mgr = _manager(tmp_repo, tmp_path, applies_to="all", container_enabled=containerized)

    state = _create(mgr, "everywhere")

    expected = Runtime.CONTAINER if containerized else Runtime.HOST
    assert state.runtime is expected
    assert state.init_status is InitStatus.OK
    assert len(fake_tmux.init_calls) == 1


def test_container_scope_runs_only_in_a_container(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    mgr = _manager(tmp_repo, tmp_path, applies_to="container", container_enabled=True)

    state = _create(mgr, "in-container")

    assert state.runtime is Runtime.CONTAINER
    assert state.init_status is InitStatus.OK
    assert len(fake_tmux.init_calls) == 1


def test_container_scope_is_skipped_on_a_host_workspace(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    mgr = _manager(tmp_repo, tmp_path, applies_to="container", container_enabled=False)

    state = _create(mgr, "on-host")

    assert state.runtime is Runtime.HOST
    assert state.init_status is InitStatus.SKIPPED
    # SKIPPED means the script was never invoked at all — not that it ran and
    # its result was discarded.
    assert fake_tmux.init_calls == []
    assert state.init_log_path is None


def test_host_scope_runs_only_on_the_host(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    mgr = _manager(tmp_repo, tmp_path, applies_to="host", container_enabled=False)

    state = _create(mgr, "hostly")

    assert state.runtime is Runtime.HOST
    assert state.init_status is InitStatus.OK
    assert len(fake_tmux.init_calls) == 1


def test_host_scope_is_skipped_in_a_container(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    mgr = _manager(tmp_repo, tmp_path, applies_to="host", container_enabled=True)

    state = _create(mgr, "not-in-container")

    assert state.runtime is Runtime.CONTAINER
    assert state.init_status is InitStatus.SKIPPED
    assert fake_tmux.init_calls == []


# ─── effective vs requested: a fallback workspace IS a host workspace ────────


def test_a_workspace_that_fell_back_to_host_runs_a_host_scoped_script(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The subtle case the whole gate hinges on.

    This workspace *requested* a container and got the host, because the runtime
    was unavailable. It is running on the host, so the host-scoped setup is
    exactly what it needs — gating on the requested runtime would have skipped
    the one script that applies.
    """
    mgr = _manager(
        tmp_repo,
        tmp_path,
        applies_to="host",
        container_enabled=True,
        cli=FakeCli(available=False),
    )

    state = _create(mgr, "fell-back")

    assert state.runtime is Runtime.HOST
    assert state.runtime_fallback_reason is not None  # it really is a fallback
    assert state.init_status is InitStatus.OK
    assert len(fake_tmux.init_calls) == 1


def test_a_workspace_that_fell_back_to_host_skips_a_container_scoped_script(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The mirror: there is no container, so container-only setup must not run."""
    mgr = _manager(
        tmp_repo,
        tmp_path,
        applies_to="container",
        container_enabled=True,
        cli=FakeCli(available=False),
    )

    state = _create(mgr, "fell-back-too")

    assert state.runtime is Runtime.HOST
    assert state.runtime_fallback_reason is not None
    assert state.init_status is InitStatus.SKIPPED
    assert fake_tmux.init_calls == []


# ─── resume / respawn consume the same gate ─────────────────────────────────


def test_resume_honors_the_scope(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> None:
    mgr = _manager(
        tmp_repo, tmp_path, applies_to="container", container_enabled=False, run_on_resume=True
    )
    state = _create(mgr, "resumed")
    mgr.pause(state.id)
    fake_tmux.init_calls.clear()

    resumed = mgr.resume(state.id)

    assert resumed.init_status is InitStatus.SKIPPED
    assert fake_tmux.init_calls == []


def test_resume_still_runs_an_applicable_script(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    mgr = _manager(
        tmp_repo, tmp_path, applies_to="host", container_enabled=False, run_on_resume=True
    )
    state = _create(mgr, "resumed-host")
    mgr.pause(state.id)
    fake_tmux.init_calls.clear()

    resumed = mgr.resume(state.id)

    assert resumed.init_status is InitStatus.OK
    assert len(fake_tmux.init_calls) == 1


def test_respawn_honors_the_scope(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> None:
    mgr = _manager(
        tmp_repo, tmp_path, applies_to="container", container_enabled=False, run_on_resume=True
    )
    state = _create(mgr, "respawned")
    fake_tmux.sessions.discard(state.tmux_session)  # the session vanished → OFFLINE
    fake_tmux.init_calls.clear()

    respawned = mgr.respawn(state.id)

    assert respawned.init_status is InitStatus.SKIPPED
    assert fake_tmux.init_calls == []


def test_respawn_still_runs_an_applicable_script(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    mgr = _manager(
        tmp_repo, tmp_path, applies_to="host", container_enabled=False, run_on_resume=True
    )
    state = _create(mgr, "respawned-host")
    fake_tmux.sessions.discard(state.tmux_session)
    fake_tmux.init_calls.clear()

    respawned = mgr.respawn(state.id)

    assert respawned.init_status is InitStatus.OK
    assert len(fake_tmux.init_calls) == 1


# ─── cascade: an ordinary field-wise knob, not part of the exclusive group ───


def test_a_project_layer_scope_merges_field_wise_with_the_script_below(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """`applies_to` merges like `timeout_seconds` or `fail_fast`.

    It is deliberately NOT a member of the `inline`/`path` exclusive group, so
    naming it in a higher layer must not strip the script the lower layer set —
    the over-eager-strip regression that group resolution exists to avoid.
    """
    del tmp_state_dir
    project = paths_mod.project_config_path(tmp_repo)
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text(
        json.dumps({"init_script": {"enabled": True, "inline": "echo project"}}),
        encoding="utf-8",
    )
    local = paths_mod.project_local_config_path(tmp_repo)
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(json.dumps({"init_script": {"applies_to": "container"}}), encoding="utf-8")

    cfg = load_config(tmp_repo, env={})

    assert cfg.init_script.applies_to == "container"
    assert cfg.init_script.inline == "echo project"
    assert cfg.init_script.enabled is True


# ─── the GROVE_* variables, on every verb that runs the script ─────────────


def _init_env(fake_tmux: FakeTmux, *, after: int) -> dict[str, str]:
    """The env of the init run this verb drove.

    Takes the call count from BEFORE the verb and asserts it grew: reading only
    `init_calls[-1]` would happily return the previous verb's env for a verb
    that never ran the script at all, which is precisely the failure being
    tested and would make this pass for the wrong reason.
    """
    assert len(fake_tmux.init_calls) == after + 1, "this verb did not run the init script"
    return fake_tmux.init_calls[-1][1]


def test_grove_vars_are_identical_on_create_resume_and_respawn(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    """Driven through the three MANAGER verbs on purpose: a test that hands
    `extra_env` straight to `tmux.run_init_script` verifies the unit but not
    the wiring, and a verb that composes no `GROVE_*` vars of its own would
    still pass — a script reading `$GROVE_BRANCH` would work on create and
    silently see an empty string on every `run_on_resume` resume.
    """
    mgr = _manager(tmp_repo, tmp_path, applies_to="all", run_on_resume=True)
    state = _create(mgr, "vars")
    expected = {
        "GROVE_REPO": state.repo_root,
        "GROVE_WORKTREE": state.worktree_path,
        "GROVE_BRANCH": state.branch,
        "GROVE_AGENT": state.agent_name,
    }
    assert all(expected.values()), expected
    assert _init_env(fake_tmux, after=0) == expected

    before = len(fake_tmux.init_calls)
    mgr.pause(state.id)
    mgr.resume(state.id)
    assert _init_env(fake_tmux, after=before) == expected

    # respawn needs OFFLINE — the session vanishing is what it recovers from.
    before = len(fake_tmux.init_calls)
    fake_tmux.sessions.discard(mgr.get(state.id).tmux_session)
    mgr.respawn(state.id)
    assert _init_env(fake_tmux, after=before) == expected


def test_the_grove_vars_are_derived_so_they_cannot_go_stale(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    """`init_env` is a property, not a persisted field.

    A stored copy would survive a branch rename or a moved worktree and then
    report the stale one; a derivation off the record cannot. All four values
    are already on `WorkspaceState`, so persisting them would only cache facts
    already held elsewhere.
    """
    mgr = _manager(tmp_repo, tmp_path, applies_to="all")
    state = _create(mgr, "derived")

    moved = replace(state, branch="renamed/after-the-fact")

    assert moved.init_env["GROVE_BRANCH"] == "renamed/after-the-fact"
    assert state.init_env["GROVE_BRANCH"] == state.branch
    # And it never reaches the store: nothing to keep honest, nothing to migrate.
    assert "init_env" not in (tmp_path / "state.json").read_text(encoding="utf-8")
