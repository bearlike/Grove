"""The one thing every predicate implements, and the registry that dispatches.

A :class:`Watcher` answers exactly one question — *has this subject settled
yet?* — and holds no scheduling, persistence, delivery or retry logic of any
kind. That separation is the whole extension story: `ci`, `command` and every
later predicate inherit this and nothing else, so adding one cannot change when
Grove wakes up, what it writes to disk, or how a callback is delivered.

**Returning ``None`` is the load-bearing case.** It means *not settled yet*, and
it is what the scheduler reads as "ask me again at the next interval". An
implementation that cannot reach its subject — a forge that timed out, a rate
limit, a container that is gone — must also answer ``None`` rather than
inventing a failed outcome: "I could not tell" and "it failed" are answers a
recipient acts on differently, and collapsing them is how a transient network
error comes to read as a red build.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, cast

from pydantic import BaseModel

from grove.core.contracts.watches import TimerPredicate, WatchOutcome, WatchPredicate


@dataclass(frozen=True, slots=True)
class StillWatching:
    """A STANDING watch saw something and carries on: keep ``predicate``, mail ``outcome`` if set.

    The third answer :meth:`Watcher.observe` can give, beside ``None`` (not
    yet) and a :class:`WatchOutcome` (settled). A standing watch never settles
    on what it sees. It reports it, remembers it through its updated
    predicate, and is checked again. ``outcome`` is ``None`` when the move was
    not news, such as recording the first baseline or a change only Grove made.
    """

    predicate: WatchPredicate
    outcome: WatchOutcome | None = None


Observation = WatchOutcome | StillWatching | None
"""Everything a watcher can answer: settled, carrying on, or not yet / cannot tell."""


class Watcher[P: BaseModel](ABC):
    """Evaluate one kind of predicate. Pure of scheduling, storage and delivery.

    Subclasses bind ``P`` to their own predicate variant and implement
    :meth:`observe`; the union-narrowing happens ONCE, here, so no subclass
    repeats it and none can forget it. They are constructed once and reused for
    every watch of that kind, so an implementation holds collaborators (a
    provider registry, a workspace manager) and never per-watch state.

    The generic exists for a concrete reason rather than for elegance: without
    it every watcher took the whole ``WatchPredicate`` union and had to re-prove
    the variant to its own helpers, which is boilerplate a type checker cannot
    verify is correct and a reader cannot tell is complete.
    """

    #: The ``kind`` tag this watcher serves, matching its predicate variant.
    kind: ClassVar[str]

    def evaluate(self, predicate: WatchPredicate, now: datetime) -> Observation:
        """Dispatch guard plus :meth:`observe`. Subclasses override the latter.

        A predicate of the wrong kind is a routing bug in the registry rather
        than a caller error, so it raises instead of answering ``None`` — a
        silent "not yet" here would leave the watch pending forever while
        looking perfectly healthy.
        """
        if predicate.kind != self.kind:  # pragma: no cover - the registry routes by kind
            raise TypeError(f"{type(self).__name__} cannot evaluate a {predicate.kind!r} predicate")
        return self.observe(cast("P", predicate), now)

    @abstractmethod
    def observe(self, predicate: P, now: datetime) -> Observation:
        """Has this settled? ``None`` means not yet — including "I cannot tell".

        Runs on a shared worker thread and must be one BOUNDED observation: a
        single API read or a single timeout-bounded command. It must never block
        waiting for the subject to change, which is precisely the sleeping this
        whole mechanism exists to replace — a watcher that calls something like
        ``gh run watch`` has moved the agent's sleep loop into Grove's one
        worker, where it stalls every other watch on the host.
        """


class TimerWatcher(Watcher[TimerPredicate]):
    """The clock reached the requested instant.

    Evaluated rather than special-cased so that a timer is an ordinary watch in
    every other respect — same registry, same persistence, same callback. The
    scheduler never actually pays a probe for one, because a timer's own
    ``at`` is its next due time, so this returns terminal on the first ask.
    """

    kind = "timer"

    def observe(self, predicate: TimerPredicate, now: datetime) -> WatchOutcome | None:
        if now < predicate.at:
            return None
        return WatchOutcome(
            ok=True,
            summary=f"The timer you set for {predicate.at.isoformat()} has elapsed.",
        )


class WatcherRegistry:
    """Which watcher serves which predicate kind.

    A plain mapping rather than a plugin mechanism: the set of predicates is
    known at build time, and every attempt to make this pluggable would be
    inventing a second way to add what a class already adds.
    """

    def __init__(self, watchers: list[Watcher[Any]]) -> None:
        self._by_kind: dict[str, Watcher[Any]] = {w.kind: w for w in watchers}

    def get(self, predicate: WatchPredicate) -> Watcher[Any] | None:
        """The watcher for this predicate, or ``None`` if none is registered.

        A missing watcher is a real state rather than a bug to raise on: a
        deployment may have a durable row for a predicate whose watcher was
        removed, and that row must settle as unwatchable instead of taking down
        every tick behind it.
        """
        return self._by_kind.get(predicate.kind)


__all__ = ["Observation", "StillWatching", "TimerWatcher", "Watcher", "WatcherRegistry"]
