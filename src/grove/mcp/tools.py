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

import shlex
from pathlib import Path

from grove.client import GroveClient, ProtocolError
from grove.core.contracts import (
    AgentSummaryView,
    AutoBranch,
    BranchPlan,
    CreateWorkspaceRequest,
    HostAttachView,
    ProjectView,
    SessionSummaryView,
    TicketProviderName,
    WorkspacePeekView,
    WorkspaceStateView,
)
from grove.core.contracts.activity import DashboardSnapshotView
from grove.core.contracts.phase import PhaseView
from grove.core.contracts.sessions import TodoListView
from grove.core.phase import TaskPhase
from grove.mcp.models import (
    AttachInstructionResult,
    KillWorkspaceResult,
    SendMessageResult,
)

# Shared frozen instance, not a call-in-default (B008): the model is
# immutable so one instance serves every call safely.
_AUTO_BRANCH = AutoBranch()


class GroveTools:
    """The MCP tool surface, bound to one ``GroveClient``."""

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

    async def list_projects(self) -> list[ProjectView]:
        """List every project Grove is configured to work in. Start here when
        you have no repo path yet: each row's `repo_root` is the value
        grove_list_agents, grove_list_workspaces, and grove_create_workspace
        take. `repo_name` is for display, and `cwd` differs from `repo_root`
        only for a nested project inside a larger repo. Read-only."""
        return await self._client.list_projects()

    async def list_sessions(
        self, repo_root: str | None = None, limit: int = 50
    ) -> list[SessionSummaryView]:
        """List coding-agent sessions newest-first — with no arguments, every
        session on this host, including ones no Grove workspace ever launched
        and repos Grove does not manage. Use it to answer "what has been running
        on this machine, and where". `repo_root` narrows to one project.

        Each row carries the session id, agent kind, the directory it ran in,
        the repo that directory belongs to, its git branch, when it was created
        and last written, whether a matching agent process is running in that
        directory right now (`live`), and the Grove workspace that owns it (or
        null). Host-scoped rows are metadata-only: `activity`, `size_bytes`, and
        the prompt/title fields are null there — narrow with `repo_root` to get
        them. Read-only.
        """
        return await self._client.list_sessions(
            repo=Path(repo_root) if repo_root is not None else None, limit=limit
        )

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

    async def get_fleet_status(self) -> DashboardSnapshotView:
        """Get the live status of EVERY workspace in one call — the read to poll
        when you are supervising more than one at a time. Per workspace it
        carries the full state, the reported task-phase, todo counts, git
        ahead/behind and diff counts, recent commits, and — per agent session —
        what the agent is doing right now: working / waiting / idle / blocked,
        `needs_attention`, the current task, any question it is asking you, and
        token counts. Read-only.

        Prefer this over calling grove_get_workspace_phase or grove_peek_workspace
        once per workspace: those answer for one workspace, this answers for all
        of them, and it is the only read that reports whether an agent is waiting
        on you. It returns every project the daemon serves, so a large fleet
        returns a large result — for a single workspace, keep using the
        per-workspace tools."""
        return await self._client.get_activity()

    async def get_workspace_phase(self, workspace_id: str) -> PhaseView | None:
        """Get a workspace's reported task-phase: how far through its task the
        agent says it is (scoping, planning, implementing, verifying,
        delivering, done), plus an optional one-line note and when it was
        last updated. Returns null when the agent has not reported a phase
        yet — a fleet-health signal distinct from "reported scoping"."""
        return await self._client.get_phase(workspace_id)

    async def get_workspace_todo(self, workspace_id: str) -> TodoListView:
        """Get a workspace's current todo/checklist list, as driven by the
        agent's own TodoWrite/update_plan tool calls. An empty list means no
        todo tool has been called yet, not an error. Read-only."""
        return await self._client.get_todo(workspace_id)

    async def attach_instruction(self, workspace_id: str) -> AttachInstructionResult:
        """Get the command a human runs to attach to a workspace's agent
        session — for handing live control of an agent over to a person.
        Containerized workspaces return the command that enters the
        container's own tmux, and no host session name."""
        instr = await self._client.get_attach(workspace_id)
        host = instr if isinstance(instr, HostAttachView) else None
        return AttachInstructionResult(
            workspace_id=workspace_id,
            tmux_session=host.tmux_session if host is not None else None,
            command=shlex.join(instr.attach_argv()),
            inside_outer_tmux=host is not None and host.inside_outer_tmux,
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
        runtime: str | None = None,
        brief: bool | None = None,
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
        a hint before choosing one. ``runtime`` is ``"host"`` or
        ``"container"``; omit it to use the configured default
        (``container.enabled``). Create-time only, never editable — a
        workspace that falls back from container to host names the reason on
        ``WorkspaceStateView.runtime_fallback_reason``; promote it later with
        ``grove_respawn_workspace``. ``brief`` hands the new agent Grove's
        first-turn brief, a short note pointing it at the ``working-in-grove``
        skill so it reports its task phase and keeps its attached tickets
        current; omit it to use the configured default (on). Returns the
        created workspace's state including its stable id.
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
            runtime=runtime,
            brief=brief,
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

    async def attach_ticket(self, workspace_id: str, ref: str) -> WorkspaceStateView:
        """Attach an issue or pull request to a workspace — the one-call verb
        for an agent that just opened a PR and wants to link it to the
        workspace that made it. ``ref`` is whatever you already have: a pasted
        URL (``.../owner/repo/pull/42`` or ``.../owner/repo/issues/42``), a
        bare ``#42``/``42``, or ``owner/repo#42``. You never need to know or
        guess which tracker (Gitea/GitHub/Linear) the repo uses — provider AND
        issue-vs-PR are both inferred from ``ref``, server-side. If a bare,
        unqualified id could belong to more than one enabled tracker, this
        raises asking you to qualify with a full URL or ``owner/repo#id``
        instead of guessing. Idempotent: attaching the same ticket again, or
        re-attaching to correct a wrong issue/PR kind, is a no-op / in-place
        fix, never a duplicate. Returns the workspace's updated state,
        including ``ticket_refs`` (also visible via ``grove_get_workspace``).
        """
        return await self._client.attach_ticket_by_ref(workspace_id, ref)

    async def detach_ticket(self, workspace_id: str, ref: str) -> WorkspaceStateView:
        """Remove an issue/PR association from a workspace. ``ref`` accepts
        the same shapes as ``grove_attach_ticket`` (URL / ``#42`` /
        ``owner/repo#42``) and resolves the same way, so whatever reference
        you attached with also detaches it. Idempotent — detaching a ticket
        that was never attached is a no-op. Returns the workspace's updated
        state."""
        return await self._client.detach_ticket_by_ref(workspace_id, ref)

    async def set_workspace_phase(
        self,
        workspace_id: str,
        phase: TaskPhase,
        note: str | None = None,
        *,
        blocked: bool = False,
        ticket: str | None = None,
    ) -> PhaseView:
        """Set or correct a workspace's task-phase claim from outside the
        agent — one of ``scoping``, ``planning``, ``implementing``,
        ``verifying``, ``delivering``, ``done``. The agent working inside the
        workspace normally reports its own phase by writing the file named
        in its ``GROVE_PHASE_FILE`` environment variable (the one channel
        that reaches it in every runtime, including a container, and keyed
        per agent so co-resident agents never overwrite each other); use
        this tool to set or fix the claim as an outside operator instead.
        ``note`` is an optional one-line detail, capped at 200 characters.
        ``ticket`` names one attached ticket to report against, e.g.
        ``"gitea:498"`` — omit it to target the workspace's own claim.
        ``blocked`` means you cannot finish this yourself; say why in
        ``note``."""
        return await self._client.set_phase(
            workspace_id, phase, note, blocked=blocked, ticket=ticket
        )

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
        error. Any other failure raises with the reason in its message; a
        **timeout means delivery is UNKNOWN, not failed** — the daemon may
        still deliver it, so peek at the workspace before re-sending rather
        than blind-retrying a message the agent may already have."""
        try:
            await self._client.send_message(workspace_id, text)
        except ProtocolError as exc:
            # 404 is ambiguous: an envelope code like "workspace_not_found"
            # is a real caller error and must propagate; a bare framework
            # 404/405 (code "http_error") means the route itself is absent —
            # the daemon predates the message endpoint.
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
