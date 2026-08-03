"""Full workspace lifecycle: create → pause → resume → kill against a real
git repo and the FakeTmux fixture."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core import (
    BranchAlreadyCheckedOut,
    BranchConflict,
    BranchNotFound,
    ExistingLocalBranch,
    NewNamedBranch,
)
from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import GitError, GroveError, WorkspaceStateError
from grove.core.git import GitRepo
from grove.core.manager import WorkspaceEvent, WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import BranchProvenance, WorkspaceState, WorkspaceStatus
from tests.conftest import FakeTmux


def _branches(repo: Path) -> set[str]:
    out = subprocess.run(
        ["git", "branch", "--list", "--format=%(refname:short)"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def _worktrees(repo: Path) -> set[Path]:
    """Worktree paths from `git worktree list`, normalized to resolved Path.

    Why Path, not str: on Windows, Python's `str(Path)` uses `\\` separators
    but `git worktree list --porcelain` emits `/`. Raw string equality
    silently mismatches on the same path. Comparing resolved Path objects
    sidesteps the separator + case-folding asymmetry on every OS.
    """
    out = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    paths: set[Path] = set()
    for line in out.stdout.splitlines():
        if line.startswith("worktree "):
            paths.add(Path(line[len("worktree ") :].strip()).resolve())
    return paths


def _wt(state_path: str) -> Path:
    """Normalize a state's worktree_path for comparison against `_worktrees`."""
    return Path(state_path).resolve()


@pytest.fixture
def manager(
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    tmp_path: Path,
) -> WorkspaceManager:
    del fake_tmux  # used via monkeypatch
    cfg = GroveConfig.model_validate(
        {
            "worktree": {
                "root_template": str(tmp_path / "trees"),
                "branch_prefix": "test/",
            },
            "tmux": {"session_prefix": "test-"},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


def test_create_succeeds_and_emits_event(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    events: list[WorkspaceEvent] = []
    manager.subscribe(events.append)

    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="my task"))

    # create() returns the *persisted intent* (RUNNING), not the reconciled
    # status. Reconciliation happens at list/peek time; see the dedicated
    # reconciliation tests for the ACTIVE/IDLE/OFFLINE promotion.
    assert state.status == WorkspaceStatus.RUNNING
    assert state.title == "my task"
    assert state.branch.startswith("test/my-task-")
    assert state.tmux_session.startswith("test-my-task-")
    # real git side
    assert state.branch in _branches(tmp_repo)
    assert _wt(state.worktree_path) in _worktrees(tmp_repo)
    # fake tmux side
    assert state.tmux_session in fake_tmux.sessions
    assert (state.tmux_session, "claude") in fake_tmux.layouts
    # event
    assert any(e.kind == "created" and e.workspace_id == state.id for e in events)


def test_create_unknown_agent_raises_grove_error(
    manager: WorkspaceManager,
) -> None:
    with pytest.raises(GroveError, match="unknown agent"):
        manager.create(CreateWorkspaceRequest(agent_name="not-real", title="x"))


def test_pause_removes_worktree_keeps_branch(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="p1"))
    paused = manager.pause(state.id)

    assert paused.status == WorkspaceStatus.PAUSED
    assert paused.paused_at is not None
    assert state.branch in _branches(tmp_repo)
    assert _wt(state.worktree_path) not in _worktrees(tmp_repo)
    assert state.tmux_session not in fake_tmux.sessions


def test_resume_recreates_worktree_and_session(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="r1"))
    manager.pause(state.id)
    resumed = manager.resume(state.id)

    # resume() also returns the persisted intent (RUNNING). list()/peek()
    # promote it to ACTIVE/IDLE based on tmux pane_activity.
    assert resumed.status == WorkspaceStatus.RUNNING
    assert resumed.paused_at is None
    assert _wt(state.worktree_path) in _worktrees(tmp_repo)
    assert state.tmux_session in fake_tmux.sessions


def test_kill_cleans_everything(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="k1"))
    manager.kill(state.id)

    branches = _branches(tmp_repo)
    assert state.branch not in branches
    assert _wt(state.worktree_path) not in _worktrees(tmp_repo)
    assert state.tmux_session not in fake_tmux.sessions
    # Record removed from store
    assert all(s.id != state.id for s in manager.store.load_all())


