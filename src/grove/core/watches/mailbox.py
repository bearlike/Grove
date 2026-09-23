"""Delivering a callback is sending ordinary mail to the agent that asked.

This is the whole integration, and its smallness is the point. The mailbox
already resolves an address to a live agent and reaches every session kind Grove
supports — a native Claude worker, a Codex app-server, OpenCode, an interactive
tmux pane, a named agent inside a container — through the one ``can_receive``
predicate steering itself enforces. A watch callback needs none of that again.

**The message is addressed FROM the recipient.** An agent waiting on its own CI
is both ends of the conversation, and a self-addressed envelope reads correctly
in a transcript: *I asked to be told this, and here it is.* It also means a
reply goes somewhere sensible rather than to a synthetic sender that no
``MailboxAddress`` could name.

**The body must stand alone.** A callback commonly lands on an IDLE session,
which starts a fresh turn with no memory of the registration — so it carries
what settled and what to do about it, never "your watch fired".
"""

from __future__ import annotations

from grove.core.contracts.mailboxes import MailboxSendRequest
from grove.core.contracts.watches import WatchOutcome, WatchView
from grove.core.mailboxes import MailboxDelivery


class MailboxWatchCourier:
    """Turns a settled watch into one mailbox message.

    Holds :class:`MailboxDelivery` rather than subclassing or wrapping it: this
    adds no transport, makes no routing decision and enforces no liveness rule
    of its own — it renders a body and hands it over.
    """

    def __init__(self, delivery: MailboxDelivery) -> None:
        self._delivery = delivery

    def __call__(self, watch: WatchView, outcome: WatchOutcome) -> tuple[str, str | None]:
        """Send the callback, returning the transport's own verdict.

        The return is ``(receipt, detail)`` exactly as the mailbox reported it,
        because the scheduler's durable row must record what Grove actually
        observed and nothing more.
        """
        receipt = self._delivery.send(
            MailboxSendRequest(
                sender=watch.recipient,
                recipient=watch.recipient,
                subject=self._subject(watch, outcome),
                body=self._body(watch, outcome),
            )
        )
        return receipt.stage, receipt.detail

    @staticmethod
    def _subject(watch: WatchView, outcome: WatchOutcome) -> str:
        """Three outcomes a reader acts on differently, told apart at a glance.

        An expiry is keyed on the watch STATE, not on ``outcome.ok``: a failed
        CI run is also not ok, and "your build is red" and "we gave up waiting"
        ask for opposite next steps.
        """
        if watch.state == "expired":
            return f"Watch deadline reached, condition not met: {watch.predicate.kind}"
        if watch.predicate.kind == "ticket":
            # The summary's first line names the ticket ("Issue #42 changed:").
            return outcome.summary.splitlines()[0].rstrip(":")
        mark = "settled" if outcome.ok else "settled with a failure"
        return f"Watch {mark}: {watch.predicate.kind}"

    @staticmethod
    def _body(watch: WatchView, outcome: WatchOutcome) -> str:
        lines = [outcome.summary]
        if outcome.url:
            lines.append(f"\nDetails: {outcome.url}")
        # The expiry summary already names the note, since it is the only thing
        # telling a fresh turn which of its watches gave up; repeating it here
        # read as two different notes.
        if watch.predicate.kind == "ticket":
            # Nobody registered this, and nobody is waiting on it: say what it
            # is, and that it carries on, so a fresh turn neither polls nor
            # mistakes it for a result it asked for.
            lines.append(
                "\nGrove sends this because the ticket is attached to your workspace. "
                "It keeps watching the ticket for as long as the workspace is running."
            )
            return "\n".join(lines)
        if watch.note and watch.state != "expired":
            lines.append(f"\nYou registered this watch with the note: {watch.note}")
        if watch.state == "expired":
            lines.append(
                "\nThis is the callback for a watch you registered. Its deadline "
                "passed before the condition was met, so Grove stopped waiting and "
                "is handing your turn back. Nothing is still being checked."
            )
        else:
            lines.append(
                "\nThis is the callback for a watch you registered, delivered because "
                "the thing you were waiting for reached a final state. You do not need "
                "to poll for it."
            )
        return "\n".join(lines)


__all__ = ["MailboxWatchCourier"]
