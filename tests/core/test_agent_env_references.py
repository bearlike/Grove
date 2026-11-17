"""A ``${VAR}`` in an agent's env resolves at that agent's launch, never at load.

The incident: a sandbox agent's env referenced a gateway URL that only the
daemon's environment and an ``ssm``-wrapped session carried, so every other
Grove process (the CLI in a plain terminal, the webapp unit, the hourly usage
backfill) failed at config load and the webapp crash-looped. These pin both
halves of the fix: unrelated work never reads the variable, and the agent that
does need it is refused before its create has any side effect.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import ConfigError
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from tests.conftest import FakeTmux


def _config(tmp_path: Path) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "agents": [
                {"name": "plain", "command": "claude", "kind": "claude_code"},
                {
                    "name": "sandbox",
                    "command": "claude",
                    "kind": "claude_code",
                    "env": {
                        "ANTHROPIC_BASE_URL": "${SANDBOX_GATEWAY}",
                        "LITERAL": "kept",
                    },
                },
            ],
        }
    )


def _manager(tmp_repo: Path, tmp_path: Path) -> WorkspaceManager:
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=_config(tmp_path), store=store)


def test_another_agent_creates_while_the_reference_is_unset(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SANDBOX_GATEWAY", raising=False)
    mgr = _manager(tmp_repo, tmp_path)
    state = mgr.create(CreateWorkspaceRequest(agent_name="plain", title="unrelated"))
    assert state.tmux_session in fake_tmux.sessions


def test_the_referencing_agent_is_refused_before_any_side_effect(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SANDBOX_GATEWAY", raising=False)
    mgr = _manager(tmp_repo, tmp_path)
    with pytest.raises(ConfigError, match="SANDBOX_GATEWAY"):
        mgr.create(CreateWorkspaceRequest(agent_name="sandbox", title="needs gateway"))
    assert mgr.list() == []
    assert fake_tmux.sessions == set()
    assert not (tmp_path / "trees").exists()


def test_the_launch_env_carries_the_value_resolved_at_launch(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SANDBOX_GATEWAY", "https://gateway.example.test")
    mgr = _manager(tmp_repo, tmp_path)
    state = mgr.create(CreateWorkspaceRequest(agent_name="sandbox", title="gateway"))
    [env] = [env for name, env, _ in fake_tmux.launch_envs if name == state.tmux_session]
    assert env["ANTHROPIC_BASE_URL"] == "https://gateway.example.test"
    assert env["LITERAL"] == "kept"
