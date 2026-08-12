"""The trace forwarder — ``trace.py``'s composition root on the activity bus.

:class:`TraceInstrumentor` can replay a session into LangFuse but nothing ever
called it: this is the long-lived owner that does, riding the
:class:`~grove.core.activity.ActivityService` delta bus exactly like
:class:`~grove.core.notifications.broker.NotificationBroker`. No new timer, no
new SSE frame type, no new status computation — a forward is an *edge* in what
already flows past it.

**What this tier exists to add is CONTEXT, and it is the half nothing else can
supply.** Tier 1 stamps ``OTEL_RESOURCE_ATTRIBUTES`` into the agent's launch
env, so an agent's own export carries the workspace identity it was born with —
frozen at process start, by construction. The live half moves afterwards: the
task phase the agent reports, the branch it actually created, the tickets
attached later, the status the fleet reconciled. Those cannot be resource
attributes, so they ride a span Grove emits itself, joined to everything else on
``langfuse.session.id``.

**Content is a different question, and it has exactly one owner per session.**
The host's own baseline emitter for a harness — Claude Code's ``Stop`` hook —
reads the very same transcript this module would replay, and it runs *inside*
Grove workspaces. Two producers over one set of bytes means every turn appears
twice, under two unrelated trace trees, with nothing anywhere reporting a fault.
So content emission is gated on
:meth:`TelemetryConfig.content_owner_for <grove.core.config.TelemetryConfig.content_owner_for>`:
``external`` (the default) means Grove emits context and stays silent about
prompts and completions; ``grove`` means nothing else emits content for that
harness and Grove replays the whole tree. The choice is configuration and never
inferred — a probe across a process boundary is wrong in both directions.

**The two harnesses legitimately resolve differently, and which is right depends
on a deployment fact only its operator knows.** Claude Code has a baseline
emitter today (a host ``Stop`` hook that reads the same transcript and runs
inside Grove workspaces), so it stays ``external``. Codex has none unless a
deployment adopts the Langfuse Codex plugin — and its own native OTel cannot
close the gap, because Codex writes prompts and tool payloads to the OTLP LOGS
stream while Langfuse ingests only traces, so a Codex trace configured every
correct way is structurally blank. A deployment WITHOUT that plugin therefore
sets ``{"codex": "grove"}``, which makes this module the only thing that can
carry Codex content at all; one WITH it leaves codex unset and takes the
baseline. Grove ships neither as a default: the mechanism is here, the policy is
the operator's, and an answer nobody recorded is the failure mode this gate
exists to prevent.

Decision and I/O are split the way the broker splits them:

- :meth:`evaluate` — **pure** given the delta. Folds one ``DashboardDelta``
  against the context/in-flight memory and returns the work to do. Builds span
  *records* (pure value objects) and touches nothing else.
- :meth:`forward` — the export edge, run on a single-worker pool.
  :meth:`TraceInstrumentor.replay` full-parses a transcript and
  :meth:`SpanSink.flush` blocks on an OTLP round-trip; neither may run on the
  ~1 Hz activity poll thread, whose stall would freeze every repo's dashboard.
"""

from __future__ import annotations

import contextlib
import hashlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock
from typing import cast

from loguru import logger

from grove import __version__
from grove.core.activity import DashboardDelta, SessionActivity, WorkspaceActivity
from grove.core.agents import get_adapter
from grove.core.config import AgentKind, TelemetryConfig, UsagePricingConfig
from grove.core.otel_resource import project_name
from grove.core.registry import RepoRegistry
from grove.core.telemetry.semconv import (
    ChatMessage,
    GroveIdentityAttr,
    GroveLiveAttr,
    LangfuseAttr,
    TraceIdentity,
)
from grove.core.trace import (
    AttributeValue,
    SpanRecord,
    SpanSink,
    TraceInstrumentor,
    build_span_sink,
    derive_span_id,
    derive_trace_id,
    price_book_estimator,
)
from grove.core.usage._pricing import PriceBook
from grove.core.workspace import WorkspaceState

_CONTEXT_SPAN_NAME = "grove:context"
"""Stable name for the enriched root span. Deliberately not derived from the
workspace title: a renamed workspace must not read as a different observation."""

