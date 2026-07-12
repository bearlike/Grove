"""Cross-project activity aggregation — the hub both clients consume.

The ``ActivityService`` is the tool-agnostic top of the Activity Dashboard
(epic #11 §3). It enumerates every workspace across every repo (via the
``RepoRegistry``), resolves each workspace's agent session(s), parses their
activity through the agent adapters, **blends** the transcript-derived status
with Grove's existing tmux-driven workspace reconciliation into one
``AgentActivityState``, attaches cheap diff stats, and exposes:

- ``snapshot()`` — one grouped-by-project picture for a render, and
- ``subscribe()`` + ``poll_once()`` — a delta bus the daemon (SSE) and the TUI
  (in-process) drive on a tick.

One engine source, two renderers. The service produces plain dataclasses (engine
IR); the ``contracts.activity`` Views serialize them for the wire. Best-effort
throughout (the peek contract): an unreadable transcript or a dead tmux session
degrades a field, it never breaks the snapshot.
"""

from __future__ import annotations

import contextlib
import itertools
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from loguru import logger

from grove.core import paths as core_paths
from grove.core.agents import (
    AgentActivity,
    AgentActivityState,
    AgentQuestion,
    AgentSession,
    SessionProvenance,
    get_adapter,
)
from grove.core.agents.base import AgentAdapter
from grove.core.agents.claude_code import ClaudeCodeAdapter
from grove.core.agents.hook import DEFAULT_SIDECAR_MAX_AGE_SECONDS, ClaudeHook, HookRecord
from grove.core.git import GitRepo
from grove.core.manager import WorkspaceEvent, WorkspaceManager
from grove.core.registry import RepoRegistry
from grove.core.workspace import CommitSummary, WorkspaceState, WorkspaceStatus

DeltaKind = Literal["workspace_changed", "session_activity"]


# ─── engine IR (in-process; the contracts.activity Views mirror these) ──────


@dataclass(slots=True, frozen=True)
class LiveCounters:
    """In-flight token counters for a session mid-generation (#181).

    Distinct from ``AgentActivity.tokens_in``/``tokens_out`` (the
    transcript-derived totals, settled once a turn flushes): this is a
    *faster* tier a client renders WHILE generating, sourced from whatever
    live side-channel is active — the #177 wire-truth proxy is the primary
    source, with partial-message deltas or OTel metrics as fallbacks. The
    whole block is ``None`` on ``SessionActivity.live`` when no such tier is
    reporting (hidden, never zeroed) — see ``ActivityService._live_counters``.
    """

    tokens_in: int
    tokens_out: int
    generating_since: datetime


@dataclass(slots=True, frozen=True)
class SessionActivity:
    """One agent session paired with its computed activity.

    ``live`` is the optional #181 live-counters block — ``None`` until a live
    tier is wired (#177); the wire mirror hides the whole block rather than
    showing zeros.
    """

    session: AgentSession
    activity: AgentActivity
    live: LiveCounters | None = None


@dataclass(slots=True, frozen=True)
class WorkspaceActivity:
    """A workspace's dashboard row: reconciled state + its sessions + cheap stats.

    ``pane_target`` is the tmux target a client promotes to a live pane on
    hover/focus; ``None`` when the session isn't live. ``base_ahead`` / ``diff_*``
    are the cheap ``--shortstat`` totals for the card — the full diff stays on the
    peek focus path (claude-squad #280: never load full diffs for a list).

    ``dirty_files`` counts uncommitted paths in the worktree — the live "the
    agent is editing" signal that precedes any commit.

    ``recent_commits`` is the durable "latest activity" signal: the last few
    commits on the workspace branch, newest first. The card reads
    ``recent_commits[0]`` ("what was done, and when it was committed") as its
    latest-activity line — commits are precise and timestamped, where the
    transcript-derived task text is ephemeral. ``observed_at`` is when this row
    was produced (the per-card "updated Xs ago"); the dashboard-wide refresh time
    is ``DashboardSnapshot.generated_at``.
    """

    state: WorkspaceState
    sessions: tuple[SessionActivity, ...]
    base_ahead: int
    base_behind: int
    diff_added: int
    diff_removed: int
    dirty_files: int
    pane_target: str | None
    recent_commits: tuple[CommitSummary, ...]
    observed_at: datetime

    @property
    def primary(self) -> AgentActivity | None:
        """The first (Grove-launched) session's activity, or ``None`` if untracked."""
        return self.sessions[0].activity if self.sessions else None

    @property
    def needs_attention(self) -> bool:
        # Sub-agent fleet entries (#173, ``AgentSession.parent_session_id`` set)
        # are itemized DETAIL, not separate top-level conversations: a worker
        # that finished normally settles to WAITING, which is exactly an
        # ATTENTION_STATE for a real human-facing session but means nothing of
        # the sort for a sub-agent — every workspace that ever ran one to
        # completion would otherwise spuriously ping "needs me". Only a
        # top-level session (grove_launched or a hand-started/discovered extra)
        # can raise this.
        return any(
            s.activity.needs_attention for s in self.sessions if s.session.parent_session_id is None
        )

    @property
    def fingerprint(self) -> tuple[object, ...]:
        """Cheap change-key for delta detection — what a poll compares tick to tick.

        ``observed_at`` is deliberately excluded: it changes every tick and would
        make every card emit a delta on every poll. The last commit sha is
        included so a fresh commit streams promptly even when it doesn't move the
        ahead/behind counts (an amend on the tip).
        """
        return (
            self.state.status,
            self.diff_added,
            self.diff_removed,
            self.dirty_files,
            self.base_ahead,
            self.base_behind,
            self.recent_commits[0].sha if self.recent_commits else None,
            # Every session, not just the primary: a hand-started secondary's
            # state change must stream too, and a sessions set going empty (a
            # discovery miss) is itself a change worth emitting.
            tuple(
                (
                    s.activity.state,
                    s.activity.last_event_at,
                    s.activity.assistant_replies,
                    s.activity.tool_calls,
                    # A live question batch appearing or resolving must stream at
                    # once — it can arrive without a state change (the capturing
                    # PreToolUse is WORKING, like the tool call it gates).
                    tuple(q.id for q in s.activity.questions),
                    # Live counters (#181): included so a client sees the token
                    # count tick up ~1Hz while generating, and sees the block
                    # disappear the moment a live tier stops reporting (e.g. the
                    # turn flushed and the transcript's own totals took over).
                    s.live.tokens_in if s.live is not None else None,
                    s.live.tokens_out if s.live is not None else None,
                )
                for s in self.sessions
            ),
        )


