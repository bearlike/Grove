"""The unauthenticated public-share reader.

One class, holding the whole answer to "what may somebody without a Grove
session read, and how do they name it". The daemon's ``/public`` routes are thin
shells over it, which is what keeps that answer in a single place rather than
spread across three route bodies where the fourth one to be added would forget
a rule.

Why a separate namespace at all, rather than teaching the bearer dependency to
recognise a share token and letting ``/workspaces/{id}/…`` serve both: a path
prefix is a boundary both the reader and the route decorator can see. Every
route under ``/workspaces`` carries ``auth_dep`` and always will; a route added
there next year is private without anybody remembering a rule. Under a
scoped-principal scheme the same new route would be one allowlist entry away
from world-readable, and that allowlist lives somewhere its author is not
looking. The prefix costs three route declarations. It is worth it.

Everything here is a READ. There is deliberately no verb, no write, no steer and
no stream: ``/events`` is a cross-project fan-out carrying every workspace on the
host, so it can never be exposed, and the transcript's existing ``after_turn``
cursor already gives a follower cheap freshness without one.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path

from grove.core.activity import ActivityService
from grove.core.contracts.activity import SessionActivityView
from grove.core.contracts.phase import PhaseView
from grove.core.contracts.public import (
    PublicActivityView,
    PublicGroveView,
    PublicPeekView,
    PublicSharedWorkspaceView,
    PublicWorkspaceView,
)
from grove.core.contracts.sessions import SessionDetailView
from grove.core.contracts.tickets import TicketRef
from grove.core.contracts.views import WorkspaceDiffView
from grove.core.errors import GroveError
from grove.core.manager import WorkspaceManager
from grove.core.registry import RepoRegistry
from grove.core.sessions import SessionExplorer, SessionListing
from grove.core.workspace import WorkspaceState
from grove.daemon._public_tickets import _PublicTicketMemo
from grove.daemon._turns import turn_window


class ShareNotFound(GroveError):
    """No shared workspace answers to this token.

    ONE error for every way a lookup can fail — unknown token, a workspace since
    unshared, a token for a record that has been killed. A caller must not be
    able to tell those apart: distinguishing them turns the link into an oracle
    answering questions about workspaces the caller holds no token for.
    """


@dataclass(frozen=True, slots=True)
class PublicWorkspaceReader:
    """Resolves a share token, then answers the three reads a shared page makes.

    Constructed per request via :meth:`for_token`, so the resolved workspace is
    established exactly once and every read below is already scoped to it. There
    is deliberately NO method here that takes a workspace id — which is what
    makes it structurally impossible for a route to read one workspace while
    holding another's token.

    Every method blocks (git, transcript parsing), so every caller runs it off
    the event loop. That is the route's job, not this class's: putting the
    offload in here would need an ``async`` seam on what is otherwise a pure
    reader, and would hide from the route the one fact its author most needs.
    """

    manager: WorkspaceManager
    state: WorkspaceState
    registry: RepoRegistry
    activity: ActivityService
    version: str
    ticket_memo: _PublicTicketMemo

    @classmethod
    def for_token(
        cls,
        token: str,
        *,
        registry: RepoRegistry,
        activity: ActivityService,
        version: str,
        ticket_memo: _PublicTicketMemo,
    ) -> PublicWorkspaceReader:
        """Bind a reader to whatever workspace ``token`` names.

        Raises :class:`ShareNotFound` for every failure, deliberately without
        saying which.
        """
        resolved = registry.resolve_share(token)
        if resolved is None:
            raise ShareNotFound("no shared workspace for this link")
        manager, state = resolved
        return cls(
            manager=manager,
            state=state,
            registry=registry,
            activity=activity,
            version=version,
            ticket_memo=ticket_memo,
        )

    def overview(self) -> PublicWorkspaceView:
        """Everything the shared page renders except the transcript and the diff.

        ONE payload rather than the four requests the authenticated surface
        makes (peek, commits, phase, activity), because this is the thing the
        public page POLLS — it has no SSE to ride. Four polls would be four
        times the work for one answer.
        """
        peek = self.manager.peek(self.state.id)
        sessions = self.activity.sessions_for(self.manager, self.state)
        report = self.manager.phase(self.state.id)
        return PublicWorkspaceView(
            peek=PublicPeekView.from_peek(peek, ticket_refs=self._ticket_refs(peek.state)),
            activity=PublicActivityView(
                sessions=[SessionActivityView.from_session_activity(s) for s in sessions],
                phase=PhaseView.from_report(report) if report is not None else None,
            ),
            session_id=self.manager.shared_session_id(self.state),
            session_pinned=self.state.share_session_id is not None,
            shared=self._shared(),
            grove=PublicGroveView(version=self.version),
        )

    def turns(self, *, last: int | None, after_turn: int | None) -> SessionDetailView | None:
        """The workspace's primary transcript, windowed.

        THE SESSION IS NOT A PARAMETER, and that is the point. The token names a
        workspace; the daemon picks the session. A caller therefore has no
        coordinate to tamper with, and the "which session" question — a real one
        on the authenticated route, with sub-agent threads and remap candidates
        — does not exist out here at all.

        **It picks it through the SAME resolver :meth:`overview` names it with**
        (``WorkspaceManager.shared_session_id``), and that is a correctness
        requirement rather than tidiness. This once took ``for_workspace()[0]``
        — an mtime-ordered listing — while ``overview`` reported the workspace's
        own primary. The two disagree whenever another transcript in the scan
        cwd is newer, which under ROOT placement is the ordinary case, because
        every workspace in a repo scans the shared repo root: on the reference
        host all three shared workspaces of one project named their own session
        and served a fourth, unshared one. A recorded pin now answers first, and
        the listing is SELECTED BY ID rather than indexed — so a session this
        workspace does not own can no longer reach a public reader by being
        recently written.

        ``None`` means this workspace has no readable transcript yet, which is a
        legitimate state for a freshly-created workspace somebody shared early —
        and for a pin whose transcript has not materialized — not an error.
        """
        session_id = self.manager.shared_session_id(self.state)
        if session_id is None:
            return None
        explorer = SessionExplorer(self.manager)
        listing: SessionListing | None = next(
            (
                ls
                for ls in explorer.for_workspace(self.state.id)
                if ls.summary.session_id == session_id
            ),
            None,
        )
        if listing is None:
            return None
        window = turn_window(explorer.turns_for(listing), last=last, after_turn=after_turn)
        return SessionDetailView.from_listing_turns(
            listing,
            window.turns,
            total_turns=window.total,
            first_turn_index=window.first_index,
            incremental=window.incremental,
        )

    def _resolve_ticket(self, ref: TicketRef) -> TicketRef:
        provider = self.manager.ticket_providers.get(ref.provider)
        return (
            provider.get_pull_request(ref.id)
            if ref.kind == "pull_request"
            else provider.get_ticket(ref.id)
        )

    def _ticket_refs(self, state: WorkspaceState) -> list[TicketRef]:
        """Resolve the shared workspace's stored refs, preserving bare failures.

        Resolution belongs after share-token scoping, never in a browser route:
        the daemon chooses the existing refs and spends its credential only on
        those. Each returned ref is constructed field-by-field, so a future
        ``TicketRef`` field cannot cross this boundary by accident.
        """
        refs: list[TicketRef] = []
        repo_root = Path(state.repo_root)
        for ref in state.ticket_refs:
            resolved = self.ticket_memo.resolve(
                repo_root=repo_root,
                ref=ref,
                fetch=partial(self._resolve_ticket, ref),
            )
            refs.append(
                TicketRef(
                    provider=ref.provider,
                    id=ref.id,
                    kind=ref.kind,
                    title=resolved.title if resolved is not None else ref.title,
                    # A self-hosted tracker's URL embeds its internal hostname,
                    # so ticket enrichment must not make it public.
                    url=None,
                    status=resolved.status if resolved is not None else ref.status,
                    ambiguous=ref.ambiguous,
                )
            )
        return refs

    def diff(self, *, path: str | None) -> WorkspaceDiffView:
        """The working-tree patch — git's own output, bounded, never parsed.

        Identical in scope and bounding to the authenticated route. The patch is
        the changed CODE, which is the thing a shared link exists to show;
        withholding part of it would make the Changes tab lie rather than make
        anything safer.
        """
        return WorkspaceDiffView.from_diff(self.manager.working_diff(self.state.id, path=path))

    def _shared(self) -> list[PublicSharedWorkspaceView]:
        """Every shared workspace in this repo, INCLUDING the one being viewed.

        This filtered self out at first, on the reasoning that a list containing
        the page you are already on is a rendering decision no client should
        have to re-make. That was wrong, and the rail proved it: it showed every
        shared workspace in the project except the one you were reading, so
        there was no "you are here", the count disagreed with every other
        surface by one, and a project with a single shared workspace rendered an
        empty list on a page that self-evidently was one.

        **Membership is this method's question; which row is CURRENT is the
        client's**, and the client already holds the token needed to answer it.
        Deciding the second question here is what cost the first one.
        """
        return [
            PublicSharedWorkspaceView.from_state(s)
            for s in self.registry.shared_in(Path(self.state.repo_root))
        ]
