"""The Grove prompt envelope: the fence, and the three bodies inside it.

Everything Grove appends to an agent's prompt is fenced in one
``<grove-instruction>`` tag, so an agent — and a human reading their own
transcript back — can tell the tool's sentence from the user's. These tests pin
the fence itself, then each body's one job: state the fact the agent cannot
observe, and forward the human's own words verbatim.

Rendering is pure and defensive by contract. It runs on a delivery path where a
raise would cost the whole answer, so a malformed input degrades rather than
throws — and the degradation has to be *visible*, never an empty message.
"""

from __future__ import annotations

import pytest

from grove.core.agents.model import AgentQuestion, AgentQuestionOption, AnswerSelection
from grove.core.instructions import GroveInstruction


def _question(prompt: str, *labels: str, multiselect: bool = False) -> AgentQuestion:
    return AgentQuestion(
        id="toolu_1#0",
        group_id="toolu_1",
        kind="multi_select" if multiselect else "single_select",
        prompt=prompt,
        options=tuple(AgentQuestionOption(label=label) for label in labels),
        multiselect=multiselect,
    )


# ─── the fence ──────────────────────────────────────────────────────────────


def test_wrap_fences_the_body_with_its_kind() -> None:
    assert GroveInstruction.wrap("attachments", "body") == (
        '<grove-instruction kind="attachments">\nbody\n</grove-instruction>'
    )


@pytest.mark.parametrize("empty", ["", "   ", "\n\t "])
def test_wrap_renders_nothing_for_an_empty_body(empty: str) -> None:
    """Absence renders as nothing, so a caller composes unconditionally and an
    absent block costs no stray tag for the model to interpret."""
    assert GroveInstruction.wrap("attachments", empty) == ""


def test_append_separates_a_humans_words_from_groves_with_one_blank_line() -> None:
    assert GroveInstruction.append("do the thing", "BLOCK") == "do the thing\n\nBLOCK"


@pytest.mark.parametrize(
    ("prompt", "block", "expected"),
    [
        ("just text", "", "just text"),
        ("", "BLOCK", "BLOCK"),
        ("", "", ""),
    ],
)
def test_append_never_leaves_a_dangling_separator(prompt: str, block: str, expected: str) -> None:
    """Either side may legitimately be empty — a message with no attachments,
    and a Grove-authored answer batch with no human text in front of it."""
    assert GroveInstruction.append(prompt, block) == expected


# ─── question answers ───────────────────────────────────────────────────────


def test_answers_states_that_the_prompt_was_dismissed() -> None:
    """The one fact the agent cannot observe. It has just seen its own tool call
    cancelled, and without this it reads that as the human refusing to answer."""
    text = GroveInstruction.answers(
        (_question("Which colour?", "Blue", "Green"),), [AnswerSelection(indexes=(0,))]
    )

    assert "dismissed" in text
    assert "cancelled" in text
    assert "do not ask again" in text


def test_answers_maps_indexes_to_the_option_labels_the_human_saw() -> None:
    text = GroveInstruction.answers(
        (_question("Which colour?", "Blue", "Green"),), [AnswerSelection(indexes=(1,))]
    )

    assert "Q1. Which colour?" in text
    assert "A1. Green" in text


def test_answers_joins_a_multiselect_into_one_answer_line() -> None:
    text = GroveInstruction.answers(
        (_question("Toppings?", "A", "B", "C", multiselect=True),),
        [AnswerSelection(indexes=(0, 2))],
    )

    assert "A1. A, C" in text


def test_answers_carries_a_note_alongside_a_choice() -> None:
    """The composition the picker grammar could not express, end to end: the
    choice reads as the answer and the note as an addition to it, never as a
    correction of it."""
    text = GroveInstruction.answers(
        (_question("Deploy now?", "Yes", "No"),),
        [AnswerSelection(indexes=(0,), text="but hold the migration")],
    )

    assert "A1. Yes" in text
    assert "Also1. but hold the migration" in text


def test_answers_renders_free_text_alone_as_the_answer() -> None:
    text = GroveInstruction.answers(
        (_question("What should I call it?"),), [AnswerSelection(text="Ada")]
    )

    assert "A1. Ada" in text
    assert "Also1." not in text


