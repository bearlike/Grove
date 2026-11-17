"""Wire-shape Pydantic models for the auth domain.

These mirror the engine dataclasses in ``grove.core.auth`` for any payload
that crosses the daemon's HTTP boundary. Engine continues to use the
underlying dataclasses internally; views exist purely to validate +
JSON-Schema the wire shape, per the CLAUDE.md boundary rule.

No view ever exposes a hash or a plaintext token. ``PairResultView`` is
the one place a token can appear, and only on the single ``approved``
poll that consumes the challenge — every other state returns
``token=None``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from grove.core.auth import LOCAL_SUBJECT, ChallengeState, PairingChallenge, Session

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class PairingChallengeView(BaseModel):
    """Read-only view of a `PairingChallenge` for HTTP responses + the TUI."""

    model_config = _FROZEN

    challenge_id: UUID
    label: str
    code: str
    created_at: datetime
    expires_at: datetime
    state: ChallengeState

    @classmethod
    def from_engine(cls, c: PairingChallenge) -> PairingChallengeView:
        return cls(
            challenge_id=c.challenge_id,
            label=c.label,
            code=c.code,
            created_at=c.created_at,
            expires_at=c.expires_at,
            state=c.state,
        )


class PairResultView(BaseModel):
    """Polling response — carries the bearer token only on the consume step."""

    model_config = _FROZEN

    challenge_id: UUID
    state: ChallengeState
    token: str | None = None
    expires_at: datetime | None = None
    """Session expiry — set alongside ``token`` on consume so callers can plan refresh."""

    @classmethod
    def pending(cls, c: PairingChallenge) -> PairResultView:
        return cls(challenge_id=c.challenge_id, state=c.state, token=None, expires_at=None)

    @classmethod
    def consumed(cls, c: PairingChallenge, *, token: str, session: Session) -> PairResultView:
        return cls(
            challenge_id=c.challenge_id,
            state=c.state,
            token=token,
            expires_at=session.expires_at,
        )


class SessionView(BaseModel):
    """Read-only summary of a session — never exposes ``token_hash``."""

    model_config = _FROZEN

    session_id: UUID
    label: str
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime
    revoked: bool = False
    # Who the session stands for: the person an OIDC sign-in named, else
    # ``local``. A client compares it with ``WorkspaceView.created_by`` to tell
    # its own work from everyone else's.
    subject: str = LOCAL_SUBJECT

    @classmethod
    def from_engine(cls, s: Session) -> SessionView:
        return cls(
            session_id=s.session_id,
            label=s.label,
            created_at=s.created_at,
            expires_at=s.expires_at,
            last_seen_at=s.last_seen_at,
            revoked=s.revoked_at is not None,
            subject=s.subject or LOCAL_SUBJECT,
        )

    @classmethod
    def of_principal(cls, *, subject: str, token_id: str, expires_at: datetime) -> SessionView:
        """A caller that presented an ID token directly holds no Grove session; describe it."""
        return cls(
            session_id=UUID(int=0),
            label=f"oidc:{token_id}" if token_id else "oidc",
            created_at=expires_at,
            expires_at=expires_at,
            last_seen_at=expires_at,
            subject=subject,
        )


class OidcExchangeRequest(BaseModel):
    """``POST /auth/oidc/session`` body: an ID token to trade for a Grove bearer."""

    model_config = ConfigDict(extra="forbid")

    id_token: str = Field(min_length=1, max_length=16_384)
    label: str = Field(default="oidc", min_length=1, max_length=120)


class SignInView(BaseModel):
    """A bearer minted for a person by ``POST /auth/oidc/session``."""

    model_config = _FROZEN

    token: str
    expires_at: datetime
    subject: str


class PairRequest(BaseModel):
    """``POST /auth/pair`` body."""

    model_config = ConfigDict(extra="forbid")

    label: str


class AuthErrorEnvelope(BaseModel):
    """Daemon-side error envelope — pinned here so the OpenAPI generator
    documents the shape clients see for every 401 / 404 / 410 / 429."""

    model_config = _FROZEN

    error: Literal[
        "auth_missing",
        "auth_invalid",
        "pair_not_found",
        "pair_expired",
        "pair_already_resolved",
        "rate_limited",
        "session_not_found",
        "auth_mode_conflict",
        "oidc_not_configured",
    ]
    message: str
