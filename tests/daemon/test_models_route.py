"""GET /models?repo=&agent= — authenticated enriched picker catalogs."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.auth import SessionStore
from grove.core.config import GroveConfig
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.conftest import FakeTmux


@pytest.fixture
def auth_store(tmp_state_dir: Path) -> SessionStore:
    del tmp_state_dir
    return SessionStore()


@pytest.fixture
def daemon(auth_store: SessionStore, fake_tmux: FakeTmux, tmp_repo: Path) -> Iterator[TestClient]:
    (tmp_repo / ".grove").mkdir()
    (tmp_repo / ".grove" / "config.json").write_text(
        """{
  "agents": [
    {
      "name": "claude",
      "command": "picker",
      "kind": "generic",
      "models": ["first-unlabelled", "first-named"]
    },
    {
      "name": "second",
      "command": "picker",
      "kind": "generic",
      "models": ["second-model"]
    }
  ],
  "models": {"display_names": {"first-named": "First named model"}}
}
""",
        encoding="utf-8",
    )
    cfg = GroveConfig.model_validate({"projects": [str(tmp_repo)]})
    with TestClient(
        build_app(cfg=cfg, store=JsonWorkspaceStore(), auth_store=auth_store)
    ) as client:
        yield client


def test_models_returns_the_default_agents_enriched_catalog(
    daemon: TestClient, auth_store: SessionStore, tmp_repo: Path
) -> None:
    response = daemon.get(f"/models?repo={tmp_repo}", headers=_auth_headers(auth_store))

    assert response.status_code == 200, response.text
    assert response.json() == [
        {"id": "first-unlabelled", "name": None, "context_window": None},
        {"id": "first-named", "name": "First named model", "context_window": None},
    ]


def test_models_returns_empty_for_an_unknown_agent(
    daemon: TestClient, auth_store: SessionStore, tmp_repo: Path
) -> None:
    response = daemon.get(
        f"/models?repo={tmp_repo}&agent=missing",
        headers=_auth_headers(auth_store),
    )

    assert response.status_code == 200, response.text
    assert response.json() == []


def test_models_rejects_an_unknown_repo_root(
    daemon: TestClient, auth_store: SessionStore, tmp_path: Path
) -> None:
    response = daemon.get(
        f"/models?repo={tmp_path / 'not-registered'}",
        headers=_auth_headers(auth_store),
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["error"] == "unknown_repo_root"


def test_models_requires_an_authenticated_reader(daemon: TestClient, tmp_repo: Path) -> None:
    response = daemon.get(f"/models?repo={tmp_repo}")

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "auth_missing"


def _auth_headers(auth_store: SessionStore) -> dict[str, str]:
    challenge = auth_store.pair_init(label="model picker test")
    auth_store.pair_approve(challenge.challenge_id)
    _, token = auth_store.pair_poll(challenge.challenge_id)
    assert token is not None
    return {"Authorization": f"Bearer {token}"}
