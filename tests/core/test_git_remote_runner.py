"""A GitRepo bound to a runner reads a worktree that is not on this host."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core.errors import GitError
from grove.core.git import GitRepo
from grove.core.native_owners import WorktreeRead
from grove.core.phase import PhaseFile

_GONE = Path("/nonexistent/remote/worktree")


def test_every_instance_read_goes_to_the_runner_even_with_no_local_directory() -> None:
    asked: list[list[str]] = []

    def worker(argv: list[str], timeout: float) -> WorktreeRead:
        asked.append(argv)
        return WorktreeRead(0, " M a.py\n?? b.py\n")

    git = GitRepo(_GONE, runner=worker)
    assert git.dirty_file_count(_GONE) == 2
    assert asked == [["git", "--no-optional-locks", "status", "--porcelain"]]


def test_a_failed_remote_read_raises_where_the_caller_asked_to_check() -> None:
    def worker(argv: list[str], timeout: float) -> WorktreeRead:
        return WorktreeRead(128, "fatal: not a git repository")

    with pytest.raises(GitError, match="remotely"):
        GitRepo(_GONE, runner=worker)._exec(["git", "rev-parse", "HEAD"])


def test_a_runner_uses_its_instance_deadline() -> None:
    """A projection can bound a remote read without shortening lifecycle git work."""
    received: list[float] = []

    def worker(argv: list[str], timeout: float) -> WorktreeRead:
        del argv
        received.append(timeout)
        return WorktreeRead(0, "main\n")

    assert GitRepo(_GONE, runner=worker, runner_timeout=0.25).current_branch() == "main"
    assert received == [0.25]


def test_a_remote_phase_document_parses_with_the_same_tolerance() -> None:
    stamp = datetime(2026, 10, 22, tzinfo=UTC)
    report = PhaseFile.from_text('{"phase": "build", "note": "x"}', stamp)
    assert report is not None and report.phase == "build" and report.updated_at == stamp
    assert PhaseFile.from_text("not json", stamp) is None
