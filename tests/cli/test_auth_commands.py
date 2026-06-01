"""``grove auth`` Typer subcommand surface.

Every CLI command talks to the engine in-process via ``SessionStore`` —
no HTTP. These tests exercise the full surface (pending / approve /
deny / sessions / revoke), pin the no-pending-pairings empty state, and
explicitly verify that ``approve`` does NOT print the token (the token
flows out only through the daemon's poll endpoint, never via stdout).
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from typer.testing import CliRunner

from grove.core.auth import SessionStore
from grove.tui.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def store(tmp_state_dir: Path) -> SessionStore:
    del tmp_state_dir  # path redirection ran above
    return SessionStore()


def test_pending_no_challenges_says_so(runner: CliRunner, store: SessionStore) -> None:
    del store
    result = runner.invoke(app, ["auth", "pending"])
    assert result.exit_code == 0
    assert "no pending pairings" in result.output


def test_pending_lists_challenges(runner: CliRunner, store: SessionStore) -> None:
    challenge = store.pair_init(label="phone")
    result = runner.invoke(app, ["auth", "pending"])
    assert result.exit_code == 0
    assert str(challenge.challenge_id) in result.output
    assert challenge.code in result.output
    assert "phone" in result.output


def test_approve_flips_state_without_printing_token(runner: CliRunner, store: SessionStore) -> None:
    challenge = store.pair_init(label="phone")
    result = runner.invoke(app, ["auth", "approve", str(challenge.challenge_id)])
    assert result.exit_code == 0
    assert "approved" in result.output
    # Token must NEVER appear in CLI output — the requesting client picks
    # it up via the daemon poll, not via the approver's terminal.
    assert "grove_v1_" not in result.output
    # And the engine state reflects the approval.
    fresh = SessionStore()
    pending = fresh.list_pending_challenges()
    assert any(
        p.challenge_id == challenge.challenge_id and p.state.value == "approved" for p in pending
    )


def test_deny_flips_state(runner: CliRunner, store: SessionStore) -> None:
    challenge = store.pair_init(label="phone")
    result = runner.invoke(app, ["auth", "deny", str(challenge.challenge_id)])
    assert result.exit_code == 0
    assert "denied" in result.output


def test_approve_unknown_id_returns_nonzero(runner: CliRunner, store: SessionStore) -> None:
    del store
    result = runner.invoke(app, ["auth", "approve", "00000000-0000-0000-0000-000000000000"])
    assert result.exit_code == 1


def test_approve_with_invalid_uuid_returns_nonzero(runner: CliRunner) -> None:
    result = runner.invoke(app, ["auth", "approve", "not-a-uuid"])
    assert result.exit_code == 1


def test_sessions_empty_says_so(runner: CliRunner, store: SessionStore) -> None:
    del store
    result = runner.invoke(app, ["auth", "sessions"])
    assert result.exit_code == 0
    assert "no active sessions" in result.output


def test_sessions_lists_paired_devices(runner: CliRunner, store: SessionStore) -> None:
    challenge = store.pair_init(label="phone")
    store.pair_approve(challenge.challenge_id)
    _, token = store.pair_poll(challenge.challenge_id)
    assert token is not None
    result = runner.invoke(app, ["auth", "sessions"])
    assert result.exit_code == 0
    assert "phone" in result.output


def test_revoke_drops_a_session(runner: CliRunner, store: SessionStore) -> None:
    challenge = store.pair_init(label="phone")
    store.pair_approve(challenge.challenge_id)
    _, token = store.pair_poll(challenge.challenge_id)
    assert token is not None
    sessions = store.list_sessions()
    assert len(sessions) == 1
    sid: UUID = sessions[0].session_id
    result = runner.invoke(app, ["auth", "revoke", str(sid)])
    assert result.exit_code == 0
    assert SessionStore().list_sessions() == []
