"""OpenAI Codex CLI rollout introspection — the third :class:`AgentAdapter`.

One concern: turn Codex CLI's rollout JSONL into the normalized
:class:`AgentActivity`. Decomposed into the same three atomic, private classes
as the Claude adapter so each is nameable in a sentence and unit-testable on its
own:

- :class:`_CodexHome` — *where* the rollouts live (``$CODEX_HOME`` resolution +
  the ``sessions/YYYY/MM/DD/rollout-*.jsonl`` glob + cwd-by-``session_meta``
  discovery). The only filesystem side effect.
- :class:`_RolloutLine` — *what one line is* (identity, classification, content
  and metric accessors for a single JSONL entry, narrowed at the edge).
- :class:`_RolloutParser` — *the aggregate* (one pass → activity + turns +
  digest).

``CodexAdapter`` is the thin public seam that wires filesystem → parser.

Grounding (verified against real on-host rollouts, codex 0.125.0, 2026-04-28):

- Path is ``$CODEX_HOME``-or-``~/.codex`` / ``sessions/YYYY/MM/DD/`` /
  ``rollout-<ISO-ts>-<uuid>.jsonl``; the session id is the ``uuid`` (also the
  ``session_meta.payload.id``). The cwd lives ONLY in ``session_meta`` and
  ``turn_context`` — NOT on every line as in Claude — so discovery reads the
  head ``session_meta`` line to confirm the cwd, never decodes a folder name.
- **Codex dual-records.** The conversation is recorded twice: once as
  ``response_item`` rows (the structured transcript) and again as ``event_msg``
  rows (``agent_message`` / ``user_message`` mirror the messages; ``token_count``
  / ``task_*`` carry status + usage). Counting turns/replies/tools from BOTH
  doubles every message — the analogue of Claude's split-block dedup. The rule:
  conversation/turns/replies/tools come from ``response_item`` ONLY; status and
  tokens come from ``event_msg`` ONLY.
- **Status from task pairing.** ``event_msg/task_started`` and
  ``event_msg/task_complete`` carry a shared ``turn_id``; an unmatched
  ``task_started`` means a turn is in flight (WORKING), all matched means the
  human has the move (WAITING). With no task events, fall back to the
  ``response_item`` tail (trailing unanswered ``function_call`` → WORKING, a
  trailing assistant ``message`` → WAITING). Codex approval prompts are
  interactive and never persisted, so there is no transcript-visible BLOCKED —
  same as Claude permission prompts being hook-only.
- **Reasoning is a black box.** ``response_item/reasoning`` is almost always
  ``encrypted_content`` (opaque base64) with an empty ``summary``/null
  ``content``; surface a reasoning marker only when ``summary`` carries readable
  ``summary_text``. Never decrypt or surface ``encrypted_content`` — that is
  model output we normalize the *shape* of, never the *semantics*.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from grove.core.agents.model import (
    AgentActivity,
    AgentActivityState,
    DigestEntry,
    OrderedDigest,
    SessionSummary,
    SessionTurn,
)

# A ``response_item message`` is the human prompt EXCEPT the injected preamble:
# the first user message wraps ``# AGENTS.md`` instructions and an
# ``<environment_context>`` block, and ``developer`` messages are system/permission
# injections. Any text containing one of these markers is machinery, not a turn.
_PREAMBLE_MARKERS: tuple[str, ...] = (
    "<environment_context>",
    "<user_instructions>",
    "# AGENTS.md",
    "<permissions instructions>",
)

_DIGEST_MAX_ENTRIES = 60
_DIGEST_TEXT_CAP = 200
_TASK_TEXT_CAP = 500


def _parse_timestamp(value: Any) -> datetime | None:
    """RFC-3339 (``...Z`` accepted) → aware datetime, or ``None`` on anything odd.

    Always aware: a tz-less string is assumed UTC rather than returned naive —
    downstream freshness math subtracts these from ``utcnow`` and a naive
    datetime would raise mid-poll instead of degrading.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _truncate(text: str, cap: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


class _CodexHome:
    """Resolves *where* Codex CLI keeps its rollouts.

    Pure path logic + read-only globbing. Reads ``$CODEX_HOME`` live on each call
    (not at construction) so a test can redirect it per case and production picks
    up a relocated config dir without a restart.
    """

    @staticmethod
    def sessions_dir() -> Path:
        """The ``sessions/`` root under ``$CODEX_HOME`` (or ``~/.codex``)."""
        raw = os.environ.get("CODEX_HOME", "").strip()
        base = Path(raw).expanduser() if raw else Path.home() / ".codex"
        return base / "sessions"

    @classmethod
    def locate(cls, cwd: Path, session_id: str) -> list[Path]:
        """The rollout file for ``session_id`` whose ``session_meta`` cwd is ``cwd``.

        Codex names the file ``rollout-<ts>-<uuid>.jsonl`` and embeds the same
        uuid as ``session_meta.payload.id`` — so we glob by the uuid suffix
        across the date-partitioned tree and confirm by the in-file id. There are
        no Codex sub-agent files, so this is a single-element list (or empty).
        The ``cwd`` is a tie-break only when more than one file claims the id.
        """
        matches = [p for p in cls._iter_rollouts() if p.stem.endswith(session_id)]
        confirmed = [p for p in matches if cls._meta_id(p) == session_id]
        pool = confirmed or matches
        if len(pool) > 1:
            preferred = [p for p in pool if cls._meta_cwd(p) == str(cwd)]
            if preferred:
                pool = preferred
        return pool[:1]

    @classmethod
    def discover(cls, cwd: Path, *, exclude_id: str | None) -> list[str]:
        """Session ids recorded for ``cwd`` (excluding ``exclude_id``), newest-first.

        The newest-first order is load-bearing for the dashboard: a workspace
        with no Grove-minted id adopts the *live* session by taking the first
        result, so an arbitrary order would surface a dead one. (Grove cannot
        mint a Codex id — there is no resume-safe ``--session-id`` flag — so
        every Codex session reaches Grove through this discovery path.)
        """
        return [sid for sid, _, _ in cls.discover_paths(cwd, exclude_id=exclude_id)]

    @classmethod
    def discover_paths(
        cls, cwd: Path, *, exclude_id: str | None = None
    ) -> list[tuple[str, Path, float]]:
        """``(session_id, path, mtime)`` for every rollout recorded in ``cwd``,
        newest-first by mtime — the one scan behind both ``discover`` (ids for
        the dashboard) and ``list_sessions`` (summaries for the explorer).

        Reads each rollout's head ``session_meta`` line for its id + cwd (the cwd
        is not on every line, unlike Claude), keeping only those whose cwd
        matches and confirming the id from the meta, not the filename.
        """
        target = str(cwd)
        found: dict[str, tuple[Path, float]] = {}
        for path in cls._iter_rollouts():
            meta = cls._meta(path)
            session_id = meta.get("id")
            if not isinstance(session_id, str) or not session_id:
                continue
            if session_id == exclude_id:
                continue
            if meta.get("cwd") != target:
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:  # best-effort: a vanished file just sorts oldest
                mtime = 0.0
            prior = found.get(session_id)
            if prior is None or mtime > prior[1]:
                found[session_id] = (path, mtime)
        return [
            (sid, path, mtime)
            for sid, (path, mtime) in sorted(found.items(), key=lambda kv: (-kv[1][1], kv[0]))
        ]

    @classmethod
    def _iter_rollouts(cls) -> Iterable[Path]:
        """Every ``rollout-*.jsonl`` under the date-partitioned sessions tree."""
        root = cls.sessions_dir()
        if not root.is_dir():
            return
        yield from root.glob("**/rollout-*.jsonl")

    @classmethod
    def _meta(cls, path: Path) -> dict[str, Any]:
        """The ``session_meta.payload`` dict (always line 1), or ``{}`` if absent.

        Bounded to a short head read — ``session_meta`` is the first record by
        construction — so a pathological file costs no more than a few lines.
        """
        try:
            with path.open(encoding="utf-8") as fh:
                for index, line in enumerate(fh):
                    if index >= 8:
                        break
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        rec = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(rec, dict) and rec.get("type") == "session_meta":
                        payload = rec.get("payload")
                        return payload if isinstance(payload, dict) else {}
        except OSError:
            return {}
        return {}

    @classmethod
    def _meta_id(cls, path: Path) -> str | None:
        value = cls._meta(path).get("id")
        return value if isinstance(value, str) and value else None

    @classmethod
    def _meta_cwd(cls, path: Path) -> str | None:
        value = cls._meta(path).get("cwd")
        return value if isinstance(value, str) and value else None

    @staticmethod
    def read_lines(path: Path) -> Iterable[dict[str, Any]]:
        """Yield each parseable JSON object in ``path``; skip blanks and bad lines.

        All full-file rollout I/O lives here (this class is the adapter's one
        filesystem side effect). Per-line tolerance means a truncated final line
        never aborts the file, and a vanished file (cleaned mid-read) yields
        nothing rather than raising.
        """
        try:
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        obj = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(obj, dict):
                        yield obj
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.debug("could not read rollout {}: {}", path, exc)
            return


@dataclass(slots=True, frozen=True)
class _RolloutLine:
    """One rollout line, wrapped so every classification rule has one home.

    Holds the raw ``dict`` (heterogeneous external JSON, narrowed only through
    these typed accessors) plus the parse index for a stable sort tiebreak. The
    raw field stays ``Any``-typed on purpose — the "genuinely heterogeneous
    external data, narrowed at the boundary" escape hatch.
    """

    raw: dict[str, Any]
    index: int

    # ── identity ──────────────────────────────────────────────────────────
    @property
    def record_type(self) -> str:
        value = self.raw.get("type")
        return value if isinstance(value, str) else ""

    @property
    def timestamp(self) -> datetime | None:
        return _parse_timestamp(self.raw.get("timestamp"))

    @property
    def sort_key(self) -> tuple[float, int]:
        """Time-sort key for the parser. A line without a timestamp keeps its
        parse position via ``index`` rather than jumping to the epoch."""
        ts = self.timestamp
        return (ts.timestamp() if ts is not None else 0.0, self.index)

    @property
    def _payload(self) -> dict[str, Any]:
        payload = self.raw.get("payload")
        return payload if isinstance(payload, dict) else {}

    @property
    def payload_type(self) -> str:
        value = self._payload.get("type")
        return value if isinstance(value, str) else ""

    # ── content extraction ────────────────────────────────────────────────
    def _content_text(self) -> str:
        """Concatenated text from a ``message`` payload's content array.

        Content is ``[{type:"input_text"|"output_text", text}, …]``; both text
        variants are human-readable, only the wrapper type differs.
        """
        content = self._payload.get("content")
        if not isinstance(content, list):
            return ""
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ]
        return "\n".join(p for p in parts if p)

    @property
    def role(self) -> str:
        value = self._payload.get("role")
        return value if isinstance(value, str) else ""

    # ── classification (response_item ONLY — the conversation source) ───────
    @property
    def is_human_turn(self) -> bool:
        """A real user prompt — the filter that separates genuine turns from the
        injected preamble.

        A ``response_item message`` with ``role:"user"`` whose text is not an
        env/instructions wrapper. ``developer`` messages (system/permission
        injections) and the first AGENTS.md+``<environment_context>`` preamble
        are excluded; the ``event_msg/user_message`` mirror is ignored entirely
        (it is not a ``response_item``) so a turn is never double-counted.
        """
        if self.record_type != "response_item" or self.payload_type != "message":
            return False
        if self.role != "user":
            return False
        body = self._content_text()
        if not body.strip():
            return False
        return not any(marker in body for marker in _PREAMBLE_MARKERS)

    @property
    def is_assistant(self) -> bool:
        """A ``response_item`` assistant reply (one reply in the turn loop)."""
        return (
            self.record_type == "response_item"
            and self.payload_type == "message"
            and self.role == "assistant"
        )

    @property
    def is_tool_call(self) -> bool:
        """A tool CALL — ``function_call`` or ``tool_search_call`` (count these).

        The result side (``function_call_output`` / ``tool_search_output``) is
        NOT counted: like Claude, only the call side is a tool call, or every
        step doubles.
        """
        return self.record_type == "response_item" and self.payload_type in (
            "function_call",
            "tool_search_call",
        )

    @property
    def is_reasoning(self) -> bool:
        return self.record_type == "response_item" and self.payload_type == "reasoning"

    def reasoning_text(self) -> str | None:
        """Readable reasoning, or ``None`` when it is opaque/encrypted.

        ``summary`` is ``[{type:"summary_text", text}, …]`` when present; the
        ``encrypted_content`` field is black-box model output and is never read.
        """
        summary = self._payload.get("summary")
        if not isinstance(summary, list):
            return None
        parts = [
            block.get("text", "")
            for block in summary
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ]
        joined = "\n".join(p for p in parts if p)
        return joined or None

    def tool_name(self) -> str:
        """The tool a call invokes — ``function_call.name`` or the search query."""
        payload = self._payload
        name = payload.get("name")
        if isinstance(name, str) and name:
            return name
        if self.payload_type == "tool_search_call":
            args = payload.get("arguments")
            if isinstance(args, dict) and isinstance(args.get("query"), str):
                return f"search: {args['query']}"
            return "tool_search"
        return "tool"

    def message_text(self) -> str:
        return self._content_text()

    # ── status + tokens (event_msg ONLY) ────────────────────────────────────
    @property
    def turn_id(self) -> str | None:
        value = self._payload.get("turn_id")
        return value if isinstance(value, str) and value else None

    @property
    def is_task_started(self) -> bool:
        return self.record_type == "event_msg" and self.payload_type == "task_started"

    @property
    def is_task_complete(self) -> bool:
        return self.record_type == "event_msg" and self.payload_type == "task_complete"

    @property
    def usage_tokens(self) -> tuple[int, int] | None:
        """Cumulative ``(input, output)`` from ``token_count.info``, or ``None``.

        ``info.total_token_usage`` is the running cumulative total for the
        session (verified on-host: ``input_tokens`` strictly grows), so the
        parser takes the LAST populated record rather than summing — summing
        cumulative totals would multiply. ``info`` is ``null`` early in a session
        (only ``rate_limits`` present), which is why tokens legitimately read 0
        until the first usage report.
        """
        if self.record_type != "event_msg" or self.payload_type != "token_count":
            return None
        info = self._payload.get("info")
        if not isinstance(info, dict):
            return None
        total = info.get("total_token_usage")
        if not isinstance(total, dict):
            return None

        def _int(key: str) -> int:
            v = total.get(key)
            return v if isinstance(v, int) else 0

        return (_int("input_tokens"), _int("output_tokens"))


