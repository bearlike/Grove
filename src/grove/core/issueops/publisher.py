"""The live sticky issue-comment publisher (#197).

``TicketStatusPublisher`` mirrors a workspace's progress onto its ticket as ONE
sticky comment, rebuilt from live state on every update. It is the activity bus's
third subscriber, alongside ``_SseHub`` and ``NotificationBroker`` — but it
deliberately does NOT ride the notification broker. The broker is an
edge-triggered, debounced push for *human attention* (WAITING/BLOCKED/ERROR); the
publisher needs the opposite granularity — the continuous WORKING-state todo churn
that never crosses an attention edge — so it subscribes to the same bus and reads
the whole story, not just the edges.

It copies the broker's *discipline* wholesale (see
``notifications/CLAUDE.md``):

- **bind(subscribe)** — the third subscriber to ``ActivityService.subscribe``.
- **pure decision / I/O dispatch split** — :meth:`observe` folds one delta into
  per-workspace coalescing state (fast, on the poll thread); :meth:`dispatch`
  does the forge round-trip off-thread. :meth:`render` is a pure function over a
  :class:`PublishSnapshot` with zero I/O.
- **coalescing, not debouncing** — the broker drops flapping edges; the publisher
  *folds* every change and flushes the merged result at most once per window
  (forges rate-limit same-comment PATCH storms — the Sweep lesson,
  ``tickets/CLAUDE.md``). Terminal states flush immediately and then stop.
- **best-effort isolation** — every provider call is wrapped; a forge failure is
  logged and swallowed at the call site (the #193 swallow obligation lives with
  the caller), never re-raised into the activity poll path.

All forge writes go through the Wave-1 ``TicketProvider`` comment I/O
(``list_comments`` / ``post_comment`` / ``edit_comment``); there is no direct
``httpx`` here.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING

from loguru import logger

from grove.core.activity import DashboardDelta, WorkspaceActivity
from grove.core.agents import AgentActivityState, TodoItem, TodoList
from grove.core.contracts.tickets import TicketProviderName
from grove.core.errors import GroveError, TicketCommentsUnsupported, TicketProviderError
from grove.core.issueops.marker import SIGNATURE_MARKER, STICKY_MARKER
from grove.core.tickets.provider import TicketProvider
from grove.core.workspace import CommitSummary

if TYPE_CHECKING:
    from grove.core.config import IssueOpsConfig
    from grove.core.contracts.issueops import IssueOpsEvent
    from grove.core.contracts.tickets import TicketRef
    from grove.core.manager import WorkspaceManager
    from grove.core.registry import RepoRegistry

# The seams the publisher reaches the engine through — narrow callables so a test
# drives the whole pipeline with an in-memory fake, and ``from_config`` binds them
# to a live ``RepoRegistry``. ``ProviderResolver`` returns ``None`` to route past a
# workspace whose provider isn't issue-ops-enabled (skip silently); ``TodoResolver``
# returns ``None`` when the workspace has no todo yet (a real answer, not an error).
ProviderResolver = Callable[[str, TicketProviderName], "TicketProvider | None"]
TodoResolver = Callable[[str, str], "TodoList | None"]

# State glyph + label for the status line. Presentation only (the render owns how a
# state reads), the todo sibling of the notifications ``REASONS`` phrasing.
_STATE_GLYPH: dict[AgentActivityState, str] = {
    AgentActivityState.STARTING: "🌱",
    AgentActivityState.WORKING: "🔨",
    AgentActivityState.WAITING: "🔔",
    AgentActivityState.BLOCKED: "⛔",
    AgentActivityState.IDLE: "💤",
    AgentActivityState.ERROR: "❌",
    AgentActivityState.UNKNOWN: "❔",
}
_STATE_LABEL: dict[AgentActivityState, str] = {
    AgentActivityState.STARTING: "Starting",
    AgentActivityState.WORKING: "Working",
    AgentActivityState.WAITING: "Waiting for review",
    AgentActivityState.BLOCKED: "Needs input",
    AgentActivityState.IDLE: "Idle",
    AgentActivityState.ERROR: "Error",
    AgentActivityState.UNKNOWN: "Unknown",
}


@dataclass(slots=True, frozen=True)
class PublishSnapshot:
    """The pure render input — everything one status comment shows, no I/O behind it.

    Built from one ``WorkspaceActivity`` row plus the separately-resolved todo, so
    :meth:`TicketStatusPublisher.render` is a total function of this dataclass and
    fully unit-testable. ``terminal`` swaps the live status body for a final
    summary (outcome + branch + link), after which the publisher stops.
    """

    workspace_id: str
    title: str
    repo_name: str
    branch: str
    state: AgentActivityState
    current_task: str | None
    todo: TodoList | None
    latest_commit: CommitSummary | None
    deep_link: str | None
    terminal: bool
    occurred_at: datetime


@dataclass(slots=True)
class _FlushJob:
    """One unit of dispatch work — the coalesced row and whether it's the finale."""

    row: WorkspaceActivity
    terminal: bool


