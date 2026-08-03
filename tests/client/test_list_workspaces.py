"""GroveClient.list_workspaces — pin the repo/ticket query-param wire contract.

Modeled on ``test_tickets.py``: an ``httpx.MockTransport`` swapped into a
connected client's own ``httpx.AsyncClient``, so the request never leaves the
process. Pins the params GET /workspaces receives, independent of the
daemon's own tests.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from grove.client import BackendConfig, GroveClient

_REPO = Path("/repo/acme")


def _client_with_handler(handler: httpx.MockTransport) -> GroveClient:
    """A connected client whose HTTP layer is swapped for ``handler`` — see
    ``test_tickets.py`` for the full rationale (no real daemon needed)."""
    client = GroveClient(BackendConfig(label="Local"))
    client._http = httpx.AsyncClient(base_url="http://daemon.test", transport=handler)
    return client


async def test_list_workspaces_no_filters_sends_no_params() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[])

    client = _client_with_handler(httpx.MockTransport(handler))
    try:
        await client.list_workspaces()
    finally:
        await client.close()

    assert captured[0].method == "GET"
    assert captured[0].url.path == "/workspaces"
    assert dict(captured[0].url.params) == {}


async def test_list_workspaces_repo_param() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[])

    client = _client_with_handler(httpx.MockTransport(handler))
    try:
        await client.list_workspaces(repo=_REPO)
    finally:
        await client.close()

    assert dict(captured[0].url.params) == {"repo": str(_REPO)}


async def test_list_workspaces_ticket_filter_combines_provider_and_id() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[])

    client = _client_with_handler(httpx.MockTransport(handler))
    try:
        await client.list_workspaces(ticket_provider="github", ticket_id="42")
    finally:
        await client.close()

    assert dict(captured[0].url.params) == {"ticket": "github:42"}


async def test_list_workspaces_repo_and_ticket_combine() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[])

    client = _client_with_handler(httpx.MockTransport(handler))
    try:
        await client.list_workspaces(repo=_REPO, ticket_provider="github", ticket_id="42")
    finally:
        await client.close()

    assert dict(captured[0].url.params) == {"repo": str(_REPO), "ticket": "github:42"}


async def test_list_workspaces_ticket_provider_without_id_raises() -> None:
    client = _client_with_handler(httpx.MockTransport(lambda r: httpx.Response(200, json=[])))
    try:
        with pytest.raises(ValueError, match="ticket_provider and ticket_id"):
            await client.list_workspaces(ticket_provider="github")
    finally:
        await client.close()
