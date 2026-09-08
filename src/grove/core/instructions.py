"""Grove's own voice inside an agent's prompt — one XML envelope, three bodies.

Grove puts words in front of an agent from three directions: the first-turn
brief that says where it is, the answers a human gave a question in Grove
rather than in the terminal, and the files a human attached to a message. All
three arrive on the same channel a *human* uses, so without a marker the agent
cannot tell Grove's sentence from its user's — and neither can the user reading
their own transcript back.

So everything Grove appends is fenced in one tag, ``<grove-instruction>``, with
a ``kind`` naming which of the three it is. One tag rather than three because
the reader's question is "did a person write this or did the tool", and that is
answered by the fence; *which* kind is a detail inside it.

**Rendering is shape, never semantics.** A body forwards what the human chose
verbatim and states the one fact the agent cannot observe (the question was
dismissed in the terminal; the attachment is at this path). It never
paraphrases a choice, ranks options or tells the model what to conclude.

**Every body is plain text and every renderer is pure**, because the delivery
side differs per runtime — typed into a tmux pane, handed to a native channel,
or printed by a hook — and a renderer that knew which would have to be three.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from grove.core.agents.model import AgentQuestion, AnswerSelection

# Which of Grove's three voices a block carries. A closed set because it drives
# the body renderers below and because it is published to the agent verbatim —
# a new kind is a new sentence a model reads, not a free-form label.
InstructionKind = Literal["workspace", "question-answers", "attachments"]


class GroveInstruction:
    """The envelope, and the three bodies that go inside it.

    All-static: there is no per-instance state, only a fence and the text that
    goes in it. Callers compose (``append``) rather than concatenate, so the
    blank line separating a human's own words from Grove's is decided once.
    """

    TAG: Final = "grove-instruction"
    """The one fence. Deliberately a single tag with a ``kind`` attribute rather
    than one tag per body: a reader — human or model — asks "is this Grove
    talking", and that has to be answerable without knowing the vocabulary."""

    @classmethod
    def wrap(cls, kind: InstructionKind, body: str) -> str:
        """Fence *body* as a Grove instruction of *kind*.

        Returns the empty string for an empty body, so a caller can render
        unconditionally and an absent block costs nothing — the same
        absence-renders-as-nothing rule the clients follow.
        """
        text = body.strip()
        if not text:
            return ""
        return f'<{cls.TAG} kind="{kind}">\n{text}\n</{cls.TAG}>'

    @staticmethod
    def append(prompt: str, block: str) -> str:
        """Put *block* after a human's *prompt*, blank line between.

        Either side may be empty: a message with no attachments is its own
        text, and a Grove-authored delivery (an answer batch) has no human text
        in front of it at all.
        """
        parts = [part for part in (prompt.strip(), block.strip()) if part]
        return "\n\n".join(parts)

    @classmethod
    def workspace(cls, brief: str, facts: Sequence[tuple[str, str]]) -> str:
        """The first-turn brief plus this workspace's own specification.

        The brief says what a Grove workspace *is*; the facts say which one this
        is. They ride together because separating them reproduces the original
        failure — an agent that reads "you are in a Grove workspace" and then
        looks at an ordinary repository checkout concludes the note is about
        somebody else. Naming the placement, the branch and the phase file
        outright is what makes the claim checkable instead of deniable.
        """
        lines = [brief.strip(), "", "This workspace:"]
        lines += [f"- {label}: {value}" for label, value in facts if value]
        return cls.wrap("workspace", "\n".join(lines))

    @classmethod
    def answers(
        cls,
        questions: Sequence[AgentQuestion],
        selections: Sequence[AnswerSelection],
    ) -> str:
        """The answers to a question batch, as one deliverable message.

        This is the whole of Grove's answer path: rather than drive the
        provider's picker widget by keystroke, Grove dismisses it and restates
        the batch as text. That is why the body must say the question was
        dismissed — the agent has just seen its own tool call cancelled, and
        without this it reads that as the human refusing to answer.

        Pure and defensive, because the captured payload and the client's plan
        are validated elsewhere and a mismatch must not raise on the render
        path: an index out of range is skipped, and a batch that renders to
        nothing degrades to a bare acknowledgement rather than an empty
        delivery.
        """
        lines: list[str] = [
            "The user answered your question(s) in Grove rather than in the terminal, "
            "so the on-screen prompt was dismissed and your tool call was cancelled. "
            "These are their answers — act on them and do not ask again."
        ]
        for i, selection in enumerate(selections):
            question = questions[i] if i < len(questions) else None
            prompt = (question.prompt if question else "").strip()
            lines.append("")
            lines.append(f"Q{i + 1}. {prompt}" if prompt else f"Q{i + 1}.")
            chosen = _labels(question, selection.indexes)
            if chosen:
                lines.append(f"A{i + 1}. {', '.join(chosen)}")
            if selection.text:
                # Rendered on its own line and labelled, so a free-text answer
                # standing beside chosen options reads as an addition rather
                # than as a correction of them.
                lines.append(f"{'A' if not chosen else 'Also'}{i + 1}. {selection.text.strip()}")
            if not chosen and not selection.text:
                lines.append(f"A{i + 1}. (no answer given)")
        return cls.wrap("question-answers", "\n".join(lines))

    @classmethod
    def attachments(cls, files: Iterable[tuple[str, str, int]]) -> str:
        """Files a human attached, as ``(name, path, size)`` in the AGENT's namespace.

        The path is the whole payload: an agent reads a file with the tools it
        already has, so an attachment needs no new capability, only an address
        it can open. Grove never describes or transcribes the content — that is
        the model's job and guessing at it would be semantics.

        **THIS BLOCK'S SHAPE IS A WIRE CONTRACT, not merely prose for a model.**
        ``SessionTurnView`` has no structured attachment field, so the appended
        text IS the record of what a human attached — and the webapp reads it
        back (``webapp/lib/grove/adapters/attachments.ts``) to render each file
        on the sent message rather than a paragraph of paths at the bottom of
        it. The ``- <name> — <path> (<size> bytes)`` row and the ``attachments``
        kind are what that reader matches on — the size suffix is optional to
        the reader, so a transcript written before it existed still renders
        its files, just without a size. Changing the row silently empties the
        transcript's attachment rows: the Python suite stays green because the
        engine is self-consistent, and the vitest suite stays green because its
        fixtures are written against whatever this file used to emit. The
        round-trip is pinned in ``tests/core/test_instructions.py``; a
        deliberate format change edits the reader in the same commit.
        """
        rows = [f"- {name} — {path} ({size} bytes)" for name, path, size in files if path]
        if not rows:
            return ""
        count = f"{len(rows)} file{'' if len(rows) == 1 else 's'}"
        return cls.wrap(
            "attachments",
            "\n".join(
                [
                    f"The user attached {count} to the message above. "
                    "Each is on disk in this workspace at the path shown; "
                    "read whichever you need.",
                    "",
                    *rows,
                ]
            ),
        )


def _labels(question: AgentQuestion | None, indexes: Sequence[int]) -> list[str]:
    """The option labels *indexes* name, skipping any the question does not have."""
    if question is None:
        return []
    return [question.options[i].label for i in indexes if 0 <= i < len(question.options)]


__all__ = ["GroveInstruction", "InstructionKind"]
