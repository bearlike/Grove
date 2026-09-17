"""`GitRepo.is_clean`/`dirty_file_count` must not refresh the shared index."""

from __future__ import annotations

import os
import subprocess
from typing import Any

import pytest

from grove.core.git import GitRepo


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


def test_status_reads_pass_no_optional_locks(
    tmp_repo, git_subprocess_calls: list[tuple[str, ...]]
) -> None:
    """Both status-backed reads pass the flag so a background observation
    never contends with a foreground `git add`/`commit` for the index lock."""
    git = GitRepo(tmp_repo)
    git_subprocess_calls.clear()

    assert git.is_clean(tmp_repo) is True
    assert git.dirty_file_count(tmp_repo) == 0

    status_calls = [call for call in git_subprocess_calls if "status" in call]
    assert status_calls == [
        ("git", "--no-optional-locks", "status", "--porcelain"),
        ("git", "--no-optional-locks", "status", "--porcelain"),
    ]


def test_status_read_does_not_touch_the_index_mtime(tmp_repo) -> None:
    """A touched tracked file forces a real status re-read; the index file
    itself must not be rewritten by that read (the whole point of the flag)."""
    tracked = tmp_repo / "README.md"
    index = tmp_repo / ".git" / "index"
    before = index.stat().st_mtime_ns
    os.utime(tracked, None)  # bump mtime without changing content

    git = GitRepo(tmp_repo)
    assert git.is_clean(tmp_repo) is True
    assert git.dirty_file_count(tmp_repo) == 0

    assert index.stat().st_mtime_ns == before


def test_dirty_file_count_still_reports_real_changes(tmp_repo) -> None:
    """The flag must not change status OUTPUT, only whether it may write."""
    git = GitRepo(tmp_repo)
    assert git.dirty_file_count(tmp_repo) == 0

    (tmp_repo / "untracked.txt").write_text("x\n", encoding="utf-8")

    assert git.dirty_file_count(tmp_repo) == 1
    assert git.is_clean(tmp_repo) is False