@dataclass(slots=True)
class _WsPub:
    """Per-workspace publishing memory: the sticky id, the coalescer, the done latch.

    A non-persisted in-memory map keyed by workspace is the deliberate v1 store
    (the brief's sanctioned choice): the sticky comment id is recoverable from the
    forge itself via the :data:`STICKY_MARKER` scan on a cold start, so persisting it
    would duplicate a source of truth the thread already holds. ``dirty_since`` is
    stamped on the clean→dirty edge and NOT refreshed while dirty, so the window
    measures from the first unflushed change (bounding staleness to one window).
    """

    comment_id: str | None = None
    last_row: WorkspaceActivity | None = None
    fingerprint: tuple[object, ...] | None = None
    dirty: bool = False
    dirty_since: datetime | None = None
    done: bool = False


class TicketStatusPublisher:
    """Owns subscription + coalescing + dispatch for the live sticky comment.

    Long-lived alongside the daemon. Built via :meth:`from_config` (``None`` when
    disabled), then :meth:`bind` wires it to the activity bus and starts the
    single-worker dispatch pool; :meth:`close` unwinds both. Unbound, it runs every
    step inline on the caller's thread — the seam the tests drive with a fake clock.
    """

    def __init__(
        self,
        *,
        config: IssueOpsConfig,
        provider_resolver: ProviderResolver,
        todo_resolver: TodoResolver,
        clock: Callable[[], datetime] | None = None,
        terminal_states: frozenset[AgentActivityState] = frozenset(),
    ) -> None:
        self._provider_resolver = provider_resolver
        self._todo_resolver = todo_resolver
        self._window = timedelta(seconds=config.update_window_seconds)
        self._deep_link_base = config.deep_link_base_url.rstrip("/")
        # Mechanism, not policy: which agent states end the sticky comment is a
        # caller-supplied set (default empty — the lifecycle ``killed`` event is
        # the always-on terminal), so no "ERROR means done" verdict is baked in.
        self._terminal_states = terminal_states
        self._clock = clock if clock is not None else self._utcnow
        # Per-workspace state, unbounded like the broker's maps (loopback, small N).
        self._state: dict[str, _WsPub] = {}
        self._lock = Lock()
        self._pool: ThreadPoolExecutor | None = None
        self._timer: threading.Timer | None = None
        self._unsub: Callable[[], None] | None = None
        self._scheduling = False  # true only while bound to a live bus

    @classmethod
    def from_config(
        cls, cfg: IssueOpsConfig, *, registry: RepoRegistry | None
    ) -> TicketStatusPublisher | None:
        """Build a publisher from config, or ``None`` when issue-ops is disabled.

        Binds the resolvers to a live ``RepoRegistry``: the provider comes from the
        repo's ``ticket_providers`` registry, the todo from ``latest_todo`` — both
        best-effort, resolving to ``None`` on the typed engine errors (an
        unconfigured provider, a sessionless workspace) so the publisher skips
        rather than throws.
        """
        if not cfg.enabled:
            return None
        assert registry is not None  # enabled requires a registry to resolve against

        def provider_resolver(repo_root: str, name: TicketProviderName) -> TicketProvider | None:
            try:
                return registry.get(Path(repo_root)).ticket_providers.get(name)
            except GroveError:
                return None

        def todo_resolver(repo_root: str, workspace_id: str) -> TodoList | None:
            try:
                return registry.get(Path(repo_root)).latest_todo(workspace_id)
            except GroveError:
                return None

        return cls(config=cfg, provider_resolver=provider_resolver, todo_resolver=todo_resolver)

    # ─── lifecycle ───────────────────────────────────────────────────────────

    def bind(
        self, subscribe: Callable[[Callable[[DashboardDelta], None]], Callable[[], None]]
    ) -> None:
        """Subscribe to the delta bus and start the dispatch worker + self-scheduling.

        Takes the bus's ``subscribe`` callable (``ActivityService.subscribe``), not
        the service, so the publisher depends only on the bus shape and a test
        drives it with a bare stub — exactly the broker's contract.
        """
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="grove-issueops")
        self._scheduling = True
        self._unsub = subscribe(self.observe)

    def close(self) -> None:
        """Unsubscribe, cancel the pending flush, and drain the dispatch worker."""
        self._scheduling = False
        if self._unsub is not None:
            with contextlib.suppress(Exception):
                self._unsub()
            self._unsub = None
        with self._lock:
            timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None

    # ─── pure decision (poll thread) ─────────────────────────────────────────

    def observe(self, delta: DashboardDelta) -> None:
        """Fold one delta into the per-workspace coalescer. The bus callback.

        Fast and non-blocking (the broker's ``_on_delta`` contract): it only mutates
        in-memory state and, at most, hands a job to the dispatch worker. Two delta
        kinds matter — a ``session_activity`` carrying a render-relevant change
        marks the workspace dirty; a lifecycle ``killed`` flushes the terminal
        summary. Everything else is ignored.
        """
        if delta.kind == "workspace_changed":
            if delta.detail.get("event") == "killed":
                self._on_killed(delta.workspace_id)
            return
        row = delta.workspace
        if row is None or not row.state.ticket_refs:
            return  # no session payload, or no ticket to mirror onto → skip silently
        job = self._record(row, self._clock())
        if job is not None:
            self._run(self.dispatch, job)  # terminal → flush immediately
        else:
            self._arm()

    def _record(self, row: WorkspaceActivity, now: datetime) -> _FlushJob | None:
        """Fold ``row`` into coalescing state; return a job iff it must flush NOW.

        Gated on a *render-relevant* fingerprint so pure diff-stat churn (dirty
        files, ahead/behind — fields the comment never shows) can't schedule a
        redundant PATCH. A terminal agent state returns the job (immediate flush)
        and latches the workspace done; otherwise it just arms the window.
        """
        with self._lock:
            rec = self._state.setdefault(row.state.id, _WsPub())
            if rec.done:
                return None
            fingerprint = self._render_fingerprint(row)
            if fingerprint == rec.fingerprint:
                return None
            rec.fingerprint = fingerprint
            rec.last_row = row
            if self._is_terminal_state(row):
                rec.dirty = False
                rec.dirty_since = None
                rec.done = True
                return _FlushJob(row=row, terminal=True)
            if not rec.dirty:
                rec.dirty = True
                rec.dirty_since = now
            return None

    def _on_killed(self, workspace_id: str) -> None:
        """A workspace's lifecycle ended: flush a final summary once, then stop.

        The record is already deleted from the store, so the summary renders from
        the last cached row. With nothing cached (killed before any activity), we
        only latch the workspace done so a late delta can't reopen it.
        """
        with self._lock:
            rec = self._state.setdefault(workspace_id, _WsPub())
            already_done = rec.done
            rec.done = True
            rec.dirty = False
            rec.dirty_since = None
            row = rec.last_row
        if already_done or row is None:
            return
        self._run(self.dispatch, _FlushJob(row=row, terminal=True))

    def flush_pending(self, now: datetime | None = None) -> None:
        """Dispatch every workspace whose coalescing window has elapsed.

        The scheduled flush (the timer calls this); also the clock-driven seam a
        coalescing test drives directly. Collects due jobs under the lock, then
        dispatches outside it — the forge round-trip never holds the lock.
        """
        moment = now if now is not None else self._clock()
        jobs: list[_FlushJob] = []
        with self._lock:
            for rec in self._state.values():
                if (
                    rec.dirty
                    and rec.dirty_since is not None
                    and rec.last_row is not None
                    and (moment - rec.dirty_since) >= self._window
                ):
                    rec.dirty = False
                    rec.dirty_since = None
                    jobs.append(_FlushJob(row=rec.last_row, terminal=False))
        for job in jobs:
            self._run(self.dispatch, job)

    # ─── engine status seam (@grove status → flush now) ──────────────────────

    def publish(self, event: IssueOpsEvent, manager: WorkspaceManager) -> None:
        """Force the ticket's workspace to re-render its sticky comment NOW.

        The engine's ``StatusPublisher`` seam (the ``@grove status`` verb),
        matched structurally so the publisher never imports the engine module: it
        takes only the wire ``IssueOpsEvent`` and the ``WorkspaceManager`` the
        engine already resolved. Maps ticket → workspace via ``find_by_ticket``
        (the same lookup the engine's verbs use) and flushes that workspace's
        latest coalesced state immediately, bypassing the window. Best-effort —
        no workspace for the ticket, or none observed yet, is a silent no-op.
        """
        match = manager.find_by_ticket(event.provider, str(event.issue_number))
        if match is not None:
            self.flush_now(match.id)

    def flush_now(self, workspace_id: str) -> None:
        """Dispatch a workspace's latest coalesced row immediately, ignoring the window.

        A human asked for the current state (``@grove status``), so we skip the
        coalescing wait rather than let the ask sit for a window. A workspace with
        no cached row yet (no render-relevant delta observed) or already latched
        ``done`` is a silent no-op — there is nothing fresh to render.
        """
        with self._lock:
            rec = self._state.get(workspace_id)
            if rec is None or rec.done or rec.last_row is None:
                return
            rec.dirty = False
            rec.dirty_since = None
            row = rec.last_row
        self._run(self.dispatch, _FlushJob(row=row, terminal=False))

    # ─── side effects (dispatch worker / inline) ─────────────────────────────

    def dispatch(self, job: _FlushJob) -> None:
        """Resolve the ticket, build the snapshot, and post-or-edit the sticky comment.

        The whole body is best-effort: an unresolved provider or todo skips
        quietly, and every forge call is guarded so one failure degrades a single
        update and never re-raises into the poll path.
        """
        ws = job.row.state
        resolved = self._route(ws.repo_root, ws.ticket_refs)
        if resolved is None:
            return  # no issue-ops-enabled provider for this workspace → skip silently
        provider, ticket_id = resolved
        todo = self._todo(ws.repo_root, ws.id)
        snapshot = self._snapshot(job.row, todo=todo, terminal=job.terminal)
        self._publish(ws.id, provider, ticket_id, self.render(snapshot))

    def _route(
        self, repo_root: str, refs: Sequence[TicketRef]
    ) -> tuple[TicketProvider, str] | None:
        """The first ticket ref that resolves to an issue-ops-enabled provider.

        One sticky comment per workspace, on its first mirrorable ticket. A ref an
        unconfigured/uncapable provider owns resolves to ``None`` and is skipped.
        """
        for ref in refs:
            provider = self._provider_resolver(repo_root, ref.provider)
            if provider is not None:
                return provider, ref.id
        return None

    def _todo(self, repo_root: str, workspace_id: str) -> TodoList | None:
        try:
            return self._todo_resolver(repo_root, workspace_id)
        except Exception as exc:  # best-effort: a todo read miss never blocks the update
            logger.debug("issueops todo read failed for {}: {}", workspace_id, exc)
            return None

    def _publish(
        self, workspace_id: str, provider: TicketProvider, ticket_id: str, body: str
    ) -> None:
        """Edit the sticky comment in place, or post it once (found-or-created).

        Sticky discipline: reuse the known comment id; on a cold start (no id yet)
        recover it by scanning the thread for :data:`STICKY_MARKER` before posting a fresh
        one. A forge failure forgets the id so the next flush re-scans — which also
        recreates a comment a human deleted (the marker scan finds nothing → post).
        """
        comment_id = self._comment_id(workspace_id)
        if comment_id is None:
            comment_id = self._recover_comment_id(provider, ticket_id)
        try:
            if comment_id is not None:
                provider.edit_comment(comment_id, body)
            else:
                comment_id = provider.post_comment(ticket_id, body).id
        except (TicketProviderError, TicketCommentsUnsupported) as exc:
            logger.warning(
                "issueops publish failed workspace={} ticket={}: {}", workspace_id, ticket_id, exc
            )
            self._forget_comment_id(workspace_id)
            return
        self._store_comment_id(workspace_id, comment_id)

    def _recover_comment_id(self, provider: TicketProvider, ticket_id: str) -> str | None:
        """Cold-start recovery: the first thread comment carrying the STICKY marker.

        Scans for :data:`STICKY_MARKER`, never the general :data:`SIGNATURE_MARKER`:
        every engine reply carries the signature too, so a signature scan would
        adopt an old usage/refusal reply and edit the status render over it. The
        sticky marker is unique to this comment, so recovery only ever re-adopts
        the status comment (or finds nothing → post a fresh one).
        """
        try:
            comments = provider.list_comments(ticket_id)
        except (TicketProviderError, TicketCommentsUnsupported) as exc:
            logger.debug("issueops marker scan failed for ticket={}: {}", ticket_id, exc)
            return None
        for comment in comments:
            if STICKY_MARKER in comment.body:
                return comment.id
        return None

    # ─── pure render ─────────────────────────────────────────────────────────

    @classmethod
    def render(cls, snapshot: PublishSnapshot) -> str:
        """Rebuild the whole comment body from ``snapshot`` — a pure function, no I/O.

        Never a diff-patch of the prior body (the sticky-comment rule): the forge
        holds the last render, we always replace it wholesale. A terminal snapshot
        swaps the live status + checklist for a final summary.
        """
        if snapshot.terminal:
            return cls._render_terminal(snapshot)
        return cls._render_live(snapshot)

    @classmethod
    def _render_live(cls, s: PublishSnapshot) -> str:
        label = _STATE_LABEL.get(s.state, s.state.value)
        glyph = _STATE_GLYPH.get(s.state, "•")
        lines = [f"### {glyph} Grove — {label}", "", f"**{s.title}** on `{s.branch}`"]
        if s.current_task:
            lines += ["", s.current_task]
        if s.todo is not None and s.todo.items:
            lines += ["", "**Checklist**"]
            lines += [cls._checklist_line(item) for item in s.todo.items]
        if s.latest_commit is not None:
            lines += ["", f"Latest commit `{s.latest_commit.sha}` — {s.latest_commit.subject}"]
        if s.deep_link:
            lines += ["", f"[Open workspace]({s.deep_link})"]
        lines += ["", cls._footer("Updated", s.occurred_at)]
        return "\n".join(lines)

    @classmethod
    def _render_terminal(cls, s: PublishSnapshot) -> str:
        label = _STATE_LABEL.get(s.state, s.state.value)
        lines = [
            "### ✅ Grove — Session ended",
            "",
            f"**{s.title}** on `{s.branch}`",
            "",
            f"Final state: {label}",
        ]
        if s.latest_commit is not None:
            lines.append(f"Latest commit `{s.latest_commit.sha}` — {s.latest_commit.subject}")
        if s.deep_link:
            lines += ["", f"[Open workspace]({s.deep_link})"]
        lines += ["", cls._footer("Concluded", s.occurred_at)]
        return "\n".join(lines)

    @staticmethod
    def _checklist_line(item: TodoItem) -> str:
        # A GitHub/Gitea task list has only checked/unchecked; ``in_progress`` maps
        # to an unchecked box with an inline note rather than a third glyph.
        box = "x" if item.status == "completed" else " "
        note = " _(in progress)_" if item.status == "in_progress" else ""
        return f"- [{box}] {item.content}{note}"

    @staticmethod
    def _footer(verb: str, moment: datetime) -> str:
        # Both markers ride the footer, each on its own line — invisible in the
        # rendered thread. STICKY_MARKER is the cold-start recovery anchor (unique
        # to this sticky comment); SIGNATURE_MARKER is the engine's ingest anti-loop
        # guard (shared with every reply, so the bot never answers its own comment).
        return (
            f"<sub>{verb} {moment:%Y-%m-%d %H:%M UTC}</sub>\n\n{STICKY_MARKER}\n{SIGNATURE_MARKER}"
        )

    # ─── snapshot + helpers ──────────────────────────────────────────────────

    def _snapshot(
        self, row: WorkspaceActivity, *, todo: TodoList | None, terminal: bool
    ) -> PublishSnapshot:
        ws = row.state
        primary = row.primary
        return PublishSnapshot(
            workspace_id=ws.id,
            title=ws.title,
            repo_name=Path(ws.repo_root).name or ws.repo_root,
            branch=ws.branch,
            state=primary.state if primary is not None else AgentActivityState.UNKNOWN,
            current_task=primary.current_task if primary is not None else None,
            todo=todo,
            latest_commit=row.recent_commits[0] if row.recent_commits else None,
            deep_link=f"{self._deep_link_base}/w/{ws.id}" if self._deep_link_base else None,
            terminal=terminal,
            occurred_at=self._clock(),
        )

    def _is_terminal_state(self, row: WorkspaceActivity) -> bool:
        primary = row.primary
        return primary is not None and primary.state in self._terminal_states

    @staticmethod
    def _render_fingerprint(row: WorkspaceActivity) -> tuple[object, ...]:
        """The render-relevant change key — what a PATCH would actually alter.

        Deliberately excludes diff/ahead-behind/dirty counts (never shown in the
        comment) so their churn stays clean; includes ``tool_calls``/replies as the
        proxy for todo/checklist progress (the todo itself isn't on the row, and
        reading it is I/O we keep off the poll thread)."""
        ws = row.state
        primary = row.primary
        return (
            primary.state if primary is not None else None,
            primary.current_task if primary is not None else None,
            primary.tool_calls if primary is not None else 0,
            primary.assistant_replies if primary is not None else 0,
            ws.branch,
            row.recent_commits[0].sha if row.recent_commits else None,
        )

    # ─── comment-id memory (lock-guarded) ────────────────────────────────────

    def _comment_id(self, workspace_id: str) -> str | None:
        with self._lock:
            rec = self._state.get(workspace_id)
            return rec.comment_id if rec is not None else None

    def _store_comment_id(self, workspace_id: str, comment_id: str) -> None:
        with self._lock:
            self._state.setdefault(workspace_id, _WsPub()).comment_id = comment_id

    def _forget_comment_id(self, workspace_id: str) -> None:
        with self._lock:
            rec = self._state.get(workspace_id)
            if rec is not None:
                rec.comment_id = None

    # ─── dispatch + scheduling seams ─────────────────────────────────────────

    def _run(self, fn: Callable[[_FlushJob], None], job: _FlushJob) -> None:
        """Off-thread when bound (dispatch worker), inline when not (the test seam).

        A timer thread already past :meth:`close`'s ``cancel()`` can reach here
        after the pool has shut down; ``submit`` then raises ``RuntimeError`` on
        that Timer thread. Swallow it to a debug line — a post-shutdown fire has
        nothing left to flush, and it must never surface as an uncaught error.
        """
        pool = self._pool
        if pool is None:
            fn(job)
            return
        try:
            pool.submit(fn, job)
        except RuntimeError as exc:  # pool shut down between the read and the submit
            logger.debug("issueops dispatch after shutdown, dropped: {}", exc)

    def _arm(self) -> None:
        """Schedule one flush a window from now — only while bound to a live bus.

        Unbound (tests), the timer never arms: the coalescing test drives
        :meth:`flush_pending` on its own clock. Bound, a single in-flight timer
        coalesces every dirty workspace into one drain, re-arming while work
        remains — so a comment is PATCHed at most once per window.
        """
        if not self._scheduling:
            return
        with self._lock:
            if self._timer is not None:
                return
            self._timer = threading.Timer(self._window.total_seconds(), self._on_timer)
            self._timer.daemon = True
            self._timer.start()

    def _on_timer(self) -> None:
        with self._lock:
            self._timer = None
        self.flush_pending(self._clock())
        with self._lock:
            pending = any(rec.dirty for rec in self._state.values())
        if pending:
            self._arm()

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(tz=UTC)


__all__ = [
    "SIGNATURE_MARKER",
    "STICKY_MARKER",
    "ProviderResolver",
    "PublishSnapshot",
    "TicketStatusPublisher",
    "TodoResolver",
]
