"""Pure tests for `grove.tui._turns` — the single turn-rendering implementation.

Both transcript surfaces (sessions screen history panel, peek rail
transcript tab) drive :class:`TranscriptBuilder`; these tests pin the
grouping policy, the markdown rendering of message bodies, and the shared
turn body without a Pilot (`.plain` / `.parts` assertions, per tests/tui
convention).
"""

from __future__ import annotations

import pytest
from rich.markdown import Markdown
from rich.text import Text

from grove.core.agents import AgentQuestion, AgentQuestionOption, DigestEntry, SessionTurn
from grove.tui._status import chrome_color, ref_color
from grove.tui._turns import QUESTION_GLYPH, TranscriptBuilder, render_transcript_digest


def _tool(text: str) -> DigestEntry:
    return DigestEntry(role="tool", text=text)


def _reply(text: str) -> DigestEntry:
    return DigestEntry(role="assistant", text=text)


def _built(
    turn: SessionTurn, *, dark: bool = True, expand_tools: bool = False
) -> TranscriptBuilder:
    builder = TranscriptBuilder(dark=dark, expand_tools=expand_tools)
    builder.add_turn(turn)
    return builder


def _line_with(builder: TranscriptBuilder, needle: str) -> Text:
    """First Text chrome part whose plain text contains `needle`."""
    for part in builder.parts:
        if isinstance(part, Text) and needle in part.plain:
            return part
    raise AssertionError(f"no Text line contains {needle!r}")


def _span_styles(text: Text, needle: str) -> list[str]:
    """Lowercased styles of spans covering `needle` (spans layer over base)."""
    return [str(s.style).lower() for s in text.spans if needle in text.plain[s.start : s.end]]


def _markdown_sources(builder: TranscriptBuilder) -> list[str]:
    return [p.markup for p in builder.parts if isinstance(p, Markdown)]


# ─── group_tool_entries ──────────────────────────────────────────────────────


def test_group_tool_entries_collapses_a_run() -> None:
    grouped = TranscriptBuilder.group_tool_entries(
        (_tool("Read a.py"), _tool("Edit a.py"), _tool("Bash pytest"))
    )
    assert grouped == (DigestEntry(role="tool", text="3 tool calls"),)


def test_group_tool_entries_uses_singular_for_one_call() -> None:
    grouped = TranscriptBuilder.group_tool_entries((_tool("Read a.py"),))
    assert grouped == (DigestEntry(role="tool", text="1 tool call"),)


def test_group_tool_entries_non_tool_entries_break_runs() -> None:
    grouped = TranscriptBuilder.group_tool_entries(
        (_tool("Read a.py"), _reply("looking"), _tool("Edit a.py"), _tool("Bash pytest"))
    )
    assert grouped == (
        DigestEntry(role="tool", text="1 tool call"),
        _reply("looking"),
        DigestEntry(role="tool", text="2 tool calls"),
    )


def test_group_tool_entries_passes_through_when_no_tools() -> None:
    entries = (_reply("hello"), _reply("done"))
    assert TranscriptBuilder.group_tool_entries(entries) == entries


# ─── markdown bodies (the headline behavior) ─────────────────────────────────


def test_message_bodies_render_as_markdown() -> None:
    """The human prompt and agent speech become Markdown renderables — headings,
    lists and inline emphasis display properly rather than as literal markup."""
    turn = SessionTurn(user_text="## Plan\n- step one\n- **two**", entries=(_reply("`done`"),))
    builder = _built(turn)
    assert _markdown_sources(builder) == ["## Plan\n- step one\n- **two**", "`done`"]
    # The plain projection keeps the source so the diff-guard / seam stays cheap.
    assert "## Plan" in builder.plain
    assert "`done`" in builder.plain


def test_machinery_rows_are_never_markdown() -> None:
    """Tool / notification / status rows are chrome, not prose — they stay Text
    so their glyphs and tiers render; only message bodies are Markdown."""
    turn = SessionTurn(
        user_text="go",
        entries=(_tool("Read x"), DigestEntry(role="status", text="paused")),
    )
    # Only the user prompt is markdown; the tool-run and status rows are Text.
    assert _markdown_sources(_built(turn)) == ["go"]


def test_body_cap_preserves_newlines() -> None:
    """The body cap keeps newlines (so markdown structure survives), unlike the
    one-line `truncate` used for chrome."""
    body = "line one\nline two\n" + "x" * 5000
    builder = _built(SessionTurn(user_text=body, entries=()))
    rendered = _markdown_sources(builder)[0]
    assert rendered.startswith("line one\nline two\n")
    assert rendered.endswith("…")
    assert len(rendered) <= 2000


