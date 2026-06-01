"""Exception hierarchy for grove.core. Clients catch GroveError; subclasses signal kind."""

from __future__ import annotations

from pathlib import Path


class GroveError(Exception):
    """Base for every error raised by grove.core. Clients catch this."""


class ConfigError(GroveError):
    """Configuration could not be loaded, parsed, or validated."""


class GitError(GroveError):
    """A git subprocess failed or produced unexpected output."""


class TmuxError(GroveError):
    """A tmux operation failed; covers both libtmux and `tmux` subprocess failures."""


class WorkspaceNotFound(GroveError):
    """Lookup by id did not match any persisted workspace."""


class WorkspaceStateError(GroveError):
    """A lifecycle transition was requested from an incompatible status."""


# ─── branch validation errors (raised by WorkspaceManager.create) ───────────


class BranchError(GroveError):
    """A `BranchPlan` could not be reconciled with live git state.

    Subclasses pinpoint the specific failure so clients (TUI flash today,
    HTTP 422 in a future API server) can render a tailored message.
    Always raised *before* any worktree side effect, so create-failure
    cleanup is unnecessary on this path.
    """


class BranchNotFound(BranchError):
    """A branch (local or remote) referenced by the plan does not exist."""


class BranchConflict(BranchError):
    """A new branch name in the plan collides with an existing branch."""


# ─── auth / pairing errors (raised by SessionStore + daemon dep) ───────────


class AuthError(GroveError):
    """Base for every authentication / pairing failure.

    Subclasses pinpoint the failure mode so the daemon can emit the right
    HTTP code + envelope. Always raised before any state-changing side
    effect — callers can treat the store as untouched on AuthError.
    """


class AuthInvalidToken(AuthError):
    """Bearer token missing, malformed, or not recognized by the store."""


class PairingNotFound(AuthError):
    """No challenge with that id (or it expired and was garbage-collected)."""


class PairingExpired(AuthError):
    """Challenge passed its TTL before approval / consumption."""


class PairingAlreadyResolved(AuthError):
    """Challenge already approved / denied / consumed; cannot transition again."""


class AuthRateLimited(AuthError):
    """Too many recent pair_init / pair_poll calls from the same source."""


class SessionNotFound(AuthError):
    """No session with that id — already revoked or never existed."""


class BranchAlreadyCheckedOut(BranchError):
    """The requested existing-local branch is already checked out at another worktree.

    Carries structured context (`name`, `worktree`) so clients can render
    a useful message ("checked out at /path/to/wt") and a future API
    response can include them in the JSON payload.
    """

    def __init__(self, name: str, worktree: Path) -> None:
        super().__init__(f"branch {name!r} is already checked out at {worktree}")
        self.name = name
        self.worktree = worktree
