"""One module per resource class; each finds Grove-owned resources and removes one."""

from __future__ import annotations

from datetime import timedelta
from typing import ClassVar, Protocol

from grove.core.reaper.ledger import ResourceClass, ResourceRef


class Collector(Protocol):
    """Lists every resource of one class that Grove could own, and removes one.

    ``found`` reports what EXISTS; deciding what is entitled is the ledger's
    job, so a collector never reads a workspace record.
    """

    cls: ClassVar[ResourceClass]
    #: How long a candidate must keep being seen before it may be removed. A
    #: constant, not config: a wrong grace is a bug to fix here.
    GRACE: ClassVar[timedelta]

    def found(self) -> list[ResourceRef]:
        """Every Grove-shaped resource of this class on the host. Never raises."""
        ...

    def remove(self, ref: ResourceRef) -> None:
        """Remove one resource.

        Raises:
            GroveError: The removal did not happen; the reaper records it and
                tries again on a later pass.
        """
        ...


__all__ = ["Collector"]
