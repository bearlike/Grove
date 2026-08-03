"""Daemon ↔ issue-ops lifespan wiring: the status publisher and the assignee poll.

The publisher is the activity bus's third subscriber (alongside the SSE hub and
the notification broker). This pins the daemon's job — that ``build_app`` binds
the injected publisher to the live ``ActivityService`` bus on startup and closes
it on shutdown — via the same ``build_app(status_publisher=...)`` injection seam
the broker uses (``test_notifications_wiring.py``). The publisher's own
coalescing/render behavior is covered in ``tests/core/issueops/test_publisher.py``;
here we only assert the wiring, with a capturing fake standing in for the real
publisher.
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
        activity = app.state.activity
        repo = _init_repo(tmp_path / "repo")
        registry.get(repo).create(CreateWorkspaceRequest(agent_name="shell", title="mirror"))
        activity.poll_once()  # a fresh workspace is a change → a delta the publisher observes

        assert pub.bound  # the lifespan bound it to the live activity bus
        assert pub.deltas  # …and a real delta reached its observe callback
        assert app.state.status_publisher is pub

    assert pub.closed  # closed on shutdown


class _CapturingPoller:
    """Records the assignee poller's own lifecycle wiring.

    Deliberately NOT a bus subscriber: the poller drives its own timer against
    the trackers and consumes no deltas, which is why the lifespan does not
    ``audience.join()`` for it.
    """

    def __init__(self) -> None:
        self.bound = False
        self.closed = False

    def bind(self) -> None:
        self.bound = True

    def close(self) -> None:
        self.closed = True


def test_lifespan_binds_and_closes_the_injected_assignee_poller(
    fake_tmux: FakeTmux,
    tmp_state_dir: Path,
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
        assert poller.bound
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
