"""``GET /workspaces/{id}/fleet``: the full sub-agent roster, sourced from the
Claude Code hook's per-``(session_id, agent_id)`` sidecar rather than a
transcript parse. The ``/todo``/``/queue`` sibling in shape and cost, but an
empty roster (not 404) for a workspace with no sub-agents — "no sub-agents"
is a real, common answer.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core import paths as core_paths
from grove.core.agents.claude_code import _ClaudeHome
from grove.core.agents.hook import ClaudeHook
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config


@pytest.fixture
def claude_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cfg = tmp_path / "claude-home"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    return cfg


@pytest.fixture
def daemon(
    tmp_state_dir: Path,
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    claude_home: Path,
) -> Iterator[TestClient]:
    store = JsonWorkspaceStore()
    app = build_app(cfg=daemon_test_config(), store=store)
    with TestClient(app) as client:
        yield client


def _write_fleet_child_transcript(
    cfg_home: Path, parent_sid: str, cwd: str, thread_id: str, *, prompt: str
) -> None:
    """A fleet child's sidechain transcript at the real on-host layout, nested
    under its parent top-level session — the same shape
    ``tests/daemon/test_sessions_endpoints.py::_write_fleet_child`` writes."""
    sub_dir = cfg_home / "projects" / _ClaudeHome.encode_cwd(Path(cwd)) / parent_sid / "subagents"
    sub_dir.mkdir(parents=True, exist_ok=True)
    (sub_dir / f"agent-{thread_id}.jsonl").write_text(
        f'{{"type":"user","uuid":"su1","isSidechain":true,"agentId":"{thread_id}",'
        f'"timestamp":"2026-06-09T08:00:01.000Z","message":{{"role":"user","content":"{prompt}"}}}}\n'
        f'{{"type":"assistant","uuid":"sa1","isSidechain":true,"agentId":"{thread_id}",'
        '"timestamp":"2026-06-09T08:00:02.000Z","message":{"id":"sm1","role":"assistant",'
        '"stop_reason":"end_turn","content":[{"type":"text","text":"Done."}]}}\n',
        encoding="utf-8",
    )


def _create_ws(daemon: TestClient, tmp_repo: Path, *, agent_name: str = "claude") -> str:
    body = daemon.post(
        "/workspaces",
        json={
            "agent_name": agent_name,
            "title": "fleet test",
            "repo_root": str(tmp_repo),
            "branch_plan": {"kind": "auto"},
        },
    ).json()
    return str(body["id"])


def _session_id(ws_id: str) -> str:
    """``WorkspaceStateView`` never exposes ``agent_session_id`` (views never
    expose transcript-correlation ids), so the test reads it off the SAME
    on-disk store the daemon's `JsonWorkspaceStore()` resolves to under the
    `tmp_state_dir` redirection — a second instance with no explicit path
    resolves the identical file."""
    session_id = JsonWorkspaceStore().get(ws_id).agent_session_id
    assert isinstance(session_id, str) and session_id
    return session_id


def test_get_fleet_returns_an_empty_roster_with_no_subagents(
    daemon: TestClient, tmp_repo: Path
) -> None:
    ws_id = _create_ws(daemon, tmp_repo)
    session_id = _session_id(ws_id)

    resp = daemon.get(f"/workspaces/{ws_id}/fleet")

    assert resp.status_code == 200
    body = resp.json()
    assert body["subagents"] == []
    assert body["sessions"] == []
    assert body["session_id"] == session_id
    assert body["supported"] is True
    assert body["error"] is None


def test_get_fleet_returns_pushed_subagents(daemon: TestClient, tmp_repo: Path) -> None:
    ws_id = _create_ws(daemon, tmp_repo)
    session_id = _session_id(ws_id)
    now = datetime.now(tz=UTC)
    ClaudeHook.record_event(
        {
            "hook_event_name": "SubagentStart",
            "session_id": session_id,
            "agent_id": "a-1",
            "agent_type": "general-purpose",
        },
        sidecar_dir=core_paths.agent_sidecar_dir(),
        tmux_pane=None,
        now=now,
    )

    resp = daemon.get(f"/workspaces/{ws_id}/fleet")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["subagents"]) == 1
    row = body["subagents"][0]
    assert row["agent_id"] == "a-1"
    assert row["agent_type"] == "general-purpose"
    assert row["state"] == "working"
    assert row["current_tool"] is None
    # No transcript exists for this hook-only child yet, so it does not also
    # appear (or duplicate) in the provider-neutral projection.
    assert body["sessions"] == []
    assert body["supported"] is True


def test_get_fleet_non_claude_kind_is_an_empty_roster_not_a_404(
    daemon: TestClient, tmp_repo: Path
) -> None:
    """The ``shell`` builtin agent mints no session id at all and has no
    hook mechanism — unlike ``/todo``, that answers an empty roster rather
    than 404: "no sub-agents" is the honest answer for every non-claude_code
    workspace, not a missing-session refusal."""
    ws_id = _create_ws(daemon, tmp_repo, agent_name="shell")

    resp = daemon.get(f"/workspaces/{ws_id}/fleet")

    assert resp.status_code == 200
    body = resp.json()
    assert body["subagents"] == []
    assert body["sessions"] == []
    assert body["supported"] is False


def test_get_fleet_unknown_workspace_is_404(daemon: TestClient) -> None:
    resp = daemon.get("/workspaces/nope/fleet")

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "workspace_not_found"


def test_get_fleet_reconciles_hook_roster_against_transcript_children(
    daemon: TestClient, tmp_repo: Path, claude_home: Path
) -> None:
    """A child with BOTH a live hook record and a materialized transcript
    appears exactly once, in ``sessions`` — the hook roster is filtered to
    entries the transcript projection has not yet surfaced, never unioned
    blindly (which would duplicate the same child under two shapes)."""
    ws_id = _create_ws(daemon, tmp_repo)
    session_id = _session_id(ws_id)
    worktree = JsonWorkspaceStore().get(ws_id).worktree_path
    now = datetime.now(tz=UTC)

    # A hook-only child (no transcript materialized yet) — appears in
    # `subagents` and nowhere in `sessions`.
    ClaudeHook.record_event(
        {
            "hook_event_name": "SubagentStart",
            "session_id": session_id,
            "agent_id": "hook-only",
            "agent_type": "general-purpose",
        },
        sidecar_dir=core_paths.agent_sidecar_dir(),
        tmux_pane=None,
        now=now,
    )

    # A child with BOTH a hook record and a transcript — must appear once,
    # under `sessions`, not duplicated into `subagents`.
    ClaudeHook.record_event(
        {
            "hook_event_name": "SubagentStart",
            "session_id": session_id,
            "agent_id": "both",
            "agent_type": "general-purpose",
        },
        sidecar_dir=core_paths.agent_sidecar_dir(),
        tmux_pane=None,
        now=now,
    )
    _write_fleet_child_transcript(
        claude_home, session_id, worktree, "both", prompt="Explore the auth flow"
    )

    resp = daemon.get(f"/workspaces/{ws_id}/fleet")

    assert resp.status_code == 200
    body = resp.json()
    assert body["supported"] is True
    hook_ids = {row["agent_id"] for row in body["subagents"]}
    session_ids = {row["session"]["session_id"] for row in body["sessions"]}
    assert hook_ids == {"hook-only"}
    assert session_ids == {"both"}
    assert not (hook_ids & session_ids)


def test_get_fleet_selects_an_explicit_root_session(
    daemon: TestClient, tmp_repo: Path, claude_home: Path
) -> None:
    """``session_id`` selects a non-primary root among the workspace's own
    sessions — never a foreign workspace's — and the response echoes it back
    resolved rather than the primary's."""
    ws_id = _create_ws(daemon, tmp_repo)
    session_id = _session_id(ws_id)

    resp = daemon.get(f"/workspaces/{ws_id}/fleet", params={"session_id": session_id})

    assert resp.status_code == 200
    assert resp.json()["session_id"] == session_id


def test_get_fleet_unknown_session_id_is_unsupported_not_404(
    daemon: TestClient, tmp_repo: Path, claude_home: Path
) -> None:
    """A ``session_id`` that does not belong to this workspace's own scan
    degrades to an honest unsupported root rather than a 500 or a foreign
    read — the fleet route never resolves a session outside this workspace's
    own tree."""
    ws_id = _create_ws(daemon, tmp_repo)

    resp = daemon.get(f"/workspaces/{ws_id}/fleet", params={"session_id": "not-a-real-session"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["supported"] is False
    assert body["sessions"] == []
    assert body["session_id"] == "not-a-real-session"
