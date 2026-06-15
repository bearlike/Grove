"""Mewbo session introspection — the first remote-backed :class:`AgentAdapter` (#36).

Mewbo sessions live behind a REST API, not in local transcript files — the
reason the adapter seam is keyed by ``(cwd, session_id)`` instead of paths.
All HTTP I/O funnels through :class:`grove.core.mewbo.MewboClient`; this
module is pure logic over the fetched payloads, decomposed like the Claude
adapter:

- :class:`_Event` — *what one event row is* (``{ts, type, payload}``), with
  every classification rule narrowed at the boundary.
- :class:`_EventLog` — *the aggregate*: one fetched ``/events`` payload turned
  into ``AgentActivity`` / turns / digest.
- :class:`MewboAdapter` — the thin seam wiring client → parser, best-effort
  by contract (a failed or malformed fetch degrades fields, never raises).

Mapping facts (verified against the Mewbo API guide + console wire types,
2026-06-11):

- ``GET /events`` carries the AUTHORITATIVE ``status`` / ``done_reason`` /
  ``title`` top-level — read them, never reconstruct from the timeline tail.
- Event payloads: ``action_plan`` steps are ``{title, description}``; tool
  events use ``tool_id`` / ``operation`` / ``tool_input``.
- Tokens: ``GET /agents`` ``total_input_tokens`` is PEAK input semantics
  (context pressure); the cumulative billed sum is the separate
  ``total_input_tokens_billed`` (on ``/usage``) — never mix the two. The
  fallback when ``/agents`` is unavailable mirrors that: max of
  ``llm_call_end`` input counts, sum of output counts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from loguru import logger

from grove.core.agents.model import (
    AgentActivity,
    AgentActivityState,
    DigestEntry,
    OrderedDigest,
    SessionSummary,
    SessionTurn,
)
from grove.core.config import load_config
from grove.core.errors import MewboError
from grove.core.mewbo import MewboClient

_DIGEST_MAX_ENTRIES = 60
_DIGEST_TEXT_CAP = 200
_TASK_TEXT_CAP = 500
# Turn entries carry the fuller, ``sessions show``-grade text (the digest
# re-truncates each to ``_DIGEST_TEXT_CAP``) — same split the Claude adapter
# draws between ``turn_entries`` (full) and its digest.
_TURN_TEXT_CAP = 800

# Authoritative-status → activity-state table. Mewbo's ``summarize_session``
# vocabulary, mapped onto Grove's axis: running → WORKING; any settled run →
# WAITING (turn over, the human's move — same convention as Claude's
# ``end_turn``); explicit needs-the-user statuses → BLOCKED; failures → ERROR.
# Anything unrecognized stays UNKNOWN — the adapter normalizes shape, it never
# guesses semantics.
_STATUS_STATE: dict[str, AgentActivityState] = {
    "running": AgentActivityState.WORKING,
    "completed": AgentActivityState.WAITING,
    "done": AgentActivityState.WAITING,
    "finished": AgentActivityState.WAITING,
    "interrupted": AgentActivityState.WAITING,
    "idle": AgentActivityState.WAITING,
    "waiting": AgentActivityState.WAITING,
    "waiting_user": AgentActivityState.BLOCKED,
    "waiting_for_user": AgentActivityState.BLOCKED,
    "needs_input": AgentActivityState.BLOCKED,
    "blocked": AgentActivityState.BLOCKED,
    "failed": AgentActivityState.ERROR,
    "error": AgentActivityState.ERROR,
}


def _map_status(status: Any, done_reason: Any) -> AgentActivityState:
    """Authoritative envelope → activity state; ``done_reason`` refines terminals.

    A session can summarize as settled while the run actually died — the
    reason field is the truth for that distinction, so an error-ish reason
    promotes any non-ERROR mapping to ERROR.
    """
    if not isinstance(status, str) or not status.strip():
        return AgentActivityState.UNKNOWN
    state = _STATUS_STATE.get(status.strip().lower(), AgentActivityState.UNKNOWN)
    if state is not AgentActivityState.ERROR and isinstance(done_reason, str):
        reason = done_reason.strip().lower()
        if "error" in reason or "fail" in reason:
            return AgentActivityState.ERROR
    return state


def _parse_timestamp(value: Any) -> datetime | None:
    """ISO-8601 string → aware datetime; ``None`` on anything odd (best-effort)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _truncate(text: str, cap: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


@dataclass(slots=True, frozen=True)
class _Event:
    """One ``{ts, type, payload}`` event row, every accessor narrowed at the edge.

    ``raw`` stays ``Any``-typed on purpose — heterogeneous external JSON,
    the documented escape hatch — and every property tolerates a missing or
    mistyped field by returning its empty value.
    """

    raw: dict[str, Any]

    @property
    def type(self) -> str:
        value = self.raw.get("type")
        return value if isinstance(value, str) else ""

    @property
    def ts(self) -> datetime | None:
        return _parse_timestamp(self.raw.get("ts"))

    @property
    def payload(self) -> dict[str, Any]:
        value = self.raw.get("payload")
        return value if isinstance(value, dict) else {}

    # ── classification ──────────────────────────────────────────────────────

    @property
    def is_user(self) -> bool:
        """A human turn boundary — Mewbo's ``user`` events ARE real prompts
        (unlike Claude transcripts, where ``user`` lines are mostly tool results)."""
        return self.type == "user"

    @property
    def is_assistant_reply(self) -> bool:
        """An assistant-authored message: mid-run steering replies and the
        terminal completion both count toward ``replies_per_turn``."""
        return self.type in {"agent_message", "completion"}

    @property
    def tool_id(self) -> str | None:
        value = self.payload.get("tool_id")
        return value if isinstance(value, str) and value else None

    @property
    def is_tool_call(self) -> bool:
        """Call-side tool event only — result events carry ``tool_id`` too,
        and counting both would double every step."""
        return self.tool_id is not None and "result" not in self.payload

    # ── content ─────────────────────────────────────────────────────────────

    def text(self) -> str:
        """Human-readable body, tolerant of the key the event kind uses."""
        for key in ("text", "content", "answer", "query"):
            value = self.payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return ""

    def tool_label(self) -> str:
        operation = self.payload.get("operation")
        if isinstance(operation, str) and operation and self.tool_id:
            return f"{self.tool_id}: {operation}"
        return self.tool_id or ""

    def tool_input_text(self) -> str:
        """The call's ``tool_input`` as a flat one-liner, or empty when absent.

        ``tool_input`` arrives as a dict (``{"path": …, "glob": …}``) on most
        tools but as a bare command string on shell-style ones (verified on the
        live ``/events`` step payloads), so both shapes flatten to ``k=v``
        pairs / the raw string — the renderer shows *what* the call asked for,
        not just its label."""
        value = self.payload.get("tool_input")
        if isinstance(value, dict):
            return " ".join(f"{key}={value[key]!r}" for key in value if value[key] is not None)
        if isinstance(value, str):
            return value.strip()
        return ""

    @property
    def is_tool_result(self) -> bool:
        """Result-side tool event — rendered as its own entry but never counted
        (the call side already counted the step; see ``is_tool_call``)."""
        return self.tool_id is not None and "result" in self.payload

    def tool_result_text(self) -> str:
        """A result event's output, error preferred — the error is the part a
        reader most needs and a successful result may also carry it as null."""
        error = self.payload.get("error")
        if isinstance(error, str) and error.strip():
            return error
        result = self.payload.get("result")
        if isinstance(result, str) and result.strip():
            return result
        return ""

    def plan_steps(self) -> list[tuple[str, str]]:
        """``action_plan`` ``(title, description)`` pairs in order.

        Steps are ``{title, description}``; description is best-effort empty so a
        title-only step still renders."""
        if self.type != "action_plan":
            return []
        steps = self.payload.get("steps")
        if not isinstance(steps, list):
            return []
        pairs: list[tuple[str, str]] = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            title = step.get("title")
            if not isinstance(title, str) or not title:
                continue
            description = step.get("description")
            pairs.append((title, description if isinstance(description, str) else ""))
        return pairs

    def plan_step_titles(self) -> list[str]:
        """``action_plan`` step titles in order (the metric/current-task view)."""
        return [title for title, _ in self.plan_steps()]

    def llm_usage(self) -> tuple[int, int]:
        """``(input, output)`` token counts from an ``llm_call_end`` rollup."""
        if self.type != "llm_call_end":
            return (0, 0)

        def _int(key: str) -> int:
            value = self.payload.get(key)
            return value if isinstance(value, int) and value >= 0 else 0

        return (_int("input_tokens"), _int("output_tokens"))

    @property
    def model(self) -> str | None:
        if self.type != "llm_call_end":
            return None
        value = self.payload.get("model")
        return value if isinstance(value, str) and value else None

    @property
    def error_text(self) -> str | None:
        value = self.payload.get("error")
        return value if isinstance(value, str) and value else None


class _EventLog:
    """One fetched ``/events`` payload aggregated into the normalized model.

    Pure — constructed from the already-fetched dict so it stays unit-testable
    without the wire, mirroring ``_TranscriptParser`` in the Claude adapter.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        raw = payload.get("events")
        self._events = (
            [_Event(item) for item in raw if isinstance(item, dict)]
            if isinstance(raw, list)
            else []
        )

    def state(self) -> AgentActivityState:
        return _map_status(self._payload.get("status"), self._payload.get("done_reason"))

    def title(self) -> str | None:
        value = self._payload.get("title")
        return value if isinstance(value, str) and value else None

    def activity(self, agents_rollup: dict[str, Any]) -> AgentActivity:
        """The full metric set; ``agents_rollup`` is ``GET /agents`` (or ``{}``).

        Tokens prefer the rollup's ``total_input_tokens`` / ``total_output_tokens``
        (peak-input semantics, see module docstring); without it they fall back
        to the same semantics computed from ``llm_call_end`` events — peak for
        input, sum for output. ``tool_calls`` prefers the rollup's
        ``total_steps`` over counting call-side tool events.
        """
        buckets: list[int] = []
        current_task: str | None = None
        first_user_text: str | None = None
        last_event_at: datetime | None = None
        model: str | None = None
        error_detail: str | None = None
        peak_in = 0
        sum_out = 0
        tool_events = 0

        for event in self._events:
            ts = event.ts
            if ts is not None and (last_event_at is None or ts > last_event_at):
                last_event_at = ts

            if event.is_user:
                buckets.append(0)
                if first_user_text is None:
                    first_user_text = _truncate(event.text(), _TASK_TEXT_CAP)
            elif event.is_assistant_reply:
                if buckets:
                    buckets[-1] += 1
                error_detail = event.error_text or error_detail

            titles = event.plan_step_titles()
            if titles:
                current_task = _truncate(titles[-1], _TASK_TEXT_CAP)
            elif event.is_tool_call:
                tool_events += 1
                current_task = _truncate(event.tool_label(), _TASK_TEXT_CAP)

            usage_in, usage_out = event.llm_usage()
            peak_in = max(peak_in, usage_in)
            sum_out += usage_out
            model = event.model or model

        tokens_in = _rollup_int(agents_rollup, "total_input_tokens", peak_in)
        tokens_out = _rollup_int(agents_rollup, "total_output_tokens", sum_out)
        tool_calls = _rollup_int(agents_rollup, "total_steps", tool_events)

        state = self.state()
        done_reason = self._payload.get("done_reason")
        if state is AgentActivityState.ERROR and error_detail is None:
            error_detail = done_reason if isinstance(done_reason, str) else None

        return AgentActivity(
            state=state,
            title=self.title(),
            current_task=current_task or first_user_text,
            human_turns=len(buckets),
            assistant_replies=sum(buckets),
            replies_per_turn=tuple(buckets),
            tool_calls=tool_calls,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            last_event_at=last_event_at,
            error_detail=error_detail,
        )

    def turns(self, *, last: int | None = None) -> tuple[SessionTurn, ...]:
        """The conversation as :class:`SessionTurn` rows, oldest first.

        Same bucketing convention as the Claude adapter: entries preceding any
        ``user`` event collect under a leading turn with empty ``user_text``
        rather than being dropped.
        """
        turns: list[SessionTurn] = []
        entries: list[DigestEntry] = []

        def _flush(user_text: str, started_at: datetime | None) -> None:
            turns.append(
                SessionTurn(user_text=user_text, started_at=started_at, entries=tuple(entries))
            )
            entries.clear()

        current: tuple[str, datetime | None] | None = None
        for event in self._events:
            if event.is_user:
                if current is not None or entries:
                    _flush(*(current or ("", None)))
                current = (event.text(), event.ts)
                continue
            event_entries = self._turn_entries(event)
            if not event_entries:
                continue
            if current is None and not entries and event.ts is not None:
                current = ("", event.ts)
            entries.extend(event_entries)
        if current is not None or entries:
            _flush(*(current or ("", None)))

        if last is not None:
            return tuple(turns[-last:]) if last > 0 else ()
        return tuple(turns)

    def digest(self) -> OrderedDigest:
        """Ordered ``user / assistant / tool`` skeleton, truncated for the
        future external-LLM interpreter (#20)."""
        entries: list[DigestEntry] = []
        for event in self._events:
            if event.is_user:
                text = _truncate(event.text(), _DIGEST_TEXT_CAP)
                if text:
                    entries.append(DigestEntry("user", text))
                continue
            # The digest is the same skeleton as the turns, each line clamped to
            # the digest cap — one rule, two truncation budgets.
            for entry in self._turn_entries(event):
                entries.append(DigestEntry(entry.role, _truncate(entry.text, _DIGEST_TEXT_CAP)))
        return OrderedDigest(tuple(entries[-_DIGEST_MAX_ENTRIES:]))

    @staticmethod
    def _turn_entries(event: _Event) -> list[DigestEntry]:
        """The rule mapping one non-user event to its rendered entries.

        Returns a list because Mewbo's richer payloads expand past one line: a
        tool call carries its input summary, and a result event becomes its OWN
        entry (the call+result pair the rendered transcript shows). The result
        side renders but is never counted — counting stays call-side only
        (``is_tool_call``), so metrics don't move while the render gets richer.
        """
        role: Literal["assistant", "tool", "status"]
        if event.is_assistant_reply:
            text = event.text()
            role, body = "assistant", _truncate(text, _TURN_TEXT_CAP) if text.strip() else ""
        elif event.is_tool_call:
            label = event.tool_label()
            summary = event.tool_input_text()
            role = "tool"
            body = f"{label} {_truncate(summary, _TURN_TEXT_CAP)}" if label and summary else label
        elif event.is_tool_result:
            role, body = "tool", _truncate(event.tool_result_text(), _TURN_TEXT_CAP)
        elif steps := event.plan_steps():
            # Title — description per step, so the plan entry shows the intent
            # behind each step, not just an arrow-joined list of titles.
            role = "status"
            body = "plan: " + "; ".join(
                f"{title}: {description}" if description else title for title, description in steps
            )
        else:
            return []
        return [DigestEntry(role, body)] if body else []


def _rollup_int(rollup: dict[str, Any], key: str, fallback: int) -> int:
    value = rollup.get(key)
    return value if isinstance(value, int) and value >= 0 else fallback


class MewboAdapter:
    """Introspect Mewbo sessions over REST (the first remote :class:`AgentAdapter`).

    Pass a :class:`MewboClient` for tests (DI); production constructs none and
    the first introspection call lazily builds one. Every read is best-effort
    like ``peek()``: a :class:`MewboError` degrades to the empty value, never
    breaks the render loop.
    """

    kind = "mewbo"
    remote = True

    def __init__(self, client: MewboClient | None = None) -> None:
        self._client = client

    def _client_or_none(self) -> MewboClient | None:
        # Adapters are shared singletons in the registry, but this one needs
        # connection config + an HTTP pool. Build it lazily from the ONE global
        # config (load_config(repo_root=None) — the same one-global-config rule
        # ActivityService follows, see core/CLAUDE.md) and cache it, so merely
        # importing the registry never reads config files or opens sockets.
        if self._client is None:
            try:
                self._client = MewboClient(load_config(None).mewbo)
            except Exception as exc:  # best-effort: broken config degrades, never raises
                logger.debug("mewbo client unavailable: {}", exc)
                return None
        return self._client

    def launch_decoration(self, session_id: str) -> list[str]:
        # No CLI to decorate: the tmux agent window runs whatever command the
        # AgentSpec names (typically a shell); the session itself lives
        # server-side and its id is SERVER-minted at create (manager fork).
        del session_id
        return []

    def model_decoration(self, model: str) -> list[str]:
        # No launch-time CLI: the model is a server-side concern selected when
        # the remote session is created, so there is no flag to decorate here.
        del model
        return []

    def locate_transcripts(self, cwd: Path, session_id: str) -> list[Path]:
        # Remote sessions have no local backing file — empty by design (the
        # deliberately filesystem-shaped method; see the protocol docstring).
        del cwd, session_id
        return []

    def discover_sessions(self, cwd: Path, *, exclude_id: str | None = None) -> list[str]:
        # Out-of-band discovery is meaningless for a remote backend: a Mewbo
        # session can't be "hand-started in this cwd" invisibly to Grove —
        # anchoring to a worktree happens only through the API create Grove
        # itself issues, so there is nothing on this host to find.
        del cwd, exclude_id
        return []

    def list_sessions(self, cwd: Path) -> list[SessionSummary]:
        """Remote sessions whose persisted context ``cwd`` matches this worktree.

        Honest filtering: rows that expose no context cwd cannot be claimed
        for this directory and are skipped — workspace association by minted
        id happens one level up (``SessionExplorer``). Built from the listing
        row alone (no per-session ``/events`` fetch), so the activity carries
        state but no metrics; ``transcript_path`` stays ``None`` (remote).
        """
        client = self._client_or_none()
        if client is None:
            return []
        try:
            rows = client.list_sessions()
        except MewboError as exc:
            logger.debug("mewbo list_sessions({}) failed: {}", cwd, exc)
            return []
        target = str(cwd)
        summaries: list[SessionSummary] = []
        for row in rows:
            context = row.get("context")
            context = context if isinstance(context, dict) else {}
            if context.get("cwd") != target:
                continue
            session_id = row.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                continue
            title = row.get("title") if isinstance(row.get("title"), str) else None
            branch = context.get("branch") if isinstance(context.get("branch"), str) else None
            summaries.append(
                SessionSummary(
                    session_id=session_id,
                    adapter_kind=self.kind,
                    transcript_path=None,
                    cwd=target,
                    created_at=_parse_timestamp(row.get("created_at")),
                    modified_at=None,
                    size_bytes=0,
                    git_branch=branch,
                    title=title,
                    activity=AgentActivity(
                        state=_map_status(row.get("status"), row.get("done_reason")),
                        title=title,
                    ),
                )
            )
        return summaries

    def read_turns(
        self, cwd: Path, session_id: str, *, last: int | None = None
    ) -> tuple[SessionTurn, ...]:
        log = self._fetch_log(cwd, session_id)
        return log.turns(last=last) if log is not None else ()

    def parse_activity(self, cwd: Path, session_id: str) -> AgentActivity:
        log = self._fetch_log(cwd, session_id)
        if log is None:
            return AgentActivity.empty(AgentActivityState.UNKNOWN)
        client = self._client_or_none()
        rollup: dict[str, Any] = {}
        if client is not None:
            try:
                rollup = client.agents_tree(session_id)
            except MewboError as exc:  # rollup is enrichment; events alone still answer
                logger.debug("mewbo agents_tree({}) failed: {}", session_id, exc)
        return log.activity(rollup)

    def transcript_digest(self, cwd: Path, session_id: str) -> OrderedDigest:
        log = self._fetch_log(cwd, session_id)
        return log.digest() if log is not None else OrderedDigest()

    # ── internal ────────────────────────────────────────────────────────────

    def _fetch_log(self, cwd: Path, session_id: str) -> _EventLog | None:
        """One ``/events`` fetch behind every read; ``None`` means degrade.

        ``cwd`` is unused on the wire — Mewbo session ids are globally unique
        server-side — but stays in the signature per the seam contract.
        """
        del cwd
        client = self._client_or_none()
        if client is None:
            return None
        try:
            return _EventLog(client.events(session_id))
        except MewboError as exc:
            logger.debug("mewbo events({}) failed: {}", session_id, exc)
            return None
