"""Pure rendering for normalized session turns — one implementation, two surfaces.

Both transcript surfaces — the sessions browser's history panel
(`screens/sessions.py`) and the peek rail's transcript tab
(`widgets/peek_rail.py`) — render :class:`SessionTurn` rows through this
module, so the typographic tiers and the tool-call grouping cannot drift
apart. Grouping policy lives here once: consecutive tool entries collapse
into a single muted ``N tool calls`` row; the sessions screen can expand
them on demand (``expand_tools=True``), the rail digest never does — the
rail is a glance surface.

Pure-render contract (same as `_status.py` consumers): helpers take
``dark: bool`` and never read ``app.current_theme``, so they stay testable
without a Pilot.
"""

from __future__ import annotations

from typing import Final

from rich.text import Text

from grove.core.agents import DigestEntry, SessionTurn
from grove.tui._status import chrome_color, ref_color

# A turn entry is capped so one giant paste can't swamp the panel — same
# trim the `grove sessions` CLI applies.
ENTRY_TEXT_CAP: Final = 2000
# Same prompt glyph the CLI renderer uses; deliberate, not a mistyped ">".
PROMPT_GLYPH: Final = "❯"  # noqa: RUF001
# Notification rows (subagent results, AskUserQuestion) — a diamond keeps
# them visually distinct from speech (⏺) and machinery (⚒) at a glance.
NOTIFICATION_GLYPH: Final = "◆"
# IRC-style role labels so the eye splits the conversation by speaker.
# `you` rides the prompt accent (clay — the chevron's existing hue, so the
# human line stays one statement); `agent` rides the agent-identity cyan
# (`ref_color("info")`, the same "who is the agent" hue the row cards use).
# Branch teal was rejected for `you`: the sessions header and the rail's
# summary card render branch names in teal right next to the transcript.
USER_LABEL: Final = "you"
AGENT_LABEL: Final = "agent"


def truncate(text: str, cap: int) -> str:
    """Whitespace-normalize and cap with an ellipsis."""
    text = " ".join(text.split())
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def group_tool_entries(entries: tuple[DigestEntry, ...]) -> tuple[DigestEntry, ...]:
    """Collapse each consecutive run of tool entries into one summary row.

    A run of N tool entries becomes a single synthetic
    ``DigestEntry(role="tool", text="N tool calls")`` (singular
    ``1 tool call``); non-tool entries break runs and pass through
    untouched. The synthetic entry renders through the same muted ``⚒``
    path an individual tool row uses, so callers need no special casing.
    """
    out: list[DigestEntry] = []
    run = 0
    for entry in entries:
        if entry.role == "tool":
            run += 1
            continue
        if run:
            out.append(_tool_run_entry(run))
            run = 0
        out.append(entry)
    if run:
        out.append(_tool_run_entry(run))
    return tuple(out)


def _tool_run_entry(count: int) -> DigestEntry:
    noun = "tool call" if count == 1 else "tool calls"
    return DigestEntry(role="tool", text=f"{count} {noun}")


def append_turn_body(
    text: Text, turn: SessionTurn, *, dark: bool, expand_tools: bool = False
) -> None:
    """Append one turn — labeled chevron prompt, then ``agent ⏺`` / tool ⚒ rows.

    IRC-style role labels split the conversation by speaker at a glance:
    ``you`` + the chevron are one bold-accent (clay) span, the ``agent``
    label is bold agent-cyan before each assistant ⏺ row. Tool rows stay
    label-free and muted (tier 3), and so does the continuation marker —
    machinery is commentary, not speech, so no speaker gets credited (the
    same rule the webapp's turns view pins). The dispatch is explicit per
    role: ``notification`` rows (subagent results) show only their
    first-line summary behind a cyan ◆; ``status`` / ``summary`` rows
    (adapter-side notes) render as muted italic — neither ever takes the
    agent ⏺ treatment. User-authored text goes through
    ``Text.append`` so markup characters in prompts render as literals.
    ``expand_tools=False`` (the default) groups consecutive tool entries
    via :func:`group_tool_entries`.
    """
    muted = chrome_color("muted", dark=dark)
    accent = chrome_color("accent", dark=dark)
    agent_hex = ref_color("info", dark=dark)
    if turn.user_text:
        text.append(f"{USER_LABEL} {PROMPT_GLYPH} ", style=f"bold {accent}")
        text.append(truncate(turn.user_text, ENTRY_TEXT_CAP))
    else:
        text.append(f"{PROMPT_GLYPH} ", style=f"bold {accent}")
        text.append("(continuation — no prompt recorded)", style=muted)
    entries = turn.entries if expand_tools else group_tool_entries(turn.entries)
    for entry in entries:
        text.append("\n")
        if entry.role == "tool":
            text.append(f"  ⚒ {truncate(entry.text, ENTRY_TEXT_CAP)}", style=muted)
        elif entry.role == "notification":
            # Only the first line is the summary; the rest is the subagent's
            # full result payload and would flood a glance surface.
            summary = entry.text.strip().partition("\n")[0]
            text.append(f"  {NOTIFICATION_GLYPH} ", style=f"bold {agent_hex}")
            text.append(truncate(summary, ENTRY_TEXT_CAP), style=muted)
        elif entry.role in ("status", "summary"):
            # Adapter-side notes (Mewbo status/summary events): commentary,
            # not speech — no speaker label, quieter than a reply.
            text.append(f"  {truncate(entry.text, ENTRY_TEXT_CAP)}", style=f"italic {muted}")
        else:  # "assistant" / "user" — spoken rows keep the agent ⏺ treatment
            text.append("  ")
            text.append(AGENT_LABEL, style=f"bold {agent_hex}")
            text.append(" ⏺ ")
            text.append(truncate(entry.text, ENTRY_TEXT_CAP))


def render_transcript_digest(turns: tuple[SessionTurn, ...], *, dark: bool) -> Text:
    """The peek rail's transcript-tab body: recent turns, oldest first.

    Compact by design — no per-turn divider header (the rail is half the
    sessions panel's width), and tool runs are always grouped: the rail
    digest never lists individual calls. Empty input renders an empty
    ``Text``; the rail owns its placeholder.
    """
    text = Text()
    for index, turn in enumerate(turns):
        if index:
            text.append("\n\n")
        append_turn_body(text, turn, dark=dark, expand_tools=False)
    return text
