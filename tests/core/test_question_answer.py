"""Manager orchestration for answering a live question (#109): answer_question.

Pins the policy: a pending capture must match the requested ``tool_use_id`` (else
``QuestionNotPending``), the plan must fit the captured questions (else
``QuestionAnswerInvalid``), the keystrokes the Claude adapter built land at the
``pane_target``-resolved pane via ``tmux.send_keys`` — and every refusal path
reaches the injection seam zero times (``FakeTmux.sent_keys`` is the spy). The
sidecar is written through the real ``ClaudeHook`` capture path, never a hand-
built file, so the test exercises the same shape the hook produces.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from grove.core.agents.hook import ClaudeHook
from grove.core.config import GroveConfig
from grove.core.contracts.questions import QuestionAnswerItem, QuestionAnswerRequest
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import PaneNotFound, QuestionAnswerInvalid, QuestionNotPending
from grove.core.manager import WorkspaceEvent, WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.tmux import SendKey
from grove.core.workspace import WorkspaceState
from tests.conftest import FakeTmux

NOW_TS = "2026-07-04T12:00:00+00:00"

_ASK_INPUT = {
    "questions": [
        {
            "question": "Which color?",
            "header": "Color",
            "multiSelect": False,
            "options": [{"label": "Blue"}, {"label": "Green"}],
        }
    ]
}


@pytest.fixture
def sidecar_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Sandbox the agent-sidecar dir the manager reads captures from."""
    target = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: target)
    return target


