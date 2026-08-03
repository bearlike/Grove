"""MCP test fixtures — a fake GroveClient stubbing the HTTP boundary.

The fake subclasses ``GroveClient`` but never builds a transport: from
``grove.mcp``'s point of view the client's public methods ARE the HTTP
boundary, so stubbing them (and only them) follows the "stub only I/O
boundaries" rule. It records every call and returns canned contract
Views, so handler tests pin the request shaping and response bounding
without a daemon.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from grove.client import GroveClient
from grove.core.contracts import (
    AgentSummaryView,
    AttachInstructionView,
    CreateWorkspaceRequest,
    HostAttachView,
    ProjectView,
    SessionSummaryView,
    WorkspacePeekView,
    WorkspaceStateView,
)
from grove.core.contracts.activity import DashboardSnapshotView


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


def make_snapshot(*, phase: str | None = "implementing") -> DashboardSnapshotView:
    """One activity snapshot carrying the two axes only this read reports:
    the workspace's task ``phase`` and its session's blended agent state."""
    activity: dict[str, Any] = {
        "state": "working",
        "title": None,
        "current_task": "wiring the client",
        "human_turns": 1,
        "assistant_replies": 2,
        "replies_per_turn": [2],
        "tool_calls": 3,
        "model": "claude-opus-4",
        "tokens_in": 10,
        "tokens_out": 20,
        "last_event_at": "2026-07-31T00:00:00Z",
        "needs_attention": False,
        "error_detail": None,
    }
    return DashboardSnapshotView.model_validate(
        {
            "generated_at": "2026-07-31T00:00:00Z",
            "total_workspaces": 1,
            "needs_attention": 0,
            "projects": [
                {
                    "repo_root": "/projects/demo",
                    "repo_name": "demo",
                    "cwd": "/projects/demo",
                    "workspaces": [
                        {
                            "state": make_state(),
                            "sessions": [
                                {
                                    "session": {
                                        "session_id": "sess-1",
                                        "adapter_kind": "claude_code",
                                        "provenance": "grove_launched",
                                        "tmux_window": "agent",
                                    },
                                    "activity": activity,
                                }
                            ],
                            "base_ahead": 1,
                            "base_behind": 0,
                            "diff_added": 4,
                            "diff_removed": 1,
                            "dirty_files": 0,
                            "pane_target": "grove-demo:agent",
                            "needs_attention": False,
                            "recent_commits": [],
                            "observed_at": "2026-07-31T00:00:00Z",
                            "phase": (
                                None
                                if phase is None
                                else {
                                    "phase": phase,
                                    "note": None,
                                    "updated_at": "2026-07-31T00:00:00Z",
                                    "index": 2,
                                    "total": 6,
                                }
                            ),
                        }
                    ],
                }
            ],
        }
    )


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
        self.activity_view: DashboardSnapshotView = make_snapshot()
        self.send_message_error: Exception | None = None
        self.attach_view: AttachInstructionView | None = None

    async def list_workspaces(
        self,
        *,
        repo: Path | None = None,
        ticket_provider: str | None = None,
        ticket_id: str | None = None,
    ) -> list[WorkspaceStateView]:
        self.calls.append(
            (
                "list_workspaces",
                {"repo": repo, "ticket_provider": ticket_provider, "ticket_id": ticket_id},
            )
        )
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
        if self.attach_view is not None:
            return self.attach_view
        return HostAttachView(tmux_session=f"grove-{ws_id}", inside_outer_tmux=False)

    async def peek(self, ws_id: str) -> WorkspacePeekView:
        self.calls.append(("peek", {"ws_id": ws_id}))
        return self.peek_view

    async def get_activity(self) -> DashboardSnapshotView:
        self.calls.append(("get_activity", {}))
        return self.activity_view

    async def send_message(self, ws_id: str, text: str) -> None:
        self.calls.append(("send_message", {"ws_id": ws_id, "text": text}))
        if self.send_message_error is not None:
            raise self.send_message_error

    async def remap_session(self, ws_id: str, session_ref: str) -> WorkspaceStateView:
        self.calls.append(("remap_session", {"ws_id": ws_id, "session_ref": session_ref}))
        return make_state(ws_id)

    async def attach_ticket_by_ref(self, ws_id: str, ref: str) -> WorkspaceStateView:
        # Resolution is server-side: the fake just records the raw ref it was
        # handed, mirroring the daemon route's actual contract instead of
        # re-parsing it here.
        self.calls.append(("attach_ticket_by_ref", {"ws_id": ws_id, "ref": ref}))
        return make_state(ws_id, ticket_refs=[{"provider": "gitea", "id": "42"}])

    async def detach_ticket_by_ref(self, ws_id: str, ref: str) -> WorkspaceStateView:
        self.calls.append(("detach_ticket_by_ref", {"ws_id": ws_id, "ref": ref}))
        return make_state(ws_id, ticket_refs=[])

    async def list_agents(self, repo: Path) -> list[AgentSummaryView]:
        self.calls.append(("list_agents", {"repo": repo}))
        return [
            AgentSummaryView(name="claude", kind="claude_code", models=("opus", "sonnet")),
            AgentSummaryView(name="shell", kind="generic"),
        ]

    async def list_sessions(
        self, *, repo: Path | None = None, limit: int = 50
    ) -> list[SessionSummaryView]:
        self.calls.append(("list_sessions", {"repo": repo, "limit": limit}))
        # A host-scope pair: one Grove-launched row, one the catalog found in a
        # directory no workspace owns — the two shapes an agent must handle.
        return [
            SessionSummaryView.model_validate(
                {
                    "session_id": "s-grove",
                    "adapter_kind": "claude_code",
                    "provenance": "grove_launched",
                    "primary": True,
                    "workspace_id": "ws-1",
                    "workspace_title": "fix login bug",
                    "workspace_branch": None,
                    "git_branch": "main",
                    "created_at": None,
                    "modified_at": None,
                    "size_bytes": None,
                    "title": None,
                    "first_prompt": None,
                    "last_prompt": None,
                    "activity": None,
                    "cwd": "/projects/demo",
                    "project": {
                        "repo_root": "/projects/demo",
                        "repo_name": "demo",
                        "is_worktree": False,
                        "is_grove_managed": True,
                    },
                    "live": True,
                }
            ),
            SessionSummaryView.model_validate(
                {
                    "session_id": "s-loose",
                    "adapter_kind": "codex",
                    "provenance": "fs_discovered",
                    "workspace_id": None,
                    "workspace_title": None,
                    "workspace_branch": None,
                    "git_branch": None,
                    "created_at": None,
                    "modified_at": None,
                    "size_bytes": None,
                    "title": None,
                    "first_prompt": None,
                    "last_prompt": None,
                    "activity": None,
                    "cwd": "/elsewhere",
                    "project": None,
                }
            ),
        ]

    async def list_projects(self) -> list[ProjectView]:
        self.calls.append(("list_projects", {}))
        # One top-level project (cwd == repo_root) and one nested project whose
        # cwd sits under its enclosing repo — the two shapes a caller must handle.
        return [
            ProjectView(repo_root="/repos/acme-api", repo_name="acme-api", cwd="/repos/acme-api"),
            ProjectView(
                repo_root="/repos/acme-web", repo_name="acme-web", cwd="/repos/acme-web/frontend"
            ),
        ]


@pytest.fixture
def fake_client() -> FakeGroveClient:
    return FakeGroveClient()
