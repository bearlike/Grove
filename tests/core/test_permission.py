"""Native permission answering (#172): the Grove-hosted ``--permission-prompt-tool``.

Two surfaces:

* The pure decision layer (``PermissionPolicy`` / ``PermissionServer.answer``): a
  permission prompt is answered with allow/deny JSON — fail-closed by default,
  permissive only on the explicit opt-in — with **no tmux keystrokes** anywhere
  (the whole point: a paneless session has no pane to type a permission answer at).
* The manager's opt-in launch composition: ``--mcp-config`` + ``--permission-prompt-tool``
  ride a ``claude_code`` launch when ``cfg.permission.enabled``, before the trailing
  initial-prompt positional (mirrors the hook ``--settings`` / channel ``--channels``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grove.core import permission
from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.permission import (
    PermissionOutcome,
    PermissionPolicy,
    PermissionRequest,
    PermissionServer,
    permission_mcp_config,
    permission_tool_ref,
)
from grove.core.store import JsonWorkspaceStore
from tests.conftest import FakeTmux

# ─── the tool reference + registration shapes ───────────────────────────────


def test_permission_default_is_off_and_fail_closed() -> None:
    """Default config keeps the tool off and, when built, fail-closed to deny."""
    assert GroveConfig().permission.enabled is False
    assert GroveConfig().permission.default == "deny"


def test_tool_ref_is_mcp_qualified() -> None:
    """The ``--permission-prompt-tool`` value resolves the tool by MCP-qualified name."""
    assert permission_tool_ref() == "mcp__grove_permission__permission_prompt"


def test_mcp_config_registers_grove_permission_server() -> None:
    config = permission_mcp_config()
    assert "grove_permission" in config["mcpServers"]
    server = config["mcpServers"]["grove_permission"]
    assert server["args"] == ["-m", "grove.core.permission"]


# ─── the pure decision layer ────────────────────────────────────────────────


def _request() -> PermissionRequest:
    return PermissionRequest(tool_name="Bash", tool_input={"command": "ls"}, tool_use_id="toolu_1")


def test_policy_deny_default_is_fail_closed() -> None:
    outcome = PermissionPolicy("deny").decide(_request())
    assert outcome.behavior == PermissionOutcome.DENY
    assert outcome.to_result()["behavior"] == "deny"
    assert "Bash" in outcome.to_result()["message"]


def test_policy_allow_opt_in_echoes_input_unmodified() -> None:
    """The permissive opt-in allows and forwards the tool input verbatim (no
    rewrite — the provider boundary: Grove permits, it never edits)."""
    outcome = PermissionPolicy("allow").decide(_request())
    result = outcome.to_result()
    assert result["behavior"] == "allow"
    assert result["updatedInput"] == {"command": "ls"}


def test_policy_unknown_mode_falls_closed_to_deny() -> None:
    """Anything but the explicit ``allow`` must deny — an unknown mode can never
    accidentally widen what an agent may do."""
    assert PermissionPolicy("banana").decide(_request()).behavior == PermissionOutcome.DENY


def test_policy_malformed_request_denies() -> None:
    assert PermissionPolicy("allow").decide(None).behavior == PermissionOutcome.DENY


def test_request_from_arguments_parses_and_rejects_junk() -> None:
    parsed = PermissionRequest.from_arguments(
        {"tool_name": "Write", "tool_input": {"path": "x"}, "tool_use_id": "t"}
    )
    assert parsed is not None
    assert parsed.tool_name == "Write"
    assert parsed.tool_input == {"path": "x"}
    # Missing / malformed tool_name → None (caller maps to fail-closed deny).
    assert PermissionRequest.from_arguments({"tool_input": {}}) is None
    assert PermissionRequest.from_arguments("nope") is None


def test_server_answer_returns_decision_json_string() -> None:
    """``PermissionServer.answer`` is the pure MCP-tool body: it serializes the
    decision to the JSON string Claude Code reads back — no tmux, no send_keys."""
    cfg = GroveConfig.model_validate({"permission": {"default": "allow"}})
    server = PermissionServer(cfg.permission)
    result = json.loads(server.answer({"tool_name": "Bash", "tool_input": {"command": "ls"}}))
    assert result == {"behavior": "allow", "updatedInput": {"command": "ls"}}

    denied = PermissionServer(GroveConfig().permission)
    assert json.loads(denied.answer({"tool_name": "Bash"}))["behavior"] == "deny"


# ─── manager launch composition: opt-in --permission-prompt-tool append ──────


def _last_decoration(fake: FakeTmux, session: str) -> list[str]:
    for name, decoration in reversed(fake.launch_decorations):
        if name == session:
            return decoration
    raise AssertionError(f"no launch decoration recorded for {session}")


def _manager(tmp_repo: Path, tmp_path: Path, *, enabled: bool) -> WorkspaceManager:
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "hooks": {"enabled": False},
            "permission": {"enabled": enabled},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


def test_permission_disabled_appends_no_flag(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    mgr = _manager(tmp_repo, tmp_path, enabled=False)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="off"))
    assert _last_decoration(fake_tmux, state.tmux_session) == [
        "--session-id",
        state.agent_session_id,
    ]


def test_permission_enabled_appends_flags_before_prompt(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The permission flags ride a claude_code launch when enabled, and stay BEFORE
    the trailing initial-prompt positional (mirrors the hook/channel appends)."""
    cfg_path = tmp_path / "grove-permission-mcp.json"
    monkeypatch.setattr(permission, "permission_mcp_config_path", lambda: cfg_path)
    mgr = _manager(tmp_repo, tmp_path, enabled=True)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="on", initial_prompt="go"))
    assert _last_decoration(fake_tmux, state.tmux_session) == [
        "--session-id",
        state.agent_session_id,
        "--mcp-config",
        str(cfg_path),
        "--permission-prompt-tool",
        "mcp__grove_permission__permission_prompt",
        "go",
    ]
    assert cfg_path.exists()  # config written at launch


def test_permission_flag_not_appended_for_shell_agent(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Non-claude_code kinds never get the flag (no permission-prompt concept)."""
    mgr = _manager(tmp_repo, tmp_path, enabled=True)
    state = mgr.create(CreateWorkspaceRequest(agent_name="shell", title="sh"))
    assert _last_decoration(fake_tmux, state.tmux_session) == []
