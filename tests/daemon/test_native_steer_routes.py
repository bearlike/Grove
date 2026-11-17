"""The daemon's steer routes on a NATIVE workspace reach the owner in-process.

`build_app` injects `OwnerSteerClient` into every manager it mints, so
`/message`, `/interrupt` and `/controls/model` on a native workspace queue a
frame onto the connected owner's delivery stream — never a tmux keystroke — and
refuse with the pane-shaped 409 when no owner is connected.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi.testclient import TestClient

from grove.core.contracts.mailboxes import MailboxAddress
from grove.core.native_owners import NativeOwnerRegistry, OwnerIdentity
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState
from grove.daemon import build_app
from grove.daemon.mailboxes import OwnerSteerClient
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config

pytestmark = pytest.mark.usefixtures("native_roster", "tmp_state_dir")


@pytest.fixture
def daemon(tmp_repo: Path, fake_tmux: FakeTmux) -> Iterator[TestClient]:
    del tmp_repo, fake_tmux
    app = build_app(cfg=daemon_test_config(), store=JsonWorkspaceStore())
    with TestClient(app) as client:
        yield client


@pytest.fixture
def native_ws(daemon: TestClient, tmp_repo: Path) -> str:
    body = daemon.post(
        "/workspaces",
        json={
            "agent_name": "claude",
            "title": "native steer",
            "repo_root": str(tmp_repo),
            "branch_plan": {"kind": "auto"},
        },
    ).json()
    assert body["native"] is True, body
    return body["id"]


def _connect_owner(daemon: TestClient, workspace_id: str) -> NativeOwnerRegistry:
    owners: NativeOwnerRegistry = daemon.app.state.native_owners
    owners.register(
        OwnerIdentity(address=MailboxAddress(workspace_id=workspace_id), generation="1" * 32),
        "provider-session",
    )
    return owners


def test_steer_routes_queue_control_frames_for_the_connected_owner(
    daemon: TestClient, native_ws: str, fake_tmux: FakeTmux
) -> None:
    owners = _connect_owner(daemon, native_ws)

    assert daemon.post(f"/workspaces/{native_ws}/message", json={"text": "hi"}).status_code == 204
    assert daemon.post(f"/workspaces/{native_ws}/interrupt").status_code == 204
    resp = daemon.post(f"/workspaces/{native_ws}/controls/model", json={"model": "opus"})
    assert resp.status_code == 204
    resp = daemon.post(f"/workspaces/{native_ws}/controls/effort", json={"effort": "xhigh"})
    assert resp.status_code == 204

    binding = owners.owner_for(native_ws)
    assert binding is not None
    frames = [binding.queue.get_nowait()[2] for _ in range(4)]
    assert [(f.op, f.text) for f in frames if f is not None] == [
        ("interrupt", ""),
        ("steer", "hi"),
        ("set_model", "opus"),
        ("set_effort", "xhigh"),
    ]
    assert fake_tmux.sent_texts == []
    assert fake_tmux.escapes == []


def test_a_native_workspace_with_no_owner_refuses_like_a_missing_pane(
    daemon: TestClient, native_ws: str
) -> None:
    """The owner is not connected (worker still booting, or dead): 409
    `pane_not_found`, the same code a session with no window gets, because a
    respawn is the remedy in both cases — never a 204 that delivered nothing."""
    resp = daemon.post(f"/workspaces/{native_ws}/interrupt")
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "pane_not_found"
    resp = daemon.post(f"/workspaces/{native_ws}/controls/model", json={"model": "opus"})
    assert resp.status_code == 409
    assert "no connected native owner" in resp.json()["detail"]["message"]


def test_effort_route_refuses_what_it_cannot_carry_and_queues_nothing(
    daemon: TestClient, native_ws: str, tmp_repo: Path
) -> None:
    """422 `effort_not_supported` for a level the kind does not list (Claude
    would have acked it), and the same for a terminal workspace with no channel.
    Both leave the owner's queue empty: a refusal that also delivered would be
    the silent-apply this route exists to prevent."""
    owners = _connect_owner(daemon, native_ws)
    binding = owners.owner_for(native_ws)
    assert binding is not None

    bad_level = daemon.post(f"/workspaces/{native_ws}/controls/effort", json={"effort": "bogus"})
    assert bad_level.status_code == 422
    assert bad_level.json()["detail"]["error"] == "effort_not_supported"
    assert binding.queue.empty()

    shape = daemon.post(f"/workspaces/{native_ws}/controls/effort", json={"effort": "--high"})
    assert shape.status_code == 422

    terminal = daemon.post(
        "/workspaces",
        json={
            "agent_name": "claude-terminal",
            "title": "tui",
            "repo_root": str(tmp_repo),
            "branch_plan": {"kind": "auto"},
        },
    ).json()
    refused = daemon.post(f"/workspaces/{terminal['id']}/controls/effort", json={"effort": "high"})
    assert refused.status_code == 422
    assert refused.json()["detail"]["error"] == "effort_not_supported"


def test_effort_route_with_no_connected_owner_is_a_pane_shaped_409(
    daemon: TestClient, native_ws: str
) -> None:
    resp = daemon.post(f"/workspaces/{native_ws}/controls/effort", json={"effort": "high"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "pane_not_found"


def test_the_terminal_twin_still_steers_the_pane(
    daemon: TestClient, tmp_repo: Path, fake_tmux: FakeTmux
) -> None:
    body = daemon.post(
        "/workspaces",
        json={
            "agent_name": "claude-terminal",
            "title": "tui",
            "repo_root": str(tmp_repo),
            "branch_plan": {"kind": "auto"},
        },
    ).json()
    assert body["native"] is False
    assert daemon.post(f"/workspaces/{body['id']}/message", json={"text": "hi"}).status_code == 204
    assert [t for _, t in fake_tmux.sent_texts] == ["hi"]


def test_the_steer_client_remembers_what_it_submitted_and_not_what_it_refused() -> None:
    """The record `label_queue` reads: inputs as the provider receives them."""
    owners = NativeOwnerRegistry()
    client = OwnerSteerClient(owners)
    loop = asyncio.new_event_loop()
    try:
        client.bind(loop)
        state = cast(WorkspaceState, SimpleNamespace(id="a" * 32))
        with pytest.raises(Exception, match="no connected native owner"):
            client.send_message(state, "refused")
        owners.register(
            OwnerIdentity(address=MailboxAddress(workspace_id="a" * 32), generation="1" * 32), "s"
        )
        client.send_message(state, "hi")
        client.invoke_control(state, "review")
        client.interrupt(state)
        assert [text for text, _ in client.recent_inputs(state)] == ["hi", "/review"]
    finally:
        loop.close()
