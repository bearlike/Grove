"""GroveClient HTTP methods — round-trip every endpoint against a real daemon.

These tests spawn an actual ``grove daemon serve`` subprocess via
``LocalTransport``, so they exercise the full HTTP wire. State and
config are isolated to ``tmp_path`` via ``XDG_STATE_HOME`` /
``XDG_CONFIG_HOME`` (which the subprocess inherits and platformdirs
honors), and every test runs ``tmux kill-server`` against the test's
session prefix in teardown so a partial run never leaks tmux state.

Marked ``integration`` + ``requires_tmux`` to mirror
``tests/integration/test_real_tmux_git.py``: real subprocesses, real
tmux, real git.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

from grove.client import BackendConfig, GroveClient
from grove.core.contracts.requests import CreateWorkspaceRequest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_tmux,
    pytest.mark.skipif(
        shutil.which("tmux") is None or shutil.which("git") is None,
        reason="tmux and git must be installed",
    ),
]

# Per-test tmux prefix keeps sessions namespaced so cleanup can target
# only what this file created — no risk of killing the developer's own
# tmux sessions on a workstation run.
_SESSION_PREFIX = "grove-cli-test-"


@pytest.fixture
def isolated_grove_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect daemon subprocess state + config into ``tmp_path``.

    The subprocess inherits this process's env, and platformdirs
    honors ``XDG_STATE_HOME`` / ``XDG_CONFIG_HOME`` on Linux. We also
    write a project-less user config that uses a long-lived shell agent
    (so the agent pane stays open through pause/resume) and a unique
    tmux session prefix (so cleanup only touches our sessions).
    """
    state_home = tmp_path / "xdg-state"
    config_home = tmp_path / "xdg-config"
    state_home.mkdir()
    config_home.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    config_dir = config_home / "grove"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "tmux": {"session_prefix": _SESSION_PREFIX},
                # Keep the default agent name "claude" so payloads in the
                # tests below need no per-fixture override; only the command
                # changes — sleep keeps the pane open through the test.
                "agents": [
                    {
                        "name": "claude",
                        "command": "sh -c 'while :; do sleep 30; done'",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def cleanup_tmux() -> Iterator[None]:
    """Kill any tmux sessions this file created, even on failure."""
    yield
    out = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    for name in out.splitlines():
        if name.startswith(_SESSION_PREFIX):
            subprocess.run(
                ["tmux", "kill-session", "-t", name],
                capture_output=True,
                check=False,
            )


@pytest.fixture
async def client(isolated_grove_env: Path, cleanup_tmux: None) -> AsyncIterator[GroveClient]:
    del isolated_grove_env, cleanup_tmux  # fixture-only; side effects above
    cfg = BackendConfig(label="Local")
    cli = GroveClient(cfg)
    await cli.connect()
    try:
        yield cli
    finally:
        await cli.close()


async def test_list_empty(client: GroveClient) -> None:
    assert await client.list_workspaces() == []


async def test_health_returns_status_and_version(client: GroveClient) -> None:
    """``/healthz`` is the public liveness probe — minimal shape, no host id."""
    health = await client.health()
    assert health.status == "ok"
    assert health.version


async def test_whoami_returns_identity_and_uptime(client: GroveClient) -> None:
    """``/whoami`` carries host + user + uptime; the auth gate is in unit
    tests (``tests/daemon/test_auth_endpoints.py``). Here we just pin
    that the wire shape parses and uptime is non-negative."""
    info = await client.whoami()
    assert info.version
    assert info.host
    assert info.user
    assert info.uptime_seconds >= 0
    assert info.platform in {"linux", "darwin", "windows"}
    assert info.python_version


async def test_create_then_list(client: GroveClient, tmp_repo: Path) -> None:
    req = CreateWorkspaceRequest(agent_name="claude", title="t1", repo_root=tmp_repo)
    state = await client.create_workspace(req)
    assert state.title == "t1"
    listed = await client.list_workspaces()
    assert any(w.id == state.id for w in listed)
    with contextlib.suppress(Exception):
        await client.kill(state.id, delete_branch=True)


async def test_get_workspace(client: GroveClient, tmp_repo: Path) -> None:
    req = CreateWorkspaceRequest(agent_name="claude", title="t2", repo_root=tmp_repo)
    created = await client.create_workspace(req)
    try:
        fetched = await client.get_workspace(created.id)
        assert fetched.id == created.id
    finally:
        with contextlib.suppress(Exception):
            await client.kill(created.id, delete_branch=True)


async def test_pause_then_resume(client: GroveClient, tmp_repo: Path) -> None:
    req = CreateWorkspaceRequest(agent_name="claude", title="t3", repo_root=tmp_repo)
    created = await client.create_workspace(req)
    try:
        paused = await client.pause(created.id)
        assert paused.status.value == "paused"
        resumed = await client.resume(created.id)
        assert resumed.status.value in {"running", "active", "idle"}
    finally:
        with contextlib.suppress(Exception):
            await client.kill(created.id, delete_branch=True)


async def test_attach_returns_session_name(client: GroveClient, tmp_repo: Path) -> None:
    req = CreateWorkspaceRequest(agent_name="claude", title="t4", repo_root=tmp_repo)
    created = await client.create_workspace(req)
    try:
        instr = await client.get_attach(created.id)
        assert instr.tmux_session == created.tmux_session
    finally:
        with contextlib.suppress(Exception):
            await client.kill(created.id, delete_branch=True)


async def test_peek(client: GroveClient, tmp_repo: Path) -> None:
    req = CreateWorkspaceRequest(agent_name="claude", title="t5", repo_root=tmp_repo)
    created = await client.create_workspace(req)
    try:
        peek = await client.peek(created.id)
        assert peek.state.id == created.id
    finally:
        with contextlib.suppress(Exception):
            await client.kill(created.id, delete_branch=True)


async def test_branches_local(client: GroveClient, tmp_repo: Path) -> None:
    branches = await client.list_branches(repo=tmp_repo, scope="local")
    names = {b.name for b in branches}
    assert "main" in names


async def test_kill(client: GroveClient, tmp_repo: Path) -> None:
    req = CreateWorkspaceRequest(agent_name="claude", title="t6", repo_root=tmp_repo)
    created = await client.create_workspace(req)
    await client.kill(created.id, delete_branch=True)
    listed = await client.list_workspaces()
    assert all(w.id != created.id for w in listed)


async def test_update_renames_title_and_sets_description(
    client: GroveClient, tmp_repo: Path
) -> None:
    req = CreateWorkspaceRequest(agent_name="claude", title="initial", repo_root=tmp_repo)
    created = await client.create_workspace(req)
    try:
        updated = await client.update_workspace(
            created.id, title="renamed", description="see ticket #1"
        )
        assert updated.title == "renamed"
        assert updated.description == "see ticket #1"
        # Identity stays — only metadata moves.
        assert updated.tmux_session == created.tmux_session
        assert updated.worktree_path == created.worktree_path
    finally:
        with contextlib.suppress(Exception):
            await client.kill(created.id, delete_branch=True)


async def test_update_clears_description_with_empty_string(
    client: GroveClient, tmp_repo: Path
) -> None:
    req = CreateWorkspaceRequest(
        agent_name="claude", title="t7", description="initial", repo_root=tmp_repo
    )
    created = await client.create_workspace(req)
    assert created.description == "initial"
    try:
        cleared = await client.update_workspace(created.id, description="")
        assert cleared.description is None
    finally:
        with contextlib.suppress(Exception):
            await client.kill(created.id, delete_branch=True)