@pytest.fixture
def manager(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> WorkspaceManager:
    del fake_tmux  # used via monkeypatch
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


def _create(manager: WorkspaceManager) -> WorkspaceState:
    return manager.create(CreateWorkspaceRequest(agent_name="claude", title="answer me"))


def _capture(sidecar_dir: Path, session_id: str, tool_use_id: str = "toolu_1") -> None:
    """Write a pending-question sidecar via the real hook capture path."""
    ClaudeHook.record_event(
        {
            "hook_event_name": "PreToolUse",
            "session_id": session_id,
            "tool_name": "AskUserQuestion",
            "tool_use_id": tool_use_id,
            "tool_input": _ASK_INPUT,
        },
        sidecar_dir=sidecar_dir,
        tmux_pane=None,
        now=datetime.now(tz=UTC),
    )


def _request(session_id: str, **kw: object) -> QuestionAnswerRequest:
    tool_use_id = kw.pop("tool_use_id", "toolu_1")
    answers = kw.pop("answers", [QuestionAnswerItem(selected_indexes=[0])])
    return QuestionAnswerRequest(
        session_id=session_id,
        tool_use_id=tool_use_id,
        answers=answers,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("bad_char", ["\x1b", "\n", "\r", "\t", "\x00", "\x7f"])
def test_question_answer_item_rejects_control_characters_in_text(bad_char: str) -> None:
    """A raw control byte (ESC/CR/LF/tab/etc.) in free text would type verbatim
    into the pane via ``send-keys -l`` — ESC cancels the whole question, CR/LF
    act as an early Enter mid-sequence (#110). The wire model rejects it outright
    rather than let it reach the keystroke builder."""
    with pytest.raises(ValidationError, match="control characters"):
        QuestionAnswerItem(text=f"hello{bad_char}world")


def test_answer_question_sends_keystrokes_at_resolved_pane_and_emits_event(
    manager: WorkspaceManager, fake_tmux: FakeTmux, sidecar_dir: Path
) -> None:
    state = _create(manager)
    sid = str(state.agent_session_id)
    _capture(sidecar_dir, sid)
    events: list[WorkspaceEvent] = []
    manager.subscribe(events.append)

    manager.answer_question(state.id, _request(sid))

    # A lone single-select answered at index 0 → the digit "1", no review step.
    assert fake_tmux.sent_keys == [(f"{state.tmux_session}:agent", ["1"])]
    answered = [e for e in events if e.kind == "question_answered"]
    assert len(answered) == 1
    assert answered[0].detail["tool_use_id"] == "toolu_1"
    assert answered[0].detail["target"] == f"{state.tmux_session}:agent"
    assert answered[0].detail["answers"] == "1"


def test_answer_question_multiselect_drives_full_sequence(
    manager: WorkspaceManager, fake_tmux: FakeTmux, sidecar_dir: Path
) -> None:
    state = _create(manager)
    sid = str(state.agent_session_id)
    ClaudeHook.record_event(
        {
            "hook_event_name": "PreToolUse",
            "session_id": sid,
            "tool_name": "AskUserQuestion",
            "tool_use_id": "toolu_m",
            "tool_input": {
                "questions": [
                    {
                        "question": "Toppings?",
                        "multiSelect": True,
                        "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}],
                    }
                ]
            },
        },
        sidecar_dir=sidecar_dir,
        tmux_pane=None,
        now=datetime.now(tz=UTC),
    )

    manager.answer_question(
        state.id,
        _request(sid, tool_use_id="toolu_m", answers=[QuestionAnswerItem(selected_indexes=[0, 2])]),
    )

    # multiSelect toggles then Tab, and a lone multiSelect still has a review Enter.
    assert fake_tmux.sent_keys == [
        (f"{state.tmux_session}:agent", ["1", "3", SendKey.TAB, SendKey.ENTER])
    ]


def test_answer_question_stale_tool_use_id_is_not_pending_and_never_sends(
    manager: WorkspaceManager, fake_tmux: FakeTmux, sidecar_dir: Path
) -> None:
    state = _create(manager)
    sid = str(state.agent_session_id)
    _capture(sidecar_dir, sid, tool_use_id="toolu_1")

    with pytest.raises(QuestionNotPending, match="toolu_1"):
        manager.answer_question(state.id, _request(sid, tool_use_id="toolu_OTHER"))
    assert fake_tmux.sent_keys == []


def test_answer_question_foreign_session_id_is_not_pending_and_never_sends(
    manager: WorkspaceManager, fake_tmux: FakeTmux, sidecar_dir: Path
) -> None:
    """A session_id that isn't this workspace's minted agent session must be
    refused before the sidecar is even read (#110) — otherwise a captured
    question belonging to a different workspace (or a second session of this
    one) would drive keystrokes into THIS workspace's pane."""
    state = _create(manager)
    sid = str(state.agent_session_id)
    _capture(sidecar_dir, sid)
    foreign_sid = sid + "-foreign"
    _capture(sidecar_dir, foreign_sid)

    with pytest.raises(QuestionNotPending, match="not the agent session bound to"):
        manager.answer_question(state.id, _request(foreign_sid))
    assert fake_tmux.sent_keys == []


def test_answer_question_no_capture_is_not_pending(
    manager: WorkspaceManager, fake_tmux: FakeTmux, sidecar_dir: Path
) -> None:
    state = _create(manager)
    sid = str(state.agent_session_id)

    with pytest.raises(QuestionNotPending, match="no pending question"):
        manager.answer_question(state.id, _request(sid))
    assert fake_tmux.sent_keys == []


def test_answer_question_plan_mismatch_is_invalid_and_never_sends(
    manager: WorkspaceManager, fake_tmux: FakeTmux, sidecar_dir: Path
) -> None:
    state = _create(manager)
    sid = str(state.agent_session_id)
    _capture(sidecar_dir, sid)

    # One captured question, two answers → the plan doesn't fit.
    bad = _request(
        sid,
        answers=[
            QuestionAnswerItem(selected_indexes=[0]),
            QuestionAnswerItem(selected_indexes=[1]),
        ],
    )
    with pytest.raises(QuestionAnswerInvalid, match="expected 1 answer"):
        manager.answer_question(state.id, bad)
    assert fake_tmux.sent_keys == []


def test_answer_question_no_pane_is_pane_not_found_and_never_sends(
    manager: WorkspaceManager, fake_tmux: FakeTmux, sidecar_dir: Path
) -> None:
    state = _create(manager)
    sid = str(state.agent_session_id)
    _capture(sidecar_dir, sid)
    fake_tmux.windows[state.tmux_session] = []  # session up, zero windows

    with pytest.raises(PaneNotFound):
        manager.answer_question(state.id, _request(sid))
    assert fake_tmux.sent_keys == []
