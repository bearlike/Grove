"""``GroveClient.get_activity`` — the fleet-status read's wire contract.

Same seam and discipline as ``test_tickets.py``: an ``httpx.MockTransport``
injected into a real client, asserting the request it issues and that the reply
deserializes into the contract View. No daemon is stood up — the route
(``GET /activity``) long predates this method; what was missing, and what these
tests pin, is the client SDK wrapper that lets the MCP tier reach it at all.
"""

from __future__ import annotations

import httpx

from grove.client import BackendConfig, GroveClient
from grove.core.contracts.activity import DashboardSnapshotView

_ACTIVITY_BODY: dict[str, object] = {
    "generated_at": "2026-07-31T00:00:00Z",
    "total_workspaces": 1,
    "needs_attention": 1,
    "projects": [
        {
            "repo_root": "/repo/acme",
            "repo_name": "acme",
            "cwd": "/repo/acme",
            "workspaces": [
                {
                    "state": {
                        "id": "ws-1",
                        "title": "Fix login",
                        "repo_root": "/repo/acme",
                        "branch": "grove/fix-login",
                        "base_branch": "main",
                        "worktree_path": "/repo/acme/.worktrees/fix-login",
                        "tmux_session": "grove-fix-login",
                        "agent_name": "claude",
                        "status": "running",
                        "created_at": "2026-07-31T00:00:00Z",
                        "updated_at": "2026-07-31T00:00:00Z",
                    },
                    "sessions": [
                        {
                            "session": {
                                "session_id": "sess-1",
                                "adapter_kind": "claude_code",
                                "provenance": "grove_launched",
                                "tmux_window": "agent",
                            },
                            "activity": {
                                "state": "waiting",
                                "title": None,
                                "current_task": None,
                                "human_turns": 1,
                                "assistant_replies": 1,
                                "replies_per_turn": [1],
                                "tool_calls": 0,
                                "model": None,
                                "tokens_in": 0,
                                "tokens_out": 0,
                                "last_event_at": None,
                                "needs_attention": True,
                                "error_detail": None,
                            },
                        }
                    ],
                    "base_ahead": 0,
                    "base_behind": 0,
                    "diff_added": 0,
                    "diff_removed": 0,
                    "dirty_files": 0,
                    "pane_target": None,
                    "needs_attention": True,
                    "recent_commits": [],
                    "observed_at": "2026-07-31T00:00:00Z",
                    "phase": {
                        "phase": "verifying",
                        "note": "running the gates",
                        "updated_at": "2026-07-31T00:00:00Z",
                        "index": 3,
                        "total": 6,
                    },
                    "todo": {"total": 4, "completed": 3, "in_progress": 1, "pending": 0},
                }
            ],
        }
    ],
}


def _client_with_handler(handler: httpx.MockTransport) -> GroveClient:
    client = GroveClient(BackendConfig(label="Local"))
    client._http = httpx.AsyncClient(base_url="http://daemon.test", transport=handler)
    return client


async def test_get_activity_issues_a_bare_get_with_no_params() -> None:
    """Zero-argument by contract — it is the read a supervisor makes holding
    nothing, so a repo scope would defeat the purpose (the ``/projects``
    precedent)."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_ACTIVITY_BODY)

    client = _client_with_handler(httpx.MockTransport(handler))
    try:
        snapshot = await client.get_activity()
    finally:
        await client.close()

    assert captured[0].method == "GET"
    assert captured[0].url.path == "/activity"
    assert dict(captured[0].url.params) == {}
    assert isinstance(snapshot, DashboardSnapshotView)


async def test_get_activity_carries_the_phase_and_agent_axes() -> None:
    """The two facts no other client method reports for a whole fleet: the
    reported task phase, and the blended agent state with ``needs_attention``.
    A caller polling N workspaces previously paid N calls for the first and
    could not get the second per workspace at all."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ACTIVITY_BODY)

    client = _client_with_handler(httpx.MockTransport(handler))
    try:
        snapshot = await client.get_activity()
    finally:
        await client.close()

    row = snapshot.projects[0].workspaces[0]
    assert row.phase is not None
    assert (row.phase.phase, row.phase.index, row.phase.total) == ("verifying", 3, 6)
    assert row.todo is not None
    assert (row.todo.completed, row.todo.total) == (3, 4)
    assert row.needs_attention is True
    assert row.sessions[0].activity.state.value == "waiting"
