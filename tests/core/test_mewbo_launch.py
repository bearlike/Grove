"""Manager launch fork for ``kind="mewbo"`` (#36).

A mewbo create mints its session SERVER-side: the manager calls
``MewboClient.create_session(cwd=worktree, title=...)`` and persists the
RETURNED id (the opposite of claude_code's client-minted ``--session-id``).
Fakes sit at the client boundary (injected via the manager's ``mewbo_client``
DI seam); git and the store run for real, tmux through the FakeTmux fixture.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import MewboError
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from tests.conftest import FakeTmux


class FakeMewboClient:
    """Records create_session / send_message calls; returns sequential ids."""

    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str | None]] = []
        self.messages: list[tuple[str, str]] = []
        self.fail = False
        self.fail_message = False  # #48: initial-prompt delivery fails, create must not

    def create_session(self, *, cwd: str | None = None, title: str | None = None) -> str:
        if self.fail:
            raise MewboError("mewbo api is down")
        self.calls.append((cwd, title))
        return f"mewbo-session-{len(self.calls)}"

    def send_message(self, session_id: str, text: str) -> None:
        if self.fail_message:
            raise MewboError("mewbo /message is down")
        self.messages.append((session_id, text))


@pytest.fixture
def fake_mewbo() -> FakeMewboClient:
    return FakeMewboClient()


@pytest.fixture
def manager(
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    fake_mewbo: FakeMewboClient,
) -> WorkspaceManager:
    del fake_tmux  # used via monkeypatch
    cfg = GroveConfig.model_validate(
        {
            "worktree": {
                "root_template": str(tmp_path / "trees"),
                "branch_prefix": "test/",
            },
            "tmux": {"session_prefix": "test-"},
            "agents": [{"name": "mewbo", "command": "$SHELL", "kind": "mewbo"}],
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=store,
        mewbo_client=fake_mewbo,  # type: ignore[arg-type]
    )


def _branches(repo: Path) -> set[str]:
    out = subprocess.run(
        ["git", "branch", "--list", "--format=%(refname:short)"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def test_create_persists_server_minted_id_anchored_to_worktree(
    manager: WorkspaceManager, fake_mewbo: FakeMewboClient, fake_tmux: FakeTmux
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="mewbo", title="remote task"))

    # The persisted id is what the server returned — not a Grove-minted UUID.
    assert state.agent_session_id == "mewbo-session-1"
    assert state.agent_kind == "mewbo"
    # The remote session was anchored to the (already-created) worktree and
    # carries the workspace title.
    assert fake_mewbo.calls == [(str(Path(state.worktree_path)), "remote task")]
    # No CLI decoration: the agent window just runs the configured command.
    assert fake_tmux.launch_decorations[-1] == (state.tmux_session, [])
    # Round-trips through the store.
    assert manager.get(state.id).agent_session_id == "mewbo-session-1"


def test_create_failure_is_loud_and_rolls_back(
    manager: WorkspaceManager,
    fake_mewbo: FakeMewboClient,
    fake_tmux: FakeTmux,
    tmp_repo: Path,
) -> None:
    fake_mewbo.fail = True
    before = _branches(tmp_repo)

    with pytest.raises(MewboError):
        manager.create(CreateWorkspaceRequest(agent_name="mewbo", title="doomed"))

    # Transactional like a fail_fast init: record gone, worktree gone, branch
    # gone, no tmux session left behind.
    assert manager.list() == []
    assert _branches(tmp_repo) == before
    assert fake_tmux.sessions == set()


def test_respawn_mints_a_fresh_remote_session(
    manager: WorkspaceManager, fake_mewbo: FakeMewboClient, fake_tmux: FakeTmux
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="mewbo", title="respawn me"))
    fake_tmux.kill_session(state.tmux_session)  # session vanished externally → OFFLINE

    respawned = manager.respawn(state.id)

    # A new remote session, not a continuation of the old one.
    assert respawned.agent_session_id == "mewbo-session-2"
    assert len(fake_mewbo.calls) == 2


def test_resume_keeps_the_persisted_remote_id(
    manager: WorkspaceManager, fake_mewbo: FakeMewboClient
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="mewbo", title="pause me"))
    manager.pause(state.id)

    resumed = manager.resume(state.id)

    # Continue = same session; re-engagement happens via /message (#37),
    # which resume deliberately does not call.
    assert resumed.agent_session_id == "mewbo-session-1"
    assert len(fake_mewbo.calls) == 1


def test_create_delivers_initial_prompt_via_remote_dispatch(
    manager: WorkspaceManager, fake_mewbo: FakeMewboClient, fake_tmux: FakeTmux
) -> None:
    """#48: a mewbo create with initial_prompt re-engages the freshly-minted
    session through send_message (the /message path), not a launch argv — the
    decoration stays empty."""
    state = manager.create(
        CreateWorkspaceRequest(agent_name="mewbo", title="go", initial_prompt="start the task")
    )

    assert fake_mewbo.messages == [(state.agent_session_id, "start the task")]
    assert fake_tmux.launch_decorations[-1] == (state.tmux_session, [])


def test_create_survives_initial_prompt_delivery_failure(
    manager: WorkspaceManager, fake_mewbo: FakeMewboClient
) -> None:
    """#48: a failed initial-prompt delivery must NOT roll back a successfully
    created workspace — the session exists, the user can steer manually."""
    fake_mewbo.fail_message = True

    state = manager.create(
        CreateWorkspaceRequest(agent_name="mewbo", title="resilient", initial_prompt="hi")
    )

    # Workspace survived: persisted, with its server-minted id, no message recorded.
    assert manager.get(state.id).agent_session_id == "mewbo-session-1"
    assert fake_mewbo.messages == []


def test_create_failure_event_carries_the_phase(
    manager: WorkspaceManager, fake_mewbo: FakeMewboClient
) -> None:
    events: list[Any] = []
    manager.subscribe(events.append)
    fake_mewbo.fail = True

    with pytest.raises(MewboError):
        manager.create(CreateWorkspaceRequest(agent_name="mewbo", title="doomed"))

    assert any(e.kind == "error" and e.detail.get("phase") == "agent_session" for e in events)