def test_answers_skips_an_out_of_range_index_without_raising() -> None:
    """Defensive on the delivery path: the plan is validated upstream, and a
    raise here would cost the whole answer rather than one label."""
    text = GroveInstruction.answers((_question("Pick", "Only"),), [AnswerSelection(indexes=(5,))])

    assert "A1. (no answer given)" in text


def test_answers_with_nothing_to_render_still_produces_a_delivery() -> None:
    """An empty string would be delivered as an empty steer, which is a message
    the agent never receives — worse than a bare acknowledgement."""
    text = GroveInstruction.answers((), [])

    assert text.startswith('<grove-instruction kind="question-answers">')
    assert "dismissed" in text


# ─── attachments ────────────────────────────────────────────────────────────


def test_attachments_lists_each_file_with_the_path_the_agent_can_open() -> None:
    text = GroveInstruction.attachments(
        [
            ("diagram.png", "/w/.grove/attachments/ab/diagram.png", 2048),
            ("notes.md", "/w/n/notes.md", 12),
        ]
    )

    assert '<grove-instruction kind="attachments">' in text
    assert "2 files" in text
    assert "- diagram.png — /w/.grove/attachments/ab/diagram.png (2048 bytes)" in text
    assert "- notes.md — /w/n/notes.md (12 bytes)" in text


def test_attachments_counts_one_file_in_the_singular() -> None:
    assert "1 file to" in GroveInstruction.attachments([("a.txt", "/w/a.txt", 1)])


def test_no_attachments_renders_nothing_at_all() -> None:
    """A message with no attachments must be byte-identical to what it was
    before this feature existed."""
    assert GroveInstruction.attachments([]) == ""


def test_an_attachment_with_no_resolvable_path_is_dropped() -> None:
    """A path is the entire payload — an entry without one names a file the
    agent cannot open, so listing it would be an instruction that fails."""
    assert GroveInstruction.attachments([("gone.png", "", 1)]) == ""


def test_the_attachment_block_keeps_the_exact_shape_the_webapp_parses() -> None:
    """The one assertion here that is about a CONSUMER rather than about content.

    ``SessionTurnView`` carries no structured attachment field, so this block is
    the record of what a human attached, and the webapp reads it back out
    (``webapp/lib/grove/adapters/attachments.ts``) to draw each file on the sent
    message. That makes the tag, the ``kind`` and the ``- <name> — <path>
    (<size> bytes)`` row a wire contract across a language boundary that no
    type checker spans.

    **Both suites would stay green if this drifted** — the engine because it is
    self-consistent, the browser because its fixtures are written against
    whatever this file used to emit — so the format is pinned literally here,
    with the reader named, and a deliberate change edits both in one commit.
    The em dash is load-bearing: the reader splits on it, and the engine's
    filename sanitizer cannot leave one inside a name.
    """
    rendered = GroveInstruction.attachments(
        [("notes.md", "/w/.grove/attachments/ab12/notes.md", 512)]
    )

    assert rendered.startswith('<grove-instruction kind="attachments">\n')
    assert rendered.endswith("\n</grove-instruction>")
    assert "\n- notes.md — /w/.grove/attachments/ab12/notes.md (512 bytes)\n" in rendered
    # Anchored at the end by the reader, so nothing may follow the closing tag.
    assert rendered.rstrip() == rendered


# ─── the workspace specification ────────────────────────────────────────────


def test_workspace_puts_the_brief_first_and_the_facts_under_it() -> None:
    """Order is load-bearing in one direction: Grove's own paragraphs establish
    what a workspace IS, and the record is read against that."""
    text = GroveInstruction.workspace("You are in a workspace.", [("id", "w1"), ("branch", "x")])

    assert text.startswith('<grove-instruction kind="workspace">\nYou are in a workspace.')
    assert "This workspace:\n- id: w1\n- branch: x" in text


def test_workspace_drops_a_fact_grove_does_not_hold() -> None:
    """An empty value is a fact Grove does not have; rendering the label anyway
    would invite the agent to treat absence as a measured answer."""
    text = GroveInstruction.workspace("brief", [("id", "w1"), ("description", "")])

    assert "description" not in text
