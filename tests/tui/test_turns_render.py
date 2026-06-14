"""Pure tests for `grove.tui._turns` — the single turn-rendering implementation.

Both transcript surfaces (sessions screen history panel, peek rail
transcript tab) render through this module; these tests pin the grouping
policy and the shared turn body without a Pilot (`.plain` assertions, per
tests/tui convention).
"""

from __future__ import annotations

import pytest
from rich.text import Text

from grove.core.agents import DigestEntry, SessionTurn
from grove.tui._status import chrome_color, ref_color
from grove.tui._turns import append_turn_body, group_tool_entries, render_transcript_digest


def _styles_for(text: Text, needle: str) -> list[str]:
    """Lowercased span styles whose covered text contains `needle`."""
    return [str(style).lower() for s, e, style in text.spans if needle in text.plain[s:e]]


def _tool(text: str) -> DigestEntry:
    return DigestEntry(role="tool", text=text)


def _reply(text: str) -> DigestEntry:
    return DigestEntry(role="assistant", text=text)


# ─── group_tool_entries ──────────────────────────────────────────────────────


def test_group_tool_entries_collapses_a_run() -> None:
    grouped = group_tool_entries((_tool("Read a.py"), _tool("Edit a.py"), _tool("Bash pytest")))
    assert grouped == (DigestEntry(role="tool", text="3 tool calls"),)


def test_group_tool_entries_uses_singular_for_one_call() -> None:
    grouped = group_tool_entries((_tool("Read a.py"),))
    assert grouped == (DigestEntry(role="tool", text="1 tool call"),)


def test_group_tool_entries_non_tool_entries_break_runs() -> None:
    grouped = group_tool_entries(
        (_tool("Read a.py"), _reply("looking"), _tool("Edit a.py"), _tool("Bash pytest"))
    )
    assert grouped == (
        DigestEntry(role="tool", text="1 tool call"),
        _reply("looking"),
        DigestEntry(role="tool", text="2 tool calls"),
    )


def test_group_tool_entries_passes_through_when_no_tools() -> None:
    entries = (_reply("hello"), _reply("done"))
    assert group_tool_entries(entries) == entries


# ─── append_turn_body ────────────────────────────────────────────────────────


def _turn() -> SessionTurn:
    return SessionTurn(
        user_text="fix the parser",
        entries=(_tool("Read parser.py"), _tool("Edit parser.py"), _reply("patched it")),
    )


def test_append_turn_body_groups_tools_by_default() -> None:
    text = Text()
    append_turn_body(text, _turn(), dark=True)
    plain = text.plain
    assert "❯ fix the parser" in plain  # noqa: RUF001 — the deliberate prompt glyph
    assert "⚒ 2 tool calls" in plain
    assert "⏺ patched it" in plain
    assert "Edit parser.py" not in plain


def test_append_turn_body_expands_tools_on_request() -> None:
    text = Text()
    append_turn_body(text, _turn(), dark=True, expand_tools=True)
    plain = text.plain
    assert "⚒ Read parser.py" in plain
    assert "⚒ Edit parser.py" in plain
    assert "tool calls" not in plain


def test_append_turn_body_tool_rows_are_muted() -> None:
    """Tool rows sit in tier 3 (muted), per the design system — grouped and
    expanded alike."""
    muted = chrome_color("muted", dark=True).lower()
    text = Text()
    append_turn_body(text, _turn(), dark=True)
    tool_spans = _styles_for(text, "tool calls")
    assert tool_spans and all(muted in style for style in tool_spans)


def test_append_turn_body_marks_missing_prompt_as_continuation() -> None:
    """Machinery is commentary, not speech: the continuation marker carries NO
    `you` label (no human spoke) — the same convention the webapp pins."""
    text = Text()
    append_turn_body(text, SessionTurn(user_text="", entries=(_reply("resumed"),)), dark=True)
    assert text.plain.startswith("❯ (continuation")  # noqa: RUF001 — prompt glyph
    assert "you" not in text.plain.splitlines()[0]
    assert "(continuation — no prompt recorded)" in text.plain


