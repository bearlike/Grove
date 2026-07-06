"""HTTP surface for manual session remap + resume-into-workspace (#120).

Two routes exercised against a real git repo + FakeTmux:
  * ``POST /workspaces`` with ``resume_session_id`` — the launch adopts the id
    (claude) or rejects the kind (shell → 422 ``resume_not_supported``).
  * ``POST /workspaces/{id}/session`` — pin an existing session as primary,
    returning the updated ``WorkspaceStateView``; error envelope for the
    unknown-workspace / unknown-ref / missing-body cases.

``agent_session_id`` is deliberately NOT on ``WorkspaceStateView`` (the
views-never-expose rule), so the pinning is asserted through the store; over the
wire the new primary surfaces on the activity/sessions streams instead.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.agents.claude_code import _ClaudeHome
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config


@pytest.fixture
def daemon(
    tmp_state_dir: Path,
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, Path, Path, JsonWorkspaceStore]]:
    """Daemon wired against a real git repo + the in-memory FakeTmux, with a
    sandboxed CLAUDE_CONFIG_DIR so discovered transcripts land under tmp.

    Yields ``(client, repo_root, claude_config_dir, store)``.
    """
    del fake_tmux
    cfg_home = tmp_state_dir / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_state_dir)
    store = JsonWorkspaceStore()
    app = build_app(cfg=daemon_test_config(), store=store)
    with TestClient(app) as client:
        yield client, tmp_repo, cfg_home, store


Daemon = tuple[TestClient, Path, Path, JsonWorkspaceStore]


def _create_claude(daemon: Daemon, **extra: object) -> dict:
    client, repo, _, _ = daemon
    payload = {
        "agent_name": "claude",
        "title": "host",
        "repo_root": str(repo),
        "branch_plan": {"kind": "auto"},
        **extra,
    }
    resp = client.post("/workspaces", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ─── resume-into-workspace on POST /workspaces ───────────────────────────────


def test_create_with_resume_session_id_pins_it(daemon: Daemon) -> None:
    """A claude create with resume_session_id adopts that id as the persisted
    agent_session_id (no fresh mint). Verified through the store — the wire view
    deliberately omits the session id."""
    _, repo, cfg_home, store = daemon
    resume = "12345678-1111-2222-3333-444455556666"
    # #F8: the resume ref must resolve to a real session in the project BEFORE any
    # side effect — materialize one at the repo root (the only scan root before
    # the workspace exists).
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(repo)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{resume}.jsonl").write_text(f'{{"type":"user","cwd":"{repo}"}}\n', encoding="utf-8")
    body = _create_claude(daemon, resume_session_id=resume)
    assert store.get(body["id"]).agent_session_id == resume


def test_create_resume_for_shell_agent_422s(daemon: Daemon) -> None:
    """A generic/shell agent has no resume handle → 422 resume_not_supported."""
    client, repo, _, _ = daemon
    resp = client.post(
        "/workspaces",
        json={
            "agent_name": "shell",
            "title": "nope",
            "repo_root": str(repo),
            "resume_session_id": "x-y-z",
        },
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == "resume_not_supported"


# ─── POST /workspaces/{id}/session (remap) ───────────────────────────────────


def test_remap_pins_a_discovered_session(daemon: Daemon) -> None:
    client, _, cfg_home, store = daemon
    ws = _create_claude(daemon)
    ws_id = ws["id"]
    # Materialize a hand-started transcript in the workspace's real worktree cwd.
    worktree = Path(ws["worktree_path"])
    hand = "cafef00d-9999-8888-7777-666655554444"
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{hand}.jsonl").write_text(
        f'{{"type":"user","cwd":"{worktree}"}}\n', encoding="utf-8"
    )

    resp = client.post(f"/workspaces/{ws_id}/session", json={"session_ref": hand})
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == ws_id  # updated workspace view
    assert store.get(ws_id).agent_session_id == hand


def test_remap_unknown_workspace_404s(daemon: Daemon) -> None:
    client, _, _, _ = daemon
    resp = client.post("/workspaces/nope/session", json={"session_ref": "abc"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "workspace_not_found"


def test_remap_unknown_session_ref_404s(daemon: Daemon) -> None:
    client, _, _, _ = daemon
    ws = _create_claude(daemon)
    resp = client.post(f"/workspaces/{ws['id']}/session", json={"session_ref": "does-not-exist"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "agent_session_not_found"


def test_remap_missing_session_ref_422s(daemon: Daemon) -> None:
    client, _, _, _ = daemon
    ws = _create_claude(daemon)
    resp = client.post(f"/workspaces/{ws['id']}/session", json={})
    assert resp.status_code == 422  # wire model rejects the empty body
