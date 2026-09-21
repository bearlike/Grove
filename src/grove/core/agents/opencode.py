"""OpenCode SQLite session introspection.

OpenCode records every session in one ``opencode.db`` rather than one transcript
per session.  This adapter reads its session/message/part/todo tables into
Grove's provider-neutral agent spine without writing to that live database.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from grove.core.agents.base import AgentVersionProbe
from grove.core.agents.model import (
    AgentActivity,
    AgentActivityState,
    AgentMessage,
    AgentSession,
    CompactionBoundary,
    ContentBlock,
    DigestEntry,
    FinalResult,
    OrderedDigest,
    QueuedMessage,
    SessionControls,
    SessionRef,
    SessionSummary,
    SessionTurn,
    TodoItem,
    TodoList,
    TokenUsage,
    ToolCall,
    ToolOutcome,
    final_result_from_messages,
    tool_outcomes,
)
from grove.core.agents.opencode_http import OpenCodeClient as _OpenCodeClient
from grove.core.agents.transcript_scope import config_dir_override

_DIGEST_MAX_ENTRIES = 60
_DIGEST_TEXT_CAP = 200
_TASK_TEXT_CAP = 500
_MODEL_PROBE_TIMEOUT_SECONDS = 15.0
"""Seconds to wait on the model probe.

Sized for a WRAPPED command, not a bare binary. `opencode models` alone is
near-instant, but a gateway profile runs it through a credential injector that
fetches secrets over the network first — measured 5.03 s on a real host against
the former 2.0 s budget, so the probe timed out and the catalog came back empty
on exactly the profiles that need it. An empty catalog is indistinguishable
from a provider that enumerates nothing, so the picker rendered disabled with
no way to tell the difference.

The bound still only guards a wedged binary: this is a create-form/controls
read, never the ~1 Hz activity tick, and the result is memoized upstream.
"""


def _probe_opencode_models(argv: tuple[str, ...]) -> str | None:
    """The bounded ``opencode models`` output, or ``None`` when unavailable."""
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=_MODEL_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("OpenCode model probe {} failed: {}", argv, exc)
        return None
    if proc.returncode != 0:
        logger.debug("OpenCode model probe {} exited {}", argv, proc.returncode)
        return None
    return proc.stdout


def _parse_opencode_models(raw: str) -> tuple[str, ...]:
    """Exact ``provider/model`` lines emitted by ``opencode models``."""
    return tuple(line.strip() for line in raw.splitlines() if "/" in line.strip())


def _provider_models(payload: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Provider-map fallback only for when the CLI resource is unavailable."""
    providers = payload.get("all") if isinstance(payload, Mapping) else None
    if not isinstance(providers, list):
        return ()
    models: list[str] = []
    for provider in providers:
        if not isinstance(provider, Mapping):
            continue
        provider_id = provider.get("id")
        catalog = provider.get("models")
        if not isinstance(provider_id, str) or not provider_id or not isinstance(catalog, Mapping):
            continue
        models.extend(
            f"{provider_id}/{model_id}" for model_id in catalog if isinstance(model_id, str)
        )
    return tuple(models)


class _OpenCodeHome:
    """Resolve the one OpenCode database path from its data-directory cascade."""

    @staticmethod
    def database_path() -> Path:
        """The database under ``XDG_DATA_HOME`` or OpenCode's Linux default.

        ``OPENCODE_CONFIG_DIR`` controls configuration discovery, not this data
        store; OpenCode's database follows ``XDG_DATA_HOME`` instead.
        """
        override = config_dir_override("XDG_DATA_HOME")
        base = (override if override is not None else os.environ.get("XDG_DATA_HOME", "")).strip()
        data_home = Path(base).expanduser() if base else Path.home() / ".local" / "share"
        return data_home / "opencode" / "opencode.db"