_CONTEXT_CHURN_KEYS: frozenset[str] = frozenset(
    {
        # phase.json's own `updated_at` moves every time an agent rewrites the
        # file, even when the reported `phase` itself is unchanged (a re-save
        # mid-task, a note edited without a phase change). Folding it into the
        # change gate mints a new context revision — a new span — for zero
        # semantic change, which was the single largest source of trace-list
        # pollution (#501: 8 of 14 traces in one live session were exactly
        # this). It still rides the emitted attributes below; only the GATE
        # that decides whether a revision is worth emitting ignores it.
        GroveLiveAttr.PHASE_UPDATED_AT,
    }
)
"""Keys that restate an already-covered fact and must not, on their own,
trigger a new context revision. See :meth:`TraceForwarder._evaluate_session`."""


@dataclass(slots=True, frozen=True)
class _Content:
    """What a content replay needs, captured at decision time.

    The state is carried rather than re-fetched because the pool thread must not
    re-enter the store for a row the poll has already resolved — and because
    ``transcript_scope`` and ``scan_cwds`` are both answers about *this* record.
    """

    state: WorkspaceState
    repo_root: Path
    kind: AgentKind
    identity: TraceIdentity


@dataclass(slots=True, frozen=True)
class _Forward:
    """One session's unit of forwarding work — decided pure, run on the pool.

    Either half may be absent: an unchanged context emits no context span, and a
    session whose content Grove does not own emits no content at all. A job with
    both halves empty is never produced.
    """

    session_id: str
    context: SpanRecord | None
    content: _Content | None