class _EventState:
    """Accumulates the ``event_msg``-sourced signals: status + tokens.

    Codex's status/tokens come from ``event_msg`` ONLY (the dual-record rule);
    keeping that accumulation here — state plus the methods over it — keeps the
    parser's main loop about the *conversation* (``response_item``) and out of
    the event bookkeeping. ``consume`` returns whether the line was an event it
    handled, so the conversation branches only run for non-event lines.
    """

    __slots__ = ("_completed", "_saw_task", "_started", "tokens_in", "tokens_out")

    def __init__(self) -> None:
        self._started: set[str] = set()
        self._completed: set[str] = set()
        self._saw_task = False
        self.tokens_in = 0
        self.tokens_out = 0

    def consume(self, line: _RolloutLine) -> bool:
        usage = line.usage_tokens
        if usage is not None:
            # Cumulative totals: take the latest populated report, never sum.
            self.tokens_in, self.tokens_out = usage
            return True
        if line.is_task_started:
            self._saw_task = True
            if line.turn_id:
                self._started.add(line.turn_id)
            return True
        if line.is_task_complete:
            self._saw_task = True
            if line.turn_id:
                self._completed.add(line.turn_id)
            return True
        return False

    def state(self, tail: _RolloutLine | None) -> AgentActivityState:
        """Transcript-only status (epic §6).

        Primary rule — task pairing: any ``task_started`` turn_id without a
        matching ``task_complete`` means a turn is in flight → WORKING; all
        started turns completed → WAITING (the human's move). This is cleaner
        than Claude's stop_reason because Codex records explicit turn boundaries.

        Fallback (no task events but conversation exists): a trailing tool call
        with no result, or a trailing human turn, → WORKING; a trailing assistant
        message → WAITING. There is no transcript-visible BLOCKED — Codex
        approval prompts are interactive and never persisted (same as Claude
        permission prompts being hook-only). Empty/missing → UNKNOWN.
        """
        if self._saw_task:
            if self._started <= self._completed:
                return AgentActivityState.WAITING
            return AgentActivityState.WORKING
        if tail is None:
            return AgentActivityState.UNKNOWN
        if tail.is_assistant:
            return AgentActivityState.WAITING
        # A human turn or a trailing tool call → the agent's move.
        return AgentActivityState.WORKING