class _OpenCodeStore:
    """Run bounded, read-only queries against OpenCode's shared SQLite store."""

    @classmethod
    @contextlib.contextmanager
    def connect(cls) -> Iterator[sqlite3.Connection]:
        path = _OpenCodeHome.database_path()
        if not path.is_file():
            raise sqlite3.OperationalError("OpenCode database does not exist")
        # `immutable=1` would skip the live server's WAL, hiding fresh messages.
        # `mode=ro` prevents Grove writes while preserving SQLite's WAL view.
        with contextlib.closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
            yield conn

    @classmethod
    def sessions(cls, *, directory: str | None = None) -> list[sqlite3.Row]:
        sql = (
            "SELECT id, parent_id, directory, title, model, time_created, time_updated FROM session"
        )
        args: tuple[str, ...] = ()
        if directory is not None:
            sql += " WHERE directory = ?"
            args = (directory,)
        sql += " ORDER BY time_updated DESC, id"
        try:
            with cls.connect() as conn:
                conn.row_factory = sqlite3.Row
                return list(conn.execute(sql, args).fetchall())
        except sqlite3.Error as exc:
            logger.debug("OpenCode session query failed: {}", exc)
            return []

    @classmethod
    def session(cls, session_id: str) -> sqlite3.Row | None:
        try:
            with cls.connect() as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT id, parent_id, directory, title, model, time_created, time_updated "
                    "FROM session WHERE id = ?",
                    (session_id,),
                ).fetchone()
                return row if isinstance(row, sqlite3.Row) else None
        except sqlite3.Error as exc:
            logger.debug("OpenCode session query for {} failed: {}", session_id, exc)
            return None

    @classmethod
    def messages(cls, session_id: str) -> list[tuple[sqlite3.Row, list[sqlite3.Row]]]:
        try:
            with cls.connect() as conn:
                conn.row_factory = sqlite3.Row
                messages = list(
                    conn.execute(
                        "SELECT id, time_created, data FROM message "
                        "WHERE session_id = ? ORDER BY time_created, id",
                        (session_id,),
                    ).fetchall()
                )
                parts = list(
                    conn.execute(
                        "SELECT message_id, time_created, data FROM part "
                        "WHERE session_id = ? ORDER BY time_created, id",
                        (session_id,),
                    ).fetchall()
                )
        except sqlite3.Error as exc:
            logger.debug("OpenCode message query for {} failed: {}", session_id, exc)
            return []
        by_message: dict[str, list[sqlite3.Row]] = {}
        for part in parts:
            by_message.setdefault(part["message_id"], []).append(part)
        return [(message, by_message.get(message["id"], [])) for message in messages]

    @classmethod
    def todos(cls, session_id: str) -> list[sqlite3.Row]:
        try:
            with cls.connect() as conn:
                conn.row_factory = sqlite3.Row
                return list(
                    conn.execute(
                        "SELECT content, status FROM todo WHERE session_id = ? ORDER BY position",
                        (session_id,),
                    ).fetchall()
                )
        except sqlite3.Error as exc:
            logger.debug("OpenCode todo query for {} failed: {}", session_id, exc)
            return []

    @classmethod
    def descendants(cls, session_id: str) -> dict[str, str]:
        """Recursive ``parent_id`` lineage (``child_id → parent_id``), never a
        same-directory heuristic — the offline mirror of the HTTP client's
        own ``descendants`` shape, so a caller never branches on which source
        answered."""
        try:
            with cls.connect() as conn:
                rows = conn.execute("SELECT id, parent_id FROM session").fetchall()
        except sqlite3.Error as exc:
            logger.debug("OpenCode child-session query for {} failed: {}", session_id, exc)
            return {}
        children: dict[str, list[str]] = {}
        for child_id, parent_id in rows:
            if isinstance(child_id, str) and isinstance(parent_id, str):
                children.setdefault(parent_id, []).append(child_id)
        pending = [session_id]
        seen = {session_id}
        out: dict[str, str] = {}
        while pending:
            parent = pending.pop()
            for child in children.get(parent, []):
                if child in seen:
                    continue
                seen.add(child)
                out[child] = parent
                pending.append(child)
        return out


_Record = Mapping[str, Any] | sqlite3.Row

# One session's ordered records, from either input shape this adapter reads:
# the offline SQLite join (`(message row, [part row, ...])`) and the live
# `GET /session/{id}/message` list (`[{"info": Message, "parts": [Part, ...]}]`).
# Normalizing both to the same tuple shape here is what lets `_OpenCodeParser`
# stay the ONE spine builder regardless of which source answered.


