"""GET /branches?repo=<path>&scope=local|remote."""

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


def test_branches_local(daemon: TestClient, tmp_repo: Path) -> None:
    resp = daemon.get(f"/branches?repo={tmp_repo}&scope=local")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    names = {item["name"] for item in body}
    assert "main" in names


def test_branches_remote_empty(daemon: TestClient, tmp_repo: Path) -> None:
    # tmp_repo from conftest has no remote configured
    resp = daemon.get(f"/branches?repo={tmp_repo}&scope=remote")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_branches_invalid_scope(daemon: TestClient, tmp_repo: Path) -> None:
    resp = daemon.get(f"/branches?repo={tmp_repo}&scope=bogus")
    assert resp.status_code == 422
