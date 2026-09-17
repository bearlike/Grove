"""Route native filesystem edges to persisted workspace identities."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path

from loguru import logger

from grove.core import paths
from grove.core.activity import RefreshDomain
from grove.core.activity_runtime import ActivityRuntime, WorkspaceInvalidated
from grove.core.admission import AdmissionLimits, BoundedInbox, InboxClosed
from grove.core.agents import get_adapter
from grove.core.agents.hook import ClaudeHook
from grove.core.agents.transcript_scope import config_dir_scope
from grove.core.file_events import FileEventBatch, FileEventRecovery, FileEventSource
from grove.core.git import GitRepo
from grove.core.phase import PhaseFile
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import TranscriptContext, WorkspaceState

WorkspaceKey = tuple[str, str]


class ActivitySources:
    """Maintain bounded native watches routed through persisted workspace identity.

    The state file is the source of truth for routing. Its ordinary metadata
    writes refresh just their changed workspace; only a change to a watch-set
    input restarts this one source group. Transcript discovery is bootstrap-only:
    source events never scan a profile, cwd, or whole fleet to find an owner.
    """

    def __init__(self, runtime: ActivityRuntime, store: JsonWorkspaceStore) -> None:
        self._runtime = runtime
        self._store = store
        self._states: dict[str, WorkspaceState] = {}
        self._sources: list[FileEventSource] = []
        self._control_files: dict[Path, tuple[WorkspaceKey, RefreshDomain]] = {}
        self._worktree_dirs: dict[Path, set[WorkspaceKey]] = {}
        self._transcript_files: dict[Path, set[WorkspaceKey]] = {}
        self._transcript_sessions: dict[Path, set[WorkspaceKey]] = {}
        self._git_paths: dict[Path, set[WorkspaceKey]] = {}
        self._state_task: asyncio.Task[None] | None = None
        self._state_dirty = False
        self._sources_dirty = False
        self._started = False
        self._closing = False
        self._sources_lock = asyncio.Lock()
        self._spool = BoundedInbox[Path](AdmissionLimits())
        self._spool_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Build and start the source group, undoing partial startup on failure."""
        if self._closing:
            raise RuntimeError("activity sources are closed")
        if self._started:
            return
        try:
            self._store.invalidate()
            states = await asyncio.to_thread(self._store.load_all)
            self._states = {state.id: state for state in states}
            await self._restart_sources(self._states.values())
        except BaseException:
            await self._close_sources()
            raise
        self._started = True
        self._spool.bind()
        self._spool_task = asyncio.create_task(self._drain_spool(), name="grove-hook-spool")

    async def _drain_spool(self) -> None:
        while True:
            try:
                delivery = await self._spool.take()
            except InboxClosed:
                return
            try:
                await asyncio.to_thread(
                    ClaudeHook.drain,
                    sidecar_dir=paths.agent_sidecar_dir(),
                    entries=[delivery.value],
                )
            except Exception as exc:
                logger.warning("hook spool ingestion failed: {}", type(exc).__name__)
            finally:
                self._spool.complete(delivery)

    def _control_events(self, batch: FileEventBatch) -> None:
        sidecars = paths.agent_sidecar_dir().resolve()
        state_path = self._store.path.resolve()
        for event in batch.events:
            if event.path == state_path:
                self._state_changed()
                continue
            if event.path == sidecars:
                # The directory did not exist at bootstrap, so its parent was
                # watched solely to observe this creation edge. Switch to the
                # actual directory before accepting sidecar writes.
                self._source_changed()
                continue
            control = self._control_files.get(event.path)
            if control is not None:
                key, domains = control
                self._invalidate(key, domains)
                continue
            if event.path.parent == sidecars:
                self._runtime.hook(event.path.stem)
            elif event.path.parent == paths.agent_hook_spool_dir(
                sidecars
            ) and event.path.name.endswith(ClaudeHook.SPOOL_SUFFIX):
                self._spool.offer(
                    event.path, size_bytes=len(str(event.path).encode()) + 64, key=str(event.path)
                )
            elif event.path.is_relative_to(sidecars / "subagents"):
                relative = event.path.relative_to(sidecars / "subagents")
                if len(relative.parts) >= 2:
                    self._runtime.hook(relative.parts[0])

    def _transcript_events(self, batch: FileEventBatch) -> None:
        keys: set[WorkspaceKey] = set()
        for event in batch.events:
            keys.update(self._transcript_files.get(event.path, ()))
            # A newly created subtree can be populated before the native watcher
            # attaches below it. Its directory-creation edge must also refresh
            # the owner, not only individual JSONL writes it may not have seen.
            keys.update(self._transcript_sessions.get(event.path, ()))
            for parent in event.path.parents:
                keys.update(self._transcript_sessions.get(parent, ()))
        for key in keys:
            self._invalidate(key, RefreshDomain.TRANSCRIPT)

    def _git_events(self, batch: FileEventBatch) -> None:
        keys: set[WorkspaceKey] = set()
        for event in batch.events:
            keys.update(self._git_paths.get(event.path, ()))
            for parent in event.path.parents:
                keys.update(self._git_paths.get(parent, ()))
        for key in keys:
            self._invalidate(key, RefreshDomain.WORKTREE)

    def _worktree_events(self, batch: FileEventBatch) -> None:
        keys: set[WorkspaceKey] = set()
        for event in batch.events:
            # Worktree roots and Git-listed parent directories are non-recursive
            # roots. A path may be admitted by only one such directory, but using
            # a set keeps shared ROOT-placement worktrees correct.
            keys.update(self._worktree_dirs.get(event.path.parent, ()))
            if event.path.is_dir() and event.path not in self._worktree_dirs:
                self._source_changed()
        for key in keys:
            self._invalidate(key, RefreshDomain.WORKTREE)

    def _invalidate(self, key: WorkspaceKey, domains: RefreshDomain) -> None:
        self._runtime.invalidate(WorkspaceInvalidated(*key, reason="filesystem", domains=domains))

    def _state_changed(self) -> None:
        self._state_dirty = True
        self._ensure_refresh_task()

    def _source_changed(self) -> None:
        self._sources_dirty = True
        self._ensure_refresh_task()

    def _ensure_refresh_task(self) -> None:
        if self._closing or not self._started:
            return
        if self._state_task is None or self._state_task.done():
            self._state_task = asyncio.create_task(
                self._refresh(), name="grove-activity-source-refresh"
            )

    async def _refresh(self) -> None:
        """Coalesce state writes and source-root changes without leaked failures."""
        try:
            while not self._closing and (self._state_dirty or self._sources_dirty):
                if self._state_dirty:
                    await self._refresh_states()
                if self._sources_dirty:
                    self._sources_dirty = False
                    try:
                        await self._restart_sources(self._states.values())
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        # The old group was already closed; make the lost edge
                        # explicit so the runtime recovers instead of silently
                        # continuing with a partial watch set.
                        logger.warning("activity source restart failed: {}", type(exc).__name__)
                        self._runtime.request_recovery()
        except asyncio.CancelledError:
            raise
        except Exception:
            # A task exception is otherwise reported only at GC time, after the
            # daemon has silently lost its event path. This task owns no caller.
            logger.exception("activity source refresh failed")
            self._runtime.request_recovery()
        finally:
            # A completed task remains truthy and would make a later event create
            # a new task correctly, but clearing it also gives shutdown one fewer
            # finished task to retain and makes completion an observable state.
            if asyncio.current_task() is self._state_task:
                self._state_task = None

    async def _refresh_states(self) -> None:
        """Diff one freshly read state generation and admit only affected keys."""
        self._state_dirty = False
        try:
            self._store.invalidate()
            states = await asyncio.to_thread(self._store.load_all)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("activity source state reload failed: {}", type(exc).__name__)
            return
        current = {state.id: state for state in states}
        previous = self._states
        changed = {
            workspace_id
            for workspace_id, state in current.items()
            if previous.get(workspace_id) != state
        }
        deleted = set(previous) - set(current)
        self._states = current
        if self._watch_signature(previous.values()) != self._watch_signature(current.values()):
            self._sources_dirty = True
        for workspace_id in changed:
            state = current[workspace_id]
            self._runtime.invalidate(
                WorkspaceInvalidated(
                    state.repo_root, workspace_id, "lifecycle", domains=RefreshDomain.FULL
                )
            )
        for workspace_id in deleted:
            state = previous[workspace_id]
            self._runtime.invalidate(
                WorkspaceInvalidated(
                    state.repo_root,
                    workspace_id,
                    "lifecycle",
                    deleted=True,
                    domains=RefreshDomain.FULL,
                )
            )

    async def _restart_sources(self, states: Iterable[WorkspaceState]) -> None:
        """Replace the one source group after its explicit watch set changes.

        A WATCHER THAT CANNOT ARM IS A DEGRADED OBSERVER, NEVER A FATAL ERROR.
        Its roots are paths other processes own — `pause` removes a worktree,
        a repo is unmounted, an agent deletes a directory mid-scan — so a
        failure to become ready is an ordinary condition, and letting it raise
        took the whole daemon down at startup for one unreadable root. The
        remaining sources still deliver, and the reconcile below re-reads
        state for everything regardless of which watcher armed.
        """
        async with self._sources_lock:
            await self._close_sources()
            state_list = tuple(states)
            sources = await asyncio.to_thread(self._build_sources, state_list)
            started: list[FileEventSource] = []
            try:
                for source in sources:
                    await source.start()
                    started.append(source)
                # `return_exceptions=True` keeps a group partially armed rather
                # than discarding every healthy watcher because one root is
                # gone. The failure is logged, not swallowed silently.
                results = await asyncio.gather(
                    *(source.wait_ready(timeout=5.0) for source in started),
                    return_exceptions=True,
                )
                for source, result in zip(started, results, strict=True):
                    if isinstance(result, BaseException):
                        logger.warning(
                            "activity source not ready for {}: {}",
                            ", ".join(str(root) for root in source.roots),
                            type(result).__name__,
                        )
                await self._runtime.reconcile()
            except BaseException:
                for source in reversed(started):
                    with suppress(Exception):
                        await source.aclose()
                self._sources.clear()
                raise
            self._sources = started

    def _build_sources(self, states: tuple[WorkspaceState, ...]) -> list[FileEventSource]:
        self._control_files.clear()
        self._worktree_dirs.clear()
        self._transcript_files.clear()
        self._transcript_sessions.clear()
        self._git_paths.clear()

        control_roots: set[Path] = {self._store.path.resolve()}
        git_roots: set[Path] = set()
        sidecars = paths.agent_sidecar_dir().resolve()
        # A missing directory is not an inert root: watching its parent captures
        # the one creation edge, then `_source_changed` replaces this with the
        # directory watch. This avoids a recursive state-directory watch.
        paths.ensure_dir(sidecars)

        transcript_roots: set[Path] = set()

        for state in states:
            key = (state.repo_root, state.id)
            root = Path(state.worktree_path)
            for control_file in (
                PhaseFile.path_for(root, state.id),
                paths.agent_exit_path(state.id),
            ):
                resolved = control_file.resolve()
                self._control_files[resolved] = (
                    key,
                    RefreshDomain.PHASE
                    if control_file == PhaseFile.path_for(root, state.id)
                    else RefreshDomain.RUNTIME,
                )
                control_roots.add(resolved)
            self._add_worktree_roots(state, key)
            self._add_git_roots(state, key, git_roots)
            transcript_roots.update(self._add_transcript_roots(state, key))

        sources = [
            FileEventSource(control_roots, self._control_events, on_recovery=self._recover),
            FileEventSource(
                [sidecars], self._control_events, recursive=True, on_recovery=self._recover
            ),
        ]
        if self._worktree_dirs:
            sources.append(
                FileEventSource(
                    self._worktree_dirs,
                    self._worktree_events,
                    on_recovery=self._recover,
                    recursive=False,
                )
            )
        if git_roots:
            sources.append(
                FileEventSource(
                    git_roots,
                    self._git_events,
                    on_recovery=self._recover,
                    recursive=True,
                    include_ignored=True,
                )
            )
        if transcript_roots:
            sources.append(
                FileEventSource(
                    transcript_roots | set(self._transcript_sessions),
                    self._transcript_events,
                    on_recovery=self._recover,
                    recursive=True,
                )
            )
        return sources

    def _add_git_roots(self, state: WorkspaceState, key: WorkspaceKey, roots: set[Path]) -> None:
        """Watch git's own HEAD/index/ref edges, not source-file coincidences."""
        try:
            repo = GitRepo(Path(state.worktree_path))
            common = repo.common_dir()
            local = repo.git_dir()
        except Exception as exc:
            logger.debug("activity source git-dir failed for {}: {}", state.id, exc)
            return
        if common is None or not common.is_dir():
            return
        local = local or common
        for path in (local / "HEAD", local / "index", common / "refs", common / "packed-refs"):
            # Missing refs/index still need a creation edge (e.g. an unborn repo).
            resolved = path.resolve()
            roots.add(resolved)
            self._git_paths.setdefault(resolved, set()).add(key)

    def _add_worktree_roots(self, state: WorkspaceState, key: WorkspaceKey) -> None:
        root = Path(state.worktree_path)
        if not root.is_dir():
            return
        # Root catches direct untracked files and a new direct child. Every other
        # root is a parent of a path Git already exposes, never `node_modules` or
        # a build tree traversed recursively.
        self._worktree_dirs.setdefault(root.resolve(), set()).add(key)
        try:
            files = GitRepo(Path(state.repo_root)).list_files(root)
        except Exception as exc:
            logger.debug("activity source git listing failed for {}: {}", state.id, exc)
            return
        for relpath in files:
            parent = (root / relpath).parent.resolve()
            while parent.is_relative_to(root.resolve()):
                if parent.is_dir():
                    self._worktree_dirs.setdefault(parent, set()).add(key)
                if parent == root.resolve():
                    break
                parent = parent.parent

    def _add_transcript_roots(self, state: WorkspaceState, key: WorkspaceKey) -> set[Path]:
        """Locate persisted candidates once, scoped to the launch context.

        Candidate IDs prove only where to watch; the activity projection owns
        session adoption and sidecar routing. An event never repeats discovery.
        """
        kind = state.agent_kind or "generic"
        adapter = get_adapter(kind)
        context = state.transcript_context
        variable = TranscriptContext.CONFIG_DIR_ENV.get(kind)
        config_dir = context.config_dir if context is not None else None
        candidates: list[tuple[str, Path]] = []
        seen: set[tuple[str, Path]] = set()
        with config_dir_scope(variable, config_dir):
            for cwd in state.transcript_scan_cwds:
                session_ids = [state.agent_session_id] if state.agent_session_id else []
                try:
                    session_ids.extend(
                        session_id for session_id, _birth, _mtime in adapter.discover_births(cwd)
                    )
                except Exception as exc:
                    logger.debug("activity source discovery failed for {}: {}", state.id, exc)
                for session_id in session_ids:
                    candidate = (session_id, cwd)
                    if candidate not in seen:
                        seen.add(candidate)
                        candidates.append(candidate)
            for session_id, cwd in candidates:
                try:
                    located = adapter.locate_transcripts(cwd, session_id)
                except Exception as exc:
                    logger.debug("activity source locate failed for {}: {}", state.id, exc)
                    continue
                for path in located:
                    resolved = path.resolve()
                    self._transcript_files.setdefault(resolved, set()).add(key)
                    if resolved.name == f"{session_id}.jsonl":
                        # The session-owned subdirectory a NEW sidechain file
                        # lands under (`<project>/<session_id>/subagents/…`,
                        # per `_ClaudeHome.locate`'s own glob) — a sibling of
                        # the main transcript, never the shared PROJECT folder
                        # that holds every session's main file for one cwd.
                        self._transcript_sessions.setdefault(
                            resolved.parent / session_id, set()
                        ).add(key)
        return set(self._transcript_files)

    @staticmethod
    def _watch_signature(states: Iterable[WorkspaceState]) -> tuple[tuple[object, ...], ...]:
        """The persisted facts that can change which paths this group watches."""
        return tuple(
            sorted(
                (
                    state.id,
                    state.repo_root,
                    state.worktree_path,
                    state.agent_kind,
                    state.agent_session_id,
                    state.transcript_context,
                )
                for state in states
            )
        )

    def _recover(self, _recovery: FileEventRecovery) -> None:
        self._runtime.request_recovery()

    async def _close_sources(self) -> None:
        sources, self._sources = self._sources, []
        for source in reversed(sources):
            with suppress(Exception):
                await source.aclose()

    async def close(self) -> None:
        """Cancel the owned refresh task and deterministically tear down watchers."""
        if self._closing:
            return
        self._closing = True
        task, self._state_task = self._state_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with self._sources_lock:
            await self._close_sources()
        self._spool.close()
        if self._spool_task is not None:
            self._spool_task.cancel()
            await asyncio.gather(self._spool_task, return_exceptions=True)
            self._spool_task = None
        self._control_files.clear()
        self._worktree_dirs.clear()
        self._transcript_files.clear()
        self._transcript_sessions.clear()
        self._git_paths.clear()
        self._started = False
