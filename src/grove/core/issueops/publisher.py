"""The live sticky issue-comment publisher.

``TicketStatusPublisher`` mirrors a workspace's progress onto every ticket it
names — typically the issue AND the pull request that resolves it — as ONE sticky
comment per thread, rebuilt from live state on every update and identical on every
thread (a reader of either wants the same answer: how far along, what remains).

It is the activity bus's third subscriber, alongside ``_SseHub`` and
``NotificationBroker`` — but it deliberately does NOT ride the notification
broker. The broker is an
edge-triggered, debounced push for *human attention* (WAITING/BLOCKED/ERROR); the
publisher needs the opposite granularity — the continuous WORKING-state todo churn
that never crosses an attention edge — so it subscribes to the same bus and reads
the whole story, not just the edges.

It copies the broker's *discipline* wholesale (see
``notifications/CLAUDE.md``):

- **bind(subscribe)** — the third subscriber to ``ActivityService.subscribe``.
- **pure decision / I/O dispatch split** — :meth:`observe` folds one delta into
  per-workspace coalescing state (fast, on the poll thread); :meth:`dispatch`
  does the forge round-trip off-thread. :meth:`render` is a pure function over a
  :class:`PublishSnapshot` with zero I/O.
- **coalescing, not debouncing** — the broker drops flapping edges; the publisher
  *folds* every change and flushes the merged result at most once per window
  (forges rate-limit same-comment PATCH storms — see
  ``tickets/CLAUDE.md``). Terminal states flush immediately and then stop.
- **best-effort isolation** — every provider call is wrapped; a forge failure is
  logged and swallowed at the call site (the swallow obligation lives with
  the caller), never re-raised into the activity poll path.

All forge writes go through the Wave-1 ``TicketProvider`` comment I/O
(``list_comments`` / ``post_comment`` / ``edit_comment``); there is no direct
``httpx`` here.
"""

from __future__ import annotations

import contextlib
import re
import textwrap
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING
from urllib.parse import quote, urlencode

from loguru import logger

from grove.core.activity import DashboardDelta, WorkspaceActivity
from grove.core.agents import AgentActivityState, TodoItem, TodoList
from grove.core.contracts.phase_palette import DARK_PHASE_HEX
from grove.core.contracts.tickets import TicketKind, TicketProviderName
from grove.core.errors import GroveError, TicketCommentsUnsupported, TicketProviderError
from grove.core.issueops.marker import SIGNATURE_MARKER, STICKY_MARKER
from grove.core.phase import PHASE_ORDER, PhaseReport
from grove.core.tickets.provider import TicketProvider
from grove.core.workspace import CommitSummary

if TYPE_CHECKING:
    from grove.core.config import IssueOpsConfig
    from grove.core.contracts.issueops import IssueOpsEvent
    from grove.core.contracts.tickets import TicketRef
    from grove.core.manager import WorkspaceManager
    from grove.core.registry import RepoRegistry

# The seams the publisher reaches the engine through — narrow callables so a test
# drives the whole pipeline with an in-memory fake, and ``from_config`` binds them
# to a live ``RepoRegistry``. ``ProviderResolver`` returns ``None`` to route past a
# workspace whose provider isn't issue-ops-enabled (skip silently); ``TodoResolver``
# returns ``None`` when the workspace has no todo yet (a real answer, not an error).
# ``PhaseResolver`` mirrors ``TodoResolver`` exactly — ``None`` means the agent has
# not reported a phase yet, a real answer rather than a miss.
ProviderResolver = Callable[[str, TicketProviderName], "TicketProvider | None"]
TodoResolver = Callable[[str, str], "TodoList | None"]
PhaseResolver = Callable[[str, str], "PhaseReport | None"]
# ``TaskResolver`` is the UNCAPPED task text. ``WorkspaceActivity`` already
# carries ``current_task``, but every adapter truncates that field to 500
# characters because it rides the ~1 Hz activity delta for every workspace on
# the host — correct there, a pure loss here, where the comment renders it once
# per flush behind a fold. So the wire field stays capped and this per-request
# seam answers whole, the same split the todo axis already makes (counts on the
# tick, the full list behind a per-request read). ``None`` means "no task text
# yet", a real answer; the capped field is the fallback.
TaskResolver = Callable[[str, str], "str | None"]
# Is ANY live workspace still holding one of these refs? Asked only when a
# workspace ends, so the finale can say whether the ticket has been dropped
# rather than only that this workspace stopped — a fact the publisher cannot
# know about itself, since its own record is deleted by then and a sibling
# workspace is somebody else's row. ``None`` (no resolver) renders nothing.
HolderResolver = Callable[[str, "Sequence[TicketRef]"], bool]
# Can a transcript still be READ by the session surface, given the coordinates a
# reader would arrive with — ``(kind, cwd, session_id)``? Deliberately a probe
# rather than a URL builder: the bytes surviving on disk and the transcript being
# REACHABLE are different facts (a container workspace writes under a pinned
# config dir the host-wide scan cannot see), and a link to a page that 404s
# spends the reader's click. ``False`` renders the id as plain text plus a
# statement that it is out of reach.
TranscriptProbe = Callable[[str, str, str], bool]

# One publish target's stable identity — (provider name, ticket id). The sticky
# comment id is remembered under this, so a workspace mirroring onto an issue and
# its pull request keeps two independent sticky comments.
_TargetKey = tuple[TicketProviderName, str]

# State glyph + label for the status line. Presentation only (the render owns how a
# state reads), the todo sibling of the notifications ``REASONS`` phrasing.
_STATE_GLYPH: dict[AgentActivityState, str] = {
    AgentActivityState.STARTING: "🌱",
    AgentActivityState.WORKING: "🔨",
    AgentActivityState.WAITING: "🔔",
    AgentActivityState.BLOCKED: "⛔",
    AgentActivityState.IDLE: "💤",
    AgentActivityState.ERROR: "❌",
    AgentActivityState.UNKNOWN: "❔",
}
_STATE_LABEL: dict[AgentActivityState, str] = {
    AgentActivityState.STARTING: "Starting",
    AgentActivityState.WORKING: "Working",
    AgentActivityState.WAITING: "Waiting for review",
    AgentActivityState.BLOCKED: "Needs input",
    AgentActivityState.IDLE: "Idle",
    AgentActivityState.ERROR: "Error",
    AgentActivityState.UNKNOWN: "Unknown",
}

# Phase labels, the todo sibling of ``_STATE_LABEL`` above — a fixed table over
# the closed ``TaskPhase`` vocabulary rather than a ``.capitalize()`` call, so a
# vocabulary change is caught here (a missing key falls back to the raw value).
_PHASE_LABEL: dict[str, str] = {
    "scoping": "Scoping",
    "planning": "Planning",
    "implementing": "Implementing",
    "verifying": "Verifying",
    "delivering": "Delivering",
    "done": "Done",
}

# How an agent kind announces itself in the summary table. The profile name is
# the operator's word for the same thing, so the row shows both only when they
# say different things (a profile literally named "codex" adds nothing).
_AGENT_KIND_LABEL: dict[str, str] = {
    "claude_code": "Claude Code",
    "codex": "Codex",
    "mewbo": "Mewbo",
    "generic": "Generic",
}

# ─── the icon vocabulary ─────────────────────────────────────────────────────
#
# ONE icon per concept, used in exactly one place each, and never decorative:
# a row label and its section title share an icon so a reader who collapses a
# section can still find its summary in the table. This EXTENDS the two
# vocabularies the comment already had — the state glyphs above and the phase
# dots — rather than introducing a second, competing one.
#
# They also earn their place on accessibility rather than costing it: Gitea
# renders every emoji as ``<span class="emoji" aria-label="...">``, so a screen
# reader announces "compass Phase" where a bare table cell announced "Phase".
# That is why the icon leads the label instead of replacing it — an icon that
# REPLACES a word has no readable fallback, which is the same rule the
# container statusline's ASCII vocabulary follows.
_ICON_STATE = "🚦"
_ICON_PHASE = "🧭"
_ICON_CHECKLIST = "☑️"
_ICON_AGENT = "🤖"
_ICON_BRANCH = "🌿"
_ICON_COMMIT = "📌"
_ICON_WORKSPACE = "🖥️"
_ICON_UPDATED = "🕒"
_ICON_ACTIVITY = "💬"
_ICON_TRACKING = "🔗"
_ICON_SESSION = "🧾"

