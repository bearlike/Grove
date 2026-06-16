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
    AgentSession,
    SessionProvenance,
    get_adapter,
)
from grove.core.agents.hook import DEFAULT_SIDECAR_MAX_AGE_SECONDS, ClaudeHook
from grove.core.git import GitRepo
from grove.core.manager import WorkspaceEvent, WorkspaceManager
from grove.core.registry import RepoRegistry
from grove.core.workspace import CommitSummary, WorkspaceState, WorkspaceStatus

DeltaKind = Literal["workspace_changed", "session_activity"]


# ─── engine IR (in-process; the contracts.activity Views mirror these) ──────


@dataclass(slots=True, frozen=True)
class SessionActivity:
    """One agent session paired with its computed activity."""

    session: AgentSession
    activity: AgentActivity


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
        return any(s.activity.needs_attention for s in self.sessions)

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

        Two paths, by whether Grove minted a deterministic id at create:

        - **Minted id present** (the #13 happy path): that ``grove_launched``
          session is the primary. When the #18 enhancement is on
          (``cfg.hooks.enabled``), concurrent sessions the user started by hand in
          this worktree are discovered and appended (``provenance="fs_discovered"``).
          Exception: a minted id that never materialized (no transcript, blend
          still STARTING/UNKNOWN) yields the primary slot to the newest
          discovered session — the in-process-rotation recovery below.
        - **No minted id** — a workspace whose agent wasn't ``kind="claude_code"``
          at create (so no ``--session-id`` was injected), one created before
          minting existed, or a purely hand-started run. The deterministic lookup
          can't help, so recover the *live* session by discovery and adopt the
          single most-recent transcript as the primary. Read-only and
          adapter-gated (a generic/shell agent discovers nothing), so it needs no
          hooks opt-in — without it the whole agent axis is blank even though a
          real transcript exists on disk for the worktree.
        """
        # Prefer the kind persisted at create — it works even when the agent is
        # scoped to a repo's project config the daemon never loads (a "Work"
        # profile defined only in private-repos). Fall back to a config lookup
        # for legacy records written before agent_kind existed.
        kind = state.agent_kind
        if kind is None:
            agent = mgr.config.find_agent(state.agent_name)
            kind = agent.kind if agent is not None else "generic"
        adapter = get_adapter(kind)
        worktree = Path(state.worktree_path)
        now = _utcnow()

        if state.agent_session_id:
            minted = self._session_activity(
                mgr, state, kind, state.agent_session_id, "grove_launched", now
            )
            extras: list[SessionActivity] = []
            if mgr.config.hooks.enabled:
                extras = [
                    self._session_activity(mgr, state, kind, discovered, "fs_discovered", now)
                    for discovered in adapter.discover_sessions(
                        worktree, exclude_id=state.agent_session_id
                    )
                ]
            # A minted id that never materialized is a dead pointer, not a young
            # session: an in-process rotation (`/clear` mints a NEW session id
            # inside the same claude) or a hand-restarted agent leaves the
            # workspace pinned on STARTING forever while the live session sits
            # discoverable in the same cwd. Recover it (ungated by hooks — same
            # read-only-discovery rule as the no-minted-id path) and surface it
            # FIRST so it becomes the primary; the minted entry stays behind it
            # and takes back over if it ever materializes. The sidecar-settled
            # case (hook says WORKING before any transcript) is deliberately not
            # demoted — only a STARTING/UNKNOWN blend means "nothing alive here".
            unmaterialized = (
                not adapter.remote
                and minted.session.transcript_path is None
                and minted.activity.state
                in (AgentActivityState.STARTING, AgentActivityState.UNKNOWN)
            )
            if unmaterialized and not extras:
                recent = adapter.discover_sessions(worktree, exclude_id=state.agent_session_id)[:1]
                extras = [
                    self._session_activity(mgr, state, kind, sid, "fs_discovered", now)
                    for sid in recent
                ]
            if unmaterialized and extras:
                return [*extras, minted]
            return [minted, *extras]

        # `discover_sessions` returns newest-first; surface exactly ONE — a worktree
        # accumulates a long transcript history, so the latest is the running session
        # and the rest are noise on a glance tile.
        recent = adapter.discover_sessions(worktree, exclude_id=None)[:1]
        return [
            self._session_activity(mgr, state, kind, sid, "fs_discovered", now) for sid in recent
        ]

    def _session_activity(
        self,
        mgr: WorkspaceManager,
        state: WorkspaceState,
        kind: str,
        session_id: str,
        provenance: SessionProvenance,
        now: datetime,
    ) -> SessionActivity:
        adapter = get_adapter(kind)
        worktree = Path(state.worktree_path)
        # locate stays alongside the (cwd, session_id)-keyed parse: the paths
        # feed the displayed transcript_path and the STARTING detection below.
        paths = adapter.locate_transcripts(worktree, session_id)
        transcript = adapter.parse_activity(worktree, session_id)
        # Remote adapters surface state with no local file, so "materialized"
        # can't mean "a file exists" — UNKNOWN-and-fileless is the only true
        # STARTING window.
        has_transcript = bool(paths) or transcript.state is not AgentActivityState.UNKNOWN
        blended = self._blend(
            state.status,
            transcript,
            has_transcript=has_transcript,
            provenance=provenance,
            remote=adapter.remote,
            now=now,
        )
        # Push-status override (#18): a sidecar from the managed hook is the
        # authoritative signal — it sees BLOCKED (permission prompt) and the clean
        # waiting/done split that polling can't. It outranks the poll until the
        # transcript outruns it (the record's own staleness call); absent or
        # superseded → polled blend stands.
        sidecar = ClaudeHook.read(session_id, sidecar_dir=core_paths.agent_sidecar_dir())
        if sidecar is not None and sidecar.supersedes_poll(
            now=now, transcript_at=transcript.last_event_at
        ):
            blended = sidecar.state
        blended = self._settle(session_id, blended, now)
        session = AgentSession(
            session_id=session_id,
            transcript_path=paths[0] if paths else None,
            adapter_kind=kind,
            provenance=provenance,
            tmux_window=mgr.config.tmux.agent_window_name,
        )
        return SessionActivity(session=session, activity=replace(transcript, state=blended))

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
