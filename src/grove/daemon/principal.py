"""Who is calling the daemon: one value every authenticated route can read."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from grove.core.auth import LOCAL_SUBJECT, Session

#: What kind of caller a principal is: a person signed in through OIDC, or this
#: host's own user, reached by pairing or a same-UID mint.
PrincipalKind = Literal["human", "local"]


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is calling, as one value every route can read off ``request.state``.

    Grove has no roles: every signed-in person sees every session, and the
    dashboard narrows to "mine" with ``subject``. ``token_id`` names the bearer
    or ID token so a log line can be tied to one sign-in without the token.
    """

    subject: str
    issuer: str
    kind: PrincipalKind
    token_id: str
    expires_at: datetime

    @classmethod
    def of_session(cls, session: Session) -> Principal:
        """The principal a Grove session bearer stands for."""
        return cls(
            subject=session.subject or LOCAL_SUBJECT,
            issuer=session.issuer,
            kind="human" if session.subject else "local",
            token_id=str(session.session_id),
            expires_at=session.expires_at,
        )


__all__ = ["Principal", "PrincipalKind"]