# ─── add_turn ────────────────────────────────────────────────────────────────


def _turn() -> SessionTurn:
    return SessionTurn(
        user_text="fix the parser",
        entries=(_tool("Read parser.py"), _tool("Edit parser.py"), _reply("patched it")),
    )


def test_add_turn_groups_tools_by_default() -> None:
    plain = _built(_turn()).plain
    assert "you ❯" in plain  # noqa: RUF001 — the deliberate prompt glyph
    assert "fix the parser" in plain
    assert "⚒ 2 tool calls" in plain
    assert "agent ⏺" in plain
    assert "patched it" in plain
    assert "Edit parser.py" not in plain


def test_add_turn_expands_tools_on_request() -> None:
    plain = _built(_turn(), expand_tools=True).plain
    assert "⚒ Read parser.py" in plain
    assert "⚒ Edit parser.py" in plain
    assert "tool calls" not in plain


def test_add_turn_tool_rows_are_muted() -> None:
    """Tool rows sit in tier 3 (muted), per the design system."""
    muted = chrome_color("muted", dark=True).lower()
    assert muted in _line_with(_built(_turn()), "tool calls").style.__str__().lower()


def test_add_turn_marks_missing_prompt_as_continuation() -> None:
    """Machinery is commentary, not speech: the continuation marker carries NO
    `you` label (no human spoke) — the same convention the webapp pins."""
    builder = _built(SessionTurn(user_text="", entries=(_reply("resumed"),)))
    assert builder.plain.startswith("❯ (continuation")  # noqa: RUF001 — prompt glyph
    assert "you" not in builder.plain.splitlines()[0]
    assert "(continuation — no prompt recorded)" in builder.plain


# ─── role labels ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("dark", [True, False])
def test_add_turn_user_label_is_bold_accent(dark: bool) -> None:
    """The human turn opens with a `you` + chevron label in the bold prompt accent."""
    accent = chrome_color("accent", dark=dark).lower()
    label = _line_with(_built(_turn(), dark=dark), "you")
    assert label.plain == "you ❯"  # noqa: RUF001 — prompt glyph
    style = str(label.style).lower()
    assert "bold" in style and accent in style


@pytest.mark.parametrize("dark", [True, False])
def test_add_turn_agent_label_is_bold_agent_cyan(dark: bool) -> None:
    """Assistant rows carry an `agent` label in the agent-identity hue (info)."""
    info = ref_color("info", dark=dark).lower()
    label = _line_with(_built(_turn(), dark=dark), "agent")
    assert label.plain == "agent ⏺"
    style = str(label.style).lower()
    assert "bold" in style and info in style


def test_add_turn_tool_rows_carry_no_role_label() -> None:
    """Tool-group rows keep the bare muted ⚒ treatment — no speaker label."""
    tool_lines = [line for line in _built(_turn()).plain.splitlines() if "⚒" in line]
    assert tool_lines == ["  ⚒ 2 tool calls"]


def test_add_turn_notification_renders_first_line_only() -> None:
    """Notification rows show a cyan ◆ plus the first-line summary in muted —
    the rest of the text is the subagent's full result and never renders."""
    entry = DigestEntry(
        role="notification",
        text="Agent(Explore): map the webapp\nfull result body\nmore detail",
    )
    builder = _built(SessionTurn(user_text="go", entries=(entry,)))
    plain = builder.plain
    assert "◆ Agent(Explore): map the webapp" in plain
    assert "full result body" not in plain
    info = ref_color("info", dark=True).lower()
    muted = chrome_color("muted", dark=True).lower()
    row = _line_with(builder, "◆")
    base = str(row.style).lower()  # the cyan ◆ glyph rides the row's base style
    assert "bold" in base and info in base
    summary_spans = _span_styles(row, "map the webapp")  # the summary is a muted span
    assert summary_spans and all(muted in s for s in summary_spans)


@pytest.mark.parametrize("role", ["status", "summary"])
def test_add_turn_status_and_summary_are_muted_italic_notes(role: str) -> None:
    """Adapter-side notes (Mewbo status/summary) are muted italic — no
    speaker label, never the agent ⏺ treatment."""
    entry = DigestEntry(role=role, text="session interrupted")  # type: ignore[arg-type]
    builder = _built(SessionTurn(user_text="go", entries=(entry,)))
    assert "session interrupted" in builder.plain
    assert "⏺ session interrupted" not in builder.plain
    muted = chrome_color("muted", dark=True).lower()
    style = str(_line_with(builder, "session interrupted").style).lower()
    assert "italic" in style and muted in style


# ─── question rows ───────────────────────────────────────────────────────────


