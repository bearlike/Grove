"""The provider-neutral ``AgentQuestion`` contract + its one normalizer.

These pin the *shape* both adapters normalize to and the single classmethod that
maps a native tool call to it — never the semantics of any one provider. The
adapter-level wiring (where the question rides the turn stream) is pinned in each
adapter's own test module; here we pin the contract in isolation.
"""

from __future__ import annotations

from grove.core.agents import AgentQuestion, AgentQuestionOption


def test_ask_user_question_batch_normalizes_per_question() -> None:
    """One AskUserQuestion tool call carrying N questions of mixed kinds → N
    ``AgentQuestion`` rows sharing the call id, each with a stable per-question id."""
    raw = {
        "questions": [
            {
                "question": "Merge or rebase?",
                "header": "Strategy",
                "multiSelect": False,
                "options": [
                    {"label": "Merge", "description": "keep both histories"},
                    {"label": "Rebase", "description": "linear history"},
                ],
            },
            {
                "question": "Which checks to run?",
                "header": "CI",
                "multiSelect": True,
                "options": [{"label": "lint"}, {"label": "tests"}],
            },
        ]
    }
    questions = AgentQuestion.from_tool_call("AskUserQuestion", raw, "call_1")

    assert len(questions) == 2
    first, second = questions

    assert first.group_id == "call_1"
    assert first.id == "call_1#0"
    assert first.kind == "single_select"
    assert first.multiselect is False
    assert first.prompt == "Merge or rebase?"
    assert first.header == "Strategy"
    assert first.source_tool == "AskUserQuestion"
    assert first.options == (
        AgentQuestionOption(label="Merge", description="keep both histories"),
        AgentQuestionOption(label="Rebase", description="linear history"),
    )
    assert first.answered is False
    assert first.answer is None

    assert second.id == "call_1#1"
    assert second.kind == "multi_select"
    assert second.multiselect is True
    # An option without a description normalizes to ``description=None``.
    assert second.options == (
        AgentQuestionOption(label="lint", description=None),
        AgentQuestionOption(label="tests", description=None),
    )


def test_question_without_options_is_free_text() -> None:
    """An options-less question is a descriptive / free-text ask, not a select —
    a select with nothing to select is a contradiction in shape. This is what an
    MCP-bridged elicitation (no choice list) normalizes to."""
    missing = AgentQuestion.from_tool_call(
        "AskUserQuestion", {"questions": [{"question": "Name the release?"}]}, "c"
    )
    assert len(missing) == 1
    assert missing[0].kind == "free_text"
    assert missing[0].options == ()
    assert missing[0].multiselect is False

    # Empty options list is the same shape — still free_text, even if multiSelect
    # was set (there is nothing to multi-select).
    empty = AgentQuestion.from_tool_call(
        "AskUserQuestion",
        {"questions": [{"question": "Describe the bug", "multiSelect": True, "options": []}]},
        "c",
    )
    assert empty[0].kind == "free_text"
    assert empty[0].multiselect is False


def test_exit_plan_mode_carries_the_dialogs_real_options() -> None:
    """ExitPlanMode is a MODE choice, so it normalizes to the dialog's own rows.

    It used to be an optionless ``confirm``, which made a plan look answerable
    in prose. It is not: approving runs inside the tool's own body and the
    destination mode is whichever row the human lands on, so the options must
    reach the client or it cannot offer the choice the agent actually asked.
    """
    questions = AgentQuestion.from_tool_call("ExitPlanMode", {"plan": "1. do x\n2. do y"}, "call_2")

    assert len(questions) == 1
    q = questions[0]
    assert q.kind == "plan_approval"
    assert q.selects_a_mode is True
    assert q.prompt == "1. do x\n2. do y"
    assert q.group_id == "call_2"
    assert q.id == "call_2#0"
    assert q.source_tool == "ExitPlanMode"
    # Three rows, in the order the dialog paints them — the index IS the answer,
    # so their count and order are the contract, not their wording.
    assert len(q.options) == 3
    assert all(option.description for option in q.options)


def test_only_a_plan_approval_selects_a_mode() -> None:
    """The discriminator is per question, so no client branches on ``kind``."""
    batch = AgentQuestion.from_tool_call(
        "AskUserQuestion",
        {"questions": [{"question": "Which?", "options": [{"label": "A"}, {"label": "B"}]}]},
        "call_b",
    )

    assert [q.selects_a_mode for q in batch] == [False]


def test_non_question_tool_yields_nothing() -> None:
    """A regular tool (Bash, Read, an unknown function) is not a question."""
    assert AgentQuestion.from_tool_call("Bash", {"command": "ls"}, "c") == ()
    assert AgentQuestion.from_tool_call("Read", {"file_path": "/x"}, "c") == ()


def test_malformed_input_degrades_to_empty_never_raises() -> None:
    """Defensive like the rest of transcript parsing — a junk payload is dropped,
    not raised (a corrupt line must never break the render loop)."""
    assert AgentQuestion.from_tool_call("AskUserQuestion", {}, "c") == ()
    assert AgentQuestion.from_tool_call("AskUserQuestion", {"questions": "x"}, "c") == ()
    assert AgentQuestion.from_tool_call("AskUserQuestion", {"questions": [42]}, "c") == ()
    # A question entry missing its text is skipped, but siblings survive.
    raw = {"questions": [{"header": "no text"}, {"question": "real?"}]}
    out = AgentQuestion.from_tool_call("AskUserQuestion", raw, "c")
    assert len(out) == 1
    assert out[0].prompt == "real?"
    assert out[0].id == "c#1"  # index is the position in the source array, stable


def test_recognizes_returns_true_only_for_question_tools() -> None:
    """The single source of truth for 'is this a question tool' — reused by the
    Claude status path for BLOCKED detection."""
    assert AgentQuestion.recognizes("AskUserQuestion") is True
    assert AgentQuestion.recognizes("ExitPlanMode") is True
    assert AgentQuestion.recognizes("Bash") is False