def test_kill_leaves_the_provision_log_on_disk(
    manager: WorkspaceManager,
    fake_tmux: FakeTmux,
    tmp_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The provision log is the sole record of what the container path
    decided silently, and it is not reproducible the way the init log is (a
    killed container can't be re-run to regenerate it). Kill must not delete
    it, unlike the init log, which it still drops.
    """
    del fake_tmux
    logs_dir = tmp_path / "provision-logs"
    monkeypatch.setattr(
        "grove.core.paths.provision_log_path",
        lambda workspace_id: logs_dir / f"{workspace_id}-provision.log",
    )
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="keep-provision-log"))
    log_path = logs_dir / f"{state.id}-provision.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("provision output\n", encoding="utf-8")

    manager.kill(state.id)

    assert log_path.exists()


def test_a_clean_kill_reports_no_residue(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    del fake_tmux, tmp_repo
    events: list[WorkspaceEvent] = []
    manager.subscribe(events.append)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="clean-kill"))

    manager.kill(state.id)

    killed = next(e for e in events if e.kind == "killed")
    assert killed.detail["branch_deleted"] == "true"
    assert "residue" not in killed.detail


def test_a_kill_whose_branch_delete_failed_says_so(
    manager: WorkspaceManager,
    fake_tmux: FakeTmux,
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The teardown stays best-effort, but it may not claim work it did not do.

    ``branch_deleted`` must reflect the actual outcome, not the *request* — a
    failed ``git branch -D`` must not announce a deleted branch. The record
    naming the leftovers is deleted on the next line, so the event is the
    last chance to say anything at all.
    """
    del fake_tmux
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="stubborn-branch"))
    events: list[WorkspaceEvent] = []
    manager.subscribe(events.append)

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise GitError("branch is checked out somewhere else")

    monkeypatch.setattr(GitRepo, "branch_delete", _refuse)

    manager.kill(state.id)

    killed = next(e for e in events if e.kind == "killed")
    assert killed.detail["branch_deleted"] == "false"
    assert killed.detail["residue"] == "branch"
    # Still best-effort: the record is gone and the branch really did survive.
    assert all(s.id != state.id for s in manager.store.load_all())
    assert state.branch in _branches(tmp_repo)


def test_pause_when_not_running_raises(manager: WorkspaceManager) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="not-running"))
    manager.pause(state.id)
    with pytest.raises(WorkspaceStateError):
        manager.pause(state.id)


def test_list_marks_running_workspace_offline_when_tmux_session_gone(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="offline-me"))
    fake_tmux.sessions.discard(state.tmux_session)
    listed = manager.list()
    target = next(s for s in listed if s.id == state.id)
    assert target.status == WorkspaceStatus.OFFLINE


def test_drift_events_do_not_recurse_when_subscriber_calls_list(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """Drift detection must be idempotent across consecutive ``list()`` calls.

    The TUI subscriber refreshes via ``manager.list()`` from inside its
    event handler. If ``offline_detected`` re-fires on every ``list()``
    call for a workspace already known-offline, the subscriber's refresh
    triggers another emission, which triggers another refresh, and so on
    until ``RecursionError`` blows up — surfaced to the user as
    "subscriber raised on offline detected event" on every kill.
    """
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="offline-once"))
    fake_tmux.sessions.discard(state.tmux_session)

    offline_events: list[WorkspaceEvent] = []

    def _subscriber(event: WorkspaceEvent) -> None:
        if event.kind == "offline_detected":
            offline_events.append(event)
            # Mimic the TUI: refresh by re-listing. Must not recurse.
            manager.list()

    manager.subscribe(_subscriber)

    # First list() picks up the drift; subsequent calls must NOT re-emit.
    manager.list()
    manager.list()
    manager.list()

    assert len(offline_events) == 1, (
        f"offline_detected should fire exactly once, got {len(offline_events)}"
    )


def test_list_promotes_running_to_active_when_pane_recently_active(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """Reconciliation surfaces ACTIVE for workspaces with fresh pane output.

    The fake reports 1s since last activity (default), and the configured
    threshold is 5s — so the workspace flips to ACTIVE.
    """
    del fake_tmux  # default activity = 0 seconds → within threshold
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="active-one"))
    listed = manager.list()
    target = next(s for s in listed if s.id == state.id)
    assert target.status == WorkspaceStatus.ACTIVE


