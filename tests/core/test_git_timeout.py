"""Every git subprocess is bounded, and a timeout reports like a failure.

Driven through a REAL hanging child rather than a patched `subprocess.run`: a
fake `git` earlier on `PATH` sleeps past the bound, so the test exercises the
same `subprocess` call production makes and would catch a `timeout=` that is
passed but never enforced. Both arms are pinned, because they answer
differently on purpose — `check=True` raises `GitError`, `check=False` returns a
failed `CompletedProcess` so the peek/dashboard reads keep their "never raises"
contract.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from grove.core.errors import GitError
from grove.core.git import GitRepo

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the stub git relies on a POSIX shebang"
)

_SLEEP_SECONDS = 30
_BOUND_SECONDS = 0.5


@pytest.fixture
def hanging_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put a `git` that never answers in time at the front of `PATH`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "git"
    stub.write_text(
        f"#!{sys.executable}\nimport time\ntime.sleep({_SLEEP_SECONDS})\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(GitRepo, "TIMEOUT_SECONDS", _BOUND_SECONDS)
    root = tmp_path / "repo"
    root.mkdir()
    return root


def test_a_hanging_git_raises_giterror_instead_of_blocking_forever(
    hanging_git: Path,
) -> None:
    with pytest.raises(GitError, match="timed out"):
        GitRepo(hanging_git).branch_delete("feature/x")


def test_a_hanging_best_effort_read_degrades_instead_of_raising(
    hanging_git: Path,
) -> None:
    # `is_clean` gates `pause`, and fails CLOSED: "could not inspect" is not
    # "clean", so a timeout must never read as permission to remove a worktree.
    assert GitRepo(hanging_git).is_clean(hanging_git) is False


def test_detect_root_answers_none_rather_than_hanging(hanging_git: Path) -> None:
    assert GitRepo.detect_root(hanging_git) is None
