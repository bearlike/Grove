"""Private launch material is generation-bound and never added to argv."""

from __future__ import annotations

from pathlib import Path

import pytest

from grove.core.auth import SessionStore
from grove.core.errors import GroveError
from grove.core.native_launch import NativeLaunch
from grove.core.native_worker import NativeWorkerConfig


def test_private_launch_has_distinct_scopes_and_rotates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_path = tmp_path / "auth.json"
    monkeypatch.setattr("grove.core.paths.user_auth_path", lambda: auth_path)
    monkeypatch.delenv("GROVE_MAILBOX_SOCKET", raising=False)
    workspace_id = "a" * 32
    kwargs = {
        "workspace_id": workspace_id,
        "provider": "claude_code",
        "command": ("claude",),
        "initial_prompt": "review",
        "container": False,
    }
    launch = NativeLaunch.prepare(**kwargs)
    config_path = Path(launch.decoration[-1])
    config = NativeWorkerConfig.model_validate_json(config_path.read_text())
    assert config.registration_token not in str(launch)
    assert config.peer_token not in str(launch)
    assert config_path.stat().st_mode & 0o777 == 0o600
    store = SessionStore()
    peer = store.validate(config.peer_token)
    owner = store.validate(config.registration_token)
    assert peer.mailbox_identity == owner.mailbox_identity
    assert not peer.mailbox_registration
    assert owner.mailbox_registration
    NativeLaunch.prepare(**kwargs)
    with pytest.raises(GroveError):
        store.validate(config.peer_token)


def test_container_without_private_transport_refuses_before_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_path = tmp_path / "auth.json"
    monkeypatch.setattr("grove.core.paths.user_auth_path", lambda: auth_path)
    monkeypatch.delenv("GROVE_MAILBOX_SOCKET", raising=False)
    with pytest.raises(GroveError, match="private mailbox socket"):
        NativeLaunch.prepare(
            workspace_id="a" * 32,
            provider="claude_code",
            command=("claude",),
            initial_prompt="review",
            container=True,
        )
    assert not auth_path.exists()
