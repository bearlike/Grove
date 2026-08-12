"""WorkspaceManager.working_diff — the bounding, and why the cut point matters.

The route-level behaviour lives in `tests/daemon/test_workspaces_diff.py`; this
covers the two things only the engine seam can show — that truncation produces a
still-parseable patch, and that splitting git's output loses no bytes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager, _split_file_patches
from grove.core.store import JsonWorkspaceStore
from tests.conftest import FakeTmux


@pytest.fixture
def manager(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> WorkspaceManager:
    del fake_tmux  # used via monkeypatch
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-", "agent_window_name": "agent"},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


def _workspace(manager: WorkspaceManager, tmp_repo: Path) -> tuple[str, Path]:
    state = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="diff", repo_root=str(tmp_repo))
    )
    return state.id, Path(state.worktree_path)


# ─── the split is lossless, which is what makes truncation a prefix ──────────


@pytest.mark.parametrize(
    "patch",
    [
        "",
        "diff --git a/x b/x\n@@ -1 +1 @@\n-a\n+b\n",
        # Two files: the newline BETWEEN them is the byte a naive `split()` eats.
        "diff --git a/x b/x\n@@ -1 +1 @@\n-a\n+b\ndiff --git a/y b/y\n@@ -1 +1 @@\n-c\n+d\n",
        # A body line that merely CONTAINS the header text must not split — the
        # match is anchored to the start of a line.
        "diff --git a/x b/x\n@@ -1 +1 @@\n+see diff --git a/fake b/fake\n",
    ],
)
def test_splitting_a_patch_loses_no_bytes(patch: str) -> None:
    assert "".join(_split_file_patches(patch)) == patch


def test_split_counts_one_chunk_per_file() -> None:
    two = "diff --git a/x b/x\n@@ -1 +1 @@\n-a\n+b\ndiff --git a/y b/y\n@@ -1 +1 @@\n-c\n+d\n"
    chunks = _split_file_patches(two)
    assert len(chunks) == 2
    assert all(c.startswith("diff --git ") for c in chunks)


def test_a_body_line_containing_the_header_does_not_split_the_patch() -> None:
    """Anchoring to line-start is the difference between one file and two."""
    patch = "diff --git a/x b/x\n@@ -1 +1 @@\n+see diff --git a/fake b/fake\n"
    assert len(_split_file_patches(patch)) == 1


# ─── truncation ──────────────────────────────────────────────────────────────


def test_truncation_cuts_at_a_file_boundary_and_says_so(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    """A patch cut mid-hunk would break the renderer rather than shorten it, so
    the cap drops whole files and reports how many survived."""
    ws_id, worktree = _workspace(manager, tmp_repo)
    for name in ("a.txt", "b.txt", "c.txt"):
        (worktree / name).write_text("x\n" * 50, encoding="utf-8")

    whole = manager.working_diff(ws_id)
    assert whole.files == 3 and whole.truncated is False

    cut = manager.working_diff(ws_id, max_bytes=len(whole.patch) // 2)
    assert cut.truncated is True
    assert 0 < cut.files < 3
    # Still a valid patch: every file header intact, and a strict prefix of the
    # whole one rather than a rewrite of it.
    assert cut.patch.startswith("diff --git ")
    assert whole.patch.startswith(cut.patch)
    assert cut.patch.count("diff --git ") == cut.files


def test_one_oversized_file_is_kept_whole_rather_than_corrupted(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    """Cutting the only file would return an unparseable fragment; the client's
    remedy for a huge single file is `path=`, not a broken body."""
    ws_id, worktree = _workspace(manager, tmp_repo)
    (worktree / "big.txt").write_text("y\n" * 500, encoding="utf-8")

    diff = manager.working_diff(ws_id, max_bytes=10)
    assert diff.files == 1
    assert diff.truncated is False
    assert diff.patch.startswith("diff --git ")


def test_working_diff_scope_matches_dirty_files_before_any_commit(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    """Until something is committed the two agree, because the creation anchor
    and HEAD are the same commit — staged, unstaged and untracked alike."""
    ws_id, worktree = _workspace(manager, tmp_repo)
    (worktree / "tracked.txt").write_text("one\n", encoding="utf-8")
    (worktree / "untracked.txt").write_text("new\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(worktree), "add", "tracked.txt"], check=True, capture_output=True
    )

    assert manager.working_diff(ws_id).files == manager.peek(ws_id).dirty_files == 2


def test_committing_keeps_a_file_in_the_patch_and_drops_it_from_dirty_files(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    """The two axes DIVERGE at the first commit, and that divergence is the point.

    The patch answers "what has this workspace changed" and must keep a
    committed file; `dirty_files` answers "what is uncommitted right now" and
    must drop it. Anchoring the patch on HEAD made it agree with the counter by
    going blank at exactly the moment a reviewer wants to read it.
    """
    ws_id, worktree = _workspace(manager, tmp_repo)
    (worktree / "committed.txt").write_text("done\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(worktree), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(worktree), "commit", "-m", "seed", "--no-gpg-sign"],
        check=True,
        capture_output=True,
    )

    diff = manager.working_diff(ws_id)
    assert diff.files == 1
    assert "committed.txt" in diff.patch
    assert "+done" in diff.patch
    assert manager.peek(ws_id).dirty_files == 0
