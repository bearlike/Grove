"""Daemon ↔ OTLP receiver mount wiring (Gitea #498).

``grove.core.telemetry.receiver.build_receiver_app`` is built and unit-tested
on its own; this file proves the one thing only the daemon integration can
prove. **The trap:** Starlette never drives a MOUNTED app's own lifespan, so
``OtlpIngest.submit`` self-starts a worker but nothing ever calls
``aclose()`` unless the DAEMON's own lifespan does — a mount that "looks
wired" (accepts requests, self-starts) can still leak its worker and never
drain on shutdown. Off by default; on is opt-in and never touches
``opentelemetry`` imports unless the config asks for it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from grove.core.config import GroveConfig
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.conftest import FakeTmux


def _cfg(*, enabled: bool) -> GroveConfig:
    return GroveConfig.model_validate(
        {"auth": {"enabled": False}, "telemetry": {"receiver": {"enabled": enabled}}}
    )


def test_receiver_off_by_default_mounts_nothing(fake_tmux: FakeTmux, tmp_state_dir: Path) -> None:
    """Default config stays byte-identical to before this route existed."""
    del fake_tmux, tmp_state_dir
    app = build_app(cfg=_cfg(enabled=False), store=JsonWorkspaceStore())
    with TestClient(app) as client:
        assert not hasattr(app.state, "otlp_ingest")
        resp = client.post("/otlp/v1/traces", content=b"")
        assert resp.status_code == 404


def test_receiver_on_mounts_at_the_configured_path_and_accepts_an_export(
    fake_tmux: FakeTmux, tmp_state_dir: Path
) -> None:
    del fake_tmux, tmp_state_dir
    app = build_app(cfg=_cfg(enabled=True), store=JsonWorkspaceStore())
    with TestClient(app) as client:
        assert hasattr(app.state, "otlp_ingest")
        resp = client.post(
            "/otlp/v1/traces", content=b"", headers={"content-type": "application/x-protobuf"}
        )
        assert resp.status_code == 200


def test_daemon_lifespan_drains_the_ingest_on_shutdown(
    fake_tmux: FakeTmux, tmp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mount alone cannot prove this — only closing the DAEMON can."""
    del fake_tmux, tmp_state_dir
    app = build_app(cfg=_cfg(enabled=True), store=JsonWorkspaceStore())
    with TestClient(app) as client:
        ingest = app.state.otlp_ingest
        spy = AsyncMock(wraps=ingest.aclose)
        monkeypatch.setattr(ingest, "aclose", spy)
        client.post("/otlp/v1/traces", content=b"")

    spy.assert_awaited_once()
