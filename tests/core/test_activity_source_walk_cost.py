"""The worktree watch set is linear in DIRECTORIES, not in tracked files.

A tracked tree names each of its directories once per file it holds, so walking
every file's whole ancestor chain re-resolves the same handful of paths
thousands of times: measured on the Grove checkout, 1372 files produced 8114
``resolve()`` calls for 138 directories. That cost is paid per workspace at
bootstrap, so a host with several workspaces spends seconds of pure syscall
overhead before the daemon is usable.

Equivalence is asserted against an independently written oracle rather than a
recorded expectation, because the risk of a de-duplicating walk is not that it
is slow -- it is that terminating early drops a directory nobody watches
afterwards, which presents as a file edit that never reaches the dashboard.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core.activity_sources import ActivitySources
from grove.core.git import GitRepo
from grove.core.workspace import WorkspaceState, WorkspaceStatus


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One git repo for the module.

    Built once because the fixture's own `git init`/`add`/`commit` are the most
    expensive thing in this file and nothing here mutates the tree. It is also
    courtesy to the rest of the session: `tests/core/agents/test_native_codex.py`
    spawns real subprocesses and waits a single `asyncio.sleep(0)` for them to
    answer, so subprocess work elsewhere in the run shifts that race.
    """
    return _build(tmp_path_factory.mktemp("walkcost"))


def _build(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    # A shape that punishes the quadratic walk: one deep chain plus siblings, so
    # the same ancestors are reachable through many different files.
    for index in range(12):
        nested = root / "src" / "deep" / "deeper" / f"leaf{index}"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "module.py").write_text("x = 1\n")
    (root / "src" / "top.py").write_text("y = 2\n")
    (root / "README.md").write_text("readme\n")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=T", "-c", "user.email=t@e.x", "commit", "-qm", "seed")
    return root


def _oracle(root: Path, files: list[str]) -> set[Path]:
    """Every directory at or under *root* that holds a tracked file.

    Written from the requirement rather than from the implementation, and
    deliberately the naive full walk: it is the thing the fast path must agree
    with.
    """
    anchor = root.resolve()
    found = {anchor}
    for relpath in files:
        parent = (root / relpath).parent.resolve()
        while parent.is_relative_to(anchor):
            if parent.is_dir():
                found.add(parent)
            if parent == anchor:
                break
            parent = parent.parent
    return found


def test_watched_directories_match_a_naive_full_walk(repo: Path) -> None:
    root = repo
    files = list(GitRepo(root).list_files(root))
    assert len(files) > 12, "fixture must hold more files than directories"

    sources = ActivitySources.__new__(ActivitySources)
    sources._worktree_dirs = {}
    sources._add_worktree_roots(
        _state(root),
        ("repo", "workspace"),
    )

    assert set(sources._worktree_dirs) == _oracle(root, files)


def test_a_directory_reached_only_through_a_later_file_is_still_watched(
    tmp_path: Path,
) -> None:
    """The de-duplication must not stop at the first file's chain.

    An ancestor-seen short circuit is the whole optimization, and the way to get
    it wrong is to mark a directory walked before recording it -- which drops
    every sibling subtree discovered later. That failure is invisible in the
    aggregate count, so this asserts on a specific leaf.
    """
    root = _build(tmp_path)
    isolated = root / "other" / "branch" / "twig"
    isolated.mkdir(parents=True)
    (isolated / "late.py").write_text("z = 3\n")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=T", "-c", "user.email=t@e.x", "commit", "-qm", "late")

    sources = ActivitySources.__new__(ActivitySources)
    sources._worktree_dirs = {}
    sources._add_worktree_roots(_state(root), ("repo", "workspace"))

    watched = set(sources._worktree_dirs)
    assert isolated.resolve() in watched
    assert (root / "other").resolve() in watched
    assert (root / "src" / "deep" / "deeper" / "leaf11").resolve() in watched


def test_the_walk_visits_each_directory_once(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cost is COUNTED, not timed: this host's noise floor swamps the ms.

    The property is that the walk scales with DIRECTORIES, not with tracked
    files, so a regression to the per-file chain walk shows up as a multiple of
    the directory count rather than as a slow test.

    Counting ``os.stat`` rather than ``Path.resolve`` is deliberate twice over.
    Assigning to ``Path.resolve`` mutates the class for the whole process, and a
    subclass counter is VACUOUS here -- the walk resolves derived paths
    (``parent.parent``, ``root / relpath``) which pathlib rebuilds as plain
    ``Path``, so the naive version would call it zero times and the mutation
    would pass. ``stat`` is what ``resolve`` actually spends, and monkeypatch
    undoes it at teardown.
    """
    root = repo
    files = list(GitRepo(root).list_files(root))
    directories = _oracle(root, files)

    calls = 0
    real_stat = os.stat

    def counting_stat(*args: object, **kwargs: object) -> os.stat_result:
        nonlocal calls
        calls += 1
        return real_stat(*args, **kwargs)  # type: ignore[arg-type]

    sources = ActivitySources.__new__(ActivitySources)
    sources._worktree_dirs = {}
    monkeypatch.setattr(os, "stat", counting_stat)
    try:
        sources._add_worktree_roots(_state(root), ("repo", "workspace"))
    finally:
        monkeypatch.undo()

    # `resolve()` stats each path component, so the ceiling is per DIRECTORY
    # times a small constant. The naive walk re-resolves a chain per file and
    # exceeds this by roughly an order of magnitude on this fixture.
    assert calls <= len(directories) * 8, (
        f"{calls} stat() for {len(directories)} dirs and {len(files)} files"
    )


def _state(root: Path) -> WorkspaceState:
    now = datetime.now(UTC)
    return WorkspaceState(
        id="walkcost",
        title="walk cost",
        repo_root=str(root),
        branch="main",
        base_branch="main",
        worktree_path=str(root),
        tmux_session="unused",
        agent_name="shell",
        agent_kind="generic",
        status=WorkspaceStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )
