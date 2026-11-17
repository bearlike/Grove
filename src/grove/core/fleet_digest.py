"""A bounded read of the whole fleet, and one message to many workspaces.

Both share one rule: a fleet question costs one BOUNDED read of the activity
projection the dashboard already maintains, never a rescan of git or
transcripts.

* :class:`FleetDigest` turns that projection into compact rows, pages them,
  and answers "what changed since my cursor" against the last set of row keys
  it handed each consumer.
* :class:`MailboxBatch` sends one message to a frozen recipient list as many
  ordinary single sends, idempotent on ``request_id`` across restarts through
  the :class:`~grove.core.delegations.DelegationJournal`.

**The row key decides what counts as a change, so what it leaves out matters as
much as what it holds.** Token counts, activity timestamps and ages tick on
their own; a key that held one would report every row as changed for work that
merely continued. Ages are computed at read time (the ``quiet`` flag), so time
passing alone is never a change. ``ACTIVE`` and ``IDLE`` collapse to one word
in the key for the same reason: they track pane output, which moves on every
keystroke of a working agent.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
import uuid
from collections import Counter, OrderedDict
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, get_args

from grove.core.agents.model import AgentActivityState
from grove.core.config import FleetDigestConfig
from grove.core.contracts.fleet import (
    DigestFlag,
    FleetDigestRow,
    FleetDigestTotals,
    FleetDigestView,
    MailboxBatchReceipt,
    MailboxBatchRequest,
)
from grove.core.contracts.mailboxes import MailboxReceipt, MailboxSendRequest
from grove.core.delegations import Delegation, DelegationJournal
from grove.core.errors import GroveError
from grove.core.mailboxes import MailboxDelivery
from grove.core.workspace import LIVE_STATUSES, WorkspaceStatus

if TYPE_CHECKING:
    from grove.core.activity import DashboardSnapshot, WorkspaceActivity

#: Flags in the order a reader should triage them, taken from the contract itself.
_FLAG_ORDER: tuple[DigestFlag, ...] = get_args(DigestFlag)


class DigestCursorInvalid(GroveError):
    """A ``since`` or ``page`` token that this daemon did not issue in any shape."""


class BatchConflict(GroveError):
    """A ``request_id`` already used for a DIFFERENT payload."""


# ─── the digest ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DigestRead:
    """One digest answer plus the keys it observed, or ``None`` for a page continuation.

    The keys travel beside the view rather than inside it because they are the
    acknowledgement's input, not something any client should hold.
    """

    view: FleetDigestView
    observed: dict[str, str] | None


@dataclass(frozen=True, slots=True)
class _Entry:
    row: FleetDigestRow
    key: str


@dataclass(slots=True)
class _Consumer:
    """The last read issued to one consumer: its change baseline and its open page set."""

    cursor: str
    keys: dict[str, str]
    issue: str
    view: FleetDigestView
    rows: tuple[FleetDigestRow, ...]


class FleetDigest:
    """Compact, paged, change-aware reads over the maintained activity projection.

    Holds no copy of the fleet. Per consumer it keeps only the keys it last
    handed out (the delta baseline) and the rows of the one read that consumer
    may still be paging through, so memory is bounded by consumers, never by
    history.
    """

    #: Rows per page: small enough that one page fits a model turn comfortably.
    PAGE_SIZE = 50
    #: The task line is the one free-text field on a row; the full text is a detail read.
    _TASK_CAP = 200
    #: Consumers whose last read is remembered.
    _CONSUMERS = 64

    def __init__(
        self,
        *,
        snapshot: Callable[[], tuple[DashboardSnapshot, int]],
        config: FleetDigestConfig,
        clock: Callable[[], datetime] | None = None,
        boot_id: str | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._config = config
        self._clock = clock or (lambda: datetime.now(UTC))
        # A cursor names THIS daemon run: the projection's sequence restarts
        # with the process, so a cursor from before a restart must resync
        # rather than be compared against numbers that mean something else.
        self._boot = boot_id or uuid.uuid4().hex[:12]
        self._consumers: OrderedDict[str, _Consumer] = OrderedDict()
        # Reads run on worker threads (`asyncio.to_thread`), several at once.
        self._lock = threading.Lock()

    # ── pure mapping: one projection row → one digest row and its key ──────

    @classmethod
    def row_of(
        cls,
        row: WorkspaceActivity,
        *,
        repo_name: str,
        now: datetime,
        quiet_minutes: float,
    ) -> FleetDigestRow:
        """Pure: the compact row for one workspace, its flags computed at *now*."""
        primary_row = row.sessions[0] if row.sessions else None
        primary = primary_row.activity if primary_row else None
        phase = row.phase
        task = primary.current_task if primary else None
        if task is not None and len(task) > cls._TASK_CAP:
            task = task[: cls._TASK_CAP - 1] + "…"
        return FleetDigestRow(
            workspace_id=row.state.id,
            title=row.state.title,
            repo_name=repo_name,
            status=row.state.status.value,
            agent=primary.state.value if primary else None,
            session_id=primary_row.session.session_id if primary_row else None,
            phase=phase.phase if phase else None,
            phase_note=phase.note if phase else None,
            phase_blocked=phase.blocked if phase else False,
            phase_updated_at=phase.updated_at if phase else None,
            todo_done=row.todo.completed if row.todo else None,
            todo_total=row.todo.total if row.todo else None,
            current_task=task,
            question_ids=cls._question_ids(row),
            tickets=[ref.key for ref in row.state.ticket_refs],
            subagents_active=row.fleet.active if row.fleet else None,
            last_event_at=primary.last_event_at if primary else None,
            observed_at=row.observed_at,
            flags=list(cls.flags_of(row, now=now, quiet_minutes=quiet_minutes)),
        )

    @classmethod
    def flags_of(
        cls, row: WorkspaceActivity, *, now: datetime, quiet_minutes: float
    ) -> tuple[DigestFlag, ...]:
        """Pure: every attention flag that applies, in triage order.

        ``quiet`` needs a reported phase to measure from; an agent that never
        reported one is not called quiet, because "no report" is a different
        fact and the row already says so with ``phase=None``.
        """
        status = row.state.status
        agent = row.primary.state if row.primary else None
        phase = row.phase
        raised: set[DigestFlag] = set()
        if cls._question_ids(row):
            raised.add("question")
        if (phase is not None and phase.blocked) or agent is AgentActivityState.BLOCKED:
            raised.add("blocked")
        if status is WorkspaceStatus.ERROR or agent is AgentActivityState.ERROR:
            raised.add("error")
        if status not in LIVE_STATUSES:
            raised.add("offline")
        if (
            agent is AgentActivityState.WORKING
            and phase is not None
            and now - phase.updated_at >= timedelta(minutes=quiet_minutes)
        ):
            raised.add("quiet")
        if phase is not None and phase.phase == "handoff":
            raised.add("handoff")
        return tuple(flag for flag in _FLAG_ORDER if flag in raised)

    @classmethod
    def key_of(cls, row: WorkspaceActivity) -> str:
        """Pure: the row key, a short stable hash of what a reader acts on.

        Deliberately excluded: token counts, reply and tool counts, activity
        timestamps and every age. They move while work merely continues, and a
        key holding one would turn every check into a wake.
        """
        status = row.state.status
        primary_row = row.sessions[0] if row.sessions else None
        phase = row.phase
        facts = [
            # ACTIVE/IDLE follow pane output, so they are one lifecycle fact here.
            "live" if status in LIVE_STATUSES else status.value,
            primary_row.activity.state.value if primary_row else None,
            phase.phase if phase else None,
            phase.blocked if phase else None,
            phase.updated_at.isoformat() if phase else None,
            sorted(cls._question_ids(row)),
            [row.todo.completed, row.todo.total] if row.todo else None,
            sorted(ref.key for ref in row.state.ticket_refs),
            primary_row.session.session_id if primary_row else None,
        ]
        digest = hashlib.sha256(json.dumps(facts, separators=(",", ":")).encode())
        return digest.hexdigest()[:16]

    @staticmethod
    def _question_ids(row: WorkspaceActivity) -> list[str]:
        """Open questions on top-level sessions; a sub-agent's prompt is its parent's detail."""
        return [
            question.id
            for session in row.sessions
            if session.session.parent_session_id is None
            for question in session.activity.questions
            if not question.answered
        ]

    # ── reads ───────────────────────────────────────────────────────────────

    def read(
        self,
        *,
        consumer: str | None = None,
        since: str | None = None,
        page: str | None = None,
        workspaces: Sequence[str] | None = None,
    ) -> DigestRead:
        """One page of the selection, or of what changed since *since*.

        Raises :class:`DigestCursorInvalid` for a token that is not one this
        daemon's code could have issued. A well-formed token from another run,
        selection or consumer position answers ``resync_required`` instead,
        because that is a state the caller recovers from, not a malformed call.
        """
        name = consumer or "anon"
        selection = tuple(sorted(set(workspaces))) if workspaces else None
        if page is not None:
            issue, offset = self._decode_page(page)
            with self._lock:
                held = self._consumers.get(name)
                if held is not None and held.issue == issue:
                    return DigestRead(self._page(held, offset), observed=None)
            # The paged read was replaced or forgotten: start over, honestly.
            return self._fresh(name, selection, resync=True)
        if since is not None:
            return self._since(name, selection, since)
        return self._fresh(name, selection, resync=False)

    def _fresh(self, name: str, selection: tuple[str, ...] | None, *, resync: bool) -> DigestRead:
        entries, degraded, cursor = self._entries(selection)
        rows = tuple(entry.row for entry in entries)
        return self._issue(name, entries, rows, (), degraded, cursor, selection, resync=resync)

    def _since(self, name: str, selection: tuple[str, ...] | None, since: str) -> DigestRead:
        boot, selected = self._decode_cursor(since)
        entries, degraded, cursor = self._entries(selection)
        with self._lock:
            prior = self._consumers.get(name)
        # Only the cursor this consumer was last handed has a baseline here; an
        # older one would be diffed against keys it never saw.
        if (
            boot != self._boot
            or selected != self._selection_hash(selection)
            or prior is None
            or prior.cursor != since
        ):
            rows = tuple(entry.row for entry in entries)
            return self._issue(name, entries, rows, (), degraded, cursor, selection, resync=True)
        current = {entry.row.workspace_id for entry in entries}
        changed = tuple(
            entry.row for entry in entries if prior.keys.get(entry.row.workspace_id) != entry.key
        )
        removed = tuple(sorted(set(prior.keys) - current))
        return self._issue(
            name, entries, changed, removed, degraded, cursor, selection, resync=False
        )

    def _entries(self, selection: tuple[str, ...] | None) -> tuple[list[_Entry], list[str], str]:
        """Every selected row with its key, the degraded repos, and the cursor."""
        snapshot, seq = self._snapshot()
        now = self._clock()
        wanted = set(selection) if selection is not None else None
        entries: list[_Entry] = []
        degraded: list[str] = []
        for group in snapshot.projects:
            if group.error is not None:
                degraded.append(group.repo_name)
            for row in group.workspaces:
                if wanted is not None and row.state.id not in wanted:
                    continue
                entries.append(
                    _Entry(
                        row=self.row_of(
                            row,
                            repo_name=group.repo_name,
                            now=now,
                            quiet_minutes=self._config.quiet_minutes,
                        ),
                        key=self.key_of(row),
                    )
                )
        entries.sort(key=lambda entry: entry.row.workspace_id)
        cursor = self._encode({"b": self._boot, "s": seq, "h": self._selection_hash(selection)})
        return entries, sorted(set(degraded)), cursor

    def _issue(
        self,
        name: str,
        entries: list[_Entry],
        rows: tuple[FleetDigestRow, ...],
        removed: tuple[str, ...],
        degraded: list[str],
        cursor: str,
        selection: tuple[str, ...] | None,
        *,
        resync: bool,
    ) -> DigestRead:
        """Remember this read as the consumer's baseline and return its first page."""
        keys = {entry.row.workspace_id: entry.key for entry in entries}
        view = FleetDigestView(
            observed_at=self._clock(),
            scope=list(selection) if selection is not None else None,
            totals=self.totals(entry.row for entry in entries),
            removed=list(removed),
            degraded_repos=degraded,
            cursor=cursor,
            resync_required=resync,
        )
        held = _Consumer(
            cursor=cursor, keys=keys, issue=uuid.uuid4().hex[:12], view=view, rows=rows
        )
        with self._lock:
            self._consumers[name] = held
            self._consumers.move_to_end(name)
            while len(self._consumers) > self._CONSUMERS:
                self._consumers.popitem(last=False)
            first = self._page(held, 0)
        return DigestRead(first, observed=keys)

    def _page(self, held: _Consumer, offset: int) -> FleetDigestView:
        size = self.PAGE_SIZE
        end = offset + size
        more = end < len(held.rows)
        return held.view.model_copy(
            update={
                "rows": list(held.rows[offset:end]),
                # Removals belong to the read, not to a page; repeating them
                # on every page would ask the caller to forget twice.
                "removed": held.view.removed if offset == 0 else [],
                "next_page": self._encode({"i": held.issue, "o": end}) if more else None,
            }
        )

    @staticmethod
    def totals(rows: Iterable[FleetDigestRow]) -> FleetDigestTotals:
        """Counts over the whole selection. Missing agent or phase is not counted as a value."""
        rows = list(rows)
        return FleetDigestTotals(
            workspaces=len(rows),
            by_status=dict(Counter(row.status for row in rows)),
            by_agent=dict(Counter(row.agent for row in rows if row.agent is not None)),
            by_phase=dict(Counter(row.phase for row in rows if row.phase is not None)),
            by_flag=dict(Counter(flag for row in rows for flag in row.flags)),
        )

    # ── tokens ──────────────────────────────────────────────────────────────

    @staticmethod
    def _selection_hash(selection: tuple[str, ...] | None) -> str:
        text = "*" if selection is None else ",".join(selection)
        return hashlib.sha256(text.encode()).hexdigest()[:12]

    @staticmethod
    def _encode(payload: Mapping[str, Any]) -> str:
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def _decode(token: str) -> dict[str, Any]:
        try:
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise DigestCursorInvalid("not a digest token this daemon issued") from exc
        if not isinstance(payload, dict):
            raise DigestCursorInvalid("not a digest token this daemon issued")
        return payload

    @classmethod
    def _decode_cursor(cls, token: str) -> tuple[str, str]:
        payload = cls._decode(token)
        boot, seq, selected = payload.get("b"), payload.get("s"), payload.get("h")
        if not (isinstance(boot, str) and isinstance(seq, int) and isinstance(selected, str)):
            raise DigestCursorInvalid("`since` is not a digest cursor")
        return boot, selected

    @classmethod
    def _decode_page(cls, token: str) -> tuple[str, int]:
        payload = cls._decode(token)
        issue, offset = payload.get("i"), payload.get("o")
        if not (isinstance(issue, str) and isinstance(offset, int) and offset >= 0):
            raise DigestCursorInvalid("`page` is not a digest page token")
        return issue, offset


