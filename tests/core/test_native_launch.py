"""Private launch material lives in a 0600 file, never in argv.

Nothing here mints a credential any more: a native worker reaches the daemon
over the same loopback rendezvous every other local Grove process uses. What
``prepare`` still owes is that the brief and the daemon rendezvous stay out of
the process table, and that a relaunch rewrites the file rather than serving
the previous launch's material.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grove.core.auth import SessionStore
from grove.core.errors import AuthInvalidToken, GroveError
from grove.core.native_launch import NativeLaunch, worker_label
from grove.core.native_worker import NativeWorkerConfig


def test_private_launch_keeps_the_brief_off_argv_and_rewrites_on_relaunch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_path = tmp_path / "auth.json"
    monkeypatch.setattr("grove.core.paths.user_auth_path", lambda: auth_path)
    monkeypatch.delenv("GROVE_MAILBOX_SOCKET", raising=False)
    kwargs = {
        "workspace_id": "a" * 32,
        "provider": "claude_code",
        "command": ("claude",),
        "initial_prompt": "review the parser, then open a PR",
        "container": False,
    }
    launch = NativeLaunch.prepare(**kwargs)
    config_path = Path(launch.decoration[-1])
    config = NativeWorkerConfig.model_validate_json(config_path.read_text())
    assert config.initial_prompt == kwargs["initial_prompt"]
    assert kwargs["initial_prompt"] not in str(launch)
    assert config_path.stat().st_mode & 0o777 == 0o600
    # Argv names the file, never its contents — the file is the boundary.
    assert launch.decoration == ("--config", str(config_path))

    relaunched = NativeLaunch.prepare(**{**kwargs, "initial_prompt": ""})
    assert Path(relaunched.decoration[-1]) == config_path
    resumed = NativeWorkerConfig.model_validate_json(config_path.read_text())
    assert resumed.initial_prompt == ""


def test_container_worker_reaches_a_mailbox_url_with_a_minted_credential_and_no_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The UDS is no longer required: a URL plus a per-workspace bearer is enough,
    the bearer validates against the host's store, and kill-time revocation ends it."""
    auth_path = tmp_path / "auth.json"
    monkeypatch.setattr("grove.core.paths.user_auth_path", lambda: auth_path)
    monkeypatch.delenv("GROVE_MAILBOX_SOCKET", raising=False)
    workspace_id = "b" * 32
    launch = NativeLaunch.prepare(
        workspace_id=workspace_id,
        provider="claude_code",
        command=("claude",),
        initial_prompt="",
        container=True,
        config_root=tmp_path / "share",
        container_config_root="/grove/agent-config/claude_code",
        mailbox_url="http://host.docker.internal:7421",
    )
    assert launch.decoration == ("--config", f"/grove/agent-config/claude_code/{workspace_id}.json")
    config = NativeWorkerConfig.model_validate_json(
        (tmp_path / "share" / f"{workspace_id}.json").read_text()
    )
    assert config.daemon_url == "http://host.docker.internal:7421"
    assert config.daemon_socket is None
    assert config.daemon_token is not None
    store = SessionStore(path=auth_path)
    assert store.validate(config.daemon_token).label == worker_label(workspace_id)
    assert store.revoke_label(worker_label(workspace_id)) == 1
    with pytest.raises(AuthInvalidToken, match="revoked"):
        store.validate(config.daemon_token)


def test_container_worker_without_url_or_socket_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GROVE_MAILBOX_SOCKET", raising=False)
    with pytest.raises(GroveError, match="reachable mailbox URL"):
        NativeLaunch.prepare(
            workspace_id="c" * 32,
            provider="claude_code",
            command=("claude",),
            initial_prompt="",
            container=True,
            config_root=tmp_path,
            container_config_root="/grove/agent-config/claude_code",
        )
