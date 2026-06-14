"""Tool handlers — one method per MCP tool, all speaking through GroveClient.

``GroveTools`` is the seam tests pin: handlers in, contract Views out,
with the injected ``GroveClient`` as the only authority consulted. The
daemon owns every decision (placement gates, provenance defaults, status
reconciliation); this layer only shapes requests and bounds responses.

Method docstrings double as the MCP tool descriptions an agent reads
when deciding what to call — keep them action-first and explicit about
side effects.
"""

from __future__ import annotations

from pathlib import Path

from grove.client import GroveClient, ProtocolError
from grove.core.contracts import (
    AutoBranch,
    BranchPlan,
    CreateWorkspaceRequest,
    WorkspacePeekView,
    WorkspaceStateView,
)
from grove.mcp.models import (
    AttachInstructionResult,
    KillWorkspaceResult,
    SendMessageResult,
)

# Shared frozen instance, not a call-in-default (B008): the model is
# immutable so one instance serves every call safely.
_AUTO_BRANCH = AutoBranch()


class GroveTools:
    """The Phase 1 tool surface (issues #1/#6), bound to one ``GroveClient``."""

    SNAPSHOT_CAP = 4096
    """Max chars of pane snapshot returned by peek — mirrors the contracts
    package's ~4 KB bounded-text rule (trailing ellipsis is the trim signal),
    so one busy pane can never flood an MCP client's context window."""

    def __init__(self, client: GroveClient) -> None:
        self._client = client

    # ─── read tools ──────────────────────────────────────────────────────────

    async def list_workspaces(self) -> list[WorkspaceStateView]:
        """List every Grove workspace the daemon knows, across all repos.

        Each entry carries the stable workspace id, title, repo root,
        branch and base branch, worktree path, tmux session, agent name,
        lifecycle status, and timestamps.
        """
        return await self._client.list_workspaces()

    async def get_workspace(self, workspace_id: str) -> WorkspaceStateView:
        """Get the full state of one workspace by its id."""
        return await self._client.get_workspace(workspace_id)

    async def peek_workspace(self, workspace_id: str) -> WorkspacePeekView:
        """Get a bounded snapshot of a workspace: git ahead/behind vs base,
        diff added/removed lines, dirty file count, recent commits, and the
        agent pane's recent terminal output (capped, read-only)."""
        peek = await self._client.peek(workspace_id)
        snap = peek.agent_snapshot
        if snap is not None and len(snap) > self.SNAPSHOT_CAP:
            peek = peek.model_copy(update={"agent_snapshot": snap[: self.SNAPSHOT_CAP - 1] + "…"})
        return peek

    async def attach_instruction(self, workspace_id: str) -> AttachInstructionResult:
        """Get the tmux command a human runs to attach to a workspace's
        session — for handing live control of an agent over to a person."""
        instr = await self._client.get_attach(workspace_id)
        return AttachInstructionResult(
            workspace_id=workspace_id,
            tmux_session=instr.tmux_session,
            command=f"tmux attach -t {instr.tmux_session}",
            inside_outer_tmux=instr.inside_outer_tmux,
        )

    # ─── lifecycle tools ─────────────────────────────────────────────────────

    async def create_workspace(
        self,
        repo_root: str,
        title: str,
        agent_name: str,
        description: str | None = None,
        branch_plan: BranchPlan = _AUTO_BRANCH,
        skip_init: bool = False,
        initial_prompt: str | None = None,
    ) -> WorkspaceStateView:
        """Create a Grove workspace: a git worktree plus a tmux session
        running the named agent. ``branch_plan`` defaults to ``auto`` (Grove
        names a fresh branch off HEAD); other kinds: ``new_named``,
        ``existing_local``, ``track_remote``, or ``root`` (run in the repo
        root on the current branch, no worktree). ``initial_prompt`` is the
        agent's first task, delivered race-free as the session boots so the
        workspace starts working immediately instead of idling at the prompt —
        omit it to boot the agent idle (then drive it with
        ``grove_send_workspace_message``). Returns the created workspace's
        state including its stable id.
        """
        req = CreateWorkspaceRequest(
            agent_name=agent_name,
            title=title,
            description=description,
            branch_plan=branch_plan,
            skip_init=skip_init,
            initial_prompt=initial_prompt,
            repo_root=Path(repo_root),
        )
        return await self._client.create_workspace(req)

    async def pause_workspace(self, workspace_id: str, force: bool = False) -> WorkspaceStateView:
        """Pause a workspace: remove its worktree and tmux session but keep
        the branch. Refuses a dirty worktree unless ``force`` is true —
        Grove never auto-commits, so uncommitted work would be lost."""
        return await self._client.pause(workspace_id, force=force)

    async def resume_workspace(self, workspace_id: str) -> WorkspaceStateView:
        """Resume a paused workspace: recreate the worktree from its branch
        and relaunch the tmux session and agent."""
        return await self._client.resume(workspace_id)

    async def respawn_workspace(self, workspace_id: str) -> WorkspaceStateView:
        """Recreate the tmux session of an offline workspace whose worktree
        still exists — the recovery verb for a vanished tmux session. Use
        resume for paused workspaces instead."""
        return await self._client.respawn(workspace_id)

    async def kill_workspace(self, workspace_id: str, delete_branch: bool) -> KillWorkspaceResult:
        """Destroy a workspace: tmux session and worktree are removed.
        ``delete_branch`` is required and explicit — true also deletes the
        local branch (never remote ones); false keeps it. The engine always
        preserves the branch for root-placement and user-attached branches
        regardless of this flag.
        """
        # No default on delete_branch by design: a destructive tool must
        # never guess. The daemon accepts null (provenance decides); the MCP
        # surface forces the caller to state intent.
        await self._client.kill(workspace_id, delete_branch=delete_branch)
        return KillWorkspaceResult(workspace_id=workspace_id, delete_branch_requested=delete_branch)

    # ─── steering ────────────────────────────────────────────────────────────

    async def send_workspace_message(self, workspace_id: str, text: str) -> SendMessageResult:
        """Send a natural-language steering message to the workspace's agent
        (typed into its tmux pane). Returns ``status="sent"`` on success or
        ``status="unavailable"`` when the connected Grove daemon does not
        support messaging yet — treat that as a missing capability, not an
        error."""
        try:
            await self._client.send_message(workspace_id, text)
        except ProtocolError as exc:
            # 404 is ambiguous: an envelope code like "workspace_not_found"
            # is a real caller error and must propagate; a bare framework
            # 404/405 (code "http_error") means the route itself is absent —
            # the daemon predates issue #37's endpoint.
            if exc.code == "http_error" and exc.status in (404, 405):
                return SendMessageResult(
                    status="unavailable",
                    workspace_id=workspace_id,
                    detail=(
                        "this Grove daemon does not support workspace messaging; "
                        "upgrade the daemon to a version with POST /workspaces/{id}/message"
                    ),
                )
            raise
        return SendMessageResult(status="sent", workspace_id=workspace_id)


__all__ = ["GroveTools"]
