"""Wire shapes for the unauthenticated public share view.

These are the ONLY shapes that ever reach a caller Grove has not authenticated,
which makes this module a security boundary rather than a convenience layer. It
holds one rule, and the rule is the whole reason the module exists:

**Every field here is written out by hand. Nothing is derived by subtracting
fields from an authenticated view.**

An allowlist fails closed. A field added to ``WorkspaceStateView`` tomorrow —
another host path, another container identity, another token — is private by
default and stays private until somebody deliberately types its name into
``PublicWorkspaceStateView.from_state``. A denylist ("serialize the state, then
drop these five keys") fails open, and it fails silently: the leak ships with
the feature that introduced the field, and no test that exists today would see
it.

Three things are therefore deliberately absent, and are worth naming so nobody
re-adds them as an oversight:

- ``repo_root`` / ``worktree_path`` / ``tmux_session`` — host-private paths and
  handles. The repo's BASENAME crosses instead, as ``project``: a reader needs
  to know which codebase they are looking at, and that is a different question
  from where it lives on somebody's disk.
- ``container`` — an image tag, a container id and a mount table describe the
  host's infrastructure, not the work.
- ``share_token`` — the credential itself. The caller already holds the one they
  used; echoing it back, or shipping any other, is free exposure.

Ticket enrichment happens daemon-side, behind a short, single-flight cache keyed
by repo root as well as provider and id — ``(provider, id)`` alone is not an
identity, since two repos number their issues independently, and a
daemon-global key would serve one project's private ticket title to another
project's public link. The browser never selects a ticket to resolve, so an
anonymous reader cannot spend the host's tracker credential on a request of
their choosing.

Each ref is rebuilt field by field, and TWO of its fields are deliberately
withheld:

- ``assignee`` — a third party's identity, which is not the sharer's to publish.
- ``url`` — a resolved url is the tracker's own ``html_url``, so on a
  self-hosted forge it embeds that forge's hostname: host-private
  infrastructure, and unreachable for an external reader anyway, so the link
  would both leak and fail. ``title`` and ``status`` carry the information the
  reader actually wants.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from grove.core.contracts.activity import SessionActivityView
from grove.core.contracts.phase import PhaseView
from grove.core.contracts.tickets import TicketRef
from grove.core.contracts.views import CommitSummaryView
from grove.core.workspace import (
    Placement,
    Runtime,
    WorkspacePeek,
    WorkspaceState,
    WorkspaceStatus,
)


class PublicWorkspaceStateView(BaseModel):
    """A workspace as an unauthenticated reader may see it.

    The hand-written allowlist this module's docstring describes. Field names
    match ``WorkspaceStateView`` wherever they overlap, on purpose: the browser
    renders both through the SAME components, which is only possible while a
    public payload structurally satisfies the shape those components read.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    description: str | None = None
    status: WorkspaceStatus
    branch: str
    base_branch: str
    #: The commit the workspace started from. A SHA already in the repository,
    #: so it is not host-private — and the Changes tab needs it to say honestly
    #: which range it measured: null means no fork point was recorded, which is
    #: what makes the commit list time-bounded rather than exact.
    base_commit: str | None = None
    agent_name: str
    created_at: datetime
    updated_at: datetime
    paused_at: datetime | None = None
    placement: Placement = Placement.WORKTREE
    runtime: Runtime = Runtime.HOST
    runtime_fallback_reason: str | None = None
    runtime_default_config: bool = False
    ticket_refs: list[TicketRef] = []
    #: The repo's directory NAME, never its path. "Grove", not
    #: "/home/someone/Projects/Grove" — enough to say which codebase this is,
    #: and nothing about the machine it sits on.
    project: str

    @classmethod
    def from_state(
        cls, s: WorkspaceState, *, ticket_refs: list[TicketRef] | None = None
    ) -> PublicWorkspaceStateView:
        return cls(
            id=s.id,
            title=s.title,
            description=s.description,
            status=s.status,
            branch=s.branch,
            base_branch=s.base_branch,
            base_commit=s.base_commit,
            agent_name=s.agent_name,
            created_at=s.created_at,
            updated_at=s.updated_at,
            paused_at=s.paused_at,
            placement=s.placement,
            runtime=s.runtime,
            runtime_fallback_reason=s.runtime_fallback_reason,
            runtime_default_config=s.runtime_default_config,
            ticket_refs=[
                TicketRef(
                    provider=ref.provider,
                    id=ref.id,
                    kind=ref.kind,
                    title=ref.title,
                    # Never crosses — see the module docstring. Nulled here as
                    # well as in the reader's resolving projection, so both
                    # paths into this view withhold it rather than one relying
                    # on the other having done it.
                    url=None,
                    status=ref.status,
                    ambiguous=ref.ambiguous,
                )
                for ref in (ticket_refs if ticket_refs is not None else s.ticket_refs)
            ],
            project=Path(s.repo_root).name,
        )


