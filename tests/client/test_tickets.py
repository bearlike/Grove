"""GroveClient ticket methods — pin the request contract + View deserialization.

The daemon ``/tickets`` routes may not exist yet, so these tests do not stand
up a real daemon. Instead they inject an
``httpx.MockTransport`` into the connected client's own ``httpx.AsyncClient`` —
the exact seam every ticket method rides — and assert two things per method:
the request it issues (verb + path + query/body) and that it deserializes the
daemon's reply into the right contract type. This pins the client SDK's wire
contract independently of the daemon's progress, the same boundary the real
HTTP tests in ``test_client_http.py`` exercise end to end.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from grove.client import BackendConfig, GroveClient, ProtocolError
from grove.core.contracts.tickets import (
    TicketProviderView,
    TicketRef,
    TicketSelector,
)
from grove.core.contracts.views import WorkspaceStateView
from grove.core.workspace import WorkspaceStatus

_REPO = Path("/repo/acme")


def _state_view_payload() -> dict[str, object]:
    """One serialized ``WorkspaceStateView`` the attach/detach routes return."""
    now = datetime(2026, 6, 13, 12, 0, 0, tzinfo=UTC)
    view = WorkspaceStateView(
        id="ws-1",
        title="Fix login",
        repo_root=str(_REPO),
        branch="grove/fix-login",
        base_branch="main",
        worktree_path=str(_REPO / ".worktrees" / "fix-login"),
        tmux_session="grove-fix-login",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )
    return view.model_dump(mode="json")


def _client_with_handler(
    handler: httpx.MockTransport,
) -> GroveClient:
    """A connected client whose HTTP layer is swapped for ``handler``.

    Builds a real ``GroveClient`` (local backend) but replaces its
    ``httpx.AsyncClient`` with one backed by the mock transport, so the
    request never leaves the process. ``connect()`` is bypassed because it
    would spawn a daemon subprocess and mint a token we do not need here.
    """
    client = GroveClient(BackendConfig(label="Local"))
    # Swap the documented HTTP seam (``_http``) for the mock transport.
    client._http = httpx.AsyncClient(
        base_url="http://daemon.test",
        transport=handler,
    )
    return client


async def test_list_ticket_providers_issues_get_with_repo_param() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json=[
                {"provider": "github", "label": "GitHub", "configured": True},
                {
                    "provider": "linear",
                    "label": "Linear",
                    "configured": False,
                    "context": "ENG",
                },
            ],
        )

    client = _client_with_handler(httpx.MockTransport(handler))
    try:
        rows = await client.list_ticket_providers(_REPO)
    finally:
        await client.close()

    assert captured[0].method == "GET"
    assert captured[0].url.path == "/tickets/providers"
    assert dict(captured[0].url.params) == {"repo": str(_REPO)}
    assert [type(r) for r in rows] == [TicketProviderView, TicketProviderView]
    assert rows[0].provider == "github"
    assert rows[1].context == "ENG"


async def test_list_assigned_tickets_no_filters_sends_only_repo() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[{"provider": "gitea", "id": "42"}])

    client = _client_with_handler(httpx.MockTransport(handler))
    try:
        refs = await client.list_assigned_tickets(_REPO)
    finally:
        await client.close()

    assert captured[0].method == "GET"
    assert captured[0].url.path == "/tickets/assigned"
    assert dict(captured[0].url.params) == {"repo": str(_REPO)}
    assert [type(r) for r in refs] == [TicketRef]
    assert refs[0].id == "42"


async def test_list_assigned_tickets_omits_none_filters_includes_set() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[])

    client = _client_with_handler(httpx.MockTransport(handler))
    try:
        await client.list_assigned_tickets(_REPO, provider="linear", status="open")
    finally:
        await client.close()

    assert dict(captured[0].url.params) == {
        "repo": str(_REPO),
        "provider": "linear",
        "status": "open",
    }


async def test_get_ticket_issues_get_with_provider_and_id_in_path() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "provider": "github",
                "id": "7",
                "title": "Ticket provider layer",
                "status": "open",
            },
        )

    client = _client_with_handler(httpx.MockTransport(handler))
    try:
        ref = await client.get_ticket(_REPO, "github", "7")
    finally:
        await client.close()

    assert captured[0].method == "GET"
    assert captured[0].url.path == "/tickets/github/7"
    assert dict(captured[0].url.params) == {"repo": str(_REPO)}
    assert isinstance(ref, TicketRef)
    assert ref.title == "Ticket provider layer"


async def test_attach_ticket_posts_selector_body_and_returns_state() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_state_view_payload())

    client = _client_with_handler(httpx.MockTransport(handler))
    try:
        state = await client.attach_ticket("ws-1", TicketSelector(provider="gitea", id="99"))
    finally:
        await client.close()

    assert captured[0].method == "POST"
    assert captured[0].url.path == "/workspaces/ws-1/tickets"
    # `kind` rides the selector, defaulted — an attach that names no kind still
    # sends the issue default rather than omitting the field.
    assert captured[0].read() == b'{"provider":"gitea","id":"99","kind":"issue"}'
    assert isinstance(state, WorkspaceStateView)
    assert state.id == "ws-1"


async def test_detach_ticket_issues_delete_on_nested_path_returns_state() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_state_view_payload())

    client = _client_with_handler(httpx.MockTransport(handler))
    try:
        state = await client.detach_ticket("ws-1", "linear", "ENG-123")
    finally:
        await client.close()

    assert captured[0].method == "DELETE"
    assert captured[0].url.path == "/workspaces/ws-1/tickets/linear/ENG-123"
    assert isinstance(state, WorkspaceStateView)
    assert state.id == "ws-1"


async def test_attach_ticket_by_ref_posts_ref_body_and_returns_state() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_state_view_payload())

    client = _client_with_handler(httpx.MockTransport(handler))
    try:
        state = await client.attach_ticket_by_ref("ws-1", "#42")
    finally:
        await client.close()

    assert captured[0].method == "POST"
    assert captured[0].url.path == "/workspaces/ws-1/tickets"
    assert captured[0].read() == b'{"ref":"#42"}'
    assert isinstance(state, WorkspaceStateView)
    assert state.id == "ws-1"


async def test_detach_ticket_by_ref_issues_delete_with_ref_query_param() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_state_view_payload())

    client = _client_with_handler(httpx.MockTransport(handler))
    try:
        state = await client.detach_ticket_by_ref("ws-1", "#42")
    finally:
        await client.close()

    assert captured[0].method == "DELETE"
    assert captured[0].url.path == "/workspaces/ws-1/tickets"
    assert dict(captured[0].url.params) == {"ref": "#42"}
    assert isinstance(state, WorkspaceStateView)
    assert state.id == "ws-1"


async def test_attach_ticket_by_ref_surfaces_ticket_link_ambiguous_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            409,
            json={"detail": {"error": "ticket_link_ambiguous", "message": "'42' could be..."}},
        )

    client = _client_with_handler(httpx.MockTransport(handler))
    try:
        with pytest.raises(ProtocolError) as excinfo:
            await client.attach_ticket_by_ref("ws-1", "42")
    finally:
        await client.close()

    assert excinfo.value.code == "ticket_link_ambiguous"
    assert excinfo.value.status == 409


async def test_attach_ticket_surfaces_protocol_error_envelope() -> None:
    """A daemon error envelope (the shared ``_unwrap`` path) becomes a typed
    ``ProtocolError`` — pins that ticket methods ride the same unwrap helper."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404, json={"detail": {"error": "workspace_not_found", "message": "no ws"}}
        )

    client = _client_with_handler(httpx.MockTransport(handler))
    try:
        with pytest.raises(ProtocolError) as excinfo:
            await client.attach_ticket("ws-x", TicketSelector(provider="gitea", id="1"))
    finally:
        await client.close()

    assert excinfo.value.code == "workspace_not_found"
    assert excinfo.value.status == 404
