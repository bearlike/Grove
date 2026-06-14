"""GET /workspaces/{id}/sessions + .../sessions/{sid}/turns + project-scoped /sessions.

Modeled on test_activity_endpoints.py: TestClient over build_app with a
store-backed fake workspace, transcripts written into a sandboxed
CLAUDE_CONFIG_DIR keyed by the workspace's (nonexistent) worktree path —
transcripts outlive worktrees, so the endpoints must work without one.
The repo-root directory itself does exist (a plain dir, not a git repo):
the project scan shells ``git worktree list`` with the root as cwd, and
``worktree_paths`` falls back to ``[root]`` when that fails.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.agents.claude_code import _ClaudeHome
from grove.core.config import GroveConfig
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus
from grove.daemon import build_app
from tests.daemon.conftest import daemon_test_config

MINTED_SID = "33333333-3333-4333-8333-333333333333"
HAND_SID = "44444444-4444-4444-8444-444444444444"
ROOT_SID = "55555555-5555-4555-8555-555555555555"


def _state(ws_id: str, repo_root: str, *, session_id: str | None = None) -> WorkspaceState:
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
        status=WorkspaceStatus.PAUSED,  # PAUSED → reconcile is trivial, no tmux
        created_at=now,
        updated_at=now,
        agent_session_id=session_id,
    )


def _write_transcript(claude_home: Path, sid: str, cwd: str, *, mtime: int, prompt: str) -> None:
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(Path(cwd))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sid}.jsonl"
    path.write_text(
        '{"type":"mode","mode":"normal"}\n'
        f'{{"type":"user","uuid":"h-{sid[:4]}","timestamp":"2026-06-09T08:00:00.000Z",'
        f'"isSidechain":false,"cwd":"{cwd}","gitBranch":"main",'
        f'"message":{{"role":"user","content":"{prompt}"}}}}\n',
        encoding="utf-8",
    )
    os.utime(path, (mtime, mtime))


@pytest.fixture
def claude_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cfg = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return cfg


@pytest.fixture
def client(tmp_state_dir: Path, claude_home: Path) -> Iterator[TestClient]:
    store = JsonWorkspaceStore()
    repo_root = tmp_state_dir / "repo-a"
    repo_root.mkdir()  # the project scan needs a real cwd for `git worktree list`
    state = _state("a1", str(repo_root), session_id=MINTED_SID)
    store.save(state)
    _write_transcript(claude_home, MINTED_SID, state.worktree_path, mtime=2_000, prompt="minted")
    _write_transcript(claude_home, HAND_SID, state.worktree_path, mtime=3_000, prompt="by hand")
    # Hand-staged at the repo root: no workspace owns that cwd → attribution None.
    _write_transcript(claude_home, ROOT_SID, str(repo_root), mtime=4_000, prompt="root staged")
    app = build_app(cfg=daemon_test_config(), store=store)
    with TestClient(app) as test_client:
        yield test_client


# ─── GET /workspaces/{id}/sessions ──────────────────────────────────────────


def test_sessions_listing_newest_first_with_provenance(client: TestClient) -> None:
    resp = client.get("/workspaces/a1/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert [s["session_id"] for s in body] == [HAND_SID, MINTED_SID]
    by_id = {s["session_id"]: s for s in body}
    assert by_id[MINTED_SID]["provenance"] == "grove_launched"
    assert by_id[HAND_SID]["provenance"] == "fs_discovered"
    assert by_id[MINTED_SID]["first_prompt"] == "minted"
    assert by_id[MINTED_SID]["workspace_id"] == "a1"
    # Host paths never cross the wire.
    assert "transcript_path" not in by_id[MINTED_SID]
    assert "cwd" not in by_id[MINTED_SID]


def test_sessions_limit(client: TestClient) -> None:
    body = client.get("/workspaces/a1/sessions", params={"limit": 1}).json()
    assert [s["session_id"] for s in body] == [HAND_SID]


def test_sessions_unknown_workspace_404(client: TestClient) -> None:
    resp = client.get("/workspaces/nope/sessions")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "workspace_not_found"


# ─── GET /workspaces/{id}/sessions/{sid}/turns ──────────────────────────────


def test_turns_returns_session_detail(client: TestClient) -> None:
    resp = client.get(f"/workspaces/a1/sessions/{MINTED_SID}/turns")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session"]["session_id"] == MINTED_SID
    assert [t["user_text"] for t in body["turns"]] == ["minted"]


def test_turns_unknown_session_404_with_typed_envelope(client: TestClient) -> None:
    resp = client.get("/workspaces/a1/sessions/99999999-9999-4999-8999-999999999999/turns")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "agent_session_not_found"


# ─── GET /sessions (project-scoped) ──────────────────────────────────────────


def test_project_sessions_spans_worktrees_with_attribution(
    client: TestClient, tmp_state_dir: Path
) -> None:
    resp = client.get("/sessions", params={"repo": str(tmp_state_dir / "repo-a")})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [s["session_id"] for s in body] == [ROOT_SID, HAND_SID, MINTED_SID]
    by_id = {s["session_id"]: s for s in body}
    minted = by_id[MINTED_SID]
    assert minted["provenance"] == "grove_launched"
    assert minted["workspace_id"] == "a1"
    assert minted["workspace_title"] == "t-a1"
    assert minted["workspace_branch"] == "b-a1"
    root = by_id[ROOT_SID]
    assert root["provenance"] == "fs_discovered"
    assert root["workspace_id"] is None
    assert root["workspace_title"] is None
    assert root["workspace_branch"] is None
    # Host paths never cross the wire.
    assert "transcript_path" not in minted
    assert "cwd" not in minted


def test_project_sessions_limit(client: TestClient, tmp_state_dir: Path) -> None:
    body = client.get(
        "/sessions", params={"repo": str(tmp_state_dir / "repo-a"), "limit": 1}
    ).json()
    assert [s["session_id"] for s in body] == [ROOT_SID]


def test_project_sessions_limit_bounds_422(client: TestClient, tmp_state_dir: Path) -> None:
    repo = str(tmp_state_dir / "repo-a")
    assert client.get("/sessions", params={"repo": repo, "limit": 0}).status_code == 422
    assert client.get("/sessions", params={"repo": repo, "limit": 201}).status_code == 422


def test_project_sessions_unknown_repo_404(client: TestClient, tmp_state_dir: Path) -> None:
    resp = client.get("/sessions", params={"repo": str(tmp_state_dir / "nope")})
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "unknown_repo_root"


# ─── auth + OpenAPI ─────────────────────────────────────────────────────────


def test_session_routes_require_auth(tmp_state_dir: Path) -> None:
    store = JsonWorkspaceStore()
    store.save(_state("a1", str(tmp_state_dir / "repo-a")))
    app = build_app(cfg=GroveConfig(), store=store)  # auth enabled (default)
    with TestClient(app) as authed:
        assert authed.get("/workspaces/a1/sessions").status_code == 401
        assert authed.get(f"/workspaces/a1/sessions/{MINTED_SID}/turns").status_code == 401
        assert authed.get("/sessions", params={"repo": "/x"}).status_code == 401


def test_openapi_documents_session_routes_and_views(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    assert "/workspaces/{ws_id}/sessions" in spec["paths"]
    assert "/workspaces/{ws_id}/sessions/{session_id}/turns" in spec["paths"]
    assert "/sessions" in spec["paths"]
    schemas = spec["components"]["schemas"]
    # Both must appear so the webapp codegen picks the wire types up.
    assert "SessionSummaryView" in schemas
    assert "SessionDetailView" in schemas
    # The attribution trio rides the existing view (additive, nullable).
    props = schemas["SessionSummaryView"]["properties"]
    assert "workspace_title" in props
    assert "workspace_branch" in props
