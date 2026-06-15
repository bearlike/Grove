"""The notification event + the channel contract.

Two atomic types, one concern each:

- :class:`Notification` is the tool-agnostic *what happened* — a workspace's
  agent crossed into a state that wants the human. It owns its own shape: how it
  is built from the activity IR (:meth:`from_activity`), how a state reads in
  English (:meth:`reason_for`), and how it renders to a headline / body
  (:meth:`title` / :meth:`body`). Every channel receives this same object, so a
  new channel never re-derives any of it.
- :class:`NotificationChannel` is the *where it goes* — the abstract contract
  every sink (Gotify, webhook, and tomorrow email / Slack / Teams) implements.
  One required action, :meth:`deliver`. Capabilities a given sink lacks raise
  ``NotImplementedError`` from their base definition rather than widening the
  contract — subclasses override only what they support.

``Notification`` is a plain frozen dataclass, not Pydantic: in-process IR that
travels from the engine out to an external sink, never back across a Grove client
boundary (the config that configures channels *is* Pydantic — it crosses the
cascade). See CLAUDE.md, "Pydantic at public-contract boundaries; plain dataclass
for in-process state".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from grove.core.agents import AgentActivityState

if TYPE_CHECKING:
    from grove.core.activity import SessionActivity, WorkspaceActivity


@dataclass(slots=True, frozen=True)
class Notification:
    """One human-worthy edge: an agent session entered an attention state.

    Carries the facts a channel needs to render and deep-link, plus a
    pre-resolved ``reason`` phrase so every channel renders the same words.
    ``title`` / ``body`` compose once here rather than in each channel — code as
    poem, not N formatters that drift.
    """

    # state → English, for the body. The keys ARE the notifiable set: a state
    # absent here can never fire (the broker intersects its config against it),
    # so adding a notifiable state is a one-line edit with no other coupling.
    REASONS: ClassVar[dict[AgentActivityState, str]] = {
        AgentActivityState.WAITING: "finished its turn",
        AgentActivityState.BLOCKED: "needs your input",
        AgentActivityState.ERROR: "hit an error",
        AgentActivityState.IDLE: "went idle",
    }

    workspace_id: str
    workspace_title: str
    repo_name: str
    branch: str
    agent_name: str
    state: AgentActivityState
    reason: str
    summary: str | None
    deep_link: str | None
    occurred_at: datetime

    @classmethod
    def reason_for(cls, state: AgentActivityState) -> str | None:
        """The English phrase for a notifiable state, or ``None`` if not notifiable."""
        return cls.REASONS.get(state)

    @classmethod
    def from_activity(
        cls,
        row: WorkspaceActivity,
        session: SessionActivity,
        *,
        state: AgentActivityState,
        deep_link_base: str,
        now: datetime,
    ) -> Notification:
        """Build a notification from one activity row + the session that edged.

        ``state`` is the already-validated notifiable state the broker detected;
        ``reason_for`` is therefore non-``None`` by construction.
        """
        ws = row.state
        reason = cls.reason_for(state)
        assert reason is not None  # broker only builds for notifiable states
        task = session.activity.current_task
        return cls(
            workspace_id=ws.id,
            workspace_title=ws.title,
            repo_name=Path(ws.repo_root).name or ws.repo_root,
            branch=ws.branch,
            agent_name=ws.agent_name,
            state=state,
            reason=reason,
            summary=task if task and task.strip() else None,
            deep_link=f"{deep_link_base}/w/{ws.id}" if deep_link_base else None,
            occurred_at=now,
        )

    def title(self) -> str:
        """The headline — repo + workspace, the at-a-glance identity."""
        return f"{self.repo_name} · {self.workspace_title}"

    def body(self) -> str:
        """The body — who did what, plus the task line when we have it."""
        lead = f"{self.agent_name} {self.reason}"
        return f"{lead}\n{self.summary}" if self.summary else lead


class NotificationChannel(ABC):
    """Abstract delivery sink — the contract every channel implements.

    The surface is intentionally narrow: delivering a notification is the one
    action every sink shares, so :meth:`deliver` is the sole abstract method and
    a rich :class:`Notification` carries everything a sink could render. When a
    future capability (attachments, threading, read-receipts) genuinely diverges
    across channels, add it here with a base body that raises
    ``NotImplementedError`` — sinks that support it override, the rest inherit the
    refusal. That keeps "what a channel can do" in one readable contract instead
    of capability flags scattered across call sites.

    ``deliver`` MAY raise; the broker wraps every call in its best-effort guard
    (bounded timeout upstream, structured per-channel log, never re-raised into
    the activity path), so a dead sink degrades that one delivery and nothing
    else. ``name`` is the label used in those logs.
    """

    name: ClassVar[str]

    @abstractmethod
    def deliver(self, notification: Notification) -> None:
        """Deliver one notification. Best-effort; may raise (the broker catches)."""

    def close(self) -> None:  # noqa: B027 — deliberate overridable no-op, not a missing abstract
        """Release held resources (an HTTP pool). No-op default for stateless sinks."""
