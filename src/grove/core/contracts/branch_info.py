"""BranchInfo — public response shape for branch-read operations.

Returned by ``WorkspaceManager.list_local_branches()`` /
``list_remote_branches()`` (and the underlying ``GitRepo`` methods).
The TUI consumes this to populate dropdowns; a future API server will
return the same shape as JSON. Pydantic frozen + ``extra='forbid'`` —
clients that drift on field names break loudly at the boundary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict


class BranchInfo(BaseModel):
    """One branch entry as seen by the engine.

    A ``BranchInfo`` is a snapshot — it is correct only for the moment it
    was read. Callers that show it in a UI should re-read after every
    workspace lifecycle event so stale entries (a branch checked out
    elsewhere a moment ago, now free) don't mislead the user.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    """The branch identifier as the user types it: ``feature/x`` for local,
    ``origin/feature/x`` for remote."""

    kind: Literal["local", "remote"]
    """Where this branch lives. ``remote`` entries leave ``is_current``,
    ``upstream``, and ``checked_out_in`` at their defaults."""

    is_current: bool = False
    """True iff this is the local branch the repo's HEAD points to (the
    one a fresh ``git checkout`` would land on). Local-only — remote
    entries are always False."""

    upstream: str | None = None
    """Local-only: the upstream tracking ref, e.g. ``origin/feature/x``.
    ``None`` when no upstream is configured."""

    checked_out_in: Path | None = None
    """Path of the worktree where this branch is currently checked out,
    or ``None`` if it isn't checked out anywhere. Drives the ``Existing``
    branch dropdown's grayed-out rows and the ``BranchAlreadyCheckedOut``
    error in ``WorkspaceManager.create()``."""


__all__ = ["BranchInfo"]
