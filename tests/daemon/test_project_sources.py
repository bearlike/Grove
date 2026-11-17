"""A project added to the user config reaches the daemon without a restart."""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import pytest

from grove.core import paths
from grove.core.activity import ActivityService
from grove.core.config import GroveConfig, load_config
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import ConfigError
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.daemon._project_sources import _ProjectSources


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    return path.resolve()


def _registry(tmp_path: Path) -> RepoRegistry:
    return RepoRegistry(cfg=GroveConfig(), store=JsonWorkspaceStore(path=tmp_path / "state.json"))


def test_reload_projects_changes_what_the_registry_lists(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    repo = _repo(tmp_path / "fresh")

    assert registry.known_projects() == []
    assert registry.reload_projects([str(repo)]) is True
    assert [p.cwd for p in registry.known_projects()] == [repo]
    assert repo in registry.known_roots()
    # The same list again is not a change, so no propagation would fire.
    assert registry.reload_projects([str(repo)]) is False


@pytest.mark.asyncio
async def test_writing_the_user_config_reloads_projects_and_notifies(tmp_path: Path) -> None:
    """Drives the real native watcher against a real file write, end to end."""
    config = tmp_path / "config" / "config.json"
    repo = _repo(tmp_path / "added")
    registry = _registry(tmp_path)
    notified = asyncio.Event()

    async def on_changed() -> None:
        notified.set()

    def loader() -> GroveConfig:
        return GroveConfig.model_validate(json.loads(config.read_text(encoding="utf-8")))

    # Neither the file nor its directory exists yet: the first `add-project` on
    # a fresh host creates both, and that creation must be observed too.
    sources = _ProjectSources(registry, on_changed, config_path=config, loader=loader)
    await sources.start()
    try:
        config.write_text(json.dumps({"projects": [str(repo)]}), encoding="utf-8")
        await asyncio.wait_for(notified.wait(), timeout=5)
        assert [p.cwd for p in registry.known_projects()] == [repo]
    finally:
        await sources.close()


@pytest.mark.asyncio
async def test_an_unreadable_config_keeps_the_last_good_projects(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    repo = _repo(tmp_path / "kept")
    registry.reload_projects([str(repo)])
    calls: list[None] = []

    async def on_changed() -> None:
        calls.append(None)

    def broken() -> GroveConfig:
        raise ConfigError("Invalid JSON")

    sources = _ProjectSources(registry, on_changed, config_path=tmp_path / "c.json", loader=broken)
    sources._on_batch(None)
    assert sources._task is not None
    await sources._task

    assert [p.cwd for p in registry.known_projects()] == [repo]
    assert calls == []


@pytest.mark.asyncio
async def test_default_loader_reads_the_user_config(tmp_state_dir: Path, tmp_path: Path) -> None:
    """The daemon's wiring uses the real cascade, not a stand-in."""
    del tmp_state_dir
    repo = _repo(tmp_path / "via-cascade")
    config = paths.user_config_path()
    config.write_text(json.dumps({"projects": [str(repo)]}), encoding="utf-8")
    assert load_config(repo_root=None).projects == [str(repo)]

    registry = _registry(tmp_path)
    notified = asyncio.Event()

    async def on_changed() -> None:
        notified.set()

    sources = _ProjectSources(registry, on_changed)
    sources._on_batch(None)
    await asyncio.wait_for(notified.wait(), timeout=5)
    assert [p.cwd for p in registry.known_projects()] == [repo]
    await sources.close()


@pytest.mark.asyncio
async def test_an_edit_before_the_watch_armed_is_still_applied(tmp_path: Path) -> None:
    """The daemon arms this watch after boot; an `add-project` in that gap has no edge."""
    config = tmp_path / "config.json"
    repo = _repo(tmp_path / "early")
    config.write_text(json.dumps({"projects": [str(repo)]}), encoding="utf-8")
    registry = _registry(tmp_path)
    notified = asyncio.Event()

    async def on_changed() -> None:
        notified.set()

    def loader() -> GroveConfig:
        return GroveConfig.model_validate(json.loads(config.read_text(encoding="utf-8")))

    sources = _ProjectSources(registry, on_changed, config_path=config, loader=loader)
    await sources.start()
    try:
        await asyncio.wait_for(notified.wait(), timeout=5)
        assert [p.cwd for p in registry.known_projects()] == [repo]
    finally:
        await sources.close()


@pytest.mark.asyncio
async def test_a_slow_propagation_never_delays_the_next_reload(tmp_path: Path) -> None:
    """Measured live: an edit behind a seconds-long reconcile waited for it."""
    first = _repo(tmp_path / "first")
    second = _repo(tmp_path / "second")
    registry = _registry(tmp_path)
    current: list[str] = [str(first)]
    release = asyncio.Event()

    async def blocked_propagation() -> None:
        await release.wait()

    sources = _ProjectSources(
        registry,
        blocked_propagation,
        config_path=tmp_path / "c.json",
        loader=lambda: GroveConfig(projects=list(current)),
    )
    sources._on_batch(None)
    await asyncio.wait_for(_until(lambda: first in registry.known_roots()), timeout=5)

    current.append(str(second))
    sources._on_batch(None)  # while the first propagation is still blocked
    await asyncio.wait_for(_until(lambda: second in registry.known_roots()), timeout=5)

    release.set()
    await sources.close()


async def _until(predicate: Callable[[], bool]) -> None:
    while not predicate():
        await asyncio.sleep(0.01)


def test_regroup_adds_and_drops_empty_projects_without_reading_the_fleet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept = _repo(tmp_path / "kept")
    added = _repo(tmp_path / "added")
    registry = _registry(tmp_path)
    registry.reload_projects([str(kept)])
    service = ActivityService(registry=registry)
    service.bootstrap()
    assert [g.cwd for g in service.snapshot().projects] == [str(kept)]

    def no_fleet_read(*_args: object) -> NoReturn:
        raise AssertionError("regroup must not re-read workspaces")

    monkeypatch.setattr(ActivityService, "_collect_snapshot", no_fleet_read)
    registry.reload_projects([str(added)])
    snapshot, cursor = service.regroup_projects()

    assert [g.cwd for g in snapshot.projects] == [str(added)]
    assert snapshot.projects[0].workspaces == ()
    assert service.snapshot_with_cursor() == (snapshot, cursor)


def test_regroup_keeps_rows_where_they_are(tmp_path: Path, fake_tmux: object) -> None:
    del fake_tmux
    repo = _repo(tmp_path / "busy")
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@x", "commit", "--allow-empty", "-qm", "i"],
        cwd=repo,
        check=True,
    )
    registry = RepoRegistry(
        cfg=GroveConfig.model_validate(
            {
                "worktree": {"root_template": str(tmp_path / "trees")},
                "tmux": {"session_prefix": "t-"},
            }
        ),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
    )
    registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="row"))
    service = ActivityService(registry=registry)
    before = service.bootstrap()
    (row,) = (w for g in before.projects for w in g.workspaces)

    registry.reload_projects([str(_repo(tmp_path / "new"))])
    snapshot, _ = service.regroup_projects()

    by_cwd = {g.cwd: g for g in snapshot.projects}
    assert by_cwd[str(repo)].workspaces == (row,)
    assert by_cwd[str((tmp_path / "new").resolve())].workspaces == ()
