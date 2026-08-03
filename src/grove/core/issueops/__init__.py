"""grove.core.issueops — turn issue-comment events into workspace actions.

One nameable concern with two faces: a forwarded, normalized issue-comment event
in, a workspace action out (:class:`IssueOpsEngine` over the pure
:class:`CommandParser`), and the workspace's progress mirrored back onto the
ticket (:class:`TicketStatusPublisher`, the live sticky status comment). The wire
shapes (``IssueOpsEvent`` / ``IssueOpsOutcome``) live in
``grove.core.contracts.issueops`` — they cross the CI-action boundary; the
``IssueOpsConfig`` submodel cascades with the rest of config in
``grove.core.config`` (it is NOT re-exported here — it lives with its config
siblings, like every other submodel).

The two markers in ``grove.core.issueops.marker`` are the constants shared by
both faces (imported from there by each, so neither depends on the other):
``SIGNATURE_MARKER`` rides every Grove comment (reply + sticky) and the engine
drops any inbound comment carrying it — the bot never answers its own comment;
``STICKY_MARKER`` rides ONLY the sticky status comment so the publisher's
cold-start recovery re-adopts *that* comment, never an old reply.

The package grew a THIRD face with the assignee work queue, and it belongs here
rather than in a package of its own because it answers the same question from a
different direction: a ticket becomes a workspace. ``PickupEngine`` is the one
path both the daemon's ``AssigneePoller`` and the ``grove tickets`` verbs take,
``HandoverLog`` is the durable marker that keeps the outbound assignment from
re-triggering the inbound poll, and ``IssuePrompt`` renders the single
``prompt_template`` both create paths share.
"""

from __future__ import annotations

from grove.core.issueops.engine import IssueOpsEngine, StatusPublisher
from grove.core.issueops.handover import HandoverEntry, HandoverKey, HandoverLog
from grove.core.issueops.marker import SIGNATURE_MARKER, STICKY_MARKER
from grove.core.issueops.parser import CommandParser, ParsedCommand
from grove.core.issueops.pickup import PickupCandidate, PickupEngine, PickupPlan
from grove.core.issueops.poller import AssigneePoller
from grove.core.issueops.prompt import IssuePrompt
from grove.core.issueops.publisher import (
    ProviderResolver,
    PublishSnapshot,
    TicketStatusPublisher,
    TodoResolver,
)

__all__ = [
    "SIGNATURE_MARKER",
    "STICKY_MARKER",
    "AssigneePoller",
    "CommandParser",
    "HandoverEntry",
    "HandoverKey",
    "HandoverLog",
    "IssueOpsEngine",
    "IssuePrompt",
    "ParsedCommand",
    "PickupCandidate",
    "PickupEngine",
    "PickupPlan",
    "ProviderResolver",
    "PublishSnapshot",
    "StatusPublisher",
    "TicketStatusPublisher",
    "TodoResolver",
]
