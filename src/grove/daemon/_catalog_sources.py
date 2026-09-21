"""Native filesystem edges for the daemon-owned catalog and diagram indexes.

The catalog and gallery are event-owned snapshots, not poll inputs.  This owner
bootstraps them once, watches only the adapter profile roots and known worktree
scopes they already use, then applies each later file edge path-locally.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from contextlib import suppress
from pathlib import Path

from loguru import logger

from grove.core.admission import Admission, AdmissionLimits, BoundedInbox, InboxClosed
from grove.core.agents import all_adapters
from grove.core.agents.claude_code import _ClaudeHome
from grove.core.agents.codex import _CodexHome
from grove.core.agents.transcript_scope import config_dir_scope
from grove.core.attachments import AttachmentStore
from grove.core.file_events import FileEvent, FileEventBatch, FileEventRecovery, FileEventSource
from grove.core.registry import RepoRegistry
from grove.core.workspace import TranscriptContext
from grove.daemon._catalog import _CatalogMemo, _GalleryMemo

_EVENT_OVERHEAD_BYTES = 64


class _CatalogSources:
    """Own bounded native watches that feed catalog and gallery path mutations.

    ``start`` performs the only full memo bootstrap off the event loop and
    awaits watcher readiness before returning.  Afterwards, a fixed inbox
    consumer serializes path-local mutations in a worker thread; source
    callbacks never perform filesystem reads themselves.  An inbox loss is one
    explicit full-index recovery, never a timer or fallback poll.
    """

    def __init__(
        self,
        catalog: _CatalogMemo,
        gallery: _GalleryMemo,
        registry: RepoRegistry,
        on_changed: Callable[[], None],
        *,
        limits: AdmissionLimits | None = None,
        source_factory: Callable[..., FileEventSource] = FileEventSource,
    ) -> None:
        self._catalog = catalog
        self._gallery = gallery
        self._registry = registry
        self._on_changed = on_changed
        self._source_factory = source_factory
        self._limits = limits or AdmissionLimits()
        self._inbox = BoundedInbox[FileEvent](self._limits)
        self._sources: list[FileEventSource] = []
        self._pending_sources: list[FileEventSource] = []
        self._consumer: asyncio.Task[None] | None = None
        self._recovery: asyncio.Task[None] | None = None
        self._recovery_pending = False
        self._recovery_followup = False
        self._started = False
        self._closed = False

    async def start(self) -> None:
        """Bootstrap indexes and start ready native sources, atomically enough to close.

        The bootstrap is deliberately before watcher startup: a path event can
        then mutate a complete snapshot rather than racing the initial scan.
        Any partial watcher group is closed if startup or readiness fails.
        """
        if self._closed:
            raise RuntimeError("catalog sources are closed")
        if self._started:
            return
        try:
            self._pending_sources = await asyncio.to_thread(self._bootstrap_and_build_sources)
            self._inbox.bind()
            self._consumer = asyncio.create_task(
                self._drain(), name="grove-catalog-source-delivery"
            )
            for source in self._pending_sources:
                await source.start()
            # A watcher that cannot arm is a degraded observer, never a fatal
            # error: these roots are worktrees and profile directories other
            # processes own, so one being gone must not take daemon startup
            # with it. The reconcile below re-reads the index regardless of
            # which watchers armed.
            results = await asyncio.gather(
                *(source.wait_ready(timeout=5.0) for source in self._pending_sources),
                return_exceptions=True,
            )
            for source, result in zip(self._pending_sources, results, strict=True):
                if isinstance(result, BaseException):
                    logger.warning(
                        "catalog source not ready for {}: {}",
                        ", ".join(str(root) for root in source.roots),
                        type(result).__name__,
                    )
            # A file can change after the off-loop bootstrap but before every
            # native watch reports ready. Reconcile once after that boundary so
            # readiness has a complete, current index rather than a blind gap.
            await asyncio.to_thread(self._recover_indexes)
        except BaseException:
            await self._close_sources(self._pending_sources)
            self._pending_sources = []
            await self._stop_consumer()
            self._inbox.close()
            self._inbox = BoundedInbox[FileEvent](self._limits)
            raise
        self._sources, self._pending_sources = self._pending_sources, []
        self._started = True

    async def close(self) -> None:
        """Close every owned watcher and worker deterministically, once."""
        if self._closed:
            return
        self._closed = True
        sources, self._sources = self._sources, []
        await self._close_sources((*sources, *self._pending_sources))
        self._pending_sources = []
        self._inbox.close()
        await self._stop_consumer()
        self._started = False

    def _bootstrap_and_build_sources(self) -> list[FileEventSource]:
        """Do cold scans and filesystem root discovery away from the daemon loop."""
        self._catalog.rows()
        self._gallery.items()
        transcript_roots = self._transcript_roots()
        gallery_roots, attachment_roots = self._gallery_roots()
        sources: list[FileEventSource] = []
        if transcript_roots:
            sources.append(self._new_source(transcript_roots, recursive=True))
        if gallery_roots:
            sources.append(self._new_source(gallery_roots, recursive=False))
        if attachment_roots:
            # Attachments are a small explicit subtree the gallery scans outside
            # Git.  Watching only it recursively catches new mockups without
            # turning a worktree (and its node_modules) into a recursive root.
            sources.append(self._new_source(attachment_roots, recursive=True))
        return sources

    def _new_source(self, roots: Iterable[Path], *, recursive: bool) -> FileEventSource:
        return self._source_factory(
            roots,
            self._on_batch,
            on_recovery=self._on_source_recovery,
            recursive=recursive,
        )

    def _transcript_roots(self) -> set[Path]:
        """Existing adapter roots plus every persisted launched-profile root.

        The private adapter-home functions are the adapters' own source-of-
        truth for their on-disk layouts.  Persisted ``TranscriptContext`` adds
        profiles a hermetic launch used but the daemon process cannot see in its
        ambient configuration; no host profile name or path is invented here.
        """
        roots: set[Path] = set()
        adapters = {adapter.kind for adapter in all_adapters()}
        for adapter_kind in adapters & TranscriptContext.CONFIG_DIR_ENV.keys():
            roots.update(self._roots_for_kind(adapter_kind))
        for state in self._registry.workspace_states():
            context = state.transcript_context
            kind = state.agent_kind
            if (
                context is None
                or kind is None
                or kind not in adapters
                or kind not in TranscriptContext.CONFIG_DIR_ENV
            ):
                continue
            variable = TranscriptContext.CONFIG_DIR_ENV[kind]
            with config_dir_scope(variable, context.config_dir):
                roots.update(self._roots_for_kind(kind))
        # A PROFILE DIRECTORY THAT DOES NOT EXIST YET IS THE ORDINARY CASE, so
        # the root is PASSED THROUGH rather than dropped. An agent's provider
        # creates `<config>/projects` on its first session, routinely after the
        # daemon started — and a root filtered out here was never reconsidered,
        # so on a fresh install the catalog silently never saw a transcript
        # again until a restart.
        #
        # `FileEventSource` owns the resolution and must be left to: it treats an
        # absent root as a DIRECTORY (the reading that can still be right once
        # the provider creates it), watches its nearest existing ancestor, and
        # still filters admitted events against the real path. Substituting the
        # ancestor HERE looks equivalent and is not — the ancestor would become
        # the root itself, so containment admits everything beneath it (measured:
        # the whole pytest tmp_path, whose own state and config dirs then flood
        # the batch and the real transcript is lost).
        return {root.resolve() for root in roots}

    @staticmethod
    def _roots_for_kind(kind: str) -> tuple[Path, ...]:
        """The transcript roots to WATCH, which is not the same set to READ.

        ``_ClaudeHome.projects_dirs()`` is documented as returning only the
        dirs that EXIST, which is right for a reader and wrong here: a provider
        creates `<config>/projects` on its first session, routinely after the
        daemon started, so a watch set built from it is empty on a fresh
        install and the catalog never sees a transcript again until a restart.
        Deriving from the unfiltered ``config_dirs()`` cascade instead lets
        ``_nearest_existing`` watch the closest real ancestor; the source's own
        containment filter still admits only paths under the true root, so
        watching an ancestor widens what is OBSERVED, never what is reported.
        """
        if kind == "claude_code":
            return tuple(base / "projects" for base in _ClaudeHome.config_dirs())
        if kind == "codex":
            return (_CodexHome.sessions_dir(),)
        return ()

    def _gallery_roots(self) -> tuple[set[Path], set[Path]]:
        """Known worktree scopes plus the gallery's explicit attachment trees.

        A worktree root catches direct additions.  Existing nested diagrams add
        only their parent directory, as supplied by Git, so the watcher never
        recursively descends through dependency or build trees.

        It reads ``DiagramGallery.census`` rather than re-walking the worktrees
        itself. The two used to enumerate independently, so daemon startup ran
        one ``git ls-files`` per worktree TWICE — measured on the reference host,
        189 worktrees for the same 4 diagrams both times.
        """
        roots: set[Path] = set()
        attachments: set[Path] = set()
        for _repo_root, worktree, diagrams in self._gallery.census():
            roots.add(worktree.resolve())
            for path in diagrams:
                if path.parent.is_dir():
                    roots.add(path.parent.resolve())
            attachment_root = AttachmentStore.root(worktree)
            if attachment_root.is_dir():
                attachments.add(attachment_root.resolve())
        return roots, attachments

    def _on_batch(self, batch: FileEventBatch) -> None:
        """Accept path edges cheaply; the fixed owner task does the filesystem read."""
        for event in batch.events:
            if self._closed:
                return
            if self._recovery_pending:
                self._recovery_followup = True
                return
            outcome = self._inbox.offer(
                event,
                size_bytes=len(str(event.path).encode()) + _EVENT_OVERHEAD_BYTES,
                key=str(event.path),
            )
            if outcome in (Admission.FULL, Admission.TOO_LARGE):
                self._request_recovery()
                return

    def _on_source_recovery(self, _recovery: FileEventRecovery) -> None:
        """A source-level gap has the same explicit repair as inbox overflow."""
        self._request_recovery()

    def _request_recovery(self) -> None:
        if self._closed:
            return
        if self._recovery_pending:
            self._recovery_followup = True
            return
        self._recovery_pending = True
        # The recovery task closes and awaits THIS consumer before replacing its
        # inbox. A drain captures its inbox locally, so an in-flight finally can
        # never complete its delivery against the replacement inbox.
        old_inbox = self._inbox
        old_consumer = self._consumer
        old_inbox.close()
        self._consumer = None
        self._recovery = asyncio.create_task(
            self._recover_indexes_once(old_consumer), name="grove-catalog-source-recovery"
        )

    async def _drain(self) -> None:
        inbox = self._inbox
        while True:
            try:
                delivery = await inbox.take()
            except InboxClosed:
                return
            try:
                changed = await asyncio.to_thread(self._update_path, delivery.value.path)
                if changed and not self._recovery_pending:
                    self._publish_change()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("catalog source path update failed")
                self._request_recovery()
            finally:
                inbox.complete(delivery)

    def _update_path(self, path: Path) -> bool:
        """Apply one edge in dependency order: gallery joins the fresh catalog."""
        catalog_changed = self._catalog.update_path(path)
        gallery_changed = self._gallery.update_path(path)
        return catalog_changed or gallery_changed

    async def _recover_indexes_once(self, old_consumer: asyncio.Task[None] | None) -> None:
        try:
            if old_consumer is not None:
                await asyncio.gather(old_consumer, return_exceptions=True)
            if not self._closed:
                await asyncio.to_thread(self._recover_indexes)
                self._publish_change()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("catalog source recovery failed")
        finally:
            self._recovery_pending = False
            self._recovery = None
            if not self._closed:
                if self._recovery_followup:
                    self._recovery_followup = False
                    self._request_recovery()
                else:
                    self._inbox = BoundedInbox[FileEvent](self._limits)
                    self._inbox.bind()
                    self._consumer = asyncio.create_task(
                        self._drain(), name="grove-catalog-source-delivery"
                    )

    def _recover_indexes(self) -> None:
        # A rejected batch has no trustworthy path set, so the deliberate full
        # rebuild is the one repair.  It runs only for this loss boundary, never
        # on elapsed time; gallery follows catalog because attribution joins it.
        self._catalog.reconcile()
        self._gallery.reconcile()

    def _publish_change(self) -> None:
        try:
            self._on_changed()
        except Exception:
            logger.exception("catalog source change callback failed")

    async def _stop_consumer(self) -> None:
        tasks = tuple(task for task in (self._consumer, self._recovery) if task is not None)
        self._consumer = None
        self._recovery = None
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    async def _close_sources(sources: Iterable[FileEventSource]) -> None:
        for source in reversed(tuple(sources)):
            with suppress(Exception):
                await source.aclose()


__all__ = []
