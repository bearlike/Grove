"""GET /agents?repo=<path> — the new-workspace picker's agent list."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config


@pytest.fixture
def daemon(tmp_state_dir: Path, fake_tmux: FakeTmux) -> Iterator[TestClient]:
    store = JsonWorkspaceStore()
    app = build_app(cfg=daemon_test_config(), store=store)
    with TestClient(app) as client:
        yield client


def test_agents_lists_configured_agents(daemon: TestClient, tmp_repo: Path) -> None:
    resp = daemon.get(f"/agents?repo={tmp_repo}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    by_name = {item["name"]: item for item in body}
    # The default cascade ships a claude_code agent plus a generic shell.
    assert "claude" in by_name
    assert by_name["claude"]["kind"] == "claude_code"
    assert "shell" in by_name
    assert by_name["shell"]["kind"] == "generic"


def test_agents_omits_command_and_env(daemon: TestClient, tmp_repo: Path) -> None:
    # command / env can carry host-private paths + secrets — they must not cross
    # the wire. The summary view is name / kind / description / models only.
    resp = daemon.get(f"/agents?repo={tmp_repo}")
    assert resp.status_code == 200, resp.text
    for item in resp.json():
        assert set(item) == {"name", "kind", "description", "models"}


def test_agents_requires_repo(daemon: TestClient) -> None:
    resp = daemon.get("/agents")
    assert resp.status_code == 422


def test_agents_resolves_per_kind_model_catalog(daemon: TestClient, tmp_repo: Path) -> None:
    # The default cascade (claude=claude_code, codex=codex, shell=generic)
    # exercises all three resolve_models branches in one request: the
    # claude_code adapter's stable tier aliases, the codex adapter's fake
    # (conftest-patched) discovery catalog, and generic's empty catalog.
    resp = daemon.get(f"/agents?repo={tmp_repo}")
    assert resp.status_code == 200, resp.text
    by_name = {item["name"]: item for item in resp.json()}
    assert by_name["claude"]["models"] == ["sonnet", "opus", "haiku"]
    assert by_name["codex"]["models"] == ["gpt-5.5", "gpt-5.4"]
    assert by_name["shell"]["models"] == []
