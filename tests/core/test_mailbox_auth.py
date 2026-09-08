"""Mailbox-scoped bearer sessions stay finite, private, and backward-compatible."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grove.core.auth import SessionStore
from grove.core.contracts.auth import SessionView
from grove.core.contracts.mailboxes import MailboxAddress, MailboxIdentity
from grove.core.errors import AuthInvalidToken


def test_auth_imports_without_contract_initialization() -> None:
    subprocess.run([sys.executable, "-c", "from grove.core.auth import SessionStore"], check=True)


class _ManualClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 9, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


def _identity(generation: str = "b" * 32) -> MailboxIdentity:
    return MailboxIdentity(
        address=MailboxAddress(workspace_id="a" * 32, agent="claude"), generation=generation
    )


def test_legacy_session_loads_and_stays_unscoped(tmp_path: Path) -> None:
    now = datetime(2026, 9, 12, tzinfo=UTC)
    path = tmp_path / "auth.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "challenges": [],
                "sessions": [
                    {
                        "session_id": "00000000-0000-0000-0000-000000000001",
                        "label": "legacy",
                        "token_hash": "0" * 64,
                        "created_at": now.isoformat(),
                        "expires_at": (now + timedelta(hours=1)).isoformat(),
                        "last_seen_at": now.isoformat(),
                        "revoked_at": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    session = SessionStore(path=path, clock=lambda: now).list_sessions()[0]

    assert session.label == "legacy"
    assert session.mailbox_identity is None
    assert session.mailbox_registration is False


def test_issue_mailbox_session_persists_hash_and_identity_without_plaintext(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    store = SessionStore(path=path)

    token, session = store.issue_mailbox_session(_identity(), label="native mailbox")

    assert token.startswith("grove_v1_")
    assert store.validate(token).mailbox_identity == _identity()
    assert token not in path.read_text(encoding="utf-8")
    assert session.mailbox_identity == _identity()
    assert session.mailbox_registration is False
    assert "mailbox_identity" not in SessionView.from_engine(session).model_dump()
    assert "mailbox_registration" not in SessionView.from_engine(session).model_dump()


def test_mailbox_registration_and_slot_revocation(tmp_path: Path) -> None:
    store = SessionStore(path=tmp_path / "auth.json")
    token, session = store.issue_mailbox_session(
        _identity(), label="registration", registration=True
    )

    assert session.mailbox_registration is True
    assert store.validate(token).mailbox_registration is True
    store.revoke_mailbox_sessions(_identity().address)
    store.revoke_mailbox_sessions(_identity().address)
    with pytest.raises(AuthInvalidToken):
        store.validate(token)


def test_malformed_mailbox_token_is_invalid(tmp_path: Path) -> None:
    with pytest.raises(AuthInvalidToken):
        SessionStore(path=tmp_path / "auth.json").validate("grove_v1_not-a-real-token")


def test_mailbox_session_has_fixed_expiry(tmp_path: Path) -> None:
    clock = _ManualClock()
    store = SessionStore(
        path=tmp_path / "auth.json",
        clock=clock,
        mailbox_session_ttl=timedelta(minutes=5),
    )
    token, initial = store.issue_mailbox_session(_identity(), label="native mailbox")

    clock.advance(timedelta(minutes=2))
    refreshed = store.validate(token)

    assert refreshed.expires_at == initial.expires_at
    assert refreshed.last_seen_at == initial.last_seen_at
    clock.advance(timedelta(minutes=4))
    with pytest.raises(AuthInvalidToken):
        store.validate(token)


def test_registration_session_survives_expiry_across_fresh_store(tmp_path: Path) -> None:
    """A worker reconnects after a daemon restart with its original credential.

    Its peer token remains finite: keeping discovery/send capabilities beyond a
    worker's lifetime would make a copied launch config a durable peer identity.
    """
    clock = _ManualClock()
    path = tmp_path / "auth.json"
    store = SessionStore(path=path, clock=clock)
    peer_token, _ = store.issue_mailbox_session(_identity(), label="peer")
    registration_token, _ = store.issue_mailbox_session(
        _identity(), label="registration", registration=True
    )

    clock.advance(timedelta(hours=13))
    restarted = SessionStore(path=path, clock=clock)

    assert restarted.validate(registration_token).mailbox_registration is True
    with pytest.raises(AuthInvalidToken):
        restarted.validate(peer_token)
    restarted.revoke_mailbox_sessions(_identity().address)
    with pytest.raises(AuthInvalidToken):
        restarted.validate(registration_token)


def test_replacement_registration_revokes_stale_generation(tmp_path: Path) -> None:
    """Minting a new native incarnation fences its predecessor at the auth seam."""
    clock = _ManualClock()
    store = SessionStore(path=tmp_path / "auth.json", clock=clock)
    stale_token, _ = store.issue_mailbox_session(
        _identity(generation="c" * 32), label="stale registration", registration=True
    )

    store.revoke_mailbox_sessions(_identity().address)
    current_token, _ = store.issue_mailbox_session(
        _identity(), label="current registration", registration=True
    )

    with pytest.raises(AuthInvalidToken):
        store.validate(stale_token)
    assert store.validate(current_token).mailbox_identity == _identity()
