"""Manager orchestration for answering a live question: answer_question.

Pins the policy: a pending capture must match the requested ``tool_use_id`` (else
``QuestionNotPending``), the plan must fit the captured questions (else
``QuestionAnswerInvalid``), and a valid plan DISMISSES the on-screen question
and delivers the rendered batch as one Grove-fenced message. Every refusal path
reaches both injection seams zero times — ``FakeTmux.escapes`` and
``FakeTmux.sent_texts`` are the spies, and asserting on BOTH is the point: a
refusal that still pressed Escape would have cancelled a question the human is
about to answer themselves.

The sidecar is written through the real ``ClaudeHook`` capture path, never a
hand-built file, so the test exercises the same shape the hook produces.
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


@pytest.mark.parametrize("bad_char", ["\x1b", "\r", "\x00", "\x7f"])
def test_question_answer_item_rejects_terminal_control_characters_in_text(bad_char: str) -> None:
    """A raw control byte in free text is typed verbatim into the pane via
    ``send-keys -l``: ESC cancels whatever is on screen and CR is the submit
    key. The wire model refuses them rather than let them reach the pane."""
    with pytest.raises(ValidationError, match="control characters"):
        QuestionAnswerItem(text=f"hello{bad_char}world")


@pytest.mark.parametrize("ok_char", ["\n", "\t"])
def test_question_answer_item_accepts_newlines_and_tabs_in_text(ok_char: str) -> None:
    """Prose, not terminal control. These were refused while an answer was
    typed into a picker's single-line "Type something." row; an answer is now
    delivered as a multi-line message, so a paragraph is an ordinary answer."""
    assert QuestionAnswerItem(text=f"hello{ok_char}world").text == f"hello{ok_char}world"


def test_question_answer_item_accepts_a_choice_and_a_note_together() -> None:
    """The composition the picker grammar could not express: "option B, and
    here is why". Refusing it made a human choose between answering the
    question and qualifying the answer."""
    item = QuestionAnswerItem(selected_indexes=[1], text="but only for staging")
    assert item.selected_indexes == [1]
    assert item.text == "but only for staging"


def test_question_answer_item_rejects_an_answer_that_says_nothing() -> None:
    with pytest.raises(ValidationError, match="selected_indexes, text, or both"):
        QuestionAnswerItem()


def test_answer_question_dismisses_the_prompt_then_delivers_the_batch_as_text(
    manager: WorkspaceManager, fake_tmux: FakeTmux, sidecar_dir: Path
) -> None:
    state = _create(manager)
    sid = str(state.agent_session_id)
    _capture(sidecar_dir, sid)
    fake_tmux.snapshots[f"{state.tmux_session}:agent"] = "Claude is working"
    events: list[WorkspaceEvent] = []
    manager.subscribe(events.append)

    manager.answer_question(state.id, _request(sid))

    pane = f"{state.tmux_session}:agent"
    # Escape first: the composer that receives the text does not exist until the
    # question widget has been dismissed.
    assert fake_tmux.escapes == [pane]
    assert not fake_tmux.sent_keys, "no picker is driven any more"
    ((target, text),) = fake_tmux.sent_texts
    assert target == pane
    assert '<grove-instruction kind="question-answers">' in text
    assert "Which color?" in text
    assert "Blue" in text
    answered = [e for e in events if e.kind == "question_answered"]
    assert len(answered) == 1
    assert answered[0].detail["tool_use_id"] == "toolu_1"
    assert answered[0].detail["target"] == pane
    assert answered[0].detail["answers"] == "1"


def test_answer_question_answers_a_whole_mixed_batch_including_multiselect(
    manager: WorkspaceManager,
    fake_tmux: FakeTmux,
    sidecar_dir: Path,
) -> None:
    """The batch shape that the keystroke driver could never answer.

    Measured against the picker grammar, four questions with a multiSelect
    among them delivered the first answer correctly, skipped the multiSelect
    entirely and collapsed the rest onto option 1 — reported as success. One
    message carries every answer in order, so the failure mode has no analogue.
    """
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
                    {"question": "Starter?", "options": [{"label": "Yes"}, {"label": "No"}]},
                    {
                        "question": "Toppings?",
                        "multiSelect": True,
                        "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}],
                    },
                    {"question": "Dessert?", "options": [{"label": "Cake"}, {"label": "Fruit"}]},
                ]
            },
        },
        sidecar_dir=sidecar_dir,
        tmux_pane=None,
        now=datetime.now(tz=UTC),
    )

    manager.answer_question(
        state.id,
        _request(
            sid,
            tool_use_id="toolu_m",
            answers=[
                QuestionAnswerItem(selected_indexes=[1]),
                QuestionAnswerItem(selected_indexes=[0, 2]),
                QuestionAnswerItem(selected_indexes=[1], text="something light"),
            ],
        ),
    )

    ((_target, text),) = fake_tmux.sent_texts
    assert "A, C" in text, "the multiSelect's several labels ride one answer line"
    assert "No" in text
    assert "Fruit" in text
    assert "something light" in text, "a note beside a choice is delivered too"


def test_answer_question_out_of_range_index_is_invalid_and_never_touches_the_pane(
    manager: WorkspaceManager, fake_tmux: FakeTmux, sidecar_dir: Path
) -> None:
    state = _create(manager)
    sid = str(state.agent_session_id)
    _capture(sidecar_dir, sid)

    with pytest.raises(QuestionAnswerInvalid, match="offers 2"):
        manager.answer_question(
            state.id, _request(sid, answers=[QuestionAnswerItem(selected_indexes=[7])])
        )
    assert fake_tmux.sent_texts == []
    assert fake_tmux.escapes == []


def test_answer_question_stale_tool_use_id_is_not_pending_and_never_sends(
    manager: WorkspaceManager, fake_tmux: FakeTmux, sidecar_dir: Path
) -> None:
    state = _create(manager)
    sid = str(state.agent_session_id)
    _capture(sidecar_dir, sid, tool_use_id="toolu_1")

    with pytest.raises(QuestionNotPending, match="toolu_1"):
        manager.answer_question(state.id, _request(sid, tool_use_id="toolu_OTHER"))
    assert fake_tmux.sent_texts == []
    assert fake_tmux.escapes == []


def test_answer_question_foreign_session_id_is_not_pending_and_never_sends(
    manager: WorkspaceManager, fake_tmux: FakeTmux, sidecar_dir: Path
) -> None:
    """A session_id that isn't this workspace's minted agent session must be
    refused before the sidecar is even read — otherwise a captured question
    belonging to a different workspace (or a second session of this one)
    would drive keystrokes into THIS workspace's pane."""
    state = _create(manager)
    sid = str(state.agent_session_id)
    _capture(sidecar_dir, sid)
    foreign_sid = sid + "-foreign"
    _capture(sidecar_dir, foreign_sid)

    with pytest.raises(QuestionNotPending, match="not the agent session bound to"):
        manager.answer_question(state.id, _request(foreign_sid))
    assert fake_tmux.sent_texts == []
    assert fake_tmux.escapes == []


