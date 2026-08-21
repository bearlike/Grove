"""Project-scoped TTL and passcode protections for public share links."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.share_policy import SharePolicy, SharePolicyStore
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config


def _shared_client(
    tmp_repo: Path,
    tmp_path: Path,
) -> tuple[TestClient, WorkspaceManager, SharePolicyStore]:
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    manager = WorkspaceManager(repo_root=tmp_repo, cfg=GroveConfig(), store=store)
    policy_store = SharePolicyStore(path=tmp_path / "share-policies.json")
    app = build_app(
        cfg=daemon_test_config(),
        store=store,
        share_policy_store=policy_store,
    )
    return TestClient(app), manager, policy_store


def _create_shared(manager: WorkspaceManager) -> tuple[str, object]:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="shared work"))
    shared = manager.update(state.id, share=True)
    assert shared.share_token is not None
    return shared.share_token, shared


def test_expired_share_is_byte_identical_to_unknown(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    del fake_tmux
    client, manager, _ = _shared_client(tmp_repo, tmp_path)
    token, shared = _create_shared(manager)
    manager.store.save(replace(shared, share_expires_at=datetime.now(UTC) - timedelta(seconds=1)))

    with client:
        unknown = client.get("/public/no-such-token")
        expired = client.get(f"/public/{token}")

    assert unknown.status_code == expired.status_code == 404
    assert unknown.content == expired.content
    assert unknown.json()["detail"]["error"] == "share_not_found"


def test_passcode_requires_the_same_response_for_missing_and_wrong(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    del fake_tmux
    client, manager, policy_store = _shared_client(tmp_repo, tmp_path)
    policy_store.save(SharePolicy.for_repo(tmp_repo, passcode="correct", ttl_seconds=None))
    token, _ = _create_shared(manager)

    with client:
        missing = client.get(f"/public/{token}")
        wrong = client.get(f"/public/{token}", headers={"X-Grove-Share-Passcode": "wrong"})
        accepted = client.get(f"/public/{token}", headers={"X-Grove-Share-Passcode": "correct"})

    assert missing.status_code == wrong.status_code == 401
    assert missing.content == wrong.content
    assert missing.json()["detail"]["error"] == "share_passcode_required"
    assert accepted.status_code == 200, accepted.text


def test_an_unprotected_project_remains_public_without_a_header(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    del fake_tmux
    client, manager, _ = _shared_client(tmp_repo, tmp_path)
    token, _ = _create_shared(manager)

    with client:
        response = client.get(f"/public/{token}")

    assert response.status_code == 200, response.text


def test_policy_wire_views_never_serialize_the_passcode_hash(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    del fake_tmux
    client, manager, policy_store = _shared_client(tmp_repo, tmp_path)
    saved = policy_store.save(SharePolicy.for_repo(tmp_repo, passcode="correct", ttl_seconds=60))
    assert saved.passcode_hash is not None
    token, _ = _create_shared(manager)

    with client:
        authenticated = client.get("/share-policy", params={"repo": str(tmp_repo)})
        public = client.get(f"/public/{token}", headers={"X-Grove-Share-Passcode": "correct"})

    assert authenticated.status_code == 200, authenticated.text
    assert authenticated.json() == {"ttl_seconds": 60, "passcode_set": True}
    assert public.status_code == 200, public.text
    assert saved.passcode_hash not in json.dumps(authenticated.json())
    assert saved.passcode_hash not in json.dumps(public.json())


def test_share_stamps_current_ttl_without_moving_existing_link_expiry(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    del fake_tmux
    client, manager, policy_store = _shared_client(tmp_repo, tmp_path)
    policy_store.save(SharePolicy.for_repo(tmp_repo, ttl_seconds=600))
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="shared work"))

    with client:
        first_response = client.patch(f"/workspaces/{state.id}", json={"share": True})

    assert first_response.status_code == 200, first_response.text
    first = manager.store.get(state.id)
    assert first.share_expires_at is not None
    first_expiry = first.share_expires_at
    policy_store.save(SharePolicy.for_repo(tmp_repo, ttl_seconds=1))

    assert manager.store.get(state.id).share_expires_at == first_expiry


def test_share_policy_routes_replace_policy_without_returning_hash(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    del fake_tmux
    client, manager, policy_store = _shared_client(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="workspace"))

    with client:
        saved = client.put(
            "/share-policy",
            params={"repo": str(tmp_repo)},
            json={"ttl_seconds": 42, "passcode": "correct"},
        )
        fetched = client.get("/share-policy", params={"repo": str(tmp_repo)})

    assert saved.status_code == fetched.status_code == 200
    assert saved.json() == fetched.json() == {"ttl_seconds": 42, "passcode_set": True}
    stored = policy_store.get(tmp_repo)
    assert stored.passcode_hash is not None
    assert stored.passcode_hash not in json.dumps(saved.json())
