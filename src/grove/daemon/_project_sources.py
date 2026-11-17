"""Pick up a project added to the user config without a daemon restart.

The daemon loads its global config once, at boot, so ``cfg.projects`` — the
list that keeps a zero-workspace repo visible on every surface — used to be
frozen for the process's life: ``grove config add-project`` (or a hand edit)
reached nothing until a restart. This owner watches the one file that list
lives in, re-reads the cascade off the loop on each edge, and hands the new
list to ``RepoRegistry.reload_projects``. Every reader of ``known_projects()``
/ ``known_roots()`` then sees it on its next call; ``on_changed`` is how the
daemon pushes the change to clients that hold a snapshot.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from loguru import logger

from grove.core import paths
from grove.core.config import GroveConfig, load_config
from grove.core.errors import ConfigError
from grove.core.file_events import FileEventBatch, FileEventSource
from grove.core.registry import RepoRegistry


class _ProjectSources:
    """One native watch on the user config, coalesced into ordered reloads."""

    def __init__(
        self,
        registry: RepoRegistry,
        on_changed: Callable[[], Awaitable[None]],
        *,
        config_path: Path | None = None,
        loader: Callable[[], GroveConfig] = lambda: load_config(repo_root=None),
    ) -> None:
        self._registry = registry
        self._on_changed = on_changed
        self._config_path = config_path or paths.user_config_path()
        self._loader = loader
        self._source: FileEventSource | None = None
        self._task: asyncio.Task[None] | None = None
        self._dirty = False
        self._push: asyncio.Task[None] | None = None
        self._stale = False

    async def start(self) -> None:
        # A missing FILE is a legitimate root (its directory is watched), but a
        # missing DIRECTORY would fall back to a non-recursive watch on an
        # ancestor two levels up that can never see the file appear. The
        # directory is Grove's own, so create it rather than miss the first
        # `add-project` on a fresh host.
        paths.ensure_dir(self._config_path.parent)
        self._source = FileEventSource([self._config_path], self._on_batch)
        await self._source.start()
        await self._source.wait_ready(timeout=5.0)
        # An edit between the boot-time config load and this watch arming has
        # no edge to report it; one reload closes that window (a no-op when
        # nothing changed, since `reload_projects` compares).
        self._on_batch(None)

    def _on_batch(self, _batch: FileEventBatch | None) -> None:
        self._dirty = True
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._reload(), name="grove-project-reload")

    async def _reload(self) -> None:
        # Edges arriving mid-reload set `_dirty` again, so the last write always
        # gets its own read — an editor's truncate-then-write is two edges.
        while self._dirty:
            self._dirty = False
            try:
                cfg = await asyncio.to_thread(self._loader)
            except ConfigError as exc:
                # A half-saved or mistyped file keeps the last good list rather
                # than emptying every surface's project picker.
                logger.warning("user config reload skipped: {}", exc)
                continue
            if self._registry.reload_projects(cfg.projects):
                logger.info("known projects reloaded from {}", self._config_path)
                self._propagate()

    def _propagate(self) -> None:
        # Propagation is a fleet reconcile (seconds on a real host), so it runs
        # as its own coalesced loop: the registry above is already current for
        # every request, and the NEXT edit's read never waits behind it.
        self._stale = True
        if self._push is None or self._push.done():
            self._push = asyncio.create_task(self._push_changes(), name="grove-project-push")

    async def _push_changes(self) -> None:
        while self._stale:
            self._stale = False
            try:
                await self._on_changed()
            except Exception:
                logger.exception("project reload propagation failed")

    async def close(self) -> None:
        for task in (self._task, self._push):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self._task = self._push = None
        if self._source is not None:
            await self._source.aclose()
            self._source = None


__all__ = []
