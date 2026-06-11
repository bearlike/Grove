"""Claude Code transcript introspection — the first :class:`AgentAdapter`.

One concern: turn Claude Code's session JSONL into the normalized
:class:`AgentActivity`. Decomposed into three atomic, private classes so each is
nameable in a sentence and testable on its own:

- :class:`_ClaudeHome` — *where* the transcripts live (config-dir resolution +
  the lossy cwd encoding + globbing). The only filesystem side effect.
- :class:`_Record` — *what one line is* (identity, classification, metrics for a
  single JSONL entry). All the "is this a real human turn?" subtlety lives here.
- :class:`_TranscriptParser` — *the aggregate* (one pass over de-duplicated,
  time-sorted records → activity + digest).

``ClaudeCodeAdapter`` is the thin public seam that wires filesystem → parser.

Grounding (verified against real on-host transcripts, Claude Code 2.1.x):
- Transcript path is ``<config>/projects/<encoded-cwd>/<session-uuid>.jsonl``;
  the encoding (every non-alphanumeric char → ``-``, per the Agent SDK sessions
  guide) is **lossy and non-reversible** (anthropics/claude-code#7009), so we
  glob by the known UUID and never decode the folder name back to a cwd.
- A ``type:"user"`` line is usually **not** a human turn: ``tool_result`` blocks
  carry ``role:"user"`` too (one real session: 4683 user lines, 80 real turns).
  The real-turn filter is the whole game — see :meth:`_Record.is_human_turn`.
- Assistant ``stop_reason`` is ``"tool_use"`` while working, ``"end_turn"`` when
  the turn completes — a status signal with no LLM required.
- Booleans like ``isSidechain`` arrive as JSON ``true``/``false`` but defensive
  coercion also tolerates the string forms; never trust the wire type.
"""

from __future__ import annotations

import json
import os
import re
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

# Markers that flag a ``type:"user"`` line as machinery, not a human turn:
# slash-command echoes, bash tool I/O, the post-compaction caveat banner, and
# the compaction summary prefix. Matching any one excludes the line.
_NON_HUMAN_MARKERS: tuple[str, ...] = (
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<local-command-stdout>",
    "<bash-input>",
    "<bash-stdout>",
    "<bash-stderr>",
    "Caveat:",
    "This session is being continued from a previous",
)

# Sentinel model id Claude Code writes for interrupts / synthetic lines; never a
# real model and never counted toward token usage or the displayed model.
_SYNTHETIC_MODEL = "<synthetic>"

_DIGEST_MAX_ENTRIES = 60
_DIGEST_TEXT_CAP = 200
_TASK_TEXT_CAP = 500