@dataclass(slots=True, frozen=True)
class ProjectGroup:
    """One project's workspace rows, the dashboard's default grouping.

    ``repo_root`` is the git repo that anchors the worktrees; ``cwd`` is the
    project's working directory. For a top-level repo ``cwd == repo_root``; for a
    nested project (#101) ``cwd`` is a subdirectory, so several groups can share
    one ``repo_root`` and are distinguished by ``cwd``. ``repo_name`` stays the
    repo's basename; clients show ``cwd`` (or its tail) to disambiguate siblings.
    """

    repo_root: str
    repo_name: str
    cwd: str
    workspaces: tuple[WorkspaceActivity, ...]


@dataclass(slots=True, frozen=True)
class DashboardSnapshot:
    """The whole cross-project picture for one render."""

    projects: tuple[ProjectGroup, ...]
    generated_at: datetime

    def iter_workspaces(self) -> Iterator[WorkspaceActivity]:
        for group in self.projects:
            yield from group.workspaces

    @property
    def total_workspaces(self) -> int:
        return sum(len(g.workspaces) for g in self.projects)

    @property
    def needs_attention(self) -> int:
        return sum(1 for w in self.iter_workspaces() if w.needs_attention)


@dataclass(slots=True, frozen=True)
class DashboardDelta:
    """A change on the activity bus (engine event; the wire ``DashboardEvent`` mirrors it).

    ``workspace_changed`` is a lifecycle wake-up (create/kill/pause/…) — payload is
    ``None`` and the client re-fetches, mirroring ``WorkspaceEvent``'s pull model.
    ``session_activity`` carries the freshly recomputed ``WorkspaceActivity`` so a
    client can patch a single card without a full re-fetch.
    """

    kind: DeltaKind
    seq: int
    workspace_id: str
    repo_root: str | None = None
    workspace: WorkspaceActivity | None = None
    detail: dict[str, str] = field(default_factory=dict)