class _RolloutParser:
    """Aggregates time-sorted rollout lines into one :class:`AgentActivity`.

    Single pass. Owns the per-turn bucketing that yields ``replies_per_turn``
    and the dual-record discipline (conversation from ``response_item``,
    status/tokens from ``event_msg`` via :class:`_EventState`). Constructed from
    already-read lines so it stays pure and unit-testable without the filesystem.
    """

    def __init__(self, lines: Sequence[_RolloutLine]) -> None:
        self._lines = lines

    def activity(self) -> AgentActivity:
        if not self._lines:
            return AgentActivity.empty(AgentActivityState.UNKNOWN)

        buckets: list[int] = []
        tool_calls = 0
        current_task: str | None = None
        last_event_at: datetime | None = None
        events = _EventState()
        # The last response_item that is a human turn, assistant reply, or tool
        # call — the tail the fallback status rule reads.
        tail: _RolloutLine | None = None

        for line in self._lines:
            ts = line.timestamp
            if ts is not None and (last_event_at is None or ts > last_event_at):
                last_event_at = ts
            if events.consume(line):
                continue

            if line.is_human_turn:
                buckets.append(0)
                current_task = current_task or _truncate(line.message_text(), _TASK_TEXT_CAP)
                tail = line
            elif line.is_assistant:
                if buckets:
                    buckets[-1] += 1
                tail = line
            elif line.is_tool_call:
                tool_calls += 1
                tail = line

        if current_task is None:
            current_task = self._first_human_text()

        return AgentActivity(
            state=events.state(tail),
            current_task=current_task,
            human_turns=len(buckets),
            assistant_replies=sum(buckets),
            replies_per_turn=tuple(buckets),
            tool_calls=tool_calls,
            model=self.model(),
            tokens_in=events.tokens_in,
            tokens_out=events.tokens_out,
            last_event_at=last_event_at,
        )

    def digest(self) -> OrderedDigest:
        """Ordered ``user / assistant / tool`` skeleton; tool outputs stripped."""
        entries: list[DigestEntry] = []
        for line in self._lines:
            if line.is_human_turn:
                text = _truncate(line.message_text(), _DIGEST_TEXT_CAP)
                entries.append(DigestEntry("user", text))
            elif line.is_assistant:
                text = _truncate(line.message_text(), _DIGEST_TEXT_CAP)
                if text:
                    entries.append(DigestEntry("assistant", text))
            elif line.is_tool_call:
                entries.append(DigestEntry("tool", line.tool_name()))
        return OrderedDigest(tuple(entries[-_DIGEST_MAX_ENTRIES:]))

    def turns(self, *, last: int | None = None) -> tuple[SessionTurn, ...]:
        """The conversation as :class:`SessionTurn` rows, oldest first.

        Assistant/tool/reasoning records that precede any human turn (a resumed
        head, or Codex's leading developer/preamble messages) collect under a
        leading turn with an empty ``user_text`` rather than being dropped.
        """
        turns: list[SessionTurn] = []
        entries: list[DigestEntry] = []
        # ``current`` is the open turn's ``(user_text, started_at)`` — boxed so the
        # entry-adding closure can open a leading continuation turn (assistant /
        # tool / reasoning before any human prompt) without re-checking the
        # condition at each of the three call sites.
        current: list[tuple[str, datetime | None] | None] = [None]

        def _flush() -> None:
            user_text, started_at = current[0] or ("", None)
            turns.append(
                SessionTurn(user_text=user_text, started_at=started_at, entries=tuple(entries))
            )
            entries.clear()

        def _add(entry: DigestEntry, when: datetime | None) -> None:
            if current[0] is None and not entries and when is not None:
                # Leading continuation block inherits the first reply's time.
                current[0] = ("", when)
            entries.append(entry)

        for line in self._lines:
            if line.is_human_turn:
                if current[0] is not None or entries:
                    _flush()
                current[0] = (line.message_text(), line.timestamp)
            elif line.is_assistant:
                text = line.message_text()
                if text.strip():
                    _add(DigestEntry("assistant", text), line.timestamp)
            elif line.is_tool_call:
                _add(DigestEntry("tool", line.tool_name()), line.timestamp)
            elif line.is_reasoning:
                # Black box unless a readable summary exists — never the opaque
                # encrypted_content.
                reasoning = line.reasoning_text()
                if reasoning:
                    _add(DigestEntry("assistant", reasoning), line.timestamp)
        if current[0] is not None or entries:
            _flush()

        if last is not None:
            return tuple(turns[-last:]) if last > 0 else ()
        return tuple(turns)

    def first_human_text(self) -> str | None:
        return self._first_human_text()

    def last_human_text(self) -> str | None:
        for line in reversed(self._lines):
            if line.is_human_turn:
                return _truncate(line.message_text(), _TASK_TEXT_CAP)
        return None

    def created_at(self) -> datetime | None:
        for line in self._lines:
            if line.timestamp is not None:
                return line.timestamp
        return None

    def model(self) -> str | None:
        """The model the session ran, from ``turn_context`` or ``session_meta``.

        ``turn_context.payload.model`` is the per-turn model (the precise value);
        ``session_meta`` carries the provider but not always a model name, so the
        turn_context value wins when present.
        """
        meta_model: str | None = None
        for line in self._lines:
            payload = line.raw.get("payload")
            if not isinstance(payload, dict):
                continue
            if line.record_type == "turn_context":
                value = payload.get("model")
                if isinstance(value, str) and value:
                    return value
            elif line.record_type == "session_meta" and meta_model is None:
                value = payload.get("model")
                if isinstance(value, str) and value:
                    meta_model = value
        return meta_model

    def recorded_cwd(self) -> str | None:
        """The cwd the session recorded (``session_meta`` then ``turn_context``)."""
        for line in self._lines:
            payload = line.raw.get("payload")
            if not isinstance(payload, dict):
                continue
            if line.record_type in ("session_meta", "turn_context"):
                cwd = payload.get("cwd")
                if isinstance(cwd, str) and cwd:
                    return cwd
        return None

    def git_branch(self) -> str | None:
        """The branch ``session_meta`` recorded, if any.

        Codex embeds a ``git: {branch, commit_hash, repository_url}`` block in
        ``session_meta`` when the cwd is a repo (verified on-host) — the only
        record carrying it, so a single head scan suffices.
        """
        for line in self._lines:
            if line.record_type != "session_meta":
                continue
            payload = line.raw.get("payload")
            git = payload.get("git") if isinstance(payload, dict) else None
            if isinstance(git, dict):
                branch = git.get("branch")
                if isinstance(branch, str) and branch:
                    return branch
        return None

    def _first_human_text(self) -> str | None:
        for line in self._lines:
            if line.is_human_turn:
                return _truncate(line.message_text(), _TASK_TEXT_CAP)
        return None