def _as_bool(value: Any) -> bool:
    """Coerce a JSON-ish truthy flag, tolerating the string forms.

    ``isSidechain`` / ``isMeta`` normally arrive as real booleans, but transcript
    records are heterogeneous external data — some tooling has emitted ``"false"``
    as a string, where a naive ``bool("false")`` is ``True``. Narrow at the edge.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _parse_timestamp(value: Any) -> datetime | None:
    """ISO-8601 (``...Z`` accepted) → aware datetime, or ``None`` on anything odd."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class _ClaudeHome:
    """Resolves *where* Claude Code keeps its transcripts.

    Pure path logic + read-only globbing. Reads the environment live on each
    call (not at construction) so a test can redirect ``CLAUDE_CONFIG_DIR`` per
    case and production picks up a relocated config dir without a restart.
    """

    @staticmethod
    def projects_dirs() -> list[Path]:
        """Every existing ``projects/`` dir across the config-dir cascade.

        Order: ``CLAUDE_CONFIG_DIR`` (comma-separated, like ccusage) → the XDG
        ``~/.config/claude`` → the legacy ``~/.claude``. De-duplicated, only the
        ones that exist.
        """
        candidates: list[Path] = []
        raw = os.environ.get("CLAUDE_CONFIG_DIR", "")
        for part in raw.split(","):
            cleaned = part.strip()
            if cleaned:
                candidates.append(Path(cleaned).expanduser())
        home = Path.home()
        candidates.append(home / ".config" / "claude")
        candidates.append(home / ".claude")

        seen: set[Path] = set()
        out: list[Path] = []
        for base in candidates:
            projects = base / "projects"
            key = projects.resolve() if projects.exists() else projects
            if key in seen:
                continue
            seen.add(key)
            if projects.is_dir():
                out.append(projects)
        return out

    @staticmethod
    def encode_cwd(cwd: Path) -> str:
        """Forward-encode a cwd to Claude's folder name.

        The documented rule (Agent SDK sessions guide) is *every*
        non-alphanumeric character → ``-`` — not just ``/`` ``.`` ``_`` — so a
        cwd containing ``@``, ``+``, or a space still hits the fast path. Only
        used to *guess* the most-likely directory; the encoding is lossy, so a
        miss falls back to a UUID glob — we never rely on decoding this back
        into a path.
        """
        return re.sub(r"[^A-Za-z0-9]", "-", str(cwd))

    @classmethod
    def locate(cls, cwd: Path, session_id: str) -> list[Path]:
        """All transcript files for ``session_id``: main thread first, sub-agents after.

        Globs by the unique UUID across every config dir (never by the lossy
        folder name). When the same UUID resolves under multiple project folders
        — collisions are possible per #7009 — the one whose first record's
        ``cwd`` matches ``cwd`` wins; otherwise all are returned and the parser's
        content-level de-dup sorts it out.
        """
        encoded = cls.encode_cwd(cwd)
        mains: list[Path] = []
        subagents: list[Path] = []
        for projects in cls.projects_dirs():
            # Fast path: the cwd we expect, checked directly before any glob.
            fast = projects / encoded / f"{session_id}.jsonl"
            if fast.is_file():
                mains.append(fast)
            for match in projects.glob(f"*/{session_id}.jsonl"):
                if match.is_file() and match not in mains:
                    mains.append(match)
            # Sub-agent transcripts (Claude Code 2.1.2+): <uuid>/subagents/agent-*.jsonl
            for match in projects.glob(f"*/{session_id}/subagents/agent-*.jsonl"):
                if match.is_file():
                    subagents.append(match)

        if len(mains) > 1:
            preferred = [p for p in mains if cls._first_cwd(p) == str(cwd)]
            if preferred:
                mains = preferred
        return [*mains, *subagents]

    @classmethod
    def discover(cls, cwd: Path, *, exclude_id: str | None) -> list[str]:
        """Session ids of transcripts recorded for ``cwd`` (excluding ``exclude_id``),
        ordered **most-recently-active first** (by transcript mtime).

        The newest-first order is load-bearing for the dashboard: a workspace
        with no Grove-minted id adopts the *live* session by taking the first
        result, so an arbitrary alphabetical order would surface a dead one.
        """
        return [sid for sid, _, _ in cls.discover_paths(cwd, exclude_id=exclude_id)]

    @classmethod
    def discover_paths(
        cls, cwd: Path, *, exclude_id: str | None = None
    ) -> list[tuple[str, Path, float]]:
        """``(session_id, transcript_path, mtime)`` for every session recorded
        in ``cwd``, newest-first by mtime — the one scan behind both
        ``discover`` (ids for the dashboard) and ``list_sessions`` (summaries
        for the explorer).

        Scans the forward-encoded candidate folder under each config dir — a
        single directory listing, not a recursive glob — and confirms each by
        the in-line ``cwd`` rather than trusting the lossy folder name.
        Sub-agent files (in a ``<uuid>/subagents/`` subdir) are skipped; only
        top-level ``<uuid>.jsonl`` session files count.
        """
        encoded = cls.encode_cwd(cwd)
        target = str(cwd)
        # id → (path, newest mtime) — one id can appear under multiple config dirs.
        found: dict[str, tuple[Path, float]] = {}
        for projects in cls.projects_dirs():
            folder = projects / encoded
            if not folder.is_dir():
                continue
            for path in folder.glob("*.jsonl"):
                session_id = path.stem
                if session_id == exclude_id or not path.is_file():
                    continue
                if cls._first_cwd(path) != target:
                    continue
                try:
                    mtime = path.stat().st_mtime
                except OSError:  # best-effort: a vanished file just sorts oldest
                    mtime = 0.0
                prior = found.get(session_id)
                if prior is None or mtime > prior[1]:
                    found[session_id] = (path, mtime)
        # Newest first (the running session); ties broken by id for a stable order.
        return [
            (sid, path, mtime)
            for sid, (path, mtime) in sorted(found.items(), key=lambda kv: (-kv[1][1], kv[0]))
        ]

    @staticmethod
    def _first_cwd(path: Path, *, max_lines: int = 200) -> str | None:
        """The ``cwd`` this session recorded, from the first line that carries one.

        Modern transcripts open with cwd-less preamble lines (``mode``,
        ``file-history-snapshot``, ``summary``); the ``cwd`` first appears a few
        lines in (the first ``attachment``/``user`` record). Returning line 0's
        cwd — as this used to — yields ``None`` for every real transcript, which
        silently breaks all cwd-based discovery and locate tie-breaking. Bounded
        by ``max_lines`` so a pathological file costs no more than a head-read;
        the cwd is always near the top in practice.
        """
        try:
            with path.open(encoding="utf-8") as fh:
                for index, line in enumerate(fh):
                    if index >= max_lines:
                        break
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        rec = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(rec, dict):
                        cwd = rec.get("cwd")
                        if isinstance(cwd, str) and cwd:
                            return cwd
        except OSError:
            return None
        return None