class ActivityService:
    """Aggregates workspaces, sessions, and activity across every repo.

    Holds no workspace state of its own — it reads through the ``RepoRegistry``
    and the agent adapters on demand. The only state it keeps is the subscriber
    list, the per-workspace change fingerprints (for ``poll_once`` delta
    detection), a monotonic ``seq``, and a small ``GitRepo`` cache.

    The timer lives at the edge: the daemon and the TUI own the tick and call
    ``poll_once()``; the service keeps time-of-day out of its core, exactly like
    the manager keeps side effects at the boundary.
    """

    def __init__(self, *, registry: RepoRegistry) -> None:
        self._registry = registry
        self._subs: list[Callable[[DashboardDelta], None]] = []
        # Repos whose manager bus we've already bridged, so re-scanning for new
        # repos never double-subscribes.
        self._bridged: set[Path] = set()
        self._bridge_unsubs: list[Callable[[], None]] = []
        # itertools.count is atomic under the GIL, so the daemon can stamp event
        # ids from a worker thread (poll_once in an executor) and the loop thread
        # (SSE snapshot frames) without a lock or a torn counter.
        self._seq = itertools.count(1)
        self._last_fingerprint: dict[str, tuple[object, ...]] = {}
        # Last definitive (state, settled_at) per session id — the hysteresis
        # memory that keeps a transient read failure (mid-write JSONL tail,
        # remote /events timeout) from flashing a live session back to
        # STARTING/UNKNOWN. Unbounded like the registry: loopback-only, small N,
        # tiny entries.
        self._settled: dict[str, tuple[AgentActivityState, datetime]] = {}
        self._git_cache: dict[Path, GitRepo] = {}

    # ─── snapshot ──────────────────────────────────────────────────────────

    def snapshot(self) -> DashboardSnapshot:
        """One grouped-by-project picture across every known repo.

        Reuses ``RepoRegistry.known_roots()`` + per-manager ``list()`` (which
        already reconciles workspace status), then layers agent activity on top.
        Bounded subprocess work per running workspace — the same discipline the
        peek rail already follows.
        """
        self._ensure_bridged()
        # One group per known *project* (#101). Several nested projects can share
        # one repo root — and thus one Manager — so workspaces are listed once per
        # repo and split into groups by their persisted ``project_subpath``. A
        # workspace whose subpath matches no declared project still gets its own
        # implied group (no row is ever dropped); every declared project appears
        # even when empty (the #95 visibility contract, carried per-project).
        seeded: dict[Path, dict[str, Path]] = {}  # repo_root → {subpath: project_cwd}
        for project in self._registry.known_projects():
            root = project.repo_root.resolve()
            sub = self._subpath(root, project.cwd)
            seeded.setdefault(root, {})[sub] = project.cwd
        groups: list[ProjectGroup] = []
        for root, declared in seeded.items():
            mgr = self._registry.get(root)
            cwds = dict(declared)  # subpath → project cwd (seeds empty groups)
            rows_by_sub: dict[str, list[WorkspaceActivity]] = {sub: [] for sub in cwds}
            for state in mgr.list():
                sub = state.project_subpath
                cwds.setdefault(sub, root / sub if sub else root)
                rows_by_sub.setdefault(sub, []).append(self._workspace_activity(mgr, state))
            for sub, cwd in cwds.items():
                groups.append(
                    ProjectGroup(
                        repo_root=str(root),
                        repo_name=root.name,
                        cwd=str(cwd),
                        workspaces=tuple(rows_by_sub.get(sub, [])),
                    )
                )
        groups.sort(key=lambda g: g.cwd)
        return DashboardSnapshot(projects=tuple(groups), generated_at=_utcnow())

    @staticmethod
    def _subpath(repo_root: Path, cwd: Path) -> str:
        """POSIX subpath of ``cwd`` under ``repo_root`` ("" when they're equal).

        Mirrors ``WorkspaceManager._project_subpath`` so a declared project's cwd
        keys the same group its workspaces (whose ``project_subpath`` the manager
        derived the same way) land in."""
        try:
            rel = cwd.resolve().relative_to(repo_root)
        except ValueError:
            return ""
        posix = rel.as_posix()
        return "" if posix == "." else posix

    # ─── delta bus ─────────────────────────────────────────────────────────

    def subscribe(self, callback: Callable[[DashboardDelta], None]) -> Callable[[], None]:
        """Register a delta callback. Returns an unsubscribe handle (idempotent)."""
        self._ensure_bridged()
        self._subs.append(callback)

        def _unsub() -> None:
            with contextlib.suppress(ValueError):
                self._subs.remove(callback)

        return _unsub

    def poll_once(self) -> None:
        """Recompute activity, emit a ``session_activity`` delta per changed workspace.

        The edge (daemon lifespan task / TUI ticker) calls this on a slow
        interval; transcript and pane changes aren't lifecycle events, so this is
        what streams them. Membership changes (create/kill) arrive promptly via
        the bridged manager bus; this catches the in-place activity drift.
        """
        self._ensure_bridged()
        fresh: dict[str, tuple[object, ...]] = {}
        for root in self._registry.known_roots():
            mgr = self._registry.get(root)
            for state in mgr.list():
                row = self._workspace_activity(mgr, state)
                fingerprint = row.fingerprint
                fresh[state.id] = fingerprint
                if self._last_fingerprint.get(state.id) != fingerprint:
                    self._emit(
                        DashboardDelta(
                            kind="session_activity",
                            seq=self.next_seq(),
                            workspace_id=state.id,
                            repo_root=str(root),
                            workspace=row,
                        )
                    )
        self._last_fingerprint = fresh

    def close(self) -> None:
        """Tear down all manager-bus bridges and subscribers (daemon shutdown)."""
        for unsub in self._bridge_unsubs:
            with contextlib.suppress(Exception):
                unsub()
        self._bridge_unsubs.clear()
        self._bridged.clear()
        self._subs.clear()

    # ─── per-workspace computation ─────────────────────────────────────────

    def _workspace_activity(
        self, mgr: WorkspaceManager, state: WorkspaceState
    ) -> WorkspaceActivity:
        sessions = self.sessions_for(mgr, state)
        git = self._git_for(Path(state.repo_root))
        try:
            ahead, behind = git.ahead_behind(state.branch, state.base_branch)
        except Exception as exc:  # best-effort: never break the snapshot
            logger.debug("activity ahead_behind({}) failed: {}", state.id, exc)
            ahead = behind = 0
        try:
            added, removed = git.diff_stats(state.branch, state.base_branch)
        except Exception as exc:
            logger.debug("activity diff_stats({}) failed: {}", state.id, exc)
            added = removed = 0
        try:
            # Uncommitted churn — in the fingerprint, so an agent editing files
            # streams a delta before anything is committed.
            dirty = git.dirty_file_count(Path(state.worktree_path))
        except Exception as exc:
            logger.debug("activity dirty_file_count({}) failed: {}", state.id, exc)
            dirty = 0
        try:
            pane_target = mgr.pane_target(state.id)
        except Exception as exc:
            logger.debug("activity pane_target({}) failed: {}", state.id, exc)
            pane_target = None
        try:
            # The durable latest-activity signal — one cheap `git log -3` per row,
            # same per-tick discipline as ahead_behind/diff_stats above.
            commits = git.recent_commits(state.branch, limit=3)
        except Exception as exc:  # best-effort: never break the snapshot
            logger.debug("activity recent_commits({}) failed: {}", state.id, exc)
            commits = ()
        return WorkspaceActivity(
            state=state,
            sessions=tuple(sessions),
            base_ahead=ahead,
            base_behind=behind,
            diff_added=added,
            diff_removed=removed,
            dirty_files=dirty,
            pane_target=pane_target,
            recent_commits=commits,
            observed_at=_utcnow(),
        )

    def sessions_for(self, mgr: WorkspaceManager, state: WorkspaceState) -> list[SessionActivity]:
        """The workspace's agent session(s) with blended activity.

        Public seam with two consumers — the daemon's poll (via
        ``_workspace_activity``) and the TUI list screen's slow tick — so the
        blend + hook-sidecar policy stays in this single site.

        Discovery is a read-only fs glob over ``state.scan_cwds`` — the union of
        ``agent_cwd`` (``worktree/subpath``, where the agent runs) and the
        worktree root (a session hand-started at the repo root of a nested
        project records *its* cwd, #F7). It **always runs**, adapter-gated only
        (generic/shell discover nothing): ``cfg.hooks.enabled`` gates the sidecar
        push, never read-only discovery (#119).

        Two paths, by whether Grove minted a deterministic id at create:

        - **Minted id present** (the #13 happy path): that ``grove_launched``
          session is the primary; only the discovered sessions this workspace
          *adopts* ride along as ``fs_discovered`` extras. Candidates are
          pre-filtered on CHEAP head metadata (birth) + the sidecar BEFORE any
          full parse (#F5), so per-tick cost is O(new sessions), not O(history) —
          a historical transcript predating the workspace is excluded from the
          card entirely (the "adopt only what's ours; the rest is noise"
          precedent). Exception: a minted id that is a *dead pointer* yields the
          primary slot to the newest adopted session; the minted entry rides
          behind and reclaims primary the moment it materializes.
        - **No minted id** — a workspace whose agent wasn't ``kind="claude_code"``
          at create, one created before minting existed, or a purely hand-started
          run. With no minted session there is no reference pane, so adoption is
          birth-only (#F1); adopt the single most-recent session that passes.
          Nothing adopted (every candidate predates this workspace, e.g. a reused
          ROOT cwd) → honestly sessionless.

        Adoption weighs transcript birth AND a hook sidecar proving the session
        was live in this workspace's own PANE after creation
        (`ClaudeHook.adopts`) — the sidecar arm lets a session *resumed* inside
        the pane (born before the workspace) be adopted, and the pane check keeps
        a shared cwd (ROOT placement) from adopting another live workspace's
        session (#F1).
        """
        kind = self._effective_kind(mgr, state)
        adapter = get_adapter(kind)
        now = _utcnow()

        if state.agent_session_id:
            # Read the minted sidecar ONCE (#F9): it drives the minted blend,
            # the dead-pointer test, AND is the reference pane every candidate's
            # adoption is verified against (#F1).
            minted_sidecar = ClaudeHook.read(
                state.agent_session_id, sidecar_dir=core_paths.agent_sidecar_dir()
            )
            reference_pane = minted_sidecar.tmux_pane if minted_sidecar is not None else None
            minted = self._session_activity(
                mgr,
                state,
                kind,
                state.agent_session_id,
                "grove_launched",
                now,
                sidecar=minted_sidecar,
                cwd=state.agent_cwd,
            )
            minted_fleet = self._fleet_entries(kind, state.agent_session_id, state.agent_cwd)
            # Only the adopted concurrent sessions ride along; the cheap pre-filter
            # already dropped history, so a full parse is paid per NEW session, not
            # per historical transcript in the cwd (#F5).
            extras: list[SessionActivity] = []
            extras_fleet: list[SessionActivity] = []
            for sid, cwd, sidecar in self._adopted_candidates(
                adapter, state, reference_pane=reference_pane, exclude_id=state.agent_session_id
            ):
                extras.append(
                    self._session_activity(
                        mgr, state, kind, sid, "fs_discovered", now, sidecar=sidecar, cwd=cwd
                    )
                )
                extras_fleet.extend(self._fleet_entries(kind, sid, cwd))
            if self._minted_unmaterialized(adapter, minted, minted_sidecar, now) and extras:
                # The minted --session-id is a dead pointer (rotated by `/clear`,
                # hand-restarted, or ended). Extras are already adoption-filtered
                # and newest-first, so the head is the live session to promote;
                # the minted entry rides behind and reclaims primary the moment
                # it materializes.
                return [*extras, *extras_fleet, minted, *minted_fleet]
            return [minted, *minted_fleet, *extras, *extras_fleet]

        # No minted id: recover the *live* session by discovery — birth-only, as
        # there is no minted session to source a reference pane from (#F1). The
        # pre-filter walks scan_cwds newest-first and returns adopted candidates
        # only, so we full-parse just the most-recent one (the rest is noise).
        for sid, cwd, sidecar in self._adopted_candidates(
            adapter, state, reference_pane=None, exclude_id=None
        ):
            primary = self._session_activity(
                mgr, state, kind, sid, "fs_discovered", now, sidecar=sidecar, cwd=cwd
            )
            return [primary, *self._fleet_entries(kind, sid, cwd)]
        return []

    @staticmethod
    def _fleet_entries(kind: str, session_id: str, cwd: Path) -> list[SessionActivity]:
        """Itemized in-session sub-agent fleet members for one session (#173) —
        additional ``WorkspaceActivityView.sessions`` rows alongside the
        primary/adopted-extra entries, each an ordinary ``SessionActivity`` (its
        ``AgentSession.parent_session_id`` points back at ``session_id``) so no
        new wire shape is needed. The existing ``active_subagents`` COUNT is
        untouched — it stays derived inside ``parse_activity`` — this only adds
        the itemized detail alongside it.

        Claude-only by construction (the in-session sidechain fleet is a Claude
        Code transcript concept, like ``build_answer_keys``'s direct
        ``ClaudeCodeAdapter`` reference in ``manager.py``); every other kind is
        a cheap no-op. A CLI ``--bg`` background run is a separate top-level
        session, not a sub-agent thread, so it is out of scope here.
        """
        if kind != "claude_code":
            return []
        return [
            SessionActivity(session=session, activity=fleet_activity)
            for session, fleet_activity in ClaudeCodeAdapter().fleet_activity(cwd, session_id)
        ]

    @staticmethod
    def _effective_kind(mgr: WorkspaceManager, state: WorkspaceState) -> str:
        """The adapter kind for ``state`` — persisted at create, else config.

        Prefer the kind persisted at create: it resolves even when the agent is
        scoped to a repo's project config the daemon never loads (a "Work"
        profile defined only in private-repos). Fall back to a config lookup for
        legacy records written before ``agent_kind`` existed.
        """
        if state.agent_kind is not None:
            return state.agent_kind
        agent = mgr.config.find_agent(state.agent_name)
        return agent.kind if agent is not None else "generic"

    def _adopted_candidates(
        self,
        adapter: AgentAdapter,
        state: WorkspaceState,
        *,
        reference_pane: str | None,
        exclude_id: str | None,
    ) -> list[tuple[str, Path, HookRecord | None]]:
        """Discovered sessions this workspace adopts — cheaply pre-filtered (#F5).

        Unions ``discover_births`` across ``state.scan_cwds`` (#F7), sorts the
        result newest-first by mtime, then applies the adoption gate on the CHEAP
        head metadata (birth) plus the sidecar — reading each candidate's sidecar
        exactly once (#F9) — BEFORE the caller pays any full transcript parse.
        Returns ``(session_id, cwd, sidecar)`` for the passers, newest-first: the
        cwd the session was discovered under (so its parse and pane check key on
        the right directory across a nested project's union) and the already-read
        sidecar (so the caller reuses it for the blend). A candidate that predates
        the workspace and left no pane-live sidecar is dropped here and never
        full-parsed.
        """
        merged: list[tuple[str, datetime | None, float, Path]] = []
        for cwd in state.scan_cwds:
            for sid, born_at, mtime in adapter.discover_births(cwd, exclude_id=exclude_id):
                merged.append((sid, born_at, mtime, cwd))
        merged.sort(key=lambda t: -t[2])  # newest-first by mtime across the union
        out: list[tuple[str, Path, HookRecord | None]] = []
        seen: set[str] = set()
        for sid, born_at, _mtime, cwd in merged:
            if sid in seen:
                continue
            seen.add(sid)
            sidecar = ClaudeHook.read(sid, sidecar_dir=core_paths.agent_sidecar_dir())
            if ClaudeHook.adopts(
                state, born_at, candidate=sidecar, reference_pane=reference_pane, cwd=cwd
            ):
                out.append((sid, cwd, sidecar))
        return out

    def _minted_unmaterialized(
        self,
        adapter: AgentAdapter,
        minted: SessionActivity,
        sidecar: HookRecord | None,
        now: datetime,
    ) -> bool:
        """Whether the minted ``--session-id`` is a dead pointer eligible for recovery.

        The minted id carries no live work in three shapes: an in-process
        rotation (`/clear` mints a fresh id in the same claude), a hand-restart,
        or the session ending outright. A materialized session — one with a
        transcript on disk — is NEVER a dead pointer (#F6): a just-remapped or
        resumed session whose last hook event is ``SessionEnd`` has a transcript
        and must stay primary, showing its honest idle/done state, rather than
        being demoted against the trusted manual pin. So both detectors require
        no transcript:

        - **No transcript, blend STARTING/UNKNOWN** — nothing was written under
          this id and no sidecar settled it, so it's honestly empty.
        - **No transcript, latest sidecar event ``SessionEnd``** — the session is
          over even though ``SessionEnd`` settles the blend to IDLE (not
          STARTING/UNKNOWN); without this the dead pointer read as a live-but-quiet
          session and recovery never ran (the 2026-07-05 incident). Gated on the
          sidecar still superseding the poll, so a transcript that somehow outran
          the ``SessionEnd`` keeps the id materialized.

        The threaded ``sidecar`` is the minted session's, read once by the caller
        (#F9). Remote adapters never recover this way (no local session to rotate).
        """
        if adapter.remote or minted.session.transcript_path is not None:
            return False
        if (
            sidecar is not None
            and sidecar.event == "SessionEnd"
            and sidecar.supersedes_poll(now=now, transcript_at=minted.activity.last_event_at)
        ):
            return True
        return minted.activity.state in (
            AgentActivityState.STARTING,
            AgentActivityState.UNKNOWN,
        )

    def _session_activity(
        self,
        mgr: WorkspaceManager,
        state: WorkspaceState,
        kind: str,
        session_id: str,
        provenance: SessionProvenance,
        now: datetime,
        *,
        sidecar: HookRecord | None,
        cwd: Path,
    ) -> SessionActivity:
        adapter = get_adapter(kind)
        # ``cwd`` is the directory this session was discovered under — agent_cwd
        # for the minted session, the discovered cwd for an extra (which may be
        # the worktree root for a nested project's root-recorded session, #F7).
        # Both filesystem adapters exact-string-match it, so keying locate/parse
        # off the wrong directory would miss the transcript.
        #
        # The ``sidecar`` is pre-read once per session per tick (#F9) and reused
        # for both the push-status override and the pending-question surface.
        # locate stays alongside the (cwd, session_id)-keyed parse: the paths
        # feed the displayed transcript_path and the STARTING detection below.
        paths = adapter.locate_transcripts(cwd, session_id)
        transcript = adapter.parse_activity(cwd, session_id)
        # Remote adapters surface state with no local file, so "materialized"
        # can't mean "a file exists" — UNKNOWN-and-fileless is the only true
        # STARTING window.
        has_transcript = bool(paths) or transcript.state is not AgentActivityState.UNKNOWN
        # A headless workspace (#146) has no tmux pane, exactly like a remote
        # adapter: the local pane says nothing about the agent's work, so the
        # transcript/adapter is the sole live-state authority. Fold it into the
        # blend's `remote` (pane-not-authoritative) arm — but ONLY the blend;
        # `_minted_unmaterialized` still uses `adapter.remote` directly, since a
        # headless claude_code transcript can still rotate (`/clear`) and recover.
        pane_not_authoritative = adapter.remote or not mgr.provides_pane
        blended = self._blend(
            state.status,
            transcript,
            has_transcript=has_transcript,
            provenance=provenance,
            remote=pane_not_authoritative,
            now=now,
        )
        # Push-status override (#18): a sidecar from the managed hook is the
        # authoritative signal — it sees BLOCKED (permission prompt) and the clean
        # waiting/done split that polling can't. It outranks the poll until the
        # transcript outruns it (the record's own staleness call); absent or
        # superseded → polled blend stands.
        if sidecar is not None and sidecar.supersedes_poll(
            now=now, transcript_at=transcript.last_event_at
        ):
            blended = sidecar.state
        blended = self._settle(session_id, blended, now)
        # Live questions (#109): the same sidecar may carry a batch captured at
        # ask-time (before the transcript flushes them). Surfaced independently of
        # the state override above and cross-checked against the transcript so a
        # resolved batch never lingers on the stream.
        questions = self._pending_questions(adapter, cwd, session_id, sidecar, transcript)
        live = self._live_counters(state=blended, transcript=transcript)
        session = AgentSession(
            session_id=session_id,
            transcript_path=paths[0] if paths else None,
            adapter_kind=kind,
            provenance=provenance,
            tmux_window=mgr.config.tmux.agent_window_name,
        )
        return SessionActivity(
            session=session,
            activity=replace(transcript, state=blended, questions=questions),
            live=live,
        )

    @staticmethod
    def _live_counters(
        *, state: AgentActivityState, transcript: AgentActivity
    ) -> LiveCounters | None:
        """The in-flight token block for a session mid-generation (#181 seam).

        No fast side-channel is wired yet — the #177 wire-truth proxy is the
        primary source, with partial-message deltas or OTel metrics as
        fallbacks — so this degrades to ``None`` (hidden, never zeroed) until
        one lands. This call site is where it plugs in: called once per
        session per poll tick, i.e. throttled to the existing ~1-2s
        ``poll_once`` cadence — no separate timer, no new SSE frame type, it
        rides the same ``session_activity`` delta the fingerprint above already
        emits.

        The reconciliation rule for whoever wires a real source: only report
        while ``state is WORKING`` (mid-generation); the instant a turn flushes
        the transcript's own ``tokens_in``/``tokens_out`` become authoritative
        again, so the live block must stop appearing rather than being reset —
        a client that keeps seeing it after settling would show a stale count
        fighting the cumulative total instead of yielding to it.
        """
        del state, transcript  # reserved for the #177 wiring
        return None

    def _pending_questions(
        self,
        adapter: AgentAdapter,
        worktree: Path,
        session_id: str,
        sidecar: HookRecord | None,
        transcript: AgentActivity,
    ) -> tuple[AgentQuestion, ...]:
        """The batch the agent is asking right now, or ``()`` (#109).

        Normalizes the sidecar's captured payload through the shared
        ``from_tool_call`` seam (no question shape re-derived), then confirms it's
        still pending. One ``AskUserQuestion`` call carries up to four questions
        answered atomically, so the whole group is returned (ordered as asked) or
        none of it. The cross-check is cheap by construction: Claude Code flushes
        nothing while a question is on screen, so while the transcript's last event
        predates the ask the batch is definitely still pending and no re-parse is
        needed. Only once the transcript advances past ``asked_at`` do we read the
        turns to see whether the resolving ``tool_result`` (an answer OR an
        Esc-cancel ``is_error``) has landed for this ``tool_use_id`` (the batch
        shares one, resolving together).
        """
        if sidecar is None or sidecar.question is None:
            return ()
        pending = sidecar.question
        questions = AgentQuestion.from_tool_call(
            pending.tool_name, pending.tool_input, pending.tool_use_id
        )
        if not questions:
            return ()
        advanced = (
            transcript.last_event_at is not None and transcript.last_event_at > pending.asked_at
        )
        if advanced and self._question_resolved(adapter, worktree, session_id, pending.tool_use_id):
            return ()
        return questions

    @staticmethod
    def _question_resolved(
        adapter: AgentAdapter, worktree: Path, session_id: str, tool_use_id: str
    ) -> bool:
        """Whether the transcript already carries a resolving result for ``tool_use_id``.

        Claude Code flushes the ``AskUserQuestion`` tool_use and its ``tool_result``
        together only after the human answers or Esc-cancels, so the presence of
        that group in the parsed turns means the question is no longer pending
        either way. Best-effort: a failed read can't confirm resolution, so we
        treat it as still pending (the answer path re-checks before it acts)."""
        try:
            turns = adapter.read_turns(worktree, session_id)
        except Exception as exc:  # best-effort: never break the snapshot
            logger.debug("activity question-resolution read_turns({}) failed: {}", session_id, exc)
            return False
        return any(
            e.question is not None and e.question.group_id == tool_use_id
            for t in turns
            for e in t.entries
        )

    @staticmethod
    def _blend(
        ws_status: WorkspaceStatus,
        transcript: AgentActivity,
        *,
        has_transcript: bool,
        provenance: SessionProvenance,
        remote: bool,
        now: datetime,
    ) -> AgentActivityState:
        """The single status-blend policy site (mirrors ``_reconcile_status``).

        Combines the transcript-derived state with Grove's already-reconciled
        workspace status (which encodes the tmux activity dimension: ACTIVE = a
        pane emitted output within the threshold, IDLE = quiet). Because the
        manager already computed that, the blend needs no extra tmux call.

        Truth table:
          - no transcript materialized (no file AND nothing parseable — remote
            adapters have no file but still surface state) → STARTING for a
            grove_launched session (created lazily on its first turn), else
            UNKNOWN (an fs_discovered file that vanished/raced between
            discover and read).
          - transcript UNKNOWN/ERROR/WAITING/BLOCKED → returned as-is (definitive
            signals; an ended turn stays WAITING, a needs-input prompt stays
            BLOCKED, regardless of tmux noise).
          - transcript WORKING (tool_use / mid-stream tail):
              · remote adapter → WORKING (the backend's status is authoritative;
                the local pane runs a bare shell and says nothing about remote
                work — gating on it read every busy remote agent as IDLE).
              · workspace ACTIVE (tmux fresh) → WORKING.
              · transcript fresh (``last_event_at`` within the sidecar window) →
                WORKING. The adapter's own abstraction outranks the tmux
                heuristic: a thinking/long-tool agent emits no pane output, and
                demoting on the quiet pane alone was the flaky WORKING→IDLE
                flapping. Tmux becomes the tiebreak only once the transcript
                itself has gone stale.
              · otherwise (both signals stale, or session not live) → IDLE — a
                tool_use tail with nothing advancing is alive-but-stalled (or a
                killed agent whose transcript froze mid-tool); precise BLOCKED
                needs a hook (#18).
        """
        # No transcript materialized: only a Grove-launched session is legitimately
        # mid-STARTING (nothing written yet on its first turn). An fs_discovered
        # session whose file we confirmed in discover() but can't read now is a
        # vanished/raced transcript → UNKNOWN, never a false "starting".
        if not has_transcript:
            return (
                AgentActivityState.STARTING
                if provenance == "grove_launched"
                else AgentActivityState.UNKNOWN
            )
        t = transcript.state
        if t in (
            AgentActivityState.UNKNOWN,
            AgentActivityState.ERROR,
            AgentActivityState.WAITING,
            AgentActivityState.BLOCKED,
        ):
            return t
        if remote:
            return t
        if ws_status == WorkspaceStatus.ACTIVE:
            return AgentActivityState.WORKING
        if (
            transcript.last_event_at is not None
            and (now - transcript.last_event_at).total_seconds() <= DEFAULT_SIDECAR_MAX_AGE_SECONDS
        ):
            return AgentActivityState.WORKING
        return AgentActivityState.IDLE

    def _settle(
        self, session_id: str, fresh: AgentActivityState, now: datetime
    ) -> AgentActivityState:
        """Hysteresis: a degraded read never erases a definitive state.

        A live session's read can transiently collapse to STARTING/UNKNOWN — a
        JSONL tail caught mid-write, a remote ``/events`` timeout, a transcript
        glob racing a file rotation — and the memoryless blend would flash the
        card back to "starting" each time. Keep the last definitive state for
        the degraded tick; the next clean read takes over. A session that never
        materialized keeps its honest STARTING/UNKNOWN.

        A settled WORKING expires on the sidecar's window (the same dead-agent
        guard, same reason): with hooks on, ``SessionStart`` settles WORKING
        before any transcript exists, so a workspace whose agent dies at boot —
        or is simply never prompted — would otherwise read WORKING forever once
        the degraded fallthrough starts answering from this cache. Settled
        WAITING/BLOCKED/ERROR/IDLE stay age-less: nothing happened since, so
        they are still true.
        """
        degraded = fresh in (AgentActivityState.STARTING, AgentActivityState.UNKNOWN)
        cached = self._settled.get(session_id)
        if degraded and cached is not None:
            state, settled_at = cached
            expired = (
                state is AgentActivityState.WORKING
                and (now - settled_at).total_seconds() > DEFAULT_SIDECAR_MAX_AGE_SECONDS
            )
            if state is not AgentActivityState.STARTING and not expired:
                return state
        self._settled[session_id] = (fresh, now)
        return fresh

    # ─── manager-bus bridge ────────────────────────────────────────────────

    def _ensure_bridged(self) -> None:
        """Subscribe to every known repo's manager bus exactly once.

        Lifecycle events (create/kill/pause/resume/respawn/update) become
        ``workspace_changed`` deltas so structural changes stream promptly without
        waiting for the next poll. Re-scanning picks up repos created after the
        service started.
        """
        for root in self._registry.known_roots():
            key = root.resolve()
            if key in self._bridged:
                continue
            mgr = self._registry.get(key)
            self._bridge_unsubs.append(mgr.subscribe(self._bridge_callback(str(key))))
            self._bridged.add(key)

    def _bridge_callback(self, repo_root: str) -> Callable[[WorkspaceEvent], None]:
        def _on_event(event: WorkspaceEvent) -> None:
            self._emit(
                DashboardDelta(
                    kind="workspace_changed",
                    seq=self.next_seq(),
                    workspace_id=event.workspace_id,
                    repo_root=repo_root,
                    detail={"event": event.kind, **event.detail},
                )
            )

        return _on_event

    # ─── internal ──────────────────────────────────────────────────────────

    def _git_for(self, repo_root: Path) -> GitRepo:
        key = repo_root.resolve()
        git = self._git_cache.get(key)
        if git is None:
            git = GitRepo(key)
            self._git_cache[key] = git
        return git

    def _emit(self, delta: DashboardDelta) -> None:
        for callback in list(self._subs):
            try:
                callback(delta)
            except Exception as exc:  # subscriber bugs must not break the service
                logger.warning("dashboard subscriber raised on {} delta: {}", delta.kind, exc)

    def next_seq(self) -> int:
        """Next monotonic event id. Public so the daemon's SSE layer stamps its
        snapshot/heartbeat frames from the *same* sequence as the deltas, keeping
        Last-Event-ID replay coherent across both."""
        return next(self._seq)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)
