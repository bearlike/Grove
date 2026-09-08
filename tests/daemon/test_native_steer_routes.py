"""The daemon's steer routes on a NATIVE workspace reach the owner in-process.

`build_app` injects `CoordinatorSteerClient` into every manager it mints, so
`/message`, `/interrupt` and `/controls/model` on a native workspace queue a
frame onto the connected owner's delivery stream — never a tmux keystroke — and
refuse with the pane-shaped 409 when no owner is connected.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.contracts.mailboxes import MailboxAccess, MailboxAddress, MailboxIdentity
from grove.core.mailboxes import MailboxCoordinator
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
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


def _connect_owner(daemon: TestClient, workspace_id: str) -> MailboxCoordinator:
    coordinator: MailboxCoordinator = daemon.app.state.mailbox_coordinator
    coordinator.register(
        MailboxIdentity(address=MailboxAddress(workspace_id=workspace_id), generation="1" * 32),
        "provider-session",
        MailboxAccess(can_discover=True, can_send=True, can_reply=True, cli=True),
    )
    return coordinator


def test_steer_routes_queue_control_frames_for_the_connected_owner(
    daemon: TestClient, native_ws: str, fake_tmux: FakeTmux
) -> None:
    coordinator = _connect_owner(daemon, native_ws)

    assert daemon.post(f"/workspaces/{native_ws}/message", json={"text": "hi"}).status_code == 204
    assert daemon.post(f"/workspaces/{native_ws}/interrupt").status_code == 204
    resp = daemon.post(f"/workspaces/{native_ws}/controls/model", json={"model": "opus"})
    assert resp.status_code == 204

    binding = coordinator.owner_for(native_ws)
    assert binding is not None
    frames = [binding.queue.get_nowait()[2] for _ in range(3)]
    assert [(f.op, f.text) for f in frames if f is not None] == [
        ("interrupt", ""),
        ("steer", "hi"),
        ("set_model", "opus"),
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
