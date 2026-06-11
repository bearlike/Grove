"""Per-repo WorkspaceManager registry — the engine's multi-repo manager cache.

Grove serves many repos out of one process (the daemon, and the cross-project
Activity Dashboard). ``RepoRegistry`` is the cache: ``get(repo_root)`` returns a
Manager, instantiating one on first access and reusing it thereafter. State on
disk is shared (``JsonWorkspaceStore`` is global), so the registry's job is
purely to avoid re-running ``WorkspaceManager.__init__`` per request.

It lives in ``grove.core`` (not the daemon) because it is pure engine — only
``config`` + ``manager`` + ``store`` — and more than one inward consumer needs it:
the daemon's HTTP layer and the ``ActivityService``. Dependencies flow inward, so
the shared cache sits in core and clients compose it. ``grove.daemon.repos``
re-exports it for back-compat.
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

        Used by ``GET /workspaces`` and the ``ActivityService`` to decide which
        Managers to enumerate. Reads fresh from the store each call so newly
        created repos appear without a restart.
        """
        return self._store.list_repo_roots()
