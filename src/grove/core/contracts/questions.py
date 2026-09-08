"""Wire shapes for the agent-question surface — the read views and the answer request.

``AgentQuestion(View)`` is the structured ask-the-human payload. It
rides the fetch-on-demand ``/turns`` pipeline AND the live activity
stream — so it lives here, in a module that neither the ``activity`` nor the
``sessions`` contracts depend on through each other. That placement is what
breaks the ``activity`` ↔ ``sessions`` import cycle a live question field would
otherwise create (``sessions`` already imports ``activity``; ``activity`` needs
the question view; the shared shape belongs below both).

The answer request is the write side: what a client POSTs to drive a
pending question to resolution. Structural validation lives on the model
(exactly one of indexes/text per item, non-blank text); the per-question kind
rules are checked against the *captured* payload in the manager, since only it
holds the questions the answer must fit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from grove.core.agents import AgentQuestion

# Mirrors ``grove.core.agents.AgentQuestionKind``. Duplicated (not imported) so a
# contracts import never drags the httpx-heavy adapter layer in — the engine
# dataclasses ride ``TYPE_CHECKING`` only. The webapp codegen drift-check is the
# wire DRY anchor across the boundary.
#
# ``plan_approval`` is the one kind whose answer is a POSITION rather than a
# payload: it names a row in the agent's own dialog, because approving a plan is
# a mode transition only that dialog can perform. It is split out from
# ``confirm`` rather than sharing it because the two take different delivery
# paths and a client must be able to tell them apart before it renders a
# control — a kind that meant "optionless yes/no" for one tool and "pick a mode"
# for another would make the degraded rendering indistinguishable from the right
# one. Kept in the same closed set so an unknown kind is still impossible.
AgentQuestionKind = Literal[
    "single_select", "multi_select", "free_text", "confirm", "plan_approval"
]

# Per-entry ceiling so one mega-prompt can't ship a multi-MB JSON body; the
# trailing ellipsis is the trim signal (no separate `truncated` flag). Shared
# with the other session views via import.
_ENTRY_TEXT_CAP = 4000


def _truncate(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


class AgentQuestionOptionView(BaseModel):
    """Wire mirror of ``grove.core.agents.AgentQuestionOption``."""

    model_config = ConfigDict(frozen=True)

    label: str
    description: str | None = None


class AgentQuestionView(BaseModel):
    """Wire mirror of ``grove.core.agents.AgentQuestion``.

    The structured payload a transcript renderer draws as a choice card. ``id``
    /``group_id`` are the stable answer-back addresses a client keys on — for the
    live pending question, ``group_id`` is the ``tool_use_id`` the answer
    POST must carry back.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    group_id: str
    kind: AgentQuestionKind
    prompt: str
    header: str | None = None
    options: list[AgentQuestionOptionView] = []
    multiselect: bool = False
    answered: bool = False
    answer: str | None = None
    source_tool: str = ""

    @classmethod
    def from_question(cls, q: AgentQuestion) -> AgentQuestionView:
        return cls(
            id=q.id,
            group_id=q.group_id,
            kind=q.kind,
            prompt=_truncate(q.prompt, _ENTRY_TEXT_CAP),
            header=q.header,
            options=[
                AgentQuestionOptionView(label=o.label, description=o.description) for o in q.options
            ],
            multiselect=q.multiselect,
            answered=q.answered,
            answer=q.answer,
            source_tool=q.source_tool,
        )


class QuestionAnswerItem(BaseModel):
    """One question's answer: chosen option indexes, free text, or both.

    ``selected_indexes`` picks predefined options (0-based, in option order);
    ``text`` is anything the human wanted to say that the options did not
    cover. **Either may be present and they compose** — "option B, and here is
    why" is a real answer, and a wire that could only carry one of the two made
    a human choose between answering the question and qualifying the answer.

    That composition is what the delivery redesign bought. While an answer was
    typed into the provider's own picker widget, the shape of the answer was
    dictated by the shape of that widget: free text existed only as the
    single-select picker's synthetic "Type something." row, so it was
    single-select-only and mutually exclusive with a choice. Grove now dismisses
    the picker and restates the whole batch as prose, so the widget's grammar
    constrains nothing and neither rule survives.

    An item carrying neither is still refused — an answer that says nothing is a
    question the human has not answered, and the caller should send no item at
    all rather than an empty one. The remaining per-question rules (one item per
    captured question, indexes that name real options) need the question itself,
    so the manager checks them against the captured payload.

    ``text`` may contain newlines and tabs; every other C0 byte and DEL are
    refused. The distinction is between prose and terminal control: an ESC in a
    payload typed into a pane cancels whatever is on screen, and the rest of C0
    is escape-sequence material, while a line break is just how people write
    more than one sentence.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    selected_indexes: list[int] | None = None
    text: str | None = None

    @model_validator(mode="after")
    def _says_something(self) -> QuestionAnswerItem:
        if self.selected_indexes is None and self.text is None:
            raise ValueError("each answer sets selected_indexes, text, or both")
        if self.selected_indexes is not None:
            if not self.selected_indexes:
                raise ValueError("selected_indexes must be non-empty")
            if any(i < 0 for i in self.selected_indexes):
                raise ValueError("selected_indexes must be non-negative")
        if self.text is not None:
            if not self.text.strip():
                raise ValueError("text must be non-blank")
            if any((ord(c) < 0x20 and c not in "\n\t") or ord(c) == 0x7F for c in self.text):
                raise ValueError(
                    "text must not contain terminal control characters "
                    "(newlines and tabs are fine; the rest of C0 and DEL are not)"
                )
        return self


class QuestionAnswerRequest(BaseModel):
    """POST body for driving a pending ``AskUserQuestion`` to resolution.

    ``tool_use_id`` is the group answer-back address captured at ask-time; the
    daemon requires it to still match the standing capture (409 on a stale id).
    ``answers`` is one item per question, in the captured order; the manager
    validates the length and the option indexes against the captured payload
    (422), dismisses the on-screen prompt, and delivers the whole batch back as
    one Grove-fenced message.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str = Field(min_length=1)
    tool_use_id: str = Field(min_length=1)
    answers: list[QuestionAnswerItem] = Field(min_length=1)