def _normalize_records(
    records: Sequence[_Record] | Sequence[tuple[sqlite3.Row, list[sqlite3.Row]]],
) -> list[tuple[_Record, list[_Record]]]:
    out: list[tuple[_Record, list[_Record]]] = []
    for record in records:
        if isinstance(record, tuple):
            message, parts = record  # sqlite offline shape
            out.append((message, list(parts)))
            continue
        if isinstance(record, Mapping) and "info" in record:
            info: object = record.get("info")
            raw_parts: object = record.get("parts")
            if isinstance(info, Mapping) and isinstance(raw_parts, list):
                record_parts: list[_Record] = [p for p in raw_parts if isinstance(p, Mapping)]
                out.append((info, record_parts))
    return out


class _OpenCodeParser:
    """Normalize one OpenCode session's ordered messages and parts."""

    def __init__(
        self, records: Sequence[_Record] | Sequence[tuple[sqlite3.Row, list[sqlite3.Row]]]
    ) -> None:
        self._records = _normalize_records(records)

    def messages(self) -> tuple[AgentMessage, ...]:
        messages: list[AgentMessage] = []
        for message, parts in self._records:
            data = _record_data(message)
            role = data.get("role")
            timestamp = _timestamp(_object(data.get("time")).get("created"))
            if role == "compaction":
                messages.append(
                    AgentMessage(
                        role="compaction",
                        message_id=_record_id(message),
                        timestamp=timestamp,
                        compaction=_compaction(data, timestamp),
                    )
                )
                continue
            if role not in ("user", "assistant"):
                continue
            content: list[ContentBlock] = []
            results: list[AgentMessage] = []
            boundaries: list[AgentMessage] = []
            for part in parts:
                part_data = _record_data(part)
                if part_data.get("type") == "compaction":
                    boundaries.append(
                        AgentMessage(
                            role="compaction",
                            message_id=_record_id(part),
                            timestamp=timestamp,
                            compaction=_compaction(part_data, timestamp),
                        )
                    )
                    continue
                tool_use, tool_result = self._part_blocks(part_data)
                if tool_use is not None:
                    content.append(tool_use)
                if tool_result is not None:
                    results.append(tool_result)
            # A compaction part has no conversational content of its own. Do not
            # manufacture an empty assistant reply around it.
            if content or not boundaries:
                messages.append(
                    AgentMessage(
                        role=role,
                        content=tuple(content),
                        message_id=_record_id(message),
                        model=_string(data.get("modelID")),
                        usage=_usage(data.get("tokens")) if role == "assistant" else None,
                        timestamp=timestamp,
                    )
                )
            messages.extend(results)
            messages.extend(boundaries)
        return tuple(messages)

    @staticmethod
    def _part_blocks(part: dict[str, Any]) -> tuple[ContentBlock | None, AgentMessage | None]:
        kind = part.get("type")
        if kind == "text":
            return (ContentBlock(type="text", text=_string(part.get("text"))), None)
        if kind == "reasoning":
            return (ContentBlock(type="thinking", text=_string(part.get("text"))), None)
        state = part.get("state")
        name = _string(part.get("tool"))
        call_id = _string(part.get("callID"))
        if kind != "tool" or not isinstance(state, dict) or not name or not call_id:
            return (None, None)
        raw_input = state.get("input")
        tool_input = raw_input if isinstance(raw_input, dict) else None
        tool_use = ContentBlock(
            type="tool_use", tool_name=name, tool_use_id=call_id, tool_input=tool_input
        )
        status = _string(state.get("status"))
        if status not in ("completed", "error"):
            return (tool_use, None) if status in ("pending", "running") else (None, None)
        # OpenCode 1.18.18 live captures only reached a stuck `running` read on
        # tool failure. `error` is declared by its schema but unobserved; map its
        # structural flag defensively without interpreting a tool's output prose.
        time = state.get("time")
        end = time.get("end") if isinstance(time, dict) else None
        return (
            tool_use,
            AgentMessage(
                role="tool",
                content=(
                    ContentBlock(
                        type="tool_result",
                        tool_use_id=call_id,
                        text=_string(state.get("output")),
                        is_error=status == "error",
                    ),
                ),
                timestamp=_timestamp(end),
            ),
        )

    def activity(self) -> AgentActivity:
        messages = self.messages()
        human_turns = 0
        replies: list[int] = []
        tool_calls = 0
        model: str | None = None
        tokens_in = 0
        tokens_out = 0
        last_event_at: datetime | None = None
        current_task: str | None = None
        tail: AgentMessage | None = None
        for message in messages:
            if message.timestamp is not None and (
                last_event_at is None or message.timestamp > last_event_at
            ):
                last_event_at = message.timestamp
            if message.role == "user":
                human_turns += 1
                replies.append(0)
                if current_task is None:
                    current_task = message.text()
                tail = message
                continue
            if message.role != "assistant":
                continue
            if replies:
                replies[-1] += 1
            model = message.model or model
            for block in message.content:
                if block.type == "tool_use":
                    tool_calls += 1
            if message.usage is not None:
                tokens_in += sum(
                    value or 0
                    for value in (
                        message.usage.input,
                        message.usage.cache_creation,
                        message.usage.cache_read,
                    )
                )
                tokens_out += message.usage.output or 0
            tail = message
        outcomes = tool_outcomes(messages)
        state = AgentActivityState.UNKNOWN
        if tail is not None:
            state = (
                AgentActivityState.WAITING
                if tail.role == "assistant"
                else AgentActivityState.WORKING
            )
            if any(
                block.type == "tool_use" and (block.tool_use_id or "") not in outcomes
                for block in tail.content
            ):
                state = AgentActivityState.WORKING
        return AgentActivity(
            state=state,
            title=None,
            current_task=_truncate(current_task, _TASK_TEXT_CAP) if current_task else None,
            human_turns=human_turns,
            assistant_replies=sum(replies),
            replies_per_turn=tuple(replies),
            tool_calls=tool_calls,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            last_event_at=last_event_at,
            started_at=messages[0].timestamp if messages else None,
        )

    def turns(self, *, last: int | None = None) -> tuple[SessionTurn, ...]:
        messages = self.messages()
        outcomes = tool_outcomes(messages)
        turns: list[SessionTurn] = []
        entries: list[DigestEntry] = []
        current: AgentMessage | None = None
        for message in messages:
            if message.role == "user":
                if current is not None or entries:
                    turns.append(
                        SessionTurn(
                            user_text=current.text() if current else "",
                            started_at=current.timestamp if current else None,
                            entries=tuple(entries),
                        )
                    )
                    entries = []
                current = message
            elif message.role == "assistant":
                entries.extend(self._assistant_entries(message, outcomes))
        if current is not None or entries:
            turns.append(
                SessionTurn(
                    user_text=current.text() if current else "",
                    started_at=current.timestamp if current else None,
                    entries=tuple(entries),
                )
            )
        return tuple(turns[-last:] if last and last > 0 else (() if last == 0 else turns))

    @staticmethod
    def _assistant_entries(
        message: AgentMessage, outcomes: Mapping[str, ToolOutcome]
    ) -> list[DigestEntry]:
        entries: list[DigestEntry] = []
        for block in message.content:
            if block.type in ("text", "thinking") and block.text:
                entries.append(DigestEntry("assistant", block.text))
            elif block.type == "tool_use":
                entries.append(
                    DigestEntry(
                        "tool",
                        block.tool_name or "tool",
                        tool=ToolCall.from_block(block, outcomes, called_at=message.timestamp),
                    )
                )
        return entries

    def digest(self) -> OrderedDigest:
        entries: list[DigestEntry] = []
        for message in self.messages():
            if message.role == "user":
                entries.append(DigestEntry("user", _truncate(message.text(), _DIGEST_TEXT_CAP)))
            elif message.role == "assistant":
                for block in message.content:
                    if block.type in ("text", "thinking") and block.text:
                        entries.append(
                            DigestEntry("assistant", _truncate(block.text, _DIGEST_TEXT_CAP))
                        )
                    elif block.type == "tool_use":
                        entries.append(DigestEntry("tool", block.tool_name or "tool"))
        return OrderedDigest(tuple(entries[-_DIGEST_MAX_ENTRIES:]))

    def first_prompt(self) -> str | None:
        return next((message.text() for message in self.messages() if message.role == "user"), None)

    def last_prompt(self) -> str | None:
        return next(
            (message.text() for message in reversed(self.messages()) if message.role == "user"),
            None,
        )