@dataclass(slots=True, frozen=True)
class _Record:
    """One transcript line, wrapped so every classification rule has one home.

    Holds the raw ``dict`` (heterogeneous external JSON, narrowed only through
    these typed accessors) plus the parse index for a stable sort tiebreak. The
    raw field stays ``Any``-typed on purpose — it's exactly the "genuinely
    heterogeneous external data, narrowed at the boundary" escape hatch.
    """

    raw: dict[str, Any]
    index: int

    # ── identity ──────────────────────────────────────────────────────────
    @property
    def type(self) -> str:
        value = self.raw.get("type")
        return value if isinstance(value, str) else ""

    @property
    def uuid(self) -> str | None:
        value = self.raw.get("uuid")
        return value if isinstance(value, str) else None

    @property
    def timestamp(self) -> datetime | None:
        return _parse_timestamp(self.raw.get("timestamp"))

    @property
    def is_sidechain(self) -> bool:
        return _as_bool(self.raw.get("isSidechain"))

    @property
    def is_meta(self) -> bool:
        return _as_bool(self.raw.get("isMeta"))

    @property
    def _message(self) -> dict[str, Any]:
        msg = self.raw.get("message")
        return msg if isinstance(msg, dict) else {}

    # ── content extraction ────────────────────────────────────────────────
    def _content_blocks(self) -> list[dict[str, Any]]:
        content = self._message.get("content")
        if isinstance(content, list):
            return [b for b in content if isinstance(b, dict)]
        return []

    def text(self) -> str:
        """Concatenated human-readable text (string content, or ``text`` blocks)."""
        content = self._message.get("content")
        if isinstance(content, str):
            return content
        parts = [
            block.get("text", "")
            for block in self._content_blocks()
            if block.get("type") == "text" and isinstance(block.get("text"), str)
        ]
        return "\n".join(p for p in parts if p)

    def _has_block(self, block_type: str) -> bool:
        return any(b.get("type") == block_type for b in self._content_blocks())

    # ── classification ────────────────────────────────────────────────────
    @property
    def is_human_turn(self) -> bool:
        """A real user message — the filter that separates 5 turns from 48 lines.

        ``type:"user"``, not a sub-agent line, not meta, carrying no
        ``tool_result`` block, and whose text isn't a slash-command echo, bash
        I/O, caveat banner, or compaction summary.
        """
        if self.type != "user" or self.is_sidechain or self.is_meta:
            return False
        if _as_bool(self.raw.get("isCompactSummary")):
            return False
        if self._has_block("tool_result"):
            return False
        body = self.text()
        if not body.strip():
            return False
        return not any(marker in body for marker in _NON_HUMAN_MARKERS)

    @property
    def is_assistant(self) -> bool:
        """A main-thread assistant API response (one reply in the turn loop)."""
        return self.type == "assistant" and not self.is_sidechain

    @property
    def stop_reason(self) -> str | None:
        value = self._message.get("stop_reason")
        return value if isinstance(value, str) else None

    @property
    def model(self) -> str | None:
        value = self._message.get("model")
        if isinstance(value, str) and value and value != _SYNTHETIC_MODEL:
            return value
        return None

    @property
    def git_branch(self) -> str | None:
        value = self.raw.get("gitBranch")
        return value if isinstance(value, str) and value else None

    @property
    def tool_use_count(self) -> int:
        return sum(1 for b in self._content_blocks() if b.get("type") == "tool_use")

    def turn_entries(self) -> list[DigestEntry]:
        """This assistant record's content in block order — text and tool calls
        interleaved exactly as they appeared, full text (the `sessions show`
        view, unlike the digest's truncated skeleton)."""
        entries: list[DigestEntry] = []
        for block in self._content_blocks():
            kind = block.get("type")
            if kind == "text":
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    entries.append(DigestEntry("assistant", text))
            elif kind == "tool_use" and block.get("name"):
                entries.append(DigestEntry("tool", str(block.get("name"))))
        return entries

    def tool_names(self) -> list[str]:
        return [
            str(b.get("name"))
            for b in self._content_blocks()
            if b.get("type") == "tool_use" and b.get("name")
        ]

    @property
    def usage_tokens(self) -> tuple[int, int]:
        """``(input, output)`` token totals, cache reads/writes folded into input.

        Folding cache tokens into "in" reflects the true context size the user is
        paying to carry, which is the dashboard-relevant number — not just the
        fresh, uncached slice.
        """
        usage = self._message.get("usage")
        if not isinstance(usage, dict):
            return (0, 0)

        def _int(key: str) -> int:
            v = usage.get(key)
            return v if isinstance(v, int) else 0

        tokens_in = (
            _int("input_tokens")
            + _int("cache_read_input_tokens")
            + _int("cache_creation_input_tokens")
        )
        return (tokens_in, _int("output_tokens"))

    @property
    def ai_title(self) -> str | None:
        if self.type != "ai-title":
            return None
        value = self.raw.get("aiTitle")
        return value if isinstance(value, str) and value else None

    @property
    def last_prompt(self) -> str | None:
        if self.type != "last-prompt":
            return None
        value = self.raw.get("lastPrompt")
        return value if isinstance(value, str) and value else None

    @property
    def dedup_key(self) -> str:
        """Stable identity for de-duping resume/fork overlap.

        Assistant lines key on ``message.id`` + ``requestId`` (ccusage's usage
        de-dup), so a re-emitted response from a forked file counts once. Other
        lines key on ``uuid``; with neither, the line is unique by parse index.
        """
        if self.type == "assistant":
            msg_id = self._message.get("id")
            req_id = self.raw.get("requestId")
            if isinstance(msg_id, str) and msg_id:
                return f"a:{msg_id}:{req_id if isinstance(req_id, str) else ''}"
        if self.uuid:
            return f"u:{self.uuid}"
        return f"i:{self.index}"


