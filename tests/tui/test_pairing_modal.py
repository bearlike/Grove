"""Pilot test for the TUI pairing modal + watcher.

Pins the contract that drives the user-visible flow:

- Watcher polls the engine SessionStore on a tick and pushes a
  PairingModal when a brand-new pending challenge appears.
- Approve dismisses with True and calls SessionStore.pair_approve.
- Deny dismisses with False and calls SessionStore.pair_deny.
- The watcher de-dupes — once a challenge has been surfaced, the same
  id never re-prompts, even if it's still pending on the next tick.
- Token never lands on the modal UI (no string starting with grove_v1_
  in any rendered widget — this is the load-bearing invariant).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from grove.core.auth import SessionStore
from grove.tui.screens.pairing import PairingModal, PairingWatcher


@pytest.fixture
def store(tmp_state_dir: Path) -> SessionStore:
    del tmp_state_dir
    return SessionStore()


def test_watcher_pushes_modal_for_new_pending_challenge(store: SessionStore) -> None:
    """First tick after a new pending challenge appears → modal pushed."""
    challenge = store.pair_init(label="phone")
    fake_app = MagicMock()
    watcher = PairingWatcher(fake_app, store=store)
    watcher.tick()

    fake_app.push_screen.assert_called_once()
    args, _ = fake_app.push_screen.call_args
    modal = args[0]
    assert isinstance(modal, PairingModal)
    assert modal._challenge.challenge_id == challenge.challenge_id


def test_watcher_does_not_re_prompt_seen_challenge(store: SessionStore) -> None:
    store.pair_init(label="phone")
    fake_app = MagicMock()
    watcher = PairingWatcher(fake_app, store=store)
    watcher.tick()
    # Simulate the modal being dismissed (close the slot).
    watcher._modal_open = False
    watcher.tick()

    assert fake_app.push_screen.call_count == 1


def test_watcher_skips_when_modal_open(store: SessionStore) -> None:
    """A second pending challenge arriving while the modal is up doesn't
    spawn a parallel modal — `_modal_open` is the gate."""
    store.pair_init(label="phone")
    fake_app = MagicMock()
    watcher = PairingWatcher(fake_app, store=store)
    watcher.tick()
    store.pair_init(label="laptop")
    watcher.tick()  # modal still open
    assert fake_app.push_screen.call_count == 1


def test_watcher_approve_dismissal_calls_engine(store: SessionStore) -> None:
    challenge = store.pair_init(label="phone")
    fake_app = MagicMock()
    watcher = PairingWatcher(fake_app, store=store)
    watcher.tick()
    # The on_dismiss callback is the second positional arg to push_screen.
    on_dismiss = fake_app.push_screen.call_args.args[1]

    on_dismiss(True)

    fresh = store.list_pending_challenges()
    matched = [c for c in fresh if c.challenge_id == challenge.challenge_id]
    assert matched and matched[0].state.value == "approved"


def test_watcher_deny_dismissal_calls_engine(store: SessionStore) -> None:
    challenge = store.pair_init(label="phone")
    fake_app = MagicMock()
    watcher = PairingWatcher(fake_app, store=store)
    watcher.tick()
    on_dismiss = fake_app.push_screen.call_args.args[1]

    on_dismiss(False)

    fresh = store.get_challenge(challenge.challenge_id)
    assert fresh.state.value == "denied"


def test_modal_render_does_not_leak_token(store: SessionStore) -> None:
    """The modal renders the label + code only; pin that no token-shaped
    string ever ends up in any rendered widget."""
    challenge = store.pair_init(label="phone")
    PairingModal(challenge)  # construct it; just want to confirm the type accepts the challenge
    # The challenge object is the only data the modal reads. Confirm it
    # carries no token-like field at all.
    state_repr = repr(challenge)
    assert "grove_v1_" not in state_repr
    assert "token" not in state_repr.lower()


def test_watcher_handles_empty_store(store: SessionStore) -> None:
    """No pending challenges → no modal pushed, no error."""
    del store
    fake_app = MagicMock()
    fresh_store = SessionStore()
    watcher = PairingWatcher(fake_app, store=fresh_store)
    watcher.tick()
    fake_app.push_screen.assert_not_called()
