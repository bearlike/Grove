"""GroveTools handlers against the fake client — request shaping, response
bounding, destructive-tool safety, capability degradation."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from grove.client import ProtocolError
from grove.core.contracts import RootBranch
from grove.mcp.tools import GroveTools
from tests.mcp.conftest import FakeGroveClient, make_peek

# ─── read tools ──────────────────────────────────────────────────────────────


async def test_list_workspaces_returns_state_views(fake_client: FakeGroveClient) -> None:
    tools = GroveTools(fake_client)
    result = await tools.list_workspaces()
    assert [w.id for w in result] == ["ws-1", "ws-2"]
    assert fake_client.calls == [
        ("list_workspaces", {"repo": None, "ticket_provider": None, "ticket_id": None})
    ]


async def test_list_workspaces_threads_repo_root_and_ticket_filter(
    fake_client: FakeGroveClient,
) -> None:
    tools = GroveTools(fake_client)
    await tools.list_workspaces(repo_root="/repo", ticket_provider="github", ticket_id="42")
    assert fake_client.calls == [
        (
            "list_workspaces",
            {"repo": Path("/repo"), "ticket_provider": "github", "ticket_id": "42"},
        )
    ]


async def test_get_workspace_passes_id(fake_client: FakeGroveClient) -> None:
    tools = GroveTools(fake_client)
    result = await tools.get_workspace("ws-42")
    assert result.id == "ws-42"
    assert fake_client.calls == [("get_workspace", {"ws_id": "ws-42"})]


async def test_list_agents_passes_repo_root_and_returns_roster(
    fake_client: FakeGroveClient,
) -> None:
    tools = GroveTools(fake_client)
    result = await tools.list_agents("/repo")
    assert [a.name for a in result] == ["claude", "shell"]
    assert result[0].models == ("opus", "sonnet")
    assert fake_client.calls == [("list_agents", {"repo": Path("/repo")})]


async def test_attach_instruction_builds_paste_ready_command(
    fake_client: FakeGroveClient,
) -> None:
    tools = GroveTools(fake_client)
    result = await tools.attach_instruction("ws-1")
    assert result.tmux_session == "grove-ws-1"
    assert result.command == "tmux attach -t grove-ws-1"
    assert result.inside_outer_tmux is False


# ─── peek bounding ───────────────────────────────────────────────────────────


async def test_peek_caps_oversized_snapshot(fake_client: FakeGroveClient) -> None:
    fake_client.peek_view = make_peek(snapshot="x" * 10_000)
    tools = GroveTools(fake_client)
    result = await tools.peek_workspace("ws-1")
    assert result.agent_snapshot is not None
    assert len(result.agent_snapshot) == GroveTools.SNAPSHOT_CAP
    # Trailing ellipsis is the trim signal, same convention as contracts.
    assert result.agent_snapshot.endswith("…")


async def test_peek_keeps_short_snapshot_verbatim(fake_client: FakeGroveClient) -> None:
    fake_client.peek_view = make_peek(snapshot="short")
    tools = GroveTools(fake_client)
    result = await tools.peek_workspace("ws-1")
    assert result.agent_snapshot == "short"


async def test_peek_tolerates_missing_snapshot(fake_client: FakeGroveClient) -> None:
    fake_client.peek_view = make_peek(snapshot=None)
    tools = GroveTools(fake_client)
    result = await tools.peek_workspace("ws-1")
    assert result.agent_snapshot is None


# ─── create ──────────────────────────────────────────────────────────────────


async def test_create_workspace_builds_request_with_auto_default(
    fake_client: FakeGroveClient,
) -> None:
    tools = GroveTools(fake_client)
    result = await tools.create_workspace(
        repo_root="/projects/demo", title="add tests", agent_name="claude"
    )
    assert result.title == "add tests"
    ((name, kwargs),) = fake_client.calls
    assert name == "create_workspace"
    req = kwargs["req"]
    assert req.repo_root == Path("/projects/demo")
    assert req.branch_plan.kind == "auto"
    assert req.skip_init is False


async def test_create_workspace_threads_model_through(
    fake_client: FakeGroveClient,
) -> None:
    tools = GroveTools(fake_client)
    await tools.create_workspace(
        repo_root="/projects/demo",
        title="add tests",
        agent_name="claude",
        model="opus",
    )
    req = fake_client.calls[0][1]["req"]
    assert req.model == "opus"


async def test_create_workspace_model_defaults_to_none(
    fake_client: FakeGroveClient,
) -> None:
    tools = GroveTools(fake_client)
    await tools.create_workspace(repo_root="/projects/demo", title="add tests", agent_name="claude")
    req = fake_client.calls[0][1]["req"]
    assert req.model is None


async def test_create_workspace_passes_branch_plan_through(
    fake_client: FakeGroveClient,
) -> None:
    tools = GroveTools(fake_client)
    await tools.create_workspace(
        repo_root="/projects/demo",
        title="hotfix",
        agent_name="claude",
        branch_plan=RootBranch(),
        skip_init=True,
    )
    req = fake_client.calls[0][1]["req"]
    assert req.branch_plan.kind == "root"
    assert req.skip_init is True


# ─── lifecycle ───────────────────────────────────────────────────────────────


async def test_pause_passes_force_flag(fake_client: FakeGroveClient) -> None:
    tools = GroveTools(fake_client)
    await tools.pause_workspace("ws-1", force=True)
    assert fake_client.calls == [("pause", {"ws_id": "ws-1", "force": True})]


async def test_resume_and_respawn_are_distinct_verbs(
    fake_client: FakeGroveClient,
) -> None:
    tools = GroveTools(fake_client)
    await tools.resume_workspace("ws-1")
    await tools.respawn_workspace("ws-1")
    assert [c[0] for c in fake_client.calls] == ["resume", "respawn"]


# ─── destructive-tool safety ─────────────────────────────────────────────────


def test_kill_requires_explicit_delete_branch() -> None:
    """The destructive flag must have NO default — a tool caller that
    omits it must fail schema validation, never get a silent guess."""
    param = inspect.signature(GroveTools.kill_workspace).parameters["delete_branch"]
    assert param.default is inspect.Parameter.empty


async def test_kill_forwards_flag_and_returns_structured_result(
    fake_client: FakeGroveClient,
) -> None:
    tools = GroveTools(fake_client)
    result = await tools.kill_workspace("ws-1", delete_branch=False)
    assert fake_client.calls == [("kill", {"ws_id": "ws-1", "delete_branch": False})]
    assert result.status == "killed"
    assert result.workspace_id == "ws-1"
    assert result.delete_branch_requested is False


# ─── message capability degradation ──────────────────────────────────────────


async def test_send_message_reports_sent(fake_client: FakeGroveClient) -> None:
    tools = GroveTools(fake_client)
    result = await tools.send_workspace_message("ws-1", "please run the tests")
    assert result.status == "sent"
    assert fake_client.calls == [
        ("send_message", {"ws_id": "ws-1", "text": "please run the tests"})
    ]


@pytest.mark.parametrize("status", [404, 405])
async def test_send_message_degrades_when_endpoint_missing(
    fake_client: FakeGroveClient, status: int
) -> None:
    """A daemon predating issue #37's endpoint answers with a framework
    404/405 (code ``http_error``) — the tool reports the capability as
    unavailable instead of erroring."""
    fake_client.send_message_error = ProtocolError(
        code="http_error", message="Not Found", status=status
    )
    tools = GroveTools(fake_client)
    result = await tools.send_workspace_message("ws-1", "hello")
    assert result.status == "unavailable"
    assert result.detail is not None


async def test_send_message_propagates_workspace_not_found(
    fake_client: FakeGroveClient,
) -> None:
    """An enveloped 404 (``workspace_not_found``) is a real caller error,
    not a missing capability — it must propagate."""
    fake_client.send_message_error = ProtocolError(
        code="workspace_not_found", message="no workspace 'ws-9'", status=404
    )
    tools = GroveTools(fake_client)
    with pytest.raises(ProtocolError):
        await tools.send_workspace_message("ws-9", "hello")


# ─── #120 resume + remap ─────────────────────────────────────────────────────


async def test_create_workspace_threads_resume_session_id(
    fake_client: FakeGroveClient,
) -> None:
    """The optional resume_session_id rides the request the tool builds."""
    tools = GroveTools(fake_client)
    await tools.create_workspace(
        repo_root="/projects/demo",
        title="resume",
        agent_name="claude",
        resume_session_id="sess-123",
    )
    ((name, kwargs),) = fake_client.calls
    assert name == "create_workspace"
    assert kwargs["req"].resume_session_id == "sess-123"


async def test_create_workspace_resume_defaults_to_none(
    fake_client: FakeGroveClient,
) -> None:
    tools = GroveTools(fake_client)
    await tools.create_workspace(repo_root="/projects/demo", title="fresh", agent_name="claude")
    ((_, kwargs),) = fake_client.calls
    assert kwargs["req"].resume_session_id is None


async def test_remap_workspace_session_passes_ref(fake_client: FakeGroveClient) -> None:
    tools = GroveTools(fake_client)
    result = await tools.remap_workspace_session("ws-7", "cafef00d")
    assert result.id == "ws-7"
    assert fake_client.calls == [("remap_session", {"ws_id": "ws-7", "session_ref": "cafef00d"})]