# ─── batch mail ─────────────────────────────────────────────────────────────

#: Holds one recipient's lifecycle lock for the duration of its send.
RecipientLock = Callable[[str], AbstractAsyncContextManager[None]]


class MailboxBatch:
    """One message to a frozen recipient list, as N ordinary sends with one receipt each.

    A batch is never a second transport: each recipient gets the same
    ``MailboxSendRequest`` and the same ``MailboxDelivery.send`` a single send
    gets, under that recipient's own lifecycle lock and never two locks at once.

    Idempotency has two layers with two jobs. The :class:`DelegationJournal`
    is the durable one: the request is recorded before anything is sent, so a
    retried id after a daemon restart replays the recorded receipts instead of
    delivering again. The in-memory future map is the concurrent one: a
    duplicate arriving while the first send is still running waits on it,
    because the journal cannot yet answer for receipts nobody has recorded.
    """

    _REMEMBERED = 256
    #: Sends in flight at once. Each holds only its own recipient's lock, so this
    #: bounds how long one slow pane can hold up the rest, not correctness.
    CONCURRENCY = 4
    #: What a replay says for a recipient whose send never recorded a receipt.
    UNRECORDED = "no receipt was recorded before the daemon stopped"

    def __init__(
        self,
        delivery: MailboxDelivery,
        *,
        hold: RecipientLock,
        journal: DelegationJournal,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._delivery = delivery
        self._hold = hold
        self._journal = journal
        self._clock = clock or (lambda: datetime.now(UTC))
        # request_id → (payload digest, the answer once it exists). Recorded
        # BEFORE the first await, so a concurrent duplicate waits on the first
        # send instead of starting a second one.
        self._records: OrderedDict[str, tuple[str, asyncio.Future[MailboxBatchReceipt]]] = (
            OrderedDict()
        )

    @staticmethod
    def digest(request: MailboxBatchRequest) -> str:
        """The payload's identity: everything but ``request_id``, canonically serialized."""
        return DelegationJournal.digest(request.model_dump(mode="json", exclude={"request_id"}))

    async def send(self, request: MailboxBatchRequest) -> MailboxBatchReceipt:
        """Deliver to every recipient, or replay the recorded answer for a retried id.

        Raises :class:`BatchConflict` when the id was used for another payload,
        in this run or any earlier one. Must run on the event loop: the record
        map is loop-owned state.
        """
        digest = self.digest(request)
        held = self._records.get(request.request_id)
        if held is not None:
            recorded_digest, answer = held
            if recorded_digest != digest:
                raise self._conflict(request.request_id)
            recorded = await asyncio.shield(answer)
            return recorded.model_copy(update={"replayed": True})
        answer = asyncio.get_running_loop().create_future()
        self._records[request.request_id] = (digest, answer)
        self._forget_oldest()
        try:
            result = await self._journaled(request, digest)
        except BaseException as exc:
            # A cancellation, a conflict found in the journal, or a journal
            # that could not record the request. Waiters see the same outcome.
            self._records.pop(request.request_id, None)
            if isinstance(exc, Exception):
                answer.set_exception(exc)
                answer.exception()  # retrieved: this caller re-raises it
            else:
                answer.cancel()
            raise
        answer.set_result(result)
        return result

    async def _journaled(self, request: MailboxBatchRequest, digest: str) -> MailboxBatchReceipt:
        """Replay what an earlier run recorded, or record the request and then send it."""
        prior = await asyncio.to_thread(self._journal.lookup, request.request_id)
        if prior is not None:
            if prior.digest != digest:
                raise self._conflict(request.request_id)
            return self._replay(prior)
        # Recorded BEFORE dispatch, and a failure here raises: a batch whose
        # request cannot be recorded cannot promise it will not send twice.
        await asyncio.to_thread(
            self._journal.record_request,
            request_id=request.request_id,
            kind="batch",
            sender=request.sender,
            digest=digest,
            subject=request.subject,
            body=request.body,
            recipients=request.recipients,
            created_at=self._clock(),
        )
        receipts = await self._deliver_all(request)
        return MailboxBatchReceipt(request_id=request.request_id, receipts=receipts)

    def _replay(self, prior: Delegation) -> MailboxBatchReceipt:
        """The recorded answer in request order; a recipient with no receipt is ``unknown``.

        A missing receipt means the run that sent this stopped before that
        send returned. The bytes may have reached the pane, so the honest
        answer is "unknown", and it is never a reason to deliver again.
        """
        recorded = {
            (r.recipient.workspace_id, r.recipient.agent): r
            for r in prior.receipts
            if r.recipient is not None
        }
        receipts = [
            recorded.get((recipient.workspace_id, recipient.agent))
            or MailboxReceipt(
                sender=prior.sender,
                recipient=recipient,
                stage="unknown",
                reason="transport_failed",
                detail=self.UNRECORDED,
                created_at=self._clock(),
            )
            for recipient in prior.recipients
        ]
        return MailboxBatchReceipt(request_id=prior.request_id, receipts=receipts, replayed=True)

    @staticmethod
    def _conflict(request_id: str) -> BatchConflict:
        return BatchConflict(f"request_id {request_id!r} was already used for a different message")

    async def _deliver_all(self, request: MailboxBatchRequest) -> list[MailboxReceipt]:
        gate = asyncio.Semaphore(self.CONCURRENCY)

        async def one(index: int) -> MailboxReceipt:
            recipient = request.recipients[index]
            single = MailboxSendRequest(
                sender=request.sender,
                recipient=recipient,
                subject=request.subject,
                body=request.body,
                in_reply_to=request.in_reply_to,
            )
            async with gate, self._hold(recipient.workspace_id):
                try:
                    receipt: MailboxReceipt = await asyncio.to_thread(self._delivery.send, single)
                except Exception as exc:
                    # The delivery's own refusals are receipts already; anything
                    # else means the outcome cannot be established. Never retried.
                    receipt = MailboxReceipt(
                        sender=request.sender,
                        recipient=recipient,
                        stage="unknown",
                        reason="transport_failed",
                        detail=type(exc).__name__,
                        created_at=self._clock(),
                    )
            # Per recipient, as each send returns: a stop mid-batch keeps every
            # receipt that already exists, and only the rest replay as unknown.
            await asyncio.to_thread(self._journal.record_receipt, request.request_id, receipt)
            return receipt

        # gather keeps request order whatever order the sends finish in.
        return list(await asyncio.gather(*(one(i) for i in range(len(request.recipients)))))

    def _forget_oldest(self) -> None:
        """Bound the record map, never forgetting a batch still being delivered."""
        while len(self._records) > self._REMEMBERED:
            oldest, (_digest, answer) = next(iter(self._records.items()))
            if not answer.done():
                return
            del self._records[oldest]


__all__ = [
    "BatchConflict",
    "DigestCursorInvalid",
    "DigestRead",
    "FleetDigest",
    "MailboxBatch",
    "RecipientLock",
]
