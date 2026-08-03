"""The suite's offline promise, asserted instead of trusted.

`tests/conftest.py::_offline_container_runtime` says "no test may require a
devcontainer CLI, a docker daemon, or an image pull". That was prose, and prose
does not fail: the fixture patched the two probes the CREATE path uses, while
the daemon's `on_project_registered` hook quietly shelled out to a real
`devcontainer build` on every repo registration — six images built on the
developer's host, and one 7-test module taking 107 seconds.

So this module spies the actual process boundary and asserts nothing named
`docker` or `devcontainer` is ever spawned. A new door into the container
runtime fails HERE, at a test whose whole subject is the guarantee, rather than
silently costing minutes and disk on someone's machine.

**Scope, because the guarantee is narrower than its name.** The spy patches
`subprocess` in THIS process, so it sees only doors this process opens. A test
that spawns a daemon SUBPROCESS (`LocalTransport`, i.e.
`tests/client/test_client_http.py`) is invisible here: the daemon is a separate
interpreter that loads no conftest and honors no monkeypatch, and the argv the
spy records is `grove daemon serve`, never the `devcontainer up` its child goes
on to run. That blind spot really fired — every workspace that file created was
a real devcontainer. **Config, not patching, is the only lever that crosses a
process boundary**, so such a test must pin `container.enabled=false` in the
config the subprocess reads. Extending the spy would not help; nothing in this
process ever sees the grandchild.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.container_infra import ProjectInfra
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Runtime
from tests.conftest import FakeCli, FakePreflight, FakeTmux

#: Binaries no test may spawn. `git` and `tmux` are absent on purpose — the
#: suite drives real git, and tmux is faked at its own module seam.
_FORBIDDEN = ("docker", "devcontainer")


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[list[str]]]:
    """Record every argv the process boundary sees, passing the rest through.

    A passthrough spy, not a blanket stub: these tests drive REAL git, so
    replacing `subprocess.run` outright would break the very code paths whose
    side effects we want to observe. (`tests/CLAUDE.md` documents why
    patching `<module>.subprocess.run` is process-wide rather than scoped.)
    """
    calls: list[list[str]] = []
    real_run = subprocess.run
    real_popen = subprocess.Popen

    def _run(argv: Sequence[str] | str, *args: object, **kwargs: object) -> object:
        if not isinstance(argv, str):
            calls.append([str(token) for token in argv])
        return real_run(argv, *args, **kwargs)  # type: ignore[arg-type,call-overload]

    def _popen(argv: Sequence[str] | str, *args: object, **kwargs: object) -> object:
        if not isinstance(argv, str):
            calls.append([str(token) for token in argv])
        return real_popen(argv, *args, **kwargs)  # type: ignore[arg-type,call-overload]

    monkeypatch.setattr(subprocess, "run", _run)
    monkeypatch.setattr(subprocess, "Popen", _popen)
    yield calls


def _assert_offline(calls: list[list[str]]) -> None:
    offenders = [argv for argv in calls if argv and Path(argv[0]).name in _FORBIDDEN]
    assert offenders == [], (
        "a container binary was spawned; the offline-container fixture no longer "
        f"covers every door into the runtime: {offenders}"
    )


def _cfg(tmp_path: Path) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "off/"},
            "tmux": {"session_prefix": "off-"},
            # Containers ENABLED — the point is that an enabled runtime still
            # spawns nothing, not that a disabled one doesn't.
            "container": {"enabled": True},
            "init_script": {"enabled": False},
            "agents": [{"name": "claude", "command": "claude", "kind": "claude_code"}],
        }
    )


async def test_registering_a_project_spawns_no_container_binary(
    tmp_repo: Path, tmp_path: Path, spawned: list[list[str]]
) -> None:
    """The door that was actually open: `RepoRegistry.on_project_registered`.

    Wires the REAL `ProjectInfra.registration_hook` the daemon wires, so this
    fails if the fixture stops covering the prebuild path.

    **`async def` is load-bearing, not stylistic.** `registration_hook` bails out
    via `asyncio.get_running_loop()` when there is no loop, so a synchronous
    version of this test passes no matter what the fixture does — it never
    reaches the prebuild at all. That is exactly why the leak lived in the
    daemon tests (`asyncio_mode = "auto"`, so they run in a loop) and nowhere
    else, and why a sync test would be a guarantee that guarantees nothing.
    """
    cfg = _cfg(tmp_path)
    registry = RepoRegistry(
        cfg=cfg,
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        on_project_registered=ProjectInfra.registration_hook(cfg),
    )

    registry.get(tmp_repo)
    # The hook is fire-and-forget, so the build rides a task the loop has not
    # run yet — asserting now would race it into a pass. Drain first.
    pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    if pending:
        await asyncio.wait(pending, timeout=30)

    _assert_offline(spawned)


def test_creating_a_workspace_spawns_no_container_binary(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux, spawned: list[list[str]]
) -> None:
    """The create path, end to end, with containers enabled and no DI at all.

    No fake CLI and no fake preflight are injected — production classes
    throughout — so the only thing keeping this offline is the autouse fixture.
    """
    del fake_tmux
    manager = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
    )

    manager.create(CreateWorkspaceRequest(agent_name="claude", title="offline"))

    # Real git DID run — proof the spy passes through rather than stubbing
    # everything into silence, which would make the assertion vacuous.
    assert any(argv and Path(argv[0]).name == "git" for argv in spawned)
    _assert_offline(spawned)


def test_provisioning_a_container_spawns_no_container_binary(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux, spawned: list[list[str]]
) -> None:
    """The PROVISION path is its own door, distinct from the create decision.

    The two tests above both stop short of a real provision — one never creates
    a workspace, the other falls back to the host at arm 4 — so neither would
    catch the provisioner compiling a static tmux with a real `docker build`
    (cached in the developer's own state dir), nor the in-container tmux probe
    shelling out to a real `devcontainer exec` if the fake has no `exec` at
    all. Reaching the provision arm needs the fake CLI + fake preflight, which
    is exactly why the door is not covered by "no DI at all".
    """
    del fake_tmux
    manager = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=FakeCli(),
        preflight=FakePreflight(),
    )

    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="provisioned"))

    # The provision really happened — otherwise this asserts nothing, the way
    # the two tests above could not have caught it.
    assert state.runtime is Runtime.CONTAINER
    assert state.container is not None and state.container.provisioned
    _assert_offline(spawned)


def test_reading_a_detached_container_workspace_spawns_no_container_binary(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux, spawned: list[list[str]]
) -> None:
    """Reading and STEERING an in-container pane is its own door.

    `capture-pane`/`send-keys` for a container workspace are `docker exec`s, and
    reconciliation reaches for one whenever the host session is gone — a READ
    path, and read paths are the class easiest to forget to add to a
    neutralizer list. It is neutralized because every one of those reads goes
    through `DockerCli.read_result`, and steering goes through the tmux module
    seam the fixture replaces; a future shortcut around either would surface
    here as a real subprocess rather than as minutes on somebody's machine.
    """
    manager = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        # An image that ships tmux, so the workspace really gets an in-container
        # pane — the fake's default is the measured reality that none do, and
        # against that default this test would take the no-tmux fallback arm
        # and assert nothing about the door it exists to guard.
        devcontainer_cli=FakeCli(image_has_tmux=True),
        preflight=FakePreflight(),
    )
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="detached"))
    assert state.container is not None and state.container.provisioned
    assert state.container.tmux_command, "the container-pane path must actually be reachable"
    fake_tmux.sessions.discard(state.tmux_session)  # the host viewport is gone

    manager.list()
    manager.peek(state.id)
    manager.agent_exit(manager.get(state.id))

    _assert_offline(spawned)


def test_the_spy_would_actually_catch_a_container_binary(spawned: list[list[str]]) -> None:
    """Negative control: prove `_assert_offline` fails when it should.

    Without this, a spy that silently recorded nothing would make every
    assertion above pass for the wrong reason.
    """
    del spawned
    with pytest.raises(AssertionError, match="container binary was spawned"):
        _assert_offline([["/usr/bin/docker", "build", "."]])
    with pytest.raises(AssertionError, match="container binary was spawned"):
        _assert_offline([["devcontainer", "up"]])
    # …and that it tolerates the binaries the suite legitimately drives.
    _assert_offline([["git", "status"], ["/usr/bin/tmux", "ls"]])