class _TranscriptParser:
    """Aggregates de-duplicated, time-sorted records into one :class:`AgentActivity`.

    Single pass. Owns the per-turn bucketing that yields ``replies_per_turn`` and
    the tail-status rule. Constructed from already-read records so it stays pure
    and unit-testable without touching the filesystem.
    """

    def __init__(self, records: Sequence[_Record]) -> None:
        self._records = records

    def activity(self) -> AgentActivity:
        if not self._records:
            return AgentActivity.empty(AgentActivityState.UNKNOWN)

        buckets: list[int] = []
        tool_calls = 0
        tokens_in = 0
        tokens_out = 0
        model: str | None = None
        title: str | None = None
        current_task: str | None = None
        last_event_at: datetime | None = None
        # The last record that is a human turn or an assistant reply — the tail
        # the status rule reads. Side records (titles, attachments) don't move it.
        tail: _Record | None = None

        for rec in self._records:
            ts = rec.timestamp
            if ts is not None and (last_event_at is None or ts > last_event_at):
                last_event_at = ts

            if rec.ai_title:
                title = rec.ai_title
                continue
            if rec.last_prompt:
                current_task = rec.last_prompt
                continue

            if rec.is_human_turn:
                buckets.append(0)
                current_task = current_task or _truncate(rec.text(), _TASK_TEXT_CAP)
                tail = rec
            elif rec.is_assistant:
                if buckets:
                    buckets[-1] += 1
                tool_calls += rec.tool_use_count
                t_in, t_out = rec.usage_tokens
                tokens_in += t_in
                tokens_out += t_out
                model = rec.model or model
                tail = rec

        # ``last-prompt`` is the authoritative current task when present; fall
        # back to the first real human turn's text captured above.
        if current_task is None:
            current_task = self._first_human_text()

        return AgentActivity(
            state=self._tail_state(tail),
            title=title,
            current_task=current_task,
            human_turns=len(buckets),
            assistant_replies=sum(buckets),
            replies_per_turn=tuple(buckets),
            tool_calls=tool_calls,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            last_event_at=last_event_at,
        )

    def digest(self) -> OrderedDigest:
        """Ordered ``user / assistant / tool`` skeleton, ``tool_result`` stripped."""
        entries: list[DigestEntry] = []
        for rec in self._records:
            if rec.is_human_turn:
                entries.append(DigestEntry("user", _truncate(rec.text(), _DIGEST_TEXT_CAP)))
            elif rec.is_assistant:
                names = rec.tool_names()
                if names:
                    entries.append(DigestEntry("tool", ", ".join(names)))
                else:
                    text = _truncate(rec.text(), _DIGEST_TEXT_CAP)
                    if text:
                        entries.append(DigestEntry("assistant", text))
        return OrderedDigest(tuple(entries[-_DIGEST_MAX_ENTRIES:]))

    def turns(self, *, last: int | None = None) -> tuple[SessionTurn, ...]:
        """The conversation as :class:`SessionTurn` rows, oldest first.

        Assistant records that precede any human turn (a resumed or compacted
        session whose head was filtered out) collect under a leading turn with
        an empty ``user_text`` rather than being dropped — `sessions show`
        renders it as a continuation block.
        """
        turns: list[SessionTurn] = []
        entries: list[DigestEntry] = []

        def _flush(user_text: str, started_at: datetime | None) -> None:
            turns.append(
                SessionTurn(user_text=user_text, started_at=started_at, entries=tuple(entries))
            )
            entries.clear()

        current: tuple[str, datetime | None] | None = None
        for rec in self._records:
            if rec.is_human_turn:
                if current is not None or entries:
                    _flush(*(current or ("", None)))
                current = (rec.text(), rec.timestamp)
            elif rec.is_assistant:
                if current is None and not entries and rec.timestamp is not None:
                    # Leading continuation block inherits the first reply's time.
                    current = ("", rec.timestamp)
                entries.extend(rec.turn_entries())
        if current is not None or entries:
            _flush(*(current or ("", None)))

        if last is not None:
            return tuple(turns[-last:]) if last > 0 else ()
        return tuple(turns)

    def first_human_text(self) -> str | None:
        """The first real prompt, truncated — the SDK's ``first_prompt`` analogue."""
        return self._first_human_text()

    def last_prompt_text(self) -> str | None:
        """The newest ``last-prompt`` record that actually carries text.

        Some ``last-prompt`` records are leafUuid-only pointers (verified
        on-host) — those are skipped, not treated as an empty prompt.
        """
        for rec in reversed(self._records):
            if rec.last_prompt:
                return rec.last_prompt
        return None

    def created_at(self) -> datetime | None:
        """Timestamp of the earliest timestamped record (records are time-sorted)."""
        for rec in self._records:
            if rec.timestamp is not None:
                return rec.timestamp
        return None

    def git_branch(self) -> str | None:
        """The branch the session first recorded (records carry ``gitBranch``)."""
        for rec in self._records:
            if rec.git_branch:
                return rec.git_branch
        return None

    def recorded_cwd(self) -> str | None:
        """The working directory the session recorded (first record carrying one)."""
        for rec in self._records:
            cwd = rec.raw.get("cwd")
            if isinstance(cwd, str) and cwd:
                return cwd
        return None

    def _first_human_text(self) -> str | None:
        for rec in self._records:
            if rec.is_human_turn:
                return _truncate(rec.text(), _TASK_TEXT_CAP)
        return None

    @staticmethod
    def _tail_state(tail: _Record | None) -> AgentActivityState:
        """Transcript-only status from the tail (epic §6).

        A human turn at the tail → WORKING (the agent's turn to respond). An
        assistant tail → WORKING while in the tool loop or mid-stream
        (``tool_use`` / no ``stop_reason``), WAITING once the turn closes
        (``end_turn`` / ``stop_sequence``). The IDLE refinement (tmux quiet) and
        STARTING (no file) are the ``ActivityService``'s blend, not the
        transcript's call.
        """
        if tail is None:
            return AgentActivityState.UNKNOWN
        if tail.is_human_turn:
            return AgentActivityState.WORKING
        if tail.stop_reason in ("end_turn", "stop_sequence"):
            return AgentActivityState.WAITING
        return AgentActivityState.WORKING


