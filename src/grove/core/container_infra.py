"""Infrastructure scopes + prebuild-at-registration.

**Two lifetimes, one label.** `grove.scope = project | workspace` makes GC
decidable. PROJECT-scoped objects carry NO `grove.workspace` label — that is
what makes it *structural*, not remembered-by-convention, that a
workspace-scoped teardown filter (`grove.scope=workspace AND
grove.workspace=<id>`) cannot match a project object: the label the filter
requires is simply absent from the project object's label set.

**Prebuild warms docker's layer cache, and that is all it does.** A cold
`devcontainer build` with features is minutes; running it at project
registration (guarded by a per-project lock, so concurrent creates share one
build) leaves those layers resident, and the create path's own `up` is then
fast. The Grove default config
(:class:`~grove.core.devcontainer.DefaultDevcontainerConfig`) goes through the
identical machinery: a project with no ``.devcontainer/`` still prebuilds, it
just resolves a different source config.

Be precise about the scope, because the machinery reads bigger than it is:
**nothing downstream consumes the prebuilt image by name.** The create path
does not pass ``--cache-from``, does not request the tag, and mounts no
dependency-cache volumes; the tag exists so this module can ask "already
warm?" and skip. Wiring the tag into provisioning is a filed follow-up. Three
pieces that pretended otherwise are gone: per-project dependency-cache volumes
(created at registration, mounted by nothing, and pruned by a `grove cache
prune` that therefore had nothing to clean up), and a project→workspace image
reconcile keyed on a ``config_hash`` no producer emitted — see
:class:`InfrastructureReconciler` for that one.

**Class design.** One owning class per lifetime (:class:`ProjectInfra`,
:class:`WorkspaceInfra`) — each holds its own naming, its own label set, and an
idempotent ``ensure()``. The label VOCABULARY is not defined here: the keys,
:class:`~grove.core.container_runtime.InfrastructureScope`, and the
filter-argv shape all live on
:class:`~grove.core.container_runtime.ContainerRuntimeState`, and
:class:`GroveLabels` builds its per-scope sets from them. This module and that
one once each carried their own half of the vocabulary — identity there
(managed + workspace), lifetime here (managed + scope + project) — and the
split was not two vocabularies to reconcile but one that nothing stamped in
full: every teardown filter required ``grove.scope`` and no created object
carried it, so the filter matched nothing while its test still passed.

Dependencies flow inward: this module imports ``config``/``devcontainer``/
``container_runtime``/``errors``. Its docker boundary
(:class:`DockerInfraBoundary`) is defined and DI'd here rather than reusing
``container_runtime``'s ``DockerCli``, because image/volume bookkeeping and one
container's lifecycle argv are genuinely different surfaces — the labels are
the part that has to be shared, not the subprocess wrapper.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal, Protocol

from loguru import logger

from grove.core.config import ContainerConfig, GroveConfig
from grove.core.container_runtime import ContainerRuntimeState, InfrastructureScope
from grove.core.devcontainer import (
    DefaultDevcontainerConfig,
    DevcontainerCli,
    DevcontainerConfig,
)
from grove.core.errors import ContainerError

# ─── labels ──────────────────────────────────────────────────────────────
#
# The vocabulary itself lives on `ContainerRuntimeState` — one module owns the
# label KEYS, the scope enum, and the filter-argv shape, so what the create
# path stamps and what a sweep queries cannot drift. This module supplies only
# the per-scope label SETS built from it.


@dataclass(frozen=True, slots=True)
class GroveLabels:
    """The label set for one object, and the filter argv that finds it back.

    Construction enforces the structural invariant the epic hinges on: a
    PROJECT-scoped label set has no ``workspace`` field to set, so it is
    physically impossible to construct one that carries
    ``grove.workspace`` — the workspace-teardown filter is therefore
    guaranteed to never match a project object, not merely conventionally
    unlikely to.
    """

    scope: InfrastructureScope
    project_slug: str
    workspace_id: str = ""

    def __post_init__(self) -> None:
        if self.scope is InfrastructureScope.WORKSPACE and not self.workspace_id:
            raise ContainerError("a WORKSPACE-scoped label set requires a workspace_id")

    def as_dict(self) -> dict[str, str]:
        """The full ``docker ... --label k=v`` set for a created object.

        The WORKSPACE arm delegates to ``ContainerRuntimeState.labels_for`` —
        the same producer the create path stamps its container with — so an
        infrastructure object and the workspace container it belongs to are
        findable by one filter rather than two that happen to agree.
        """
        if self.scope is InfrastructureScope.WORKSPACE:
            return ContainerRuntimeState.labels_for(
                self.workspace_id, project_slug=self.project_slug
            )
        return {
            ContainerRuntimeState.MANAGED_LABEL_KEY: ContainerRuntimeState.MANAGED_LABEL_VALUE,
            ContainerRuntimeState.SCOPE_LABEL_KEY: self.scope.value,
            ContainerRuntimeState.PROJECT_LABEL_KEY: self.project_slug,
        }

    def label_flags(self) -> list[str]:
        """``--label k=v`` argv pairs, in the same shape `docker run`/`volume create` take."""
        flags: list[str] = []
        for key, value in self.as_dict().items():
            flags += ["--label", f"{key}={value}"]
        return flags

    @staticmethod
    def filter_flags(**labels: str) -> list[str]:
        """``--filter label=k=v`` argv pairs — delegated to the one producer."""
        return ContainerRuntimeState.filter_flags(**labels)

    @classmethod
    def workspace_teardown_filter(cls, workspace_id: str) -> list[str]:
        """The exact filter a workspace `kill` uses to find ITS objects.

        Scoped to ``grove.scope=workspace`` in addition to the workspace id —
        belt-and-suspenders on top of the label-absence guarantee above, and
        the shape the "teardown cannot match project objects" test exercises.
        Identical by construction to what ``ContainerRuntimeState.filters()``
        emits for the same workspace, since both read the same keys.
        """
        return cls.filter_flags(
            **{
                ContainerRuntimeState.MANAGED_LABEL_KEY: ContainerRuntimeState.MANAGED_LABEL_VALUE,
                ContainerRuntimeState.SCOPE_LABEL_KEY: InfrastructureScope.WORKSPACE.value,
                ContainerRuntimeState.WORKSPACE_LABEL_KEY: workspace_id,
            }
        )


def slugify_project(repo_root: Path) -> str:
    """A docker-tag-safe project identifier derived from the repo directory name.

    Docker reference components allow only ``[a-z0-9._-]``; anything else
    collapses to ``-``. Pure and deterministic — the same repo path always
    slugs to the same name, which is what lets the image tag and the cache
    volume names key off it without a separate persisted mapping.
    """
    raw = repo_root.resolve().name.lower()
    slug = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-")
    return slug or "project"


# ─── the docker boundary this module needs, DI'd ───────────────────────────

_ObjectKind = Literal["container", "volume", "image"]


@dataclass(frozen=True, slots=True)
class LabeledObject:
    """One Grove-labelled docker object, as an enumeration reports it.

    Carries the workspace LABEL, not only the name, because a name is not an
    identity here. The devcontainer CLI names the
    containers Grove creates, so a real one is ``mystifying_austin`` — nothing
    Grove could recognise, and nothing a prefix rule can be written against.
    The label is stamped by ``ContainerRuntimeState.labels_for`` on every object
    Grove creates, which makes it the only authoritative answer.

    ``workspace_id`` is ``""`` for a PROJECT-scoped object, and that is what
    makes project scope fall out of a workspace-orphan query **by
    construction**: :class:`GroveLabels` cannot build a project label set
    carrying ``grove.workspace`` at all. A name-based sweep has no scope signal
    whatsoever and would need a new prefix rule for every scoped object anyone
    ever adds.
    """

    name: str
    workspace_id: str = ""


class DockerInfraBoundary(Protocol):
    """The narrow docker-CLI surface the prebuild and the orphan sweep need.

    Deliberately NOT `container_runtime`'s `DockerCli` — that boundary is
    shaped around ONE workspace container's lifecycle argv, not image/volume
    bookkeeping. Two narrow protocols beat one that answers both questions.
    """

    def image_exists(self, tag: str) -> bool: ...

    def list_labeled(self, kind: _ObjectKind, *, filters: Sequence[str]) -> list[LabeledObject]: ...


class DockerCliInfraBoundary:
    """The default :class:`DockerInfraBoundary` over the ``docker`` CLI.

    Sibling in posture to `container_runtime`'s `DockerCli`: argv-only,
    ``shell=False``, one narrow error type. Reads (`image_exists`,
    `list_labeled`) never raise — an unreadable engine reads as "not found" /
    "nothing to report", matching `DockerCli.read`'s precedent so a
    prebuild check degrades to "rebuild" rather than crashing registration.
    """

    def __init__(self, *, docker_bin: str = "docker") -> None:
        self._docker_bin = docker_bin

    def image_exists(self, tag: str) -> bool:
        try:
            result = subprocess.run(
                [self._docker_bin, "image", "inspect", tag],
                capture_output=True,
                text=True,
                check=False,
                shell=False,
                timeout=10,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            logger.debug("container_infra: image inspect({}) failed: {}", tag, exc)
            return False
        return result.returncode == 0

    #: The name field each `docker ... ls` spells differently, paired with the
    #: workspace label EVERY enumeration now carries — the identity a name
    #: cannot supply. Tab-separated because a docker object name cannot
    #: contain a tab (`[a-zA-Z0-9][a-zA-Z0-9_.-]*`), so the split is total.
    _NAME_FIELD: ClassVar[Mapping[_ObjectKind, str]] = {
        "container": "{{.Names}}",
        "volume": "{{.Name}}",
        "image": "{{.Repository}}:{{.Tag}}",
    }

    def list_labeled(self, kind: _ObjectKind, *, filters: Sequence[str]) -> list[LabeledObject]:
        subcommand = {
            "container": ["ps", "-a"],
            "volume": ["volume", "ls"],
            "image": ["image", "ls"],
        }[kind]
        # `grove.managed=1` is anchored HERE, not left to each caller's filter
        # set, so the chokepoint invariant ("no enumeration can see a foreign
        # object") holds on the infra read path the same way
        # `ContainerRuntimeState.filters()` holds it on the lifecycle one.
        # Docker ANDs repeated `--filter label=` flags, so a caller re-passing
        # the managed filter is a harmless duplicate rather than a widening.
        anchored = [
            *ContainerRuntimeState.managed_filter(),
            *filters,
        ]
        label = f'{{{{.Label "{ContainerRuntimeState.WORKSPACE_LABEL_KEY}"}}}}'
        fmt = f"{self._NAME_FIELD[kind]}\t{label}"
        argv = [self._docker_bin, *subcommand, *anchored, "--format", fmt]
        try:
            result = subprocess.run(
                argv, capture_output=True, text=True, check=False, shell=False, timeout=15
            )
        except (subprocess.SubprocessError, OSError) as exc:
            logger.debug("container_infra: list_labeled({}) failed: {}", kind, exc)
            return []
        if result.returncode != 0:
            return []
        found: list[LabeledObject] = []
        for line in result.stdout.splitlines():
            name, _, workspace = line.strip().partition("\t")
            if name:
                found.append(LabeledObject(name=name, workspace_id=workspace.strip()))
        return found


# ─── project scope ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ImageEnsureResult:
    """The outcome of :meth:`ProjectInfra.ensure_image` — built or already warm."""

    tag: str
    built: bool
    elapsed_s: float

    config_hash: str
    """Fingerprint of the project's OWN resolved configuration.

    Not comparable with :attr:`ContainerRuntimeState.config_hash`, which
    fingerprints the complete override WITH Grove's overlay folded in — a
    different document, so the two never agree and a comparison reads as
    permanent drift. That comparison is exactly what the cut image-reconcile
    was built on; keep the two hashes on their own sides of the boundary.
    """


class ProjectBuildCoordinator:
    """Per-project build serialization: two concurrent creates run ONE build.

    A process-wide registry of `asyncio.Lock`s keyed by project slug (not an
    instance attribute on `ProjectInfra` — two `ProjectInfra` instances for
    the same project, e.g. one per request, must still serialize on the SAME
    lock). `dict.setdefault` under the GIL is the whole synchronization this
    needs: lock creation itself never awaits, so there is no window for two
    coroutines to each mint a fresh lock for the same key.
    """

    _locks: ClassVar[dict[str, asyncio.Lock]] = {}

    @classmethod
    def lock_for(cls, project_slug: str) -> asyncio.Lock:
        lock = cls._locks.get(project_slug)
        if lock is None:
            lock = cls._locks.setdefault(project_slug, asyncio.Lock())
        return lock


class ProjectInfra:
    """The PROJECT-lifetime object owner: the prebuilt image.

    Warmed at project registration and never by a workspace `kill`, because the
    whole point is that the build is paid once per project rather than once per
    worktree.

    Scope worth stating, because the shape suggests more than it does: the build
    warms **docker's own local layer cache**, and that is the entire mechanism.
    Nothing downstream names the tag — the create path does not pass
    `--cache-from`, does not request the image by name, and mounts no
    dependency-cache volumes. A cold `up` after this is fast because the layers
    are resident, not because Grove handed the CLI anything.
    """

    #: Local image namespace — Grove mints these tags itself; registry
    #: cache-push (`--cache-to type=registry`) is deliberately out of scope, so
    #: this is never pushed anywhere, only used as a locally-unique prefix.
    IMAGE_NAMESPACE: ClassVar[str] = "grove"

    def __init__(
        self,
        *,
        repo_root: Path,
        cli: DevcontainerCli,
        docker: DockerInfraBoundary,
        container_cfg: ContainerConfig | None = None,
    ) -> None:
        self._repo_root = repo_root.resolve()
        self._cli = cli
        self._docker = docker
        self._container_cfg = container_cfg or ContainerConfig()

    @property
    def project_slug(self) -> str:
        return slugify_project(self._repo_root)

    @property
    def labels(self) -> GroveLabels:
        return GroveLabels(scope=InfrastructureScope.PROJECT, project_slug=self.project_slug)

    def image_tag(self, config_hash: str) -> str:
        """The content-addressed tag: `$NAMESPACE/$PROJ-dev:$HASH`.

        Consumed only by this class — it is `devcontainer build --image-name`
        and the key of the "already warm, skip the build" check. The create path
        never names it; see the module docstring.
        """
        return f"{self.IMAGE_NAMESPACE}/{self.project_slug}-dev:{config_hash}"

    def resolve_source_config(self) -> tuple[Path | None, DevcontainerConfig]:
        """The config to build, and the `--config` override (None = the repo's own).

        A repo with a `.devcontainer/` reads its own configuration; a repo
        with none prebuilds the packaged `DefaultDevcontainerConfig`
        instead — the *same* machinery, just a different source document, so
        every repo gets a pre-built default container.
        """
        devcontainer_dir = self._repo_root / ".devcontainer"
        if devcontainer_dir.exists():
            result = self._cli.read_configuration(self._repo_root, include_merged=True)
            return None, result.effective
        default_path = DefaultDevcontainerConfig.path(self._container_cfg)
        return default_path, DefaultDevcontainerConfig.load(self._container_cfg)

    async def ensure_image(self, *, force: bool = False) -> ImageEnsureResult:
        """Idempotently ensure this project's prebuilt image exists; build if not.

        Serialized on :class:`ProjectBuildCoordinator`'s per-project lock — the
        second of two concurrent callers blocks here and then observes the
        tag the first caller just built, so it returns `built=False` without
        ever invoking `devcontainer build` itself. The blocking `devcontainer
        build` subprocess runs off the event loop via `asyncio.to_thread`.
        """
        async with ProjectBuildCoordinator.lock_for(self.project_slug):
            return await asyncio.to_thread(self._ensure_image_sync, force=force)

    def _ensure_image_sync(self, *, force: bool) -> ImageEnsureResult:
        config_path, effective = self.resolve_source_config()
        config_hash = effective.fingerprint()
        tag = self.image_tag(config_hash)
        if not force and self._docker.image_exists(tag):
            logger.debug("container_infra: {} already warm, skipping build", tag)
            return ImageEnsureResult(tag=tag, built=False, elapsed_s=0.0, config_hash=config_hash)
        started = time.monotonic()
        self._cli.build(self._repo_root, image_name=tag, config=config_path)
        elapsed = time.monotonic() - started
        logger.info("container_infra: built {} in {:.1f}s", tag, elapsed)
        return ImageEnsureResult(tag=tag, built=True, elapsed_s=elapsed, config_hash=config_hash)

    @classmethod
    def registration_hook(cls, cfg: GroveConfig) -> Callable[[Path], None]:
        """A ``RepoRegistry.on_project_registered`` callback that warms a project.

        Moving the image build off the per-workspace create path is the whole
        point of prebuilding, so it runs at *first project access* instead — the
        moment the registry mints that repo's Manager.

        Three properties this shape buys, each load-bearing:

        * **Never blocks registration.** The build is scheduled as a task and the
          hook returns immediately; ``get()`` must stay a cache lookup, because
          every request on every surface goes through it.
        * **Never fails registration.** A prebuild is an optimization — the
          create path provisions correctly without it, just colder — so every
          failure is logged and swallowed. A repo whose image cannot build must
          still be listable.
        * **Only where a loop is running.** With no event loop this is a
          synchronous CLI (``grove ls``, ``grove sessions``), where a
          minutes-long build in the middle of a listing is indefensible. The
          warm-up is a daemon-lifetime concern; elsewhere it is skipped, and the
          next create simply pays the build itself.
        """
        if not cfg.container.enabled:
            return lambda _repo_root: None

        # A bare `create_task` result is only weakly referenced by the loop, so a
        # minutes-long build can be garbage-collected mid-flight and cancelled
        # for no visible reason. Holding the task until it completes is what
        # makes fire-and-forget actually fire.
        pending: set[asyncio.Task[None]] = set()

        def _warm(repo_root: Path) -> None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                logger.debug("container_infra: no event loop, skipping prebuild for {}", repo_root)
                return
            infra = cls(
                repo_root=repo_root,
                cli=DevcontainerCli(timeout=cfg.container.up_timeout_seconds),
                docker=DockerCliInfraBoundary(docker_bin=cfg.container.docker_bin),
                container_cfg=cfg.container,
            )
            task = loop.create_task(infra.warm_quietly())
            pending.add(task)
            task.add_done_callback(pending.discard)

        return _warm

    async def warm_quietly(self) -> None:
        """``ensure_image`` with every failure logged and swallowed — the hook's body."""
        try:
            image = await self.ensure_image()
        except Exception as exc:
            logger.warning("container_infra: prebuild for {} failed: {}", self._repo_root, exc)
            return
        logger.info(
            "container_infra: {} warm (image {}{})",
            self.project_slug,
            image.tag,
            f", built in {image.elapsed_s:.1f}s" if image.built else ", already present",
        )


# ─── workspace scope ─────────────────────────────────────────────────────────


class WorkspaceInfra:
    """The WORKSPACE-lifetime LABEL owner — deliberately no naming at all.

    Grove names neither the container nor its volumes: the devcontainer CLI
    names containers, and the volumes that exist are minted by the CLI's own
    features. A naming scheme that reads authoritative and is fiction is worse
    than an absent one, because the next reader uses it — and a name-based
    orphan sweep would, reporting live workspaces as leaks. The label set below
    is the real identity, and the only thing this class legitimately owns.

    The label set — the actual container lifecycle
    (`up`/`exec`/teardown) is `container_runtime`'s `ContainerRuntimeState` +
    `ContainerLifecycle`, not reimplemented here. What this module needs from
    workspace scope is exactly enough to name the objects the orphan sweep
    reasons about, without a second lifetime class inventing its own label
    shape.
    """

    def __init__(self, *, workspace_id: str, project_slug: str) -> None:
        self.workspace_id = workspace_id
        self.project_slug = project_slug

    @property
    def labels(self) -> GroveLabels:
        return GroveLabels(
            scope=InfrastructureScope.WORKSPACE,
            project_slug=self.project_slug,
            workspace_id=self.workspace_id,
        )

    def teardown_filter(self) -> list[str]:
        """The exact `docker ... --filter` argv a workspace `kill` must use."""
        return GroveLabels.workspace_teardown_filter(self.workspace_id)


# ─── orphan sweep ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OrphanReport:
    """Report-only sweep result — never auto-removed (auto-remove races a
    still-initializing workspace; a human pruning is strictly cheaper)."""

    containers: tuple[str, ...] = ()
    volumes: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.containers and not self.volumes


class InfrastructureReconciler:
    """The host-wide orphan sweep: which Grove-labelled objects belong to nothing.

    Report-only by contract — see :meth:`orphan_sweep`. It once also carried a
    project→workspace image reconcile, cut because it was keyed on a
    ``config_hash`` nothing produced: the caller was expected to supply "the tag
    this project should serve", and the only two hashes in the codebase cover
    DIFFERENT documents (`ProjectInfra.resolve_source_config()`'s fingerprint is
    the project's own configuration; `ContainerRuntimeState.config_hash` is the
    complete override WITH Grove's additions folded in). Comparing them would
    have reported permanent drift, so nothing ever called it.
    """

    def __init__(self, *, docker: DockerInfraBoundary) -> None:
        self._docker = docker

    def orphan_sweep(self, *, live_workspace_ids: Iterable[str]) -> OrphanReport:
        """Report every `grove.managed=1` object whose workspace is gone.

        Report-only by contract: nothing here removes anything.

        **Identity is the `grove.workspace` LABEL, never a name.** A
        name-based rule parsing a `grove-ws-<id>` prefix would be worthless —
        the devcontainer CLI names the containers, so a real one is
        `mystifying_austin` — and would fail in OPPOSITE directions for the
        two object kinds: every container would fall to `"" not in live` and
        report as an orphan **including running ones**, while every volume
        would fail the `grove-ws-` test and report as nothing. A report that
        cannot be trusted is worse than an absent one, because acting on it
        destroys running work.

        Project scope needs no check here and gets none. A project-scoped
        object carries no `grove.workspace` at all (`GroveLabels` cannot build
        one that does), so the truthiness test excludes it structurally — the
        same reason a *named* sweep would have needed a new prefix rule for
        every scoped object anyone ever adds.
        """
        live = set(live_workspace_ids)
        managed_filter = ContainerRuntimeState.managed_filter()
        return OrphanReport(
            containers=self._orphans(
                self._docker.list_labeled("container", filters=managed_filter), live
            ),
            volumes=self._orphans(
                self._docker.list_labeled("volume", filters=managed_filter), live
            ),
        )

    @staticmethod
    def _orphans(objects: Iterable[LabeledObject], live: set[str]) -> tuple[str, ...]:
        """Names of workspace-scoped objects whose workspace is not live.

        An object with no workspace label is never an orphan of a workspace —
        it is project-scoped, or something Grove does not own the lifetime of.
        Absence of the label is the exclusion; there is no name rule to get
        wrong.
        """
        return tuple(
            obj.name for obj in objects if obj.workspace_id and obj.workspace_id not in live
        )