# ─── role labels ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("dark", [True, False])
def test_append_turn_body_user_label_is_bold_accent(dark: bool) -> None:
    """The human turn opens with a `you` + chevron label in the bold prompt accent."""
    accent = chrome_color("accent", dark=dark).lower()
    text = Text()
    append_turn_body(text, _turn(), dark=dark)
    assert text.plain.startswith("you ❯ fix the parser")  # noqa: RUF001 — prompt glyph
    label_spans = _styles_for(text, "you")
    assert label_spans and all("bold" in s and accent in s for s in label_spans)


@pytest.mark.parametrize("dark", [True, False])
def test_append_turn_body_agent_label_is_bold_agent_cyan(dark: bool) -> None:
    """Assistant rows carry an `agent` label in the agent-identity hue (info)."""
    info = ref_color("info", dark=dark).lower()
    text = Text()
    append_turn_body(text, _turn(), dark=dark)
    assert "agent ⏺ patched it" in text.plain
    label_spans = _styles_for(text, "agent")
    assert label_spans and all("bold" in s and info in s for s in label_spans)


def test_append_turn_body_tool_rows_carry_no_role_label() -> None:
    """Tool-group rows keep the bare muted ⚒ treatment — no speaker label."""
    text = Text()
    append_turn_body(text, _turn(), dark=True)
    tool_lines = [line for line in text.plain.splitlines() if "⚒" in line]
    assert tool_lines == ["  ⚒ 2 tool calls"]


def test_append_turn_body_notification_renders_first_line_only() -> None:
    """Notification rows show a cyan ◆ plus the first-line summary in muted —
    the rest of the text is the subagent's full result and never renders."""
    entry = DigestEntry(
        role="notification",
        text="Agent(Explore): map the webapp\nfull result body\nmore detail",
    )
    text = Text()
    append_turn_body(text, SessionTurn(user_text="go", entries=(entry,)), dark=True)
    plain = text.plain
    assert "◆ Agent(Explore): map the webapp" in plain
    assert "full result body" not in plain
    info = ref_color("info", dark=True).lower()
    muted = chrome_color("muted", dark=True).lower()
    glyph_spans = _styles_for(text, "◆")
    assert glyph_spans and all("bold" in s and info in s for s in glyph_spans)
    summary_spans = _styles_for(text, "map the webapp")
    assert summary_spans and all(muted in s for s in summary_spans)


@pytest.mark.parametrize("role", ["status", "summary"])
def test_append_turn_body_status_and_summary_are_muted_italic_notes(role: str) -> None:
    """Adapter-side notes (Mewbo status/summary) are muted italic — no
    speaker label, never the agent ⏺ treatment."""
    entry = DigestEntry(role=role, text="session interrupted")  # type: ignore[arg-type]
    text = Text()
    append_turn_body(text, SessionTurn(user_text="go", entries=(entry,)), dark=True)
    assert "session interrupted" in text.plain
    assert "⏺ session interrupted" not in text.plain
    muted = chrome_color("muted", dark=True).lower()
    note_spans = _styles_for(text, "session interrupted")
    assert note_spans and all("italic" in s and muted in s for s in note_spans)


# ─── render_transcript_digest ────────────────────────────────────────────────


def test_render_transcript_digest_renders_turns_oldest_first_and_grouped() -> None:
    turns = (
        SessionTurn(user_text="first ask", entries=(_reply("first answer"),)),
        SessionTurn(user_text="second ask", entries=(_tool("Bash ls"), _reply("second answer"))),
    )
    plain = render_transcript_digest(turns, dark=True).plain
    assert plain.index("first ask") < plain.index("second ask")
    assert "⏺ second answer" in plain
    assert "⚒ 1 tool call" in plain
    assert "Bash ls" not in plain  # the rail digest never lists individual calls


def test_render_transcript_digest_carries_role_labels() -> None:
    """The rail digest picks the labels up automatically — one implementation."""
    turns = (SessionTurn(user_text="first ask", entries=(_reply("first answer"),)),)
    plain = render_transcript_digest(turns, dark=True).plain
    assert "you ❯ first ask" in plain  # noqa: RUF001 — the deliberate prompt glyph
    assert "agent ⏺ first answer" in plain


def test_render_transcript_digest_empty_returns_empty_text() -> None:
    assert render_transcript_digest((), dark=True).plain == ""
