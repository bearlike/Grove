"""The daemon's ambient env must not dictate an agent's profile.

The *generic hermetic launch boundary* — `AgentSpec.env_unset` clears the vars
the config names (no var name is hard-coded in code) before ``env`` is
applied — plus the read side's union-scan, which finds a default-profile
transcript in the tool's default dir regardless of any ``CLAUDE_CONFIG_DIR``
the daemon happens to carry. No per-agent typed field, no ``config_dir``
threaded through the read path: the env mechanism is the contract, and a
future container launcher applies the same ``env`` / ``env_unset`` at create.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grove.core.agents.claude_code import ClaudeCodeAdapter, _ClaudeHome
from grove.core.config import GroveConfig, _merge_agents
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from tests.conftest import FakeTmux


def _launch_env(fake: FakeTmux, session: str) -> tuple[dict[str, str], tuple[str, ...]]:
    for name, env, env_unset in reversed(fake.launch_envs):
        if name == session:
            return env, env_unset
    raise AssertionError(f"no launch env recorded for {session}")


# ─── the generic env mechanism (config contract) ─────────────────────────────


def test_no_var_name_is_hardcoded_in_builtin_agents() -> None:
    """Pure mechanism: the built-in agents name no env var to set/clear. Hermetic
    profiles are the operator's policy, expressed in their config — never baked
    into Grove's code as a hard-coded var name."""
    for agent in GroveConfig().agents:
        assert agent.env == {}
        assert agent.env_unset == ()


def test_field_merge_keeps_env_unset_when_overriding_command() -> None:
    """The cascade footgun (mirrors the ``kind`` guard): tweaking only ``command``
    in a higher layer must not silently drop a configured ``env_unset``."""
    base = [{"name": "claude", "command": "claude", "env_unset": ["CLAUDE_CONFIG_DIR"]}]
    overlay = [{"name": "claude", "command": "claude --model sonnet"}]
    merged = _merge_agents(base, overlay)
    assert merged[0]["command"] == "claude --model sonnet"
    assert merged[0]["env_unset"] == ["CLAUDE_CONFIG_DIR"]


def test_user_env_pins_profile_alongside_configured_unset() -> None:
    """A higher layer adds only ``env``; a lower layer's ``env_unset`` survives the
    field-level merge and coexists with it. Unset-before-export (verified in the
    tmux test) means the export then wins — so the pinned dir reaches the agent."""
    base = [{"name": "claude", "command": "claude", "env_unset": ["CLAUDE_CONFIG_DIR"]}]
    overlay = [{"name": "claude", "env": {"CLAUDE_CONFIG_DIR": "/work"}}]
    merged = _merge_agents(base, overlay)
    assert merged[0]["env"] == {"CLAUDE_CONFIG_DIR": "/work"}
    assert merged[0]["env_unset"] == ["CLAUDE_CONFIG_DIR"]


# ─── write side: the manager threads the configured env through to launch ─────


def test_configured_env_unset_carries_through_to_launch(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """End-to-end: an agent configured with ``env_unset`` reaches the tmux layout
    with exactly that set — the manager transforms nothing; the config's env
    contract on the AgentSpec is what carries through to the pane."""
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "agents": [
                {
                    "name": "claude",
                    "command": "claude",
                    "kind": "claude_code",
                    "env_unset": ["CLAUDE_CONFIG_DIR"],
                }
            ],
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    mgr = WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)

    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="hermetic"))
    env, env_unset = _launch_env(fake_tmux, state.tmux_session)
    assert env_unset == ("CLAUDE_CONFIG_DIR",)
    assert "CLAUDE_CONFIG_DIR" not in env


# ─── read side: union-scan finds the default-profile transcript despite skew ──


def test_default_profile_transcript_found_despite_daemon_config_dir_skew(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With the daemon carrying a mismatched ``CLAUDE_CONFIG_DIR`` (a stale
    value the long-lived tmux server may still hold), a hermetic
    default-profile agent writes its transcript to Claude's default dir.
    ``projects_dirs`` always unions that default in, so the read side still
    finds the transcript and activity advances out of the empty/UNKNOWN state."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    # The daemon's ambient value points somewhere with no transcript — the skew.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "work-profile"))

    cwd = tmp_path / "repo"
    session_id = "11111111-2222-3333-4444-555555555555"
    folder = home / ".claude" / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True)
    (folder / f"{session_id}.jsonl").write_text(
        f'{{"type":"user","cwd":"{cwd}","uuid":"u1","timestamp":"2026-06-14T10:00:00Z",'
        f'"message":{{"role":"user","content":"hello"}}}}\n'
        f'{{"type":"assistant","cwd":"{cwd}","uuid":"u2","timestamp":"2026-06-14T10:00:01Z",'
        f'"message":{{"role":"assistant","content":[{{"type":"text","text":"hi"}}],'
        f'"stop_reason":"end_turn","model":"claude-x"}}}}\n',
        encoding="utf-8",
    )

    activity = ClaudeCodeAdapter().parse_activity(cwd, session_id)
    assert activity.human_turns == 1  # the transcript WAS located despite the skew
