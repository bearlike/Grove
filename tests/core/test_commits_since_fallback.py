"""The commit log for a record with NO recorded fork point.

`base_commit` is nullable, and `diff_base`'s fallback is the base BRANCH — which
is right for a Grove-created branch and degenerate for a ROOT workspace, whose
`base_branch` is the literal string ``"HEAD"``. ``git log HEAD..<branch>`` is
then *structurally* empty however much work was done, and the Changes tab
printed "No commits on this branch yet" over a branch holding a hundred. That is
a confident false claim rather than a missing answer, which this repo treats as
the worse failure.

The fix answers by TIME instead: `created_at` is a recorded fact, so
``git log --since=<created_at> <branch>`` never claims to be the anchor and
answers a different, well-posed question. It errs HIGH — a commit somebody else
pushed to the branch inside the window is included — so the answer says which
question it answered, via `CommitScope`.

Real git throughout, for the reason `test_base_commit_anchor.py` states: the
whole subject is which revision (and which date) git is handed, and a faked
`GitRepo` would only re-assert this module's own belief about that. Commit dates
are PINNED via `GIT_COMMITTER_DATE` rather than taken from the wall clock —
git's date filter has one-second resolution, so a test committing "now" against
a stamp taken "now" is a coin flip.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.branch_plan import NewNamedBranch, RootBranch
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.git import GitRepo
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState
from tests.conftest import FakeTmux

# Three points an hour apart — far enough that git's one-second date resolution
# cannot blur them, and all of them AHEAD of wall-clock now on purpose: the
# shared `tmp_repo` fixture commits its own `init` at whatever time the suite
# runs, and a window opening after that is what keeps a commit this file does
# not own out of every assertion without rewriting a fixture it does not own.
# A literal date would rot into that fixture's commit the moment it passed.
_NOW = datetime.now(UTC)
BEFORE = _NOW + timedelta(hours=1)
BORN = _NOW + timedelta(hours=2)
AFTER = _NOW + timedelta(hours=3)


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)
    return out.stdout.strip()


def _commit_at(cwd: Path, name: str, when: datetime) -> None:
    """Commit `name` with a PINNED committer date.

    ``--since`` filters on the committer date (which is also what `%cI` prints),
    so that is the one that has to be controlled; the author date rides along so
    the two never disagree in a way a future reader has to untangle.
    """
    (cwd / name).write_text("x\n", encoding="utf-8")
    stamp = when.isoformat()
    subprocess.run(["git", "-C", str(cwd), "add", "-A"], check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-C", str(cwd), "commit", "-m", f"add {name}", "--no-gpg-sign"],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp},
    )


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


def _as_pre_anchor(
    manager: WorkspaceManager, tmp_path: Path, state: WorkspaceState
) -> WorkspaceState:
    """Rewrite a record into the shape that has no anchor — the real bug's shape.

    A pre-anchor record: written before `base_commit` existed, so the field is
    None and every "since created" read has only `created_at` to work from.
    """
    JsonWorkspaceStore(path=tmp_path / "state.json").save(
        replace(state, base_commit=None, created_at=BORN)
    )
    return manager.get(state.id)


# ─── the reproduction: the ref range cannot answer, the window can ───────────


def test_the_ref_range_a_pre_anchor_root_record_falls_back_to_is_structurally_empty(
    tmp_repo: Path,
) -> None:
    """The measurement the whole change rests on, pinned against real git.

    A ROOT workspace's `base_branch` is the literal ``"HEAD"`` and its branch is
    whatever HEAD points at, so the two are the SAME REF and ``git log
    HEAD..main`` is empty by construction — not because nothing happened, but
    because a range from a ref to itself has no members. Reproduced on this
    repo's own root workspace at the time of writing: 0 against a true 106.

    Both halves are asserted together on purpose. Showing only that the window
    form returns commits would leave "was the old form really broken?" resting
    on the docstring, and this file's entire premise is that it was.
    """
    _commit_at(tmp_repo, "one.txt", AFTER)
    _commit_at(tmp_repo, "two.txt", AFTER)
    git = GitRepo(tmp_repo)

    assert git.branch_commits("main", "HEAD") == ()

    windowed = git.branch_commits_since("main", BORN)
    assert [c.subject for c in windowed] == ["add two.txt", "add one.txt"]


def test_commits_predating_the_window_are_excluded(tmp_repo: Path) -> None:
    """The window is a filter, not "all of history relabelled".

    This also proves the stamp Grove formats is one git actually UNDERSTANDS.
    ``--since`` is parsed by approxidate, which never fails — an unparseable
    value is silently read as *now*, which would return an empty log that looks
    exactly like a real answer. If that happened here, `after.txt` would be
    missing rather than the test failing on something diagnostic.
    """
    _commit_at(tmp_repo, "before.txt", BEFORE)
    _commit_at(tmp_repo, "after.txt", AFTER)

    subjects = [c.subject for c in GitRepo(tmp_repo).branch_commits_since("main", BORN)]

    assert subjects == ["add after.txt"]


def test_a_naive_timestamp_is_read_as_utc(tmp_repo: Path) -> None:
    """A naive stamp means UTC here, matching `adopts_session`'s coercion.

    Reading it as LOCAL time would move the window by the host's offset — a
    filter that silently answers differently per machine, and in the direction
    that hides work for anyone east of UTC.
    """
    _commit_at(tmp_repo, "before.txt", BEFORE)
    _commit_at(tmp_repo, "after.txt", AFTER)
    git = GitRepo(tmp_repo)

    naive = git.branch_commits_since("main", BORN.replace(tzinfo=None))

    assert [c.subject for c in naive] == [c.subject for c in git.branch_commits_since("main", BORN)]


def test_the_since_form_respects_limit_and_never_raises(tmp_repo: Path) -> None:
    """Parity with `branch_commits`: capped on request, best-effort always."""
    for i in range(4):
        _commit_at(tmp_repo, f"f{i}.txt", AFTER)
    git = GitRepo(tmp_repo)

    assert len(git.branch_commits_since("main", BORN, limit=2)) == 2
    assert git.branch_commits_since("does-not-exist", BORN) == ()


# ─── the answer says which question it answered ──────────────────────────────


def test_each_form_labels_its_own_scope(tmp_repo: Path) -> None:
    """`CommitScope` is what keeps the degraded answer distinguishable.

    Three forms, three honest labels — and `recent_commits`'s None is the load
    bearing one: it walks branch history with no anchor at all, so giving it
    either scope would be a claim nobody made. That is the same reason
    `WorkspaceDiff.reason` is set if and only if `available` is False.
    """
    _commit_at(tmp_repo, "one.txt", AFTER)
    _git(tmp_repo, "checkout", "-b", "feat/x")
    _commit_at(tmp_repo, "two.txt", AFTER)
    git = GitRepo(tmp_repo)

    anchored = git.branch_commits("feat/x", "main")
    windowed = git.branch_commits_since("feat/x", BORN)
    unanchored = git.recent_commits("feat/x")

    assert anchored and {c.scope for c in anchored} == {"since_fork_point"}
    assert windowed and {c.scope for c in windowed} == {"since_created_at"}
    assert unanchored and {c.scope for c in unanchored} == {None}


# ─── the manager picks the form, and only when it has to ─────────────────────


def test_a_pre_anchor_root_record_reports_the_commits_it_used_to_hide(
    manager: WorkspaceManager, tmp_repo: Path, tmp_path: Path
) -> None:
    """The headline: the exact record shape that printed a false zero.

    Root placement, `base_branch == "HEAD"`, no anchor — the state of this
    repo's own root workspace. Every commit on the branch inside the window is
    reported, and every row says the answer is time-bounded.
    """
    state = _create(manager, "root task", branch_plan=RootBranch())
    assert state.base_branch == "HEAD"
    _as_pre_anchor(manager, tmp_path, state)
    _commit_at(tmp_repo, "one.txt", AFTER)
    _commit_at(tmp_repo, "two.txt", AFTER)

    commits = manager.commits(state.id)

    assert [c.subject for c in commits] == ["add two.txt", "add one.txt"]
    assert {c.scope for c in commits} == {"since_created_at"}


def test_work_that_predates_a_pre_anchor_workspace_is_not_credited_to_it(
    manager: WorkspaceManager, tmp_repo: Path, tmp_path: Path
) -> None:
    """The window still has a floor, which is what makes it an answer at all.

    Without one this would be `recent_commits` wearing a filter's name — every
    commit the branch ever carried, presented as this workspace's work.
    """
    state = _create(manager, "root task", branch_plan=RootBranch())
    _as_pre_anchor(manager, tmp_path, state)
    _commit_at(tmp_repo, "ancient.txt", BEFORE)
    _commit_at(tmp_repo, "mine.txt", AFTER)

    assert [c.subject for c in manager.commits(state.id)] == ["add mine.txt"]


def test_an_anchored_record_is_answered_exactly_as_before(
    manager: WorkspaceManager, tmp_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With an anchor present the fallback must not merely agree — it must not RUN.

    Asserted by making the window form fail loudly rather than by comparing two
    outputs: the two forms return the same subjects in this scenario, so an
    output comparison would pass with the branch inverted. What is being pinned
    is that the confident path is untouched, and only a call-site assertion can
    say that.
    """
    state = _create(manager, "feature", branch_plan=NewNamedBranch(name="feat/y", base_ref="main"))
    assert state.base_commit is not None
    worktree = Path(state.worktree_path)
    _commit_at(worktree, "mine.txt", AFTER)

    def _never(*args: object, **kwargs: object) -> None:
        raise AssertionError("branch_commits_since ran for a record that HAS an anchor")

    monkeypatch.setattr(GitRepo, "branch_commits_since", _never)

    commits = manager.commits(state.id)

    assert [c.subject for c in commits] == ["add mine.txt"]
    assert {c.scope for c in commits} == {"since_fork_point"}
