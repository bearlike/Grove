"""A registered callback: what to watch for, and who to tell when it happens.

An agent waiting on something slow — a CI pipeline, a deploy, a queue — has had
exactly one option: sleep in a loop. That costs a process and a pane per waiter,
dies silently when the pane dies, and survives neither a daemon restart nor a
reboot. A watch is the durable replacement: the agent says what to wait for and
halts, and Grove delivers the outcome as ordinary mail when it happens.

**The predicate is the extension point, and Grove models no domain of its own.**
``WatchPredicate`` is a discriminated union in the :class:`BranchPlan` shape, so
a new kind of thing to wait for is a new variant plus a
:class:`~grove.core.watches.Watcher` — never a change to the scheduler, the
registry or any surface. ``ci`` ships built in because it is what agents ask for
first, but it holds no privileged position: it is one variant among four.

**A callback is not evidence that a model acted on it.** Delivery rides the
mailbox, so it inherits that contract exactly (see
:mod:`grove.core.contracts.mailboxes`): ``delivered`` means Grove handed the
bytes to a transport. Nothing here claims a session read them.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from grove.core.contracts.mailboxes import MailboxAddress
from grove.core.contracts.tickets import TicketKind, TicketProviderName

WatchId = Annotated[str, Field(pattern=r"^wch_[a-f0-9]{32}$")]

# The lifecycle of one watch, and every member is a state a human acts on
# differently. `pending` is still being evaluated; `fired` means the predicate
# went terminal and the callback was handed to a transport; `expired` means the
# deadline arrived first — itself delivered, because an agent that halted must
# never be left with no signal at all. `cancelled` is a caller's own withdrawal.
# `undeliverable` is the one that is nobody's fault and must not read as either
# success or failure of the WATCH: the predicate may well have gone terminal,
# and the recipient had stopped being live by the time we went to tell it.
WatchState = Literal["pending", "fired", "expired", "cancelled", "undeliverable"]

# What Grove observed about the callback it sent, mirroring `MailboxStage` for
# the reason the mailbox docstring gives: a stage that suggested a model had
# read something would be a claim no transport can support. `unknown` is the
# crash-shaped one — the outcome was persisted, the send was attempted, and the
# process died before the receipt came back. It is NEVER retried.
WatchReceipt = Literal["delivered", "rejected", "unknown"]

# The floor on how often a watch may re-evaluate. An agent asking to poll a
# forge every second turns one registration into a rate-limit incident, and no
# real subject settles faster than this anyway. Held here rather than in config
# because it protects somebody else's service from Grove, which is not a knob a
# deployment should be able to turn off.
MIN_INTERVAL = timedelta(seconds=15)

# The ceiling on how long a watch may live before it gives up and says so. A
# watch is a promise to wake somebody; an unbounded one is a row that outlives
# the task, the workspace and the person who registered it.
MAX_DEADLINE = timedelta(days=1)

# How long an OPEN-ENDED watch waits when the caller names no deadline. The
# caller has halted, so this is the longest a session can sit stalled on a
# subject that never settles — a CI run that hangs, a command that never exits,
# a forge that stops answering — before it is told so and can carry on. Short
# on purpose: re-registering is one call, while a stalled session is invisible.
# A timer is exempt because its settle time is known by construction; see
# `WatchRegistration.deadline`.
DEFAULT_DEADLINE = timedelta(minutes=15)

# How often a watch re-evaluates when the caller does not say.
DEFAULT_INTERVAL = timedelta(seconds=30)


class TimerPredicate(BaseModel):
    """Wake me at a wall-clock instant. The durable replacement for ``sleep``.

    It evaluates no external state at all, which is what makes it the cheapest
    thing in the system: the scheduler's own deadline IS the answer, so a timer
    costs one heap entry and never runs a probe.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["timer"] = "timer"
    at: datetime


class CiPredicate(BaseModel):
    """Wake me when the checks on this commit have all concluded.

    Keyed on an immutable ``head_sha`` and never on a branch name, because a
    force-push or a rebase makes a DIFFERENT commit with the same branch name —
    a watch that followed the name would answer about work nobody asked about.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["ci"] = "ci"
    # The closed provider vocabulary rather than a free string: this value
    # SELECTS a configured provider (and therefore a base URL and a credential),
    # so a typo must be refused at the wire rather than resolved to nothing at
    # probe time, hours after the agent halted waiting for it.
    provider: TicketProviderName
    owner: str = Field(min_length=1, max_length=100)
    repo: str = Field(min_length=1, max_length=100)
    head_sha: str = Field(pattern=r"^[0-9a-f]{7,40}$")


class CommandPredicate(BaseModel):
    """Wake me when this command exits with one of these statuses.

    The escape hatch for everything Grove does not model. ``argv`` is a list and
    is never a shell string: a predicate assembled from a string would make the
    registration itself a shell injection, and the value-becomes-syntax class is
    one this tree has already paid for at several other boundaries.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["command"] = "command"
    argv: list[str] = Field(min_length=1, max_length=64)
    terminal_exit_codes: list[int] = Field(default_factory=lambda: [0])
    workspace_id: str = Field(pattern=r"^[a-f0-9]{32}$")

    @field_validator("argv")
    @classmethod
    def no_flag_shaped_program(cls, value: list[str]) -> list[str]:
        """The program name may not begin with ``-``; it would be read as a flag."""
        if value[0].startswith("-"):
            raise ValueError("the command to run cannot begin with '-'")
        return value


