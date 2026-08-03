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
AgentQuestionKind = Literal["single_select", "multi_select", "free_text", "confirm"]

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
    """One question's answer: chosen option indexes XOR free text — exactly one.

    ``selected_indexes`` picks predefined options (0-based, in option order);
    ``text`` is a free-text ("Type something.") answer. No ``kind`` discriminator:
    a client sends exactly one key, so a ``model_validator`` enforces the XOR
    rather than a tagged union (a tag the webapp form does not carry). The
    single-vs-multi and free-text-only-on-single-select rules can't be checked
    here (they need the question) — the manager checks them against the captured
    payload.

    ``text`` is rejected outright if it carries any control byte (ord < 0x20 or
    0x7f, including tab/newline/ESC). The Claude adapter types ``text``
    verbatim into the pane via ``send-keys -l``; the whole design rests on a
    closed key vocabulary (digits, Tab, Enter) driving the picker deterministically,
    and a raw control byte reopens that surface (ESC cancels the question outright,
    CR/LF act as an early Enter mid-sequence and desync the positional driver).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    selected_indexes: list[int] | None = None
    text: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> QuestionAnswerItem:
        has_indexes = self.selected_indexes is not None
        has_text = self.text is not None
        if has_indexes == has_text:
            raise ValueError("each answer sets exactly one of selected_indexes or text")
        if self.selected_indexes is not None:
            if not self.selected_indexes:
                raise ValueError("selected_indexes must be non-empty")
            if any(i < 0 for i in self.selected_indexes):
                raise ValueError("selected_indexes must be non-negative")
        if self.text is not None:
            if not self.text.strip():
                raise ValueError("text must be non-blank")
            if any(ord(c) < 0x20 or ord(c) == 0x7F for c in self.text):
                raise ValueError(
                    "text must not contain control characters (the terminal free-text "
                    "input is single-line; tab/newline are rejected along with the rest)"
                )
        return self


class QuestionAnswerRequest(BaseModel):
    """POST body for driving a pending ``AskUserQuestion`` to resolution.

    ``tool_use_id`` is the group answer-back address captured at ask-time; the
    daemon requires it to still match the standing capture (409 on a stale id).
    ``answers`` is one item per question, in the captured order; the manager
    validates length + per-question kind rules against the captured payload
    (422), then the Claude adapter maps them to deterministic keystrokes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str = Field(min_length=1)
    tool_use_id: str = Field(min_length=1)
    answers: list[QuestionAnswerItem] = Field(min_length=1)
