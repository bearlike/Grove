"""IssueOpsEngine — turn a forwarded issue-comment event into a workspace action.

The keystone of the issue-ops layer: one policy object that takes a
normalized :class:`IssueOpsEvent`, decides what it means, and drives the existing
lifecycle seams — reusing ``find_by_ticket`` / ``send_message`` / ``create`` /
``pause`` / ``resume`` / ``kill`` verbatim. Zero new lifecycle logic lives here;
this is routing and gate policy only, with the I/O (manager verbs, reply
comments, the status seam) at the edges.

The pipeline, in order (each drop is terminal):

1. **dedupe** — a bounded LRU on ``(provider, owner, repo, comment_id)``; CI
   retries deliver the same event at-least-once, so a repeat is ``ignored``.
2. **bot / marker** — a bot actor, or a comment carrying Grove's own signature
   marker, is ``ignored`` (a comment-driven bot must never answer itself).
3. **resolve repo** — the target Manager is the one whose provider config's
   ``owner``/``repo`` match the event; no match → ``ignored`` (a bare ticket id is
   ambiguous across repos, so the resolution is always repo+ticket, never a
   fleet-wide scan). The resolved repo's config supplies the trigger, permission
   policy, agent, and prompt template.
4. **parse** — the pure :class:`CommandParser` against the repo's trigger; not
   triggered → ``ignored``.
5. **permission** — write access (forwarder-asserted) or an ``allowed_actors``
   entry; else ``refused`` with a reply.
6. **route** — verb → the matching manager verb; free text → steer a running
   workspace or create one; usage/refusals → a reply comment (never silence).

Every ack/reply write is best-effort: a failed reply or status render is logged
and swallowed, never re-raised into the routing path.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from loguru import logger

from grove.core.contracts.issueops import IssueOpsAction, IssueOpsEvent, IssueOpsOutcome
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.contracts.tickets import TicketSelector
from grove.core.errors import GroveError
from grove.core.issueops.marker import SIGNATURE_MARKER
from grove.core.issueops.parser import CommandParser, ParsedCommand
from grove.core.issueops.prompt import IssuePrompt
from grove.core.workspace import LIVE_STATUSES

if TYPE_CHECKING:
    from grove.core.config import IssueOpsConfig
    from grove.core.contracts.tickets import TicketComment
    from grove.core.manager import WorkspaceManager
    from grove.core.registry import RepoRegistry

# Forge permission strings that count as write-or-above. GitHub emits
# admin/maintain/write/triage/read/none; Gitea admin/write/read/none. "triage"
# and below are NOT write. Matched case-insensitively against the forwarder's
# assertion — a provider-protocol fact, not user policy, so it's fine to name here.
_WRITE_PERMISSIONS: frozenset[str] = frozenset({"write", "admin", "maintain", "owner"})

# How many recent (provider, owner, repo, comment_id) keys the dedupe ring keeps.
# Generous — one comment is one key, so this bounds memory while covering any
# realistic burst of CI retries.
_DEDUPE_CAPACITY = 1024


class _RecentKeys:
    """A bounded, thread-safe LRU set for at-least-once dedupe.

    ``check_and_add`` is atomic: it records the key and reports whether it was
    already present, so concurrent handler threads (the daemon dispatches
    ``handle`` into the executor) can't both treat one comment as fresh. Oldest
    keys evict past ``capacity``.
    """

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._seen: OrderedDict[tuple[str, ...], None] = OrderedDict()
        self._lock = threading.Lock()

    def check_and_add(self, key: tuple[str, ...]) -> bool:
        with self._lock:
            if key in self._seen:
                self._seen.move_to_end(key)
                return True
            self._seen[key] = None
            if len(self._seen) > self._capacity:
                self._seen.popitem(last=False)
            return False


@runtime_checkable
class StatusPublisher(Protocol):
    """The seam the sticky-status publisher hooks to re-render on a ``status`` verb.

    A no-op default ships here so the engine is complete without the publisher;
    the sibling task injects a real one. Called best-effort — a raise is logged
    and swallowed, never surfaced into routing.
    """

    def publish(self, event: IssueOpsEvent, manager: WorkspaceManager) -> None: ...


class _NullStatusPublisher:
    """Default :class:`StatusPublisher`: a ``status`` verb resolves to a no-op."""

    def publish(self, event: IssueOpsEvent, manager: WorkspaceManager) -> None:
        del event, manager


class IssueOpsEngine:
    """Route forwarded issue-comment events to workspace actions.

    Holds the dedupe ring, the pure parser, and the status seam; resolves the
    target Manager (and its per-repo issue-ops config) through the injected
    :class:`RepoRegistry` on each event. ``handle`` is the single entry point and
    returns an :class:`IssueOpsOutcome` the daemon reflects back to the CI action.
    """

    def __init__(
        self,
        *,
        registry: RepoRegistry,
        status_publisher: StatusPublisher | None = None,
        dedupe_capacity: int = _DEDUPE_CAPACITY,
    ) -> None:
        self._registry = registry
        self._status = status_publisher or _NullStatusPublisher()
        self._parser = CommandParser()
        self._recent = _RecentKeys(dedupe_capacity)

    def handle(self, event: IssueOpsEvent) -> IssueOpsOutcome:  # noqa: PLR0911
        """Run one event through the pipeline and report the terminal outcome.

        The early-return count IS the pipeline: each gate (dedupe, bot, marker,
        repo, trigger, permission) is a terminal drop, and collapsing them behind
        a flag would read worse than the guard chain — so PLR0911 is silenced here
        the way ``manager.create`` silences its branch-count lints.
        """
        key = (event.provider, event.owner, event.repo, event.comment_id)
        if self._recent.check_and_add(key):
            return IssueOpsOutcome(action="ignored", code="duplicate")
        if event.actor_is_bot:
            return IssueOpsOutcome(action="ignored", code="bot")
        if SIGNATURE_MARKER in event.comment_body:
            return IssueOpsOutcome(action="ignored", code="signature_marker")

        mgr = self._resolve_manager(event)
        if mgr is None:
            logger.info(
                "issue-ops: no repo resolves for {}:{}/{} — dropping comment {}",
                event.provider,
                event.owner,
                event.repo,
                event.comment_id,
            )
            return IssueOpsOutcome(action="ignored", code="unknown_repo")

        ic = mgr.config.issueops
        command = self._parser.parse(event.comment_body, trigger=ic.trigger)
        if command is None:
            return IssueOpsOutcome(action="ignored", code="not_triggered")

        if not self._permitted(event, ic):
            self._reply(
                mgr,
                event,
                f"@{event.actor} needs repo write access to drive Grove here "
                "(or an `issueops.allowed_actors` entry).",
            )
            return IssueOpsOutcome(action="refused", code="insufficient_permission")

        return self._route(mgr, event, ic, command)

    # ─── resolution & policy ────────────────────────────────────────────────

    def _resolve_manager(self, event: IssueOpsEvent) -> WorkspaceManager | None:
        """The Manager whose provider config's owner/repo match the event, or None.

        Generic over the provider: ``event.provider`` is exactly a field name on
        ``TicketsConfig`` (``gitea``/``github``/``linear``), so one ``getattr``
        reaches the right submodel with no per-provider branching. Linear carries
        no owner/repo, so a Linear event resolves nothing here — issue-ops targets
        the numeric forges. Scans ``known_roots`` (loopback-only, small N).
        """
        for root in self._registry.known_roots():
            mgr = self._registry.get(root)
            provider_cfg = getattr(mgr.config.tickets, event.provider, None)
            if provider_cfg is None or not getattr(provider_cfg, "enabled", False):
                continue
            if (
                getattr(provider_cfg, "owner", None) == event.owner
                and getattr(provider_cfg, "repo", None) == event.repo
            ):
                return mgr
        return None

    @staticmethod
    def _permitted(event: IssueOpsEvent, ic: IssueOpsConfig) -> bool:
        """Write access (forwarder-asserted) OR an ``allowed_actors`` widening entry.

        The default policy is strict (write-or-above); the allowlist only ever
        widens it — it never narrows what write access already grants.
        """
        if event.actor.lower() in {a.lower() for a in ic.allowed_actors}:
            return True
        return event.actor_permission.lower() in _WRITE_PERMISSIONS

    # ─── routing ────────────────────────────────────────────────────────────

    def _route(
        self,
        mgr: WorkspaceManager,
        event: IssueOpsEvent,
        ic: IssueOpsConfig,
        command: ParsedCommand,
    ) -> IssueOpsOutcome:
        if command.kind == "usage":
            self._reply(mgr, event, f"I didn't understand that — {command.reason}.")
            return IssueOpsOutcome(action="refused", code="usage")
        if command.kind == "verb":
            return self._route_verb(mgr, event, str(command.verb))
        return self._route_prompt(mgr, event, ic, command.text)

    def _route_verb(
        self, mgr: WorkspaceManager, event: IssueOpsEvent, verb: str
    ) -> IssueOpsOutcome:
        """A lifecycle verb → the matching manager op, over the ticket's workspace."""
        if verb == "status":
            self._render_status(mgr, event)
            return IssueOpsOutcome(action="status")

        match = mgr.find_by_ticket(event.provider, str(event.issue_number))
        if match is None:
            self._reply(mgr, event, "No Grove workspace is tracking this ticket yet.")
            return IssueOpsOutcome(action="refused", code="no_workspace")

        try:
            if verb == "pause":
                mgr.pause(match.id)
                action: IssueOpsAction = "paused"
            elif verb == "resume":
                mgr.resume(match.id)
                action = "resumed"
            else:  # stop
                mgr.kill(match.id)
                action = "stopped"
        except GroveError as exc:
            self._reply(mgr, event, f"Could not {verb} the workspace: {exc}")
            return IssueOpsOutcome(action="refused", code=f"{verb}_failed", workspace_id=match.id)
        return IssueOpsOutcome(action=action, workspace_id=match.id)

    def _route_prompt(
        self,
        mgr: WorkspaceManager,
        event: IssueOpsEvent,
        ic: IssueOpsConfig,
        text: str,
    ) -> IssueOpsOutcome:
        """Free-text → steer a running workspace for this ticket, else create one."""
        match = mgr.find_by_ticket(event.provider, str(event.issue_number))
        if match is not None and match.status in LIVE_STATUSES:
            try:
                mgr.send_message(match.id, text)
            except GroveError as exc:
                self._reply(mgr, event, f"Could not steer the workspace: {exc}")
                return IssueOpsOutcome(action="refused", code="steer_failed", workspace_id=match.id)
            return IssueOpsOutcome(action="steered", workspace_id=match.id)
        if match is not None:
            self._reply(
                mgr,
                event,
                f"Workspace `{match.id}` for this ticket isn't running "
                f"(`{match.status}`); `{ic.trigger} resume` it first.",
            )
            return IssueOpsOutcome(
                action="refused", code="workspace_not_running", workspace_id=match.id
            )

        request = CreateWorkspaceRequest(
            agent_name=ic.agent,
            title=event.issue_title.strip()[:120] or f"issue-{event.issue_number}",
            ticket=TicketSelector(provider=event.provider, id=str(event.issue_number)),
            initial_prompt=self._render_prompt(mgr, ic, event, text),
        )
        try:
            state = mgr.create(request)
        except GroveError as exc:
            self._reply(mgr, event, f"Could not create a workspace: {exc}")
            return IssueOpsOutcome(action="refused", code="create_failed")
        return IssueOpsOutcome(action="created", workspace_id=state.id)

    def _render_prompt(
        self, mgr: WorkspaceManager, ic: IssueOpsConfig, event: IssueOpsEvent, command_text: str
    ) -> str:
        """Fill the boot-prompt template from the event PLUS the live thread.

        The thread read is best-effort and create-only: it costs one GET on a
        path about to spend minutes provisioning a workspace, and it is what
        makes ``{comments}`` mean the same thing here as on the assignee-pickup
        path — one template with one meaning, rather than a second mechanism.
        """
        return IssuePrompt.render(
            ic.prompt_template,
            number=event.issue_number,
            title=event.issue_title,
            body=event.issue_body,
            url=event.issue_url,
            command_text=command_text,
            comments=IssuePrompt.render_thread(self._thread_comments(mgr, event)),
        )

    @staticmethod
    def _thread_comments(mgr: WorkspaceManager, event: IssueOpsEvent) -> list[TicketComment]:
        """The ticket's comments, or an empty list — never a failed create.

        Swallowed like every other provider read on this path: a tracker that
        will not answer costs the agent context, which is a worse prompt, not a
        broken workspace.
        """
        try:
            return mgr.ticket_providers.get(event.provider).list_comments(str(event.issue_number))
        except GroveError as exc:
            logger.warning("issue-ops could not read the thread for the boot prompt: {}", exc)
            return []

    # ─── best-effort side effects (never re-raise into routing) ─────────────

    def _reply(self, mgr: WorkspaceManager, event: IssueOpsEvent, body: str) -> None:
        """Post a reply comment via the ticket provider — best-effort ack.

        A refusal/usage always gets a reply (never silence); a failure to post it
        (provider not configured, wire error, comments unsupported — all
        ``GroveError``) is logged and swallowed, never surfaced into routing.

        Every reply carries :data:`SIGNATURE_MARKER` (invisible in rendered
        markdown) so the bot's own usage/refusal replies can never re-trigger the
        pipeline via the marker guard — the belt to the bot-actor check's
        suspenders, independent of whether the forge tags the actor as a bot.
        """
        try:
            provider = mgr.ticket_providers.get(event.provider)
            provider.post_comment(str(event.issue_number), f"{body}\n\n{SIGNATURE_MARKER}")
        except GroveError as exc:
            logger.warning(
                "issue-ops reply to comment {} failed (swallowed): {}", event.comment_id, exc
            )

    def _render_status(self, mgr: WorkspaceManager, event: IssueOpsEvent) -> None:
        """Trigger the status re-render seam — best-effort."""
        try:
            self._status.publish(event, mgr)
        except Exception as exc:  # best-effort seam: isolate any publisher failure
            logger.warning(
                "issue-ops status render for comment {} failed (swallowed): {}",
                event.comment_id,
                exc,
            )


__all__ = ["IssueOpsEngine", "StatusPublisher"]