def test_answer_question_no_capture_is_not_pending(
    manager: WorkspaceManager, fake_tmux: FakeTmux, sidecar_dir: Path
) -> None:
    state = _create(manager)
    sid = str(state.agent_session_id)

    with pytest.raises(QuestionNotPending, match="no pending question"):
        manager.answer_question(state.id, _request(sid))
    assert fake_tmux.sent_texts == []
    assert fake_tmux.escapes == []


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
    assert fake_tmux.sent_texts == []
    assert fake_tmux.escapes == []


def test_answer_question_no_pane_is_pane_not_found_and_never_sends(
    manager: WorkspaceManager, fake_tmux: FakeTmux, sidecar_dir: Path
) -> None:
    state = _create(manager)
    sid = str(state.agent_session_id)
    _capture(sidecar_dir, sid)
    fake_tmux.windows[state.tmux_session] = []  # session up, zero windows

    with pytest.raises(PaneNotFound):
        manager.answer_question(state.id, _request(sid))
    assert fake_tmux.sent_texts == []
    assert fake_tmux.escapes == []


_PLAN_INPUT = {"plan": "# Add a health endpoint\n\n- Add `health.py`\n- Register it"}


def _capture_plan(sidecar_dir: Path, session_id: str, tool_use_id: str = "toolu_plan") -> None:
    """Capture a pending ExitPlanMode through the real hook path."""
    ClaudeHook.record_event(
        {
            "hook_event_name": "PreToolUse",
            "session_id": session_id,
            "tool_name": "ExitPlanMode",
            "tool_use_id": tool_use_id,
            "tool_input": _PLAN_INPUT,
        },
        sidecar_dir=sidecar_dir,
        tmux_pane=None,
        now=datetime.now(tz=UTC),
    )