class PublicPeekView(BaseModel):
    """The working-tree read, shaped like ``WorkspacePeekView`` minus the pane.

    Field-for-field with the authenticated peek except that ``state`` is the
    narrowed view above and the two pane fields are gone — a public reader may
    not see the terminal, and the surest way to enforce that is for the pane
    never to be in a shape they can receive.
    """

    model_config = ConfigDict(frozen=True)

    state: PublicWorkspaceStateView
    base_ahead: int
    base_behind: int
    diff_added: int
    diff_removed: int
    dirty_files: int
    recent_commits: list[CommitSummaryView]

    @classmethod
    def from_peek(
        cls, p: WorkspacePeek, *, ticket_refs: list[TicketRef] | None = None
    ) -> PublicPeekView:
        return cls(
            state=PublicWorkspaceStateView.from_state(p.state, ticket_refs=ticket_refs),
            base_ahead=p.base_ahead,
            base_behind=p.base_behind,
            diff_added=p.diff_added,
            diff_removed=p.diff_removed,
            dirty_files=p.dirty_files,
            recent_commits=[CommitSummaryView.from_summary(c) for c in p.recent_commits],
        )


class PublicActivityView(BaseModel):
    """What the agent is doing, for a reader who cannot steer it.

    Two fields, because two are what the shared surface renders: the session
    rows behind the activity figures, and the task phase. ``WorkspaceActivityView``
    is NOT reused — it embeds a whole ``WorkspaceStateView``, so shipping it
    would hand over every host path this module exists to withhold, through a
    field nobody would think to look at.

    ``pane_target`` is absent for the same reason the pane is absent from
    ``PublicPeekView``.
    """

    model_config = ConfigDict(frozen=True)

    sessions: list[SessionActivityView] = []
    phase: PhaseView | None = None


class PublicSharedWorkspaceView(BaseModel):
    """One shared workspace in the repo, as the public rail lists it.

    **This list INCLUDES the workspace being viewed**, which is why it is not
    called `siblings` — a sibling list excludes self by definition, and the name
    would then be a lie about the payload. It first shipped self-excluding, and
    the result was a rail that showed every shared workspace in the project
    except the one you were reading: the reader cannot see where they are, the
    count is off by one against any other surface, and a project with exactly
    one shared workspace renders an empty list on a page that plainly is one.
    Membership is the caller's answer; which row is current is the client's, and
    it already has the token to work that out.

    Each row carries a token, which is the point and is worth stating plainly:
    sharing a workspace makes it reachable from every other shared workspace in
    its repo. That is the requested behaviour rather than an oversight, and the
    containment is that an unshared workspace has no token and therefore cannot
    appear here at all.
    """

    model_config = ConfigDict(frozen=True)

    token: str
    title: str
    status: WorkspaceStatus
    branch: str
    updated_at: datetime

    @classmethod
    def from_state(cls, s: WorkspaceState) -> PublicSharedWorkspaceView:
        # Only ever called for a state the caller already filtered on
        # `share_token`; the assert-free `or ""` keeps mypy honest without
        # inventing a failure mode the caller cannot produce.
        return cls(
            token=s.share_token or "",
            title=s.title,
            status=s.status,
            branch=s.branch,
            updated_at=s.updated_at,
        )


class PublicGroveView(BaseModel):
    """Who made the thing you are looking at.

    A shared link is the one Grove surface reached by people who have never
    installed it, so it is also the only one that has to introduce itself. The
    version comes from the running daemon rather than a constant in the browser
    bundle, for the reason ``/api/version`` already argues: a number baked into
    the front end describes the bundle, which can be a different release from
    the process that answered.
    """

    model_config = ConfigDict(frozen=True)

    version: str
    #: The public project. A constant rather than config: this names Grove
    #: itself, not the user's repo, so there is nothing here for an operator to
    #: vary and a knob would only ever be a way to get it wrong.
    repo_url: str = "https://github.com/bearlike/Grove"
    #: One sentence, lifted from the project README so the two cannot drift into
    #: two different descriptions of one product.
    tagline: str = (
        "The terminal workspace manager for AI coding agents. Spin up a forest "
        "of isolated agent workspaces. Reach any of them asynchronously from "
        "your terminal, your browser, or another agent."
    )


class PublicWorkspaceView(BaseModel):
    """The whole public overview — one request, everything the page renders.

    One route rather than the four the authenticated surface uses (peek,
    commits, phase, activity), because this payload is POLLED: the public view
    has no SSE (``/events`` is a cross-project fan-out carrying every workspace
    on the host and can never be exposed), so freshness here is one cheap
    repeated GET. Four of them per tick would be four times the work for one
    answer.

    ``session_id`` is resolved server-side and handed over, so the transcript
    route never has to accept a session id from an unauthenticated caller — the
    token names the workspace and the daemon picks the session, which means
    there is no coordinate for a caller to tamper with.
    """

    model_config = ConfigDict(frozen=True)

    peek: PublicPeekView
    activity: PublicActivityView
    #: The session the ``/turns`` route will serve — resolved through the ONE
    #: seam that route also uses, so the id named here and the transcript
    #: actually rendered cannot describe two different conversations.
    session_id: str | None = None
    #: Whether ``session_id`` came from the pin recorded when this link was
    #: issued, or was derived from the workspace's current primary session. A
    #: reader outside the auth boundary cannot check the workspace themselves,
    #: so the page has to be able to say which of the two it is showing:
    #: "the session this link was shared from" is a different promise from
    #: "whatever this workspace is doing now", and only one of them is stable.
    #: It leaks nothing — the id it qualifies already crosses.
    session_pinned: bool = False
    #: Every shared workspace in this repo INCLUDING this one — see
    #: :class:`PublicSharedWorkspaceView` for why self is not filtered out here.
    shared: list[PublicSharedWorkspaceView] = []
    grove: PublicGroveView
