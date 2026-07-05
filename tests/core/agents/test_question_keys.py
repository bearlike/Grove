"""ClaudeCodeAdapter.build_answer_keys — the AskUserQuestion keystroke grammar (#109).

Table-driven over every verified variant (on-host, Claude Code 2.1.x). The op
list is the deterministic contract the manager sends to ``tmux.send_keys``, so
each case pins the exact sequence — including the review-step rule (a trailing
Enter iff >1 question OR any multiSelect) and the rejections the manager maps to
422.
"""

from __future__ import annotations

import pytest

from grove.core.agents.claude_code import ClaudeCodeAdapter
from grove.core.agents.model import (
    AgentQuestion,
    AgentQuestionKind,
    AgentQuestionOption,
    AnswerSelection,
)
from grove.core.tmux import SendKey, SendOp


def _q(kind: AgentQuestionKind, n_options: int, *, multiselect: bool = False) -> AgentQuestion:
    return AgentQuestion(
        id="toolu_x#0",
        group_id="toolu_x",
        kind=kind,
        prompt="pick",
        options=tuple(AgentQuestionOption(label=chr(65 + i)) for i in range(n_options)),
        multiselect=multiselect,
    )


_SINGLE = _q("single_select", 3)
_SINGLE_2 = _q("single_select", 2)
_MULTI = _q("multi_select", 3, multiselect=True)


@pytest.mark.parametrize(
    ("questions", "answers", "expected"),
    [
        # A lone single-select submits directly on its digit — no review step.
        pytest.param([_SINGLE], [AnswerSelection(indexes=(0,))], ["1"], id="lone-single-first"),
        pytest.param([_SINGLE], [AnswerSelection(indexes=(2,))], ["3"], id="lone-single-last"),
        # Free-text answer on a single-select: the synthetic "Type something."
        # option (len+1), the text verbatim, then Enter submits. No extra review.
        pytest.param(
            [_SINGLE_2],
            [AnswerSelection(text="Kiwi")],
            ["3", "Kiwi", SendKey.ENTER],
            id="lone-single-freetext",
        ),
        # A lone multiSelect DOES have a review step (Tab → Submit → Enter).
        pytest.param(
            [_MULTI],
            [AnswerSelection(indexes=(0, 2))],
            ["1", "3", SendKey.TAB, SendKey.ENTER],
            id="lone-multi",
        ),
        # The headline verified sequence: single (idx 1) + multiSelect (idx 0,2)
        # → 2, 1, 3, Tab, then the review Enter.
        pytest.param(
            [_SINGLE, _MULTI],
            [AnswerSelection(indexes=(1,)), AnswerSelection(indexes=(0, 2))],
            ["2", "1", "3", SendKey.TAB, SendKey.ENTER],
            id="two-questions-single-plus-multi",
        ),
        # Two single-selects still get a review step (>1 question).
        pytest.param(
            [_SINGLE, _SINGLE_2],
            [AnswerSelection(indexes=(0,)), AnswerSelection(indexes=(1,))],
            ["1", "2", SendKey.ENTER],
            id="two-single-selects",
        ),
        # Free-text inside a multi-question batch: its own Enter advances, the
        # final review Enter submits.
        pytest.param(
            [_SINGLE_2, _SINGLE],
            [AnswerSelection(text="Other thing"), AnswerSelection(indexes=(2,))],
            ["3", "Other thing", SendKey.ENTER, "3", SendKey.ENTER],
            id="freetext-then-single",
        ),
        # Free-text as the LAST question of a multi-question batch: its own
        # Enter advances past it, then the batch-level review Enter submits
        # (>1 question) — the mirror image of "freetext-then-single".
        pytest.param(
            [_SINGLE, _SINGLE_2],
            [AnswerSelection(indexes=(0,)), AnswerSelection(text="Kiwi")],
            ["1", "3", "Kiwi", SendKey.ENTER, SendKey.ENTER],
            id="single-then-freetext-last",
        ),
    ],
)
def test_build_answer_keys_grammar(
    questions: list[AgentQuestion],
    answers: list[AnswerSelection],
    expected: list[SendOp],
) -> None:
    assert ClaudeCodeAdapter.build_answer_keys(questions, answers) == expected


@pytest.mark.parametrize(
    ("questions", "answers", "match"),
    [
        pytest.param(
            [_SINGLE],
            [AnswerSelection(indexes=(0,)), AnswerSelection(indexes=(1,))],
            "expected 1 answer",
            id="too-many-answers",
        ),
        pytest.param(
            [_SINGLE, _MULTI],
            [AnswerSelection(indexes=(0,))],
            "expected 2 answer",
            id="too-few-answers",
        ),
        pytest.param(
            [_SINGLE], [AnswerSelection(indexes=(9,))], "out of range", id="index-out-of-range"
        ),
        pytest.param(
            [_SINGLE],
            [AnswerSelection(indexes=(0, 1))],
            "exactly one option index",
            id="single-select-two-indexes",
        ),
        pytest.param(
            [_MULTI],
            [AnswerSelection(text="x")],
            "only on single-select",
            id="multiselect-freetext-rejected",
        ),
        pytest.param(
            [_MULTI], [AnswerSelection(indexes=())], "at least one", id="multiselect-empty"
        ),
        pytest.param(
            [_q("confirm", 0)],
            [AnswerSelection(text="ok")],
            "only on single-select",
            id="confirm-unsupported",
        ),
        pytest.param(
            [_q("free_text", 0)],
            [AnswerSelection(text="hi")],
            "only on single-select",
            id="optionless-freetext-unsupported",
        ),
    ],
)
def test_build_answer_keys_rejects_bad_plans(
    questions: list[AgentQuestion],
    answers: list[AnswerSelection],
    match: str,
) -> None:
    """A plan that doesn't fit the captured questions raises ValueError — the
    manager maps that to a 422. The builder never guesses keystrokes."""
    with pytest.raises(ValueError, match=match):
        ClaudeCodeAdapter.build_answer_keys(questions, answers)
