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

Grounding (verified against real on-host rollouts, codex 0.125.0):

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
  trailing assistant ``message`` → WAITING). Codex *approval* prompts are
  interactive and never persisted, same as Claude permission prompts being
  hook-only — but a **question** is persisted, so BLOCKED is transcript-visible
  here and outranks task pairing (next point).
- **Questions are a native tool call, not an interactive prompt.** Codex asks
  the human through ``request_user_input``, an ordinary ``function_call`` whose
  JSON-string ``arguments`` carry the same ``questions[]`` batch shape Claude's
  ``AskUserQuestion`` does, and whose ``function_call_output`` carries
  ``{"answers": {<question id>: {"answers": [...]}}}``. The rollout is flushed
  record-by-record as the turn runs (measured live on codex-cli 0.147.0), so a
  call with no output yet IS a question standing on screen — which is what makes
  a transcript-only BLOCKED honest for Codex where it is impossible for Claude.
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

import contextlib
import json
import os
import sqlite3
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from grove.core.agents.base import AgentVersionProbe
from grove.core.agents.model import (
    AgentActivity,
    AgentActivityState,
    AgentMessage,
    AgentQuestion,
    CompactionBoundary,
    ContentBlock,
    DigestEntry,
    FileEdit,
    FinalResult,
    MessageRole,
    OrderedDigest,
    QueuedMessage,
    SessionControl,
    SessionControls,
    SessionRef,
    SessionSummary,
    SessionTurn,
    TodoList,
    TokenUsage,
    ToolCall,
    ToolOutcome,
    final_result_from_messages,
    latest_todo_from_messages,
    tool_outcomes,
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
        the dashboard), ``discover_births`` (the cheap adoption pre-filter),
        and ``list_sessions`` (summaries for the explorer).

        Expressed over :meth:`discover_all` (unlike Claude's own
        ``discover_paths``, which is left untouched — see that method's
        docstring): Codex's path is date-partitioned and carries no cwd, so
        its "narrow" per-cwd scan was ALREADY a full store walk + a cwd
        filter — there is no cheaper form to preserve, so folding it onto the
        shared broader walk is a genuine DRY win rather than a hot-path
        regression. ``discover_all`` reads each rollout's head exactly once
        (the same read this method used to perform inline); this just filters
        and re-shapes the tuple.
        """
        target = str(cwd)
        found: dict[str, tuple[Path, float, datetime | None]] = {}
        for ref in cls.discover_all():
            if ref.session_id == exclude_id or ref.cwd != target:
                continue
            if ref.transcript_path is None:
                continue
            prior = found.get(ref.session_id)
            if prior is None or ref.mtime > prior[1]:
                found[ref.session_id] = (ref.transcript_path, ref.mtime, ref.birth)
        return [
            (sid, path, mtime, birth)
            for sid, (path, mtime, birth) in sorted(
                found.items(), key=lambda kv: (-kv[1][1], kv[0])
            )
        ]

    @classmethod
    def discover_all(cls) -> tuple[SessionRef, ...]:
        """Every rollout in the store, host-wide — the catalog's discovery unit.

        For Codex this is the SAME walk :meth:`discover_paths` already had to
        perform (date-partitioned paths carry no cwd, so there is no
        cheaper-than-full-store scan) — one bounded head read per rollout via
        :meth:`_meta_and_birth`, never a full parse. ``git_branch`` comes free
        from the same ``session_meta.payload`` the cwd/id/birth already read
        (``payload.git.branch``, 151/168 rollouts on the reference host).
        Best-effort: a malformed or vanished file is skipped, never raised; a
        rollout with no ``id`` in its meta is skipped (unidentifiable), but one
        with no ``cwd`` still yields a ref with ``cwd=None``. ``size_bytes``
        comes free too, off the same ``stat()`` call that already produces
        ``mtime`` — a single ``stat_result`` answers both.
        """
        refs: list[SessionRef] = []
        for path in cls._iter_rollouts():
            meta, birth = cls._meta_and_birth(path)
            session_id = meta.get("id")
            if not isinstance(session_id, str) or not session_id:
                continue
            cwd = meta.get("cwd")
            cwd = cwd if isinstance(cwd, str) and cwd else None
            git = meta.get("git")
            branch = git.get("branch") if isinstance(git, dict) else None
            branch = branch if isinstance(branch, str) and branch else None
            try:
                st = path.stat()
                mtime = st.st_mtime
                size_bytes: int | None = st.st_size
            except OSError:  # best-effort: a vanished file just sorts oldest
                mtime = 0.0
                size_bytes = None
            refs.append(
                SessionRef(
                    session_id=session_id,
                    adapter_kind="codex",
                    cwd=cwd,
                    transcript_path=path,
                    birth=birth,
                    mtime=mtime,
                    git_branch=branch,
                    size_bytes=size_bytes,
                )
            )
        return tuple(sorted(refs, key=lambda ref: (-ref.mtime, ref.session_id)))

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
        so the cheap adoption pre-filter reads a session's birth without a
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


class _CodexQueue:
    """Resolves *what Codex is still holding* for a thread — a SQLite read.

    Codex's queue is not in the rollout, and that is a census rather than a
    search that came up empty: 194 rollouts / 208,777 records on the reference
    host enumerate 28 distinct ``(type, payload.type)`` pairs, none of them
    queue-shaped, and there are no sidecar files. It lives in
    ``$CODEX_HOME/queue_1.sqlite``, table ``queued_items(id, thread_id,
    payload_json, queue_order, created_at_ms, updated_at_ms)``, joined to a
    session by ``thread_id``.

    Two properties this class is built around:

    * **The database is WAL and belongs to a running Codex**, so it is opened
      strictly read-only (``?mode=ro``) — Grove never takes a write lock on
      another process's live store, and a URI that cannot open simply answers
      nothing.
    * **A missing file or table is UNSUPPORTED, never an error.** Older Codex
      builds ship neither, and a host that has never run Codex has no config
      root at all; treating that as a failure would put a red state on every
      workspace of a provider that is working perfectly.

    The table was observed to EXIST and be EMPTY on the reference host, so
    ``payload_json``'s shape is unmeasured — hence :meth:`_text`'s deliberate
    fallback rather than a guessed key path.
    """

    FILENAME = "queue_1.sqlite"
    TABLE = "queued_items"
    TIMEOUT_SECONDS = 2.0
    """Bounds a store held by a busy writer. Every caller is a per-request read
    behind the executor, so a wedged sqlite must cost a bounded wait, not a
    thread."""

    @classmethod
    def path(cls) -> Path:
        """The queue database, resolved through the SAME config-root seam the
        rollouts and prompts use — one definition of where Codex lives, so a
        relocated ``$CODEX_HOME`` moves all three together."""
        return _CodexHome.base_dir() / cls.FILENAME

    @classmethod
    def pending(cls, thread_id: str) -> tuple[QueuedMessage, ...]:
        """Everything queued for ``thread_id``, in the harness's own
        ``queue_order``. ``()`` for a store, table or thread that has nothing —
        the three are indistinguishable here on purpose (see the class
        docstring); the ``supported`` flag one layer up carries the distinction
        that matters."""
        path = cls.path()
        if not thread_id or not path.exists():
            return ()
        try:
            with contextlib.closing(
                sqlite3.connect(
                    f"file:{path}?mode=ro",
                    uri=True,
                    timeout=cls.TIMEOUT_SECONDS,
                )
            ) as conn:
                rows = conn.execute(
                    f"SELECT payload_json, created_at_ms FROM {cls.TABLE} "
                    "WHERE thread_id = ? ORDER BY queue_order",
                    (thread_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            # Includes the no-such-table case for a build predating the queue.
            logger.debug("codex queue read failed for {}: {}", thread_id, exc)
            return ()
        return tuple(
            QueuedMessage(text=cls._text(payload), sent_at=cls._at(created_ms), position=i)
            for i, (payload, created_ms) in enumerate(rows)
        )

    @staticmethod
    def _text(payload: Any) -> str:
        """The message a payload row holds.

        The column's shape is UNOBSERVED (the table was empty every time it was
        read), so this reads the two shapes that would need no interpretation —
        a bare JSON string, or an object carrying a ``text`` — and otherwise
        hands back the stored value verbatim. Showing the raw payload is honest
        where guessing a key path would silently show nothing the day the shape
        is not what someone imagined.
        """
        if not isinstance(payload, str):
            return ""
        try:
            decoded = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return payload
        if isinstance(decoded, str):
            return decoded
        if isinstance(decoded, dict):
            value = decoded.get("text")
            if isinstance(value, str):
                return value
        return payload

    @staticmethod
    def _at(created_ms: Any) -> datetime | None:
        if not isinstance(created_ms, int):
            return None
        try:
            return datetime.fromtimestamp(created_ms / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None


class _CodexControls:
    """Resolves *which input controls* a Codex session exposes — the codex analog
    of :class:`_ClaudeControls`.

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

    @property
    def is_exec_command_end(self) -> bool:
        """A completed shell call's OWN measurement — ``event_msg``
        ``exec_command_end`` — the status/tokens half of the dual-record rule
        (never the conversation): it carries ``duration``/``exit_code`` for the
        ``exec_command`` ``function_call`` sharing its ``call_id``, written once
        the process exits.

        **Version-gated, not universal — verified 2026-08-11 against every
        rollout on this host.** Present (and at 100% coverage of the matching
        ``exec_command`` calls) on codex-cli 0.122.0/0.125.0 only; ABSENT on
        every older version sampled (0.93.0-0.121.x) and on the currently
        installed 0.147.0, which replaced it with an unrelated shape
        (``event_msg`` ``item_completed`` wrapping an ``item.type ==
        "CommandExecution"``, keyed by the item's own id with no correlation
        field back to the owning tool call found on this host) — see
        agents/CLAUDE.md. Reading this record is therefore a real improvement
        for rollouts written by the versions that emit it and a safe no-op
        everywhere else, exactly like every other defensive read in this file.
        """
        return self.record_type == "event_msg" and self.payload_type == "exec_command_end"

    def exec_command_end_metrics(self) -> tuple[str, int | None, int] | None:
        """``(call_id, duration_ms, exit_code)`` for this line, or ``None`` for
        any other line or a malformed record.

        ``duration`` arrives as a Rust ``Duration`` struct — ``{"secs": int,
        "nanos": int}``, NEVER a bare number — converted to milliseconds here so
        every consumer of ``ToolCall.duration_ms`` shares one unit; a malformed
        shape yields ``None`` rather than a fabricated duration.  ``exit_code``
        is required (a plain int) for the whole record to count: 707/707 real
        records measured on-host carry both fields together, so a call lacking
        one alongside the other is unmeasured, not partially measured.
        """
        if not self.is_exec_command_end:
            return None
        call_id = self._payload.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            return None
        exit_code = self._payload.get("exit_code")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            return None
        return (call_id, self._exec_duration_ms(self._payload.get("duration")), exit_code)

    @staticmethod
    def _exec_duration_ms(value: Any) -> int | None:
        """A Rust ``Duration`` struct (``{"secs": int, "nanos": int}``) → whole
        milliseconds, or ``None`` on any other shape."""
        if not isinstance(value, dict):
            return None
        secs = value.get("secs")
        nanos = value.get("nanos")
        if not isinstance(secs, int) or isinstance(secs, bool):
            return None
        if not isinstance(nanos, int) or isinstance(nanos, bool):
            nanos = 0
        return secs * 1000 + nanos // 1_000_000

    @property
    def is_compaction(self) -> bool:
        """A compaction boundary — the harness replacing history with a summary.

        The key is a TOP-LEVEL ``type:"compacted"`` record, present in every
        codex-cli version on this host from 0.93.0 through 0.147.0 (132 records
        across 40 real rollouts). The ``event_msg``/``context_compacted`` mirror
        is deliberately NOT the key: it carries no content and is ABSENT
        entirely in 0.147.0, so keying on it would go blind on the current
        release — the one place the dual-record rule's ``event_msg`` half is not
        merely redundant but wrong.
        """
        return self.record_type == "compacted"

    def compaction_boundary(self) -> CompactionBoundary:
        """This ``compacted`` line as a :class:`CompactionBoundary`.

        Two fields are structurally unanswerable here, and both stay ``None``
        rather than being guessed:

        * **``trigger``** — no Codex version records one anywhere in the rollout,
          so manual vs automatic is genuinely unknown. Defaulting it either way
          would be indistinguishable on the wire from Claude's measured value.
        * **``dropped_tokens``** — no per-compaction token accounting exists.

        ``summary`` reads ``payload.message`` because that is where the field
        lives, but it measured EMPTY on 132 of 132 real records: the
        ``replacement_history`` beside it is encrypted, so ``""`` is a fact about
        the format rather than a parse failure. Reading the field anyway costs
        nothing and is what would surface a future Codex that fills it.
        """
        message = self._payload.get("message")
        return CompactionBoundary(
            trigger=None,
            at=self.timestamp,
            dropped_tokens=None,
            summary=message if isinstance(message, str) else "",
        )

    # ── spine mapping ─────────────────────────────────────────────────────────
    def to_message(
        self, exec_metrics: Mapping[str, tuple[int | None, int]] | None = None
    ) -> AgentMessage | None:
        """Map this rollout line onto one agentic-loop spine message, or ``None``
        for a line that is not a loop message (``event_msg`` status/tokens,
        ``session_meta``, ``turn_context``).

        Codex records one native line per message OR tool call, so each spine
        message carries a single block: a human ``message`` → ``user`` (text); an
        assistant ``message`` → ``assistant`` (text); a ``function_call`` /
        ``custom_tool_call`` / ``tool_search_call`` → ``assistant`` (a ``tool_use``
        block); a ``reasoning`` → ``assistant`` (a ``thinking`` block, ``text``
        ``None`` when opaque); a call output → ``tool`` (a ``tool_result`` block).
        Usage rides its own ``token_count`` line, so it is stamped on by
        :meth:`_RolloutParser.messages` rather than here; Codex has no
        per-message id and no sub-agent threads, so those stay unset.

        ``exec_metrics`` is the ``call_id`` → ``(duration_ms, exit_code)`` map
        :meth:`_RolloutParser._exec_metrics` pre-scans once per parse — the same
        shape ``token_count`` usage already uses, one native fact from a sibling
        ``event_msg`` record landing on the ``response_item`` it belongs to.
        ``None``/absent for every line that is not a resolved ``exec_command``
        result, which is every line on a CLI version that never wrote
        ``exec_command_end`` at all (see :attr:`is_exec_command_end`)."""
        role = self._spine_role()
        if role is None:
            return None
        if role == "compaction":
            # Contentless by contract: the payload cannot be a content block,
            # and Codex's own replacement history is encrypted anyway.
            return AgentMessage(
                role=role, timestamp=self.timestamp, compaction=self.compaction_boundary()
            )
        content: tuple[ContentBlock, ...]
        if role == "tool":
            metrics = exec_metrics.get(self.call_id or "") if exec_metrics else None
            content = (
                ContentBlock(
                    type="tool_result",
                    tool_use_id=self.call_id,
                    text=self._output_text(self._payload.get("output")),
                    duration_ms=metrics[0] if metrics else None,
                    exit_code=metrics[1] if metrics else None,
                ),
            )
        elif self.is_tool_call:
            content = (self._tool_use_block(),)
        elif self.is_reasoning:
            content = (ContentBlock(type="thinking", text=self.reasoning_text()),)
        else:  # a plain human or assistant message
            content = (ContentBlock(type="text", text=self.message_text()),)
        return AgentMessage(role=role, content=content, timestamp=self.timestamp)

    @staticmethod
    def _output_text(output: Any) -> str:
        """A ``function_call_output``'s body as text — SHAPE normalization only.

        Codex writes ``output`` in two shapes, and the second is not rare:
        measured over 9016 real outputs in the last 120 rollouts on this host,
        **7064 are a bare string and 1952 are a content-block LIST** of
        ``{"type": "input_text", "text": …}`` — the same two-shape split Claude's
        ``tool_result.content`` has, which is why this mirrors
        ``claude_code._Record._result_text`` rather than inventing a rule. Until
        it did, every list-shaped output coerced to ``""`` and roughly a fifth of
        Codex's tool responses reached the wire empty while the record held them.

        Any dict carrying a string ``text`` contributes, whatever its ``type``
        tag: an image part legitimately has no text and drops out, and matching
        the tag would re-break the moment Codex renames it.
        """
        if isinstance(output, str):
            return output
        if isinstance(output, list):
            return "\n".join(
                part["text"]
                for part in output
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        return ""

    def _spine_role(self) -> MessageRole | None:
        if self.is_compaction:
            return "compaction"
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

    def turn_context_model(self) -> str | None:
        """``turn_context.model`` — the model announced for the turn that
        FOLLOWS this line, or ``None`` for any other line.

        The per-line sibling of :meth:`_RolloutParser.model`, which answers for
        the session as a whole. Both exist because they answer different
        questions: the session-level one names what the session ran, this one
        lets a run whose model changed mid-session attribute each half to the
        model that actually served it.
        """
        if self.record_type != "turn_context":
            return None
        payload = self.raw.get("payload")
        if not isinstance(payload, dict):
            return None
        value = payload.get("model")
        return value if isinstance(value, str) and value else None

    def turn_usage(self) -> TokenUsage | None:
        """``token_count.info.last_token_usage`` as a :class:`TokenUsage` — the
        usage of the model request that just completed, or ``None`` for any
        other line.

        The PER-TURN sibling of :attr:`usage_tokens` (which reads the CUMULATIVE
        ``total_token_usage``): attaching the cumulative total to a message would
        multiply-count the session, since every report restates every earlier
        turn.

        Two field-semantics differences from Claude's ``usage`` object, both
        normalized here so no downstream consumer needs a Codex branch:

        - Codex's ``input_tokens`` INCLUDES ``cached_input_tokens`` (verified
          on-host: ``total_tokens == input_tokens + output_tokens`` while cached
          grows inside input), where ``TokenUsage.input`` is contracted as the
          FRESH input the cost layer charges at the full rate. Left un-netted the
          cached tokens would be billed twice — once as input, once as cache
          read.
        - ``reasoning_output_tokens`` maps onto ``TokenUsage.reasoning``, which
          is informational only: like every provider that reports it, Codex
          counts it INSIDE ``output_tokens``, and the price book deliberately
          does not charge it as a fifth class.

        An absent field stays ``None`` — a fabricated zero reads as a measured
        zero.
        """
        if self.record_type != "event_msg" or self.payload_type != "token_count":
            return None
        info = self._payload.get("info")
        if not isinstance(info, dict):
            return None
        last = info.get("last_token_usage")
        if not isinstance(last, dict):
            return None

        def _int(key: str) -> int | None:
            value = last.get(key)
            return value if isinstance(value, int) else None

        raw_input, cached = _int("input_tokens"), _int("cached_input_tokens")
        fresh = raw_input - cached if raw_input is not None and cached is not None else raw_input
        return TokenUsage(
            input=fresh,
            output=_int("output_tokens"),
            cache_creation=_int("cache_write_input_tokens"),
            cache_read=cached,
            reasoning=_int("reasoning_output_tokens"),
        )

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
        message → WAITING. Empty/missing → UNKNOWN.

        BLOCKED is decided by the caller, NOT here, and it outranks every answer
        this method can give: a pending ``request_user_input`` sits inside an
        unfinished turn, so task pairing legitimately reads WORKING for the whole
        time the human is being asked. Codex *approval* prompts remain
        interactive and unpersisted — they are still invisible to any transcript
        reader.
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
        last_event_at: datetime | None = None
        events = _EventState()
        # The last response_item that is a human turn, assistant reply, or tool
        # call — the tail the fallback status rule reads.
        tail: _RolloutLine | None = None
        # Question calls whose ``function_call_output`` has not landed yet, keyed
        # by call_id in ask order. See :meth:`_track_question`.
        open_questions: dict[str, tuple[AgentQuestion, ...]] = {}

        for line in self._lines:
            ts = line.timestamp
            if ts is not None and (last_event_at is None or ts > last_event_at):
                last_event_at = ts
            if events.consume(line):
                continue

            if line.is_human_turn:
                buckets.append(0)
                tail = line
            elif line.is_assistant:
                if buckets:
                    buckets[-1] += 1
                tail = line
            elif line.is_tool_call:
                tool_calls += 1
                tail = line
                self._track_question(line, open_questions)
            elif line.is_tool_call_output and line.call_id:
                # The answer (or an Esc-cancel / error string) resolves the whole
                # group — the same group-level rule ``turns()`` applies.
                open_questions.pop(line.call_id, None)

        # One selection helper, shared with `current_task_text()`, so the
        # capped field on this ~1 Hz-delivered activity and the uncapped
        # per-request read can never name different text.
        raw_task = self.current_task_text()
        current_task = _truncate(raw_task, _TASK_TEXT_CAP) if raw_task is not None else None

        pending = tuple(q for group in open_questions.values() for q in group)

        return AgentActivity(
            state=AgentActivityState.BLOCKED if pending else events.state(tail),
            questions=pending,
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

    @staticmethod
    def _track_question(
        line: _RolloutLine, open_questions: dict[str, tuple[AgentQuestion, ...]]
    ) -> None:
        """Record a question-shaped tool call as still open, if it is one.

        Codex's ask-the-human tool (``request_user_input``) is an ordinary
        ``function_call``, so the ONLY thing separating "the human is being
        asked" from "the human already answered" is whether the matching
        ``function_call_output`` has been written — and it is written whatever
        happened (an answers object, an ``aborted by user after Ns`` string, an
        unavailable-in-this-mode error), so a group with no output is genuinely
        outstanding rather than merely un-normalizable.

        Recognition goes through :meth:`AgentQuestion.recognizes` so "is this a
        question" cannot drift between this status path and the turn renderer,
        and normalization through the shared ``from_tool_call`` seam so there is
        no second question shape. A call whose payload does not normalize is not
        tracked: an activity that reported BLOCKED with nothing to render would
        be a workspace the user cannot act on.
        """
        call_id = line.call_id
        if call_id is None or not AgentQuestion.recognizes(line.tool_name()):
            return
        questions = AgentQuestion.from_tool_call(line.tool_name(), line.parsed_arguments(), call_id)
        if questions:
            open_questions[call_id] = questions

    def messages(self) -> tuple[AgentMessage, ...]:
        """The time-sorted rollout lines mapped onto the agentic-loop spine
        — the ONE representation :meth:`turns` and :meth:`digest` below
        both project (DRY: one parse, many projections). ``session_meta`` /
        ``turn_context`` metadata and the ``event_msg`` mirrors map to nothing,
        except ``token_count``, whose per-turn usage lands on the assistant
        message it belongs to.

        Codex records usage as its OWN line, written once the model request it
        reports on has finished, so the owner is the newest assistant message
        preceding it (the reply, tool call or reasoning that request produced).
        Consuming the claim (``pending = None``) is what keeps a second report
        from re-stamping an already-attributed message, and an absent
        ``token_count`` leaves ``usage`` unset rather than zeroed.

        ``exec_command_end`` (a completed shell call's own duration/exit-status
        measurement) is the identical shape one step further: it is also an
        ``event_msg`` sibling of a ``response_item``, so it is pre-scanned once
        (:meth:`_exec_metrics`) and handed to :meth:`_RolloutLine.to_message`,
        which stamps it onto the ``tool_result`` block it belongs to by
        ``call_id`` — never a second pass over ``self._lines``.
        """
        exec_metrics = self._exec_metrics()
        out: list[AgentMessage] = []
        pending: int | None = None
        model = self.model()
        for line in self._lines:
            usage = line.turn_usage()
            if usage is not None:
                if pending is not None:
                    out[pending] = replace(out[pending], usage=usage)
                    pending = None
                continue
            # `turn_context` announces the model for the turn that FOLLOWS it, so
            # a session whose model was switched mid-run attributes each half
            # correctly rather than to whichever value happened to be first.
            turn_model = line.turn_context_model()
            if turn_model is not None:
                model = turn_model
            message = line.to_message(exec_metrics)
            if message is None:
                continue
            if message.role == "assistant":
                pending = len(out)
                # Usage without a model prices to nothing: the cost layer looks
                # the rate up BY model, so an unnamed generation carries tokens
                # and no cost however well the price book is configured. Codex
                # names it per turn; it just never reached the spine.
                message = replace(message, model=model)
            out.append(message)
        return tuple(out)

    def _exec_metrics(self) -> dict[str, tuple[int | None, int]]:
        """``call_id`` → ``(duration_ms, exit_code)`` for every completed
        ``exec_command`` call this rollout measured natively.

        One pass over ``self._lines`` reading only ``event_msg``
        ``exec_command_end`` records (see :attr:`_RolloutLine.is_exec_command_end`
        for the CLI-version gate); every other line contributes nothing. A
        repeated ``call_id`` — not expected, Codex mints them per call — keeps
        the LAST record, matching :func:`tool_outcomes`'s own last-write-wins
        rule for a repeated id.
        """
        out: dict[str, tuple[int | None, int]] = {}
        for line in self._lines:
            entry = line.exec_command_end_metrics()
            if entry is not None:
                call_id, duration_ms, exit_code = entry
                out[call_id] = (duration_ms, exit_code)
        return out

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
            elif message.role == "compaction" and message.compaction is not None:
                # Headline only — the digest strips payloads by design, exactly
                # as the Claude adapter does for the same entry.
                entries.append(DigestEntry("compaction", message.compaction.headline()))
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
        # Pre-scan every function_call_output for the call→outcome map so a
        # question renders resolved and a tool entry knows its response, error
        # flag and end time, wherever the output landed. The SAME
        # `tool_outcomes` seam the Claude adapter uses — an unresolved call is
        # what "still running" means in both files.
        outcomes = tool_outcomes(messages)
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
                for entry in self._assistant_entries(message, outcomes):
                    _add(entry, message.timestamp)
            elif message.role == "compaction" and message.compaction is not None:
                # An entry inside the turn it happened in, never a turn of its
                # own — the same placement the Claude adapter gives it.
                _add(
                    DigestEntry(
                        "compaction",
                        message.compaction.headline(),
                        compaction=message.compaction,
                    ),
                    message.timestamp,
                )
            # role == "tool": a result carrier — feeds `answered`, no entry.
        if current[0] is not None or entries:
            _flush()

        if last is not None:
            return tuple(turns[-last:]) if last > 0 else ()
        return tuple(turns)

    @staticmethod
    def _assistant_entries(
        message: AgentMessage, outcomes: Mapping[str, ToolOutcome]
    ) -> list[DigestEntry]:
        """One assistant message's content projected to turn entries: prose text,
        readable reasoning (a ``thinking`` block, rendered as an assistant line —
        never the opaque ``encrypted_content``), structured ``question`` rows for
        a question-shaped tool call, structured ``file_edit`` rows for a file-edit
        call (Codex's ``apply_patch`` ``custom_tool_call`` or an MCP-bridged edit
        ``function_call``), a structured ``todo`` row for an ``update_plan`` call,
        else one plain ``tool`` entry. A question is stamped with its matching
        output text (``outcomes``) at the group level.

        Every entry a tool call produced also carries that call's
        :class:`ToolCall` — the identical rule the Claude adapter applies, off
        the identical shared map, which is why "is this tool still running"
        needed no Codex-specific branch: an open ``function_call`` with no
        ``function_call_output`` for its ``call_id`` is exactly an unresolved
        entry in ``outcomes``."""
        entries: list[DigestEntry] = []
        for block in message.content:
            if block.type in ("text", "thinking"):
                if block.text and block.text.strip():
                    entries.append(DigestEntry("assistant", block.text))
            elif block.type == "tool_use" and block.tool_name:
                name = block.tool_name
                cid = block.tool_use_id
                call = ToolCall.from_block(block, outcomes, called_at=message.timestamp)
                questions = AgentQuestion.from_tool_call(name, block.tool_input, cid or "")
                if questions:
                    resolved = (
                        (q.resolved(outcomes[cid].text) for q in questions)
                        if cid is not None and cid in outcomes
                        else questions
                    )
                    entries.extend(
                        DigestEntry("question", q.prompt, question=q, tool=call) for q in resolved
                    )
                elif FileEdit.recognizes(name) and (
                    edits := FileEdit.from_tool_call(name, block.tool_input)
                ):
                    entries.extend(
                        DigestEntry("file_edit", f"{name} {e.path}".strip(), file_edit=e, tool=call)
                        for e in edits
                    )
                elif TodoList.recognizes(name) and (
                    todos := TodoList.from_tool_call(name, block.tool_input)
                ):
                    entries.extend(
                        DigestEntry("todo", lst.summary, todo=lst, tool=call) for lst in todos
                    )
                else:
                    entries.append(DigestEntry("tool", name, tool=call))
        return entries

    def first_human_text(self) -> str | None:
        return self._first_human_text()

    def current_task_text(self) -> str | None:
        """The session's task text, UNCAPPED — the ONE selection
        :meth:`activity` caps onto ``AgentActivity.current_task``.

        Codex records no ``last-prompt`` analogue, so the rule is just the
        first real human turn's text (the injected preamble is already excluded
        by ``is_human_turn``). Both readers share this method so the capped and
        uncapped answers cannot drift apart. Whitespace-only text is ``None``.
        """
        raw = self._first_human_raw()
        return raw if raw and raw.strip() else None

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
        raw = self._first_human_raw()
        return _truncate(raw, _TASK_TEXT_CAP) if raw is not None else None

    def _first_human_raw(self) -> str | None:
        for line in self._lines:
            if line.is_human_turn:
                return line.message_text()
        return None


_MODELS_PROBE_TIMEOUT = 5.0
"""Seconds to wait on ``codex debug models``. The bundled catalog is instant
and offline; the bound only guards a wedged binary — best-effort never hangs."""


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
    # The queue is a SQLite table under the config root (see `_CodexQueue`).
    reports_queue = True
    # `function_call_output` has no error key in any version, so a Codex tool
    # result can never be anything but `is_error=False` — see the census in
    # `_Line.blocks`. Re-measured 2026-08-11 over this host's whole store: the
    # payload keys across 31,373 real records are exactly
    # {type, call_id, output} (+ an id/metadata variant), zero error-shaped.
    reports_tool_errors = False

    def launch_decoration(self, session_id: str, *, resume: bool = False) -> list[str]:
        """Empty for a fresh run — Codex mints its own thread id, with no flag to
        set it, so ``_mint_agent_session_id`` returns ``None`` and Grove tracks it
        purely through fs discovery.

        ``resume=True`` returns the ``resume <uuid>`` SUBCOMMAND. Codex's
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
        """``--model <id>`` — Codex CLI's per-launch model selector.

        Independent of correlation: Codex mints no session id, but it still
        honors ``--model`` at launch, so this rides the command even though
        ``launch_decoration`` is empty.
        """
        return ["--model", model]

    def offline_decoration(self) -> list[str]:
        """Pin the sandbox to ``workspace-write`` with networking off:
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
        """Empty — and deliberately so, not a gap waiting for a TOML writer.

        Codex has no env switch at all: OTel is configured through
        ``config.toml [otel]`` (the ``codex-otel`` crate). The tempting fix is
        for Grove to write that TOML, and it is wrong three ways. **It would not
        work**: Codex splits every event across two targets, sending prompts,
        tool arguments and outputs to the OTLP *logs* stream and only lengths and
        counts to traces — and LangFuse ingests traces, with no logs endpoint —
        so a fully-enabled Codex trace is structurally, permanently blank. Grove
        replays the rollout transcript instead, which is why this adapter's
        ``read_messages`` is the Codex content path. **It would reach past this
        session**: ``config.toml`` is the user's own global Codex config, so a
        launch-time write changes every Codex run on the host, including ones
        Grove never started — breaking "a session run without Grove is
        unaffected". Adapters are read-only over the filesystem by contract;
        ``onboarding.py`` is the one module that writes another tool's config,
        through that tool's own CLI, and only when a human asks. **And it would
        ship an egress default nobody chose**: ``metrics_exporter`` defaults to
        ``statsig``, so turning the block on hands OpenAI product metrics unless
        the same write also pins it off.

        A per-launch ``-c otel.*`` override (the form ``offline_decoration``
        uses) would dodge the global-config objection but not the first one, so
        it buys blank traces and the metrics default. The residual is cosmetic:
        ``passthrough_kinds`` still exports the OTLP endpoint/headers into a
        Codex pane, which the Rust exporter never reads.
        """
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
        raw = _probe_codex_models(AgentVersionProbe.binary_of(command))
        return _parse_codex_models(raw) if raw is not None else ()

    def tool_version(self, command: str) -> str | None:
        """``codex --version``, recorded verbatim (on-host: ``codex-cli
        0.147.0`` — the distribution name is part of the vendor's own answer and
        is kept rather than parsed off)."""
        return AgentVersionProbe.version(command, "--version")

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
        adoption pre-filter. Birth rides out of the same bounded
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

    def discover_all(self) -> tuple[SessionRef, ...]:
        """Every rollout in the store, host-wide. Best-effort: ``()`` on any error."""
        try:
            return _CodexHome.discover_all()
        except OSError as exc:
            logger.debug("discover_all() failed: {}", exc)
            return ()

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
        """The session's agentic-loop spine — the message list
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
        """The session's terminal outcome — a projection of
        :meth:`read_messages`, never a second parser."""
        return final_result_from_messages(self.read_messages(cwd, session_id))

    def latest_todo(self, cwd: Path, session_id: str) -> TodoList | None:
        """The session's current todo/checklist state — a projection of
        :meth:`read_messages`, never a second parser. Codex has no Task-system
        analog (``TASK_TOOL_NAMES`` never matches an ``update_plan`` call), so
        this is always the plain whole-list-per-call read."""
        return latest_todo_from_messages(self.read_messages(cwd, session_id))

    def pending_queue(self, cwd: Path, session_id: str) -> tuple[QueuedMessage, ...]:
        """What Codex is still holding for this session, in its own queue order.

        Read from ``$CODEX_HOME/queue_1.sqlite`` (see :class:`_CodexQueue`), not
        the rollout — Codex records nothing queue-shaped there, verified by
        enumerating every record type in the whole store. ``cwd`` is unused: the
        queue is keyed by thread id, which IS the session id, and the store is
        config-root-global. Deliberately NOT memoized on the transcript's stat
        signature the way the rollout projections are: this reads a different
        file, and a queue that changes without the rollout growing is the normal
        case (a message sits queued precisely because nothing is being written).
        """
        del cwd
        return _CodexQueue.pending(session_id)

    def latest_task(self, cwd: Path, session_id: str) -> str | None:
        """The session's task text, uncapped — the same
        :meth:`_RolloutParser.current_task_text` selection
        :meth:`parse_activity` caps onto ``AgentActivity.current_task``.

        Rides the same incremental line read + stat-signature memo every other
        projection here does.
        """
        paths = self.locate_transcripts(cwd, session_id)
        return _MEMO.get_or_compute(
            ("task", str(cwd), session_id),
            paths,
            lambda: _RolloutParser(self._read(paths)).current_task_text(),
        )

    def session_controls(self, cwd: Path, session_id: str) -> SessionControls:
        """Enumerate the session's input controls — the codex analog of Claude's
        TIER 1 scan: user-global custom prompts + ``config.toml`` MCP
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
        (the daemon-CPU fix): a poll tick pays ``json.loads`` only
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

    def add(self, raw: dict[str, Any], source: str) -> None:  # noqa: ARG002
        # ``source`` is ignored deliberately: this fold appends and never merges
        # by id, so it has no cross-file collision to disambiguate.
        self._lines.append(_RolloutLine(raw=raw, index=self._index))
        self._index += 1

    def records(self) -> list[_RolloutLine]:
        return self._lines


_TRANSCRIPTS = TranscriptCache(_LineFolder)
_MEMO = ResultMemo()