def _truncate(text: str, cap: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


class ClaudeCodeAdapter:
    """Introspect Claude Code sessions (the first concrete :class:`AgentAdapter`).

    Stateless: every method is read-only over the filesystem or pure, so one
    shared instance serves every workspace. Filesystem reads are funnelled
    through :class:`_ClaudeHome`; all parsing through :class:`_TranscriptParser`.
    """

    kind = "claude_code"

    def launch_decoration(self, session_id: str) -> list[str]:
        """``--session-id <uuid>`` — what makes correlation deterministic (#13)."""
        return ["--session-id", session_id]

    def locate_transcripts(self, cwd: Path, session_id: str) -> list[Path]:
        try:
            return _ClaudeHome.locate(cwd, session_id)
        except OSError as exc:  # best-effort: a glob failure must not break peek
            logger.debug("locate_transcripts({}, {}) failed: {}", cwd, session_id, exc)
            return []

    def discover_sessions(self, cwd: Path, *, exclude_id: str | None = None) -> list[str]:
        """Session ids of transcripts whose recorded cwd is ``cwd`` but that Grove
        didn't launch (out-of-band discovery, #18).

        Scans only the forward-encoded candidate folder per config dir (one
        directory listing — bounded), and confirms each by the in-line ``cwd``
        rather than trusting the lossy folder name. Excludes ``exclude_id`` (the
        Grove-launched session) so only the user's hand-started ``claude`` runs
        surface. Best-effort: returns ``[]`` on any error.
        """
        try:
            return _ClaudeHome.discover(cwd, exclude_id=exclude_id)
        except OSError as exc:
            logger.debug("discover_sessions({}) failed: {}", cwd, exc)
            return []

    def list_sessions(self, cwd: Path) -> list[SessionSummary]:
        """Normalized summaries for every session recorded in ``cwd``, newest-first.

        One full parse per main transcript (sub-agent files are excluded from
        the summary scope — they describe sidechains, not the session). The
        same parse yields both the listing metadata and the point-in-time
        ``activity``, so a listing never reads a file twice. Best-effort:
        a session that fails to read still lists with empty metadata.
        """
        try:
            scanned = _ClaudeHome.discover_paths(cwd)
        except OSError as exc:
            logger.debug("list_sessions({}) failed: {}", cwd, exc)
            return []
        return [self._summarize(sid, path, mtime) for sid, path, mtime in scanned]

    def read_turns(
        self, paths: Sequence[Path], *, last: int | None = None
    ) -> tuple[SessionTurn, ...]:
        records = self._read(paths)
        return _TranscriptParser(records).turns(last=last)

    def parse_activity(self, paths: Sequence[Path]) -> AgentActivity:
        records = self._read(paths)
        return _TranscriptParser(records).activity()

    def transcript_digest(self, paths: Sequence[Path]) -> OrderedDigest:
        records = self._read(paths)
        return _TranscriptParser(records).digest()

    # ── internal ──────────────────────────────────────────────────────────
    def _summarize(self, session_id: str, path: Path, mtime: float) -> SessionSummary:
        """One session's listing row, from a single parse of its main transcript."""
        records = self._read([path])
        parser = _TranscriptParser(records)
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
            title=activity.title,
            first_prompt=parser.first_human_text(),
            last_prompt=parser.last_prompt_text(),
            activity=activity,
        )

    @staticmethod
    def _read(paths: Sequence[Path]) -> list[_Record]:
        """Read, de-dup, and time-sort every record across the given files.

        Per-line ``try/except`` so a truncated final line never aborts the file;
        a missing file is tolerated (sessions get cleaned mid-read). De-dup keeps
        the first occurrence per key; the sort is stable on parse order for
        records that share (or lack) a timestamp.
        """
        records: list[_Record] = []
        index = 0
        for path in paths:
            for raw in _iter_json_lines(path):
                records.append(_Record(raw=raw, index=index))
                index += 1

        seen: set[str] = set()
        unique: list[_Record] = []
        for rec in records:
            key = rec.dedup_key
            if key in seen:
                continue
            seen.add(key)
            unique.append(rec)

        unique.sort(key=_sort_key)
        return unique


def _sort_key(rec: _Record) -> tuple[float, int]:
    ts = rec.timestamp
    # Records without a timestamp keep their parse position via the index, sorting
    # stably rather than jumping to the epoch.
    return (ts.timestamp() if ts is not None else 0.0, rec.index)


def _iter_json_lines(path: Path) -> Iterable[dict[str, Any]]:
    """Yield each parseable JSON object in ``path``; skip blanks and bad lines."""
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
        logger.debug("could not read transcript {}: {}", path, exc)
        return
