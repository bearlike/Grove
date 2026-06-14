"""MCP test fixtures — a fake GroveClient stubbing the HTTP boundary.

The fake subclasses ``GroveClient`` but never builds a transport: from
``grove.mcp``'s point of view the client's public methods ARE the HTTP
boundary, so stubbing them (and only them) follows the "stub only I/O
boundaries" rule. It records every call and returns canned contract
Views, so handler tests pin the request shaping and response bounding
without a daemon.
"""

from __future__ import annotations

from typing import Any

import pytest

from grove.client import GroveClient
from grove.core.contracts import (
    AttachInstructionView,
    CreateWorkspaceRequest,
    WorkspacePeekView,
    WorkspaceStateView,
)


def make_state(ws_id: str = "ws-1", **overrides: Any) -> WorkspaceStateView:
    data: dict[str, Any] = {
        "id": ws_id,
        "title": "fix login bug",
        "repo_root": "/projects/demo",
        "branch": "grove/fix-login-bug-123",
        "base_branch": "main",
        "worktree_path": "/projects/demo/.worktrees/fix-login-bug-123",
        "tmux_session": "grove-fix-login-bug-123",
        "agent_name": "claude",
        "status": "running",
        "created_at": "2026-06-11T00:00:00Z",
        "updated_at": "2026-06-11T00:00:00Z",
    }
    data.update(overrides)
    return WorkspaceStateView.model_validate(data)


def make_peek(snapshot: str | None = "agent output") -> WorkspacePeekView:
    return WorkspacePeekView.model_validate(
        {
            "state": make_state(),
            "base_ahead": 2,
            "base_behind": 0,
            "diff_added": 10,
            "diff_removed": 3,
            "dirty_files": 1,
            "recent_commits": [],
            "agent_snapshot": snapshot,
            "snapshot_taken_at": "2026-06-11T00:00:00Z" if snapshot else None,
        }
    )


class FakeGroveClient(GroveClient):
    """Records calls; returns canned Views. No transport, no HTTP."""

    def __init__(self) -> None:  # deliberately no super().__init__()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.peek_view: WorkspacePeekView = make_peek()
        self.send_message_error: Exception | None = None

    async def list_workspaces(self) -> list[WorkspaceStateView]:
        self.calls.append(("list_workspaces", {}))
        return [make_state("ws-1"), make_state("ws-2")]

    async def get_workspace(self, ws_id: str) -> WorkspaceStateView:
        self.calls.append(("get_workspace", {"ws_id": ws_id}))
        return make_state(ws_id)

    async def create_workspace(self, req: CreateWorkspaceRequest) -> WorkspaceStateView:
        self.calls.append(("create_workspace", {"req": req}))
        return make_state("ws-new", title=req.title, agent_name=req.agent_name)

    async def pause(self, ws_id: str, *, force: bool = False) -> WorkspaceStateView:
        self.calls.append(("pause", {"ws_id": ws_id, "force": force}))
        return make_state(ws_id, status="paused")

    async def resume(self, ws_id: str) -> WorkspaceStateView:
        self.calls.append(("resume", {"ws_id": ws_id}))
        return make_state(ws_id)

    async def respawn(self, ws_id: str) -> WorkspaceStateView:
        self.calls.append(("respawn", {"ws_id": ws_id}))
        return make_state(ws_id)

    async def kill(self, ws_id: str, *, delete_branch: bool | None = None) -> None:
        self.calls.append(("kill", {"ws_id": ws_id, "delete_branch": delete_branch}))

    async def get_attach(self, ws_id: str) -> AttachInstructionView:
        self.calls.append(("get_attach", {"ws_id": ws_id}))
        return AttachInstructionView(tmux_session=f"grove-{ws_id}", inside_outer_tmux=False)

    async def peek(self, ws_id: str) -> WorkspacePeekView:
        self.calls.append(("peek", {"ws_id": ws_id}))
        return self.peek_view

    async def send_message(self, ws_id: str, text: str) -> None:
        self.calls.append(("send_message", {"ws_id": ws_id, "text": text}))
        if self.send_message_error is not None:
            raise self.send_message_error


@pytest.fixture
def fake_client() -> FakeGroveClient:
    return FakeGroveClient()