class TicketSnapshot(BaseModel):
    """The fields of a ticket a PERSON changes, as last seen. The baseline a check compares against.

    Deliberately absent: everything Grove itself writes to a tracker. Assignees
    are excluded because Grove assigns its own account. The last-updated time
    is excluded because every sticky-comment edit can move it. The body is held
    as a digest taken AFTER Grove's badge footer is cut out. Leaving these out
    is what keeps Grove's own writes from producing a notification, so no loop
    is possible.

    ``None`` on ``body_digest`` or ``comment_count`` means the tracker does not
    report that field. It never means empty.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str | None = None
    status: str | None = None
    draft: bool = False
    body_digest: str | None = None
    comment_count: int | None = None


class TicketPredicate(BaseModel):
    """Tell this workspace whenever a person changes this attached issue or pull request.

    The one STANDING predicate: it never settles on a change. Each change is
    mailed, and the watch keeps going with the new ``baseline``. It also has no
    deadline, because a deadline exists to wake an agent that stopped to wait,
    and nobody stops for this. Its lifetime is the workspace's: Grove registers
    it when a ticket is attached to a running workspace, and cancels it on
    detach, pause or kill. Agents never register it by hand.

    ``baseline`` is ``None`` until the first check has read the ticket, so the
    state at attach time is recorded silently rather than reported as a change.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["ticket"] = "ticket"
    # The ticket is named the way the workspace names it (a bare `provider:id`
    # ref), so the provider must be resolved through that workspace's repo.
    workspace_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    provider: TicketProviderName
    ticket_id: str = Field(min_length=1, max_length=64)
    ticket_kind: TicketKind = "issue"
    baseline: TicketSnapshot | None = None


WatchPredicate = Annotated[
    TimerPredicate | CiPredicate | CommandPredicate | TicketPredicate,
    Field(discriminator="kind"),
]


class WatchRegistration(BaseModel):
    """One request to be woken. The caller names both the subject and itself.

    ``recipient`` is where the callback goes, and it is the caller's own address
    in every ordinary use — an agent registering a watch on its own work. Like a
    mailbox ``sender`` it is DECLARED rather than proven, which is what lets an
    orchestrator register a watch on behalf of a worker it supervises.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    recipient: MailboxAddress
    predicate: WatchPredicate
    every: timedelta = Field(default=DEFAULT_INTERVAL)
    # `None` means "the default for this predicate", NEVER "no deadline": every
    # watch expires, and the expiry is itself delivered, so a halted session is
    # always pinged back. Open-ended predicates default to DEFAULT_DEADLINE; a
    # timer defaults to its own instant, since a timer set for 30 minutes under
    # a 15-minute cap could only ever expire and never fire. An explicit value
    # always wins, up to MAX_DEADLINE. Resolution lives in the scheduler, which
    # is the one place that knows "now".
    deadline: timedelta | None = None
    note: str = Field(default="", max_length=200)

    @field_validator("every")
    @classmethod
    def not_below_floor(cls, value: timedelta) -> timedelta:
        if value < MIN_INTERVAL:
            raise ValueError(f"`every` must be at least {int(MIN_INTERVAL.total_seconds())}s")
        return value

    @field_validator("deadline")
    @classmethod
    def within_ceiling(cls, value: timedelta | None) -> timedelta | None:
        if value is None:
            return None
        if value <= timedelta(0):
            raise ValueError("`deadline` must be positive")
        if value > MAX_DEADLINE:
            raise ValueError(f"`deadline` may not exceed {MAX_DEADLINE}")
        return value


class WatchOutcome(BaseModel):
    """What a watcher concluded, in the words the recipient will read.

    ``summary`` is a whole self-contained sentence rather than a status code,
    because of where it lands: an idle session receiving this starts a FRESH
    turn with no memory of registering anything, so "your watch fired" tells it
    nothing it can act on. ``ok`` says whether the thing being waited for
    succeeded — distinct from whether the watch itself worked.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    summary: str = Field(min_length=1, max_length=4000)
    url: str | None = None


class WatchView(BaseModel):
    """One registered watch as any client sees it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: WatchId
    recipient: MailboxAddress
    predicate: WatchPredicate
    state: WatchState
    note: str = ""
    # The cadence the caller asked for, stored rather than re-derived: the
    # scheduler once computed it from `next_due - created_at`, which grows every
    # time the watch is re-armed, so a 30s watch was probed at 30, 60, 120, 240s.
    every: timedelta = DEFAULT_INTERVAL
    created_at: datetime
    # `None` only for a standing watch (`ticket`), which has no halted agent to
    # hand a turn back to and lives exactly as long as its workspace.
    expires_at: datetime | None
    next_due: datetime | None = None
    settled_at: datetime | None = None
    outcome: WatchOutcome | None = None
    receipt: WatchReceipt | None = None
    receipt_detail: str | None = None


class WatchList(BaseModel):
    """Every watch this host currently holds, newest registration first."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    watches: list[WatchView] = Field(default_factory=list)


__all__ = [
    "DEFAULT_DEADLINE",
    "DEFAULT_INTERVAL",
    "MAX_DEADLINE",
    "MIN_INTERVAL",
    "CiPredicate",
    "CommandPredicate",
    "TicketPredicate",
    "TicketSnapshot",
    "TimerPredicate",
    "WatchId",
    "WatchList",
    "WatchOutcome",
    "WatchPredicate",
    "WatchReceipt",
    "WatchRegistration",
    "WatchState",
    "WatchView",
]