def _question_entry(question: AgentQuestion) -> DigestEntry:
    return DigestEntry(role="question", text=question.prompt, question=question)


def _built_question(entry: DigestEntry) -> TranscriptBuilder:
    return _built(SessionTurn(user_text="go", entries=(entry,)))


def test_add_turn_unanswered_select_renders_header_prompt_options_and_pending() -> None:
    """An open single-select question shows the glyph header, the prompt, every
    option (label + description), and a pending marker in the accent hue."""
    question = AgentQuestion(
        id="c1#0",
        group_id="c1",
        kind="single_select",
        prompt="Which database should I use?",
        header="Database choice",
        options=(
            AgentQuestionOption(label="Postgres", description="relational, ACID"),
            AgentQuestionOption(label="SQLite"),
        ),
        source_tool="AskUserQuestion",
    )
    builder = _built_question(_question_entry(question))
    plain = builder.plain
    assert QUESTION_GLYPH in plain
    assert "Database choice" in plain
    assert "Which database should I use?" in plain
    assert "Postgres" in plain
    assert "relational, ACID" in plain
    assert "SQLite" in plain
    assert "awaiting your answer" in plain
    accent = chrome_color("accent", dark=True).lower()
    row = _line_with(builder, QUESTION_GLYPH)  # the whole question is one multi-line Text
    header_spans = _span_styles(row, "Database choice")
    assert header_spans and all("bold" in s and accent in s for s in header_spans)
    pending_spans = _span_styles(row, "awaiting your answer")
    assert pending_spans and all(accent in s for s in pending_spans)


def test_add_turn_answered_question_shows_answer_and_check() -> None:
    """A resolved question renders a check-style marker plus the muted answer."""
    question = AgentQuestion(
        id="c2#0",
        group_id="c2",
        kind="single_select",
        prompt="Pick a license",
        header="License",
        options=(AgentQuestionOption(label="MIT"), AgentQuestionOption(label="Apache-2.0")),
        answered=True,
        answer="MIT",
        source_tool="AskUserQuestion",
    )
    builder = _built_question(_question_entry(question))
    plain = builder.plain
    assert "MIT" in plain
    assert "✓" in plain
    assert "awaiting your answer" not in plain
    muted = chrome_color("muted", dark=True).lower()
    answer_spans = _span_styles(_line_with(builder, QUESTION_GLYPH), "MIT")
    assert answer_spans and all(muted in s for s in answer_spans)


def test_add_turn_confirm_question_has_no_option_lines() -> None:
    """A confirm (ExitPlanMode) question has no options — just the prompt and a
    confirm affordance, never a spurious indented option line."""
    question = AgentQuestion(
        id="c3#0",
        group_id="c3",
        kind="confirm",
        prompt="Approve this plan?",
        source_tool="ExitPlanMode",
    )
    plain = _built_question(_question_entry(question)).plain
    assert "Approve this plan?" in plain
    # No select markers: the radio/checkbox leading marks never appear for confirm.
    assert "◯" not in plain
    assert "☐" not in plain


def test_add_turn_question_falls_back_to_text_when_payload_absent() -> None:
    """Defensive: wire data may carry role=='question' with no structured
    payload — render the text body rather than crashing."""
    entry = DigestEntry(role="question", text="Should I proceed?", question=None)
    plain = _built_question(entry).plain
    assert "Should I proceed?" in plain
    assert QUESTION_GLYPH in plain


# ─── render_transcript_digest ────────────────────────────────────────────────


def test_render_transcript_digest_renders_turns_oldest_first_and_grouped() -> None:
    turns = (
        SessionTurn(user_text="first ask", entries=(_reply("first answer"),)),
        SessionTurn(user_text="second ask", entries=(_tool("Bash ls"), _reply("second answer"))),
    )
    plain = render_transcript_digest(turns, dark=True).plain
    assert plain.index("first ask") < plain.index("second ask")
    assert "second answer" in plain
    assert "⚒ 1 tool call" in plain
    assert "Bash ls" not in plain  # the rail digest never lists individual calls


def test_render_transcript_digest_carries_role_labels() -> None:
    """The rail digest picks the labels up automatically — one implementation."""
    turns = (SessionTurn(user_text="first ask", entries=(_reply("first answer"),)),)
    plain = render_transcript_digest(turns, dark=True).plain
    assert "you ❯" in plain  # noqa: RUF001 — the deliberate prompt glyph
    assert "first ask" in plain
    assert "agent ⏺" in plain
    assert "first answer" in plain


def test_render_transcript_digest_empty_returns_empty_text() -> None:
    assert render_transcript_digest((), dark=True).plain == ""
