"""Tool-result shapes that have no existing contract View.

Most tool outputs reuse ``grove.core.contracts`` Views directly
(``WorkspaceStateView``, ``WorkspacePeekView``); these models cover the
two operations whose daemon response carries no body (kill → 204) or
whose availability is itself the answer (message → capability probe).
Every field is explicit status data, never prose an MCP client would
have to parse.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

_FROZEN = ConfigDict(extra="forbid", frozen=True)


class KillWorkspaceResult(BaseModel):
    """Outcome of ``grove_kill_workspace`` — the daemon returns 204, so the
    MCP layer echoes what was asked for retry-safe bookkeeping.

    ``delete_branch_requested`` is the *request*, not a guarantee: the
    engine overrides it to False for root-placement workspaces and never
    touches remote branches.
    """

    model_config = _FROZEN

    status: Literal["killed"] = "killed"
    workspace_id: str
    delete_branch_requested: bool


class SendMessageResult(BaseModel):
    """Outcome of ``grove_send_workspace_message``.

    ``status="unavailable"`` means the connected daemon predates the
    message endpoint — the capability is discoverable as structured status
    data instead of surfacing a raw HTTP error the caller would have to
    interpret.
    """

    model_config = _FROZEN

    status: Literal["sent", "unavailable"]
    workspace_id: str
    detail: str | None = None


class AttachInstructionResult(BaseModel):
    """Human-handoff payload for ``grove_attach_instruction``.

    ``command`` is ready to paste into a shell on the daemon's host, whatever
    the workspace's runtime: ``tmux attach`` for a host workspace, and the
    ``devcontainer exec … tmux`` that enters the container's own tmux for a
    containerized one. ``inside_outer_tmux`` warns that the user is
    already in a tmux client and should switch (``tmux switch-client``) rather
    than nest.

    ``tmux_session`` is null for a containerized workspace and that is the
    honest answer rather than a gap: the session it would name lives on the
    container's tmux server, so a name pasted at a host shell addresses nothing.
    """

    model_config = _FROZEN

    workspace_id: str
    tmux_session: str | None
    command: str
    inside_outer_tmux: bool


__all__ = [
    "AttachInstructionResult",
    "KillWorkspaceResult",
    "SendMessageResult",
]