def test_list_promotes_running_to_idle_when_pane_quiet(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """When pane_activity is older than the threshold, the workspace is IDLE."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="quiet"))
    # Push activity well past the 5-second threshold.
    fake_tmux.activity_seconds_ago[f"{state.tmux_session}:agent"] = 60
    listed = manager.list()
    target = next(s for s in listed if s.id == state.id)
    assert target.status == WorkspaceStatus.IDLE


def test_list_marks_orphaned_when_worktree_dir_missing(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """If the worktree dir is gone (deleted externally), reconcile flags
    the workspace ORPHANED — distinct from OFFLINE so the user knows the
    only valid action is kill."""
    del fake_tmux
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="orphan"))
    # Delete the worktree externally — git is now confused, the record is
    # stranded. Reconciliation should catch this before the OFFLINE check.
    shutil.rmtree(state.worktree_path, ignore_errors=True)
    listed = manager.list()
    target = next(s for s in listed if s.id == state.id)
    assert target.status == WorkspaceStatus.ORPHANED


def test_list_filters_to_repo(
    manager: WorkspaceManager, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    del fake_tmux
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="kept"))
    # Inject a record from a different repo directly into the store.
    foreign = WorkspaceState(
        id="foreign",
        title="not-mine",
        repo_root=str(tmp_path / "other-repo"),
        branch="x",
        base_branch="HEAD",
        worktree_path="/x",
        tmux_session="x",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )
    manager.store.save(foreign)

    ids = {s.id for s in manager.list()}
    assert state.id in ids
    assert "foreign" not in ids


def test_respawn_recreates_session_for_offline_workspace(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """Offline = persisted RUNNING + tmux session vanished + worktree intact.
    respawn() should bring it back to a live session without git work."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="respawn-me"))
    fake_tmux.sessions.discard(state.tmux_session)
    # Sanity: reconciliation reports OFFLINE for this state.
    assert manager.list()[0].status == WorkspaceStatus.OFFLINE

    events: list[WorkspaceEvent] = []
    manager.subscribe(events.append)

    respawned = manager.respawn(state.id)

    assert respawned.status == WorkspaceStatus.RUNNING  # persisted intent
    assert state.tmux_session in fake_tmux.sessions
    assert (state.tmux_session, "claude") in fake_tmux.layouts
    assert any(e.kind == "respawned" and e.workspace_id == state.id for e in events)


def test_respawn_refuses_for_paused_workspace(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """respawn applies only to OFFLINE; paused workspaces use resume."""
    del fake_tmux
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="pp"))
    manager.pause(state.id)
    with pytest.raises(WorkspaceStateError, match="expected offline"):
        manager.respawn(state.id)


def test_respawn_refuses_for_active_workspace(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """A live workspace doesn't need respawn."""
    del fake_tmux
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="alive"))
    with pytest.raises(WorkspaceStateError, match="expected offline"):
        manager.respawn(state.id)


