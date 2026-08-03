"""GroveClient transport failures always carry a reason.

The defect these pin is not "a request failed" but "a request failed and said
nothing": ``httpx``'s timeout exceptions stringify to the empty string, and the
client let them escape raw, so every consumer that renders ``str(exc)`` — the
MCP SDK's ``f"Error executing tool {name}: {e}"`` among them — reported a bare
failure with no body at all.

Same ``httpx.MockTransport`` seam as ``test_list_workspaces.py`` /
``test_tickets.py``: swapped into a connected client's own ``httpx.AsyncClient``
so nothing leaves the process.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest

from grove.client import BackendConfig, GroveClient, TransportError
from grove.core.contracts import AutoBranch, CreateWorkspaceRequest


def _client_with_handler(handler: Callable[[httpx.Request], httpx.Response]) -> GroveClient:
    client = GroveClient(BackendConfig(label="Local"))
    client._http = httpx.AsyncClient(
        base_url="http://daemon.test", transport=httpx.MockTransport(handler)
    )
    return client


def test_httpx_timeouts_stringify_to_nothing() -> None:
    """The property the whole guard exists for, pinned so it is never re-derived.

    httpcore maps a bare ``TimeoutError()`` through to httpx, and that exception's
    message is the empty string — so an unguarded timeout renders as *nothing*.
    Should a future httpx start supplying a message, this test failing is the
    signal that the guard's rationale changed, not that the guard is unnecessary.
    """
    assert str(httpx.ReadTimeout("")) == ""


async def test_timeout_raises_transport_error_naming_the_call_and_budget() -> None:
    """A timed-out steer names the call, the daemon, and the budget it blew."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("", request=request)

    client = _client_with_handler(handler)
    try:
        with pytest.raises(TransportError) as excinfo:
            await client.send_message("ws-1", "RETRACTION - ignore the previous claim")
    finally:
        await client.close()

    message = str(excinfo.value)
    assert message  # the whole point: never empty
    assert "/workspaces/ws-1/message" in message
    assert "30s" in message
    # A timeout is not a statement that the work did not happen, and a caller
    # that reads it as one will duplicate the effect by retrying.
    assert "may still" in message


async def test_network_failure_names_the_httpx_cause() -> None:
    """A non-timeout transport failure keeps httpx's own type in the reason, so a
    refused connection is distinguishable from a dropped one."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("All connection attempts failed", request=request)

    client = _client_with_handler(handler)
    try:
        with pytest.raises(TransportError) as excinfo:
            await client.list_workspaces()
    finally:
        await client.close()

    assert "ConnectError" in str(excinfo.value)
    assert "All connection attempts failed" in str(excinfo.value)


async def test_message_less_httpx_error_still_names_its_type() -> None:
    """Several httpx errors carry an empty message too — the type name is the
    fallback that can never be blank."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("", request=request)

    client = _client_with_handler(handler)
    try:
        with pytest.raises(TransportError) as excinfo:
            await client.list_workspaces()
    finally:
        await client.close()

    assert "ReadError" in str(excinfo.value)


async def test_a_success_whose_body_is_not_json_is_a_transport_error() -> None:
    """The other half of the same contract.

    `_request` guards the failure path; a **200** carrying a proxy error page or
    a truncated body escaped as a raw `json.JSONDecodeError` — neither a
    `ProtocolError` (so the capability-degrade branches that key on one are
    unreachable) nor a `TransportError` (so the module's documented contract was
    simply false for it). "Expecting value: line 1 column 1" reads to a caller
    like a Grove bug rather than a broken hop.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>502 Bad Gateway</html>")

    client = _client_with_handler(handler)
    try:
        with pytest.raises(TransportError) as excinfo:
            await client.list_workspaces()
    finally:
        await client.close()

    message = str(excinfo.value)
    assert "/workspaces" in message
    assert "not JSON" in message


def _create_request() -> CreateWorkspaceRequest:
    return CreateWorkspaceRequest(
        agent_name="claude", title="t", repo_root=Path("/repo"), branch_plan=AutoBranch()
    )


@pytest.mark.parametrize(
    ("call", "path"),
    [
        (lambda c: c.create_workspace(_create_request()), "/workspaces"),
        (lambda c: c.resume("ws-1"), "/workspaces/ws-1/resume"),
        (lambda c: c.respawn("ws-1"), "/workspaces/ws-1/respawn"),
    ],
    ids=["create", "resume", "respawn"],
)
async def test_lifecycle_calls_outlast_the_engines_own_init_budget(
    call: Callable[[GroveClient], Awaitable[object]], path: str
) -> None:
    """create/resume/respawn run the user's init script, which the engine bounds
    at ``InitScriptConfig.timeout_seconds`` (300s by default). A client deadline
    below the server's own bound on a single step reports a healthy slow operation
    as a failure — and does so *after* the daemon has committed to the work, so
    the operation lands anyway and the caller never learns of it."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        raise httpx.ReadTimeout("", request=request)

    client = _client_with_handler(handler)
    try:
        with pytest.raises(TransportError) as excinfo:
            await call(client)
    finally:
        await client.close()

    assert captured[0].url.path == path
    assert captured[0].extensions["timeout"]["read"] > 300.0
    # Pinned to the constant, not to a literal: the budget is the SUM of the
    # engine's per-step bounds and legitimately grows when a step is added
    # (e.g. container provisioning). What must never change is that it exceeds
    # every one of those bounds and that the message names it.
    assert f"{GroveClient._LIFECYCLE_TIMEOUT_S:g}s" in str(excinfo.value)
