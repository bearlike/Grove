"""GET /workspaces aggregates across all known repos in the store."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus
from grove.daemon import build_app
from tests.daemon.conftest import daemon_test_config


def _state(ws_id: str, repo_root: str) -> WorkspaceState:
    now = datetime.now(tz=UTC)
    return WorkspaceState(
        id=ws_id,
        title=f"t-{ws_id}",
        repo_root=repo_root,
        branch=f"b-{ws_id}",
        base_branch="main",
        worktree_path=f"{repo_root}/.grove/worktrees/{ws_id}",
        tmux_session=f"grove-{ws_id}",
        agent_name="claude",
        status=WorkspaceStatus.PAUSED,  # PAUSED so reconcile is trivial
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def daemon_with_states(
    tmp_state_dir: Path,
) -> Iterator[tuple[TestClient, JsonWorkspaceStore]]:
    store = JsonWorkspaceStore()
    store.save(_state("a1", str(tmp_state_dir / "repo-a")))
    store.save(_state("b1", str(tmp_state_dir / "repo-b")))
    app = build_app(cfg=daemon_test_config(), store=store)
    with TestClient(app) as client:
        yield client, store


def test_list_returns_workspaces_from_all_repos(
    daemon_with_states: tuple[TestClient, JsonWorkspaceStore],
) -> None:
    client, _ = daemon_with_states
    resp = client.get("/workspaces")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    ids = sorted(item["id"] for item in body)
    assert ids == ["a1", "b1"]


def test_list_returns_workspace_state_view_shape(
    daemon_with_states: tuple[TestClient, JsonWorkspaceStore],
) -> None:
    client, _ = daemon_with_states
    body = client.get("/workspaces").json()
    one = body[0]
    for key in (
        "id",
        "title",
        "repo_root",
        "branch",
        "base_branch",
        "worktree_path",
        "tmux_session",
        "agent_name",
        "status",
        "created_at",
        "updated_at",
        "branch_provenance",
    ):
        assert key in one, f"missing {key} in WorkspaceStateView wire shape"
    # internal-only fields are not on the wire
    assert "init_log_path" not in one
    assert "init_env" not in one
