"""Daemon mailbox router: scoped peer access and runtime-owned delivery."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from grove.core.auth import SessionStore
from grove.core.config import GroveConfig
from grove.core.contracts.mailboxes import MailboxAccess, MailboxAddress, MailboxIdentity
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus
from grove.daemon import build_app
from grove.daemon._lifecycle import _LifecycleRunner
from grove.daemon.mailboxes import MailboxRouter

_WORKSPACE = "a" * 32
_OTHER_WORKSPACE = "c" * 32
_GENERATION = "b" * 32
_OTHER_GENERATION = "d" * 32


def _state(
    workspace_id: str, repo_root: Path, agent_name: str = "mailbox", *, native: bool = True
) -> WorkspaceState:
    now = datetime.now(tz=UTC)
    return WorkspaceState(
        id=workspace_id,
        title=f"mailbox-{workspace_id[:4]}",
        repo_root=str(repo_root),
        branch="main",
        base_branch="main",
        worktree_path=str(repo_root / ".grove" / "worktrees" / workspace_id),
        tmux_session=f"grove-{workspace_id}",
        agent_name=agent_name,
        status=WorkspaceStatus.PAUSED,
        created_at=now,
        updated_at=now,
        agent_kind="claude_code",
        native=native,
    )


class _ManualClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 9, 15, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


def _config(*, auth: bool = True) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "auth": {"enabled": auth},
            "agents": [
                {"name": "mailbox", "command": "claude", "kind": "claude_code", "native": True},
                {"name": "ordinary", "command": "claude", "kind": "claude_code"},
            ],
        }
    )


def _identity(workspace_id: str = _WORKSPACE, generation: str = _GENERATION) -> MailboxIdentity:
    return MailboxIdentity(
        address=MailboxAddress(workspace_id=workspace_id, agent=""), generation=generation
    )


def _token(store: SessionStore, identity: MailboxIdentity, *, registration: bool = False) -> str:
    token, _ = store.issue_mailbox_session(
        identity, label="mailbox runtime", registration=registration
    )
    return token


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _write_project_config(repo: Path) -> None:
    config = repo / ".grove" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "name": "mailbox",
                        "command": "claude",
                        "kind": "claude_code",
                        "native": True,
                    },
                    {"name": "ordinary", "command": "claude", "kind": "claude_code"},
                ]
            }
        )
    )


@pytest.fixture
def mailbox_app(tmp_state_dir: Path) -> tuple[FastAPI, SessionStore, JsonWorkspaceStore]:
    repo = tmp_state_dir / "repo"
    _write_project_config(repo)
    store = JsonWorkspaceStore()
    store.save(_state(_WORKSPACE, repo))
    store.save(_state(_OTHER_WORKSPACE, repo, agent_name="ordinary", native=False))
    auth_store = SessionStore()
    return build_app(cfg=_config(), store=store, auth_store=auth_store), auth_store, store


def test_peer_routes_require_auth_and_registration_tokens_are_not_peers(
    mailbox_app: tuple[FastAPI, SessionStore, JsonWorkspaceStore],
) -> None:
    app, auth_store, _ = mailbox_app
    registration = _token(auth_store, _identity(), registration=True)
    with TestClient(app) as client:
        assert client.get("/mailboxes/peers").status_code == 401
        response = client.get("/mailboxes/peers", headers=_headers(registration))
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "mailbox_registration_denied"


def test_peer_directory_is_managed_primary_agents_only(
    mailbox_app: tuple[FastAPI, SessionStore, JsonWorkspaceStore],
) -> None:
    app, auth_store, _ = mailbox_app
    peer = _token(auth_store, _identity())
    with TestClient(app) as client:
        router: MailboxRouter = app.state.mailbox_router
        binding = router._coordinator.register(
            _identity(),
            "provider",
            MailboxAccess(can_discover=True, can_send=True, can_reply=True, cli=True, mcp=True),
        )
        response = client.get("/mailboxes/peers", headers=_headers(peer))
        router._coordinator.unregister(binding)
    assert response.status_code == 200, response.text
    page = response.json()
    assert {row["address"]["workspace_id"] for row in page["peers"]} == {
        _WORKSPACE,
        _OTHER_WORKSPACE,
    }
    registered = next(row for row in page["peers"] if row["address"]["workspace_id"] == _WORKSPACE)
    unsupported = next(
        row for row in page["peers"] if row["address"]["workspace_id"] == _OTHER_WORKSPACE
    )
    assert registered["can_receive"] is True and registered["reason"] is None
    assert unsupported["can_receive"] is False and unsupported["reason"] == "unsupported"


async def _first_sse_frame(
    app: FastAPI, path: str, headers: dict[str, str]
) -> tuple[dict[str, Any], str]:
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"provider_session_id=provider-session",
        "root_path": "",
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 80),
    }
    start: dict[str, Any] = {}
    chunks: list[bytes] = []
    got_first = asyncio.Event()
    request_delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal request_delivered
        if not request_delivered:
            request_delivered = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await got_first.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            start.update(message)
        elif message["type"] == "http.response.body" and message.get("body"):
            chunks.append(message["body"])
            got_first.set()

    await asyncio.wait_for(app(scope, receive, send), timeout=10)
    return start, b"".join(chunks).decode().split("\n\n", 1)[0]


async def test_runtime_connection_registers_before_deliveries(
    mailbox_app: tuple[FastAPI, SessionStore, JsonWorkspaceStore],
) -> None:
    app, auth_store, _ = mailbox_app
    runtime = _token(auth_store, _identity(), registration=True)
    async with app.router.lifespan_context(app):
        start, frame = await _first_sse_frame(app, "/mailboxes/connection", _headers(runtime))
    assert start["status"] == 200
    assert "event: registered" in frame
    data_line = next(line[6:].strip() for line in frame.splitlines() if line.startswith("data:"))
    assert json.loads(data_line) == {"generation": _GENERATION}


async def test_expired_registration_reconnects_after_daemon_restart(
    tmp_state_dir: Path,
) -> None:
    """The durable owner credential re-registers; the finite peer token does not."""
    repo = tmp_state_dir / "repo"
    _write_project_config(repo)
    store = JsonWorkspaceStore()
    store.save(_state(_WORKSPACE, repo))
    clock = _ManualClock()
    auth_store = SessionStore(path=tmp_state_dir / "auth.json", clock=clock)
    registration = _token(auth_store, _identity(), registration=True)
    peer = _token(auth_store, _identity())
    app = build_app(cfg=_config(), store=store, auth_store=auth_store)
    async with app.router.lifespan_context(app):
        first_start, _ = await _first_sse_frame(
            app, "/mailboxes/connection", _headers(registration)
        )

    clock.advance(timedelta(hours=13))
    restarted_auth = SessionStore(path=auth_store.path, clock=clock)
    restarted = build_app(cfg=_config(), store=store, auth_store=restarted_auth)
    async with restarted.router.lifespan_context(restarted):
        start, _ = await _first_sse_frame(
            restarted, "/mailboxes/connection", _headers(registration)
        )
        peer_response = await _asgi_request(restarted, "GET", "/mailboxes/peers", _headers(peer))

    assert first_start["status"] == start["status"] == 200
    assert peer_response["status"] == 401
    assert peer_response["json"]["detail"]["error"] == "auth_invalid"


def test_runtime_registration_refuses_decoy_or_unconfigured_primary(
    mailbox_app: tuple[FastAPI, SessionStore, JsonWorkspaceStore],
) -> None:
    app, auth_store, _ = mailbox_app
    decoy = _token(
        auth_store,
        MailboxIdentity(
            address=MailboxAddress(workspace_id=_WORKSPACE, agent="ordinary"),
            generation=_GENERATION,
        ),
        registration=True,
    )
    unsupported = _token(
        auth_store, _identity(_OTHER_WORKSPACE, _OTHER_GENERATION), registration=True
    )
    with TestClient(app) as client:
        for token in (decoy, unsupported):
            response = client.get(
                "/mailboxes/connection",
                params={"provider_session_id": "provider"},
                headers=_headers(token),
            )
            assert response.status_code == 404
            assert response.json()["detail"]["error"] == "mailbox_unsupported"


async def test_replaced_registration_cannot_register_stale_generation(
    mailbox_app: tuple[FastAPI, SessionStore, JsonWorkspaceStore],
) -> None:
    """A new native incarnation's revocation fences the old owner's bearer."""
    app, auth_store, _ = mailbox_app
    stale = _token(auth_store, _identity(generation="e" * 32), registration=True)
    auth_store.revoke_mailbox_sessions(_identity().address)
    current = _token(auth_store, _identity(), registration=True)

    async with app.router.lifespan_context(app):
        stale_start, _ = await _first_sse_frame(app, "/mailboxes/connection", _headers(stale))
        current_start, _ = await _first_sse_frame(app, "/mailboxes/connection", _headers(current))

    assert stale_start["status"] == 401
    assert current_start["status"] == 200


def test_runtime_ack_needs_live_registration_binding(
    mailbox_app: tuple[FastAPI, SessionStore, JsonWorkspaceStore],
) -> None:
    app, auth_store, _ = mailbox_app
    runtime = _token(auth_store, _identity(), registration=True)
    with TestClient(app) as client:
        response = client.post(
            "/mailboxes/ack",
            headers=_headers(runtime),
            json={"message_id": "mbx_" + "e" * 32, "stage": "queued"},
        )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "sender_not_bound"


async def test_send_waits_for_runtime_ack_and_status_hides_from_other_sender(
    tmp_state_dir: Path,
) -> None:
    repo = tmp_state_dir / "repo"
    _write_project_config(repo)
    store = JsonWorkspaceStore()
    store.save(_state(_WORKSPACE, repo))
    auth_store = SessionStore()
    app = build_app(cfg=_config(), store=store, auth_store=auth_store)
    sender_identity = _identity("f" * 32, "1" * 32)
    sender = _token(auth_store, sender_identity)
    other_sender = _token(auth_store, _identity("e" * 32, "2" * 32))
    runtime = _token(auth_store, _identity(), registration=True)

    async with app.router.lifespan_context(app):
        router: MailboxRouter = app.state.mailbox_router
        binding = router._coordinator.register(
            _identity(),
            "provider",
            MailboxAccess(can_discover=True, can_send=True, can_reply=True, cli=True, mcp=True),
        )
        sender_binding = router._coordinator.register(
            sender_identity,
            "sender-provider",
            MailboxAccess(can_discover=True, can_send=True, can_reply=True, cli=True, mcp=True),
        )
        payload = {
            "kind": "send",
            "recipient": _identity().address.model_dump(),
            "expected_generation": _GENERATION,
            "body": "hello recipient",
        }
        send_task = asyncio.create_task(
            _asgi_request(app, "POST", "/mailboxes/messages", _headers(sender), payload)
        )
        await asyncio.sleep(0)
        delivery = await asyncio.wait_for(router._coordinator.next_delivery(binding), 2)
        assert delivery.message_id.startswith("mbx_")
        router._coordinator.acknowledge(
            binding, delivery.message_id, stage="delivered", evidence="provider-input-1"
        )
        sent = await send_task
        assert sent["status"] == 200
        receipt = sent["json"]
        assert receipt["stage"] == "delivered"
        assert receipt["native_evidence"] == "provider-input-1"
        stranger = await _asgi_request(
            app, "GET", f"/mailboxes/messages/{delivery.message_id}", _headers(other_sender)
        )
        assert stranger["status"] == 404
        assert stranger["json"]["detail"]["error"] == "receipt_not_found"
        router._coordinator.unregister(binding)
        router._coordinator.unregister(sender_binding)

    # The scoped runtime bearer cannot use the broad normal daemon surface.
    response = await _asgi_request(app, "GET", "/workspaces", _headers(runtime))
    assert response["status"] == 403
    assert response["json"]["detail"]["error"] == "mailbox_scope_denied"


async def _asgi_request(
    app: FastAPI,
    method: str,
    path: str,
    headers: dict[str, str],
    payload: dict[str, object] | None = None,
) -> dict[str, Any]:
    body = b"" if payload is None else json.dumps(payload).encode()
    request_sent = False
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Future()

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    raw_headers = [(key.lower().encode(), value.encode()) for key, value in headers.items()]
    if payload is not None:
        raw_headers.append((b"content-type", b"application/json"))
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": raw_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 80),
    }
    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    raw = b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )
    return {"status": start["status"], "json": json.loads(raw or b"null")}


async def test_lifecycle_hold_excludes_bounded_mailbox_wait() -> None:
    runner = _LifecycleRunner(max_workers=1)
    entered = asyncio.Event()
    release = asyncio.Event()
    lifecycle_entered = asyncio.Event()

    async def mailbox_wait() -> None:
        async with runner.hold("workspace"):
            entered.set()
            await release.wait()

    async def lifecycle_verb() -> None:
        def record() -> None:
            lifecycle_entered.set()

        await runner.run("workspace", record)

    try:
        hold_task = asyncio.create_task(mailbox_wait())
        await entered.wait()
        lifecycle_task = asyncio.create_task(lifecycle_verb())
        await asyncio.sleep(0)
        assert not lifecycle_entered.is_set()
        release.set()
        await hold_task
        await lifecycle_task
        assert lifecycle_entered.is_set()
    finally:
        runner.shutdown()
