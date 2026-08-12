"""Cross-worktree agent-session exploration for one project.

One question: *which agent sessions belong to this project, and how do I read
one?* The :class:`SessionExplorer` aggregates what the adapters already know —
``list_sessions`` per scanned directory, ``locate_transcripts`` /
``read_turns`` per session — across the repo root, every git worktree, and
every Grove workspace path (including paused workspaces whose worktree is gone:
transcripts outlive worktrees). It annotates each session with its Grove
workspace and provenance, so the ``grove sessions`` CLI stays a thin renderer.

Read-only by construction: the explorer never launches, mutates, or deletes
anything — it composes the adapters' read-only scans. Adding a new agent tool
(codex, opencode) changes nothing here; ``all_adapters()`` picks up its
adapter automatically.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from grove.core import paths as core_paths
from grove.core import process as process_module
from grove.core.agents import (
    SessionProvenance,
    SessionRef,
    SessionSummary,
    SessionTurn,
    all_adapters,
    get_adapter,
)
from grove.core.agents.claude_code import ClaudeCodeAdapter
from grove.core.agents.hook import DEFAULT_SIDECAR_MAX_AGE_SECONDS, ClaudeHook
from grove.core.contracts.usage import DurationView
from grove.core.errors import GroveError
from grove.core.git import GitRepo, detect_root
from grove.core.manager import WorkspaceManager, build
from grove.core.process import LiveRuntime
from grove.core.session_duration import duration_of
from grove.core.turn_count import TurnCountCache

if TYPE_CHECKING:
    from grove.core.agents.base import AgentAdapter
    from grove.core.registry import RepoRegistry
    from grove.core.workspace import WorkspaceState

# Aware epoch for "no mtime" rows so the newest-first sort never compares
# aware and naive datetimes (that raises, and a sort must never raise here).
_EPOCH = datetime.fromtimestamp(0, tz=UTC)


@dataclass(slots=True, frozen=True)
class SessionListing:
    """One session row with its project context attached.

    ``workspace_*`` fields are ``None`` for a session found in a directory
    Grove doesn't manage (a hand-made worktree, or the repo root with no ROOT
    workspace). ``provenance`` is ``grove_launched`` only when the id matches
    a workspace's minted ``agent_session_id``.

    ``duration`` is the wall clock and the compute total (see
    :func:`~grove.core.session_duration.duration_of`), derived here from the
    message spine because this scope is already parsing the transcript.
    ``None`` means the spine could not be read at all — never *no work*, which
    is a ``DurationView`` whose own fields are null. It is the same number
    :class:`CatalogEntry` reads out of the durable cache, from the same
    function, so the two scopes cannot disagree about one session.
    """

    summary: SessionSummary
    provenance: SessionProvenance
    workspace_id: str | None = None
    workspace_title: str | None = None
    workspace_branch: str | None = None
    duration: DurationView | None = None


class SessionExplorer:
    """Aggregate, filter, and resolve agent sessions across a project's worktrees."""

    def __init__(self, manager: WorkspaceManager) -> None:
        self._manager = manager

    @classmethod
    def from_cwd(cls, cwd: Path) -> SessionExplorer:
        """Build an explorer for the project enclosing ``cwd``.

        Works from inside any worktree: the *main* worktree (first entry of
        ``git worktree list``) is the root the workspace store is keyed by, so
        the explorer always binds its manager there — binding to the linked
        worktree's own root would find zero workspaces.
        """
        root = detect_root(cwd)
        if root is None:
            raise GroveError(f"{cwd} is not inside a git repository")
        main_root = GitRepo(root).worktree_paths()[0]
        return cls(build(main_root))

    @property
    def repo_root(self) -> Path:
        return self._manager.repo_root

    def scan_roots(self) -> list[Path]:
        """Every directory whose sessions belong to this project, de-duplicated.

        Union of the live ``git worktree list`` (main first; covers hand-made
        worktrees Grove never managed), every workspace's persisted ``agent_cwd``
        (worktree/subpath — where a nested project's agent actually runs and
        records its transcript's cwd), its ``worktree_path``
        (covers paused workspaces whose directory is gone — their transcripts
        still live under the encoded-cwd projects folder), and, for a workspace
        with a ``transcript_context`` override, the recorded cwd it names
        instead — a container-launched agent's real cwd, which can never equal
        either host path. ``agent_cwd`` collapses to the worktree root for the
        common empty-subpath case, so the extra entries only matter for nested
        or overridden projects.
        """
        out: list[Path] = []
        seen: set[str] = set()
        states = self._manager.list()
        candidates = [
            *GitRepo(self._manager.repo_root).worktree_paths(),
            *(state.agent_cwd for state in states),
            *(Path(state.worktree_path) for state in states),
            *(
                Path(state.transcript_context.agent_cwd)
                for state in states
                if state.transcript_context is not None
            ),
        ]
        for path in candidates:
            key = str(path)
            if key not in seen:
                seen.add(key)
                out.append(path)
        return out

    def list(
        self,
        *,
        agent: str | None = None,
        workspace: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[SessionListing]:
        """Every session across the project, newest-first, optionally filtered.

        ``agent`` matches the adapter kind exactly; ``workspace`` matches a
        workspace id prefix or a case-insensitive title substring; ``since``
        keeps sessions modified at/after that instant; ``limit`` caps the
        result after sorting.
        """
        states = self._manager.list()
        # A scanned root binds to its workspace by cwd — key on agent_cwd (a
        # nested project's real cwd), worktree_path, and a
        # transcript_context override's recorded cwd so a session found
        # under any resolves to its workspace. First-wins on collision.
        by_cwd: dict[str, WorkspaceState] = {}
        for s in states:
            cwd_keys = [str(s.agent_cwd), s.worktree_path]
            if s.transcript_context is not None:
                cwd_keys.append(s.transcript_context.agent_cwd)
            for cwd_key in cwd_keys:
                by_cwd.setdefault(cwd_key, s)
        minted: dict[str, WorkspaceState] = {
            s.agent_session_id: s for s in states if s.agent_session_id
        }
        # A workspace's effective agent kind, computed once (config lookup for
        # legacy records): the cwd fallback below tags a session only when its
        # kind matches, so it's read per state, not per session.
        eff_kind: dict[str, str] = {s.id: self._manager.effective_kind(s) for s in states}
        override_by_root = self._override_by_root(states, eff_kind)

        listings: list[SessionListing] = []
        seen: set[tuple[str, str]] = set()
        for root in self.scan_roots():
            override = override_by_root.get(str(root))
            scope = (
                self._manager.transcript_config_dir_scope(*override)
                if override is not None
                else contextlib.nullcontext()
            )
            with scope:
                for adapter in all_adapters():
                    for summary in adapter.list_sessions(root):
                        key = (summary.adapter_kind, summary.session_id)
                        if key in seen:
                            continue
                        seen.add(key)
                        # Minted-id equality is authoritative (Grove launched it
                        # here, and the id was minted by this workspace's own
                        # adapter, so the kind matches by construction). The cwd
                        # fallback is NOT: a ROOT workspace's cwd is the shared
                        # repo root, so a foreign-kind session there (a codex
                        # rollout under a claude_code workspace) sits in the same
                        # dir — annotating it with this workspace's id/title
                        # would mis-attribute a session the workspace can never
                        # own (its adapter can't read it; remap rejects a kind
                        # mismatch). So the cwd fallback tags only a same-kind
                        # session; otherwise the browse row stays unmapped,
                        # honest history.
                        state = minted.get(summary.session_id)
                        if state is None:
                            candidate = by_cwd.get(str(root))
                            if (
                                candidate is not None
                                and summary.adapter_kind == eff_kind[candidate.id]
                            ):
                                state = candidate
                        listings.append(
                            SessionListing(
                                summary=summary,
                                provenance=(
                                    "grove_launched"
                                    if summary.session_id in minted
                                    else "fs_discovered"
                                ),
                                workspace_id=state.id if state else None,
                                workspace_title=state.title if state else None,
                                workspace_branch=state.branch if state else None,
                                duration=self._duration(adapter, root, summary.session_id),
                            )
                        )

        if agent is not None:
            listings = [ls for ls in listings if ls.summary.adapter_kind == agent]
        if workspace is not None:
            needle = workspace.lower()
            listings = [
                ls
                for ls in listings
                if (ls.workspace_id or "").startswith(workspace)
                or needle in (ls.workspace_title or "").lower()
            ]
        if since is not None:
            listings = [
                ls
                for ls in listings
                if ls.summary.modified_at is not None and ls.summary.modified_at >= since
            ]
        listings.sort(key=lambda ls: ls.summary.modified_at or _EPOCH, reverse=True)
        return listings[:limit] if limit is not None else listings

    @staticmethod
    def _override_by_root(
        states: Sequence[WorkspaceState], eff_kind: dict[str, str]
    ) -> dict[str, tuple[str, str]]:
        """Scanned root → ``(kind, config_dir)`` for the workspaces that pin one.

        Lets :meth:`list` scope the adapter's config-dir env var while scanning
        that root, so the pinned/bind-mounted host directory is what
        ``list_sessions`` actually searches.

        Keyed off EVERY entry of ``transcript_scan_cwds``, not just the context's
        own recorded cwd: :meth:`scan_roots` also yields each workspace's
        worktree root, so keying on the recorded cwd alone left a NESTED pinned
        workspace's root scan running under the *ambient* config dir — silently
        missing a session recorded at the worktree root under the pinned one
        (the union this exists to preserve).

        Two workspaces sharing a root with divergent pins is last-wins. Seen and
        deliberately left: it needs a shared cwd (ROOT placement) with different
        overrides, and the honest fix is per-scan attribution rather than a
        root→override map — a larger change than this seam warrants.
        """
        out: dict[str, tuple[str, str]] = {}
        for state in states:
            ctx = state.transcript_context
            if ctx is None:
                continue
            for cwd in state.transcript_scan_cwds:
                out[str(cwd)] = (eff_kind[state.id], ctx.config_dir)
        return out

    def for_workspace(self, workspace_id: str) -> tuple[SessionListing, ...]:
        """Every session recorded for one workspace's directory, newest-first.

        The bounded variant of :meth:`list` for per-request consumers (the
        daemon's ``GET /workspaces/{id}/sessions``): scans only the workspace's
        own cwd instead of every worktree, so the transcript-parse cost stays
        one directory regardless of project size. Raises
        :class:`~grove.core.errors.WorkspaceNotFound` for an unknown id.

        Scans the union of ``state.agent_cwd`` (worktree/subpath — where a nested
        project's agent runs) and the worktree root (a session hand-started at the
        repo root records *its* cwd, #F7), deduped for a flat workspace. Both are
        where filesystem adapters exact-match a transcript's recorded cwd.

        A discovered (``fs_discovered``) listing is kept only when
        ``state.adopts_session`` accepts it — its transcript birth postdates this
        workspace's ``created_at``, OR a hook sidecar proves it was live in this
        workspace's own PANE after creation (the same pane-verified evidence rule
        `ActivityService.sessions_for` uses, so a session *resumed* in the pane —
        born before the workspace — still attributes here, while a shared cwd
        can't steal another workspace's live session, #F1). Without the gate, a
        fresh workspace whose cwd already holds older transcripts (especially
        ROOT placement, whose cwd is the shared repo root) would present a stale,
        unrelated session as its own. A ``grove_launched`` listing is never gated
        — Grove minted it for this workspace regardless of birth. :meth:`list`
        and the project-scoped listing stay ungated by design: those are
        browse-everything history views, not workspace attribution.

        Returns a tuple — in this class body a ``list[...]`` annotation would
        resolve to the :meth:`list` method, not the builtin (the documented
        mypy shadowing trap).
        """
        return self._scan_workspace(self._manager.get(workspace_id), adopt_gate=True)

    def candidates_for(self, workspace_id: str) -> tuple[SessionListing, ...]:
        """Every session recorded in one workspace's directories, newest-first,
        UNGATED — the remap-picker seam.

        The ungated sibling of :meth:`for_workspace`: the same bounded one-cwd
        scan (cheap per request), but applying NO ``adopts_session`` birth/pane
        gate. So a session the auto-adoption heuristic rejects — a
        dead-minted-pointer's live successor born before the workspace, or a
        foreign session sharing a ROOT cwd — is still offered. This is exactly
        the set a human picks from to remap: the operator supplies the
        attribution the gate withholds (and `manager.remap_session`, the write
        it feeds, is likewise ungated). Provenance is still ``grove_launched``
        for the minted id, ``fs_discovered`` otherwise. Raises
        :class:`~grove.core.errors.WorkspaceNotFound` for an unknown id.

        A browse-everything counterpart to the ungated project-wide :meth:`list`,
        but scoped to one workspace's ``scan_cwds`` — so a picker pays one
        directory's parse, not the whole project's. Returns a tuple (the
        ``list``-method shadowing trap, as in :meth:`for_workspace`).
        """
        return self._scan_workspace(self._manager.get(workspace_id), adopt_gate=False)

    def _scan_workspace(
        self, state: WorkspaceState, *, adopt_gate: bool
    ) -> tuple[SessionListing, ...]:
        """The shared one-cwd scan behind :meth:`for_workspace` (``adopt_gate``
        True) and :meth:`candidates_for` (False).

        Scans the ``state.transcript_scan_cwds`` union **through the single
        adapter for this workspace's effective kind**, dedupes by ``(kind, id)``,
        tags provenance by minted-id equality, sorts newest-first by mtime. Only
        when ``adopt_gate`` does it drop a discovered listing ``ClaudeHook.adopts``
        rejects (birth ≥ ``created_at`` OR a pane-verified live-here sidecar,
        #F1) — the minted (``grove_launched``) listing is never gated either way.
        One derivation so the gated and ungated reads can't drift.

        The single-adapter restriction is the kind counterpart of the cwd
        scope: a workspace runs exactly one agent kind, so only that adapter's
        sessions can be its own. A ROOT workspace's cwd is the shared repo root,
        where the human also runs *other* tools — a foreign-kind transcript there
        (a codex rollout under a claude_code workspace) can never be this
        workspace's minted session, be adopted by its adapter, or be pinned
        (``remap_session`` rejects a kind mismatch). Scanning every adapter let
        ``candidates_for`` offer exactly those un-pinnable sessions; restricting
        to ``mgr.effective_kind`` keeps the picker in agreement with the pin and
        matches the read path (``ActivityService.sessions_for`` discovers through
        this same one adapter).

        Honors ``state.transcript_context``: ``transcript_scan_cwds``
        substitutes the container-recorded cwd for the union above, and the
        whole scan is wrapped in the matching config-dir env scope so the
        adapter searches the override's host directory instead of the ambient
        one. No override (the default) is byte-for-byte the plain scan.
        """
        kind = self._manager.effective_kind(state)
        adapter = get_adapter(kind)
        # Reference pane from the minted session's sidecar (#F1): the pane this
        # workspace owns, against which a discovered session's live-here evidence
        # is verified. Read once, before the scan loop — and only when gating.
        reference_pane: str | None = None
        if adopt_gate and state.agent_session_id:
            ref = ClaudeHook.read(
                state.agent_session_id, sidecar_dir=core_paths.agent_sidecar_dir()
            )
            reference_pane = ref.tmux_pane if ref is not None else None
        listings: list[SessionListing] = []
        seen: set[tuple[str, str]] = set()
        ctx = state.transcript_context
        with self._manager.transcript_config_dir_scope(
            kind, ctx.config_dir if ctx is not None else None
        ):
            for cwd in state.transcript_scan_cwds:
                for summary in adapter.list_sessions(cwd):
                    key = (summary.adapter_kind, summary.session_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    provenance: SessionProvenance = (
                        "grove_launched"
                        if summary.session_id == state.agent_session_id
                        else "fs_discovered"
                    )
                    if adopt_gate and provenance != "grove_launched":
                        candidate = ClaudeHook.read(
                            summary.session_id, sidecar_dir=core_paths.agent_sidecar_dir()
                        )
                        if not ClaudeHook.adopts(
                            state,
                            summary.created_at,
                            candidate=candidate,
                            reference_pane=reference_pane,
                            cwd=cwd,
                        ):
                            continue
                    listings.append(
                        SessionListing(
                            summary=summary,
                            provenance=provenance,
                            workspace_id=state.id,
                            workspace_title=state.title,
                            workspace_branch=state.branch,
                            duration=self._duration(adapter, cwd, summary.session_id),
                        )
                    )
        listings.sort(key=lambda ls: ls.summary.modified_at or _EPOCH, reverse=True)
        return tuple(listings)

    @staticmethod
    def _duration(adapter: AgentAdapter, cwd: Path, session_id: str) -> DurationView | None:
        """This session's two clocks, from the spine the listing is already parsing.

        Project scope pays for its own measurement rather than reading the
        catalog's durable cache, for the same reason ``turn_count`` does: the
        parse is happening here anyway, and a cache filled only when somebody
        browses the HOST-wide list would leave this column empty for a user who
        never opens it. Both roads run :func:`duration_of` over
        ``adapter.read_messages``, so the number is the same one either way.

        ``read_messages`` covers the main transcript AND every sub-agent file
        (that union is the whole point — the compute total is a fleet's), which
        is a different fold key from the one ``list_sessions`` used for the
        summary, so the first call per transcript VERSION pays a parse and every
        later one is a ``stat`` against the adapter's memo. Best-effort by
        contract: a browse row must degrade to an unmeasured column, never raise
        out of a listing.
        """
        try:
            return duration_of(adapter.read_messages(cwd, session_id))
        except Exception as exc:
            logger.debug(
                "session duration unavailable for {} {}: {}",
                adapter.kind,
                session_id,
                type(exc).__name__,
            )
            return None

    def resolve(self, ref: str) -> SessionListing:
        """The unique session whose id matches ``ref`` exactly or by prefix.

        Raises :class:`GroveError` when nothing matches or the prefix is
        ambiguous (the message lists the candidates, so the user can extend
        the prefix without re-running ``list``).
        """
        listings = self.list()
        exact = [ls for ls in listings if ls.summary.session_id == ref]
        if exact:
            return exact[0]
        matches = [ls for ls in listings if ls.summary.session_id.startswith(ref)]
        if not matches:
            raise GroveError(f"no session matches {ref!r} in this project")
        if len(matches) > 1:
            ids = ", ".join(ls.summary.session_id for ls in matches[:8])
            raise GroveError(f"session ref {ref!r} is ambiguous: {ids}")
        return matches[0]

    def transcripts(self, listing: SessionListing) -> tuple[Path, ...]:
        """Every transcript file for the session — main thread first, then
        sub-agent files — via the owning adapter's locator. Empty for a
        remote-backed session (no local files).

        Scoped to the owning workspace's ``transcript_context.config_dir``
        override, if any — ``_session_cwd`` already resolves to the
        session's own recorded cwd (a container path, when relevant), so only
        the adapter's config-dir env needs redirecting to find that host
        directory at all.
        """
        summary = listing.summary
        adapter = get_adapter(summary.adapter_kind)
        with self._manager.transcript_config_dir_scope(
            summary.adapter_kind, self._transcript_config_dir(listing)
        ):
            return tuple(adapter.locate_transcripts(self._session_cwd(listing), summary.session_id))

    def _transcript_config_dir(self, listing: SessionListing) -> str | None:
        """The config-dir override for reading ``listing``'s session.

        ``None`` (today's behavior) when the listing isn't attributed to a
        workspace, that workspace has since vanished (a kill racing a read —
        best-effort, degrades to no override rather than raising), or it has
        no override set.
        """
        if listing.workspace_id is None:
            return None
        try:
            state = self._manager.get(listing.workspace_id)
        except GroveError:
            return None
        ctx = state.transcript_context
        return ctx.config_dir if ctx is not None else None

    def turns_for(
        self, listing: SessionListing, *, last: int | None = None
    ) -> tuple[SessionTurn, ...]:
        """The normalized conversation for an already-resolved listing.

        Split from :meth:`turns` so a caller holding a listing (the daemon's
        turns endpoint, fed by :meth:`for_workspace`) skips the full-project
        :meth:`resolve` scan.

        Scoped to the owning workspace's config-dir override, if any — same
        reasoning as :meth:`transcripts`.
        """
        adapter = get_adapter(listing.summary.adapter_kind)
        with self._manager.transcript_config_dir_scope(
            listing.summary.adapter_kind, self._transcript_config_dir(listing)
        ):
            return adapter.read_turns(
                self._session_cwd(listing), listing.summary.session_id, last=last
            )

    def subagent_turns(
        self, workspace_id: str, thread_id: str, *, last: int | None = None
    ) -> tuple[SessionListing, tuple[SessionTurn, ...]] | None:
        """The resolution fallback for a fleet-child ``thread_id`` — one that
        `for_workspace` never lists.

        A fleet row's ``session_id`` IS the Claude sub-agent thread id
        (``agentId``), and ``discover_paths`` deliberately skips
        ``subagents/`` — so it never appears in any workspace's own session
        listing, and a direct id lookup always misses. Kind-scoped to
        ``claude_code`` exactly like ``ActivityService._fleet_entries`` (a
        direct :class:`ClaudeCodeAdapter` instantiation, never a widened
        ``AgentAdapter`` Protocol for one kind's capability — the in-session
        sidechain fleet is a Claude Code transcript concept). Tries
        ``thread_id`` against each of the workspace's own top-level sessions
        (its own :meth:`for_workspace` listing) via the adapter's
        ``subagent_turns``/``fleet_activity`` projections — both already read
        off the same memoized spine, so this pays no second parser — and
        returns the first match's turns plus a ``SessionListing`` synthesized
        from that SAME per-thread identity (title/current_task degrade
        exactly as ``fleet_activity`` already does: the ``.meta.json``
        sidecar, else the truncated first task prompt). ``None`` when
        ``thread_id`` belongs to none of them, or the workspace isn't
        ``claude_code``.
        """
        state = self._manager.get(workspace_id)
        if self._manager.effective_kind(state) != "claude_code":
            return None
        adapter = ClaudeCodeAdapter()
        for listing in self.for_workspace(workspace_id):
            cwd = self._session_cwd(listing)
            top_id = listing.summary.session_id
            # Both projections are ambient-env reads, and the scope `for_workspace`
            # opens is long gone by the time this body runs — it closed when that
            # call returned. Scoping the loop HEADER would have looked
            # right and fixed nothing: the listing resolved, then the reads below
            # found nothing and this returned None, i.e. a 404 on the fleet
            # drill-in for any pinned workspace. `fleet_activity` returns a list,
            # so the rows are fully materialized before the scope closes.
            with self._manager.transcript_scope(state):
                turns = adapter.subagent_turns(cwd, top_id, thread_id, last=last)
                fleet = adapter.fleet_activity(cwd, top_id) if turns else []
            if not turns:
                continue
            for session, activity in fleet:
                if session.session_id != thread_id:
                    continue
                fleet_listing = SessionListing(
                    summary=SessionSummary(
                        session_id=thread_id,
                        adapter_kind=session.adapter_kind,
                        transcript_path=session.transcript_path,
                        cwd=str(cwd),
                        created_at=activity.started_at,
                        modified_at=activity.last_event_at,
                        size_bytes=self._stat_size(session.transcript_path),
                        git_branch=listing.summary.git_branch,
                        title=activity.title,
                        first_prompt=activity.current_task,
                        activity=activity,
                    ),
                    provenance="fs_discovered",
                    workspace_id=state.id,
                    workspace_title=state.title,
                    workspace_branch=state.branch,
                )
                return (fleet_listing, turns)
        return None

    @staticmethod
    def _stat_size(path: Path | None) -> int:
        """Best-effort file size for a synthesized fleet-child summary — never
        raises (a vanished/unreadable file just reads as ``0``, the same
        degrade-honestly posture as the rest of this seam)."""
        if path is None:
            return 0
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def _session_cwd(self, listing: SessionListing) -> Path:
        """The cwd key the owning adapter resolves this session under.

        The recorded cwd first; a filesystem session that never recorded one
        falls back to its transcript's own folder; a remote session has
        neither, so the project root — the directory this explorer is bound
        to — is the only cwd left to ask under. One derivation for both
        :meth:`transcripts` and :meth:`turns_for` so they can't drift.
        """
        summary = listing.summary
        if summary.cwd:
            return Path(summary.cwd)
        if summary.transcript_path is not None:
            return summary.transcript_path.parent
        return self.repo_root

    def turns(self, ref: str, *, last: int | None = None) -> tuple[SessionTurn, ...]:
        """The normalized conversation for the session matching ``ref``."""
        return self.turns_for(self.resolve(ref), last=last)


@dataclass(slots=True, frozen=True)
class ProjectContext:
    """The repo identity a scanned session cwd resolves to (the host-wide
    Session Catalog).

    Resolved by a WALK-UP ``.git`` stat, never a ``git rev-parse``
    subprocess: measured ~12x cheaper over 92 distinct on-host cwds (0.036s
    vs 0.429s) — the same hot-path discipline
    ``RepoRegistry._declared_projects`` already applies to its common case.
    ``.git`` is a FILE inside a linked worktree (a ``gitdir:`` pointer), so
    the walk tests ``exists()`` — an ``is_dir()`` test would silently miss
    every worktree, which is most of what this catalog exists to surface.

    Deliberately carries NO branch: the correct branch for a catalog row is
    the one the SESSION recorded (``SessionRef.git_branch``), not whatever
    the worktree happens to be checked out to right now — a different,
    more expensive question. See :class:`CatalogEntry`.
    """

    repo_root: Path
    repo_name: str
    is_worktree: bool
    is_grove_managed: bool


@dataclass(slots=True, frozen=True)
class CatalogEntry:
    """One host-wide session row — :class:`SessionListing`'s sibling at wider
    scope: the same session identity + Grove provenance/workspace
    annotation, plus the resolved project context.

    ``project`` is ``None`` exactly when the row can't be placed on a
    project — the session's head read never recovered a cwd (~2 % of Claude
    transcripts on the reference host) or the cwd resolves to no enclosing
    git repo (10 sessions on the reference host). Neither case drops the
    row; both render as the bare directory.

    ``live`` is a HONEST cwd-level signal, never a fabricated 1:1
    pid-to-session binding — see :meth:`SessionCatalog.fold_liveness`, the
    pure function that sets it.

    ``turn_count`` and ``duration`` are ``None`` until the durable fact cache
    has parsed this session's transcript at its CURRENT fingerprint — the scan
    itself parses nothing. ``None`` therefore means *not measured yet* (or,
    permanently, *no cwd to read it under*), never *zero*: a session that
    genuinely had no turn reports ``0``, and one that did no measurable work
    reports a ``DurationView`` whose own fields are null. Both come from the
    same parse and are stored together, so a row can never carry one without the
    other. See :class:`~grove.core.turn_count.TurnCountCache`.
    """

    ref: SessionRef
    provenance: SessionProvenance
    project: ProjectContext | None
    workspace_id: str | None = None
    workspace_title: str | None = None
    live: bool = False
    turn_count: int | None = None
    duration: DurationView | None = None


class SessionCatalog:
    """Host-wide session enumeration — :class:`SessionExplorer`'s sibling at
    wider scope.

    ``SessionExplorer`` answers *"which agent sessions belong to THIS
    project, and how do I read one?"*; this answers the sibling question at
    host scope: *"which agent sessions exist on this HOST, and where did
    each come from?"* Same question shape, wider scope — same module, a
    sibling class, not a new subpackage.

    Repos are discovered FROM the sessions' own recorded cwds — never a
    host-wide filesystem crawl for ``.git`` directories. ``discover_all()``
    already walks each adapter's whole store; this only resolves each
    DISTINCT cwd upward to its enclosing repo, memoized once per scan (on
    the reference host, 373 transcripts sit behind 90 distinct cwds — the
    resolution cost tracks the cwd count, not the session count).

    Grove-workspace annotation reuses ``RepoRegistry.known_roots()`` (never
    a filesystem crawl either — the existing store-roots-union-declared-roots
    union) and the identical minted-id-first, cwd-second, kind-gated
    provenance rule ``SessionExplorer.list`` already established, just
    unioned across every known repo instead of one.
    """

    def __init__(
        self, registry: RepoRegistry, *, turn_counts: TurnCountCache | None = None
    ) -> None:
        self._registry = registry
        self._turn_counts = turn_counts or TurnCountCache()

    def scan(self, *, limit: int | None = None) -> tuple[CatalogEntry, ...]:
        """Every discoverable session, newest-first by transcript mtime.

        Metadata only — never a full parse: ``discover_all()`` is built
        entirely from each adapter's bounded head reads, plus a LOOKUP in the
        durable turn-count cache (one file read and one ``stat`` per remembered
        row, still no parse). ``limit`` caps the result AFTER sorting, for a
        fast first paint on a large host.
        """
        minted, by_cwd, eff_kind = self._workspace_maps()
        known_resolved = set(self._registry.known_roots())  # already resolved
        project_cache: dict[str, ProjectContext | None] = {}
        entries: list[CatalogEntry] = []
        seen: set[tuple[str, str]] = set()
        for adapter in all_adapters():
            for ref in adapter.discover_all():
                key = (ref.adapter_kind, ref.session_id)
                if key in seen:
                    continue
                seen.add(key)
                # Minted-id equality first, cwd equality second — the exact
                # rule `SessionExplorer.list` uses, reused rather than
                # re-derived (kind-gated so a foreign-kind session sharing a
                # ROOT workspace's cwd doesn't borrow its identity).
                state = minted.get(ref.session_id)
                if state is None and ref.cwd is not None:
                    candidate = by_cwd.get(ref.cwd)
                    if candidate is not None and ref.adapter_kind == eff_kind[candidate.id]:
                        state = candidate
                project: ProjectContext | None = None
                if ref.cwd is not None:
                    if ref.cwd not in project_cache:
                        project_cache[ref.cwd] = self._resolve_project(
                            Path(ref.cwd), known_resolved
                        )
                    project = project_cache[ref.cwd]
                entries.append(
                    CatalogEntry(
                        ref=ref,
                        provenance=(
                            "grove_launched" if ref.session_id in minted else "fs_discovered"
                        ),
                        project=project,
                        workspace_id=state.id if state else None,
                        workspace_title=state.title if state else None,
                    )
                )
        # ONE bounded /proc scan per catalog request (never per row, never on
        # the 2 s poll) — folded in by the pure `fold_liveness` below so the
        # I/O and the liveness JUDGMENT stay separate and independently testable.
        entries = list(
            self.fold_liveness(entries, process_module.list_agent_runtimes(), now=time.time())
        )
        # One cache read for the whole scan, carrying every parse-derived fact
        # at once. A row the cache cannot answer at this transcript's CURRENT
        # fingerprint stays None on all of them — the honest "not measured",
        # filled later by `count_turns` off the request path.
        facts = self._turn_counts.facts_for([e.ref for e in entries])
        measured: list[CatalogEntry] = []
        for entry in entries:
            known = facts.get((entry.ref.adapter_kind, entry.ref.session_id))
            if known is None:
                measured.append(entry)
                continue
            measured.append(replace(entry, turn_count=known.turns, duration=known.duration))
        entries = measured
        entries.sort(key=lambda e: e.ref.mtime, reverse=True)
        return tuple(entries[:limit]) if limit is not None else tuple(entries)

    def count_turns(
        self, entries: Sequence[CatalogEntry], *, stop: Callable[[], bool] | None = None
    ) -> int:
        """Parse and durably cache every parse-derived fact ``scan`` left null.

        Named for the count it shipped with, because the daemon schedules it by
        that name; it fills the turn count AND the durations, from one parse per
        changed session, into one file. A second background pass per column
        would re-walk the same transcripts on its own schedule and could
        disagree with this one about what "current" means.

        BLOCKING and unbounded — a cold host is tens of seconds of transcript
        parsing — so a caller runs it on a background worker and never inside a
        request; ``stop`` lets a shutdown end the pass between sessions. Takes
        the whole scan (not just the unmeasured rows) because the set is also
        what prunes vanished sessions out of the file. Returns how many sessions
        it measured, so a caller can log a cold pass without inspecting the file.
        """
        return self._turn_counts.fill([e.ref for e in entries], stop=stop)

    @staticmethod
    def fold_liveness(
        entries: Sequence[CatalogEntry], runtimes: Sequence[LiveRuntime], *, now: float
    ) -> tuple[CatalogEntry, ...]:
        """Fold ``LiveRuntime`` signals into catalog rows — PURE, zero I/O
        (unit-testable with synthetic runtimes and rows, no ``/proc``, no
        subprocess).

        A row is marked ``live`` only when BOTH hold: a runtime of the SAME
        adapter kind exists at the row's cwd, AND the row's transcript is
        fresh (within ``DEFAULT_SIDECAR_MAX_AGE_SECONDS`` of ``now`` — the
        identical staleness window ``ActivityService._blend`` already uses to
        decide whether a transcript is recent enough to trust a live state).
        Multiple runtimes sharing one cwd (routine — 20+ concurrent
        ``claude`` processes were observed on one host) fold to the SAME
        honest cwd-level ``True``; this NEVER picks one and calls it "the"
        live session — that would be exactly the fabricated 1:1 binding the
        evidence forbids.
        """
        live_cwds = {(rt.kind, str(rt.cwd)) for rt in runtimes}
        folded: list[CatalogEntry] = []
        for entry in entries:
            ref = entry.ref
            is_live = (
                ref.cwd is not None
                and (ref.adapter_kind, ref.cwd) in live_cwds
                and (now - ref.mtime) <= DEFAULT_SIDECAR_MAX_AGE_SECONDS
            )
            folded.append(replace(entry, live=True) if is_live else entry)
        return tuple(folded)

    def _workspace_maps(
        self,
    ) -> tuple[dict[str, WorkspaceState], dict[str, WorkspaceState], dict[str, str]]:
        """Minted-id / cwd / effective-kind maps, unioned across every KNOWN
        repo — the same three maps :meth:`SessionExplorer.list` builds for one
        repo, just widened. Reused verbatim, not re-derived, so the
        provenance rule can't drift between the project-scoped and host-wide
        views. Never a filesystem crawl: ``known_roots()`` is the existing
        store-roots-union-declared-roots union.
        """
        minted: dict[str, WorkspaceState] = {}
        by_cwd: dict[str, WorkspaceState] = {}
        eff_kind: dict[str, str] = {}
        for root in self._registry.known_roots():
            manager = self._registry.get(root)
            for state in manager.list():
                eff_kind[state.id] = manager.effective_kind(state)
                if state.agent_session_id:
                    minted[state.agent_session_id] = state
                cwd_keys = [str(state.agent_cwd), state.worktree_path]
                if state.transcript_context is not None:
                    cwd_keys.append(state.transcript_context.agent_cwd)
                for cwd_key in cwd_keys:
                    by_cwd.setdefault(cwd_key, state)
        return minted, by_cwd, eff_kind

    @staticmethod
    def _resolve_repo_root(cwd: Path) -> Path | None:
        """Walk UP from ``cwd`` to the first ``.git`` — a cheap stat, never a
        ``git rev-parse`` subprocess (see :class:`ProjectContext`). ``.git``
        is a FILE inside a linked worktree, so ``exists()`` catches both a
        real repo and a worktree; ``is_dir()`` would miss the latter.
        """
        current = cwd
        while True:
            if (current / ".git").exists():
                return current
            parent = current.parent
            if parent == current:
                return None
            current = parent

    def _resolve_project(self, cwd: Path, known_resolved: set[Path]) -> ProjectContext | None:
        """``cwd`` → its enclosing repo, or ``None`` when no repo encloses it
        (10 sessions on the reference host) — honest, never dropped, never a
        fabricated project.

        The nearest ``.git`` is a linked worktree's own pointer FILE, not the
        main repo — so a worktree cwd's walk-up stops one level short of the
        canonical root ``known_roots()`` deals in. Resolving that pointer
        (one more small file read, still no subprocess) is what lets every
        worktree of one repo group under the SAME project instead of each
        becoming its own, and what makes ``is_grove_managed`` compare against
        the right root at all.
        """
        nearest = self._resolve_repo_root(cwd)
        if nearest is None:
            return None
        git_marker = nearest / ".git"
        is_worktree = git_marker.is_file()
        root = nearest
        if is_worktree:
            main_root = self._read_worktree_main_root(git_marker)
            if main_root is not None:
                root = main_root
        return ProjectContext(
            repo_root=root,
            repo_name=root.name,
            is_worktree=is_worktree,
            is_grove_managed=root.resolve() in known_resolved,
        )

    @staticmethod
    def _read_worktree_main_root(git_file: Path) -> Path | None:
        """A linked worktree's ``.git`` is a pointer file
        (``gitdir: <main>/.git/worktrees/<name>``, verified against a real
        ``git worktree add`` layout) — one small text read (never a
        subprocess) recovers the TRUE main repo root. Best-effort: a
        malformed or unreadable pointer degrades to ``None`` (the caller
        keeps the worktree's own directory as the root).
        """
        try:
            line = git_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not line.startswith("gitdir:"):
            return None
        target = Path(line.removeprefix("gitdir:").strip())
        if not target.is_absolute():
            target = (git_file.parent / target).resolve()
        parts = target.parts
        if ".git" not in parts:
            return None
        idx = len(parts) - 1 - parts[::-1].index(".git")
        return Path(*parts[:idx])


__all__ = [
    "CatalogEntry",
    "ProjectContext",
    "SessionCatalog",
    "SessionExplorer",
    "SessionListing",
]
