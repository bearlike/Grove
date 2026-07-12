"""grove.core.issueops — turn issue-comment events into workspace actions (#192).

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
"""

from __future__ import annotations

from grove.core.issueops.engine import IssueOpsEngine, StatusPublisher
from grove.core.issueops.marker import SIGNATURE_MARKER, STICKY_MARKER
from grove.core.issueops.parser import CommandParser, ParsedCommand
from grove.core.issueops.publisher import (
    ProviderResolver,
    PublishSnapshot,
    TicketStatusPublisher,
    TodoResolver,
)

__all__ = [
    "SIGNATURE_MARKER",
    "STICKY_MARKER",
    "CommandParser",
    "IssueOpsEngine",
    "ParsedCommand",
    "ProviderResolver",
    "PublishSnapshot",
    "StatusPublisher",
    "TicketStatusPublisher",
    "TodoResolver",
]
