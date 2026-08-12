"""Daemon ↔ trace-forwarder lifespan wiring.

The forwarder is the activity bus's fourth subscriber (after the SSE hub, the
notification broker and the issue-ops publisher). This pins the daemon's whole
job — construct from ``cfg.telemetry``, bind to the live ``ActivityService``
bus, close on shutdown — and the contract that matters most when telemetry is
off: a disabled forwarder must be indistinguishable from an absent one.

Its own gating/export behaviour lives in ``tests/core/test_trace_forwarder.py``;
a capturing fake stands in for it here, so no test ever builds a real OTLP
exporter.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.activity import DashboardDelta
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.store import JsonWorkspaceStore
from grove.daemon import app as app_module
from grove.daemon import build_app
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config


class _CapturingForwarder:
    """Records the lifecycle wiring the lifespan performs."""

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


def test_telemetry_off_leaves_no_forwarder_on_the_app_at_all(
    fake_tmux: FakeTmux, tmp_state_dir: Path
) -> None:
    """Disabled is indistinguishable from absent — no attribute, no subscriber,
    no thread."""
    del fake_tmux, tmp_state_dir
    app = build_app(cfg=daemon_test_config(), store=JsonWorkspaceStore())
    with TestClient(app) as client:
        del client
        assert not hasattr(app.state, "trace_forwarder")


def test_lifespan_binds_the_forwarder_to_the_live_bus_and_closes_it(
    fake_tmux: FakeTmux,
    tmp_state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del fake_tmux, tmp_state_dir
    forwarder = _CapturingForwarder()
    monkeypatch.setattr(
        app_module.TraceForwarder, "from_config", classmethod(lambda cls, cfg, **kw: forwarder)
    )
    app = build_app(cfg=daemon_test_config(), store=JsonWorkspaceStore())

    with TestClient(app) as client:
        del client
        registry = app.state.registry
        repo = _init_repo(tmp_path / "repo")
        registry.get(repo).create(CreateWorkspaceRequest(agent_name="shell", title="mirror"))
        app.state.activity.poll_once()  # a fresh workspace is a change → a real delta

        assert forwarder.bound
        assert forwarder.deltas  # …and it reached the forwarder's own callback
        assert app.state.trace_forwarder is forwarder

    assert forwarder.closed
