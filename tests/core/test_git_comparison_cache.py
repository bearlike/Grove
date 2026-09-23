"""GitRepo's immutable commit-comparison cache, exercised against real git."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from grove.core.git import GitRepo
from grove.core.workspace import CommitSummary


def _git(runner: Callable[..., subprocess.CompletedProcess[Any]], cwd: Path, *args: str) -> None:
    runner(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(runner: Callable[..., subprocess.CompletedProcess[Any]], cwd: Path, name: str) -> None:
    (cwd / name).write_text(f"{name}\n", encoding="utf-8")
    _git(runner, cwd, "add", name)
    _git(runner, cwd, "commit", "-m", f"add {name}", "--no-verify")


@pytest.fixture
def git_subprocess_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Record real git invocations without replacing their behavior."""
    real_run = subprocess.run
    calls: list[tuple[str, ...]] = []

    def _counted_run(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cmd and cmd[0] == "git":
            calls.append(tuple(cmd))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr("grove.core.git.subprocess.run", _counted_run)
    return calls


def _activity_comparisons(
    git: GitRepo,
) -> tuple[tuple[int, int], tuple[int, int], tuple[CommitSummary, ...]]:
    return (
        git.ahead_behind("feature", "main"),
        git.diff_stats("feature", "main"),
        git.recent_commits("feature", limit=3),
    )


def test_comparisons_reuse_resolved_oids_and_refresh_when_a_ref_is_amended(
    tmp_repo: Path, git_subprocess_calls: list[tuple[str, ...]]
) -> None:
    """Unchanged names reuse their immutable result; an amended tip cannot."""
    real_run = subprocess.run
    _git(real_run, tmp_repo, "checkout", "-b", "feature")
    _commit(real_run, tmp_repo, "feature.txt")
    git = GitRepo(tmp_repo)

    first = _activity_comparisons(git)
    cold_processes = len(git_subprocess_calls)
    git_subprocess_calls.clear()

    assert _activity_comparisons(git) == first
    warm_processes = len(git_subprocess_calls)

    _git(real_run, tmp_repo, "commit", "--amend", "-m", "amended feature", "--no-verify")
    git_subprocess_calls.clear()
    amended = _activity_comparisons(git)
    changed_processes = len(git_subprocess_calls)

    assert cold_processes == 9  # three comparison commands plus three batch OID reads
    assert warm_processes == 3  # only batch OID resolution; no comparison command repeats
    assert changed_processes == 6
    assert amended[2][0].subject == "amended feature"


def test_comparison_uses_the_resolved_oids_when_a_ref_moves_mid_read(
    tmp_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ref move after resolution must not poison the old OID cache key."""
    _git(subprocess.run, tmp_repo, "checkout", "-b", "feature")
    _commit(subprocess.run, tmp_repo, "original.txt")
    original = _git_oid(tmp_repo, "feature")
    _commit(subprocess.run, tmp_repo, "moved.txt")
    moved = _git_oid(tmp_repo, "feature")
    _git(subprocess.run, tmp_repo, "reset", "--hard", original)
    git = GitRepo(tmp_repo)
    real_run = git._run
    moved_ref = False

    def _move_before_log(
        cmd: list[str], *, cwd: Path | None = None, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        nonlocal moved_ref
        if cmd[1:2] == ["log"] and not moved_ref:
            moved_ref = True
            _git(subprocess.run, tmp_repo, "reset", "--hard", moved)
        return real_run(cmd, cwd=cwd, check=check)

    monkeypatch.setattr(git, "_run", _move_before_log)

    first = git.recent_commits("feature")
    second = git.recent_commits("feature")

    assert [commit.subject for commit in first] == ["add original.txt", "init"]
    assert [commit.subject for commit in second] == ["add moved.txt", "add original.txt", "init"]


def test_oid_equivalent_branch_names_share_one_cached_comparison(
    tmp_repo: Path, git_subprocess_calls: list[tuple[str, ...]]
) -> None:
    """Aliases resolve to one immutable key, rather than separate name-keyed entries."""
    _git(subprocess.run, tmp_repo, "branch", "alias", "main")
    git = GitRepo(tmp_repo)
    git_subprocess_calls.clear()

    assert git.recent_commits("main") == git.recent_commits("alias")

    log_calls = [call for call in git_subprocess_calls if call[1:2] == ("log",)]
    assert len(log_calls) == 1


def test_failed_ref_comparison_recovers_after_the_ref_is_created(tmp_repo: Path) -> None:
    """A transient ref failure is not retained as a confident empty comparison."""
    git = GitRepo(tmp_repo)

    assert git.ahead_behind("recovered", "main") == (0, 0)
    assert git.diff_stats("recovered", "main") == (0, 0)
    assert git.recent_commits("recovered") == ()

    _git(subprocess.run, tmp_repo, "checkout", "-b", "recovered")
    _commit(subprocess.run, tmp_repo, "recovered.txt")

    assert git.ahead_behind("recovered", "main") == (1, 0)
    assert git.diff_stats("recovered", "main") == (1, 0)
    assert [commit.subject for commit in git.recent_commits("recovered")] == [
        "add recovered.txt",
        "init",
    ]


def test_status_read_does_not_refresh_the_index(
    tmp_repo: Path, git_subprocess_calls: list[tuple[str, ...]]
) -> None:
    """Background status must not mutate the shared index while a user may add."""
    tracked = tmp_repo / "README.md"
    before = (tmp_repo / ".git" / "index").stat().st_mtime_ns
    os.utime(tracked, None)
    git = GitRepo(tmp_repo)
    git_subprocess_calls.clear()

    assert git.dirty_file_count(tmp_repo) == 0

    assert (tmp_repo / ".git" / "index").stat().st_mtime_ns == before
    # ``--no-optional-locks`` is a git-wide option and must PRECEDE the
    # subcommand; `git status --no-optional-locks` is rejected outright
    # (exit 129), which ``check=False`` would silently swallow.
    status_calls = [call for call in git_subprocess_calls if "status" in call]
    assert status_calls == [("git", "--no-optional-locks", "status", "--porcelain")]


def test_local_git_config_change_invalidates_a_cached_log(
    tmp_repo: Path, git_subprocess_calls: list[tuple[str, ...]]
) -> None:
    """Commit abbreviations are config-sensitive, so local config joins the key."""
    git = GitRepo(tmp_repo)
    git_subprocess_calls.clear()

    original = git.recent_commits("main")
    _git(subprocess.run, tmp_repo, "config", "core.abbrev", "12")
    changed = git.recent_commits("main")

    assert len(original[0].sha) == 7
    assert len(changed[0].sha) == 12
    log_calls = [call for call in git_subprocess_calls if call[1:2] == ("log",)]
    assert len(log_calls) == 2


def test_shallow_state_change_invalidates_a_cached_log(
    tmp_repo: Path, tmp_path: Path, git_subprocess_calls: list[tuple[str, ...]]
) -> None:
    """A shallow boundary changes graph traversal even when a tip OID does not."""
    _commit(subprocess.run, tmp_repo, "second.txt")
    shallow_repo = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "--depth=1", f"file://{tmp_repo}", str(shallow_repo)],
        check=True,
        capture_output=True,
        text=True,
    )
    shallow_file = shallow_repo / ".git" / "shallow"
    assert shallow_file.exists()
    git = GitRepo(shallow_repo)
    git_subprocess_calls.clear()

    first = git.recent_commits("main")
    state = shallow_file.stat()
    os.utime(shallow_file, ns=(state.st_atime_ns, state.st_mtime_ns + 1))
    second = git.recent_commits("main")

    assert second == first
    log_calls = [call for call in git_subprocess_calls if call[1:2] == ("log",)]
    assert len(log_calls) == 2


def test_comparison_cache_evicts_the_least_recently_used_oid_entry(
    tmp_repo: Path, monkeypatch: pytest.MonkeyPatch, git_subprocess_calls: list[tuple[str, ...]]
) -> None:
    """A bounded cache recomputes an evicted immutable comparison rather than growing."""
    real_run = subprocess.run
    for branch in ("one", "two", "three"):
        _git(real_run, tmp_repo, "checkout", "main")
        _git(real_run, tmp_repo, "checkout", "-b", branch)
        _commit(real_run, tmp_repo, f"{branch}.txt")
    _git(real_run, tmp_repo, "checkout", "main")
    monkeypatch.setattr(GitRepo, "COMPARISON_CACHE_MAX_ENTRIES", 2)
    git = GitRepo(tmp_repo)
    git_subprocess_calls.clear()

    git.recent_commits("one")
    git.recent_commits("two")
    git.recent_commits("one")
    git.recent_commits("three")
    git.recent_commits("two")

    log_calls = [call for call in git_subprocess_calls if call[1:2] == ("log",)]
    assert len(log_calls) == 4


def test_comparison_cache_serves_one_calculation_to_concurrent_readers(
    tmp_repo: Path, git_subprocess_calls: list[tuple[str, ...]]
) -> None:
    """The cache lock prevents concurrent readers from duplicating one immutable log read."""
    real_run = subprocess.run
    _git(real_run, tmp_repo, "checkout", "-b", "feature")
    _commit(real_run, tmp_repo, "feature.txt")
    git = GitRepo(tmp_repo)
    git_subprocess_calls.clear()

    def _read(_: int) -> tuple[CommitSummary, ...]:
        return git.recent_commits("feature")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_read, range(8)))

    assert results and all(result == results[0] for result in results)
    assert [commit.subject for commit in results[0]] == ["add feature.txt", "init"]
    log_calls = [call for call in git_subprocess_calls if call[1:2] == ("log",)]
    assert len(log_calls) == 1


def _git_oid(cwd: Path, ref: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", ref], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()
