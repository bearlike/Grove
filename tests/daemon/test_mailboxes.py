"""Daemon mailbox routes: the contacts directory, the send, and owner streams."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from grove.core.auth import SessionStore
from grove.core.config import GroveConfig
from grove.core.contracts.mailboxes import MailboxAddress
from grove.core.native_owners import NativeOwnerRegistry, OwnerIdentity, OwnerUnavailable
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus
from grove.daemon import build_app

_WORKSPACE = "a" * 32
_TERMINAL_WORKSPACE = "c" * 32
_GENERATION = "b" * 32


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
    # A TERMINAL-headed workspace: the case the previous design excluded from
    # the directory entirely, and the whole point of this one.
    store.save(_state(_TERMINAL_WORKSPACE, repo, agent_name="ordinary", native=False))
    auth_store = SessionStore()
    return build_app(cfg=_config(), store=store, auth_store=auth_store), auth_store, store


def _send_body(recipient: str = _TERMINAL_WORKSPACE) -> dict[str, object]:
    return {
        "sender": {"workspace_id": _WORKSPACE, "agent": ""},
        "recipient": {"workspace_id": recipient, "agent": ""},
        "subject": "Ready for review",
        "body": "The PR is open.",
    }


def test_mailbox_routes_need_the_daemon_bearer_like_every_other_route(
    mailbox_app: tuple[FastAPI, SessionStore, JsonWorkspaceStore],
) -> None:
    """One credential system, not two — but still authenticated."""
    app, _auth_store, _ = mailbox_app
    with TestClient(app) as client:
        assert client.get("/mailboxes/contacts").status_code == 401
        assert client.post("/mailboxes/messages", json=_send_body()).status_code == 401


def test_contacts_list_terminal_workspaces_too(
    mailbox_app: tuple[FastAPI, SessionStore, JsonWorkspaceStore],
) -> None:
    _app, _auth_store, store = mailbox_app
    with TestClient(build_app(cfg=_config(auth=False), store=store)) as client:
        rows = client.get("/mailboxes/contacts").json()["contacts"]

    ids = {row["address"]["workspace_id"] for row in rows}
    assert _TERMINAL_WORKSPACE in ids, "a terminal-headed agent is an ordinary contact"
    assert _WORKSPACE in ids


def test_a_paused_workspace_is_listed_but_not_writable(
    mailbox_app: tuple[FastAPI, SessionStore, JsonWorkspaceStore],
) -> None:
    """Both records are PAUSED, so neither can receive — and both still list."""
    _app, _auth_store, store = mailbox_app
    with TestClient(build_app(cfg=_config(auth=False), store=store)) as client:
        rows = client.get("/mailboxes/contacts").json()["contacts"]
        receipt = client.post("/mailboxes/messages", json=_send_body()).json()

    assert all(row["live"] is False for row in rows)
    assert receipt["stage"] == "rejected"
    assert receipt["reason"] == "not_live"


def test_owner_registration_refuses_a_terminal_workspace(
    mailbox_app: tuple[FastAPI, SessionStore, JsonWorkspaceStore],
) -> None:
    """A terminal agent is mailable but owns no native control stream."""
    _app, _auth_store, store = mailbox_app
    with TestClient(build_app(cfg=_config(auth=False), store=store)) as client:
        response = client.get(
            "/mailboxes/connection",
            params={
                "workspace_id": _TERMINAL_WORKSPACE,
                "provider_session_id": "session-1",
            },
        )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "native_session_unsupported"


def test_owner_registry_queues_controls_and_refuses_a_second_owner() -> None:
    async def scenario() -> None:
        owners = NativeOwnerRegistry()
        identity = OwnerIdentity(
            address=MailboxAddress(workspace_id=_WORKSPACE), generation=_GENERATION
        )
        binding = owners.register(identity, "session-1")

        owners.control(_WORKSPACE, "steer", "hello")
        frame = await owners.next_frame(binding)
        assert (frame.op, frame.text) == ("steer", "hello")

        with pytest.raises(OwnerUnavailable):
            owners.register(identity, "session-2")

    asyncio.run(scenario())


def test_a_control_for_an_absent_owner_refuses_rather_than_vanishing() -> None:
    owners = NativeOwnerRegistry()

    with pytest.raises(OwnerUnavailable) as excinfo:
        owners.control(_WORKSPACE, "interrupt")

    assert excinfo.value.code == "not_registered"


def test_interrupts_coalesce_and_jump_the_text_queue() -> None:
    async def scenario() -> None:
        owners = NativeOwnerRegistry()
        binding = owners.register(
            OwnerIdentity(address=MailboxAddress(workspace_id=_WORKSPACE), generation=_GENERATION),
            "session-1",
        )
        owners.control(_WORKSPACE, "steer", "first")
        owners.control(_WORKSPACE, "interrupt")
        owners.control(_WORKSPACE, "interrupt")

        first = await owners.next_frame(binding)
        second = await owners.next_frame(binding)

        assert first.op == "interrupt", "cancellation is not queued behind text"
        assert second.op == "steer"
        assert binding.queue.empty(), "the second interrupt coalesced into the first"

    asyncio.run(scenario())
