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
- **``apply_patch`` is a ``custom_tool_call``, not a ``function_call``.** The
  file editor is recorded as ``payload.type == "custom_tool_call"`` whose
  ``input`` is the raw ``*** Begin Patch`` … ``*** End Patch`` body (a plain
  string, NOT a JSON ``arguments`` blob), with the result mirrored back as a
  normal ``function_call_output`` sharing the ``call_id`` (verified on-host,
  codex 0.125.0). ``custom_tool_call`` is in the ``is_tool_call`` set so these
  edits are counted and rendered; ``_tool_entries`` reconstructs a ``FileEdit``
  from the patch body — before that they were silently invisible.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from grove.core.agents.model import (
    AgentActivity,
    AgentActivityState,
    AgentMessage,
    AgentQuestion,
    ContentBlock,
    DigestEntry,
    FileEdit,
    FinalResult,
    MessageRole,
    OrderedDigest,
    SessionControl,
    SessionControls,
    SessionSummary,
    SessionTurn,
    TodoList,
    final_result_from_messages,
    latest_todo_from_messages,
)
from grove.core.agents.transcript_cache import ResultMemo, TranscriptCache

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
    def base_dir() -> Path:
        """``$CODEX_HOME`` (or ``~/.codex``) — the config root the rollouts,
        prompts, and ``config.toml`` all live under."""
        raw = os.environ.get("CODEX_HOME", "").strip()
        return Path(raw).expanduser() if raw else Path.home() / ".codex"

    @classmethod
    def sessions_dir(cls) -> Path:
        """The ``sessions/`` root under ``$CODEX_HOME`` (or ``~/.codex``)."""
        return cls.base_dir() / "sessions"

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
        return [sid for sid, *_ in cls.discover_paths(cwd, exclude_id=exclude_id)]

    @classmethod
    def discover_paths(
        cls, cwd: Path, *, exclude_id: str | None = None
    ) -> list[tuple[str, Path, float, datetime | None]]:
        """``(session_id, path, mtime, birth)`` for every rollout recorded in
        ``cwd``, newest-first by mtime — the one scan behind ``discover`` (ids for
        the dashboard), ``discover_births`` (the cheap adoption pre-filter, #F5),
        and ``list_sessions`` (summaries for the explorer).

        Reads each rollout's head ``session_meta`` line for its id + cwd (the cwd
        is not on every line, unlike Claude), keeping only those whose cwd
        matches and confirming the id from the meta, not the filename. ``birth``
        (the first head record's timestamp) rides out of the same bounded head
        read — no full parse — so the adoption gate can reject history cheaply.
        """
        target = str(cwd)
        found: dict[str, tuple[Path, float, datetime | None]] = {}
        for path in cls._iter_rollouts():
            meta, birth = cls._meta_and_birth(path)
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
                found[session_id] = (path, mtime, birth)
        return [
            (sid, path, mtime, birth)
            for sid, (path, mtime, birth) in sorted(
                found.items(), key=lambda kv: (-kv[1][1], kv[0])
            )
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

        A projection of :meth:`_meta_and_birth` for callers that want only the
        meta (id / cwd lookups)."""
        return cls._meta_and_birth(path)[0]

    @classmethod
    def _meta_and_birth(cls, path: Path) -> tuple[dict[str, Any], datetime | None]:
        """The ``(session_meta.payload, birth)`` from ONE bounded head read.

        Bounded to a short head read — ``session_meta`` is the first record by
        construction — so a pathological file costs no more than a few lines.
        ``birth`` is the first head record's timestamp (records are time-sorted),
        so the cheap adoption pre-filter (#F5) reads a session's birth without a
        full parse.
        """
        payload: dict[str, Any] = {}
        birth: datetime | None = None
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
                    if not isinstance(rec, dict):
                        continue
                    if birth is None:
                        birth = _parse_timestamp(rec.get("timestamp"))
                    if not payload and rec.get("type") == "session_meta":
                        candidate = rec.get("payload")
                        payload = candidate if isinstance(candidate, dict) else {}
                    if payload and birth is not None:
                        break
        except OSError:
            return ({}, None)
        return (payload, birth)

    @classmethod
    def _meta_id(cls, path: Path) -> str | None:
        value = cls._meta(path).get("id")
        return value if isinstance(value, str) and value else None

    @classmethod
    def _meta_cwd(cls, path: Path) -> str | None:
        value = cls._meta(path).get("cwd")
        return value if isinstance(value, str) and value else None


class _CodexControls:
    """Resolves *which input controls* a Codex session exposes — the codex analog
    of :class:`_ClaudeControls` (#178).

    Codex's controls are user-global (its config root, not the worktree): custom
    prompts under ``$CODEX_HOME/prompts/*.md`` (invoked as ``/name`` in the codex
    TUI) and MCP servers declared as ``[mcp_servers.<name>]`` in
    ``$CODEX_HOME/config.toml``. No skills concept, so that list stays empty.
    Pure best-effort filesystem read — a missing dir or malformed TOML drops the
    source, never raises.
    """

    _MAX_ENTRIES = 500

    @classmethod
    def scan(cls) -> SessionControls:
        base = _CodexHome.base_dir()
        return SessionControls(
            commands=tuple(cls._scan_prompts(base / "prompts")),
            mcp_servers=tuple(cls._scan_mcp(base / "config.toml")),
        )

    @classmethod
    def _scan_prompts(cls, root: Path) -> list[SessionControl]:
        if not root.is_dir():
            return []
        out: list[SessionControl] = []
        try:
            for path in sorted(root.glob("*.md"))[: cls._MAX_ENTRIES]:
                if path.is_file():
                    out.append(SessionControl(name=path.stem, scope="user"))
        except OSError:
            return out
        return out

    @classmethod
    def _scan_mcp(cls, path: Path) -> list[SessionControl]:
        """``[mcp_servers.<name>]`` table keys from ``config.toml`` → server names.

        ``tomllib`` is stdlib (Grove targets Python ≥3.12); a read/parse failure
        yields no servers rather than raising."""
        import tomllib  # noqa: PLC0415 — local: only this scan needs the parser

        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return []
        servers = raw.get("mcp_servers") if isinstance(raw, dict) else None
        if not isinstance(servers, dict):
            return []
        return [
            SessionControl(name=str(name), scope="user")
            for name in list(servers)[: cls._MAX_ENTRIES]
        ]


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
        """A tool CALL — ``function_call``, ``tool_search_call``, or
        ``custom_tool_call`` (count these).

        The result side (``function_call_output`` / ``tool_search_output``) is
        NOT counted: like Claude, only the call side is a tool call, or every
        step doubles. ``custom_tool_call`` is Codex's freeform-tool record — the
        ``apply_patch`` file editor rides here, NOT as a ``function_call``
        (verified on-host: ``payload.type == "custom_tool_call"``, ``name ==
        "apply_patch"``, ``input`` a raw patch string) — so before it was in
        this tuple every ``apply_patch`` edit was invisible: uncounted, absent
        from turns/digest, silently skipped.
        """
        return self.record_type == "response_item" and self.payload_type in (
            "function_call",
            "tool_search_call",
            "custom_tool_call",
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

    @property
    def is_function_call(self) -> bool:
        """A ``function_call`` specifically (not ``tool_search_call``).

        The narrower gate question extraction uses: only a ``function_call``
        carries a ``call_id`` + a JSON-string ``arguments`` an MCP-bridged
        question tool fills. A ``tool_search_call`` has neither, so it never
        reaches ``from_tool_call``.
        """
        return self.record_type == "response_item" and self.payload_type == "function_call"

    @property
    def call_id(self) -> str | None:
        """The ``call_id`` correlating a ``function_call`` with its output."""
        value = self._payload.get("call_id")
        return value if isinstance(value, str) and value else None

    def parsed_arguments(self) -> dict[str, Any]:
        """A ``function_call``'s ``arguments`` as a dict.

        Codex serializes ``arguments`` as a JSON STRING (verified on-host); a
        ``tool_search_call`` already carries a dict. Either is accepted; a parse
        failure or a non-object yields ``{}`` — best-effort like the rest of the
        parser, so a junk payload drops the question, never raises.
        """
        args = self._payload.get("arguments")
        if isinstance(args, dict):
            return args
        if isinstance(args, str):
            try:
                parsed = json.loads(args)
            except (json.JSONDecodeError, ValueError):
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @property
    def is_custom_tool_call(self) -> bool:
        """A ``custom_tool_call`` specifically — Codex's freeform-tool record.

        The ``apply_patch`` file editor is recorded HERE, not as a
        ``function_call`` (verified on-host, codex 0.125.0): the distinguishing
        shape is a string ``input`` (the raw patch body) rather than a JSON
        ``arguments`` blob. Mirrors :attr:`is_function_call` so the two call
        shapes classify identically.
        """
        return self.record_type == "response_item" and self.payload_type == "custom_tool_call"

    def custom_tool_input(self) -> str | None:
        """A ``custom_tool_call``'s raw ``input`` string (``None`` if absent).

        The counterpart to :meth:`parsed_arguments` for the custom-tool shape —
        but ``custom_tool_call.input`` is NOT JSON (``function_call.arguments``
        is), it is the tool body verbatim: for ``apply_patch`` the ``*** Begin
        Patch`` … ``*** End Patch`` diff. Returned as-is, never parsed.
        """
        value = self._payload.get("input")
        return value if isinstance(value, str) else None

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

    @property
    def is_tool_call_output(self) -> bool:
        """A tool RESULT line — ``function_call_output`` or the ``apply_patch``
        result ``custom_tool_call_output``. Both feed the call→output answer map
        that resolves a question entry; neither is a counted call."""
        return self.record_type == "response_item" and self.payload_type in (
            "function_call_output",
            "custom_tool_call_output",
        )

    # ── spine mapping (#179) ─────────────────────────────────────────────────
    def to_message(self) -> AgentMessage | None:
        """Map this rollout line onto one agentic-loop spine message, or ``None``
        for a line that is not a loop message (``event_msg`` status/tokens,
        ``session_meta``, ``turn_context``).

        Codex records one native line per message OR tool call, so each spine
        message carries a single block: a human ``message`` → ``user`` (text); an
        assistant ``message`` → ``assistant`` (text); a ``function_call`` /
        ``custom_tool_call`` / ``tool_search_call`` → ``assistant`` (a ``tool_use``
        block); a ``reasoning`` → ``assistant`` (a ``thinking`` block, ``text``
        ``None`` when opaque); a call output → ``tool`` (a ``tool_result`` block).
        Codex has no per-message id, no per-message usage (usage is cumulative,
        on :class:`AgentActivity`), and no sub-agent threads — those stay unset."""
        role = self._spine_role()
        if role is None:
            return None
        content: tuple[ContentBlock, ...]
        if role == "tool":
            output = self._payload.get("output")
            content = (
                ContentBlock(
                    type="tool_result",
                    tool_use_id=self.call_id,
                    # Coerced like the old answer map: a non-string output resolves
                    # a question without inventing a body.
                    text=output if isinstance(output, str) else "",
                ),
            )
        elif self.is_tool_call:
            content = (self._tool_use_block(),)
        elif self.is_reasoning:
            content = (ContentBlock(type="thinking", text=self.reasoning_text()),)
        else:  # a plain human or assistant message
            content = (ContentBlock(type="text", text=self.message_text()),)
        return AgentMessage(role=role, content=content, timestamp=self.timestamp)

    def _spine_role(self) -> MessageRole | None:
        if self.is_human_turn:
            return "user"
        if self.is_assistant or self.is_tool_call or self.is_reasoning:
            return "assistant"
        if self.is_tool_call_output:
            return "tool"
        return None

    def _tool_use_block(self) -> ContentBlock:
        """A tool-call line as a ``tool_use`` block. ``tool_input`` is normalized
        to the dict each shape's normalizer consumes: a ``function_call``'s JSON
        ``arguments``, or a ``custom_tool_call``'s raw patch body wrapped as
        ``{"input": <patch>}`` (``apply_patch`` records the diff as a bare
        string, not JSON). A ``tool_search_call`` carries no args the projections
        read, so its input stays ``None``."""
        if self.is_function_call:
            tool_input: dict[str, Any] | None = self.parsed_arguments()
        elif self.is_custom_tool_call:
            tool_input = {"input": self.custom_tool_input()}
        else:
            tool_input = None
        return ContentBlock(
            type="tool_use",
            tool_name=self.tool_name(),
            tool_use_id=self.call_id,
            tool_input=tool_input,
        )

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
            started_at=self.created_at(),
        )

    def messages(self) -> tuple[AgentMessage, ...]:
        """The time-sorted rollout lines mapped onto the agentic-loop spine
        (#179) — the ONE representation :meth:`turns` and :meth:`digest` below
        both project (DRY: one parse, many projections). ``event_msg`` status /
        token lines and the ``session_meta`` / ``turn_context`` metadata map to
        nothing."""
        return tuple(msg for line in self._lines if (msg := line.to_message()) is not None)

    def digest(self) -> OrderedDigest:
        """Ordered ``user / assistant / tool`` skeleton; tool outputs stripped —
        a projection of :meth:`messages`."""
        entries: list[DigestEntry] = []
        for message in self.messages():
            if message.role == "user":
                entries.append(DigestEntry("user", _truncate(message.text(), _DIGEST_TEXT_CAP)))
            elif message.role == "assistant":
                for block in message.content:
                    if block.type == "text":
                        text = _truncate(block.text or "", _DIGEST_TEXT_CAP)
                        if text:
                            entries.append(DigestEntry("assistant", text))
                    elif block.type == "tool_use":
                        entries.append(DigestEntry("tool", block.tool_name or "tool"))
                    # thinking: excluded from the digest skeleton, as before.
            # role == "tool": a result carrier — excluded from the skeleton.
        return OrderedDigest(tuple(entries[-_DIGEST_MAX_ENTRIES:]))

    def turns(self, *, last: int | None = None) -> tuple[SessionTurn, ...]:
        """The conversation as :class:`SessionTurn` rows, oldest first — a
        projection of :meth:`messages`.

        Assistant / tool-call / reasoning messages that precede any human turn (a
        resumed head, or Codex's leading developer/preamble messages) collect
        under a leading turn with an empty ``user_text`` rather than being
        dropped.
        """
        messages = self.messages()
        # Pre-scan every tool_result block for the call→output answer map so a
        # question renders resolved wherever its output landed.
        answered: dict[str, str | None] = {}
        for message in messages:
            for block in message.content:
                if block.type == "tool_result" and block.tool_use_id is not None:
                    answered[block.tool_use_id] = block.text
        turns: list[SessionTurn] = []
        entries: list[DigestEntry] = []
        # ``current`` is the open turn's ``(user_text, started_at)`` — boxed so the
        # entry-adding closure can open a leading continuation turn (an assistant
        # message before any human prompt) without re-checking at each call site.
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

        for message in messages:
            if message.role == "user":
                if current[0] is not None or entries:
                    _flush()
                current[0] = (message.text(), message.timestamp)
            elif message.role == "assistant":
                for entry in self._assistant_entries(message, answered):
                    _add(entry, message.timestamp)
            # role == "tool": a result carrier — feeds `answered`, no entry.
        if current[0] is not None or entries:
            _flush()

        if last is not None:
            return tuple(turns[-last:]) if last > 0 else ()
        return tuple(turns)

    @staticmethod
    def _assistant_entries(
        message: AgentMessage, answered: Mapping[str, str | None]
    ) -> list[DigestEntry]:
        """One assistant message's content projected to turn entries: prose text,
        readable reasoning (a ``thinking`` block, rendered as an assistant line —
        never the opaque ``encrypted_content``), structured ``question`` rows for
        a question-shaped tool call, structured ``file_edit`` rows for a file-edit
        call (Codex's ``apply_patch`` ``custom_tool_call`` or an MCP-bridged edit
        ``function_call``), a structured ``todo`` row for an ``update_plan`` call,
        else one plain ``tool`` entry. A question is stamped with its matching
        output text (``answered``) at the group level."""
        entries: list[DigestEntry] = []
        for block in message.content:
            if block.type in ("text", "thinking"):
                if block.text and block.text.strip():
                    entries.append(DigestEntry("assistant", block.text))
            elif block.type == "tool_use" and block.tool_name:
                name = block.tool_name
                cid = block.tool_use_id
                questions = AgentQuestion.from_tool_call(name, block.tool_input, cid or "")
                if questions:
                    resolved = (
                        (q.resolved(answered[cid]) for q in questions)
                        if cid is not None and cid in answered
                        else questions
                    )
                    entries.extend(DigestEntry("question", q.prompt, question=q) for q in resolved)
                elif FileEdit.recognizes(name) and (
                    edits := FileEdit.from_tool_call(name, block.tool_input)
                ):
                    entries.extend(
                        DigestEntry("file_edit", f"{name} {e.path}".strip(), file_edit=e)
                        for e in edits
                    )
                elif TodoList.recognizes(name) and (
                    todos := TodoList.from_tool_call(name, block.tool_input)
                ):
                    entries.extend(DigestEntry("todo", lst.summary, todo=lst) for lst in todos)
                else:
                    entries.append(DigestEntry("tool", name))
        return entries

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


_MODELS_PROBE_TIMEOUT = 5.0
"""Seconds to wait on ``codex debug models``. The bundled catalog is instant
and offline; the bound only guards a wedged binary — best-effort never hangs."""


def _binary_of(command: str) -> str:
    """The executable (first shell token) of a launch command, or ``""``.

    ``AgentSpec.command`` may carry flags (``codex --full-auto``); model
    discovery needs only the binary. No var name is hard-coded — the binary
    comes from config, honoring a renamed/aliased ``codex``."""
    try:
        parts = shlex.split(command)
    except ValueError:  # unbalanced quotes in a hand-edited command
        return ""
    return parts[0] if parts else ""


def _probe_codex_models(binary: str) -> str | None:
    """Raw stdout of ``<binary> debug models``, or ``None`` on any failure.

    The ONE subprocess in this adapter — a read-only introspection of Codex's
    own bundled model catalog (offline, structured JSON). Best-effort by
    contract, exactly like the filesystem reads: a missing binary, a non-zero
    exit, or a timeout returns ``None`` (the caller offers an empty catalog),
    never raises. ``shell=False`` with a fixed list argv keeps it injection-safe;
    the bounded timeout keeps it non-hanging. This is the seam tests patch so
    the suite never shells out to a real ``codex``.
    """
    try:
        proc = subprocess.run(
            [binary, "debug", "models"],  # fixed argv, shell=False, bounded
            capture_output=True,
            text=True,
            timeout=_MODELS_PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("codex debug models ({}) failed: {}", binary, exc)
        return None
    if proc.returncode != 0:
        logger.debug("codex debug models ({}) exited {}", binary, proc.returncode)
        return None
    return proc.stdout


def _parse_codex_models(raw: str) -> tuple[str, ...]:
    """Slugs of the *listable* models in ``codex debug models`` JSON, in order.

    Keeps only ``visibility == "list"`` entries — dropping internal/hidden ones
    (``codex-auto-review`` is ``"hide"``) — orders by the catalog's own
    ``priority`` (0 first), and returns each ``slug``. Pure and best-effort:
    malformed JSON or an unexpected shape yields ``()`` (verified against real
    on-host output, codex-cli 0.125.0: ``{"models":[{slug,visibility,priority,…}]}``).
    """
    try:
        models = json.loads(raw).get("models", [])
    except (json.JSONDecodeError, AttributeError) as exc:
        logger.debug("codex debug models parse failed: {}", exc)
        return ()
    listable = [m for m in models if isinstance(m, dict) and m.get("visibility") == "list"]
    listable.sort(key=lambda m: m.get("priority", 1_000_000))
    return tuple(str(m["slug"]) for m in listable if m.get("slug"))


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
    resumable = True

    def launch_decoration(self, session_id: str, *, resume: bool = False) -> list[str]:
        """Empty for a fresh run — Codex mints its own thread id, with no flag to
        set it, so ``_mint_agent_session_id`` returns ``None`` and Grove tracks it
        purely through fs discovery.

        ``resume=True`` returns the ``resume <uuid>`` SUBCOMMAND (#120). Codex's
        grammar is ``codex [OPTIONS] <COMMAND> [ARGS]`` (top-level flags precede
        the subcommand), so this rides *after* the configured command verbatim —
        ``codex`` → ``codex resume <uuid>``, ``codex --full-auto`` → ``codex
        --full-auto resume <uuid>`` — no subcommand-injection machinery needed.
        Explicit-uuid resume bypasses Codex's own cwd matching, so the persisted
        primary is known without discovery.
        """
        if resume:
            return ["resume", session_id]
        del session_id
        return []

    def model_decoration(self, model: str) -> list[str]:
        """``--model <id>`` — Codex CLI's per-launch model selector (#96).

        Independent of correlation: Codex mints no session id, but it still
        honors ``--model`` at launch, so this rides the command even though
        ``launch_decoration`` is empty.
        """
        return ["--model", model]

    def offline_decoration(self) -> list[str]:
        """Pin the sandbox to ``workspace-write`` with networking off (#148):
        ``--sandbox workspace-write -c sandbox_workspace_write.network_access=false``.
        Codex has no standalone "disable web tool" flag — network access is a
        sandbox-policy knob, not a tool toggle, so this is the CLI's own
        network-off form rather than a Grove-invented one."""
        return [
            "--sandbox",
            "workspace-write",
            "-c",
            "sandbox_workspace_write.network_access=false",
        ]

    def telemetry_env(self) -> dict[str, str]:
        # Codex configures OpenTelemetry through `config.toml [otel]` (the
        # `codex-otel` crate), NOT env vars — there is no env switch to inject,
        # so the passthrough only ever hands Codex the OTLP endpoint/headers
        # (harmless) and its native OTel is enabled config-side. No-op here.
        return {}

    def available_models(self, command: str) -> tuple[str, ...]:
        """Codex's listable model slugs, read live from ``codex debug models``.

        The real *auto-refresh* path: Codex ships a structured, offline model
        catalog, so Grove READS it rather than hard-coding slugs that drift each
        release. The binary comes from the configured ``command`` (its first
        shell token). Best-effort — an unreadable catalog yields ``()`` and the
        picker falls back to a free-text field. The value is still forwarded
        verbatim on create, so an id absent from this list works fine.
        """
        raw = _probe_codex_models(_binary_of(command))
        return _parse_codex_models(raw) if raw is not None else ()

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

    def discover_births(
        self, cwd: Path, *, exclude_id: str | None = None
    ) -> list[tuple[str, datetime | None, float]]:
        """``(session_id, birth, mtime)`` for discovered rollouts — the cheap
        adoption pre-filter (#F5). Birth rides out of the same bounded
        ``session_meta`` head read discovery already does; no full parse.
        Best-effort: ``[]`` on any error."""
        try:
            return [
                (sid, birth, mtime)
                for sid, _path, mtime, birth in _CodexHome.discover_paths(
                    cwd, exclude_id=exclude_id
                )
            ]
        except OSError as exc:
            logger.debug("discover_births({}) failed: {}", cwd, exc)
            return []

    def list_sessions(self, cwd: Path) -> list[SessionSummary]:
        try:
            scanned = _CodexHome.discover_paths(cwd)
        except OSError as exc:
            logger.debug("list_sessions({}) failed: {}", cwd, exc)
            return []
        return [self._summarize(sid, path, mtime) for sid, path, mtime, _ in scanned]

    def read_turns(
        self, cwd: Path, session_id: str, *, last: int | None = None
    ) -> tuple[SessionTurn, ...]:
        paths = self.locate_transcripts(cwd, session_id)
        return _MEMO.get_or_compute(
            ("turns", str(cwd), session_id, last),
            paths,
            lambda: _RolloutParser(self._read(paths)).turns(last=last),
        )

    def read_messages(self, cwd: Path, session_id: str) -> tuple[AgentMessage, ...]:
        """The session's agentic-loop spine (#179) — the message list
        ``read_turns`` / ``transcript_digest`` project from, and the seam
        downstream fleet / trace / final-result consumers read. Codex has no
        sub-agent transcripts, so every message is main-thread."""
        paths = self.locate_transcripts(cwd, session_id)
        return _MEMO.get_or_compute(
            ("messages", str(cwd), session_id),
            paths,
            lambda: _RolloutParser(self._read(paths)).messages(),
        )

    def final_result(self, cwd: Path, session_id: str) -> FinalResult | None:
        """The session's terminal outcome (#149) — a projection of
        :meth:`read_messages`, never a second parser."""
        return final_result_from_messages(self.read_messages(cwd, session_id))

    def latest_todo(self, cwd: Path, session_id: str) -> TodoList | None:
        """The session's current todo/checklist state (#194) — a projection of
        :meth:`read_messages`, never a second parser. Codex has no Task-system
        analog (``TASK_TOOL_NAMES`` never matches an ``update_plan`` call), so
        this is always the plain whole-list-per-call read."""
        return latest_todo_from_messages(self.read_messages(cwd, session_id))

    def session_controls(self, cwd: Path, session_id: str) -> SessionControls:
        """Enumerate the session's input controls — the codex analog of Claude's
        TIER 1 scan (#178): user-global custom prompts + ``config.toml`` MCP
        servers (see :class:`_CodexControls`). ``cwd``/``session_id`` are unused —
        codex's control surface is config-root-global, not per-worktree — but stay
        in the signature per the seam contract. Best-effort: empty on any error."""
        del cwd, session_id
        try:
            return _CodexControls.scan()
        except OSError as exc:  # best-effort: a scan hiccup must not break the panel
            logger.debug("codex session_controls failed: {}", exc)
            return SessionControls.empty()

    def parse_activity(self, cwd: Path, session_id: str) -> AgentActivity:
        paths = self.locate_transcripts(cwd, session_id)
        return _MEMO.get_or_compute(
            ("activity", str(cwd), session_id),
            paths,
            lambda: _RolloutParser(self._read(paths)).activity(),
        )

    def transcript_digest(self, cwd: Path, session_id: str) -> OrderedDigest:
        paths = self.locate_transcripts(cwd, session_id)
        return _MEMO.get_or_compute(
            ("digest", str(cwd), session_id),
            paths,
            lambda: _RolloutParser(self._read(paths)).digest(),
        )

    @staticmethod
    def clear_caches() -> None:
        """Drop the incremental transcript cache + derived-result memo.

        A test seam (module-level caches outlive per-test tmp dirs) and an
        operational escape hatch; never needed on the hot path."""
        _TRANSCRIPTS.clear()
        _MEMO.clear()

    # ── internal ──────────────────────────────────────────────────────────
    def _summarize(self, session_id: str, path: Path, mtime: float) -> SessionSummary:
        """One session's listing row, from a single parse of its main rollout."""
        return _MEMO.get_or_compute(
            ("summary", session_id, str(path)),
            [path],
            lambda: self._summarize_uncached(session_id, path, mtime),
        )

    def _summarize_uncached(self, session_id: str, path: Path, mtime: float) -> SessionSummary:
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

        Codex rollouts are single-file per session with no cross-file replay,
        so — unlike Claude's split-block dedup — there is no logical-record
        merge to do; each line is its own record (:class:`_LineFolder` is a
        plain appender). Reading is incremental via :class:`TranscriptCache`
        (the daemon-CPU fix, 2026-07-11): a poll tick pays ``json.loads`` only
        for bytes appended since the previous read. The sort stays per call —
        appends keep the list nearly sorted, so timsort is cheap.
        """
        lines = _TRANSCRIPTS.read(paths)
        lines.sort(key=lambda line: line.sort_key)
        return lines


class _LineFolder:
    """Per path-set fold state behind :meth:`CodexAdapter._read` — a plain
    appender (no dedup/merge; see ``_read``'s docstring)."""

    __slots__ = ("_index", "_lines")

    def __init__(self) -> None:
        self._lines: list[_RolloutLine] = []
        self._index = 0

    def add(self, raw: dict[str, Any]) -> None:
        self._lines.append(_RolloutLine(raw=raw, index=self._index))
        self._index += 1

    def records(self) -> list[_RolloutLine]:
        return self._lines


_TRANSCRIPTS = TranscriptCache(_LineFolder)
_MEMO = ResultMemo()
