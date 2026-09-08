"""``AgentQuestion.plan_mismatch`` — the two rules a wire model cannot check.

Both need the CAPTURED questions, which the client never sends back: is there
one answer per question, and does every index name an option that exists. Pure
over its inputs, so it is testable without a workspace, a pane or a sidecar.

The rules that are deliberately ABSENT matter as much as the ones present, and
each one has a test here saying so. Free text only on a single-select, no text
beside a choice, `confirm` unanswerable, multi-select refused — every one of
those was a property of the provider's picker WIDGET, not of the question, and
they left with the keystroke driver. A regression that quietly reinstates any
of them would look like validation rather than like the capability loss it is.
"""

from __future__ import annotations

import pytest

from grove.core.agents.model import AgentQuestion, AgentQuestionOption, AnswerSelection

_SINGLE = AgentQuestion(
    id="c#0",
    group_id="c",
    kind="single_select",
    prompt="Which?",
    options=(AgentQuestionOption(label="A"), AgentQuestionOption(label="B")),
)
_MULTI = AgentQuestion(
    id="c#1",
    group_id="c",
    kind="multi_select",
    prompt="Which ones?",
    options=(
        AgentQuestionOption(label="X"),
        AgentQuestionOption(label="Y"),
        AgentQuestionOption(label="Z"),
    ),
    multiselect=True,
)
_CONFIRM = AgentQuestion(id="c#2", group_id="c", kind="confirm", prompt="Approve this plan?")
_FREE = AgentQuestion(id="c#3", group_id="c", kind="free_text", prompt="Name it.")


def test_a_matching_plan_has_no_mismatch() -> None:
    assert (
        AgentQuestion.plan_mismatch((_SINGLE, _MULTI), [AnswerSelection(indexes=(0,))] * 2) is None
    )


@pytest.mark.parametrize(
    ("selections", "expected"),
    [
        pytest.param([], "expected 1 answer", id="none"),
        pytest.param([AnswerSelection(indexes=(0,))] * 2, "expected 1 answer", id="too-many"),
    ],
)
def test_the_plan_must_carry_one_item_per_question(
    selections: list[AnswerSelection], expected: str
) -> None:
    """Answers are positional, so a plan of the wrong length does not merely
    lose an answer — it shifts every later one onto the wrong question."""
    assert expected in (AgentQuestion.plan_mismatch((_SINGLE,), selections) or "")


def test_an_index_naming_an_option_that_does_not_exist_is_a_mismatch() -> None:
    mismatch = AgentQuestion.plan_mismatch((_SINGLE,), [AnswerSelection(indexes=(4,))])

    assert mismatch is not None
    assert "selects option 5" in mismatch and "offers 2" in mismatch


def test_nothing_captured_is_a_mismatch_rather_than_a_vacuous_pass() -> None:
    """A capture that normalized to no questions cannot be answered, and
    reporting success would deliver a message about nothing."""
    assert AgentQuestion.plan_mismatch((), []) is not None


# ─── the rules that left with the picker ────────────────────────────────────


def test_free_text_is_accepted_on_every_kind() -> None:
    """Free text used to exist only as the single-select picker's synthetic
    "Type something." row, so it was single-select-only. An answer is now prose
    and every kind takes one."""
    for question in (_SINGLE, _MULTI, _CONFIRM, _FREE):
        assert AgentQuestion.plan_mismatch((question,), [AnswerSelection(text="anything")]) is None


def test_a_choice_and_a_note_together_are_accepted() -> None:
    assert (
        AgentQuestion.plan_mismatch((_MULTI,), [AnswerSelection(indexes=(0, 1), text="but not Z")])
        is None
    )


def test_multi_select_is_answerable() -> None:
    """It rendered as answerable in the UI and 422'd on submit for as long as
    the keystroke driver existed, because its focus and submit transitions were
    never verified. There is no widget to transition any more."""
    assert AgentQuestion.plan_mismatch((_MULTI,), [AnswerSelection(indexes=(0, 2))]) is None


def test_several_indexes_on_a_single_select_are_not_refused_here() -> None:
    """Deliberately not a rule. The picker could only physically select one, so
    the driver had to refuse; a rendered answer naming two labels is merely a
    human who meant both, and second-guessing that is model semantics.
    """
    assert AgentQuestion.plan_mismatch((_SINGLE,), [AnswerSelection(indexes=(0, 1))]) is None
