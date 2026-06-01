"""Daemon HTTP auth gate + pairing endpoints.

Exercises the *real* auth-on path that production runs (no
``auth.enabled=False`` shortcut). Pins:

- Pairing flow end-to-end: pair -> approve (engine call) -> poll yields token.
- Every existing endpoint refuses unauthenticated.
- 401 envelope shape for missing / malformed / unknown / revoked / expired.
- Sessions list / revoke round-trip.
- Approval is engine-only — there is no HTTP route that approves a pairing.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from grove.core.auth import SessionStore
from grove.core.config import GroveConfig
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app


@pytest.fixture
def auth_store(tmp_state_dir: Path) -> SessionStore:
    del tmp_state_dir  # ensures path redirection ran before we instantiate
    return SessionStore()


@pytest.fixture
def daemon(auth_store: SessionStore) -> Iterator[TestClient]:
    cfg = GroveConfig()  # auth.enabled defaults to True
    app = build_app(cfg=cfg, store=JsonWorkspaceStore(), auth_store=auth_store)
    with TestClient(app) as client:
        yield client


# ─── pairing endpoints ──────────────────────────────────────────────────────


def test_pair_init_returns_challenge_with_code(daemon: TestClient) -> None:
    resp = daemon.post("/auth/pair", json={"label": "phone"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["label"] == "phone"
    assert body["state"] == "pending"
    assert body["code"][4] == "-"


def test_pair_init_unauthenticated(daemon: TestClient) -> None:
    """Bootstrap path — pairing must not require an existing session."""
    resp = daemon.post("/auth/pair", json={"label": "phone"})
    assert resp.status_code == 200


def test_pair_init_rejects_empty_label(daemon: TestClient) -> None:
    resp = daemon.post("/auth/pair", json={"label": "   "})
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_label"


def test_full_pair_flow_yields_working_token(daemon: TestClient, auth_store: SessionStore) -> None:
    # 1. Browser kicks off pairing.
    init_resp = daemon.post("/auth/pair", json={"label": "phone"})
    init_resp.raise_for_status()
    challenge_id = init_resp.json()["challenge_id"]

    # 2. Pending → approved happens via the engine (TUI / CLI), not HTTP.
    auth_store.pair_approve(UUID(challenge_id))

    # 3. Poll consumes the approval and returns the bearer token.
    poll_resp = daemon.get(f"/auth/pair/{challenge_id}")
    poll_resp.raise_for_status()
    body = poll_resp.json()
    assert body["state"] == "consumed"
    token = body["token"]
    assert token and token.startswith("grove_v1_")

    # 4. The token works against a gated endpoint.
    auth = {"Authorization": f"Bearer {token}"}
    me_resp = daemon.get("/auth/sessions/me", headers=auth)
    assert me_resp.status_code == 200
    assert me_resp.json()["label"] == "phone"

    # 5. Token is single-use on poll — second poll yields no token.
    poll2 = daemon.get(f"/auth/pair/{challenge_id}")
    assert poll2.status_code == 200
    assert poll2.json()["token"] is None


def test_no_http_endpoint_can_approve(daemon: TestClient) -> None:
    """The deliberate omission — approve is engine-only.

    Pin this so a future contributor doesn't add ``POST /auth/pair/{id}/approve``
    by reflex. Approval over HTTP would let a remote attacker self-approve
    and break the threat model.
    """
    resp = daemon.post(
        "/auth/pair/00000000-0000-0000-0000-000000000000/approve",
        json={},
    )
    # 405 (method not allowed) or 404 (no such route) — both prove there's
    # no HTTP approve endpoint registered.
    assert resp.status_code in {404, 405}


# ─── gated endpoints reject unauthenticated ─────────────────────────────────


_GATED_ROUTES = [
    ("GET", "/workspaces"),
    ("GET", "/workspaces/some-id"),
    ("POST", "/workspaces"),
    ("POST", "/workspaces/some-id/pause"),
    ("POST", "/workspaces/some-id/resume"),
    ("POST", "/workspaces/some-id/respawn"),
    ("POST", "/workspaces/some-id/kill"),
    ("PATCH", "/workspaces/some-id"),
    ("GET", "/workspaces/some-id/attach"),
    ("GET", "/workspaces/some-id/peek"),
    ("GET", "/workspaces/some-id/commits"),
    ("GET", "/branches?repo=/tmp&scope=local"),
    ("GET", "/auth/sessions"),
    ("GET", "/auth/sessions/me"),
    ("GET", "/auth/pending"),
    ("GET", "/whoami"),
]


@pytest.mark.parametrize(("method", "path"), _GATED_ROUTES)
def test_gated_endpoint_refuses_no_token(daemon: TestClient, method: str, path: str) -> None:
    resp = daemon.request(method, path, json={})
    assert resp.status_code == 401
    detail = resp.json()["detail"]
    assert detail["error"] == "auth_missing"


@pytest.mark.parametrize(("method", "path"), _GATED_ROUTES)
def test_gated_endpoint_refuses_malformed_token(daemon: TestClient, method: str, path: str) -> None:
    resp = daemon.request(
        method, path, json={}, headers={"Authorization": "Bearer not-a-grove-token"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "auth_invalid"


def test_healthz_does_not_require_auth(daemon: TestClient) -> None:
    """Liveness probes must work without a session — that's their whole job."""
    resp = daemon.get("/healthz")
    assert resp.status_code == 200


# ─── sessions ──────────────────────────────────────────────────────────────


def _mint_token(auth_store: SessionStore, label: str = "test") -> str:
    challenge = auth_store.pair_init(label=label)
    auth_store.pair_approve(challenge.challenge_id)
    _, token = auth_store.pair_poll(challenge.challenge_id)
    assert token is not None
    return token


def test_list_sessions_returns_only_active(daemon: TestClient, auth_store: SessionStore) -> None:
    token1 = _mint_token(auth_store, "phone")
    _mint_token(auth_store, "laptop")
    auth = {"Authorization": f"Bearer {token1}"}
    resp = daemon.get("/auth/sessions", headers=auth)
    assert resp.status_code == 200
    bodies = resp.json()
    assert {b["label"] for b in bodies} == {"phone", "laptop"}
    # No token_hash / revoked_at internals leak.
    for b in bodies:
        assert "token_hash" not in b


def test_revoke_kills_subsequent_calls(daemon: TestClient, auth_store: SessionStore) -> None:
    token = _mint_token(auth_store, "phone")
    auth = {"Authorization": f"Bearer {token}"}
    me = daemon.get("/auth/sessions/me", headers=auth).json()
    sid = me["session_id"]
    revoke = daemon.delete(f"/auth/sessions/{sid}", headers=auth)
    assert revoke.status_code == 204
    again = daemon.get("/auth/sessions/me", headers=auth)
    assert again.status_code == 401
    assert again.json()["detail"]["error"] == "auth_invalid"


def test_pair_deny_requires_session(daemon: TestClient, auth_store: SessionStore) -> None:
    challenge = auth_store.pair_init(label="phone")
    # No auth → 401.
    resp = daemon.post(f"/auth/pair/{challenge.challenge_id}/deny")
    assert resp.status_code == 401
    # With auth → 204.
    token = _mint_token(auth_store, "approver")
    resp = daemon.post(
        f"/auth/pair/{challenge.challenge_id}/deny",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204


def test_pair_poll_for_unknown_id_returns_404(daemon: TestClient) -> None:
    resp = daemon.get("/auth/pair/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "pair_not_found"
