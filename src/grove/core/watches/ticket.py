"""Watch an attached issue or pull request, and describe what a person changed.

Two classes, one question each:

* :class:`TicketChange` is PURE. It compares two snapshots plus the comments
  that arrived between them, and writes the text a recipient reads. It knows
  nothing about forges, schedules or mail.
* :class:`TicketWatcher` is the I/O edge. It reads the ticket through the
  shared :class:`~grove.core.tickets.reads.TicketReads` cache (at most once a
  minute per ticket, however many workspaces hold it), lists the thread only
  when the comment count rose, and hands the scheduler the new baseline.

**Grove's own writes must never come back as a change, or the product loops.**
The sticky comment is edited every few seconds. If each edit woke the agent,
the agent would report a new phase, and the comment would be edited again. The
exclusion is built into what is compared, not bolted on as a filter afterwards:

* The snapshot holds only fields a person changes. Assignees are left out
  because Grove assigns its own account; the last-updated time is left out
  because a sticky edit can move it.
* The description is digested after
  :func:`~grove.core.issueops.footer.splice_footer` has removed Grove's badge
  region.
* A new comment counts only if it carries neither issue-ops marker and was not
  written by the account the provider's own token authenticates as.

A change that is only Grove's own (the count rose because Grove commented)
still moves the baseline, but silently, with an empty summary.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from loguru import logger

from grove.core.contracts.tickets import TicketComment
from grove.core.contracts.watches import TicketPredicate, TicketSnapshot, WatchOutcome
from grove.core.errors import GroveError
from grove.core.issueops.footer import splice_footer
from grove.core.issueops.marker import SIGNATURE_MARKER, STICKY_MARKER
from grove.core.tickets.provider import TicketProvider, TicketState
from grove.core.tickets.reads import TicketReads
from grove.core.watches.watcher import StillWatching, Watcher

ProviderLookup = Callable[[TicketPredicate], TicketProvider | None]
"""The provider the watched workspace's repo configures, or ``None`` if it is gone.

A lookup rather than a registry, so this module never imports the engine's
orchestration, which is the same reason ``CommandWatcher`` takes one.
"""


@dataclass(frozen=True, slots=True)
class TicketChange:
    """What a person changed on one ticket between two checks. Pure; no I/O."""

    label: str
    before: TicketSnapshot
    after: TicketSnapshot
    comments: tuple[TicketComment, ...] = ()

    EXCERPT_CHARS: ClassVar[int] = 280
    """How much of one comment a message quotes; the mail points at the thread, not a copy."""

    @staticmethod
    def snapshot(state: TicketState) -> TicketSnapshot:
        """The person-authored part of one read, with Grove's badge footer removed first."""
        digest = None
        if state.body is not None:
            human = splice_footer(state.body, "")
            digest = hashlib.sha256(human.encode()).hexdigest()
        return TicketSnapshot(
            title=state.ref.title,
            status=state.ref.status,
            draft=state.ref.draft,
            body_digest=digest,
            comment_count=state.comment_count,
        )

    def lines(self) -> list[str]:
        """One sentence per change a person made, in the order a reader triages them."""
        before, after = self.before, self.after
        out: list[str] = []
        if before.status != after.status:
            out.append(
                f"State changed from {before.status or 'unknown'} to {after.status or 'unknown'}."
            )
        if before.draft != after.draft:
            out.append("Marked ready for review." if before.draft else "Converted to a draft.")
        if before.title != after.title:
            out.append(f"Title changed from {before.title!r} to {after.title!r}.")
        if before.body_digest is not None and before.body_digest != after.body_digest:
            out.append("The description was edited.")
        for comment in self.comments:
            text = " ".join(comment.body.split())
            if len(text) > self.EXCERPT_CHARS:
                text = text[: self.EXCERPT_CHARS - 1] + "…"
            out.append(f"New comment from {comment.author or 'someone'}: {text}")
        return out

    def summary(self) -> str:
        """The mailed text, or ``""`` when nothing a person did is in it.

        It must stand alone, because it usually lands in a fresh turn. The
        closing caveat is there because Grove cannot tell an agent's own action
        (taken with the operator's token) from the operator's.
        """
        lines = self.lines()
        if not lines:
            return ""
        return "\n".join(
            [
                f"{self.label} changed:",
                *(f"- {line}" for line in lines),
                "",
                "A change made with the account Grove's agents use (possibly your own"
                " action through that token) is reported like anyone else's.",
            ]
        )


class TicketWatcher(Watcher[TicketPredicate]):
    """Report each change a person makes to an attached ticket. Never settles on its own."""

    kind = "ticket"

    def __init__(self, providers: ProviderLookup, reads: TicketReads) -> None:
        self._providers = providers
        self._reads = reads

    def observe(self, predicate: TicketPredicate, now: datetime) -> StillWatching | None:  # noqa: ARG002
        """``None`` for "nothing moved" or "could not tell"; otherwise carry on from a new baseline.

        The first check has no baseline yet and records one silently, so the
        state the ticket was in when it was attached is never reported as news.
        """
        provider = self._providers(predicate)
        if provider is None:
            return None
        try:
            state = self._reads.state(provider, predicate.ticket_id, predicate.ticket_kind)
            after = TicketChange.snapshot(state)
            before = predicate.baseline
            if before == after:
                return None
            comments = (
                () if before is None else self._new_comments(provider, predicate, before, after)
            )
        except GroveError as exc:
            logger.debug(
                "ticket watch could not read {}#{}: {}",
                predicate.provider,
                predicate.ticket_id,
                exc,
            )
            return None
        carry_on = predicate.model_copy(update={"baseline": after})
        if before is None:
            return StillWatching(carry_on)
        noun = "Pull request" if predicate.ticket_kind == "pull_request" else "Issue"
        summary = TicketChange(
            label=f"{noun} #{predicate.ticket_id}", before=before, after=after, comments=comments
        ).summary()
        outcome = WatchOutcome(ok=True, summary=summary, url=state.ref.url) if summary else None
        return StillWatching(carry_on, outcome)

    def _new_comments(
        self,
        provider: TicketProvider,
        predicate: TicketPredicate,
        before: TicketSnapshot,
        after: TicketSnapshot,
    ) -> tuple[TicketComment, ...]:
        """The human comments among the newest ones, listed only when the count rose."""
        if before.comment_count is None or after.comment_count is None:
            return ()
        added = after.comment_count - before.comment_count
        if added <= 0:
            return ()
        newest = provider.list_comments(predicate.ticket_id)[-added:]
        own = self._own_login(provider)
        return tuple(comment for comment in newest if self._is_human(comment, own))

    @staticmethod
    def _is_human(comment: TicketComment, own_login: str | None) -> bool:
        """Not Grove's: no issue-ops marker, and not written by the token's own account."""
        if SIGNATURE_MARKER in comment.body or STICKY_MARKER in comment.body:
            return False
        return own_login is None or comment.author != own_login

    @staticmethod
    def _own_login(provider: TicketProvider) -> str | None:
        """The bot account, or ``None`` when the tracker cannot say (the markers still apply)."""
        try:
            return provider.viewer_login()
        except GroveError:
            return None


__all__ = ["ProviderLookup", "TicketChange", "TicketWatcher"]
