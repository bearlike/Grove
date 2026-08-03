"""GroveClient.list_sessions / .session_turns — pin the query-param wire contract.

Modeled on ``test_list_workspaces.py``: an ``httpx.MockTransport`` swapped into
a connected client's own ``httpx.AsyncClient``, so the request never leaves the
process. What these pin is the scope encoding — host scope is the ABSENCE of
``repo`` on the same route, and a workspace-less session is addressed by
``(kind, cwd)`` — independent of the daemon's own tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from grove.client import BackendConfig, GroveClient

_REPO = Path("/repo/acme")
_ROW: dict[str, Any] = {
    "session_id": "s-1",
    "adapter_kind": "claude_code",
    "provenance": "fs_discovered",
    "workspace_id": None,
    "workspace_title": None,
    "workspace_branch": None,
    "git_branch": "main",
    "created_at": None,
    "modified_at": None,
    "size_bytes": None,
    "title": None,
    "first_prompt": None,
    "last_prompt": None,
    "activity": None,
    "cwd": "/elsewhere/app",
    "project": {
        "repo_root": "/elsewhere/app",
        "repo_name": "app",
        "is_worktree": False,
        "is_grove_managed": False,
    },
    "live": True,
}


def _client_with_handler(handler: httpx.MockTransport) -> GroveClient:
    client = GroveClient(BackendConfig(label="Local"))
    client._http = httpx.AsyncClient(base_url="http://daemon.test", transport=handler)
    return client


async def test_list_sessions_without_repo_asks_for_host_scope() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[_ROW])

    client = _client_with_handler(httpx.MockTransport(handler))
    try:
        rows = await client.list_sessions()
    finally:
        await client.close()

    assert captured[0].url.path == "/sessions"
    assert dict(captured[0].url.params) == {"limit": "50"}
    # A catalog row's honest nulls survive validation, and its placement fields
    # arrive parsed rather than as a loose dict.
    assert rows[0].activity is None
    assert rows[0].size_bytes is None
    assert rows[0].live is True
    assert rows[0].project is not None
    assert rows[0].project.repo_name == "app"


async def test_list_sessions_with_repo_narrows_to_one_project() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[])

    client = _client_with_handler(httpx.MockTransport(handler))
    try:
        await client.list_sessions(repo=_REPO, limit=5)
    finally:
        await client.close()

    assert dict(captured[0].url.params) == {"repo": str(_REPO), "limit": "5"}


async def test_session_turns_addresses_a_workspaceless_session_by_kind_and_cwd() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"session": _ROW, "turns": []})

    client = _client_with_handler(httpx.MockTransport(handler))
    try:
        detail = await client.session_turns("s-1", kind="claude_code", cwd="/elsewhere/app", last=3)
    finally:
        await client.close()

    assert captured[0].url.path == "/sessions/s-1/turns"
    assert dict(captured[0].url.params) == {
        "kind": "claude_code",
        "cwd": "/elsewhere/app",
        "last": "3",
    }
    assert detail.session.session_id == "s-1"


async def test_session_turns_omits_last_when_unset() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"session": _ROW, "turns": []})

    client = _client_with_handler(httpx.MockTransport(handler))
    try:
        await client.session_turns("s-1", kind="claude_code", cwd="/elsewhere/app")
    finally:
        await client.close()

    assert "last" not in dict(captured[0].url.params)
