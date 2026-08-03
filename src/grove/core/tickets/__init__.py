"""grove.core.tickets — the branch-aware ticket-provider layer.

One ``TicketProvider`` interface, implemented once per tracker (Gitea, GitHub,
Linear) and reused by every client through the ``TicketProviderRegistry``. The
engine calls only the pure branch parse/format; the daemon's ``/tickets`` routes
call the network methods. Provider-specific parsing and API shapes stay isolated
inside each concrete class — an adapter normalizes shape, never semantics.

The public surface is the registry plus the Protocol; the concrete provider
classes are internal (the registry constructs them from config). Wire shapes
(``TicketRef`` / ``TicketSelector`` / ``TicketProviderView``) live in
``grove.core.contracts.tickets``, not here — they cross the client boundary.
"""

from __future__ import annotations

from grove.core.tickets.link import TicketLink
from grove.core.tickets.provider import (
    HttpTicketProvider,
    NumberTicketProvider,
    TicketProvider,
)
from grove.core.tickets.registry import TicketProviderRegistry

__all__ = [
    "HttpTicketProvider",
    "NumberTicketProvider",
    "TicketLink",
    "TicketProvider",
    "TicketProviderRegistry",
]
