"""`WorkspaceState.base_commit` — the recorded anchor for "since created".

The question every one of these pins is *what does this workspace measure its
own work against*. The old answer was `base_branch`, re-derived on every read,
which is right for a Grove-created branch and degenerate everywhere else: for a
ROOT workspace the branch IS the base branch, so `git log base..branch` was
empty however much work had been done.

Real git throughout — the whole point is which revision git is handed, and a
faked `GitRepo` would only re-assert this module's own belief about that.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.branch_plan import ExistingLocalBranch, NewNamedBranch, RootBranch
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.git import GitRepo
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState
from tests.conftest import FakeTmux


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)
    return out.stdout.strip()


def _commit(cwd: Path, name: str, body: str = "x\n") -> str:
    (cwd / name).write_text(body, encoding="utf-8")
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-m", f"add {name}", "--no-gpg-sign")
    return _git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def manager(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> WorkspaceManager:
    del fake_tmux  # used via monkeypatch
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


def _create(manager: WorkspaceManager, title: str, **kw: Any) -> WorkspaceState:
    return manager.create(CreateWorkspaceRequest(agent_name="claude", title=title, **kw))


# ─── what gets recorded, per plan variant ────────────────────────────────────


def test_new_branch_records_the_sha_its_base_ref_pointed_at(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    """The unambiguous case, and the one that must be exact: a branch created
    off `main` starts at whatever `main` was at that instant."""
    base_sha = _git(tmp_repo, "rev-parse", "main")

    state = _create(manager, "feature", branch_plan=NewNamedBranch(name="feat/x", base_ref="main"))

    assert state.base_commit == base_sha
    assert state.diff_base == base_sha


def test_root_records_live_head(manager: WorkspaceManager, tmp_repo: Path) -> None:
    """The case that makes root workspaces work at all — there is no base
    branch to diverge from, so the anchor is HEAD at the moment of create."""
    head = _git(tmp_repo, "rev-parse", "HEAD")

    state = _create(manager, "root task", branch_plan=RootBranch())

    assert state.base_commit == head


def test_attaching_an_existing_branch_records_that_branch_tip_not_repo_head(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    """The attached branch's own tip is where this workspace starts. Reading
    the repo root's HEAD instead would credit the workspace with every commit
    that branch already carried."""
    _git(tmp_repo, "checkout", "-b", "existing")
    branch_tip = _commit(tmp_repo, "already-here.txt")
    _git(tmp_repo, "checkout", "main")
    root_head = _git(tmp_repo, "rev-parse", "HEAD")
    assert branch_tip != root_head

    state = _create(manager, "attach", branch_plan=ExistingLocalBranch(name="existing"))

    assert state.base_commit == branch_tip


# ─── the anchor is fixed in time ─────────────────────────────────────────────


def test_the_base_branch_moving_on_does_not_move_the_anchor(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    """ "Since the workspace was created" is anchored in TIME, not in the base
    branch's present. Someone else landing work on `main` afterwards must not
    retroactively enlarge or shrink what this workspace is credited with."""
    state = _create(manager, "feature", branch_plan=NewNamedBranch(name="feat/y", base_ref="main"))
    recorded = state.base_commit
    worktree = Path(state.worktree_path)
    _commit(worktree, "mine.txt")

    _commit(tmp_repo, "someone-elses.txt")  # `main` moves on underneath

    assert manager.get(state.id).base_commit == recorded
    subjects = [c.subject for c in manager.commits(state.id)]
    assert subjects == ["add mine.txt"]


# ─── the two consumers ───────────────────────────────────────────────────────


def test_root_workspace_reports_its_commits(manager: WorkspaceManager, tmp_repo: Path) -> None:
    """The headline bug: a root workspace's branch is its own base branch, so
    the branch-derived range collapsed to empty and the user saw nothing."""
    state = _create(manager, "root task", branch_plan=RootBranch())
    _commit(tmp_repo, "one.txt")
    _commit(tmp_repo, "two.txt")

    subjects = [c.subject for c in manager.commits(state.id)]

    assert subjects == ["add two.txt", "add one.txt"]


def test_root_workspace_patch_carries_committed_and_uncommitted_work(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    """Same bug on the files axis: committed work has to stay in the patch, or
    a root workspace has no surface at all that says what it changed."""
    state = _create(manager, "root task", branch_plan=RootBranch())
    _commit(tmp_repo, "committed.txt", "landed\n")
    (tmp_repo / "in-flight.txt").write_text("wip\n", encoding="utf-8")

    diff = manager.working_diff(state.id)

    assert diff.available is True
    assert diff.files == 2
    assert "committed.txt" in diff.patch and "+landed" in diff.patch
    assert "in-flight.txt" in diff.patch and "+wip" in diff.patch


def test_peek_line_stats_count_work_a_root_workspace_committed(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    """`diff_stats` shares the anchor for the same reason `commits` does; only
    `ahead_behind` keeps the base BRANCH, since "behind" asks how far that
    branch has moved and a frozen commit can only answer zero."""
    state = _create(manager, "root task", branch_plan=RootBranch())
    _commit(tmp_repo, "counted.txt", "a\nb\nc\n")

    assert manager.peek(state.id).diff_added == 3


# ─── absent must read as absent ──────────────────────────────────────────────


def test_a_record_without_an_anchor_degrades_to_the_previous_behavior(
    manager: WorkspaceManager, tmp_repo: Path, tmp_path: Path
) -> None:
    """A workspace that already existed when the field shipped has no anchor,
    and no baseline is fabricated for it: the patch falls back to HEAD and the
    log falls back to `base_branch`, exactly as it did before the field existed.

    **The time-window fallback must NOT reach this record**, which is the whole
    point of asserting the scope here. `base_branch` is `main`, a real ref that
    is genuinely behind `feat/z`, so `main..feat/z` is exact — and a window errs
    high, so taking one here would trade a precise answer for a looser one on
    the sole grounds that a *different* record shape cannot use refs. The
    fallback is keyed on whether the range is degenerate, not on whether the
    anchor is missing; the ROOT case that needs it is pinned separately in
    `test_commits_since_fallback.py`.

    Backfilling a plausible-looking anchor stays refused either way: a wrong
    baseline is indistinguishable from a right one.
    """
    state = _create(manager, "legacy", branch_plan=NewNamedBranch(name="feat/z", base_ref="main"))
    worktree = Path(state.worktree_path)
    JsonWorkspaceStore(path=tmp_path / "state.json").save(replace(state, base_commit=None))
    _commit(worktree, "committed.txt")
    (worktree / "loose.txt").write_text("wip\n", encoding="utf-8")

    legacy = manager.get(state.id)
    assert legacy.base_commit is None
    assert legacy.diff_base == "main"
    commits = manager.commits(state.id)
    assert [c.subject for c in commits] == ["add committed.txt"]
    assert {c.scope for c in commits} == {"since_fork_point"}
    # The patch is uncommitted-only again.
    diff = manager.working_diff(state.id)
    assert diff.files == 1
    assert "loose.txt" in diff.patch


def test_the_anchor_survives_a_store_round_trip(
    manager: WorkspaceManager, tmp_repo: Path, tmp_path: Path
) -> None:
    """Persisted state is a compatibility surface — and a state file written
    before the field existed must still load, as None."""
    path = tmp_path / "state.json"
    state = _create(manager, "feature", branch_plan=NewNamedBranch(name="feat/w", base_ref="main"))
    assert JsonWorkspaceStore(path=path).get(state.id).base_commit == state.base_commit

    raw = json.loads(path.read_text(encoding="utf-8"))
    for record in raw["workspaces"].values():
        record.pop("base_commit", None)
    path.write_text(json.dumps(raw), encoding="utf-8")

    assert JsonWorkspaceStore(path=path).get(state.id).base_commit is None


# ─── the case deliberately NOT handled ───────────────────────────────────────


def test_edits_already_in_the_tree_at_create_are_counted_as_the_workspaces_own(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    """A root workspace created over an already-dirty tree reports those edits
    as its own, and that is the accepted answer rather than a bug.

    Separating them needs a per-file snapshot of the pre-existing dirt taken at
    create and consulted on every read — machinery whose only job is to shave a
    slightly generous number, in a case the user is already the author of. The
    number errs high; it never errs by hiding work.
    """
    (tmp_repo / "pre-existing.txt").write_text("not mine\n", encoding="utf-8")

    state = _create(manager, "root task", branch_plan=RootBranch())

    assert "pre-existing.txt" in manager.working_diff(state.id).patch


def test_git_repo_tracked_patch_defaults_to_head(tmp_repo: Path) -> None:
    """The mechanism's default is the historical one, so a caller with no
    anchor to pass gets exactly the old patch."""
    _commit(tmp_repo, "seed.txt", "one\n")
    (tmp_repo / "seed.txt").write_text("two\n", encoding="utf-8")

    patch = GitRepo(tmp_repo).tracked_patch(tmp_repo)

    assert "-one" in patch and "+two" in patch
