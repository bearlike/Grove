"""Manager steering policy (#37): send_message / interrupt.

Pins the policy seams: injection goes to the `pane_target`-resolved target
via `tmux.send_text`, every refusal is a typed error, and — critically —
no refusal path ever reaches the injection seam (FakeTmux.sent_texts is
the spy). The mewbo arm is exercised through the constructor's
`mewbo_client` injection seam — never a patched private.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import (
    AgentSessionNotFound,
    PaneNotFound,
    SteeringUnsupported,
    WorkspaceStateError,
)
from grove.core.manager import WorkspaceEvent, WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState
from tests.conftest import FakeTmux


class FakeMewboClient:
    """Call-recording stand-in for the steering slice of ``MewboClient``."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.interrupts: list[str] = []

    def send_message(self, session_id: str, text: str) -> None:
        self.messages.append((session_id, text))

    def interrupt(self, session_id: str) -> None:
        self.interrupts.append(session_id)


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
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=store,
        mewbo_client=fake_mewbo,  # type: ignore[arg-type]  # steering-slice duck type
    )


def _create(manager: WorkspaceManager, title: str = "steer me") -> WorkspaceState:
    return manager.create(CreateWorkspaceRequest(agent_name="claude", title=title))


def _as_mewbo(manager: WorkspaceManager, state: WorkspaceState) -> None:
    """Persist the record re-kinded to mewbo, keeping the minted session id.

    Cheaper than a full mewbo create (covered by test_mewbo_launch) while
    still exercising the real dispatch: steering keys on the persisted
    ``agent_kind`` + ``agent_session_id``, both present after this.
    """
    manager.store.save(replace(state, agent_kind="mewbo"))


# ─── send_message ────────────────────────────────────────────────────────────


def test_send_message_injects_at_resolved_pane_and_emits_event(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = _create(manager)
    events: list[WorkspaceEvent] = []
    manager.subscribe(events.append)

    manager.send_message(state.id, "please run the tests")

    # Injection lands at the pane_target-resolved agent window, never a
    # hard-coded target.
    assert fake_tmux.sent_texts == [(f"{state.tmux_session}:agent", "please run the tests")]
    sent = [e for e in events if e.kind == "message_sent"]
    assert len(sent) == 1
    assert sent[0].workspace_id == state.id
    assert sent[0].detail["text_length"] == str(len("please run the tests"))
    assert sent[0].detail["target"] == f"{state.tmux_session}:agent"
    # Audit trail carries length, never content — steering text can hold secrets.
    assert "please run the tests" not in sent[0].detail.values()


def test_send_message_falls_back_to_non_shell_window(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """pane_target policy reuse: a renamed agent window still receives the steer."""
    state = _create(manager)
    fake_tmux.windows[state.tmux_session] = ["shell", "renamed-agent"]

    manager.send_message(state.id, "hello")

    assert fake_tmux.sent_texts == [(f"{state.tmux_session}:renamed-agent", "hello")]


def test_send_message_refuses_paused_and_never_injects(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = _create(manager)
    manager.pause(state.id)

    with pytest.raises(WorkspaceStateError, match="cannot send message"):
        manager.send_message(state.id, "hello")
    assert fake_tmux.sent_texts == []


def test_send_message_refuses_offline_and_never_injects(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = _create(manager)
    fake_tmux.sessions.clear()  # session vanished externally → OFFLINE

    with pytest.raises(WorkspaceStateError, match="cannot send message"):
        manager.send_message(state.id, "hello")
    assert fake_tmux.sent_texts == []


def test_send_message_refuses_when_no_pane_resolves(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = _create(manager)
    # Session is up but reports zero windows (reorganized/emptied externally).
    fake_tmux.windows[state.tmux_session] = []

    with pytest.raises(PaneNotFound):
        manager.send_message(state.id, "hello")
    assert fake_tmux.sent_texts == []


def test_send_message_mewbo_kind_steers_via_api(
    manager: WorkspaceManager, fake_tmux: FakeTmux, fake_mewbo: FakeMewboClient
) -> None:
    state = _create(manager)
    _as_mewbo(manager, state)
    events: list[WorkspaceEvent] = []
    manager.subscribe(events.append)

    manager.send_message(state.id, "hello")

    # The remote arm posts to the session API and never touches tmux.
    assert fake_mewbo.messages == [(str(state.agent_session_id), "hello")]
    assert fake_tmux.sent_texts == []
    sent = [e for e in events if e.kind == "message_sent"]
    assert len(sent) == 1
    assert sent[0].detail["target"] == f"mewbo:{state.agent_session_id}"
    assert sent[0].detail["text_length"] == str(len("hello"))


def test_send_message_mewbo_without_session_id_is_typed_error(
    manager: WorkspaceManager, fake_mewbo: FakeMewboClient
) -> None:
    state = _create(manager)
    manager.store.save(replace(state, agent_kind="mewbo", agent_session_id=None))

    with pytest.raises(AgentSessionNotFound):
        manager.send_message(state.id, "hello")
    assert fake_mewbo.messages == []


# ─── interrupt ───────────────────────────────────────────────────────────────


def test_interrupt_refuses_tmux_hosted_agents(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = _create(manager)

    # No safe generic interrupt: a cancel keystroke into an arbitrary CLI
    # is not a contract, so claude_code/generic always refuse.
    with pytest.raises(SteeringUnsupported, match="interrupt"):
        manager.interrupt(state.id)
    assert fake_tmux.sent_texts == []


def test_interrupt_mewbo_kind_interrupts_via_api(
    manager: WorkspaceManager, fake_tmux: FakeTmux, fake_mewbo: FakeMewboClient
) -> None:
    state = _create(manager)
    _as_mewbo(manager, state)

    manager.interrupt(state.id)

    assert fake_mewbo.interrupts == [str(state.agent_session_id)]
    assert fake_tmux.sent_texts == []