def test_respawn_refuses_when_worktree_missing(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """If the worktree is gone (orphaned), respawn raises — kill is the path."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="rm-me"))
    fake_tmux.sessions.discard(state.tmux_session)
    shutil.rmtree(state.worktree_path, ignore_errors=True)
    # Reconciler now reports ORPHANED, not OFFLINE; ensure_can_respawn refuses.
    with pytest.raises(WorkspaceStateError, match="expected offline"):
        manager.respawn(state.id)


def test_store_refuses_to_persist_computed_status(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """Defense in depth: a bug that tries to save a computed status raises."""
    del fake_tmux
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="guard"))
    bogus = WorkspaceState(
        id=state.id,
        title=state.title,
        repo_root=state.repo_root,
        branch=state.branch,
        base_branch=state.base_branch,
        worktree_path=state.worktree_path,
        tmux_session=state.tmux_session,
        agent_name=state.agent_name,
        status=WorkspaceStatus.ACTIVE,  # computed — must not round-trip
        created_at=state.created_at,
        updated_at=state.updated_at,
    )
    with pytest.raises(GroveError, match="refusing to persist computed status"):
        manager.store.save(bogus)


def test_create_with_new_named_branch_uses_user_supplied_name(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    """NewNamedBranch: branch is exactly what the user typed; no Grove prefix
    or timestamp suffix. Provenance is GROVE_CREATED because Grove still
    created the branch — the user just picked the name."""
    del fake_tmux
    request = CreateWorkspaceRequest(
        agent_name="claude",
        title="payment redesign",
        branch_plan=NewNamedBranch(name="feature/payment-v2", base_ref="main"),
    )
    state = manager.create(request)
    assert state.branch == "feature/payment-v2"
    assert state.branch_provenance == BranchProvenance.GROVE_CREATED
    assert "feature/payment-v2" in _branches(tmp_repo)


def test_create_with_existing_local_branch_attaches_without_creating(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    """ExistingLocalBranch: Grove checks out the user's pre-existing branch.
    No new branch created. Provenance is USER_ATTACHED."""
    del fake_tmux
    # Seed a branch the user "already has"
    subprocess.run(
        ["git", "branch", "feature/preexisting", "main"],
        cwd=tmp_repo,
        check=True,
        capture_output=True,
    )
    branches_before = _branches(tmp_repo)
    assert "feature/preexisting" in branches_before

    state = manager.create(
        CreateWorkspaceRequest(
            agent_name="claude",
            title="resume legacy",
            branch_plan=ExistingLocalBranch(name="feature/preexisting"),
        )
    )
    assert state.branch == "feature/preexisting"
    assert state.branch_provenance == BranchProvenance.USER_ATTACHED
    # No new branches added — only the worktree is new.
    assert _branches(tmp_repo) == branches_before


def test_create_with_branch_conflict_raises_typed_error(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    """A NewNamedBranch whose name matches an existing branch raises
    BranchConflict before any worktree side effect."""
    del fake_tmux
    subprocess.run(
        ["git", "branch", "already/here", "main"],
        cwd=tmp_repo,
        check=True,
        capture_output=True,
    )

    with pytest.raises(BranchConflict, match="already exists"):
        manager.create(
            CreateWorkspaceRequest(
                agent_name="claude",
                title="conflict",
                branch_plan=NewNamedBranch(name="already/here", base_ref="main"),
            )
        )
    # Validation failed up front — no rollback artifacts.
    assert manager.store.load_all() == []


def test_create_with_existing_local_already_checked_out_raises(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    """ExistingLocalBranch on a branch that's already in another worktree
    raises BranchAlreadyCheckedOut with the foreign worktree path."""
    del fake_tmux
    # main is already checked out in the repo's primary worktree (tmp_repo
    # itself). Trying to attach 'main' should refuse.
    with pytest.raises(BranchAlreadyCheckedOut) as excinfo:
        manager.create(
            CreateWorkspaceRequest(
                agent_name="claude",
                title="conflict",
                branch_plan=ExistingLocalBranch(name="main"),
            )
        )
    assert excinfo.value.name == "main"
    assert excinfo.value.worktree.resolve() == tmp_repo.resolve()


def test_create_with_missing_base_ref_raises_branch_not_found(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    """A NewNamedBranch whose base_ref does not exist raises BranchNotFound."""
    del fake_tmux, tmp_repo
    with pytest.raises(BranchNotFound, match="does not exist"):
        manager.create(
            CreateWorkspaceRequest(
                agent_name="claude",
                title="mistyped",
                branch_plan=NewNamedBranch(name="ok-name", base_ref="no-such-branch"),
            )
        )


def test_kill_grove_created_branch_default_deletes(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    """Default-on for GROVE_CREATED: the branch is deleted by default."""
    del fake_tmux
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="grove-made"))
    assert state.branch in _branches(tmp_repo)
    manager.kill(state.id)  # default delete_branch=None → True for GROVE_CREATED
    assert state.branch not in _branches(tmp_repo)


def test_kill_user_attached_branch_default_keeps(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    """Default-off for USER_ATTACHED: the user's pre-existing branch survives kill."""
    del fake_tmux
    subprocess.run(
        ["git", "branch", "feature/keepme", "main"],
        cwd=tmp_repo,
        check=True,
        capture_output=True,
    )
    state = manager.create(
        CreateWorkspaceRequest(
            agent_name="claude",
            title="keep my branch",
            branch_plan=ExistingLocalBranch(name="feature/keepme"),
        )
    )
    manager.kill(state.id)  # default → False for USER_ATTACHED
    assert "feature/keepme" in _branches(tmp_repo)
    # Worktree still cleaned up.
    assert _wt(state.worktree_path) not in _worktrees(tmp_repo)


def _fail_after_worktree_add(manager: WorkspaceManager) -> Callable[..., None]:
    """Let `worktree_add` do its real work, then fail the create at that step.

    `git worktree add -b` creates the ref before it validates anything else,
    and `_add_worktree` sets an upstream after the add returns — so a failure
    at this point routinely leaves a real branch (and sometimes a real
    worktree) behind. Patching the call to raise *without* running it would
    test a case where there is nothing to clean up.
    """
    real = manager._git.worktree_add

    def _boom(*args: object, **kwargs: object) -> None:
        real(*args, **kwargs)  # type: ignore[arg-type]
        raise GroveError("simulated failure after the ref exists")

    manager._git.worktree_add = _boom  # type: ignore[method-assign]
    return real


def test_failed_create_deletes_the_branch_it_leaked(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    """A create that dies at `worktree_add` must leave nothing behind.

    What this pins is the *retry*, not the tidiness: an abandoned branch
    makes the next create fail `BranchConflict`, blaming the user for a
    branch Grove created and forgot.
    """
    del fake_tmux
    real_add = _fail_after_worktree_add(manager)
    plan = NewNamedBranch(name="feature/retry-me", base_ref="main")

    with pytest.raises(GroveError):
        manager.create(
            CreateWorkspaceRequest(agent_name="claude", title="doomed", branch_plan=plan)
        )

    assert "feature/retry-me" not in _branches(tmp_repo)
    assert manager.list() == []
    # The whole point: the same request now succeeds.
    manager._git.worktree_add = real_add  # type: ignore[method-assign]
    retried = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="doomed", branch_plan=plan)
    )
    assert retried.branch == "feature/retry-me"


