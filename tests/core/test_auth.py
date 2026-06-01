"""Unit tests for ``grove.core.auth.SessionStore``.

Pin the state-machine semantics (pair_init / approve / deny / poll / consume),
the persistence shape, sliding TTLs, GC, rate limits, and tamper resistance.
Side effects (clock, RNG, file path) are injected so the tests run hermetic.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from grove.core.auth import (
    TOKEN_PREFIX,
    ChallengeState,
    SessionStore,
)
from grove.core.errors import (
    AuthInvalidToken,
    AuthRateLimited,
    GroveError,
    PairingAlreadyResolved,
    PairingNotFound,
    SessionNotFound,
)


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "auth.json"


class _ManualClock:
    """Deterministic clock for the store. Tests advance it explicitly."""

    def __init__(self, *, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


def _fresh_store(
    path: Path,
    *,
    clock: _ManualClock | None = None,
    pair_init_per_minute: int = 5,
    pair_poll_per_minute: int = 60,
    session_ttl: timedelta = timedelta(days=30),
    pairing_ttl: timedelta = timedelta(minutes=5),
) -> tuple[SessionStore, _ManualClock]:
    clk = clock or _ManualClock()
    rng = secrets.SystemRandom()
    return (
        SessionStore(
            path=path,
            session_ttl=session_ttl,
            pairing_ttl=pairing_ttl,
            pair_init_per_minute=pair_init_per_minute,
            pair_poll_per_minute=pair_poll_per_minute,
            clock=clk,
            rng=rng,
        ),
        clk,
    )


# ─── pair_init ──────────────────────────────────────────────────────────────


def test_pair_init_creates_pending_challenge_with_uppercase_dashed_code(store_path: Path) -> None:
    store, _ = _fresh_store(store_path)
    challenge = store.pair_init(label="Krishna's iPhone")
    assert challenge.state == ChallengeState.PENDING
    assert challenge.label == "Krishna's iPhone"
    # Code is XXXX-XXXX from the safe alphabet.
    raw = challenge.code.replace("-", "")
    assert len(raw) == 8
    assert all(c in "BCDFGHJKMNPQRSTVWXYZ23456789" for c in raw)
    assert challenge.code[4] == "-"


def test_pair_init_persists_challenge_to_disk(store_path: Path) -> None:
    store, _ = _fresh_store(store_path)
    challenge = store.pair_init(label="phone")
    assert store_path.exists()
    # A fresh store loaded from the same path sees the challenge.
    other, _ = _fresh_store(store_path)
    pending = other.list_pending_challenges()
    assert [c.challenge_id for c in pending] == [challenge.challenge_id]


def test_pair_init_rejects_empty_or_too_long_label(store_path: Path) -> None:
    store, _ = _fresh_store(store_path)
    with pytest.raises(GroveError):
        store.pair_init(label="   ")
    with pytest.raises(GroveError):
        store.pair_init(label="x" * 200)


def test_pair_init_strips_label_whitespace(store_path: Path) -> None:
    store, _ = _fresh_store(store_path)
    challenge = store.pair_init(label="  laptop  ")
    assert challenge.label == "laptop"


def test_pair_init_rate_limits_per_source_address(store_path: Path) -> None:
    store, _ = _fresh_store(store_path, pair_init_per_minute=2)
    store.pair_init(label="a", requester_addr="10.0.0.1")
    store.pair_init(label="b", requester_addr="10.0.0.1")
    with pytest.raises(AuthRateLimited):
        store.pair_init(label="c", requester_addr="10.0.0.1")
    # Different source has its own bucket.
    store.pair_init(label="d", requester_addr="10.0.0.2")


def test_pair_init_local_caller_bypasses_rate_limit(store_path: Path) -> None:
    """In-process callers (CLI / tests) pass requester_addr=None — no limit."""
    store, _ = _fresh_store(store_path, pair_init_per_minute=1)
    for i in range(10):
        store.pair_init(label=f"local-{i}")  # no requester_addr


# ─── approve / deny ─────────────────────────────────────────────────────────


def test_pair_approve_transitions_pending_to_approved(store_path: Path) -> None:
    store, _ = _fresh_store(store_path)
    c = store.pair_init(label="phone")
    approved = store.pair_approve(c.challenge_id)
    assert approved.state == ChallengeState.APPROVED


def test_pair_approve_idempotent_on_already_approved(store_path: Path) -> None:
    store, _ = _fresh_store(store_path)
    c = store.pair_init(label="phone")
    store.pair_approve(c.challenge_id)
    again = store.pair_approve(c.challenge_id)
    assert again.state == ChallengeState.APPROVED


def test_pair_approve_rejects_terminal_states(store_path: Path) -> None:
    store, _ = _fresh_store(store_path)
    c = store.pair_init(label="phone")
    store.pair_deny(c.challenge_id)
    with pytest.raises(PairingAlreadyResolved):
        store.pair_approve(c.challenge_id)


def test_pair_deny_transitions_pending_to_denied(store_path: Path) -> None:
    store, _ = _fresh_store(store_path)
    c = store.pair_init(label="phone")
    denied = store.pair_deny(c.challenge_id)
    assert denied.state == ChallengeState.DENIED


def test_pair_approve_unknown_id_raises(store_path: Path) -> None:
    store, _ = _fresh_store(store_path)
    with pytest.raises(PairingNotFound):
        store.pair_approve(uuid4())


# ─── poll / consume ─────────────────────────────────────────────────────────


def test_pair_poll_pending_returns_no_token(store_path: Path) -> None:
    store, _ = _fresh_store(store_path)
    c = store.pair_init(label="phone")
    challenge, token = store.pair_poll(c.challenge_id)
    assert challenge.state == ChallengeState.PENDING
    assert token is None


def test_pair_poll_after_approve_mints_token_once(store_path: Path) -> None:
    store, _ = _fresh_store(store_path)
    c = store.pair_init(label="phone")
    store.pair_approve(c.challenge_id)
    challenge, token = store.pair_poll(c.challenge_id)
    assert challenge.state == ChallengeState.CONSUMED
    assert token is not None
    assert token.startswith(TOKEN_PREFIX)
    # Second poll returns no token — token is single-use.
    challenge2, token2 = store.pair_poll(c.challenge_id)
    assert challenge2.state == ChallengeState.CONSUMED
    assert token2 is None


def test_pair_poll_session_is_persisted_with_hash_only(store_path: Path) -> None:
    store, _ = _fresh_store(store_path)
    c = store.pair_init(label="phone")
    store.pair_approve(c.challenge_id)
    _, token = store.pair_poll(c.challenge_id)
    assert token is not None
    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0].label == "phone"
    # Persisted file contains only the hash, never the plaintext.
    raw = store_path.read_text()
    assert token not in raw
    # SHA-256 hex is 64 chars — sanity-check it's there.
    assert sessions[0].token_hash and len(sessions[0].token_hash) == 64


def test_pair_poll_denied_returns_denied_no_token(store_path: Path) -> None:
    store, _ = _fresh_store(store_path)
    c = store.pair_init(label="phone")
    store.pair_deny(c.challenge_id)
    challenge, token = store.pair_poll(c.challenge_id)
    assert challenge.state == ChallengeState.DENIED
    assert token is None


def test_pair_poll_expired_returns_expired_no_token(store_path: Path) -> None:
    store, clock = _fresh_store(store_path, pairing_ttl=timedelta(minutes=5))
    c = store.pair_init(label="phone")
    clock.advance(timedelta(minutes=10))
    challenge, token = store.pair_poll(c.challenge_id)
    assert challenge.state == ChallengeState.EXPIRED
    assert token is None


def test_pair_poll_rate_limits_per_source(store_path: Path) -> None:
    store, _ = _fresh_store(store_path, pair_poll_per_minute=2)
    c = store.pair_init(label="phone")
    store.pair_poll(c.challenge_id, requester_addr="1.1.1.1")
    store.pair_poll(c.challenge_id, requester_addr="1.1.1.1")
    with pytest.raises(AuthRateLimited):
        store.pair_poll(c.challenge_id, requester_addr="1.1.1.1")


# ─── validate / revoke ──────────────────────────────────────────────────────


def test_validate_returns_session_for_valid_token(store_path: Path) -> None:
    store, _ = _fresh_store(store_path)
    c = store.pair_init(label="phone")
    store.pair_approve(c.challenge_id)
    _, token = store.pair_poll(c.challenge_id)
    assert token is not None
    session = store.validate(token)
    assert session.label == "phone"


def test_validate_rejects_malformed_token(store_path: Path) -> None:
    store, _ = _fresh_store(store_path)
    with pytest.raises(AuthInvalidToken):
        store.validate("not-a-token")
    with pytest.raises(AuthInvalidToken):
        store.validate("")
    with pytest.raises(AuthInvalidToken):
        store.validate("grove_v0_oldformat")


def test_validate_rejects_unknown_token(store_path: Path) -> None:
    store, _ = _fresh_store(store_path)
    with pytest.raises(AuthInvalidToken):
        store.validate(f"{TOKEN_PREFIX}aaaaaaaa")


def test_validate_rejects_revoked_session(store_path: Path) -> None:
    store, _ = _fresh_store(store_path)
    c = store.pair_init(label="phone")
    store.pair_approve(c.challenge_id)
    _, token = store.pair_poll(c.challenge_id)
    assert token is not None
    sess = store.validate(token)
    store.revoke(sess.session_id)
    with pytest.raises(AuthInvalidToken):
        store.validate(token)


def test_validate_rejects_expired_session(store_path: Path) -> None:
    store, clock = _fresh_store(store_path, session_ttl=timedelta(hours=1))
    c = store.pair_init(label="phone")
    store.pair_approve(c.challenge_id)
    _, token = store.pair_poll(c.challenge_id)
    assert token is not None
    clock.advance(timedelta(hours=2))
    with pytest.raises(AuthInvalidToken):
        store.validate(token)


def test_validate_slides_session_expiry_on_each_call(store_path: Path) -> None:
    """Each ``validate()`` outside the throttle window pushes ``expires_at`` forward."""
    store, clock = _fresh_store(store_path, session_ttl=timedelta(hours=1))
    c = store.pair_init(label="phone")
    store.pair_approve(c.challenge_id)
    _, token = store.pair_poll(c.challenge_id)
    assert token is not None
    first_session = store.validate(token)
    initial_expiry = first_session.expires_at

    clock.advance(timedelta(minutes=2))  # past the 60-second write-throttle
    refreshed = store.validate(token)
    assert refreshed.expires_at > initial_expiry


def test_revoke_unknown_session_raises(store_path: Path) -> None:
    store, _ = _fresh_store(store_path)
    with pytest.raises(SessionNotFound):
        store.revoke(uuid4())


# ─── persistence + GC ───────────────────────────────────────────────────────


def test_store_persists_sessions_across_instances(store_path: Path) -> None:
    a, _ = _fresh_store(store_path)
    c = a.pair_init(label="phone")
    a.pair_approve(c.challenge_id)
    _, token = a.pair_poll(c.challenge_id)
    assert token is not None

    b, _ = _fresh_store(store_path)
    sess = b.validate(token)
    assert sess.label == "phone"


def test_gc_drops_stale_terminal_challenges(store_path: Path) -> None:
    store, clock = _fresh_store(store_path, pairing_ttl=timedelta(minutes=5))
    c = store.pair_init(label="phone")
    store.pair_deny(c.challenge_id)
    clock.advance(timedelta(minutes=15))
    # A read after the GC window cleans it up.
    assert store.list_pending_challenges() == []
    # Subsequent get_challenge raises (record purged).
    with pytest.raises(PairingNotFound):
        store.get_challenge(c.challenge_id)


def test_pending_challenge_promotes_to_expired_after_ttl(store_path: Path) -> None:
    store, clock = _fresh_store(store_path, pairing_ttl=timedelta(minutes=5))
    c = store.pair_init(label="phone")
    clock.advance(timedelta(minutes=6))
    # Pending challenge past TTL is reported as EXPIRED, not PENDING.
    fetched = store.get_challenge(c.challenge_id)
    assert fetched.state == ChallengeState.EXPIRED


def test_disk_file_does_not_contain_plaintext_token(store_path: Path) -> None:
    """The strongest tamper invariant — what's on disk is never a credential."""
    store, _ = _fresh_store(store_path)
    c = store.pair_init(label="phone")
    store.pair_approve(c.challenge_id)
    _, token = store.pair_poll(c.challenge_id)
    assert token is not None
    contents = store_path.read_text()
    assert token not in contents
    assert TOKEN_PREFIX not in contents