class TraceForwarder:
    """Owns the span sink + the per-session forwarding memory; turns deltas into traces.

    Long-lived alongside the daemon. ``bind`` wires it to an ``ActivityService``
    bus and starts the export worker; ``close`` unwinds both and drains the
    exporter. Built via :meth:`from_config` — ``None`` when telemetry is off or
    its credentials did not resolve, so the daemon stays oblivious to the policy
    and a disabled forwarder is indistinguishable from an absent one.
    """

    def __init__(
        self,
        *,
        cfg: TelemetryConfig,
        registry: RepoRegistry,
        sink: SpanSink,
        pricing: UsagePricingConfig | None = None,
    ) -> None:
        """``sink`` is the injection seam a test drives with an in-memory
        processor (:func:`~grove.core.trace.sink_from_processor`), so the whole
        span-construction path production takes is exercised without an
        exporter. The instrumentor is built *over the same sink* rather than
        beside it: two sinks would mean two OTLP exporters, two batch processors
        and two copies of the credentials warning for one logical exporter.

        ``pricing`` builds the one :class:`~grove.core.usage._pricing.PriceBook`
        this forwarder uses for the whole daemon lifetime (construction is a
        cheap in-memory sort over already-loaded config, never a file read, but
        it must still happen once here rather than per replay or per span) and
        wires it into the instrumentor via :func:`~grove.core.trace.price_book_estimator`.
        ``None`` (the default) leaves ``cost_estimator`` unset, exactly today's
        behaviour — this section owns no pricing config itself, only the
        `TelemetryConfig` slice; a caller with the resolved `GroveConfig` (e.g.
        `cfg.usage.pricing`) passes it here to turn cost estimation on.
        """
        self._cfg = cfg
        self._registry = registry
        self._sink = sink
        cost_estimator = price_book_estimator(PriceBook(pricing)) if pricing is not None else None
        self._instrumentor = TraceInstrumentor(cfg, sink=sink, cost_estimator=cost_estimator)
        # Per-session last-emitted context attributes — the change gate. A quiet
        # fleet emits no deltas at all, so this only guards the case where a
        # session is busy (a delta per tick) while its CONTEXT has not moved.
        self._context: dict[str, tuple[tuple[str, AttributeValue], ...]] = {}
        # Sessions whose replay is queued or running. One worker means the pool
        # itself serializes, but without this a fleet mid-burst queues one whole
        # transcript parse per tick per session behind a slow export.
        self._inflight: set[str] = set()
        self._lock = Lock()
        self._pool: ThreadPoolExecutor | None = None
        self._unsub: Callable[[], None] | None = None

    @classmethod
    def from_config(
        cls,
        cfg: TelemetryConfig,
        *,
        registry: RepoRegistry,
        pricing: UsagePricingConfig | None = None,
    ) -> TraceForwarder | None:
        """Build a forwarder from config, or ``None`` when there is nothing to do.

        ``None`` when telemetry is disabled, and ``None`` when it is enabled but
        the LangFuse credentials or the ``telemetry`` extra did not resolve —
        :func:`~grove.core.trace.build_span_sink` has already warned in that
        case, and it warns exactly once because the sink is built here, per
        owner, never per tick.

        ``pricing`` is forwarded to :meth:`__init__` verbatim — see there for
        why it is optional and what passing it does.
        """
        if not cfg.enabled:
            return None
        sink = build_span_sink(cfg)
        if sink is None:
            return None
        return cls(cfg=cfg, registry=registry, sink=sink, pricing=pricing)

    # ─── lifecycle ───────────────────────────────────────────────────────────

    def bind(
        self, subscribe: Callable[[Callable[[DashboardDelta], None]], Callable[[], None]]
    ) -> None:
        """Subscribe to a delta bus and start the export worker.

        Takes the bus's ``subscribe`` callable rather than the service, so the
        forwarder depends only on the bus shape and tests drive it with a stub —
        the ``NotificationBroker.bind`` contract verbatim.
        """
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="grove-trace")
        self._unsub = subscribe(self._on_delta)

    def close(self) -> None:
        """Unsubscribe, drain the worker, and force one last export.

        The final flush is what stops a daemon shutdown from dropping whatever
        the batch processor was still holding; best-effort like every other
        teardown step here, because a dead exporter must not fail a shutdown.
        """
        if self._unsub is not None:
            with contextlib.suppress(Exception):
                self._unsub()
            self._unsub = None
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None
        with contextlib.suppress(Exception):
            self._sink.flush()

    # ─── pure decision ───────────────────────────────────────────────────────

    def evaluate(self, delta: DashboardDelta) -> list[_Forward]:
        """Fold one delta into forwarding work. Pure; updates memory under the lock.

        Only ``session_activity`` deltas carry the recomputed
        ``WorkspaceActivity`` this reads — a ``workspace_changed`` delta is a
        lifecycle wake-up with no payload, and re-fetching one here would put a
        store read on the poll thread for a row the very next tick supplies.
        """
        if delta.kind != "session_activity":
            return []
        row = delta.workspace
        if row is None or delta.repo_root is None:
            return []
        repo_root = Path(delta.repo_root)
        with self._lock:
            return [
                job
                for session in row.sessions
                # Sub-agent threads (`parent_session_id` set) are itemized
                # DETAIL of the primary session, not independent top-level
                # conversations — the same discriminator `WorkspaceActivity
                # .needs_attention` already applies for the identical reason.
                # A context span reports WORKSPACE facts (phase, branch,
                # tickets, status): none of them are true of a thread on its
                # own, so emitting one per thread republished the same facts
                # once per sub-agent — a fleet of N sub-agents multiplied one
                # workspace's context traces by N+1 (#504). Content is not
                # lost by skipping these ids: `TraceInstrumentor.plan` already
                # discovers and nests every sub-agent thread INSIDE the
                # primary session's own replay (`_Fleet.of`, walking spawn
                # edges recursively), and re-claiming a thread id here as its
                # own "session" was already inert — its transcript is glob-
                # matched by `locate_transcripts`, but every message on it
                # carries `is_sidechain=True`, so `plan()`'s
                # `main_thread = (m for m in messages if not m.is_sidechain)`
                # filter always emptied it to zero manifests. This filter
                # only removes work that never produced a span.
                if session.session.parent_session_id is None
                and (job := self._evaluate_session(row, session, repo_root)) is not None
            ]

    def _evaluate_session(
        self, row: WorkspaceActivity, session: SessionActivity, repo_root: Path
    ) -> _Forward | None:
        """One session's two independent questions: has its context moved, and
        does Grove own its content."""
        session_id = session.session.session_id
        if not session_id:
            return None
        identity = self._identity(row, session)
        attributes = {**self._context_attributes(row, session), **identity.attributes()}
        attributes[LangfuseAttr.TRACE_TAGS] = identity.tags()
        # The gate excludes pure-restatement keys (see `_CONTEXT_CHURN_KEYS`)
        # so a timestamp moving alone never counts as a change; every other
        # key still emits every attribute, including the churn ones.
        gate_fingerprint = tuple(
            sorted(
                (key, value) for key, value in attributes.items() if key not in _CONTEXT_CHURN_KEYS
            )
        )
        context: SpanRecord | None = None
        if self._context.get(session_id) != gate_fingerprint:
            self._context[session_id] = gate_fingerprint
            context = self._context_span(row, session, attributes, gate_fingerprint)
        content = self._claim_content(row.state, session, repo_root, identity)
        if context is None and content is None:
            return None
        return _Forward(session_id=session_id, context=context, content=content)

    def _claim_content(
        self,
        state: WorkspaceState,
        session: SessionActivity,
        repo_root: Path,
        identity: TraceIdentity,
    ) -> _Content | None:
        """The #459 gate: Grove replays a transcript only where it OWNS that
        harness's content, and only when no replay for the session is already
        queued.

        Resolved from the session's own adapter kind rather than the workspace's
        configured agent, because the kind is what decides which baseline
        emitter is watching the same bytes.
        """
        kind = cast("AgentKind", session.session.adapter_kind)
        if self._cfg.content_owner_for(kind) != "grove":
            return None
        session_id = session.session.session_id
        if session_id in self._inflight:
            return None
        self._inflight.add(session_id)
        return _Content(state=state, repo_root=repo_root, kind=kind, identity=identity)

    def _context_span(
        self,
        row: WorkspaceActivity,
        session: SessionActivity,
        attributes: dict[str, AttributeValue],
        gate_fingerprint: tuple[tuple[str, AttributeValue], ...],
        /,
    ) -> SpanRecord:
        """The enriched root span: everything Tier 1's frozen resource
        attributes cannot say.

        Its trace id is derived from the SESSION alone
        (``f"{session_id}/context"``) — never from a revision — so every
        context update for one session lands as a sibling observation inside
        ONE trace, rather than minting a fresh single-span trace per attribute
        change (#501: 8 of 14 traces in one live session were exactly that,
        every one rendering with empty input/output). The span id stays
        per-revision, derived from the fingerprint that decided this revision
        was worth emitting, so revisions remain distinct, immutable siblings
        under that one trace — re-emitting a growing span is unsafe under
        LangFuse v4 and blurs which state was true when.

        This is never the trace :meth:`TraceInstrumentor.replay` emits into:
        the agent mints its own trace id from the transcript and Grove cannot
        reach it, so the two sides of one session join only through
        ``langfuse.session.id``, never a shared trace id. The span id's key
        space (``"agent"``, ``"grove-context"``) is disjoint from the replay's
        own, so the two can never collide even where they happen to share a
        trace by coincidence.

        Built through :meth:`SpanRecord.agent` and then widened rather than
        constructed field-by-field: that classmethod owns the
        ``langfuse.observation.*`` vocabulary, and re-spelling it here is how the
        two would drift.

        **It carries a rendered input and output, and that is not decoration.**
        LangFuse's session view lists a trace by its root observation's input
        and output, so a context trace without them is a row a human reads as
        empty and never opens — the tier is then paying to publish something
        nobody can navigate to. The brief (what this workspace was asked to do)
        is the input; the live state (where it has got to) is the output. Both
        are rendered from facts already resolved for this tick, so neither costs
        a read.
        """
        context_trace_key = f"{session.session.session_id}/context"
        revision = hashlib.sha256(repr(gate_fingerprint).encode()).hexdigest()[:16]
        brief, standing = self._context_content(row, session)
        base = SpanRecord.agent(
            trace_id=derive_trace_id(context_trace_key),
            span_id=derive_span_id(f"{context_trace_key}/{revision}", "agent", "grove-context"),
            parent_span_id=None,
            name=_CONTEXT_SPAN_NAME,
            start_time=row.observed_at,
            end_time=row.observed_at,
            agent_id=row.state.id,
            input_messages=brief,
            output_messages=standing,
        )
        return replace(base, attributes={**base.attributes, **attributes})

    @staticmethod
    def _context_content(
        row: WorkspaceActivity, session: SessionActivity
    ) -> tuple[tuple[ChatMessage, ...], tuple[ChatMessage, ...]]:
        """The workspace's brief and its standing report, as readable prose.

        Two deliberately different questions, which is why they are the input
        and the output of one observation rather than one blob: the brief is
        what a human asked for and barely changes, the report is where the work
        has got to and changes constantly. Rendering them as the two halves of
        an ``invoke_agent`` observation is what makes a session's context trace
        answer *"what is this workspace, and how is it going"* at a glance.

        Every line is omitted when its fact is absent, so a workspace with no
        description, no ticket and no phase renders as the few lines that ARE
        true rather than as a form with blanks — the same rule the attribute
        half follows one method down.

        **Every line here is Grove's OWN record — never a word of transcript.**
        Title, description, tickets, status, phase and todo counts are facts
        Grove wrote or counted; the agent's `current_task` is the one nearby
        fact that is transcript PROSE, and it is deliberately absent, because
        this span is emitted whatever ``content_owner`` says. Including it
        would make the content gate conditional on which axis a payload
        happened to arrive on, which is not a gate.
        """
        state = row.state
        brief = [f"workspace: {state.title}"]
        if state.description:
            brief.append(f"description: {state.description}")
        brief.extend(f"ticket: {ref.id} {ref.url}".rstrip() for ref in state.ticket_refs if ref.id)
        standing = [
            f"workspace status: {state.status.value}",
            f"agent state: {session.activity.state.value}",
        ]
        if row.phase is not None:
            phase = f"phase: {row.phase.phase}"
            standing.append(f"{phase} — {row.phase.note}" if row.phase.note else phase)
        if row.todo is not None and row.todo.total:
            standing.append(f"todo: {row.todo.completed}/{row.todo.total} complete")
        return (
            (ChatMessage.of_text("user", "\n".join(brief)),),
            (ChatMessage.of_text("assistant", "\n".join(standing)),),
        )

    @staticmethod
    def _identity(row: WorkspaceActivity, session: SessionActivity) -> TraceIdentity:
        """Who this session's spans belong to — the facts, and the tags they become.

        Built ONCE per session per tick and spent twice: on the context span
        this tier already emits, and on every span of the transcript replay,
        which until now carried no workspace identity at all and so could not
        be filtered by repo, branch or agent in the one place a human looks.
        Two producers, one object, so the two can never disagree about which
        workspace they are describing.

        ``branch`` is the LIVE branch rather than the record's create-time
        snapshot, for the same reason the context attributes use it: an agent
        that branched after create is exactly the case worth seeing. Grove's own
        version is the only version knowable here — the agent's needs a
        subprocess probe, which this tier runs per tick and must not pay for.
        """
        state = row.state
        return TraceIdentity(
            workspace_id=state.id,
            workspace_title=state.title,
            repo=Path(state.repo_root).name,
            project=project_name(state),
            branch=row.live_branch or state.branch,
            base_branch=state.base_branch,
            worktree=state.worktree_path,
            runtime=state.runtime.value,
            placement=state.placement.value,
            agent_name=state.agent_name,
            agent_kind=session.session.adapter_kind,
            orchestrator_version=__version__,
            ticket_ids=tuple(ref.id for ref in state.ticket_refs),
            phase=row.phase.phase if row.phase is not None else "",
        )

    @staticmethod
    def _context_attributes(
        row: WorkspaceActivity, session: SessionActivity
    ) -> dict[str, AttributeValue]:
        """What is true of this session RIGHT NOW, and could not have been at launch.

        Strictly the mutable half. The stable identity — workspace, repo,
        branch, runtime, agent — moved to :meth:`_identity`, which both this
        tier and the transcript replay now spend, so one workspace cannot
        describe itself two ways depending on which producer a reader opened.
        What is left here is exactly what a frozen resource attribute cannot
        express, which is this tier's whole reason to exist: a status, a live
        agent state, a phase note, a todo count, a discovered session id.

        An empty value is omitted rather than emitted blank, so "Grove had
        nothing to say" never renders as a real empty fact — and so the
        fingerprint derived from this dict does not churn on a field flickering
        between absent and "".
        """
        state = row.state
        candidates: dict[str, AttributeValue] = {
            # The correlation key, and the only non-`grove.*` fact here. It is
            # the WORKSPACE's join key rather than this session's own id,
            # because the agent's spans were stamped with it at launch and a
            # resource attribute cannot be revised afterwards — keying by the
            # discovered id publishes Grove's half into a session the agent's
            # half will never join. For `claude_code` the two are the same
            # value; for `codex`, whose native id Grove cannot pin, they are not.
            LangfuseAttr.SESSION_ID: state.telemetry_session_id,
            # The harness's OWN session id, recorded even when it is not the
            # join key above. For codex the two differ, and this is the only
            # place the native thread id — the one its rollout file is named
            # after, and the one a human greps for — is stated as a fact rather
            # than left to be inferred from a trace name.
            GroveLiveAttr.AGENT_SESSION_ID: session.session.session_id,
            # Trace-level in LangFuse's own rendering now that one session's
            # context revisions share one trace id (see `_context_span`), but
            # still derived from the mutable title on every revision: a rename
            # updates the displayed name on the next revision rather than
            # forking the trace, because the trace id itself never depends on
            # title — only this attribute's VALUE does.
            LangfuseAttr.TRACE_NAME: f"grove:{state.title}",
            GroveLiveAttr.WORKSPACE_STATUS: state.status.value,
            GroveLiveAttr.AGENT_STATE: session.activity.state.value,
            GroveIdentityAttr.SESSION_ID: state.tmux_session,
            GroveLiveAttr.TICKET_URLS: ",".join(ref.url for ref in state.ticket_refs if ref.url),
        }
        if row.phase is not None:
            candidates[GroveLiveAttr.PHASE_NOTE] = row.phase.note or ""
            candidates[GroveLiveAttr.PHASE_UPDATED_AT] = row.phase.updated_at.isoformat()
        if row.todo is not None:
            candidates[GroveLiveAttr.TODO_TOTAL] = row.todo.total
            candidates[GroveLiveAttr.TODO_COMPLETED] = row.todo.completed
        return {key: value for key, value in candidates.items() if value != ""}

    # ─── side effects ────────────────────────────────────────────────────────

    def forward(self, job: _Forward) -> None:
        """Emit one session's spans. The export edge — best-effort, never raises.

        A failure here is a debug line and nothing else: telemetry that breaks
        the poll loop costs the fleet its dashboard to protect an observability
        convenience, which is the wrong trade in every direction.
        """
        try:
            if job.context is not None:
                self._sink(job.context)
            if job.content is not None:
                self._replay(job.content, job.session_id)
            self._sink.flush()
        except Exception as exc:  # best-effort — mirrors dispatch()'s discipline
            logger.debug("trace forward failed for session {}: {}", job.session_id, exc)
        finally:
            if job.content is not None:
                with self._lock:
                    self._inflight.discard(job.session_id)

    def _replay(self, content: _Content, session_id: str) -> None:
        """Replay the session's spine, scoped like every other transcript read.

        ``transcript_scope`` is not optional here: a workspace launched under a
        pinned ``CLAUDE_CONFIG_DIR`` resolves its transcripts somewhere the
        daemon's own environment never looks, so an unscoped read silently finds
        nothing and the trace is simply empty. The scan is the ``scan_cwds``
        union for the same reason discovery uses it — the adapters exact-match a
        recorded cwd, and a nested project's session is recorded under one of the
        two. A wrong cwd locates no transcript and costs a directory read; a
        second matching one is stopped by the instrumentor's own watermark.

        The adapter is resolved by kind and read polymorphically: ``read_messages``
        is on the shared :class:`~grove.core.agents.base.AgentAdapter` Protocol,
        so a kind with no message spine (a remote session, a bare shell) answers
        ``()`` and the replay emits nothing. Naming such a kind as a content
        owner is a config statement Grove cannot honour; it degrades to
        context-only here, and ``grove doctor`` is where the operator is told —
        this runs per tick and has nowhere to say it once.
        """
        adapter = get_adapter(content.kind)
        manager = self._registry.get(content.repo_root)
        for cwd in content.state.scan_cwds:
            with manager.transcript_scope(content.state):
                self._instrumentor.replay(
                    cwd,
                    session_id,
                    adapter,
                    session_group_id=content.state.telemetry_session_id,
                    identity=content.identity,
                )

    # ─── internal ────────────────────────────────────────────────────────────

    def _on_delta(self, delta: DashboardDelta) -> None:
        """Bus callback — decide on the emitting thread, export off the pool."""
        pool = self._pool
        if pool is None:
            return
        for job in self.evaluate(delta):
            pool.submit(self.forward, job)
