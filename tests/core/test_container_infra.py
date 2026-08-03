"""Infrastructure scopes + prebuild at project registration.

No real `docker` or `devcontainer` binary is ever invoked: the docker
boundary is a small in-memory fake (:class:`_FakeDocker`) and the
devcontainer-CLI boundary used by the concurrency test is a fake `build()`
that just counts calls and flips the fake docker's "image exists" state —
exactly the seam `ProjectInfra.ensure_image` is written against
(`DockerInfraBoundary` / `DevcontainerCli`), so no subprocess module needs
patching here.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest

from grove.core.container_infra import (
    GroveLabels,
    InfrastructureReconciler,
    LabeledObject,
    ProjectBuildCoordinator,
    ProjectInfra,
    WorkspaceInfra,
    slugify_project,
)
from grove.core.container_runtime import ContainerRuntimeState, InfrastructureScope
from grove.core.errors import ContainerError

# ─── fakes ───────────────────────────────────────────────────────────────


class _FakeDocker:
    """In-memory stand-in for :class:`DockerInfraBoundary`."""

    def __init__(self) -> None:
        self.built_tags: set[str] = set()
        self.volumes: dict[str, dict[str, str]] = {}
        self.removed_volumes: list[str] = []
        self.objects: dict[str, dict[str, dict[str, str]]] = {"container": {}, "volume": {}}

    def image_exists(self, tag: str) -> bool:
        return tag in self.built_tags

    def volume_ensure(self, name: str, *, labels: Mapping[str, str]) -> None:
        self.volumes[name] = dict(labels)
        self.objects["volume"][name] = dict(labels)

    def volume_remove(self, name: str) -> None:
        self.removed_volumes.append(name)
        self.volumes.pop(name, None)
        self.objects["volume"].pop(name, None)

    def register(self, kind: str, name: str, labels: Mapping[str, str]) -> None:
        """Test helper: seed a labeled container/volume as if docker created it."""
        self.objects[kind][name] = dict(labels)

    def list_labeled(self, kind: str, *, filters: Sequence[str]) -> list[LabeledObject]:
        wanted: dict[str, str] = {}
        it = iter(filters)
        for flag in it:
            if flag == "--filter":
                raw = next(it)
                _, kv = raw.split("=", 1)
                key, value = kv.split("=", 1)
                wanted[key] = value
        matches: list[LabeledObject] = []
        for name, labels in self.objects.get(kind, {}).items():
            if all(labels.get(k) == v for k, v in wanted.items()):
                # The real boundary reads the workspace label off the object;
                # a fake that returned bare names would let a name-based
                # discriminator pass again.
                matches.append(
                    LabeledObject(
                        name=name,
                        workspace_id=labels.get(ContainerRuntimeState.WORKSPACE_LABEL_KEY, ""),
                    )
                )
        return matches


class _FakeDevcontainerCli:
    """Counts `build()` calls; `read_configuration` is unused (default-config path)."""

    def __init__(self, *, docker: _FakeDocker, delay: float = 0.05) -> None:
        self._docker = docker
        self._delay = delay
        self.build_calls = 0

    def build(
        self, workspace_folder: Path, *, image_name: str = "", **_: object
    ) -> SimpleNamespace:
        self.build_calls += 1
        time.sleep(self._delay)
        self._docker.built_tags.add(image_name)
        return SimpleNamespace(outcome="success", image_name=[image_name])


def _project_infra(tmp_path: Path, docker: _FakeDocker, *, delay: float = 0.0) -> ProjectInfra:
    repo_root = tmp_path / "acme-widgets"
    repo_root.mkdir(exist_ok=True)
    cli = _FakeDevcontainerCli(docker=docker, delay=delay)
    return ProjectInfra(repo_root=repo_root, cli=cli, docker=docker)  # type: ignore[arg-type]


# ─── naming + labels ─────────────────────────────────────────────────────


def test_slugify_project_is_docker_tag_safe() -> None:
    assert slugify_project(Path("/repos/Acme Widgets!!")) == "acme-widgets"


def test_project_labels_never_carry_a_workspace_label(tmp_path: Path) -> None:
    """The structural half of the teardown-cannot-match invariant."""
    infra = _project_infra(tmp_path, _FakeDocker())
    labels = infra.labels.as_dict()

    assert labels[ContainerRuntimeState.MANAGED_LABEL_KEY] == "1"
    assert labels[ContainerRuntimeState.SCOPE_LABEL_KEY] == InfrastructureScope.PROJECT.value
    assert ContainerRuntimeState.WORKSPACE_LABEL_KEY not in labels


def test_workspace_scoped_labels_require_a_workspace_id() -> None:
    with pytest.raises(ContainerError):
        GroveLabels(scope=InfrastructureScope.WORKSPACE, project_slug="acme")


def test_workspace_teardown_filter_cannot_match_project_objects(tmp_path: Path) -> None:
    """End-to-end proof: seed BOTH a project object and a workspace object,
    query with the exact filter a workspace `kill` uses, and assert it finds
    only its own workspace's object — the project object is structurally
    unreachable because it never set `grove.workspace` in the first place."""
    docker = _FakeDocker()
    project = _project_infra(tmp_path, docker)
    ws = WorkspaceInfra(workspace_id="ws-1", project_slug=project.project_slug)
    other_ws = WorkspaceInfra(workspace_id="ws-2", project_slug=project.project_slug)

    docker.register("container", "grove-image-cache-object", project.labels.as_dict())
    # Docker-random names, because that is what the devcontainer CLI produces —
    # nothing here may depend on a name Grove could recognise.
    docker.register("container", "ws-1-container", ws.labels.as_dict())
    docker.register("container", "ws-2-container", other_ws.labels.as_dict())

    found = [obj.name for obj in docker.list_labeled("container", filters=ws.teardown_filter())]

    assert found == ["ws-1-container"]
    assert "grove-image-cache-object" not in found
    assert "ws-2-container" not in found


def test_teardown_filter_matches_the_labels_the_create_path_actually_stamps(
    tmp_path: Path,
) -> None:
    """The invariant, against a REAL create-path label set — not an infra one.

    The version above seeds its workspace object from `WorkspaceInfra.labels`,
    so it proved a property of objects this module constructs. Every container
    a user actually has is stamped by `ContainerRuntimeState.labels_for` on the
    provision path; that producer must emit `grove.scope` too, or the teardown
    filter (which requires it) matches nothing that actually exists while its
    own test stays green. Registering the real label set is what makes this
    test able to catch that.
    """
    docker = _FakeDocker()
    project = _project_infra(tmp_path, docker)
    created = ContainerRuntimeState.labels_for("ws-1", project_slug=project.project_slug)
    docker.register("container", "ws-1-container", created)
    docker.register("container", "grove-image-cache-object", project.labels.as_dict())

    def names(filters: list[str]) -> list[str]:
        return [obj.name for obj in docker.list_labeled("container", filters=filters)]

    assert names(GroveLabels.workspace_teardown_filter("ws-1")) == ["ws-1-container"]
    # The identity path's own enumeration agrees with the lifetime path's: same
    # labels, same match, so a container can never be visible to one and not the
    # other.
    identity = ContainerRuntimeState(id_labels=created)
    assert names(identity.filters()) == ["ws-1-container"]
    # And a different workspace's filter still matches nothing of ws-1's.
    assert names(GroveLabels.workspace_teardown_filter("ws-2")) == []


# ─── prebuild + concurrency ───────────────────────────────────────────────


def test_ensure_image_builds_once_when_cold(tmp_path: Path) -> None:
    docker = _FakeDocker()
    infra = _project_infra(tmp_path, docker)

    result = asyncio.run(infra.ensure_image())

    assert result.built is True
    assert docker.image_exists(result.tag)


def test_ensure_image_skips_build_when_warm(tmp_path: Path) -> None:
    docker = _FakeDocker()
    infra = _project_infra(tmp_path, docker)
    asyncio.run(infra.ensure_image())  # cold build

    result = asyncio.run(infra.ensure_image())  # warm

    assert result.built is False


def test_two_concurrent_creates_produce_one_build(tmp_path: Path) -> None:
    """The per-project asyncio.Lock: two simultaneous `ensure_image` calls
    against the SAME project must invoke `devcontainer build` exactly once —
    the second waits and reuses what the first built."""
    docker = _FakeDocker()
    repo_root = tmp_path / "acme-widgets"
    repo_root.mkdir()
    cli = _FakeDevcontainerCli(docker=docker, delay=0.1)
    infra_a = ProjectInfra(repo_root=repo_root, cli=cli, docker=docker)  # type: ignore[arg-type]
    infra_b = ProjectInfra(repo_root=repo_root, cli=cli, docker=docker)  # type: ignore[arg-type]
    assert infra_a.project_slug == infra_b.project_slug

    async def _race() -> tuple[object, object]:
        return await asyncio.gather(infra_a.ensure_image(), infra_b.ensure_image())

    result_a, result_b = asyncio.run(_race())

    assert cli.build_calls == 1
    assert {result_a.built, result_b.built} == {True, False}  # type: ignore[attr-defined]

    ProjectBuildCoordinator._locks.pop(infra_a.project_slug, None)  # test hygiene


# ─── cache volumes: project-scoped, survive a workspace kill ──────────────


# ─── orphan sweep ──────────────────────────────────────────────────────────


def test_orphan_sweep_never_reports_a_live_workspace(tmp_path: Path) -> None:
    """The failure that would have destroyed running work.

    Containers carry docker-random names — the devcontainer CLI names them — so
    the old `grove-ws-<id>` parse yielded `""` for every real one, and
    `"" not in live` is True: EVERY container was reported as an orphan,
    including live ones. A verb onto that would have handed a user their own
    running fleet labelled as leaks.
    """
    docker = _FakeDocker()
    infra = _project_infra(tmp_path, docker)
    live = WorkspaceInfra(workspace_id="ws-live", project_slug=infra.project_slug)
    dead = WorkspaceInfra(workspace_id="ws-dead", project_slug=infra.project_slug)
    docker.register("container", "mystifying_austin", live.labels.as_dict())
    docker.register("container", "quirky_franklin", dead.labels.as_dict())

    report = InfrastructureReconciler(docker=docker).orphan_sweep(  # type: ignore[arg-type]
        live_workspace_ids=["ws-live"]
    )

    assert report.containers == ("quirky_franklin",)
    assert "mystifying_austin" not in report.containers
    # Report-only: nothing was actually removed.
    assert docker.removed_volumes == []


def test_orphan_sweep_reports_a_workspace_volume_by_its_label(tmp_path: Path) -> None:
    """The volume arm, which fails in the OPPOSITE direction from the container one.

    `_is_workspace_volume` must not require a `grove-ws-` prefix, since no real
    volume carries one — a prefix check would report nothing whatever leaked.
    """
    docker = _FakeDocker()
    infra = _project_infra(tmp_path, docker)
    dead = WorkspaceInfra(workspace_id="ws-dead", project_slug=infra.project_slug)
    docker.register("volume", "dind-var-lib-docker-8f2a", dead.labels.as_dict())

    report = InfrastructureReconciler(docker=docker).orphan_sweep(  # type: ignore[arg-type]
        live_workspace_ids=["ws-live"]
    )

    assert report.volumes == ("dind-var-lib-docker-8f2a",)


def test_a_project_scoped_object_is_excluded_by_construction(tmp_path: Path) -> None:
    """Not by a scope check — by the label it cannot carry.

    `GroveLabels` makes a PROJECT-scoped set with `grove.workspace` impossible
    to build, so the truthiness test excludes it and no prefix rule is needed.
    That is why the label rewrite is the SMALLER change: a name-based sweep
    needs a new rule for every scoped object anyone ever adds, and a
    project-scoped VS Code volume is exactly such a case.
    """
    docker = _FakeDocker()
    infra = _project_infra(tmp_path, docker)
    assert ContainerRuntimeState.WORKSPACE_LABEL_KEY not in infra.labels.as_dict()
    docker.register("volume", f"grove-cache-{infra.project_slug}-uv", infra.labels.as_dict())
    docker.register("volume", f"devc-{infra.project_slug}-vscode-server", infra.labels.as_dict())

    report = InfrastructureReconciler(docker=docker).orphan_sweep(  # type: ignore[arg-type]
        live_workspace_ids=[]
    )

    assert report.volumes == ()
    assert report.is_empty
