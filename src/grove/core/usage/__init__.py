"""grove.core.usage — the historical usage audit (index, query, quota).

Deliberately empty of re-exports. Each concern under this package is imported
from its own module (``grove.core.usage.quota`` and friends), so adding one
never widens a shared surface that every consumer then depends on.

``UsageService`` is the one exception, kept reachable as ``grove.core.usage.
UsageService`` for its existing callers — but as a lazy `__getattr__` (PEP
562) rather than a plain import, because eagerly importing it here pulls in
``service`` -> ``insights`` -> ``_command`` -> ``bashlex`` (~90ms) on package
init, which fires for anyone importing ANY submodule of this package,
including ``grove.core.usage.projector`` from ``session_duration.py`` — a
caller that never touches ``UsageService`` at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from grove.core.usage.service import UsageService

__all__ = ["UsageService"]


def __getattr__(name: str) -> object:
    if name == "UsageService":
        from grove.core.usage.service import UsageService  # noqa: PLC0415

        return UsageService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
