"""Daemon ↔ issue-ops lifespan wiring: the status publisher and the assignee poll.

The publisher binds to the live ``ActivityService`` bus and closes at daemon
shutdown. The assignee poller instead receives the registry's manager-materialization
subscription; it drives its own tracker timer and consumes no activity deltas.
Publisher coalescing/render behavior is covered in
``tests/core/issueops/test_publisher.py``; these tests pin only daemon wiring.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from fastapi.testclient import TestClient

from grove.core.activity import DashboardDelta
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config


class _CapturingPublisher:
    """Records the lifecycle wiring ``build_app`` performs (the publisher's seam).

    ``bind`` subscribes ``observe`` to the bus exactly like the real publisher, so
    a captured delta proves the lifespan wired it to the live activity stream;
    ``close`` records shutdown and unsubscribes.
    """

    def __init__(self) -> None:
        self.bound = False
        self.closed = False
        self.deltas: list[DashboardDelta] = []
        self._unsub: Callable[[], None] | None = None

    def bind(
        self, subscribe: Callable[[Callable[[DashboardDelta], None]], Callable[[], None]]
    ) -> None:
        self.bound = True
        self._unsub = subscribe(self.observe)

    def observe(self, delta: DashboardDelta) -> None:
        self.deltas.append(delta)

    def close(self) -> None:
        self.closed = True
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def publish(self, event: object, manager: object) -> None:  # StatusPublisher seam
        del event, manager


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "t@g.l"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--no-verify"], cwd=path, check=True, capture_output=True
    )
    return path.resolve()


def test_lifespan_binds_and_closes_the_injected_status_publisher(
    fake_tmux: FakeTmux,
    tmp_state_dir: Path,
    tmp_path: Path,
) -> None:
    del fake_tmux
    pub = _CapturingPublisher()
    store = JsonWorkspaceStore()
    app = build_app(cfg=daemon_test_config(), store=store, status_publisher=pub)  # type: ignore[arg-type]

    with TestClient(app) as client:
        del client
        registry = app.state.registry
        repo = _init_repo(tmp_path / "repo")
        registry.get(repo).create(CreateWorkspaceRequest(agent_name="shell", title="mirror"))

        assert pub.bound  # the lifespan bound it to the live activity bus
        # The manager event crosses the ActivityService bridge immediately;
        # publication does not wait for the removed periodic poll trigger.
        assert pub.deltas and pub.deltas[-1].kind == "workspace_changed"
        assert pub.deltas[-1].detail["event"] == "created"
        assert app.state.status_publisher is pub

    assert pub.closed  # closed on shutdown


class _CapturingPoller:
    """Records registry-subscription and lifecycle wiring for the own-timer poller."""

    def __init__(self) -> None:
        self.bound = False
        self.closed = False
        self.managers: list[tuple[Path, object]] = []
        self._unsub: Callable[[], None] | None = None

    def bind(
        self, subscribe: Callable[[Callable[[Path, object], None]], Callable[[], None]]
    ) -> None:
        self.bound = True
        self._unsub = subscribe(self._observe_manager)

    def _observe_manager(self, root: Path, manager: object) -> None:
        self.managers.append((root, manager))

    def close(self) -> None:
        self.closed = True
        if self._unsub is not None:
            self._unsub()
            self._unsub = None


def test_lifespan_binds_and_closes_the_injected_assignee_poller(
    fake_tmux: FakeTmux,
    tmp_state_dir: Path,
    tmp_path: Path,
) -> None:
    del fake_tmux, tmp_state_dir
    poller = _CapturingPoller()
    app = build_app(
        cfg=daemon_test_config(),
        store=JsonWorkspaceStore(),
        assignee_poller=poller,  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        del client
        repo = _init_repo(tmp_path / "repo")
        manager = app.state.registry.get(repo)

        assert poller.bound
        assert poller.managers == [(repo, manager)]
        assert app.state.assignee_poller is poller
    assert poller.closed


def test_no_poller_is_built_when_both_halves_are_off(
    fake_tmux: FakeTmux,
    tmp_state_dir: Path,
) -> None:
    """The default daemon does no tracker polling at all — both halves are opt-in."""
    del fake_tmux, tmp_state_dir
    app = build_app(cfg=daemon_test_config(), store=JsonWorkspaceStore())
    with TestClient(app) as client:
        del client
        assert getattr(app.state, "assignee_poller", None) is None
