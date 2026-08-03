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
from dataclasses import dataclass
from pathlib import Path

from grove.core.config import GroveConfig
from grove.core.git import detect_root
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore


@dataclass(frozen=True, slots=True)
class Project:
    """A listable project: an agent working directory plus the git repo that
    anchors its worktrees.

    For a top-level repo, ``cwd == repo_root``. For a nested project, ``cwd`` is
    a subdirectory of the repo and ``repo_root`` is the enclosing repo — distinct
    projects can therefore share one ``repo_root`` (one Manager, one worktree
    family) while differing only in where the agent session starts.
    """

    repo_root: Path
    cwd: Path


class RepoRegistry:
    """Lazy ``WorkspaceManager`` cache keyed by canonical ``repo_root``."""

    def __init__(
        self,
        *,
        cfg: GroveConfig,
        store: JsonWorkspaceStore,
        config_loader: Callable[[Path], GroveConfig] | None = None,
        on_project_registered: Callable[[Path], None] | None = None,
    ) -> None:
        self._cfg = cfg
        self._store = store
        # Fired exactly once per repo, the moment its Manager is first cached —
        # the engine's definition of "project registration". A caller wires
        # this to `ProjectInfra.ensure` (best-effort, never blocking `get()`)
        # to move the devcontainer image build off the per-workspace create
        # path and onto first project access instead. ``None`` (default) keeps
        # `get()` a pure cache, matching ``config_loader``'s own opt-in shape.
        self._on_project_registered = on_project_registered
        # How each repo's config is resolved at first access. The daemon
        # injects ``load_config`` so every Manager sees its OWN cascade
        # (defaults → user → project ``<repo>/.grove/config.json`` →
        # project-local). Without this the daemon validated ``create`` against
        # the single global config it loaded with ``repo_root=None`` — so a
        # project-scoped agent read as "unknown" and a project ``init_script``
        # read as disabled → ``SKIPPED``. ``None`` falls back to the shared
        # ``cfg`` for every repo — the seam tests use to inject
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
            if self._on_project_registered is not None:
                self._on_project_registered(key)
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
        roots.update(p.repo_root for p in self._declared_projects())
        return list(roots)

    def known_projects(self) -> list[Project]:
        """Listable projects, by union of two sources — the listing seam.

        Where ``known_roots()`` answers "which *repos* exist" (the Manager-dispatch
        + repo-validation seam), this answers "which *projects* should a client
        offer", which is a superset: a single repo can expose several nested
        subdirectory projects. The deduped (by ``cwd``) union of:

        - store-derived repo roots, each as a repo-level ``Project`` (``cwd ==
          repo_root``) — the empty-project-visibility arm carried forward;
        - config-declared projects, each ``Project(repo_root=<enclosing repo>,
          cwd=<declared path>)`` so a declared *subdirectory* lists distinctly
          while still anchoring its worktrees at the true repo root.

        Reads fresh from the store each call (new repos appear without a restart),
        the same contract ``known_roots()`` holds.
        """
        by_cwd: dict[Path, Project] = {}
        for root in self._store.list_repo_roots():
            resolved = root.resolve()
            by_cwd.setdefault(resolved, Project(repo_root=resolved, cwd=resolved))
        for project in self._declared_projects():
            by_cwd.setdefault(project.cwd, project)
        return list(by_cwd.values())

    def _declared_projects(self) -> list[Project]:
        """Config-declared projects resolved to ``(enclosing repo root, cwd)``.

        Best-effort by contract: a ``cfg.projects`` entry that doesn't exist or
        isn't inside a git repo is silently dropped (never fail config load or a
        request). ``~`` is expanded and paths ``resolve()``d at consume time —
        the same symlink-collapse rule store roots and Manager keys follow.

        The common case — an entry that IS a repo root — is decided by a cheap
        ``.git`` stat (a stat is far cheaper than a subprocess on this hot path)
        and yields ``cwd == repo_root``. Only an entry that ISN'T itself a repo
        root pays one
        ``git rev-parse`` to find its enclosing repo, which is what lets a nested
        subdirectory surface as a distinct project anchored at the real root.
        """
        projects: list[Project] = []
        for raw in self._cfg.projects:
            try:
                resolved = Path(raw).expanduser().resolve()
            except OSError:
                continue
            if not resolved.exists():
                continue
            if (resolved / ".git").exists():
                projects.append(Project(repo_root=resolved, cwd=resolved))
                continue
            root = detect_root(resolved)
            if root is not None:
                projects.append(Project(repo_root=root, cwd=resolved))
        return projects