def test_answering_a_plan_selects_its_row_and_never_escapes_or_types(
    manager: WorkspaceManager, fake_tmux: FakeTmux, sidecar_dir: Path
) -> None:
    """A plan is answered by landing on a dialog row, not by prose.

    This is the whole fix, and every clause is load-bearing. Measured on Claude
    Code 2.1.270: Escape on a plan dialog is that dialog's REJECT — it records
    ``User rejected Claude's plan`` — so the dismiss-and-restate path that is
    right for every other question actively rejected the thing it claimed to
    approve, while the daemon answered 204. Typing is wrong for the same reason
    the row is right: the destination permission mode is chosen by *which row*,
    and no sentence can express it.
    """
    state = _create(manager)
    sid = str(state.agent_session_id)
    _capture_plan(sidecar_dir, sid)
    events: list[WorkspaceEvent] = []
    manager.subscribe(events.append)

    # Row 2 ("approve, approving edits as they come") — one Down, then Enter.
    manager.answer_question(
        state.id,
        _request(sid, tool_use_id="toolu_plan", answers=[QuestionAnswerItem(selected_indexes=[1])]),
    )

    pane = f"{state.tmux_session}:agent"
    assert fake_tmux.escapes == [], "Escape is this dialog's REJECT — never send it"
    assert fake_tmux.sent_texts == [], "a mode choice is a row, not a sentence"
    ((target, keys),) = fake_tmux.sent_keys
    assert target == pane
    assert [str(key) for key in keys] == ["Down", "Enter"]
    answered = [e for e in events if e.kind == "question_answered"]
    assert answered[0].detail["delivery"] == "selection"
    assert answered[0].detail["option"] == "2"


def test_answering_the_first_plan_row_sends_enter_alone(
    manager: WorkspaceManager, fake_tmux: FakeTmux, sidecar_dir: Path
) -> None:
    """Index 0 is where the cursor already sits, so it needs no movement.

    Pinned separately because an off-by-one here approves a DIFFERENT mode than
    the reader picked — the failure is silent and the wrong outcome is a real,
    plausible one.
    """
    state = _create(manager)
    sid = str(state.agent_session_id)
    _capture_plan(sidecar_dir, sid)

    manager.answer_question(
        state.id,
        _request(sid, tool_use_id="toolu_plan", answers=[QuestionAnswerItem(selected_indexes=[0])]),
    )

    ((_, keys),) = fake_tmux.sent_keys
    assert [str(key) for key in keys] == ["Enter"]


def test_a_plan_answer_naming_no_row_is_refused_before_the_pane(
    manager: WorkspaceManager, fake_tmux: FakeTmux, sidecar_dir: Path
) -> None:
    """ "No row" is not something a caller can land on, so it is a 422, not keys.

    Without this the keystrokes would be a bare Enter, silently approving
    whichever row the cursor happened to be over.
    """
    state = _create(manager)
    sid = str(state.agent_session_id)
    _capture_plan(sidecar_dir, sid)

    with pytest.raises(QuestionAnswerInvalid):
        manager.answer_question(
            state.id,
            _request(
                sid,
                tool_use_id="toolu_plan",
                answers=[QuestionAnswerItem(text="yes please")],
            ),
        )
    assert fake_tmux.sent_keys == []
    assert fake_tmux.sent_texts == []
    assert fake_tmux.escapes == []
