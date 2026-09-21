"""Focused unit and Pilot coverage for the native-session controls screen."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Checkbox, TextArea

from grove.core.agents import AgentQuestion, AgentQuestionOption, SessionControls
from grove.tui.screens.native_controls import (
    NativeControlAction,
    NativeControlsScreen,
    _answer_request,
    _queue_status,
)


class _ControlsHost(App[None]):
    def __init__(self, screen: NativeControlsScreen) -> None:
        super().__init__()
        self._screen = screen
        self.result: NativeControlAction | None = None

    def compose(self) -> ComposeResult:
        return iter(())

    def on_mount(self) -> None:
        self.push_screen(self._screen, self._captured)

    def _captured(self, result: NativeControlAction | None) -> None:
        self.result = result


def _questions() -> tuple[AgentQuestion, ...]:
    return (
        AgentQuestion(
            id="ask#0",
            group_id="ask",
            kind="multi_select",
            prompt="Choose integrations",
            options=(
                AgentQuestionOption("Calendar"),
                AgentQuestionOption("Mail"),
                AgentQuestionOption("Chat"),
            ),
            multiselect=True,
        ),
        AgentQuestion(
            id="ask#1",
            group_id="ask",
            kind="free_text",
            prompt="Anything else?",
        ),
    )


def test_answer_request_preserves_multiselect_and_multiline_free_text() -> None:
    """The native answer payload preserves the provider-neutral batch shape."""
    request = _answer_request(
        _questions(),
        session_id="session-1",
        selected_indexes=((0, 2), ()),
        texts=("include both", "Line one\nLine two"),
    )

    assert request.session_id == "session-1"
    assert request.tool_use_id == "ask"
    assert request.answers[0].selected_indexes == [0, 2]
    assert request.answers[0].text == "include both"
    assert request.answers[1].selected_indexes is None
    assert request.answers[1].text == "Line one\nLine two"


def test_queue_status_distinguishes_unsupported_from_empty() -> None:
    """A client must never present an unobservable queue as empty."""
    assert _queue_status((), supported=False) == "queue unavailable"
    assert _queue_status((), supported=True) == "queue empty"


@pytest.mark.asyncio
async def test_answer_modal_submits_multiselect_and_multiline_draft() -> None:
    """The question form returns the exact shared request after a multiline draft."""
    app = _ControlsHost(
        NativeControlsScreen(
            workspace_title="native task",
            controls=SessionControls.empty(),
            queue=(),
            queue_supported=False,
            questions=_questions(),
            session_id="session-1",
        )
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#answer-0-0", Checkbox).value = True
        screen.query_one("#answer-0-2", Checkbox).value = True
        screen.query_one("#answer-text-0", TextArea).text = "include both"
        screen.query_one("#answer-text-1", TextArea).text = "Line one\nLine two"
        screen._submit_answers()
        await pilot.pause()
        await pilot.pause()

    assert app.result is not None
    assert app.result.kind == "answer"
    assert app.result.answer is not None
    assert app.result.answer.answers[0].selected_indexes == [0, 2]
    assert app.result.answer.answers[0].text == "include both"
    assert app.result.answer.answers[1].text == "Line one\nLine two"
