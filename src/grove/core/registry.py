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

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from grove.core import paths
from grove.core.config import GroveConfig
from grove.core.contracts.project_identity import ProjectIdentity
from grove.core.errors import GroveError, ProjectNotOnNode, WorkspaceNotFound
from grove.core.git import GitRepo, detect_root
from grove.core.lifecycle_stages import Stages
from grove.core.manager import WorkspaceManager
from grove.core.native import NativeSteerClient
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import ProvisionStatus, ShareToken, WorkspaceRef, WorkspaceState


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
        native_steer: NativeSteerClient | None = None,
    ) -> None:
        self._cfg = cfg
        self._store = store
        # How a native workspace's owner is reached. The daemon holds the
        # coordinator the owner workers connect to, so it injects an in-process
        # client here and every Manager it mints steers without a network hop;
        # ``None`` leaves each Manager its out-of-process default (the daemon's
        # own steer routes), which is what a CLI or TUI process needs.
        self._native_steer = native_steer
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
        # The declared project list is held apart from ``cfg`` so a long-lived
        # owner can swap it when the user config changes (`reload_projects`)
        # without rebuilding the registry or dropping its cached Managers.
        self._projects: tuple[str, ...] = tuple(cfg.projects)
        self._cache: dict[Path, WorkspaceManager] = {}
        # A repo's identity changes only when its origin or default branch does,
        # which is rare enough that a process-lifetime memo is right; it costs
        # two git calls per repo, once.
        self._identities: dict[Path, ProjectIdentity | None] = {}
        self._manager_subscribers: list[Callable[[Path, WorkspaceManager], None]] = []

    def subscribe_managers(
        self, callback: Callable[[Path, WorkspaceManager], None]
    ) -> Callable[[], None]:
        """Observe existing and newly materialized managers without rescanning readers."""
        self._manager_subscribers.append(callback)
        for root, manager in tuple(self._cache.items()):
            callback(root, manager)

        def unsubscribe() -> None:
            if callback in self._manager_subscribers:
                self._manager_subscribers.remove(callback)

        return unsubscribe

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
            mgr = WorkspaceManager(
                repo_root=key,
                cfg=cfg,
                store=self._store,
                native_steer=self._native_steer,
                config_loader=self._config_loader,
            )
            self._cache[key] = mgr
            for callback in tuple(self._manager_subscribers):
                callback(key, mgr)
            if self._on_project_registered is not None:
                self._on_project_registered(key)
        return mgr

    def reload_projects(self, projects: Iterable[str]) -> bool:
        """Replace the config-declared project list; ``True`` when it changed.

        The one seam that lets a project added to the user config reach a
        process that loaded its config long ago (the daemon, an open TUI)
        without a restart. Only the DECLARED arm moves: cached Managers keep
        the cascade they resolved, because a new project is a new repo and
        mints its own Manager — with a fresh cascade — on first access.
        """
        new = tuple(projects)
        changed = new != self._projects
        self._projects = new
        return changed

    def resolve_workspace(self, ref: str) -> tuple[WorkspaceManager, WorkspaceState]:
        """The workspace ``ref`` names, and a Manager bound to ITS repo.

        A workspace id is unique across the host, and the repo a workspace
        belongs to is recorded on the workspace — so naming one is enough to
        reach it, and asking the caller to also be standing in the right
        directory is a precondition the operation never had. Every id-addressed
        verb (attach, message, pause, kill, …) resolves here, which is why they
        work from anywhere: the record supplies the repo the Manager binds to.

        Same shape and same reason as :meth:`resolve_share` — one store read
        answers for every repo, and only the single matching repo's Manager is
        materialized, through the ordinary cache. Walking ``known_roots()``
        instead would resolve every project's whole config cascade to answer a
        question one record already answers.

        Raises ``GroveError`` naming the candidates when the ref matches
        nothing or several. Ambiguity is judged HOST-wide, so a prefix that was
        unique inside one repo can now be ambiguous — which is the honest
        answer for a ref that no longer carries a repo to disambiguate it.
        """
        state = WorkspaceRef.resolve(self._store.load_all(), ref)
        return self.get(Path(state.repo_root)), state

    def resolve_share(self, token: str) -> tuple[WorkspaceManager, WorkspaceState] | None:
        """The workspace a public share token names, or ``None``.

        The ONE place a token becomes an identity, which is why it belongs here
        rather than on a Manager: a token is host-wide by construction (the
        public reader is handed a bare string and has no repo to scope it with),
        so resolution has to happen above the per-repo cache.

        It reads the STORE directly rather than walking ``known_roots()`` and
        asking each Manager. One store read answers for every repo, where the
        walk would resolve every project's whole config cascade — and instantiate
        a Manager per repo — on a request that is unauthenticated by design.
        Only the single matching repo's Manager is then materialized, through the
        ordinary cache.

        ``None`` covers every failure identically: no such token, an expired
        token, a token belonging to a workspace that has since been unshared, or
        an empty string. A caller must not distinguish them; the whole point of
        the capability is that a wrong guess learns nothing.
        """
        if not token:
            return None
        now = datetime.now(UTC)
        for state in self._store.load_all():
            if not ShareToken.matches(token, state.share_token):
                continue
            if state.share_expires_at is not None and now >= state.share_expires_at:
                return None
            return self.get(Path(state.repo_root)), state
        return None

    def shared_in(self, repo_root: Path) -> list[WorkspaceState]:
        """Every publicly-shared workspace in one repo, newest first.

        What the public view's rail lists. Scoped by ``repo_root`` — the repo,
        not the nested ``Project`` — because "the same project" to somebody
        reading a shared link means the codebase, and a security-adjacent path
        is the wrong place to introduce a second notion of project identity.

        The caller is trusted to already hold a token for ONE of these, and the
        consequence is deliberate and stated on the wire contract: sharing a
        workspace makes it discoverable from every other shared workspace in its
        repo. A private workspace never appears here.
        """
        target = str(Path(repo_root).resolve())
        shared = [s for s in self._store.load_all() if s.share_token and s.repo_root == target]
        return sorted(shared, key=lambda s: s.updated_at, reverse=True)

    def workspace_states(self) -> list[WorkspaceState]:
        """Every persisted workspace without status reconciliation or Manager setup.

        Host-wide read indexes use this when they need workspace identity to
        annotate one filesystem edge. ``WorkspaceManager.list()`` additionally
        reaches tmux and lifecycle state, which is neither needed nor affordable
        for a path-local catalog update. The store supplies independent values
        from its signature-indexed snapshot.
        """
        return self._store.load_all()

    def resolve_interrupted(self) -> list[str]:
        """Finish every lifecycle verb a dead process left half-done, then stale provisions.

        A create is unwound and a kill completed, each through its workspace's
        own Manager so the repo's cascade applies. Best-effort per workspace:
        one that cannot be finished is logged and the rest still run.

        Returns:
            The workspace ids that were acted on; empty when nothing was left.
        """
        acted: list[str] = []
        for workspace_id, stages in Stages.interrupted():
            try:
                state = self._store.get(workspace_id)
            except WorkspaceNotFound:
                stages.path.unlink(missing_ok=True)
                continue
            try:
                self.get(Path(state.repo_root)).resume_interrupted(workspace_id, stages)
            except GroveError as exc:
                logger.error(
                    "workspace {}: interrupted {} not finished: {}", workspace_id, stages.verb, exc
                )
                continue
            acted.append(workspace_id)
        return acted + self.resolve_interrupted_provisions()

    def resolve_interrupted_provisions(self) -> list[str]:
        """Settle every record a killed provision left claiming it is still building.

        ``PROVISIONING`` is the one status that ends on its own, so a record
        stuck at it after its process died says "wait" forever about a build
        nobody is running. A provision holds ``paths.exclusive_lock`` on its log
        for its whole run, so a free lock here is proof the builder is gone,
        while a build still running in a CLI or TUI keeps its lock and is left
        alone. The record moves to ``FAILED``, which reconciles to OFFLINE and
        offers ``respawn``, the verb that provisions again.

        Returns:
            The ids of the records that were resolved; empty when none were.
        """
        resolved: list[str] = []
        for state in self._store.load_all():
            if state.provision_status is not ProvisionStatus.PROVISIONING:
                continue
            log_path = paths.provision_log_path(state.id)
            if paths.lock_is_held(log_path):
                continue
            self._store.save(replace(state, provision_status=ProvisionStatus.FAILED))
            logger.warning(
                "workspace {}: its provision was interrupted (the process that ran it is "
                "gone); marked failed, respawn to provision again",
                state.id,
            )
            resolved.append(state.id)
        return resolved

    def identity(self, repo_root: Path) -> ProjectIdentity | None:
        """The repo's cross-node name, or ``None`` when it has no remote to be named by."""
        key = repo_root.resolve()
        if key not in self._identities:
            repo = GitRepo(key)
            remote = repo.origin_url()
            normalized = ProjectIdentity.normalize_remote(remote) if remote else None
            self._identities[key] = (
                ProjectIdentity(remote=normalized, default_branch=repo.default_branch())
                if normalized
                else None
            )
        return self._identities[key]

    def root_for(self, identity: ProjectIdentity) -> Path:
        """This node's path for ``identity``: the per-node half of a project's name.

        Matches the remote only. A node's clone may track a different default
        branch name than the caller assumed, and the branch a workspace starts
        from is the create request's own business.
        """
        for root in sorted(self.known_roots()):
            found = self.identity(root)
            if found is not None and found.remote == identity.remote:
                return root
        raise ProjectNotOnNode(f"no repository on this node has remote {identity.remote}")

    def known_identities(self) -> list[ProjectIdentity]:
        """Every project this node can create in, by its cross-node name."""
        found = (self.identity(root) for root in sorted(self.known_roots()))
        return [identity for identity in found if identity is not None]

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
        for raw in self._projects:
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
