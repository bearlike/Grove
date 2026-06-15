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

from collections.abc import Callable
from pathlib import Path

from grove.core.config import GroveConfig
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore


class RepoRegistry:
    """Lazy ``WorkspaceManager`` cache keyed by canonical ``repo_root``."""

    def __init__(
        self,
        *,
        cfg: GroveConfig,
        store: JsonWorkspaceStore,
        config_loader: Callable[[Path], GroveConfig] | None = None,
    ) -> None:
        self._cfg = cfg
        self._store = store
        # How each repo's config is resolved at first access. The daemon
        # injects ``load_config`` so every Manager sees its OWN cascade
        # (defaults → user → project ``<repo>/.grove/config.json`` →
        # project-local). Without this the daemon validated ``create`` against
        # the single global config it loaded with ``repo_root=None`` — so a
        # project-scoped agent read as "unknown" and a project ``init_script``
        # read as disabled → ``SKIPPED`` (issues #46/#47). ``None`` falls back
        # to the shared ``cfg`` for every repo — the seam tests use to inject
        # an in-memory config with no files on disk. Auth/daemon sections are
        # safe to vary per repo here: they are consumed only from the global
        # ``cfg`` build_app holds, never off a registry Manager.
        self._config_loader = config_loader
        self._cache: dict[Path, WorkspaceManager] = {}

    def get(self, repo_root: Path) -> WorkspaceManager:
        """Return (or create) a Manager for ``repo_root``.

        ``repo_root`` is canonicalized via ``Path.resolve()`` so distinct
        symlink paths to the same repo collapse to a single Manager. The
        Manager's config is resolved once per repo via ``config_loader`` (or
        the shared ``cfg`` when none was injected) and cached with it.
        """
        key = repo_root.resolve()
        mgr = self._cache.get(key)
        if mgr is None:
            cfg = self._config_loader(key) if self._config_loader is not None else self._cfg
            mgr = WorkspaceManager(repo_root=key, cfg=cfg, store=self._store)
            self._cache[key] = mgr
        return mgr

    def known_roots(self) -> list[Path]:
        """Repos that exist, by union of two sources.

        The deduped union (``Path.resolve()`` collapses symlinks) of:
        store-derived roots (every repo with ≥1 persisted workspace) and
        config-declared roots (``cfg.projects``). The config arm is what keeps
        an *empty* project visible — a freshly-added repo, or one whose
        workspaces were all killed, has no store row but stays a known project.

        This is the single place that decides "which repos exist", so every
        downstream consumer (``GET /workspaces``, ``ActivityService``, the
        pickers) inherits empty-project visibility with no further change. Reads
        fresh from the store each call so newly created repos appear without a
        restart.
        """
        roots = {root.resolve() for root in self._store.list_repo_roots()}
        roots.update(self._declared_roots())
        return list(roots)

    def _declared_roots(self) -> set[Path]:
        """Config-declared project roots that are existing git repos.

        Best-effort by contract: a ``cfg.projects`` entry that doesn't exist or
        isn't a git repo is silently dropped (never fail config load or a
        request). ``~`` is expanded and the path resolved at consume time — the
        same symlink-collapse rule the store roots and Manager keys follow.
        """
        roots: set[Path] = set()
        for raw in self._cfg.projects:
            try:
                resolved = Path(raw).expanduser().resolve()
            except OSError:
                continue
            if (resolved / ".git").exists():
                roots.add(resolved)
        return roots
