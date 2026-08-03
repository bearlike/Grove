"""One phase file per AGENT, not per worktree.

A single fixed path derived from the workspace alone
(``<worktree>/.grove/phase.json``) is correct only where a worktree hosts
exactly one agent. Grove ships two configurations where it does not — ROOT
placement (every root workspace on a repo IS the repo root) and ``grove agent
add`` (N agents, one container, one worktree, one record) — so the two cases
below are the reason the file exists, and each drives the real
``WorkspaceManager`` against a real git repo rather than asserting on a
composition in isolation.

The git-exclude tests use REAL git deliberately: ``info/exclude``'s anchoring is
the constraint that pins WHERE the files live, and a pattern that silently fails
to match is indistinguishable from no pattern at all until ``pause``/``kill``
refuse months later.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.container_infra import slugify_project
from grove.core.container_runtime import ContainerRuntimeState
from grove.core.container_tmux import CONTAINER_TMUX_ROOT
from grove.core.contracts.branch_plan import RootBranch
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.phase import PhaseFile
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Placement, Runtime, WorkspaceState
from tests.conftest import (
    DOCKER_INSPECT_STARTED_AT,
    FAKE_REMOTE_FOLDER,
    FakeCli,
    FakePreflight,
    FakeTmux,
)

FULL_ID = "c" * 64
IN_CONTAINER_TMUX = f"{CONTAINER_TMUX_ROOT}/bin/amd64/tmux"


def _cfg(tmp_path: Path) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "container": {"enabled": False},
        }
    )


@pytest.fixture
def manager(tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux) -> WorkspaceManager:
    del fake_tmux  # installed via monkeypatch
    return WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=FakeCli(container_id=FULL_ID),
        preflight=FakePreflight(),
    )


def _phase_env_of(fake_tmux: FakeTmux, session: str) -> str | None:
    """What ``GROVE_PHASE_FILE`` the launch for *session* published, if any."""
    for name, env, _unset in fake_tmux.launch_envs:
        if name == session:
            return env.get(PhaseFile.PATH_ENV)
    raise AssertionError(f"no launch recorded for session {session!r}")


def _containerize(manager: WorkspaceManager, state: WorkspaceState) -> WorkspaceState:
    """Give a created workspace the identity a container create persists."""
    stored = manager.store.get(state.id)
    stored.runtime = Runtime.CONTAINER
    stored.container = ContainerRuntimeState(
        container_id=FULL_ID,
        image_ref="ghcr.io/example/dev:1",
        remote_user="vscode",
        remote_workspace_folder=FAKE_REMOTE_FOLDER,
        id_labels=ContainerRuntimeState.labels_for(
            state.id, project_slug=slugify_project(manager.repo_root)
        ),
        provisioned=True,
        provisioned_start=DOCKER_INSPECT_STARTED_AT,
        tmux_command=IN_CONTAINER_TMUX,
    )
    manager.store.save(stored)
    return stored


def _check_ignore(worktree: Path, relpath: str) -> bool:
    """Does real git consider *relpath* excluded inside *worktree*?"""
    result = subprocess.run(
        ["git", "check-ignore", relpath],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


# ─── ROOT placement: several workspaces, one directory ──────────────────────


def test_two_root_workspaces_report_phases_independently(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    """The headline defect: every ROOT workspace on a repo has the SAME
    ``worktree_path`` (the repo root), so one fixed path meant last-write-wins
    with neither agent able to tell."""
    one = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="root one", branch_plan=RootBranch())
    )
    two = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="root two", branch_plan=RootBranch())
    )
    assert one.placement is Placement.ROOT
    assert Path(one.worktree_path) == Path(two.worktree_path) == tmp_repo

    manager.set_phase(one.id, "implementing", "editing the parser")
    manager.set_phase(two.id, "verifying", "running the suite")

    first = manager.phase(one.id)
    second = manager.phase(two.id)
    assert first is not None and second is not None
    assert (first.phase, first.note) == ("implementing", "editing the parser")
    assert (second.phase, second.note) == ("verifying", "running the suite")


def test_two_root_workspaces_are_told_different_paths_at_launch(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    """The write side of the same fact: each agent is HANDED its own absolute
    path, so neither has to derive one from a directory they share."""
    one = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="root one", branch_plan=RootBranch())
    )
    two = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="root two", branch_plan=RootBranch())
    )

    first = _phase_env_of(fake_tmux, one.tmux_session)
    second = _phase_env_of(fake_tmux, two.tmux_session)
    assert first is not None and second is not None
    assert first != second
    # Under the shared worktree, so a container's write crosses the bind mount.
    assert Path(first).parent == tmp_repo / PhaseFile.RELDIR
    # And what the agent writes is exactly what the manager reads back.
    Path(first).parent.mkdir(parents=True, exist_ok=True)
    Path(first).write_text('{"phase": "planning"}\n', encoding="utf-8")
    report = manager.phase(one.id)
    assert report is not None and report.phase == "planning"
    assert manager.phase(two.id) is None


# ─── several agents in ONE container ────────────────────────────────────────


def test_two_agents_in_one_container_report_phases_independently(
    manager: WorkspaceManager,
) -> None:
    """`grove agent add` adds no persisted state, so the second agent is a tmux
    session name — which is exactly the key the phase path needs."""
    state = _containerize(
        manager, manager.create(CreateWorkspaceRequest(agent_name="claude", title="containered"))
    )
    worktree = Path(state.worktree_path)

    primary = PhaseFile.key_for(state.id)
    extra = PhaseFile.key_for(state.id, agent="agent-2")
    assert primary != extra

    PhaseFile.write(worktree, primary, "implementing", "the workspace's own agent")
    PhaseFile.write(worktree, extra, "verifying", "the added agent")

    own = manager.phase_for(state)
    added = PhaseFile.read(worktree, extra)
    assert own is not None and own.phase == "implementing"
    assert added is not None and added.phase == "verifying"


def test_an_added_container_agent_crosses_with_its_own_phase_path(
    manager: WorkspaceManager,
) -> None:
    """The launch env is the channel, and `--remote-env` carries it in for free.

    Both launch roads compose through the one `_launch_env`, so the property to
    pin is that they agree on EVERYTHING but this one field — an added agent
    that differed anywhere else would be a differently-configured agent, which
    is the drift that seam exists to prevent.
    """
    state = _containerize(
        manager, manager.create(CreateWorkspaceRequest(agent_name="claude", title="containered"))
    )

    env = manager._launch_env(state, manager._agent_spec("claude"), agent_slot="reviewer")
    own = manager._launch_env(state, manager._agent_spec("claude"))

    assert env[PhaseFile.PATH_ENV] != own[PhaseFile.PATH_ENV]
    # A CONTAINER path, re-rooted at the CLI's own reported workspace folder —
    # never a host path, which the agent would act on and get wrong.
    assert env[PhaseFile.PATH_ENV].startswith(f"{FAKE_REMOTE_FOLDER}/{PhaseFile.RELDIR}/")
    assert env[PhaseFile.PATH_ENV].endswith(f"{state.id}.reviewer.json")
    # Everything else about the two launches is identical.
    assert {k: v for k, v in env.items() if k != PhaseFile.PATH_ENV} == {
        k: v for k, v in own.items() if k != PhaseFile.PATH_ENV
    }


def test_a_container_without_a_reported_workspace_folder_publishes_nothing(
    manager: WorkspaceManager,
) -> None:
    """ "Cannot name it in the agent's namespace" must not degrade to a host path.

    A foreign-namespace path is worse than none, because the read side (here,
    the agent) acts on whatever it is given. Omitting the variable leaves the
    documented literal fallback, which Grove still reads.
    """
    state = _containerize(
        manager, manager.create(CreateWorkspaceRequest(agent_name="claude", title="containered"))
    )
    stored = manager.store.get(state.id)
    assert stored.container is not None
    stored.container = stored.container.model_copy(update={"remote_workspace_folder": ""})

    env = manager._launch_env(stored, manager._agent_spec("claude"))
    assert PhaseFile.PATH_ENV not in env


# ─── nested project_subpath ─────────────────────────────────────────────────


def test_a_nested_workspace_is_pointed_at_the_worktree_root(
    manager: WorkspaceManager, tmp_repo: Path, fake_tmux: FakeTmux
) -> None:
    """The agent's cwd is ``<worktree>/services/api``; its phase file is NOT.

    "Write `.grove/phase.json` in your worktree" is precisely the sentence such
    an agent resolves against its cwd, landing somewhere Grove never reads and
    — worse — outside the anchored git exclude. Naming the absolute path removes
    the interpretation entirely.
    """
    sub = tmp_repo / "services" / "api"
    sub.mkdir(parents=True)
    (sub / ".keep").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "sub", "--no-verify"], cwd=tmp_repo, check=True, capture_output=True
    )

    state = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="nested", project_cwd=sub)
    )
    assert state.project_subpath == "services/api"
    worktree = Path(state.worktree_path)

    published = _phase_env_of(fake_tmux, state.tmux_session)
    assert published is not None
    assert Path(published) == worktree / PhaseFile.RELDIR / f"{state.id}.json"
    assert Path(published).parent != Path(state.agent_cwd) / PhaseFile.RELDIR

    Path(published).write_text('{"phase": "scoping"}\n', encoding="utf-8")
    report = manager.phase_for(state)
    assert report is not None and report.phase == "scoping"
    # And git really does exclude it there — the anchoring that chose the spot.
    assert _check_ignore(worktree, f"{PhaseFile.RELDIR}/{state.id}.json")
    assert not _check_ignore(worktree, f"services/api/{PhaseFile.RELDIR}/{state.id}.json")


# ─── back-compat ────────────────────────────────────────────────────────────


def test_the_single_shared_phase_file_is_still_read(manager: WorkspaceManager) -> None:
    """A workspace that reported before the per-agent layout keeps its badge."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="legacy"))
    legacy = PhaseFile.path_for(state.worktree_path, None)
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text('{"phase": "delivering", "note": "from before"}\n', encoding="utf-8")

    report = manager.phase_for(state)
    assert report is not None
    assert (report.phase, report.note) == ("delivering", "from before")