def _record_data(record: Mapping[str, Any] | sqlite3.Row) -> dict[str, Any]:
    """The raw mapping's JSON ``data`` payload, or a live mapping itself."""
    try:
        raw = record["data"]
    except (IndexError, KeyError):
        raw = None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}
    return dict(record) if isinstance(record, Mapping) else {}


def _record_id(record: Mapping[str, Any] | sqlite3.Row) -> str | None:
    try:
        value = record["id"]
    except (IndexError, KeyError):
        value = None
    return _string(value)


def _object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _compaction(data: Mapping[str, Any], at: datetime | None) -> CompactionBoundary:
    """OpenCode's native compaction record/part on the shared empty-role seam.

    ``duration_ms`` and ``model`` come from the record's own fields, unlike
    Claude Code where the model has to be inferred from the surrounding
    conversation: OpenCode stamps the model onto the compaction record itself.
    Both spellings observed in the real database are read — a top-level
    ``modelID`` (the assistant-side record) and a nested ``model.modelID`` (the
    user-side one) — because the two halves of one compaction are written by
    different writers and neither is documented as canonical.

    Duration is the record's own ``time.created`` → ``time.completed`` span. An
    unfinished compaction has no ``completed`` and yields ``None`` rather than a
    zero, which would claim an instantaneous compaction that never happened.
    """
    reason = data.get("reason")
    summary = data.get("summary")
    time = _object(data.get("time"))
    started, completed = _timestamp(time.get("created")), _timestamp(time.get("completed"))
    duration_ms = (
        max(0, int((completed - started).total_seconds() * 1000))
        if started is not None and completed is not None
        else None
    )
    return CompactionBoundary(
        trigger=reason if reason in ("auto", "manual") else None,
        at=at,
        dropped_tokens=None,
        summary=summary if isinstance(summary, str) else "",
        duration_ms=duration_ms,
        model=_string(data.get("modelID")) or _string(_object(data.get("model")).get("modelID")),
    )


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return None