class CodexAdapter:
    """Introspect OpenAI Codex CLI sessions (a filesystem :class:`AgentAdapter`).

    Stateless: every method is read-only over the filesystem or pure, so one
    shared instance serves every workspace. Filesystem reads funnel through
    :class:`_CodexHome`; all parsing through :class:`_RolloutParser`.

    Codex cannot be launched with a chosen session id — its thread id is minted
    internally and ``--session-id`` is resume-only — so ``launch_decoration`` is
    empty and every Codex session reaches Grove through ``discover_sessions``,
    exactly like a hand-started Claude run.
    """

    kind = "codex"
    remote = False

    def launch_decoration(self, session_id: str) -> list[str]:
        """Empty — Codex mints its own thread id, with no flag to set it.

        ``_mint_agent_session_id`` returns ``None`` for any kind with an empty
        decoration, so Grove tracks the session purely through fs discovery.
        """
        del session_id
        return []

    def locate_transcripts(self, cwd: Path, session_id: str) -> list[Path]:
        try:
            return _CodexHome.locate(cwd, session_id)
        except OSError as exc:  # best-effort: a glob failure must not break peek
            logger.debug("locate_transcripts({}, {}) failed: {}", cwd, session_id, exc)
            return []

    def discover_sessions(self, cwd: Path, *, exclude_id: str | None = None) -> list[str]:
        try:
            return _CodexHome.discover(cwd, exclude_id=exclude_id)
        except OSError as exc:
            logger.debug("discover_sessions({}) failed: {}", cwd, exc)
            return []

    def list_sessions(self, cwd: Path) -> list[SessionSummary]:
        try:
            scanned = _CodexHome.discover_paths(cwd)
        except OSError as exc:
            logger.debug("list_sessions({}) failed: {}", cwd, exc)
            return []
        return [self._summarize(sid, path, mtime) for sid, path, mtime in scanned]

    def read_turns(
        self, cwd: Path, session_id: str, *, last: int | None = None
    ) -> tuple[SessionTurn, ...]:
        lines = self._read(self.locate_transcripts(cwd, session_id))
        return _RolloutParser(lines).turns(last=last)

    def parse_activity(self, cwd: Path, session_id: str) -> AgentActivity:
        lines = self._read(self.locate_transcripts(cwd, session_id))
        return _RolloutParser(lines).activity()

    def transcript_digest(self, cwd: Path, session_id: str) -> OrderedDigest:
        lines = self._read(self.locate_transcripts(cwd, session_id))
        return _RolloutParser(lines).digest()

    # ── internal ──────────────────────────────────────────────────────────
    def _summarize(self, session_id: str, path: Path, mtime: float) -> SessionSummary:
        """One session's listing row, from a single parse of its rollout."""
        lines = self._read([path])
        parser = _RolloutParser(lines)
        try:
            size_bytes = path.stat().st_size
        except OSError:
            size_bytes = 0
        activity = parser.activity()
        return SessionSummary(
            session_id=session_id,
            adapter_kind=self.kind,
            transcript_path=path,
            cwd=parser.recorded_cwd(),
            created_at=parser.created_at(),
            modified_at=(datetime.fromtimestamp(mtime, tz=UTC) if mtime > 0 else None),
            size_bytes=size_bytes,
            git_branch=parser.git_branch(),
            title=None,  # Codex has no session title concept.
            first_prompt=parser.first_human_text(),
            last_prompt=parser.last_human_text(),
            activity=activity,
        )

    @staticmethod
    def _read(paths: Sequence[Path]) -> list[_RolloutLine]:
        """Read and time-sort every line across the given files.

        Per-line ``try/except`` (a truncated final line never aborts the file)
        and a tolerated missing file (sessions get cleaned mid-read). Codex
        rollouts are single-file per session with no cross-file replay, so —
        unlike Claude's split-block dedup — there is no logical-record merge to
        do; each line is its own record.
        """
        lines: list[_RolloutLine] = []
        index = 0
        for path in paths:
            for raw in _CodexHome.read_lines(path):
                lines.append(_RolloutLine(raw=raw, index=index))
                index += 1
        lines.sort(key=lambda line: line.sort_key)
        return lines