def test_a_keyed_report_wins_over_the_legacy_file(manager: WorkspaceManager) -> None:
    """Back-compat is a fallback, never a competitor: once this agent has
    reported to its own file, the shared one can no longer speak for it."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="legacy"))
    legacy = PhaseFile.path_for(state.worktree_path, None)
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text('{"phase": "scoping"}\n', encoding="utf-8")
    manager.set_phase(state.id, "done")

    report = manager.phase_for(state)
    assert report is not None and report.phase == "done"


# ─── the git exclude, against real git ──────────────────────────────────────


def test_the_exclude_covers_the_per_agent_directory(manager: WorkspaceManager) -> None:
    """A mid-string slash in ``info/exclude`` is anchored to the working-tree
    root, so the pattern has to name the directory the files actually live in.

    Mutation-checked by hand: drop ``.grove/phase/`` from ``PhaseFile.EXCLUDES``
    and this test plus the pause/kill ones below fail, on real git, in exactly
    the way a user would hit them.
    """
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="excluded"))
    manager.set_phase(state.id, "implementing")
    worktree = Path(state.worktree_path)

    assert _check_ignore(worktree, f"{PhaseFile.RELDIR}/{state.id}.json")
    # Both shapes, because a repo can hold an older single-file workspace alongside a new one.
    assert _check_ignore(worktree, PhaseFile.LEGACY_RELPATH)
    # `git status` — hence `is_clean`, the pause precondition — must not see it.
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=worktree, capture_output=True, text=True, check=True
    )
    assert status.stdout.strip() == ""


def test_pause_and_resume_survive_a_reported_phase(manager: WorkspaceManager) -> None:
    """``git worktree remove`` refuses while untracked files exist, so an
    unexcluded phase file breaks ``pause`` for the rest of a workspace's life."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="pause me"))
    manager.set_phase(state.id, "implementing", "mid-flight")
    assert Path(state.worktree_path).exists()

    paused = manager.pause(state.id)
    assert not Path(paused.worktree_path).exists()
    # The phase went with the worktree, deliberately: a phase is a live claim by
    # a running agent, not a durable record (the durable one is the commit log).
    assert manager.phase(state.id) is None

    resumed = manager.resume(state.id)
    assert Path(resumed.worktree_path).exists()


def test_kill_succeeds_with_a_phase_file_present(manager: WorkspaceManager) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="kill me"))
    manager.set_phase(state.id, "done")

    manager.kill(state.id)
    assert not Path(state.worktree_path).exists()


# ─── the key itself ─────────────────────────────────────────────────────────


def test_the_key_is_the_workspace_plus_the_slot() -> None:
    """Both halves separate a different kind of co-tenant, and the primary is
    keyed by the bare id so renaming ``container.tmux.session`` cannot orphan a
    workspace's own file."""
    assert PhaseFile.key_for("abc123") == "abc123"
    assert PhaseFile.key_for("abc123", agent="reviewer") == "abc123.reviewer"
    assert PhaseFile.relpath("abc123.reviewer") == ".grove/phase/abc123.reviewer.json"