# The one ink used for every node label and for the current node's ring.
#
# Contrast is a SOLVED problem here rather than a per-node judgement, because
# every fill in ``DARK_PHASE_HEX`` is light: measured against this ink the worst
# case in the whole palette is ``delivering`` (#5f9c0f) at **5.6:1**, and the
# muted ``done`` gray (#96938c) sits at 6.2:1 — both clear WCAG AA for normal
# text. White is the trap it looks like the answer to: it reaches only 3.4:1 on
# ``delivering`` and 3.1:1 on the gray, failing AA on exactly the two fills a
# reader would assume needed it. So: one dark ink, no per-class exception.
#
# Every node therefore carries an EXPLICIT fill and an EXPLICIT label color,
# which is what makes the diagram theme-independent. A node left unfilled would
# inherit the forge's own page background — white in light mode, near-black in
# dark — and no single label color can be legible on both.
_NODE_INK = "#111111"

# What the CURRENT node takes when its own ramp entry is the muted ``done`` gray
# — i.e. at the final phase, where "current" and "completed" would otherwise be
# the same colour and only the ring would say which node the workspace is on.
#
# The ramp's deepest LIVE entry (``delivering``, "converging, handing off"), so
# the diagram still climbs to its current node and no hex is invented. Measured
# against :data:`_NODE_INK` at **5.6:1** — the palette's worst case and still
# clear of WCAG AA; white on it reaches only 3.4:1, which is why the ink does
# not move with the fill.
_ACTIVE_FILL_AT_DONE = DARK_PHASE_HEX[PHASE_ORDER[-2]]

_TICKET_TTL = timedelta(seconds=60)
"""How long one enriched ticket ref is reused before the forge is asked again.

Enrichment is one GET per ref per flush, and a working agent flushes about once
per ``update_window_seconds`` (5 s) — so an unmemoized read spends a few thousand
API calls an hour *per workspace* against budgets counted in thousands (GitHub
allows 5000/hr authenticated), and buys nothing: a title never moves and a
ticket's state changes on a human timescale. Memoized at the READER, the shape
``ContainerLiveness`` already uses for ``docker inspect`` on the reconcile path.
"""

_TASK_WRAP_WIDTH = 88
"""Column the activity excerpt is hard-wrapped to inside its fenced block.

A fenced block does not reflow — it scrolls sideways — so an unwrapped excerpt
makes the reader drag a scrollbar to read a sentence. Wrapping is therefore a
readability decision the render owns, not a bound on the text: nothing is cut."""

_TAG_MARKUP = re.compile(r"</?[A-Za-z][^<>]*>")
"""Tag-shaped constructs in agent-written task text.

``current_task`` is a transcript excerpt, so it carries whatever harness-internal
markup the agent's own protocol uses (``<teammate-message teammate_id=… >``).
Stripping is not cosmetic: both forges' HTML sanitizers drop an unknown tag
anyway, so the rendered comment ALREADY loses it — leaving it in only makes the
stored body disagree with what a reader sees, and an unbalanced ``<`` swallows
the text after it. Stripping the construct here makes source and render agree.

Deliberately shape-based (a tag-looking construct), never a list of known tag
names: the provider-boundary rule says normalize shape, and a vendor's tag
vocabulary is exactly the semantics an adapter must not learn."""

_DIAGRAM_NOTE_CAP = 48
"""Diagram-layout bound on the agent's note, well under ``phase.NOTE_CAP``.

A horizontal flowchart is as wide as its widest node, so a 200-character note on
the current phase would stretch the whole chart past any comment column while
its five neighbours stayed word-sized. The full note is never lost: the phase
caption line below the diagram carries it uncut."""

# Characters that end a mermaid node label early, or start a construct inside
# one, mapped to the numeric/named entity codes mermaid decodes back to the
# original glyph. ``str.translate`` substitutes each source character exactly
# once in a single pass, so the ``#`` this table emits is NOT itself re-escaped
# — which is the whole reason ``#`` can safely sit in the table beside the
# entities that begin with it.
_MERMAID_ENTITIES = str.maketrans(
    {
        "#": "#35;",
        '"': "#quot;",
        "<": "#60;",
        ">": "#62;",
        "[": "#91;",
        "]": "#93;",
        "(": "#40;",
        ")": "#41;",
        "{": "#123;",
        "}": "#125;",
        "|": "#124;",
        "`": "#96;",
    }
)


@dataclass(slots=True, frozen=True)
class SessionLink:
    """One agent session the comment names, and whether it can be reached.

    ``url`` is the session surface's own address, built ONLY where the probe says
    the transcript is still readable — the `_link` rule applied to a destination
    that outlives its workspace. ``reachable`` is therefore not "does a url
    exist" restated: a session with no url is one a reader cannot get to, which
    the render says out loud rather than dressing the id as a link.
    """

    session_id: str
    kind: str
    url: str | None
    primary: bool

    @property
    def short(self) -> str:
        """The id a human can compare against a `grove sessions` listing."""
        return self.session_id[:8]


@dataclass(slots=True, frozen=True)
class PublishSnapshot:
    """The pure render input — everything one status comment shows, no I/O behind it.

    Built from one ``WorkspaceActivity`` row plus the separately-resolved todo and
    phase, so :meth:`TicketStatusPublisher.render` is a total function of this
    dataclass and fully unit-testable. ``terminal`` swaps the live status body for
    a final summary (outcome + branch + link), after which the publisher stops.
    ``phase`` is ``None`` whenever the agent has never written a phase file — that
    absence renders as nothing, never a placeholder line (an agent that doesn't
    report phase looks exactly as it did before this axis existed).

    ``tickets`` is the workspace's whole ref list — the same refs routing turns
    into publish targets, ENRICHED at dispatch so each carries the ``title`` its
    tracking line leads with. It reaches the render so the comment can name its
    siblings (the issue this PR resolves, the PR resolving this issue), which is
    the one genuinely cross-target thing a reader wants and the one thing a
    per-target ``kind`` could never supply.

    ``repo_label`` names the repository beside the branch: the provider's own
    ``owner/repo`` scope where it has one, else the repo directory's name. A
    branch name alone is ambiguous the moment a reader is looking at more than
    one project — which is exactly what reading this comment on a tracker is.

    ``agent_name``/``agent_kind`` are the workspace's configured profile and the
    adapter kind behind it, both persisted at create. They say WHO did the work
    and never the model: a model identifier dates the comment and invites
    conclusions the comment cannot support.

    ``branch_url`` / ``commit_url`` are resolved at dispatch too, from the first
    eligible target's provider — the render stays pure, and a tracker that fronts
    no repo (or an unscoped one) yields ``None``, which renders as plain text.
    **A link is only ever built where a destination exists**: a link that lands
    nowhere spends the reader's click and is worse than the bare text.
    """

    workspace_id: str
    title: str
    repo_label: str
    branch: str
    agent_name: str
    agent_kind: str | None
    state: AgentActivityState
    current_task: str | None
    todo: TodoList | None
    phase: PhaseReport | None
    latest_commit: CommitSummary | None
    deep_link: str | None
    terminal: bool
    occurred_at: datetime
    tickets: tuple[TicketRef, ...] = ()
    branch_url: str | None = None
    commit_url: str | None = None
    sessions: tuple[SessionLink, ...] = ()
    """Every top-level session this workspace ran, newest first, primary first.

    Resolved from the LAST CACHED ROW rather than looked up, which is what makes
    the finale possible at all: by the time a workspace ends its record is
    deleted, so anything the terminal body needs must already have been captured
    while the workspace was alive. Sub-agent sessions are excluded — a fleet's
    internal fan-out is not a session a reader would open.
    """
    ticket_held: bool | None = None
    """Is another live workspace still working these tickets? ``None`` = not asked.

    Only the finale asks. Absence renders nothing, the phase rule again: a
    publisher that cannot see the rest of the fleet must not claim the ticket has
    been dropped.
    """


@dataclass(slots=True)
class _FlushJob:
    """One unit of dispatch work — the coalesced row and whether it's the finale."""

    row: WorkspaceActivity
    terminal: bool


@dataclass(slots=True, frozen=True)
class _Target:
    """One publish destination — a resolved provider plus the thread id on it.

    A workspace mirrors onto EVERY eligible ref (typically the issue *and* the
    pull request that resolves it), so everything downstream of routing is
    per-target rather than per-workspace. Comment I/O needs no new provider code
    to reach a PR: both numeric forges build ``/repos/{o}/{r}/issues/{id}/comments``
    and interpolate the id raw, and a PR number is a valid id there — a PR target
    is therefore an ordinary ref, not a second code path.
    """

    provider: TicketProvider
    ticket_id: str

    @property
    def key(self) -> _TargetKey:
        """The sticky-id map key: provider NAME + ticket id, never the provider object.

        The resolver may hand back a different provider instance for the same
        tracker (a repo's registry is rebuilt on a fresh ``registry.get``), so
        keying on identity would silently lose the comment id and post a duplicate.
        """
        return (self.provider.name, self.ticket_id)


