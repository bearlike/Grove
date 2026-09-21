"""NativeControlsScreen — one compact control surface for a live native session.

The screen deliberately owns only form state. The list screen owns every manager
call, so blocking reads and writes stay off Textual's UI thread and typed manager
failures keep the existing status-bar flash behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Label, Select, Static, TextArea

from grove.core.agents import AgentQuestion, QueuedMessage, SessionControl, SessionControls
from grove.core.contracts.questions import QuestionAnswerItem, QuestionAnswerRequest
from grove.tui.screens._modal import GroveModal
from grove.tui.widgets.footer import ContextualFooter, FooterKey


@dataclass(frozen=True, slots=True)
class NativeControlAction:
    """One operator intent returned to the list screen's manager-call edge."""

    kind: Literal["interrupt", "compact", "model", "command", "answer"]
    value: str | None = None
    answer: QuestionAnswerRequest | None = None


def _queue_status(messages: tuple[QueuedMessage, ...], *, supported: bool) -> str:
    """Say whether an empty queue is known-empty or simply unobservable."""
    if not supported:
        return "queue unavailable"
    if not messages:
        return "queue empty"
    return f"queue · {len(messages)} waiting"


def _answer_request(
    questions: tuple[AgentQuestion, ...],
    *,
    session_id: str,
    selected_indexes: tuple[tuple[int, ...], ...],
    texts: tuple[str, ...],
) -> QuestionAnswerRequest:
    """Build the shared wire request without reducing choices to provider keys."""
    if not questions or len(questions) != len(selected_indexes) or len(questions) != len(texts):
        raise ValueError("answers must match the pending question batch")
    group_id = questions[0].group_id
    if any(question.group_id != group_id for question in questions):
        raise ValueError("pending questions must share one answer group")
    return QuestionAnswerRequest(
        session_id=session_id,
        tool_use_id=group_id,
        answers=[
            QuestionAnswerItem(
                selected_indexes=list(indexes) or None,
                text=text if text.strip() else None,
            )
            for indexes, text in zip(selected_indexes, texts, strict=True)
        ],
    )


