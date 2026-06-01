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

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.status import (
    HTTP_401_UNAUTHORIZED,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_429_TOO_MANY_REQUESTS,
)

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


def _http_for(exc: GroveError) -> HTTPException:
    """Map auth/pair engine errors to HTTP. First match wins."""
    if isinstance(exc, AuthInvalidToken):
        return HTTPException(HTTP_401_UNAUTHORIZED, _envelope("auth_invalid", str(exc)))
    if isinstance(exc, PairingNotFound):
        return HTTPException(HTTP_404_NOT_FOUND, _envelope("pair_not_found", str(exc)))
    if isinstance(exc, PairingAlreadyResolved):
        return HTTPException(HTTP_409_CONFLICT, _envelope("pair_already_resolved", str(exc)))
    if isinstance(exc, AuthRateLimited):
        return HTTPException(HTTP_429_TOO_MANY_REQUESTS, _envelope("rate_limited", str(exc)))
    if isinstance(exc, SessionNotFound):
        return HTTPException(HTTP_404_NOT_FOUND, _envelope("session_not_found", str(exc)))
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
        except AuthRateLimited as exc:
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
        except (PairingNotFound, AuthRateLimited) as exc:
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
    "make_require_session",
]
