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

from grove.core.native_launch import NativeLaunch
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
