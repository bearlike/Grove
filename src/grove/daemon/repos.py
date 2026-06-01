"""Per-repo WorkspaceManager registry.

The HTTP daemon serves multiple repos out of one process. ``RepoRegistry``
is the cache: ``get(repo_root)`` returns a Manager, instantiating one on
first access and reusing it thereafter. State on disk is shared
(``JsonWorkspaceStore`` is global per CLAUDE.md), so the registry's job
is purely to avoid re-running ``WorkspaceManager.__init__`` per request.
"""

from __future__ import annotations

from pathlib import Path

from grove.core.config import GroveConfig
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore


class RepoRegistry:
    """Lazy ``WorkspaceManager`` cache keyed by canonical ``repo_root``."""

    def __init__(self, *, cfg: GroveConfig, store: JsonWorkspaceStore) -> None:
        self._cfg = cfg
        self._store = store
        self._cache: dict[Path, WorkspaceManager] = {}

    def get(self, repo_root: Path) -> WorkspaceManager:
        """Return (or create) a Manager for ``repo_root``.

        ``repo_root`` is canonicalized via ``Path.resolve()`` so distinct
        symlink paths to the same repo collapse to a single Manager.
        """
        key = repo_root.resolve()
        mgr = self._cache.get(key)
        if mgr is None:
            mgr = WorkspaceManager(repo_root=key, cfg=self._cfg, store=self._store)
            self._cache[key] = mgr
        return mgr

    def known_roots(self) -> list[Path]:
        """Repos that have at least one persisted workspace.

        Used by ``GET /workspaces`` to decide which Managers to call
        ``list()`` on. Reads fresh from the store each call so newly
        created repos appear without restart.
        """
        return self._store.list_repo_roots()
