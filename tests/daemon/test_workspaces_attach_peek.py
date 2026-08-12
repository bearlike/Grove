"""GET /workspaces/{id}/attach, /peek and /commits."""

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
def ws_id(daemon: TestClient, tmp_repo: Path) -> str:
    body = daemon.post(
        "/workspaces",
        json={
            "agent_name": "claude",
            "title": "attach-peek-test",
            "repo_root": str(tmp_repo),
            "branch_plan": {"kind": "auto"},
        },
    ).json()
    return body["id"]


def test_attach_returns_instruction(daemon: TestClient, ws_id: str) -> None:
    resp = daemon.get(f"/workspaces/{ws_id}/attach")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "tmux_session" in body
    assert "inside_outer_tmux" in body


def test_peek_returns_view(daemon: TestClient, ws_id: str) -> None:
    resp = daemon.get(f"/workspaces/{ws_id}/peek")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in (
        "state",
        "base_ahead",
        "base_behind",
        "diff_added",
        "diff_removed",
        "dirty_files",
        "recent_commits",
    ):
        assert key in body, f"missing {key} in WorkspacePeekView"
    assert body["state"]["id"] == ws_id


# ─── GET /workspaces/{id}/commits ─────────────────────────────────────────────


def _commit(worktree: str, n: int) -> None:
    """One real commit on the workspace branch — `git log base..branch` needs them."""
    (Path(worktree) / f"f{n}.txt").write_text(str(n), encoding="utf-8")
    subprocess.run(["git", "-C", worktree, "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", worktree, "commit", "-m", f"c{n}", "--no-gpg-sign"],
        check=True,
        capture_output=True,
    )


def test_commits_default_is_uncapped_and_limit_takes_a_page(daemon: TestClient, ws_id: str) -> None:
    """`limit` is opt-in: the bare array has nowhere to admit a truncation, so
    capping by default would silently shorten the log for every shipped client."""
    worktree = daemon.get(f"/workspaces/{ws_id}").json()["worktree_path"]
    for n in range(5):
        _commit(worktree, n)

    everything = daemon.get(f"/workspaces/{ws_id}/commits").json()
    assert len(everything) == 5

    page = daemon.get(f"/workspaces/{ws_id}/commits", params={"limit": 2}).json()
    assert len(page) == 2
    # Newest-first, so a page is the head of the same order, not a random slice.
    assert [c["sha"] for c in page] == [c["sha"] for c in everything[:2]]


def test_commits_limit_is_bounded(daemon: TestClient, ws_id: str) -> None:
    assert daemon.get(f"/workspaces/{ws_id}/commits", params={"limit": 0}).status_code == 422
    assert daemon.get(f"/workspaces/{ws_id}/commits", params={"limit": 1001}).status_code == 422
