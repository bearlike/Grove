"""GET /workspaces/{id}/diff — the working-tree patch, straight from git.

Driven against a REAL repo with REAL edits rather than a stubbed git, because
every property that matters here is a property of git's own output: that an
untracked file appears at all, that a binary file reports itself instead of
being diffed, and that a truncated patch is still parseable. A scripted double
would answer whatever shape the test author imagined.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config


@pytest.fixture
def daemon(tmp_state_dir: Path, tmp_repo: Path, fake_tmux: FakeTmux) -> Iterator[TestClient]:
    store = JsonWorkspaceStore()
    app = build_app(cfg=daemon_test_config(), store=store)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def workspace(daemon: TestClient, tmp_repo: Path) -> tuple[str, Path]:
    body = daemon.post(
        "/workspaces",
        json={
            "agent_name": "claude",
            "title": "diff-test",
            "repo_root": str(tmp_repo),
            "branch_plan": {"kind": "auto"},
        },
    ).json()
    return body["id"], Path(body["worktree_path"])


def _git(worktree: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(worktree), *args], check=True, capture_output=True)


def test_clean_worktree_is_available_with_an_empty_patch(
    daemon: TestClient, workspace: tuple[str, Path]
) -> None:
    """The distinction the whole route turns on: nothing changed is NOT the
    same answer as git could not tell you."""
    ws_id, _ = workspace
    body = daemon.get(f"/workspaces/{ws_id}/diff").json()
    assert body["available"] is True
    assert body["reason"] is None
    assert body["patch"] == ""
    assert body["files"] == 0


def test_modified_and_untracked_files_both_reach_the_patch(
    daemon: TestClient, workspace: tuple[str, Path]
) -> None:
    """A plain `git diff` omits a new file entirely, and creating files is an
    agent's usual first act — so an untracked-blind diff is blank for exactly
    the work a reviewer wants to see.

    Committing does not remove a file from the answer either: the patch is
    measured against the workspace's creation anchor, so `tracked.txt` arrives
    as the new file it is *relative to when this workspace started*, carrying
    its final content rather than only the edit made since the commit.
    """
    ws_id, worktree = workspace
    (worktree / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "seed", "--no-gpg-sign")
    (worktree / "tracked.txt").write_text("two\n", encoding="utf-8")
    (worktree / "brand-new.txt").write_text("hello\n", encoding="utf-8")

    body = daemon.get(f"/workspaces/{ws_id}/diff").json()
    assert body["available"] is True
    assert body["files"] == 2
    assert "tracked.txt" in body["patch"]
    assert "brand-new.txt" in body["patch"]
    assert "+hello" in body["patch"]
    assert "+two" in body["patch"]


def test_the_patch_is_raw_git_output(daemon: TestClient, workspace: tuple[str, Path]) -> None:
    """Unified format, verbatim — the client parses it, the daemon does not."""
    ws_id, worktree = workspace
    (worktree / "a.txt").write_text("x\n", encoding="utf-8")

    patch = daemon.get(f"/workspaces/{ws_id}/diff").json()["patch"]
    assert patch.startswith("diff --git ")
    assert "--- /dev/null" in patch
    assert "@@" in patch


def test_untracked_diff_never_stages_anything(
    daemon: TestClient, workspace: tuple[str, Path]
) -> None:
    """Reading a diff must not mutate the user's index — the reason untracked
    files go through `--no-index` rather than `git add --intent-to-add`."""
    ws_id, worktree = workspace
    (worktree / "new.txt").write_text("x\n", encoding="utf-8")

    daemon.get(f"/workspaces/{ws_id}/diff")

    staged = subprocess.run(
        ["git", "-C", str(worktree), "diff", "--cached", "--name-only"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert staged.stdout == "", "reading the diff staged something"


def test_path_selects_one_file(daemon: TestClient, workspace: tuple[str, Path]) -> None:
    """The per-file form is the escape hatch for a big tree — it is what lets a
    client read one file's hunks instead of raising the cap."""
    ws_id, worktree = workspace
    (worktree / "a.txt").write_text("a\n", encoding="utf-8")
    (worktree / "b.txt").write_text("b\n", encoding="utf-8")

    body = daemon.get(f"/workspaces/{ws_id}/diff", params={"path": "b.txt"}).json()
    assert body["files"] == 1
    assert "b.txt" in body["patch"]
    assert "a.txt" not in body["patch"]


def test_ignored_files_stay_out_of_the_patch(
    daemon: TestClient, workspace: tuple[str, Path]
) -> None:
    """Untracked enumeration honours the repo's ignore rules, so build output
    never lands in a review."""
    ws_id, worktree = workspace
    (worktree / ".gitignore").write_text("junk/\n", encoding="utf-8")
    (worktree / "junk").mkdir()
    (worktree / "junk" / "out.bin").write_text("noise\n", encoding="utf-8")

    patch = daemon.get(f"/workspaces/{ws_id}/diff").json()["patch"]
    assert "junk/out.bin" not in patch
    assert ".gitignore" in patch


def test_a_binary_file_reports_itself_instead_of_being_diffed(
    daemon: TestClient, workspace: tuple[str, Path]
) -> None:
    """git already says this; the requirement is that it SURVIVES rather than
    being filtered out on the way to the client."""
    ws_id, worktree = workspace
    (worktree / "blob.bin").write_bytes(b"\x00\x01\x02\xff\xfe")

    patch = daemon.get(f"/workspaces/{ws_id}/diff").json()["patch"]
    assert "blob.bin" in patch
    assert "Binary files" in patch


def test_a_missing_worktree_is_unavailable_not_empty(
    daemon: TestClient, workspace: tuple[str, Path]
) -> None:
    """A paused workspace's worktree is gone. Reporting that as "no changes"
    would send a reader hunting for work that is simply unreadable."""
    ws_id, _ = workspace
    daemon.post(f"/workspaces/{ws_id}/pause", json={"force": True})

    body = daemon.get(f"/workspaces/{ws_id}/diff").json()
    assert body["available"] is False
    assert body["reason"] == "worktree_missing"
    assert body["patch"] == ""


def test_unknown_workspace_still_404s(daemon: TestClient) -> None:
    assert daemon.get("/workspaces/nope/diff").status_code == 404