def _usage(value: object) -> TokenUsage | None:
    if not isinstance(value, dict):
        return None

    def count(key: str) -> int | None:
        item = value.get(key)
        return item if isinstance(item, int) and not isinstance(item, bool) else None

    cache = value.get("cache")
    cache = cache if isinstance(cache, dict) else {}
    usage = TokenUsage(
        input=count("input"),
        output=count("output"),
        reasoning=count("reasoning"),
        cache_creation=cache.get("write") if isinstance(cache.get("write"), int) else None,
        cache_read=cache.get("read") if isinstance(cache.get("read"), int) else None,
    )
    return (
        usage
        if any(
            item is not None
            for item in (
                usage.input,
                usage.output,
                usage.reasoning,
                usage.cache_creation,
                usage.cache_read,
            )
        )
        else None
    )


def _truncate(text: str | None, cap: int) -> str:
    compact = " ".join((text or "").split())
    return compact if len(compact) <= cap else compact[: cap - 1].rstrip() + "…"


class OpencodeAdapter:
    """Introspect OpenCode's shared SQLite history as an :class:`AgentAdapter`."""

    kind = "opencode"
    remote = False
    resumable = True
    reports_queue = False
    reports_tool_errors = True

    def launch_decoration(self, session_id: str, *, resume: bool = False) -> list[str]:
        return ["--session", session_id] if resume else []

    def model_decoration(self, model: str) -> list[str]:
        return ["--model", model]

    def offline_decoration(self) -> list[str]:
        return []

    def telemetry_env(self) -> dict[str, str]:
        return {}

    def available_models(self, command: str) -> tuple[str, ...]:
        """Discover exact ``provider/model`` ids from the configured CLI first.

        ``opencode models`` is OpenCode's native catalog and preserves custom
        provider ids verbatim. A local server's provider map is an availability
        fallback only when the CLI cannot be executed, never a competing policy.

        The probe rides the WHOLE configured command (see
        ``AgentVersionProbe.probe_argv``), not just its first token: a gateway
        profile wraps the binary in a credential injector
        (``ssm-cli run … -- opencode``), so probing the bare first token runs
        the wrapper with a subcommand it does not have and yields nothing —
        and even if it resolved, it would enumerate without the credentials
        the gateway needs.
        """
        argv = AgentVersionProbe.probe_argv(command, "models")
        raw = _probe_opencode_models(argv) if argv else None
        if raw is not None:
            return _parse_opencode_models(raw)
        client = _OpenCodeClient.for_reads()
        return _provider_models(client.providers("") if client is not None else None)

    def tool_version(self, command: str) -> str | None:
        return AgentVersionProbe.version(command, "--version")

    def locate_transcripts(self, cwd: Path, session_id: str) -> list[Path]:
        """Return OpenCode's shared DB, because it has no per-session transcript files."""
        del cwd
        return [_OpenCodeHome.database_path()] if _OpenCodeStore.session(session_id) else []

    def discover_sessions(self, cwd: Path, *, exclude_id: str | None = None) -> list[str]:
        return [
            row["id"]
            for row in _OpenCodeStore.sessions(directory=str(cwd))
            if row["id"] != exclude_id
        ]

    def discover_births(
        self, cwd: Path, *, exclude_id: str | None = None
    ) -> list[tuple[str, datetime | None, float]]:
        return [
            (row["id"], _timestamp(row["time_created"]), row["time_updated"] / 1000)
            for row in _OpenCodeStore.sessions(directory=str(cwd))
            if row["id"] != exclude_id
        ]

    def discover_all(self) -> tuple[SessionRef, ...]:
        path = _OpenCodeHome.database_path()
        try:
            size = path.stat().st_size
        except OSError:
            size = None
        return tuple(
            SessionRef(
                session_id=row["id"],
                adapter_kind=self.kind,
                cwd=row["directory"],
                transcript_path=path,
                birth=_timestamp(row["time_created"]),
                mtime=row["time_updated"] / 1000,
                size_bytes=size,
            )
            for row in _OpenCodeStore.sessions()
        )

    def list_sessions(self, cwd: Path) -> list[SessionSummary]:
        return [self._summary(row) for row in _OpenCodeStore.sessions(directory=str(cwd))]

    def session_summary(
        self, cwd: Path, session_id: str, *, full: bool = False
    ) -> SessionSummary | None:
        del cwd
        row = _OpenCodeStore.session(session_id)
        return self._summary(row, full=full) if row is not None else None

    def read_messages(self, cwd: Path, session_id: str) -> tuple[AgentMessage, ...]:
        return self._messages(cwd, session_id)

    def read_turns(
        self, cwd: Path, session_id: str, *, last: int | None = None
    ) -> tuple[SessionTurn, ...]:
        return self._parser(cwd, session_id).turns(last=last)

    def parse_activity(self, cwd: Path, session_id: str) -> AgentActivity:
        return self._parser(cwd, session_id).activity()

    def transcript_digest(self, cwd: Path, session_id: str) -> OrderedDigest:
        return self._parser(cwd, session_id).digest()

    def session_controls(self, cwd: Path, session_id: str) -> SessionControls:
        del cwd, session_id
        return SessionControls.empty()

    def final_result(self, cwd: Path, session_id: str) -> FinalResult | None:
        return final_result_from_messages(self.read_messages(cwd, session_id))

    def latest_todo(self, cwd: Path, session_id: str) -> TodoList | None:
        del cwd
        items = tuple(
            TodoItem(
                content=row["content"],
                status=row["status"]
                if row["status"] in ("pending", "in_progress", "completed")
                else "pending",
            )
            for row in _OpenCodeStore.todos(session_id)
        )
        return TodoList(items) if items else None

    def pending_queue(self, cwd: Path, session_id: str) -> tuple[QueuedMessage, ...]:
        del cwd, session_id
        return ()

    def latest_task(self, cwd: Path, session_id: str) -> str | None:
        return self._parser(cwd, session_id).first_prompt()

    def fleet_activity(
        self, cwd: Path, session_id: str
    ) -> list[tuple[AgentSession, AgentActivity]]:
        """Every descendant published by OpenCode's explicit parent edge.

        The HTTP children API is authoritative while available. Its recursive
        walk never scans a cwd, so a contemporaneous top-level session cannot be
        adopted as a child merely because it shares the project directory.
        """
        descendants = self._descendants(cwd, session_id)
        out: list[tuple[AgentSession, AgentActivity]] = []
        for child_id, parent_id in descendants.items():
            # `full` because this method PUBLISHES each child's activity; the
            # metadata-only default would make every child unusable here.
            summary = self.session_summary(cwd, child_id, full=True)
            # `SessionSummary.activity` is nullable — a summary can resolve while
            # its parse products do not — and this method's contract is a
            # non-null activity per child. Skip rather than publish a `None`
            # the caller would have to re-check: a child Grove cannot describe
            # is honestly absent from the fleet, not a row with no state.
            if summary is None or summary.activity is None:
                continue
            out.append(
                (
                    AgentSession(
                        session_id=child_id,
                        transcript_path=summary.transcript_path,
                        adapter_kind=self.kind,
                        provenance="fs_discovered",
                        parent_session_id=parent_id,
                    ),
                    summary.activity,
                )
            )
        return out

    def subagent_turns(
        self, cwd: Path, session_id: str, thread_id: str, *, last: int | None = None
    ) -> tuple[SessionTurn, ...]:
        """One real descendant session projected through this adapter's turn builder."""
        if thread_id not in self._descendants(cwd, session_id):
            return ()
        return self.read_turns(cwd, thread_id, last=last)

    @staticmethod
    def _descendants(cwd: Path, session_id: str) -> dict[str, str]:
        """The parent edge, from a configured server when one is named."""
        client = _OpenCodeClient.for_reads()
        live = client.descendants(str(cwd), session_id) if client is not None else None
        return live if live is not None else _OpenCodeStore.descendants(session_id)

    def _messages(self, cwd: Path, session_id: str) -> tuple[AgentMessage, ...]:
        return self._parser(cwd, session_id).messages()

    def _parser(self, cwd: Path, session_id: str) -> _OpenCodeParser:
        """One read of a session's messages, live-first only when a server is named.

        With no configured address this is the database read it has always been:
        dialling a guessed port costs ~45 ms per call on the activity tick and
        could only ever reach a server Grove does not own.
        """
        client = _OpenCodeClient.for_reads()
        live = client.messages(str(cwd), session_id) if client is not None else None
        return _OpenCodeParser(live if live is not None else _OpenCodeStore.messages(session_id))

    def _summary(self, row: sqlite3.Row, *, full: bool = True) -> SessionSummary:
        """One session row, with its parse products only when ``full``.

        The row itself is metadata the session table already holds; the three
        parse products below each need every message of the session read and
        normalized. Honouring ``full`` is what keeps the identity-keyed resolve
        inside the protocol's metadata-only cost guarantee — a listing that
        resolves a minted id on every scan must not pay a whole-session parse
        per row to do it. ``None`` here means NOT PARSED AT THIS SCOPE, which
        the wire already contracts, never "this session has no activity".
        """
        parser = _OpenCodeParser(_OpenCodeStore.messages(row["id"])) if full else None
        path = _OpenCodeHome.database_path()
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        return SessionSummary(
            session_id=row["id"],
            adapter_kind=self.kind,
            transcript_path=path,
            cwd=row["directory"],
            created_at=_timestamp(row["time_created"]),
            modified_at=_timestamp(row["time_updated"]),
            size_bytes=size,
            title=_string(row["title"]),
            first_prompt=parser.first_prompt() if parser else None,
            last_prompt=parser.last_prompt() if parser else None,
            activity=parser.activity() if parser else None,
        )