class NativeControlsScreen(GroveModal[NativeControlAction | None]):
    """Compact controls, queue inspection and structured question answers.

    The selected workspace's list screen has already fetched this snapshot on a
    worker. Keeping the modal as a plain form means it cannot race the session
    while it is mounted, and it preserves the user's multiline draft until they
    explicitly submit or cancel it.
    """

    DEFAULT_CSS = """
    NativeControlsScreen .grove-dialog {
        width: 78;
        max-height: 90%;
    }
    NativeControlsScreen #native-controls-scroll {
        height: auto;
        max-height: 28;
    }
    NativeControlsScreen .native-controls-section {
        margin-top: 1;
    }
    NativeControlsScreen .native-controls-question {
        border: round $secondary;
        padding: 0 1;
        margin-top: 1;
    }
    NativeControlsScreen .native-controls-answer {
        height: 5;
        margin-top: 1;
    }
    NativeControlsScreen .-hidden {
        display: none;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        *,
        workspace_title: str,
        controls: SessionControls,
        queue: tuple[QueuedMessage, ...],
        queue_supported: bool,
        questions: tuple[AgentQuestion, ...] = (),
        session_id: str | None = None,
    ) -> None:
        super().__init__()
        self._workspace_title = workspace_title
        self._controls = controls
        self._queue = queue
        self._queue_supported = queue_supported
        self._questions = questions
        self._session_id = session_id

    def compose(self) -> ComposeResult:
        commands = (*self._controls.commands, *self._controls.skills)
        with Vertical(classes="grove-dialog"):
            yield Label("Native session controls", classes="grove-dialog-title")
            yield Label(f"controls for {self._workspace_title}", markup=False)
            with Horizontal(classes="grove-dialog-buttons"):
                yield Button("Interrupt", id="interrupt", variant="error")
                yield Button("Compact", id="compact", variant="primary")
                yield Button("Close", id="cancel", variant="default")
            with VerticalScroll(id="native-controls-scroll"):
                yield Static(
                    f"model · {self._controls.current_model or 'not reported'}",
                    classes="native-controls-section",
                )
                if self._controls.models:
                    yield Select(
                        [(model, model) for model in self._controls.models],
                        value=self._controls.current_model
                        if self._controls.current_model in self._controls.models
                        else Select.NULL,
                        prompt="Switch model",
                        id="model",
                    )
                    yield Button("Switch model", id="switch-model")
                if commands:
                    yield Label("Commands", classes="native-controls-section")
                    yield Select(
                        [(_control_label(command), command.name) for command in commands],
                        prompt="Choose command or skill",
                        id="command",
                    )
                    yield Button("Run command", id="run-command")
                yield Label("Queue", classes="native-controls-section")
                yield Static(
                    _queue_status(self._queue, supported=self._queue_supported), id="queue-status"
                )
                for message in self._queue:
                    yield Static(f"{message.position + 1}. {message.text}", markup=False)
                if self._controls.permission_mode:
                    yield Static(
                        f"permissions · {self._controls.permission_mode}",
                        classes="native-controls-section grove-detail",
                    )
                if self._questions:
                    yield Label("Pending questions", classes="native-controls-section")
                    for index, question in enumerate(self._questions):
                        with Vertical(classes="native-controls-question"):
                            if question.header:
                                yield Label(question.header, markup=False)
                            yield Static(question.prompt, markup=False)
                            for option_index, option in enumerate(question.options):
                                label = option.label
                                if option.description:
                                    label = f"{label} — {option.description}"
                                yield Checkbox(label, id=f"answer-{index}-{option_index}")
                            placeholder = "Optional detail" if question.options else "Your answer"
                            yield TextArea(
                                placeholder=placeholder,
                                id=f"answer-text-{index}",
                                classes="native-controls-answer",
                            )
                    yield Button("Submit answers", id="submit-answers", variant="primary")
        yield ContextualFooter()

    def on_mount(self) -> None:
        keys = [
            FooterKey("escape", "Close"),
            FooterKey("enter", "Activate"),
        ]
        self.query_one(ContextualFooter).set_keys(keys)
        self.query_one("#interrupt", Button).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "cancel":
            self.dismiss(None)
        elif button_id == "interrupt":
            self.dismiss(NativeControlAction("interrupt"))
        elif button_id == "compact":
            self.dismiss(NativeControlAction("compact"))
        elif button_id == "switch-model":
            model = self.query_one("#model", Select).value
            if isinstance(model, str):
                self.dismiss(NativeControlAction("model", value=model))
            else:
                self.app.bell()
        elif button_id == "run-command":
            command = self.query_one("#command", Select).value
            if isinstance(command, str):
                self.dismiss(NativeControlAction("command", value=command))
            else:
                self.app.bell()
        elif button_id == "submit-answers":
            self._submit_answers()

    def _submit_answers(self) -> None:
        if self._session_id is None:
            self.app.bell()
            return
        selected_indexes = tuple(
            tuple(
                option_index
                for option_index, _option in enumerate(question.options)
                if self.query_one(f"#answer-{question_index}-{option_index}", Checkbox).value
            )
            for question_index, question in enumerate(self._questions)
        )
        texts = tuple(
            self.query_one(f"#answer-text-{index}", TextArea).text
            for index in range(len(self._questions))
        )
        try:
            request = _answer_request(
                self._questions,
                session_id=self._session_id,
                selected_indexes=selected_indexes,
                texts=texts,
            )
        except ValueError:
            self.app.bell()
            return
        self.dismiss(NativeControlAction("answer", answer=request))


def _control_label(control: SessionControl) -> str:
    """Keep command origin available without making it policy."""
    detail = f" — {control.detail}" if control.detail else ""
    return f"/{control.name} · {control.scope}{detail}"
