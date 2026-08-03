"""Authentication router + ``require_session`` dependency for the daemon.

Every existing daemon endpoint is gated through ``require_session``; the
two pairing endpoints (``POST /auth/pair`` + ``GET /auth/pair/{id}``) are the
only unauthenticated entry points and form the bootstrap path. Approval is
NOT exposed over HTTP — it lives inside the engine, surfaced by the TUI
modal and the ``grove auth approve`` CLI (in-process, no HTTP), so a remote
caller cannot self-approve.

Error envelope matches the rest of the daemon: ``{"detail": {"error":
<code>, "message": <text>}}``. Codes pinned in
``grove.core.contracts.AuthErrorEnvelope``.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.status import (
    HTTP_401_UNAUTHORIZED,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_429_TOO_MANY_REQUESTS,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from grove.core.agents.hook import ClaudeHook
from grove.core.auth import Session, SessionStore
from grove.core.contracts.auth import (
    PairingChallengeView,
    PairRequest,
    PairResultView,
    SessionView,
)
from grove.core.errors import (
    AuthInvalidToken,
    AuthRateLimited,
    AuthStoreUnreadable,
    GroveError,
    PairingAlreadyResolved,
    PairingNotFound,
    SessionNotFound,
)


def _envelope(code: str, message: str) -> dict[str, str]:
    """Daemon-wide error envelope shape — kept here so the auth router and
    the main app share one helper rather than two near-duplicates. FastAPI
    wraps this under the ``detail`` key automatically."""
    return {"error": code, "message": message}


#: Auth-domain error → (status, wire code). A linear ``isinstance`` scan like
#: the engine map in ``app.py``, so the same rule applies: a subclass entry MUST
#: precede its parent. ``AuthStoreUnreadable`` is 503 and never a 4xx — a
#: server-side storage fault the caller did nothing to cause and no re-request
#: fixes; it has to be listed rather than left to fall through, because the
#: pair routes read a bare ``GroveError`` as bad caller input.
_AUTH_ERROR_HTTP: tuple[tuple[type[GroveError], int, str], ...] = (
    (AuthInvalidToken, HTTP_401_UNAUTHORIZED, "auth_invalid"),
    (PairingNotFound, HTTP_404_NOT_FOUND, "pair_not_found"),
    (PairingAlreadyResolved, HTTP_409_CONFLICT, "pair_already_resolved"),
    (AuthRateLimited, HTTP_429_TOO_MANY_REQUESTS, "rate_limited"),
    (SessionNotFound, HTTP_404_NOT_FOUND, "session_not_found"),
    (AuthStoreUnreadable, HTTP_503_SERVICE_UNAVAILABLE, "auth_store_unavailable"),
)


def _http_for(exc: GroveError) -> HTTPException:
    """Map auth/pair engine errors to HTTP. First match wins."""
    for cls, status, code in _AUTH_ERROR_HTTP:
        if isinstance(exc, cls):
            return HTTPException(status, _envelope(code, str(exc)))
    return HTTPException(500, _envelope("grove_error", str(exc)))


# ─── dependency ─────────────────────────────────────────────────────────────


def make_require_session(
    *,
    auth_store: SessionStore,
    enabled: bool,
) -> Callable[[Request], Awaitable[Session]]:
    """Build the ``require_session`` dependency closed over a store.

    Returns a callable suitable for ``Depends(...)``. We use a factory
    rather than a module-level coroutine so each app instance can pin its
    own store + enabled flag (tests benefit; the closure stays
    deterministic).

    When ``enabled=False`` the dep short-circuits to a synthetic Session;
    only test scaffolding ever flips this off.
    """

    async def require_session(request: Request) -> Session:
        if not enabled:
            # Test-only path — return a sentinel session so handlers that
            # access ``request.state.session`` still work.
            return _SENTINEL_SESSION
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise HTTPException(
                HTTP_401_UNAUTHORIZED,
                _envelope("auth_missing", "missing or malformed Authorization header"),
            )
        token = header[len("Bearer ") :].strip()
        try:
            return auth_store.validate(token)
        except AuthInvalidToken as exc:
            raise HTTPException(
                HTTP_401_UNAUTHORIZED,
                _envelope("auth_invalid", str(exc)),
            ) from exc

    return require_session


def make_require_hook_token(*, enabled: bool) -> Callable[[Request], Awaitable[None]]:
    """Build the hook-ingest route's auth dependency.

    A same-host shared secret (:meth:`ClaudeHook.ensure_ingest_token`), never
    the `SessionStore` pairing bearer `require_session` checks — pairing needs
    a human to approve a challenge, and the native Claude Code http hook fires
    on every tracked event with nobody watching. ``enabled=False`` mirrors
    `make_require_session`'s test-only escape hatch (`cfg.auth.enabled`), so
    the same config flag gates both auth mechanisms together.
    """

    async def require_hook_token(request: Request) -> None:
        if not enabled:
            return
        header = request.headers.get("authorization", "")
        token = header[len("Bearer ") :].strip() if header.lower().startswith("bearer ") else ""
        if not token or not secrets.compare_digest(token, ClaudeHook.ensure_ingest_token()):
            raise HTTPException(
                HTTP_401_UNAUTHORIZED,
                _envelope("auth_invalid", "missing or invalid hook token"),
            )

    return require_hook_token


_SENTINEL_SESSION = Session(
    session_id=UUID("00000000-0000-0000-0000-000000000000"),
    label="<auth-disabled>",
    token_hash="",
    created_at=datetime.fromtimestamp(0, tz=UTC),
    expires_at=datetime.fromtimestamp(2**31 - 1, tz=UTC),
    last_seen_at=datetime.fromtimestamp(0, tz=UTC),
    revoked_at=None,
)


# ─── router ─────────────────────────────────────────────────────────────────


def build_auth_router(
    *,
    auth_store: SessionStore,
    require_session: Callable[[Request], Awaitable[Session]],
) -> APIRouter:
    """Auth + pairing endpoints. Pair-init/poll are unauthenticated by design."""

    router = APIRouter(prefix="/auth", tags=["auth"])

    @router.post("/pair", response_model=PairingChallengeView)
    async def pair_init(req: PairRequest, request: Request) -> PairingChallengeView:
        try:
            challenge = auth_store.pair_init(
                label=req.label,
                requester_addr=request.client.host if request.client else None,
            )
        except (AuthRateLimited, AuthStoreUnreadable) as exc:
            # AuthStoreUnreadable MUST be caught before the bare `GroveError`
            # below, which exists only for the label rules: otherwise a corrupt
            # or unreadable `auth.json` would be reported as `invalid_label`,
            # telling a user with a perfectly good label that their input was
            # wrong — and handing an unauthenticated caller the absolute path
            # of the file while doing it.
            raise _http_for(exc) from exc
        except GroveError as exc:
            raise HTTPException(
                422,
                _envelope("invalid_label", str(exc)),
            ) from exc
        return PairingChallengeView.from_engine(challenge)

    @router.get("/pair/{challenge_id}", response_model=PairResultView)
    async def pair_poll(challenge_id: UUID, request: Request) -> PairResultView:
        try:
            challenge, token = auth_store.pair_poll(
                challenge_id,
                requester_addr=request.client.host if request.client else None,
            )
        except (PairingNotFound, AuthRateLimited, AuthStoreUnreadable) as exc:
            raise _http_for(exc) from exc
        if token is not None:
            # Find the freshly-minted session so we can attach its expiry.
            sessions = sorted(
                auth_store.list_sessions(include_revoked=False),
                key=lambda s: s.created_at,
            )
            session = sessions[-1]
            return PairResultView.consumed(challenge, token=token, session=session)
        return PairResultView.pending(challenge)

    @router.post("/pair/{challenge_id}/deny", status_code=204)
    async def pair_deny(
        challenge_id: UUID,
        _session: Session = Depends(require_session),  # noqa: B008
    ) -> None:
        try:
            auth_store.pair_deny(challenge_id)
        except (PairingNotFound, PairingAlreadyResolved) as exc:
            raise _http_for(exc) from exc

    @router.get("/sessions", response_model=list[SessionView])
    async def list_sessions(
        _session: Session = Depends(require_session),  # noqa: B008
    ) -> list[SessionView]:
        return [SessionView.from_engine(s) for s in auth_store.list_sessions()]

    @router.get("/sessions/me", response_model=SessionView)
    async def session_me(
        session: Session = Depends(require_session),  # noqa: B008
    ) -> SessionView:
        return SessionView.from_engine(session)

    @router.delete("/sessions/{session_id}", status_code=204)
    async def revoke(
        session_id: UUID,
        _session: Session = Depends(require_session),  # noqa: B008
    ) -> None:
        try:
            auth_store.revoke(session_id)
        except SessionNotFound as exc:
            raise _http_for(exc) from exc

    @router.get("/pending", response_model=list[PairingChallengeView])
    async def list_pending(
        _session: Session = Depends(require_session),  # noqa: B008
    ) -> list[PairingChallengeView]:
        """Surface in-progress pairings to authorized clients (the webapp
        'pending devices' panel). Bare list — pending and approved
        challenges only; terminal records GC themselves."""
        return [PairingChallengeView.from_engine(c) for c in auth_store.list_pending_challenges()]

    return router


__all__ = [
    "build_auth_router",
    "make_require_hook_token",
    "make_require_session",
]
