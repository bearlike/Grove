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
from grove.core.agents.hook import ClaudeHook
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config


@pytest.fixture
def daemon(
    tmp_state_dir: Path,
    tmp_repo: Path,
    fake_tmux: FakeTmux,
) -> Iterator[TestClient]:
    store = JsonWorkspaceStore()
    app = build_app(cfg=daemon_test_config(), store=store)
    with TestClient(app) as client:
        yield client


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

    resp = daemon.get(f"/workspaces/{ws_id}/fleet")

    assert resp.status_code == 200
    assert resp.json() == {"subagents": []}


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
    assert resp.json() == {"subagents": []}


def test_get_fleet_unknown_workspace_is_404(daemon: TestClient) -> None:
    resp = daemon.get("/workspaces/nope/fleet")

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "workspace_not_found"
