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
    AgentSummaryView,
    AutoBranch,
    BranchPlan,
    CreateWorkspaceRequest,
    TicketProviderName,
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

    async def list_workspaces(
        self,
        repo_root: str | None = None,
        ticket_provider: TicketProviderName | None = None,
        ticket_id: str | None = None,
    ) -> list[WorkspaceStateView]:
        """List Grove workspaces the daemon knows. With no filters, every
        workspace across all repos. `repo_root` scopes to one repo (like
        `grove_list_agents`). `ticket_provider`/`ticket_id` (give both
        together) narrow to the single workspace already tracking that
        ticket — use this to check "does a workspace exist for this ticket"
        before creating a new one — scoped to `repo_root` when given, else
        across every repo.

        Each entry carries the stable workspace id, title, repo root,
        branch and base branch, worktree path, tmux session, agent name,
        lifecycle status, and timestamps.
        """
        return await self._client.list_workspaces(
            repo=Path(repo_root) if repo_root is not None else None,
            ticket_provider=ticket_provider,
            ticket_id=ticket_id,
        )

    async def get_workspace(self, workspace_id: str) -> WorkspaceStateView:
        """Get the full state of one workspace by its id."""
        return await self._client.get_workspace(workspace_id)

    async def list_agents(self, repo_root: str) -> list[AgentSummaryView]:
        """List the agents available for a repo — the valid `agent_name`
        values for grove_create_workspace — each with its offered `models`
        (up to 10) for the optional `model` argument. `model` is a hint, not a
        whitelist: any id the tool understands is accepted. Read-only."""
        return await self._client.list_agents(Path(repo_root))

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
        resume_session_id: str | None = None,
        model: str | None = None,
    ) -> WorkspaceStateView:
        """Create a Grove workspace: a git worktree plus a tmux session
        running the named agent. ``branch_plan`` defaults to ``auto`` (Grove
        names a fresh branch off HEAD); other kinds: ``new_named``,
        ``existing_local``, ``track_remote``, or ``root`` (run in the repo
        root on the current branch, no worktree). ``initial_prompt`` is the
        agent's first task, delivered race-free as the session boots so the
        workspace starts working immediately instead of idling at the prompt —
        omit it to boot the agent idle (then drive it with
        ``grove_send_workspace_message``). ``resume_session_id`` continues an
        EXISTING agent session in the new workspace instead of starting fresh
        (claude launches ``--resume``, codex ``resume <id>``); only claude_code
        and codex agents support it, and it accepts a full session id or a
        unique id prefix scoped to the project (an unknown/ambiguous/wrong-kind
        ref fails before any side effect). ``model`` is forwarded VERBATIM to
        the agent tool — claude/codex receive it as ``--model <id>``, mewbo
        applies it server-side, and a generic agent ignores it; Grove never
        interprets the id, so ANY value the tool understands is accepted, not
        just a fixed list. Omit it to use the tool's own default model. Call
        ``grove_list_agents`` to see each agent's offered models (up to 10) as
        a hint before choosing one. Returns the created workspace's state
        including its stable id.
        """
        req = CreateWorkspaceRequest(
            agent_name=agent_name,
            title=title,
            description=description,
            branch_plan=branch_plan,
            skip_init=skip_init,
            initial_prompt=initial_prompt,
            resume_session_id=resume_session_id,
            model=model,
            repo_root=Path(repo_root),
        )
        return await self._client.create_workspace(req)

    async def remap_workspace_session(
        self, workspace_id: str, session_ref: str
    ) -> WorkspaceStateView:
        """Pin an existing agent session as a workspace's tracked primary session.
        Use this to correct which session the dashboard follows — e.g. after
        ``/clear`` rotated the agent's session id, or to adopt a hand-started
        session as the workspace's own. ``session_ref`` is a session id or a
        unique id-prefix within the workspace's project (see
        ``grove sessions list``). Trusted and idempotent; returns the updated
        workspace state.
        """
        return await self._client.remap_session(workspace_id, session_ref)

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
