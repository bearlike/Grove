"""The notification event + the channel contract.

Four atomic types, one concern each:

- :class:`WorkspaceIdentity` is *who this is about* — the workspace facts every
  notification renders (repo, title, branch, agent). Split out because the
  lifecycle trigger has no activity row to read them from: the broker caches one
  of these per workspace and a lifecycle push renders from the cache.
- :class:`NotificationReason` pairs the English phrase with the urgency. One
  table entry answers both "how does this read" and "how loudly does it land",
  so the two can never drift apart per trigger.
- :class:`Notification` is the tool-agnostic *what happened*. It owns its own
  shape: three constructors (one per trigger — an agent-state edge, a question,
  a workspace lifecycle event) and three renderers (:meth:`title`, :meth:`body`,
  :meth:`markdown`). Every channel receives this same object, so a new channel
  re-derives nothing.
- :class:`NotificationChannel` is the *where it goes* — the abstract contract
  every sink (Gotify, webhook, and tomorrow email / Slack / Web Push)
  implements. One required action, :meth:`deliver`.

**Severity is the channel-agnostic urgency axis** — the seam that keeps channels
dumb. The event decides how much it matters (a pending question is ``high``, a
routine pause is ``low``); each channel maps that onto its own scale (Gotify's
0-10 integer, ntfy's 1-5) in *its* config, never here. Without it every channel
would re-implement "is this important", keyed off the state enum — four sinks,
four drifting policies.

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
from typing import TYPE_CHECKING, ClassVar, Literal

from grove.core.agents import AgentActivityState, AgentQuestion

if TYPE_CHECKING:
    from grove.core.activity import SessionActivity, WorkspaceActivity
    from grove.core.workspace import WorkspaceState

NotificationSeverity = Literal["low", "normal", "high", "urgent"]
"""Channel-agnostic urgency. Each sink maps it onto its own scale in config."""

NotificationTrigger = Literal["agent_state", "question", "lifecycle"]
"""Which edge produced this notification — the three the broker detects."""


@dataclass(slots=True, frozen=True)
class NotificationReason:
    """Why we are pushing: the English phrase plus how loudly it should land."""

    phrase: str
    severity: NotificationSeverity


@dataclass(slots=True, frozen=True)
class WorkspaceIdentity:
    """The workspace facts every notification renders, independent of trigger.

    A lifecycle delta carries no activity row (only an id + the event detail), so
    the broker caches the identity it learns from activity deltas and renders
    lifecycle pushes against it. :meth:`unresolved` is the honest fallback for a
    workspace that errored before it ever reported activity.
    """

    workspace_id: str
    title: str
    repo_name: str
    branch: str
    agent_name: str

    @classmethod
    def from_state(cls, ws: WorkspaceState) -> WorkspaceIdentity:
        """The identity of a workspace we have an activity row for."""
        return cls(
            workspace_id=ws.id,
            title=ws.title,
            repo_name=Path(ws.repo_root).name or ws.repo_root,
            branch=ws.branch,
            agent_name=ws.agent_name,
        )

    @classmethod
    def unresolved(
        cls, workspace_id: str, *, repo_root: str | None, detail: dict[str, str]
    ) -> WorkspaceIdentity:
        """Best-effort identity for a workspace the broker has never seen active.

        A create that fails at ``worktree_add`` emits its error before any
        activity poll observes it. The lifecycle detail carries ``title``/
        ``agent`` on ``created``; everything else falls back to a short id, which
        still deep-links correctly. Never raises — a thin notification beats none.
        """
        return cls(
            workspace_id=workspace_id,
            title=detail.get("title") or workspace_id[:8],
            repo_name=Path(repo_root).name if repo_root else "",
            branch=detail.get("branch", ""),
            agent_name=detail.get("agent", ""),
        )

    def headline(self) -> str:
        """The at-a-glance identity: repo + workspace (the notification title)."""
        return f"{self.repo_name} · {self.title}" if self.repo_name else self.title


@dataclass(slots=True, frozen=True)
class Notification:
    """One human-worthy edge in a workspace: agent state, question, or lifecycle.

    Carries the facts a channel needs to render and deep-link, plus a resolved
    ``reason`` phrase and ``severity`` so every channel says the same words at the
    same volume. ``title`` / ``body`` / ``markdown`` compose once here rather than
    in each channel — code as poem, not N formatters that drift.
    """

    # state → (English, urgency), for the agent-state trigger. The keys ARE the
    # notifiable set: a state absent here can never fire (the broker intersects
    # its config against it), so adding a notifiable state is a one-line edit.
    REASONS: ClassVar[dict[AgentActivityState, NotificationReason]] = {
        AgentActivityState.WAITING: NotificationReason("finished its turn", "normal"),
        AgentActivityState.BLOCKED: NotificationReason("needs your input", "high"),
        AgentActivityState.ERROR: NotificationReason("hit an error", "high"),
        AgentActivityState.IDLE: NotificationReason("went idle", "low"),
    }

    # Lifecycle event kind (``WorkspaceEvent.kind``, bridged onto the delta's
    # ``detail["event"]``) → (English, urgency). Same contract: the keys are the
    # notifiable set. Deliberately covers the *interruption* events plus the
    # routine verbs — config picks which ones actually push, so a user who wants
    # a buzz on every create can have one without a code change.
    LIFECYCLE: ClassVar[dict[str, NotificationReason]] = {
        "error": NotificationReason("hit a workspace error", "high"),
        "orphaned_detected": NotificationReason("was orphaned — its worktree is gone", "high"),
        "offline_detected": NotificationReason("went offline — its session vanished", "normal"),
        "created": NotificationReason("was created", "low"),
        "killed": NotificationReason("was killed", "low"),
        "paused": NotificationReason("was paused", "low"),
        "resumed": NotificationReason("was resumed", "low"),
        "respawned": NotificationReason("was respawned", "low"),
    }

    # The question trigger is one reason, not a table — a question always means
    # the same thing and always wants the human. Phrased at render (plural).
    QUESTION: ClassVar[NotificationReason] = NotificationReason("asked you a question", "high")

    workspace: WorkspaceIdentity
    trigger: NotificationTrigger
    event: str
    """Machine label a channel tags with: the state, ``question``, or the event kind."""

    reason: str
    severity: NotificationSeverity
    state: AgentActivityState | None
    """The agent's state, when one is known. ``None`` for a lifecycle push."""

    summary: str | None
    questions: tuple[AgentQuestion, ...]
    deep_link: str | None
    occurred_at: datetime

    @property
    def workspace_id(self) -> str:
        """Delegated so channels and logs read one flat address, not a path."""
        return self.workspace.workspace_id

    # ─── construction, one classmethod per trigger ───────────────────────────

    @classmethod
    def reason_for(cls, state: AgentActivityState) -> NotificationReason | None:
        """The reason for a notifiable agent state, or ``None`` if not notifiable."""
        return cls.REASONS.get(state)

    @classmethod
    def lifecycle_reason_for(cls, event: str) -> NotificationReason | None:
        """The reason for a notifiable lifecycle event, or ``None`` if not notifiable."""
        return cls.LIFECYCLE.get(event)

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
        """An agent-state edge: the session crossed into a notifiable state.

        ``state`` is the already-validated notifiable state the broker detected,
        so ``reason_for`` is non-``None`` by construction. Pending questions ride
        along when the state is BLOCKED — the same edge, rendered richer.
        """
        reason = cls.reason_for(state)
        assert reason is not None  # broker only builds for notifiable states
        return cls(
            workspace=WorkspaceIdentity.from_state(row.state),
            trigger="agent_state",
            event=state.value,
            reason=reason.phrase,
            severity=reason.severity,
            state=state,
            summary=cls._summary_of(session, state),
            questions=cls.unanswered(session),
            deep_link=cls._deep_link(deep_link_base, row.state.id),
            occurred_at=now,
        )

    @classmethod
    def for_questions(
        cls,
        row: WorkspaceActivity,
        session: SessionActivity,
        *,
        questions: tuple[AgentQuestion, ...],
        deep_link_base: str,
        now: datetime,
    ) -> Notification:
        """A question edge: the agent posted question(s) the human has not answered.

        Deliberately independent of the state axis. A question is a *content*
        signal every adapter can produce (a Claude ``AskUserQuestion``, a Codex
        MCP-bridged equivalent), while BLOCKED is a *state* only some adapters
        ever reach — so pushing off the question itself is what gives every
        harness the same "the agent needs you" notification.
        """
        plural = "" if len(questions) == 1 else f" ({len(questions)} questions)"
        return cls(
            workspace=WorkspaceIdentity.from_state(row.state),
            trigger="question",
            event="question",
            reason=f"{cls.QUESTION.phrase}{plural}",
            severity=cls.QUESTION.severity,
            state=session.activity.state,
            summary=cls._summary_of(session),
            questions=questions,
            deep_link=cls._deep_link(deep_link_base, row.state.id),
            occurred_at=now,
        )

    @classmethod
    def for_lifecycle(
        cls,
        identity: WorkspaceIdentity,
        *,
        event: str,
        detail: dict[str, str],
        deep_link_base: str,
        now: datetime,
    ) -> Notification:
        """A lifecycle edge: the workspace itself changed (error, kill, orphan…).

        ``event`` is the already-validated notifiable kind the broker detected.
        The detail map is the manager's own event payload; its ``phase``/``error``
        keys are what make a failure diagnosable from the phone.
        """
        reason = cls.lifecycle_reason_for(event)
        assert reason is not None  # broker only builds for notifiable events
        return cls(
            workspace=identity,
            trigger="lifecycle",
            event=event,
            reason=reason.phrase,
            severity=reason.severity,
            state=None,
            summary=cls._detail_summary(detail),
            questions=(),
            deep_link=cls._deep_link(deep_link_base, identity.workspace_id),
            occurred_at=now,
        )

    # ─── rendering ───────────────────────────────────────────────────────────

    def title(self) -> str:
        """The headline — repo + workspace, the at-a-glance identity."""
        return self.workspace.headline()

    def body(self) -> str:
        """Plain-text body, for sinks with no rich rendering."""
        lines = [f"{self.workspace.agent_name or 'agent'} {self.reason}".strip()]
        if self.summary:
            lines.append(self.summary)
        lines.extend(self._question_lines(bullet="-", emphasis=""))
        return "\n".join(lines)

    def markdown(self) -> str:
        """Rich body — the same facts, marked up for a client that renders them.

        Gotify (Android via Markwon, web via react-markdown) and ntfy both render
        CommonMark; HTML is not honored anywhere, so this stays pure markdown.
        The deep link is a trailing link rather than a bare URL so it reads as a
        sentence on a client that renders it, and still copy-pastes on one that
        does not.
        """
        lines = [f"**{self.workspace.agent_name or 'agent'}** {self.reason}".strip()]
        if self.summary:
            lines.append(f"\n> {self.summary}")
        question_lines = self._question_lines(bullet="-", emphasis="**")
        if question_lines:
            lines.append("")
            lines.extend(question_lines)
        footer = f"`{self.workspace.branch}`" if self.workspace.branch else ""
        if self.deep_link:
            link = f"[Open workspace]({self.deep_link})"
            footer = f"{footer} · {link}" if footer else link
        if footer:
            lines.append(f"\n{footer}")
        return "\n".join(lines)

    def tags(self) -> tuple[str, ...]:
        """Machine labels for sinks that filter or badge (ntfy's ``tags``)."""
        return (self.event, self.severity)

    # ─── internal ────────────────────────────────────────────────────────────

    @staticmethod
    def unanswered(session: SessionActivity) -> tuple[AgentQuestion, ...]:
        """The session's still-open questions — the single definition of "pending".

        Shared by the broker's question-edge detector and by the renderers, so
        "which questions does the human still owe an answer to" is decided once.
        """
        return tuple(q for q in session.activity.questions if not q.answered)

    def _question_lines(self, *, bullet: str, emphasis: str) -> list[str]:
        """One block per open question: the prompt, then its options."""
        lines: list[str] = []
        for question in self.questions:
            head = question.header or "Question"
            lines.append(f"{emphasis}{head}{emphasis} — {question.prompt}".strip())
            lines.extend(
                f"{bullet} {opt.label}" + (f" — {opt.description}" if opt.description else "")
                for opt in question.options
            )
        return lines

    @staticmethod
    def _summary_of(
        session: SessionActivity, state: AgentActivityState | None = None
    ) -> str | None:
        """The one line under the headline: *why it broke*, else *what it was doing*.

        On an ERROR edge the adapter's ``error_detail`` is the whole point of the
        push, and ``current_task`` beside the word "error" is actively misleading —
        it names the work in flight, not the failure. Everywhere else the task line
        is the useful one. Gated on the edge's own state rather than on
        ``error_detail`` merely being set, because a session that has since
        recovered can still carry a stale detail, and a finished turn must not read
        as a failure.
        """
        activity = session.activity
        if state is AgentActivityState.ERROR and activity.error_detail:
            return activity.error_detail
        task = activity.current_task
        return task if task and task.strip() else None

    @staticmethod
    def _detail_summary(detail: dict[str, str]) -> str | None:
        """A lifecycle event's payload as one line — the manager's own phase/error."""
        phase, error = detail.get("phase"), detail.get("error")
        if phase and error:
            return f"{phase}: {error}"
        return error or phase or None

    @staticmethod
    def _deep_link(base: str, workspace_id: str) -> str | None:
        """The one place a workspace URL is derived. Empty base → no link."""
        return f"{base}/w/{workspace_id}" if base else None


class NotificationChannel(ABC):
    """Abstract delivery sink — the contract every channel implements.

    The surface is intentionally narrow: delivering a notification is the one
    action every sink shares, so :meth:`deliver` is the sole abstract method and
    a rich :class:`Notification` carries everything a sink could render — the
    plain body, the markdown body, the severity, the open questions, the deep
    link. A sink renders what it supports and ignores the rest; it never
    re-derives a phrase, a priority, or a URL. When a future capability
    (attachments, threading, read-receipts) genuinely diverges across channels,
    add it here with a base body that raises ``NotImplementedError`` — sinks that
    support it override, the rest inherit the refusal. That keeps "what a channel
    can do" in one readable contract instead of capability flags scattered across
    call sites.

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