@dataclass(slots=True)
class _WsPub:
    """Per-workspace publishing memory: the sticky ids, the coalescer, the done latch.

    A non-persisted in-memory map keyed by workspace is the deliberate v1 store
    (the brief's sanctioned choice): a sticky comment id is recoverable from the
    forge itself via the :data:`STICKY_MARKER` scan on a cold start, so persisting it
    would duplicate a source of truth the thread already holds. ``dirty_since`` is
    stamped on the clean→dirty edge and NOT refreshed while dirty, so the window
    measures from the first unflushed change (bounding staleness to one window).

    ``comment_ids`` is keyed per TARGET, not per workspace: two targets sharing one
    scalar id would each edit the other's comment on alternate flushes — one thread
    growing a duplicate while the other's status was overwritten by its neighbour's.
    """

    comment_ids: dict[_TargetKey, str] = field(default_factory=dict)
    last_row: WorkspaceActivity | None = None
    fingerprint: tuple[object, ...] | None = None
    dirty: bool = False
    dirty_since: datetime | None = None
    done: bool = False


class TicketStatusPublisher:
    """Owns subscription + coalescing + dispatch for the live sticky comment.

    Long-lived alongside the daemon. Built via :meth:`from_config` (``None`` when
    disabled), then :meth:`bind` wires it to the activity bus and starts the
    single-worker dispatch pool; :meth:`close` unwinds both. Unbound, it runs every
    step inline on the caller's thread — the seam the tests drive with a fake clock.
    """

    def __init__(
        self,
        *,
        config: IssueOpsConfig,
        provider_resolver: ProviderResolver,
        todo_resolver: TodoResolver,
        phase_resolver: PhaseResolver,
        task_resolver: TaskResolver | None = None,
        holder_resolver: HolderResolver | None = None,
        transcript_probe: TranscriptProbe | None = None,
        clock: Callable[[], datetime] | None = None,
        terminal_states: frozenset[AgentActivityState] = frozenset(),
    ) -> None:
        self._provider_resolver = provider_resolver
        self._todo_resolver = todo_resolver
        self._phase_resolver = phase_resolver
        self._task_resolver = task_resolver
        self._holder_resolver = holder_resolver
        self._transcript_probe = transcript_probe
        self._window = timedelta(seconds=config.update_window_seconds)
        self._deep_link_base = config.deep_link_base_url.rstrip("/")
        # Mechanism, not policy: which agent states end the sticky comment is a
        # caller-supplied set (default empty — the lifecycle ``killed`` event is
        # the always-on terminal), so no "ERROR means done" verdict is baked in.
        self._terminal_states = terminal_states
        self._clock = clock if clock is not None else self._utcnow
        # Per-workspace state, unbounded like the broker's maps (loopback, small N).
        self._state: dict[str, _WsPub] = {}
        # Enriched ticket refs, TTL-memoized ACROSS workspaces — the key is the
        # ticket, not the workspace, so two workspaces naming one issue share
        # the read. Swept on every miss (see :meth:`_enrich`).
        self._tickets: dict[tuple[TicketProviderName, str, TicketKind], tuple[datetime, TicketRef]]
        self._tickets = {}
        self._lock = Lock()
        self._pool: ThreadPoolExecutor | None = None
        self._timer: threading.Timer | None = None
        self._unsub: Callable[[], None] | None = None
        self._scheduling = False  # true only while bound to a live bus

    @classmethod
    def from_config(
        cls,
        cfg: IssueOpsConfig,
        *,
        registry: RepoRegistry | None,
        transcript_probe: TranscriptProbe | None = None,
    ) -> TicketStatusPublisher | None:
        """Build a publisher from config, or ``None`` when issue-ops is disabled.

        Binds the resolvers to a live ``RepoRegistry``: the provider comes from the
        repo's ``ticket_providers`` registry, the todo from ``latest_todo``, the
        phase from ``phase`` — all best-effort, resolving to ``None`` on the typed
        engine errors (an unconfigured provider, a sessionless workspace) so the
        publisher skips rather than throws.

        ``transcript_probe`` is the ONE resolver the registry cannot supply, and
        it is injected rather than built here on purpose: reachability must be
        answered by the very reader a link would send someone to (the daemon's
        own session catalog), so "we linked it" and "a reader can open it" cannot
        drift. Absent, no session is ever linked — the honest default for a
        deployment with no session surface at all.
        """
        if not cfg.enabled:
            return None
        assert registry is not None  # enabled requires a registry to resolve against

        def provider_resolver(repo_root: str, name: TicketProviderName) -> TicketProvider | None:
            try:
                return registry.get(Path(repo_root)).ticket_providers.get(name)
            except GroveError:
                return None

        def todo_resolver(repo_root: str, workspace_id: str) -> TodoList | None:
            try:
                return registry.get(Path(repo_root)).latest_todo(workspace_id)
            except GroveError:
                return None

        def phase_resolver(repo_root: str, workspace_id: str) -> PhaseReport | None:
            try:
                return registry.get(Path(repo_root)).phase(workspace_id)
            except GroveError:
                return None

        def task_resolver(repo_root: str, workspace_id: str) -> str | None:
            try:
                return registry.get(Path(repo_root)).latest_task(workspace_id)
            except GroveError:
                return None

        def holder_resolver(repo_root: str, refs: Sequence[TicketRef]) -> bool:
            """Is any LIVE workspace still tracking one of these refs?

            ``find_by_ticket`` scans the store, and ``kill`` deletes a record
            outright rather than marking it killed, so a dead workspace can never
            answer here — which is exactly the property the finale needs.
            """
            try:
                mgr = registry.get(Path(repo_root))
                return any(mgr.find_by_ticket(r.provider, r.id) is not None for r in refs)
            except GroveError:
                return False

        return cls(
            config=cfg,
            provider_resolver=provider_resolver,
            todo_resolver=todo_resolver,
            phase_resolver=phase_resolver,
            task_resolver=task_resolver,
            holder_resolver=holder_resolver,
            transcript_probe=transcript_probe,
        )

    # ─── the finale's two extra reads (both terminal-only, both best-effort) ──

    def _ticket_held(self, repo_root: str, refs: Sequence[TicketRef]) -> bool | None:
        """Is another live workspace still on these tickets? ``None`` = unanswerable."""
        if self._holder_resolver is None:
            return None
        try:
            return self._holder_resolver(repo_root, refs)
        except Exception as exc:  # best-effort: never blocks the finale
            logger.debug("issueops holder read failed for {}: {}", repo_root, exc)
            return None

    def _session_links(self, row: WorkspaceActivity) -> tuple[SessionLink, ...]:
        """Every top-level session of the workspace, primary first, each probed once.

        Built from the row the coalescer already cached, because by the time this
        matters the store record is gone — the capture happened while the
        workspace was alive, which is the whole reason the finale can say
        anything at all.

        Sub-agent sessions are dropped: a fleet's internal fan-out is not
        something a reader would open, and it is unbounded where this list must
        stay comment-sized.
        """
        cwd = str(row.state.agent_cwd)
        links: list[SessionLink] = []
        for index, entry in enumerate(row.sessions):
            session = entry.session
            if session.parent_session_id is not None:
                continue
            links.append(
                SessionLink(
                    session_id=session.session_id,
                    kind=session.adapter_kind,
                    url=self._session_url(session.adapter_kind, cwd, session.session_id),
                    # ``WorkspaceActivity.primary`` IS ``sessions[0]`` — the same
                    # definition read the same way, rather than a second rule
                    # about which session is the main one.
                    primary=index == 0,
                )
            )
        return tuple(links)

    def _session_url(self, kind: str, cwd: str, session_id: str) -> str | None:
        """The session surface's address, or ``None`` where nothing could read it.

        Two independent reasons for ``None``, and neither is a degradation to
        paper over: no webapp base is configured (there is no surface), or the
        probe says this transcript is out of the reader's reach. A container
        workspace is the ordinary second case — its transcript survives on the
        host under a pinned config dir that the host-wide scan does not walk, so
        the bytes exist and the page would still 404.
        """
        if not self._deep_link_base or self._transcript_probe is None:
            return None
        try:
            reachable = self._transcript_probe(kind, cwd, session_id)
        except Exception as exc:  # best-effort: an unreadable probe links nothing
            logger.debug("issueops transcript probe failed for {}: {}", session_id, exc)
            return None
        if not reachable:
            return None
        query = urlencode({"kind": kind, "cwd": cwd})
        return f"{self._deep_link_base}/sessions/{quote(session_id)}?{query}"

    # ─── lifecycle ───────────────────────────────────────────────────────────

    def bind(
        self, subscribe: Callable[[Callable[[DashboardDelta], None]], Callable[[], None]]
    ) -> None:
        """Subscribe to the delta bus and start the dispatch worker + self-scheduling.

        Takes the bus's ``subscribe`` callable (``ActivityService.subscribe``), not
        the service, so the publisher depends only on the bus shape and a test
        drives it with a bare stub — exactly the broker's contract.
        """
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="grove-issueops")
        self._scheduling = True
        self._unsub = subscribe(self.observe)

    def close(self) -> None:
        """Unsubscribe, cancel the pending flush, and drain the dispatch worker."""
        self._scheduling = False
        if self._unsub is not None:
            with contextlib.suppress(Exception):
                self._unsub()
            self._unsub = None
        with self._lock:
            timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None

    # ─── pure decision (poll thread) ─────────────────────────────────────────

    def observe(self, delta: DashboardDelta) -> None:
        """Fold one delta into the per-workspace coalescer. The bus callback.

        Fast and non-blocking (the broker's ``_on_delta`` contract): it only mutates
        in-memory state and, at most, hands a job to the dispatch worker. Two delta
        kinds matter — a ``session_activity`` carrying a render-relevant change
        marks the workspace dirty; a lifecycle ``killed`` flushes the terminal
        summary. Everything else is ignored.
        """
        if delta.kind == "workspace_changed":
            if delta.detail.get("event") == "killed":
                self._on_killed(delta.workspace_id)
            return
        row = delta.workspace
        if row is None or not row.state.ticket_refs:
            return  # no session payload, or no ticket to mirror onto → skip silently
        job = self._record(row, self._clock())
        if job is not None:
            self._run(self.dispatch, job)  # terminal → flush immediately
        else:
            self._arm()

    def _record(self, row: WorkspaceActivity, now: datetime) -> _FlushJob | None:
        """Fold ``row`` into coalescing state; return a job iff it must flush NOW.

        Gated on a *render-relevant* fingerprint so pure diff-stat churn (dirty
        files, ahead/behind — fields the comment never shows) can't schedule a
        redundant PATCH. A terminal agent state returns the job (immediate flush)
        and latches the workspace done; otherwise it just arms the window.
        """
        with self._lock:
            rec = self._state.setdefault(row.state.id, _WsPub())
            if rec.done:
                return None
            fingerprint = self._render_fingerprint(row)
            if fingerprint == rec.fingerprint:
                return None
            rec.fingerprint = fingerprint
            rec.last_row = row
            if self._is_terminal_state(row):
                rec.dirty = False
                rec.dirty_since = None
                rec.done = True
                return _FlushJob(row=row, terminal=True)
            if not rec.dirty:
                rec.dirty = True
                rec.dirty_since = now
            return None

    def _on_killed(self, workspace_id: str) -> None:
        """A workspace's lifecycle ended: flush a final summary once, then stop.

        The record is already deleted from the store, so the summary renders from
        the last cached row. With nothing cached (killed before any activity), we
        only latch the workspace done so a late delta can't reopen it.
        """
        with self._lock:
            rec = self._state.setdefault(workspace_id, _WsPub())
            already_done = rec.done
            rec.done = True
            rec.dirty = False
            rec.dirty_since = None
            row = rec.last_row
        if already_done or row is None:
            return
        self._run(self.dispatch, _FlushJob(row=row, terminal=True))

    def flush_pending(self, now: datetime | None = None) -> None:
        """Dispatch every workspace whose coalescing window has elapsed.

        The scheduled flush (the timer calls this); also the clock-driven seam a
        coalescing test drives directly. Collects due jobs under the lock, then
        dispatches outside it — the forge round-trip never holds the lock.
        """
        moment = now if now is not None else self._clock()
        jobs: list[_FlushJob] = []
        with self._lock:
            for rec in self._state.values():
                if (
                    rec.dirty
                    and rec.dirty_since is not None
                    and rec.last_row is not None
                    and (moment - rec.dirty_since) >= self._window
                ):
                    rec.dirty = False
                    rec.dirty_since = None
                    jobs.append(_FlushJob(row=rec.last_row, terminal=False))
        for job in jobs:
            self._run(self.dispatch, job)

    # ─── engine status seam (@grove status → flush now) ──────────────────────

    def publish(self, event: IssueOpsEvent, manager: WorkspaceManager) -> None:
        """Force the ticket's workspace to re-render its sticky comment NOW.

        The engine's ``StatusPublisher`` seam (the ``@grove status`` verb),
        matched structurally so the publisher never imports the engine module: it
        takes only the wire ``IssueOpsEvent`` and the ``WorkspaceManager`` the
        engine already resolved. Maps ticket → workspace via ``find_by_ticket``
        (the same lookup the engine's verbs use) and flushes that workspace's
        latest coalesced state immediately, bypassing the window. Best-effort —
        no workspace for the ticket, or none observed yet, is a silent no-op.
        """
        match = manager.find_by_ticket(event.provider, str(event.issue_number))
        if match is not None:
            self.flush_now(match.id)

    def flush_now(self, workspace_id: str) -> None:
        """Dispatch a workspace's latest coalesced row immediately, ignoring the window.

        A human asked for the current state (``@grove status``), so we skip the
        coalescing wait rather than let the ask sit for a window. A workspace with
        no cached row yet (no render-relevant delta observed) or already latched
        ``done`` is a silent no-op — there is nothing fresh to render.
        """
        with self._lock:
            rec = self._state.get(workspace_id)
            if rec is None or rec.done or rec.last_row is None:
                return
            rec.dirty = False
            rec.dirty_since = None
            row = rec.last_row
        self._run(self.dispatch, _FlushJob(row=row, terminal=False))

    # ─── side effects (dispatch worker / inline) ─────────────────────────────

    def dispatch(self, job: _FlushJob) -> None:
        """Resolve every target, build the snapshot once, and mirror it onto each.

        One render, N writes: :meth:`render` is a pure full rebuild and the body a
        reader wants is the same on an issue and on the PR that resolves it, so
        rendering per target would only pay the cost twice for identical bytes.
        The whole body is best-effort: no eligible target or an unresolved todo
        skips quietly, and each target's forge round-trip is guarded on its own, so
        one broken thread degrades exactly one comment and never re-raises into the
        poll path.
        """
        ws = job.row.state
        targets = self._route(ws.repo_root, ws.ticket_refs)
        if not targets:
            return  # no target can carry the mirror for this workspace → skip silently
        todo = self._todo(ws.repo_root, ws.id)
        phase = self._phase(ws.repo_root, ws.id)
        task = self._task(ws.repo_root, ws.id)
        tickets = tuple(self._enrich(ws.repo_root, ref) for ref in ws.ticket_refs)
        snapshot = self._snapshot(
            job.row,
            todo=todo,
            phase=phase,
            task=task,
            tickets=tickets,
            # Repo-level links come from the first eligible target's provider:
            # every target is a tracker for THIS repo, and one body goes to all
            # of them, so the choice must be deterministic rather than per-target.
            links=targets[0].provider,
            terminal=job.terminal,
        )
        body = self.render(snapshot)
        for target in targets:
            self._publish(ws.id, target, body)

    def _route(self, repo_root: str, refs: Sequence[TicketRef]) -> list[_Target]:
        """Every ticket ref whose provider can ACTUALLY carry a comment.

        Multi-target on purpose: a workspace's refs are typically the issue and the
        pull request that resolves it, and a reader of either wants the live status,
        so all eligible refs are mirrored rather than the first.

        Eligibility is ``provider.can_comment`` (capability AND credential), never
        mere enablement — the bug this replaced. Resolution only fails for a
        provider the registry considers *disabled*, so an enabled-but-tokenless
        Gitea, or Linear (which backs no comment I/O at all and raises
        ``TicketCommentsUnsupported`` on every write), resolved fine, was taken as
        *the* target, and permanently shadowed every later ref — a workspace whose
        branch named two trackers mirrored onto neither, silently and forever.

        Deduped by ``_Target.key`` so two refs naming one thread (an attach on top
        of the same branch-parsed ref) can't post twice into it.
        """
        targets: dict[_TargetKey, _Target] = {}
        for ref in refs:
            provider = self._provider_resolver(repo_root, ref.provider)
            if provider is None or not provider.can_comment:
                continue
            target = _Target(provider=provider, ticket_id=ref.id)
            targets.setdefault(target.key, target)
        return list(targets.values())

    def _todo(self, repo_root: str, workspace_id: str) -> TodoList | None:
        try:
            return self._todo_resolver(repo_root, workspace_id)
        except Exception as exc:  # best-effort: a todo read miss never blocks the update
            logger.debug("issueops todo read failed for {}: {}", workspace_id, exc)
            return None

    def _phase(self, repo_root: str, workspace_id: str) -> PhaseReport | None:
        try:
            return self._phase_resolver(repo_root, workspace_id)
        except Exception as exc:  # best-effort: a phase read miss never blocks the update
            logger.debug("issueops phase read failed for {}: {}", workspace_id, exc)
            return None

    def _task(self, repo_root: str, workspace_id: str) -> str | None:
        """The uncapped task text, or ``None`` to fall back to the capped field."""
        if self._task_resolver is None:
            return None
        try:
            return self._task_resolver(repo_root, workspace_id)
        except Exception as exc:  # best-effort: a task read miss never blocks the update
            logger.debug("issueops task read failed for {}: {}", workspace_id, exc)
            return None

    def _enrich(self, repo_root: str, ref: TicketRef) -> TicketRef:
        """Resolve one ref's display fields at dispatch. Best-effort.

        **A ref is persisted BARE by design** — ``attach_ticket`` stores only
        provider + id + kind, because display enrichment is meant to be an
        on-demand fetch rather than stale persisted state — so nothing carries a
        ``title`` until this runs, and an unenriched entry is its bare reference
        alone.

        Refs resolve at DISPATCH, exactly as todo and phase already do, and for
        the same reason — a read that needs I/O must never run on the poll
        thread. A miss (unresolvable provider, no credential, a deleted ticket)
        returns the ref untouched, which still renders and still links.

        A pull request goes to ``get_pull_request``, never ``get_ticket``. That
        rule was load-bearing while the render carried a status (the issues
        endpoint reports a merged PR as ``closed``, true and useless); it no
        longer is, and it stays because it costs the same one GET and is the
        honest source for the state this ref carries.
        """
        key = (ref.provider, ref.id, ref.kind)
        now = self._clock()
        with self._lock:
            cached = self._tickets.get(key)
            if cached is not None and (now - cached[0]) < _TICKET_TTL:
                return cached[1]
        provider = self._provider_resolver(repo_root, ref.provider)
        if provider is None:
            return ref
        try:
            fresh = (
                provider.get_pull_request(ref.id)
                if ref.kind == "pull_request"
                else provider.get_ticket(ref.id)
            )
        except Exception as exc:  # best-effort: enrichment never blocks the update
            logger.debug("issueops ticket read failed for {}: {}", ref.id, exc)
            return ref
        with self._lock:
            # Sweep on miss so a long-lived daemon never accumulates one entry
            # per ticket it has ever seen.
            self._tickets = {k: v for k, v in self._tickets.items() if (now - v[0]) < _TICKET_TTL}
            self._tickets[key] = (now, fresh)
        return fresh

    def _publish(self, workspace_id: str, target: _Target, body: str) -> None:
        """Edit one target's sticky comment in place, or post it once (found-or-created).

        Sticky discipline, per target: reuse that target's known comment id; on a
        cold start (no id yet) recover it by scanning ITS thread for
        :data:`STICKY_MARKER` before posting a fresh one. A forge failure forgets
        only the failing target's id — a workspace mirroring onto two threads must
        not lose the healthy one's id (that would re-scan and, on a thread whose
        marker read also fails, duplicate the comment) — so the next flush re-scans
        exactly the thread that broke, which also recreates a comment a human
        deleted (the marker scan finds nothing → post).
        """
        comment_id = self._comment_id(workspace_id, target.key)
        if comment_id is None:
            comment_id = self._recover_comment_id(target.provider, target.ticket_id)
        try:
            if comment_id is not None:
                target.provider.edit_comment(comment_id, body)
            else:
                comment_id = target.provider.post_comment(target.ticket_id, body).id
        except (TicketProviderError, TicketCommentsUnsupported) as exc:
            logger.warning(
                "issueops publish failed workspace={} target={}: {}", workspace_id, target.key, exc
            )
            self._forget_comment_id(workspace_id, target.key)
            return
        self._store_comment_id(workspace_id, target.key, comment_id)

    def _recover_comment_id(self, provider: TicketProvider, ticket_id: str) -> str | None:
        """Cold-start recovery: the first thread comment carrying the STICKY marker.

        Scans for :data:`STICKY_MARKER`, never the general :data:`SIGNATURE_MARKER`:
        every engine reply carries the signature too, so a signature scan would
        adopt an old usage/refusal reply and edit the status render over it. The
        sticky marker is unique to this comment, so recovery only ever re-adopts
        the status comment (or finds nothing → post a fresh one).
        """
        try:
            comments = provider.list_comments(ticket_id)
        except (TicketProviderError, TicketCommentsUnsupported) as exc:
            logger.debug("issueops marker scan failed for ticket={}: {}", ticket_id, exc)
            return None
        for comment in comments:
            if STICKY_MARKER in comment.body:
                return comment.id
        return None

    # ─── pure render ─────────────────────────────────────────────────────────

    @classmethod
    def render(cls, snapshot: PublishSnapshot) -> str:
        """Rebuild the whole comment body from ``snapshot`` — a pure function, no I/O.

        Never a diff-patch of the prior body (the sticky-comment rule): the forge
        holds the last render, we always replace it wholesale. A terminal snapshot
        swaps the live status + checklist for a final summary.
        """
        if snapshot.terminal:
            return cls._render_terminal(snapshot)
        return cls._render_live(snapshot)

    @classmethod
    def _render_live(cls, s: PublishSnapshot) -> str:
        """Heading, the at-a-glance table, then four named sections. In that order.

        The whole body is one hierarchy: an ``##`` heading that answers "what
        state is this in", the title, a two-column table holding every scalar
        the publisher knows, and then :data:`_SECTIONS` — the same four titles,
        in the same order, in every render. Only the CONTENT varies.

        That fixed vocabulary is what makes the comment scannable across a
        ticket list: a reader learns the shape once. It is also why no count,
        state or timestamp is allowed into a section title — a title that moves
        cannot be recognized at a glance, so every varying number lives in the
        table (which is the overview) or inside the section it belongs to.
        """
        label = _STATE_LABEL.get(s.state, s.state.value)
        glyph = _STATE_GLYPH.get(s.state, "•")
        lines = [f"## {glyph} Grove — {label}", "", f"**{cls._cell(s.title)}**"]
        # No Status row: the heading already answers it, and the loudest signal
        # in the comment should be stated once.
        lines += cls._summary_table(s, stamp="Updated")
        lines += cls._progress_section(s)
        lines += cls._activity_section(s)
        lines += cls._checklist_section(s)
        lines += cls._sessions_section(s)
        lines += cls._tracking_section(s)
        lines += ["", cls._footer()]
        return "\n".join(lines)

    @classmethod
    def _render_terminal(cls, s: PublishSnapshot) -> str:
        """The finale — the same hierarchy, minus what is now moot.

        It keeps the heading, the table, Progress and Tracking, and drops the
        live activity and checklist: a half-ticked list under a session that has
        ended reads as work still in flight.

        The one row the live body does NOT carry appears here — ``Final state``
        — because this heading says "Session ended" rather than naming the
        agent's state, so unlike the live body there is nothing else to say it.
        """
        label = _STATE_LABEL.get(s.state, s.state.value)
        glyph = _STATE_GLYPH.get(s.state, "•")
        lines = ["## ✅ Grove — Session ended", "", f"**{cls._cell(s.title)}**"]
        handover = cls._handover_line(s)
        if handover:
            lines += ["", handover]
        lines += cls._summary_table(s, stamp="Concluded", final_state=f"{glyph} {label}")
        lines += cls._progress_section(s)
        lines += cls._sessions_section(s)
        lines += cls._tracking_section(s)
        lines += ["", cls._footer()]
        return "\n".join(lines)

    # ─── the at-a-glance table ───────────────────────────────────────────────

    @classmethod
    def _summary_table(
        cls, s: PublishSnapshot, *, stamp: str, final_state: str | None = None
    ) -> list[str]:
        """Every scalar the comment knows, one row each, each row led by its icon.

        A row exists only for a fact that exists — an absent phase, commit or
        deep link renders no row at all rather than an empty one, the same
        "absence is not a state" rule the phase axis follows everywhere.

        **This table is where every varying number lives**, which is what lets
        the section titles below stay fixed: the checklist's progress is a row
        here, so ``☑️ Checklist`` never has to carry a count to stay useful when
        collapsed. The phase row is the dot-bar caption WITHOUT the agent's note
        — the note is prose, it belongs under the diagram where there is room
        for it, and a table cell holding 200 characters wrecks the column.

        The branch names its repository in a trailing bracket. The link already
        encodes it, but only a reader who hovers can see it, and this comment is
        read on a *tracker* — the one place where several projects' branches all
        called ``main`` land in front of the same person.
        """
        rows: list[tuple[str, str, str]] = []
        if final_state is not None:
            rows.append((_ICON_STATE, "Final state", final_state))
        if s.phase is not None:
            rows.append((_ICON_PHASE, "Phase", cls._phase_caption(s.phase)))
        if s.todo is not None and s.todo.items:
            done = sum(1 for item in s.todo.items if item.status == "completed")
            rows.append((_ICON_CHECKLIST, "Checklist", f"{done} of {len(s.todo.items)} done"))
        agent = cls._agent_caption(s)
        if agent:
            rows.append((_ICON_AGENT, "Agent", agent))
        branch = cls._link(f"`{s.branch}`", s.branch_url)
        if s.repo_label:
            branch = f"{branch} ({cls._cell(s.repo_label)})"
        rows.append((_ICON_BRANCH, "Branch", branch))
        if s.latest_commit is not None:
            sha = cls._link(f"`{s.latest_commit.sha}`", s.commit_url)
            rows.append((_ICON_COMMIT, "Commit", f"{sha} — {cls._cell(s.latest_commit.subject)}"))
        # The workspace link is the LIVE body's alone. Once a workspace ends its
        # record is deleted, so `/w/<id>` resolves to nothing — and the finale is
        # the render a reader meets months later, which is exactly when a dead
        # link costs the most. The finale points at the transcript instead.
        if s.deep_link and not s.terminal:
            rows.append((_ICON_WORKSPACE, "Workspace", f"[Open in Grove]({s.deep_link})"))
        if s.terminal:
            transcript = cls._transcript_cell(s)
            if transcript:
                rows.append((_ICON_SESSION, "Transcript", transcript))
        rows.append((_ICON_UPDATED, stamp, cls._stamp(s.occurred_at)))
        header = ["", "|  |  |", "| :-- | :-- |"]
        return header + [f"| {icon} **{key}** | {value} |" for icon, key, value in rows]

    @staticmethod
    def _link(text: str, url: str | None) -> str:
        """``text`` as a link when there is somewhere to go, plain text otherwise.

        The whole rule in one place: an element naming something with a
        destination must reach it, and an element with no destination must NOT
        be dressed as one.
        """
        return f"[{text}]({url})" if url else text

    @classmethod
    def _agent_caption(cls, s: PublishSnapshot) -> str:
        """Who did the work: the agent kind, and the profile when it says more.

        Both facts are persisted on the workspace at create, so this row is
        documentary — "a Claude Code agent did this, under this profile". **The
        model identifier is deliberately absent**: it dates the comment, adds a
        token nobody acts on, and invites the reader to draw conclusions from a
        name the comment cannot support.

        The profile is the operator's own word for the same thing, so it renders
        only when it differs from the kind's label — a profile named ``codex``
        would otherwise read "Codex (codex)". Neither fact known renders NO row,
        the same "absence is not a state" rule the phase axis follows.
        """
        kind = _AGENT_KIND_LABEL.get(s.agent_kind or "", "")
        profile = " ".join(s.agent_name.split())
        if not kind:
            return cls._cell(profile)
        if profile and profile.casefold().replace(" ", "") != kind.casefold().replace(" ", ""):
            return f"{kind} ({cls._cell(profile)})"
        return kind

    @staticmethod
    def _cell(text: str) -> str:
        """Make free text safe inside a markdown table cell.

        Commit subjects and agent notes are free text, so an unescaped ``|``
        ends the cell early and shears the row — the value-becomes-syntax class
        again, fixed where the value enters the syntax. A newline would end the
        ROW, so whitespace collapses too.
        """
        return " ".join(text.split()).replace("|", "\\|")

    @staticmethod
    def _stamp(moment: datetime) -> str:
        return f"{moment:%Y-%m-%d %H:%M UTC}"

    # ─── the four sections (fixed titles, varying content) ───────────────────

    @staticmethod
    def _section(icon: str, title: str, body: list[str], *, open_: bool = False) -> list[str]:
        """One named section. Every section in the comment is built by this.

        The title is ``<icon> <Title>`` and NOTHING else — no count, no state,
        no timestamp. A section title that changes between renders cannot be
        recognized at a glance, and recognition is the entire job of a heading
        in a comment a reader has already seen ten times on other tickets. Every
        varying number lives in the at-a-glance table instead.

        ``open_`` distinguishes *bounded* content, which stays visible, from
        content that grows without bound, which collapses. Verified on a real
        Gitea render rather than assumed: ``<details open>`` survives the
        comment sanitizer, and markdown inside the body (lists, task lists,
        tables, fenced blocks) is parsed — but ONLY with the blank lines this
        method puts around it, without which the whole block is treated as raw
        HTML.
        """
        tag = "<details open>" if open_ else "<details>"
        return ["", tag, f"<summary>{icon} {title}</summary>", "", *body, "", "</details>"]

    @classmethod
    def _progress_section(cls, s: PublishSnapshot) -> list[str]:
        """The phase diagram and the agent's note. Open — it is the point of the comment.

        **A workspace that has reported no phase renders NOTHING here — not an
        all-remaining diagram, and not an empty section.** A six-node chart with
        every block pale would assert "nothing is done yet", and that is a claim
        Grove cannot make: the agent may be most of the way through and simply
        not reporting. Absence of a report is a fact about the AGENT, not a
        position on the task axis.

        The note rides here as a blockquote rather than in the table's phase
        row, and it is the only place it appears: it is prose up to 200
        characters, which wrecks a table column but reads well under the chart
        it explains.
        """
        if s.phase is None:
            return []
        body = [cls._phase_diagram(s.phase)]
        if s.phase.note:
            body += ["", f"> {cls._plain_task(s.phase.note)}"]
        return cls._section(_ICON_PHASE, "Progress", body, open_=True)

    @classmethod
    def _activity_section(cls, s: PublishSnapshot) -> list[str]:
        """What the agent is doing right now — whole, wrapped, and out of the way.

        The title says only "Latest activity". It used to carry a cut-down
        preview of the text, which was the worst of both: a reader who wanted
        the gist got a fragment, and a reader who wanted the text had to expand
        anyway to find it continued.

        The body is a fenced block, which buys three things at once. The text is
        agent-written, so a stray ``#`` or ``|`` in it cannot become markup;
        it is *hard-wrapped* here, because a code block scrolls sideways rather
        than reflowing and an unwrapped paragraph would need dragging; and the
        ``[updated …]`` line gives the excerpt the timestamp it otherwise lacks
        once it is behind a fold.
        """
        text = cls._plain_task(s.current_task)
        if not text:
            return []
        wrapped = textwrap.wrap(text, width=_TASK_WRAP_WIDTH) or [text]
        fence = cls._fence_for(text)
        body = [fence, f"[updated {cls._stamp(s.occurred_at)}]", "", *wrapped, fence]
        return cls._section(_ICON_ACTIVITY, "Latest activity", body)

    @staticmethod
    def _fence_for(text: str) -> str:
        """A fence longer than the longest backtick run in ``text``.

        Agent text can contain a fenced block of its own, and a three-backtick
        fence would then be closed by the agent's own content — the rest of the
        message escaping into the comment as markup. The same value-becomes-
        syntax class as the mermaid escaping and the table-cell pipe.
        """
        longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
        return "`" * max(3, longest + 1)

    @staticmethod
    def _plain_task(text: str | None) -> str:
        """Agent-written text with tag-shaped markup removed and whitespace collapsed."""
        if not text:
            return ""
        return " ".join(_TAG_MARKUP.sub(" ", text).split())

    @classmethod
    def _checklist_section(cls, s: PublishSnapshot) -> list[str]:
        """The todo list. Collapsed, because it grows without bound as work proceeds.

        Its progress is not in the title — it is the table's ``Checklist`` row,
        so the overview stays visible while the list itself stays folded.
        """
        if s.todo is None or not s.todo.items:
            return []
        return cls._section(
            _ICON_CHECKLIST, "Checklist", [cls._checklist_line(item) for item in s.todo.items]
        )

    # ─── the phase diagram ───────────────────────────────────────────────────

    @classmethod
    def _phase_diagram(cls, report: PhaseReport) -> str:
        """The six phases as a horizontal mermaid flowchart, done → now → remaining.

        Verified rendering rather than assumed: GitHub documents ```` ```mermaid ````
        for issues and pull requests, and Gitea has carried a built-in renderer
        since 1.15 (``MERMAID_MAX_SOURCE_CHARACTERS`` defaults to 50000, ~80x
        this payload). Both were confirmed by observation. The
        plain-text caption in :meth:`_phase_line` still rides beneath it, so a
        surface that renders no diagram is no worse off than before — and a
        screen reader gets the same answer in one line.

        **The colour language is derived from ``DARK_PHASE_HEX``, never invented**,
        so a block here reads the same as the badge the TUI and webapp already
        show. Each of the three states takes the palette member that already
        means it: a completed phase takes ``done``'s muted gray, whose own
        definition is "settled; recedes from the fleet view"; the current phase
        takes ITS OWN ramp entry, which is the whole point of a sequential
        palette; a remaining phase takes the ramp's palest anchor, defined as
        "oriented, not yet producing".

        Two fills would collide by construction — at ``scoping`` the current node
        matches the remaining ones, at ``done`` it matches the completed ones —
        and the two collisions are NOT treated alike, deliberately.

        At ``scoping`` the collision stands: the ring separates the current node,
        and it survives a reader who cannot distinguish the hues at all. At
        ``done`` it does not, because that is where a workspace ENDS and the last
        render is the one a reader meets forever after: a current node in the
        completed gray leaves the finished comment saying nothing about where the
        work actually stopped except through a ring. So the current node takes
        :data:`_ACTIVE_FILL_AT_DONE` there — still a palette member, still dark
        ink, and the ring stays on top of it.
        """
        done_fill = DARK_PHASE_HEX["done"]
        now_fill = DARK_PHASE_HEX[report.phase]
        if now_fill == done_fill:
            # Keyed on the COLLISION, not on ``phase == "done"``: it is the fill
            # sameness that costs the reader, so a palette that later moves
            # another entry onto the gray is covered by the same line.
            now_fill = _ACTIVE_FILL_AT_DONE
        todo_fill = DARK_PHASE_HEX[PHASE_ORDER[0]]
        lines = [
            "```mermaid",
            "flowchart LR",
            f"  classDef done fill:{done_fill},stroke:{done_fill},color:{_NODE_INK}",
            f"  classDef now fill:{now_fill},stroke:{_NODE_INK},stroke-width:3px,color:{_NODE_INK}",
            f"  classDef todo fill:{todo_fill},stroke:{todo_fill},color:{_NODE_INK}",
        ]
        for index, phase in enumerate(PHASE_ORDER):
            if index < report.index:
                node_class = "done"
            elif index == report.index:
                node_class = "now"
            else:
                node_class = "todo"
            label = _PHASE_LABEL.get(phase, phase)
            if node_class == "now" and report.note:
                label = f"{label}<br>{cls._mermaid_label(report.note)}"
            lines.append(f'  p{index}["{label}"]:::{node_class}')
        lines.append("  " + " --> ".join(f"p{i}" for i in range(len(PHASE_ORDER))))
        lines.append("```")
        return "\n".join(lines)

    @staticmethod
    def _mermaid_label(text: str) -> str:
        """Make agent-written text safe to sit inside a mermaid node label.

        The note is free text an LLM wrote, so it is the one untrusted-ish value
        that reaches the diagram — and a single ``"`` in it does not degrade the
        chart, it **replaces the whole thing with a parse error**, verbatim:
        ``Expecting 'SQE', 'PE', ... got 'STR'`` (reproduced on a real Gitea
        1.26.2 render, then confirmed fixed by this escaping). Escaping here is
        the fix, not a belt over one — the same reasoning as the branch-name
        guard, where a value becomes syntax.

        Truncation happens BEFORE translation on purpose: cutting afterwards
        could slice an entity code in half and leave a literal ``#12`` on screen.
        """
        flat = " ".join(text.split())
        if len(flat) > _DIAGRAM_NOTE_CAP:
            flat = flat[: _DIAGRAM_NOTE_CAP - 1].rstrip() + "…"
        return flat.translate(_MERMAID_ENTITIES)

    @staticmethod
    def _handover_line(s: PublishSnapshot) -> str:
        """One sentence answering "is anyone still on this?" — the finale's job.

        "Session ended" is a fact about a WORKSPACE; a reader on a ticket is
        asking something else, and the two answers come apart exactly when it
        matters (a second workspace picked the ticket up, or nothing did). The
        publisher cannot answer it from its own state — its record is already
        deleted and a sibling workspace is somebody else's row — so an injected
        resolver asks the store, and an unanswerable question renders NOTHING
        rather than a guess dressed as a finding.

        The claim is deliberately scoped to Grove: a human may well be working
        the ticket, which is not a thing this comment can see.
        """
        if s.ticket_held is None:
            return ""
        if s.ticket_held:
            return "_Another Grove workspace is still working this ticket._"
        return "_No Grove workspace is working this ticket._"

    @classmethod
    def _transcript_cell(cls, s: PublishSnapshot) -> str:
        """The finale's replacement for the dead workspace link: the primary session.

        Reachable → a link to the session surface. Not reachable → the short id
        as plain text, followed by the reason, because "the transcript exists but
        this deployment cannot serve it" is a fact a reader acts on (they can
        still find it on the host) where a bare unexplained id is noise.
        """
        primary = next((link for link in s.sessions if link.primary), None)
        if primary is None:
            return ""
        if primary.url:
            return cls._link(f"`{primary.short}`", primary.url)
        return f"`{primary.short}` — not reachable from here"

    @classmethod
    def _sessions_section(cls, s: PublishSnapshot) -> list[str]:
        """The sessions BESIDE the primary one — a ticket worked more than once.

        Collapsed, because a long-running ticket accumulates them without bound,
        and omitted entirely when the workspace only ever ran one session, which
        is the ordinary case — the primary already has its row in the table.

        Scope worth stating plainly: this is every session of THIS workspace, not
        every session that ever worked the ticket. Nothing on disk ties a session
        to a ticket once its workspace is gone, so the wider claim would need
        durable state Grove does not keep, and pretending otherwise would render
        an incomplete list as if it were the history.
        """
        extras = [link for link in s.sessions if not link.primary]
        if not extras:
            return []
        body = [cls._session_line(link) for link in extras]
        return cls._section(_ICON_SESSION, "Other sessions", body)

    @classmethod
    def _session_line(cls, link: SessionLink) -> str:
        kind = _AGENT_KIND_LABEL.get(link.kind, link.kind)
        target = cls._link(f"`{link.short}`", link.url) if link.url else f"`{link.short}`"
        suffix = "" if link.url else " — not reachable from here"
        return f"- {kind} {target}{suffix}"

    @classmethod
    def _tracking_section(cls, s: PublishSnapshot) -> list[str]:
        """Every ticket this workspace names, then the way back into Grove.

        This is the cross-link the multi-target design deferred: the body is
        identical on every thread, so naming all the refs is what lets a reader
        on the issue see the PR that resolves it, and a reader on the PR see the
        issue it closes. Issues sort ahead of pull requests (a stable sort, so
        the engine's own order survives within each kind) because that is the
        order the work happened in.

        Each entry is one prose line ENDING in its reference, so the eye lands on
        the link: a ticket's own title says what it is far better than a ``Kind``
        column, and a sentence that ends where the reader wants to click beats a
        grid they have to scan across.

        The block stays open rather than collapsed: it is bounded by the refs a
        workspace names, and it is the one thing a reader of *this* thread
        cannot get anywhere else. Only what exists renders — an unenriched ref is
        its bare reference alone, and no refs at all means no block.
        """
        lines = [cls._ticket_line(ref) for ref in sorted(s.tickets, key=cls._issues_first)]
        if not lines:
            return []
        return cls._section(_ICON_TRACKING, "Tracking", lines, open_=True)

    @staticmethod
    def _issues_first(ref: TicketRef) -> bool:
        return ref.kind != "issue"

    @classmethod
    def _ticket_line(cls, ref: TicketRef) -> str:
        """One tracked ticket: its title, then the reference it ends on.

        The reference is written BARE rather than as a markdown link, and that is
        the whole mechanism: a forge turns ``#42`` into a live reference to its
        own thread, which a ``[#42](url)`` renders as ordinary link text instead.
        Verified on this Gitea's own renderer (``/api/v1/markdown`` in ``comment``
        mode, plus a real browser): a bare ``#N`` auto-links inside a ``<details>``
        body, which is where every one of these sits.

        **Neither ``kind`` nor ``status`` renders, and the status one is a signal
        deliberately GIVEN UP rather than delegated.** It would be comfortable to
        say the forge shows the state instead; it does not — measured on a page
        carrying an open issue, a closed issue and a merged pull request, all
        three render with the identical ``ref-issue`` class and colour, no
        strikethrough and no tooltip. The trade taken instead: this comment's job
        is to say where the WORKSPACE is, a status rendered here is only as fresh
        as the last flush, and the ticket's own page is one click away and never
        stale. ``kind`` is the cheaper half — a title says what an entry is, and
        the reference reaches the same place either way.
        """
        title = cls._plain_task(ref.title)
        return f"- {title} — #{ref.id}" if title else f"- #{ref.id}"

    @staticmethod
    def _phase_caption(report: PhaseReport) -> str:
        """The compact progress render — deliberately the densest line in the comment.

        A human skimming a ticket learns more from "Verifying, 4 of 6" than from a
        status glyph alone: WORKING is true whether the agent is still reading the
        ticket or already opening the PR, and the phase is the one axis that
        answers "how far in". Dots over a numeric bar because they render
        identically in every forge's markdown (no HTML, no table) and stay legible
        at ticket-list scan depth, not just inside the expanded comment.

        It is the Phase row of the summary table, so the row label supplies the
        word "Phase" and the caption is dots + name + position. **The agent's note
        is deliberately NOT here** — it is prose, it can run to 200 characters, and
        a table cell that long wrecks the column; it renders once, as a blockquote
        under the diagram it explains. "4 of 6" rather than "(4/6)" so it counts
        the same way the Checklist row beside it does.
        """
        label = _PHASE_LABEL.get(report.phase, report.phase)
        dots = "".join("●" if i <= report.index else "○" for i in range(len(PHASE_ORDER)))
        return f"{dots} {label} · {report.index + 1} of {len(PHASE_ORDER)}"

    @staticmethod
    def _checklist_line(item: TodoItem) -> str:
        # A GitHub/Gitea task list has only checked/unchecked; ``in_progress`` maps
        # to an unchecked box with an inline note rather than a third glyph.
        box = "x" if item.status == "completed" else " "
        note = " _(in progress)_" if item.status == "in_progress" else ""
        return f"- [{box}] {item.content}{note}"

    @staticmethod
    def _footer() -> str:
        # Both markers ride the footer, each on its own line — invisible in the
        # rendered thread. STICKY_MARKER is the cold-start recovery anchor (unique
        # to this sticky comment); SIGNATURE_MARKER is the engine's ingest anti-loop
        # guard (shared with every reply, so the bot never answers its own comment).
        #
        # The visible "Updated <ts>" line that used to live here is now the
        # summary table's last row: one timestamp, in the place a reader scans,
        # instead of the same fact at both ends of the comment.
        return f"{STICKY_MARKER}\n{SIGNATURE_MARKER}"

    # ─── snapshot + helpers ──────────────────────────────────────────────────

    def _snapshot(
        self,
        row: WorkspaceActivity,
        *,
        todo: TodoList | None,
        phase: PhaseReport | None,
        task: str | None,
        tickets: tuple[TicketRef, ...],
        links: TicketProvider,
        terminal: bool,
    ) -> PublishSnapshot:
        ws = row.state
        primary = row.primary
        commit = row.recent_commits[0] if row.recent_commits else None
        return PublishSnapshot(
            workspace_id=ws.id,
            title=ws.title,
            # The provider's own ``owner/repo`` scope where it has one — the same
            # repository the branch link points into — falling back to the repo
            # directory's name for a tracker that fronts no repo at all.
            repo_label=links.context or Path(ws.repo_root).name or ws.repo_root,
            branch=ws.branch,
            agent_name=ws.agent_name,
            agent_kind=ws.agent_kind,
            state=primary.state if primary is not None else AgentActivityState.UNKNOWN,
            # The uncapped read wins; the delta's own 500-char field is the
            # fallback for a workspace whose session cannot be resolved.
            current_task=task or (primary.current_task if primary is not None else None),
            todo=todo,
            phase=phase,
            latest_commit=commit,
            deep_link=f"{self._deep_link_base}/w/{ws.id}" if self._deep_link_base else None,
            terminal=terminal,
            occurred_at=self._clock(),
            tickets=tickets,
            branch_url=links.branch_url(ws.branch),
            commit_url=links.commit_url(commit.sha) if commit is not None else None,
            sessions=self._session_links(row),
            # Only the finale asks who else holds the ticket: while this
            # workspace is alive the answer is "this one", which the comment is
            # already saying, and the read would then run on every flush.
            ticket_held=self._ticket_held(ws.repo_root, ws.ticket_refs) if terminal else None,
        )

    def _is_terminal_state(self, row: WorkspaceActivity) -> bool:
        primary = row.primary
        return primary is not None and primary.state in self._terminal_states

    @staticmethod
    def _render_fingerprint(row: WorkspaceActivity) -> tuple[object, ...]:
        """The render-relevant change key — what a PATCH would actually alter.

        Deliberately excludes diff/ahead-behind/dirty counts (never shown in the
        comment) so their churn stays clean; includes ``tool_calls``/replies as the
        proxy for todo/checklist progress. The row now carries ``todo`` as
        ``TodoProgress`` COUNTS, but the rendered checklist is still resolved at
        dispatch, and a todo can only advance by the agent calling its own todo
        tool — which moves ``tool_calls`` in the same tick. So the counts stay out
        of this key as redundant rather than as unavailable. Contrast ``phase``
        below, which no other member moves with.

        **The agent row is absent from this key on purpose, and the reasoning is
        the one that has to be CHECKED rather than assumed.** A field that never
        moves is exactly the kind that silently fails to appear when the first
        render predates it — so the question is not "does it change" but "is it
        there before the first render". ``agent_name`` and ``agent_kind`` are
        both written by ``create()`` when the record is first persisted, ahead of
        any activity row, so every render this key can ever gate already carries
        them. That is what makes the omission safe; "it is constant" alone would
        not have been.

        **The enriched ticket status is deliberately NOT here, and it is the one
        rendered field that cannot be.** This key is computed on the poll thread,
        purely over the row; a ticket's live status is a forge GET, and putting
        network I/O behind a per-tick fingerprint is the one thing the
        observe/dispatch split exists to prevent. So a pull request that merges
        while nothing else moves does not itself schedule a PATCH — it is picked
        up by the next flush any other change causes, or immediately by
        ``@grove status``. Bounded and stated rather than silent: unlike
        ``phase``, whose absence here was invisible, this one has a name and a
        remedy."""
        ws = row.state
        primary = row.primary
        phase = row.phase
        return (
            primary.state if primary is not None else None,
            primary.current_task if primary is not None else None,
            primary.tool_calls if primary is not None else 0,
            primary.assistant_replies if primary is not None else 0,
            ws.branch,
            row.recent_commits[0].sha if row.recent_commits else None,
            # The phase must be here even though the poll's own fingerprint
            # already carries it: that one feeds the SSE stream, this one alone
            # decides whether a PATCH is scheduled. Every other member above is a
            # by-product of the agent *working* — a tool call, a reply, a commit
            # — so a phase set through the CLI, MCP or HTTP moved none of them
            # and the comment that renders the phase never updated until
            # something unrelated happened to change. Masked while an agent is
            # mid-run, which is why it survived; the failure is exactly the two
            # quiet cases the axis exists for — "reported, then stopped", and "a
            # human corrected it from outside".
            #
            # Only (phase, note), never the whole report: ``updated_at`` is the
            # phase file's mtime, so an agent rewriting an identical phase would
            # move this key and buy a redundant PATCH against a forge that
            # rate-limits same-comment edits.
            phase.phase if phase is not None else None,
            phase.note if phase is not None else None,
        )

    # ─── comment-id memory (lock-guarded) ────────────────────────────────────

    def _comment_id(self, workspace_id: str, key: _TargetKey) -> str | None:
        with self._lock:
            rec = self._state.get(workspace_id)
            return rec.comment_ids.get(key) if rec is not None else None

    def _store_comment_id(self, workspace_id: str, key: _TargetKey, comment_id: str) -> None:
        with self._lock:
            self._state.setdefault(workspace_id, _WsPub()).comment_ids[key] = comment_id

    def _forget_comment_id(self, workspace_id: str, key: _TargetKey) -> None:
        with self._lock:
            rec = self._state.get(workspace_id)
            if rec is not None:
                rec.comment_ids.pop(key, None)

    # ─── dispatch + scheduling seams ─────────────────────────────────────────

    def _run(self, fn: Callable[[_FlushJob], None], job: _FlushJob) -> None:
        """Off-thread when bound (dispatch worker), inline when not (the test seam).

        A timer thread already past :meth:`close`'s ``cancel()`` can reach here
        after the pool has shut down; ``submit`` then raises ``RuntimeError`` on
        that Timer thread. Swallow it to a debug line — a post-shutdown fire has
        nothing left to flush, and it must never surface as an uncaught error.
        """
        pool = self._pool
        if pool is None:
            fn(job)
            return
        try:
            pool.submit(fn, job)
        except RuntimeError as exc:  # pool shut down between the read and the submit
            logger.debug("issueops dispatch after shutdown, dropped: {}", exc)

    def _arm(self) -> None:
        """Schedule one flush a window from now — only while bound to a live bus.

        Unbound (tests), the timer never arms: the coalescing test drives
        :meth:`flush_pending` on its own clock. Bound, a single in-flight timer
        coalesces every dirty workspace into one drain, re-arming while work
        remains — so a comment is PATCHed at most once per window.
        """
        if not self._scheduling:
            return
        with self._lock:
            if self._timer is not None:
                return
            self._timer = threading.Timer(self._window.total_seconds(), self._on_timer)
            self._timer.daemon = True
            self._timer.start()

    def _on_timer(self) -> None:
        with self._lock:
            self._timer = None
        self.flush_pending(self._clock())
        with self._lock:
            pending = any(rec.dirty for rec in self._state.values())
        if pending:
            self._arm()

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(tz=UTC)


__all__ = [
    "SIGNATURE_MARKER",
    "STICKY_MARKER",
    "PhaseResolver",
    "ProviderResolver",
    "PublishSnapshot",
    "TicketStatusPublisher",
    "TodoResolver",
]
