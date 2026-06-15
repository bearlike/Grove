"""Pure rendering for normalized session turns — one implementation, two surfaces.

Both transcript surfaces — the sessions browser's history panel
(`screens/sessions.py`) and the peek rail's transcript tab
(`widgets/peek_rail.py`) — render :class:`SessionTurn` rows through
:class:`TranscriptBuilder`, so the typographic tiers, the markdown
rendering and the tool-call grouping cannot drift apart. The surfaces add
only chrome — the sessions panel a header and per-turn dividers, the rail
blank-line gaps — never their own turn body.

Message bodies (the human prompt and agent speech) render through
`rich.markdown.Markdown`, so headings, lists, fenced code and inline
emphasis display properly instead of as literal markup. Speaker labels,
tool-run rows and adapter notes stay styled `Text` in the design-system
tiers — they are chrome, not prose.

Pure-render contract (same as `_status.py` consumers): the builder takes
``dark: bool`` and never reads ``app.current_theme``, so it stays testable
without a Pilot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.text import Text

from grove.core.agents import DigestEntry, SessionTurn
from grove.tui._status import chrome_color, ref_color

# A turn entry is capped so one giant paste can't swamp the panel — same
# trim the `grove sessions` CLI applies.
ENTRY_TEXT_CAP: Final = 2000
# Same prompt glyph the CLI renderer uses; deliberate, not a mistyped ">".
PROMPT_GLYPH: Final = "❯"  # noqa: RUF001
TOOL_GLYPH: Final = "⚒"
AGENT_GLYPH: Final = "⏺"
# Notification rows (subagent results, AskUserQuestion) — a diamond keeps
# them visually distinct from speech (⏺) and machinery (⚒) at a glance.
NOTIFICATION_GLYPH: Final = "◆"
# Question rows (issue #74) — the agent asked the human a structured question.
# A double question mark (General Punctuation, terminal-safe) reads as "your
# input is needed" and stays distinct from the prompt chevron, tool ⚒, agent
# ⏺, and notification ◆ glyphs already in use.
QUESTION_GLYPH: Final = "⁇"
# Per-option leading marks: a radio-style ring for single-select, a checkbox for
# multi-select — terminal-safe Geometric Shapes / boxes, never a Nerd Font icon.
RADIO_MARK: Final = "◯"
CHECKBOX_MARK: Final = "☐"
# State-line markers: a check for answered (resolved), a chevron for pending.
ANSWERED_MARK: Final = "✓"
PENDING_MARK: Final = "›"  # noqa: RUF001 — deliberate chevron, not a ">"
# IRC-style role labels so the eye splits the conversation by speaker. The
# label sits on its own line above the markdown body (markdown is a block
# renderable, not an inline span) — speakers align at column 0, machinery
# indents under them. `you` rides the prompt accent (clay — the chevron's
# existing hue); `agent` rides the agent-identity cyan (`ref_color("info")`).
USER_LABEL: Final = "you"
AGENT_LABEL: Final = "agent"
# Pygments themes for fenced code blocks, matched to terminal polarity so a
# dark code background never lands on a light terminal (and vice-versa).
_CODE_THEME_DARK: Final = "monokai"
_CODE_THEME_LIGHT: Final = "default"


def truncate(text: str, cap: int) -> str:
    """Whitespace-normalize to one line and cap with an ellipsis.

    For single-line chrome only (row labels, tool rows, notification
    summaries). Message bodies are NOT routed through here — they keep
    their newlines so Markdown can render structure
    (see :meth:`TranscriptBuilder.add_turn`).
    """
    text = " ".join(text.split())
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


@dataclass(slots=True)
class TranscriptBuilder:
    """Accumulates a transcript as Rich renderables plus a plain projection.

    One implementation, two surfaces: both drive this builder, so the turn
    grouping and the per-role dispatch live here exactly once. The surfaces
    differ only in chrome they add via :meth:`line` / :meth:`gap` around
    :meth:`add_turn`.

    ``renderable`` feeds a `Static`; ``plain`` is the cheap, stable text
    projection both the per-surface diff-guards and the tests read (Markdown
    bodies project to their capped source). ``parts`` exposes the rendered
    sequence for style assertions. Pure: ``dark`` is injected, never read
    from ``app.current_theme``.
    """

    dark: bool
    expand_tools: bool = False
    _parts: list[RenderableType] = field(default_factory=list, init=False)
    _plain: list[str] = field(default_factory=list, init=False)

    # ─── composition primitives the surfaces use ──────────────────────────

    def line(self, text: Text) -> None:
        """Append one chrome line — a label, divider, header or machinery row."""
        self._parts.append(text)
        self._plain.append(text.plain)

    def gap(self) -> None:
        """Append a blank line — the spacing between entries and between turns."""
        self._parts.append(Text())
        self._plain.append("")

    def add_turn(self, turn: SessionTurn) -> None:
        """Append one turn: the prompt block, then each entry, blank-separated.

        The human prompt and agent speech render as Markdown (label line +
        body block); tool runs collapse to a single muted ``N tool calls``
        row unless ``expand_tools``; ``notification`` rows show only their
        first-line summary; ``status`` / ``summary`` adapter notes render as
        muted italic. The dispatch is explicit per role — the ``else`` branch
        means "speech", never "default": a new `DigestEntry.role` must add its
        own branch here rather than fall through to the agent treatment.
        """
        muted = chrome_color("muted", dark=self.dark)
        accent = chrome_color("accent", dark=self.dark)
        agent_hex = ref_color("info", dark=self.dark)
        if turn.user_text:
            self.line(Text(f"{USER_LABEL} {PROMPT_GLYPH}", style=f"bold {accent}"))
            self._body(turn.user_text)
        else:
            cont = Text(f"{PROMPT_GLYPH} ", style=f"bold {accent}")
            cont.append("(continuation — no prompt recorded)", style=muted)
            self.line(cont)
        entries = turn.entries if self.expand_tools else self.group_tool_entries(turn.entries)
        for entry in entries:
            self.gap()
            self._add_entry(entry, muted=muted, accent=accent, agent_hex=agent_hex)

    # ─── outputs ──────────────────────────────────────────────────────────

    @property
    def renderable(self) -> RenderableType:
        """The accumulated transcript as one Rich renderable for a `Static`."""
        return Group(*self._parts)

    @property
    def plain(self) -> str:
        """Plain-text projection — the diff-guard signature and test seam."""
        return "\n".join(self._plain)

    @property
    def parts(self) -> tuple[RenderableType, ...]:
        """The rendered sequence (Text chrome + Markdown bodies) — a test seam."""
        return tuple(self._parts)

    # ─── per-role rendering ───────────────────────────────────────────────

    def _add_entry(self, entry: DigestEntry, *, muted: str, accent: str, agent_hex: str) -> None:
        if entry.role == "tool":
            self.line(Text(f"  {TOOL_GLYPH} {truncate(entry.text, ENTRY_TEXT_CAP)}", style=muted))
        elif entry.role == "notification":
            # Only the first line is the summary; the rest is the subagent's
            # full result payload and would flood a glance surface. Split
            # BEFORE truncate, which whitespace-normalizes the whole payload.
            summary = entry.text.strip().partition("\n")[0]
            row = Text(f"  {NOTIFICATION_GLYPH} ", style=f"bold {agent_hex}")
            row.append(truncate(summary, ENTRY_TEXT_CAP), style=muted)
            self.line(row)
        elif entry.role in ("status", "summary"):
            # Adapter-side notes (Mewbo status/summary events): commentary,
            # not speech — no speaker label, quieter than a reply.
            self.line(Text(f"  {truncate(entry.text, ENTRY_TEXT_CAP)}", style=f"italic {muted}"))
        elif entry.role == "question":
            # Structured ask-the-human (issue #74): render the typed payload
            # (header · prompt · options · answered/pending). Defensive fallback
            # to the plain text body when the wire data omits `question`.
            self._question(entry, accent=accent, muted=muted)
        else:  # "assistant" / "user" — spoken rows: agent label + markdown body
            self.line(Text(f"{AGENT_LABEL} {AGENT_GLYPH}", style=f"bold {agent_hex}"))
            self._body(entry.text)

    def _question(self, entry: DigestEntry, *, accent: str, muted: str) -> None:
        """Render one ``role=="question"`` row (issue #74) as one multi-line Text.

        Layout: a ``⁇ header  prompt`` line, one indented option line per choice
        (radio for single-select, checkbox for multi-select; ``label — desc`` when
        a description is present), then a state line (a check + muted answer when
        answered, an accent pending marker otherwise). A ``confirm`` question
        (ExitPlanMode) has no options. Falls back to the plain text body when the
        structured payload is absent, so malformed wire data still renders. Unlike
        speech, a question is chrome `Text` (not Markdown) — its structure is the
        typed payload, not author prose.
        """
        question = entry.question
        text = Text()
        if question is None:
            text.append(f"  {QUESTION_GLYPH} ", style=f"bold {accent}")
            text.append(truncate(entry.text, ENTRY_TEXT_CAP), style=muted)
            self.line(text)
            return
        text.append(f"  {QUESTION_GLYPH} ")
        text.append(truncate(question.header or "question", ENTRY_TEXT_CAP), style=f"bold {accent}")
        text.append(f"  {truncate(question.prompt, ENTRY_TEXT_CAP)}")
        mark = CHECKBOX_MARK if question.multiselect else RADIO_MARK
        for option in question.options:
            label = option.label
            if option.description:
                label = f"{label} — {option.description}"
            text.append(f"\n    {mark} {truncate(label, ENTRY_TEXT_CAP)}", style=muted)
        text.append("\n    ")
        if question.answered:
            text.append(f"{ANSWERED_MARK} ", style=muted)
            text.append(truncate(question.answer or "answered", ENTRY_TEXT_CAP), style=muted)
        else:
            text.append(f"{PENDING_MARK} awaiting your answer", style=accent)
        self.line(text)

    def _body(self, source: str) -> None:
        """Append a message body as rendered Markdown.

        Capped (not whitespace-normalized) so a giant paste can't swamp the
        panel while newlines survive for Markdown to render structure. The
        plain projection is the capped source — a faithful, cheap signature.
        """
        capped = (
            source if len(source) <= ENTRY_TEXT_CAP else source[: ENTRY_TEXT_CAP - 1].rstrip() + "…"
        )
        theme = _CODE_THEME_DARK if self.dark else _CODE_THEME_LIGHT
        self._parts.append(Markdown(capped, code_theme=theme))
        self._plain.append(capped)

    # ─── grouping policy (pure) ───────────────────────────────────────────

    @staticmethod
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
                out.append(TranscriptBuilder._tool_run_entry(run))
                run = 0
            out.append(entry)
        if run:
            out.append(TranscriptBuilder._tool_run_entry(run))
        return tuple(out)

    @staticmethod
    def _tool_run_entry(count: int) -> DigestEntry:
        noun = "tool call" if count == 1 else "tool calls"
        return DigestEntry(role="tool", text=f"{count} {noun}")


def render_transcript_digest(turns: tuple[SessionTurn, ...], *, dark: bool) -> TranscriptBuilder:
    """The peek rail's transcript-tab body: recent turns, oldest first.

    Compact by design — no per-turn divider header (the rail is half the
    sessions panel's width), and tool runs are always grouped: the rail
    digest never lists individual calls. The rail paints ``.renderable``
    into its Static and diff-guards on ``.plain``; an empty input yields an
    empty builder (``.plain == ""``) and the rail owns its placeholder.
    """
    builder = TranscriptBuilder(dark=dark)
    for index, turn in enumerate(turns):
        if index:
            builder.gap()
        builder.add_turn(turn)
    return builder