def test_failed_create_never_deletes_a_user_attached_branch(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    """Rollback unwinds what create did, and create did not make this branch.

    Placement alone is not a sufficient gate: a WORKTREE workspace attached to
    the user's own branch must not lose it to a create that fails after the
    record was written and unwinds through teardown.
    """
    del fake_tmux
    subprocess.run(
        ["git", "branch", "feature/precious", "main"],
        cwd=tmp_repo,
        check=True,
        capture_output=True,
    )
    _fail_after_worktree_add(manager)

    with pytest.raises(GroveError):
        manager.create(
            CreateWorkspaceRequest(
                agent_name="claude",
                title="attach and fail",
                branch_plan=ExistingLocalBranch(name="feature/precious"),
            )
        )

    assert "feature/precious" in _branches(tmp_repo)
    assert manager.list() == []


def test_kill_explicit_delete_branch_true_overrides_provenance_default(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    """Caller can force delete_branch=True even on USER_ATTACHED."""
    del fake_tmux
    subprocess.run(
        ["git", "branch", "feature/burn", "main"],
        cwd=tmp_repo,
        check=True,
        capture_output=True,
    )
    state = manager.create(
        CreateWorkspaceRequest(
            agent_name="claude",
            title="burn",
            branch_plan=ExistingLocalBranch(name="feature/burn"),
        )
    )
    manager.kill(state.id, delete_branch=True)
    assert "feature/burn" not in _branches(tmp_repo)


def test_kill_explicit_delete_branch_false_overrides_provenance_default(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    """Caller can force delete_branch=False even on GROVE_CREATED."""
    del fake_tmux
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="keep-grove-branch"))
    branch_name = state.branch
    manager.kill(state.id, delete_branch=False)
    assert branch_name in _branches(tmp_repo)


def test_init_script_failure_rolls_back(
    manager: WorkspaceManager,
    fake_tmux: FakeTmux,
    tmp_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Sandbox the init log: it survives rollback by design, so an unpatched
    # path would leave an orphan file in the real user state dir.
    monkeypatch.setattr(
        "grove.core.paths.init_log_path",
        lambda workspace_id: tmp_path / "init-logs" / f"{workspace_id}-init.log",
    )
    # Configure the manager's config to enable init + fail_fast.
    cfg = GroveConfig.model_validate(
        {
            "worktree": {
                "root_template": manager.config.worktree.root_template,
                "branch_prefix": manager.config.worktree.branch_prefix,
            },
            "tmux": {"session_prefix": manager.config.tmux.session_prefix},
            "init_script": {
                "enabled": True,
                "inline": "false",
                "fail_fast": True,
            },
        }
    )
    rollback_manager = WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=manager.store)
    fake_tmux.init_exit_code = 1

    with pytest.raises(GroveError, match="fail_fast"):
        rollback_manager.create(CreateWorkspaceRequest(agent_name="claude", title="dies"))

    # Worktree, branch, tmux session, and store record must all be gone.
    assert not any(b.startswith("test/dies-") for b in _branches(tmp_repo))
    assert all("dies-" not in str(p) for p in _worktrees(tmp_repo))
    assert all("dies-" not in s for s in fake_tmux.sessions)
    assert rollback_manager.store.load_all() == []


def test_failed_init_keeps_log_and_error_carries_tail(
    manager: WorkspaceManager,
    fake_tmux: FakeTmux,
    tmp_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rollback must not delete the init log — the one artifact that explains
    a fail_fast failure — and the error must name both the failing command
    and its stderr.
    """
    logs_dir = tmp_path / "init-logs"
    monkeypatch.setattr(
        "grove.core.paths.init_log_path",
        lambda workspace_id: logs_dir / f"{workspace_id}-init.log",
    )
    cfg = GroveConfig.model_validate(
        {
            "worktree": {
                "root_template": manager.config.worktree.root_template,
                "branch_prefix": manager.config.worktree.branch_prefix,
            },
            "tmux": {"session_prefix": manager.config.tmux.session_prefix},
            "init_script": {"enabled": True, "inline": "false", "fail_fast": True},
        }
    )
    rollback_manager = WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=manager.store)
    fake_tmux.init_exit_code = 1
    fake_tmux.init_stderr = "error: unrecognized subcommand 'sync'\n"

    with pytest.raises(GroveError) as excinfo:
        rollback_manager.create(CreateWorkspaceRequest(agent_name="claude", title="diag"))

    # Rollback completed (no record left) but the log survived it.
    assert rollback_manager.store.load_all() == []
    logs = list(logs_dir.glob("*-init.log"))
    assert len(logs) == 1

    # The error is self-diagnosing: log path + stderr tail, no shell reopening.
    message = str(excinfo.value)
    assert str(logs[0]) in message
    assert "unrecognized subcommand 'sync'" in message


# ─── a refused pause is a no-op, not a half-teardown ────────────────────────


def _dirty(state: WorkspaceState, *, tracked: bool) -> None:
    """Make the worktree dirty the way git's own removal check sees it."""
    worktree = Path(state.worktree_path)
    if tracked:
        (worktree / "README.md").write_text("modified\n", encoding="utf-8")
    else:
        (worktree / "brand-new.txt").write_text("an agent's first act\n", encoding="utf-8")


@pytest.mark.parametrize("tracked", [True, False])
def test_pausing_a_dirty_workspace_changes_nothing(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_repo: Path, tracked: bool
) -> None:
    """If `pause` kills the session and stops the container before asking git
    to remove the worktree, a dirty worktree's refusal leaves the first two
    undone — the user is told the pause failed (reasonably read as "nothing
    happened") while the workspace actually sits with no session, a stopped
    container, a worktree still on disk and a record still RUNNING. Pause
    must gate on the dirty check BEFORE tearing anything down.

    Parametrized over tracked and UNTRACKED changes because a precondition
    using `--untracked-files=no` reports clean for a worktree holding only
    new files — an agent's usual first act — while `git worktree remove`
    itself refuses on "modified **or** untracked"."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="dirty"))
    _dirty(state, tracked=tracked)

    with pytest.raises(WorkspaceStateError):
        manager.pause(state.id)

    # Nothing happened, which is what "the pause failed" has to mean.
    assert state.tmux_session in fake_tmux.sessions
    assert _wt(state.worktree_path) in _worktrees(tmp_repo)
    assert manager.store.get(state.id).status == WorkspaceStatus.RUNNING


@pytest.mark.parametrize("tracked", [True, False])
def test_force_still_discards_and_succeeds(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_repo: Path, tracked: bool
) -> None:
    """`force` is the documented escape hatch and must not be gated by the new
    precondition — the user has already said discard, so it is not asked."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="forced"))
    _dirty(state, tracked=tracked)

    paused = manager.pause(state.id, force=True)

    assert paused.status == WorkspaceStatus.PAUSED
    assert _wt(state.worktree_path) not in _worktrees(tmp_repo)
    assert state.tmux_session not in fake_tmux.sessions


def test_a_clean_workspace_still_pauses(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    """The precondition must not make the ordinary path stricter."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="clean"))

    assert manager.pause(state.id).status == WorkspaceStatus.PAUSED
    assert _wt(state.worktree_path) not in _worktrees(tmp_repo)
