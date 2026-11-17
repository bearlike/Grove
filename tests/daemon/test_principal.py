"""#965: who is calling, from an OIDC ID token or a Grove bearer, and whose workspaces they see."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

from grove.core.auth import SessionStore
from grove.core.config import GroveConfig, OidcConfig
from grove.core.errors import AuthInvalidToken
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from grove.daemon.oidc import OidcVerifier
from tests.conftest import FakeTmux

ISSUER = "https://id.example.com"
CLIENT = "grove-test"
KEY = RSAKey.generate_key(2048, parameters={"kid": "k1"})
_REAL_CLIENT = httpx.Client


def _issuer() -> httpx.Client:
    """An in-memory OIDC issuer: discovery plus a JWKS holding :data:`KEY`."""
    jwks = KeySet([KEY]).as_dict(private=False)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": f"{ISSUER}/jwks"})
        return httpx.Response(200, json=jwks)

    return _REAL_CLIENT(transport=httpx.MockTransport(handler))


def _id_token(sub: str = "alice", *, aud: str = CLIENT, exp_in: int = 600) -> str:
    claims = {"iss": ISSUER, "sub": sub, "aud": aud, "exp": int(time.time()) + exp_in}
    return jwt.encode({"alg": "RS256", "kid": "k1"}, claims, KEY)


def test_a_valid_id_token_names_a_human_principal() -> None:
    verifier = OidcVerifier(OidcConfig(issuer=ISSUER, client_id=CLIENT), client=_issuer())
    principal = verifier.verify(_id_token("alice"))
    assert (principal.subject, principal.issuer, principal.kind) == ("alice", ISSUER, "human")


@pytest.mark.parametrize(
    "token",
    [_id_token(aud="other"), _id_token(exp_in=-60)],
    # Stable ids: the token is minted per collection, and the CI census matches
    # test ids across two collections.
    ids=["wrong-audience", "expired"],
)
def test_a_token_for_another_audience_or_already_expired_is_refused(token: str) -> None:
    verifier = OidcVerifier(OidcConfig(issuer=ISSUER, client_id=CLIENT), client=_issuer())
    with pytest.raises(AuthInvalidToken):
        verifier.verify(token)


@pytest.fixture
def app_parts(
    tmp_state_dir: Path,
    tmp_path: Path,
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, SessionStore, JsonWorkspaceStore]:
    del fake_tmux, tmp_state_dir
    monkeypatch.setattr("grove.daemon.oidc.httpx.Client", lambda **_kw: _issuer())
    cfg = GroveConfig.model_validate(
        {
            "auth": {"oidc": {"issuer": ISSUER, "client_id": CLIENT}},
            "reaper": {"enabled": False},
            "container": {"enabled": False},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    auth = SessionStore(path=tmp_path / "auth.json")
    return TestClient(build_app(cfg=cfg, store=store, auth_store=auth)), auth, store


def test_sign_in_mints_a_bearer_whose_creates_and_listing_are_the_persons(
    app_parts: tuple[TestClient, SessionStore, JsonWorkspaceStore], tmp_repo: Path
) -> None:
    client, auth, store = app_parts
    signed = client.post("/auth/oidc/session", json={"id_token": _id_token("alice")})
    assert signed.status_code == 200, signed.text
    alice = {"authorization": f"Bearer {signed.json()['token']}"}
    assert client.get("/auth/sessions/me", headers=alice).json()["subject"] == "alice"

    created = client.post(
        "/workspaces",
        headers=alice,
        json={"agent_name": "claude", "title": "mine", "repo_root": str(tmp_repo)},
    )
    assert created.status_code == 200, created.text
    assert created.json()["created_by"] == "alice"
    # Somebody else's workspace, and a legacy record that decodes as the local user.
    other = store.get(created.json()["id"])
    store.save(replace(other, id="b" * 32, tmux_session="other", created_by="bob"))

    mine = client.get("/workspaces", headers=alice).json()
    everyone = client.get("/workspaces", params={"creator": "all"}, headers=alice).json()
    assert [w["created_by"] for w in mine] == ["alice"]
    assert sorted(w["created_by"] for w in everyone) == ["alice", "bob"]
    # A paired local user sees only the local user's work by default.
    local = {"authorization": f"Bearer {auth.mint_local('local-test')}"}
    assert client.get("/workspaces", headers=local).json() == []


def test_a_federated_daemon_refuses_pairing_with_a_typed_conflict(tmp_path: Path) -> None:
    cfg = GroveConfig.model_validate(
        {
            "federation": {"enabled": True, "node": {"role": "hub"}},
            "reaper": {"enabled": False},
        }
    )
    auth = SessionStore(path=tmp_path / "auth.json")
    client = TestClient(
        build_app(cfg=cfg, store=JsonWorkspaceStore(path=tmp_path / "s.json"), auth_store=auth)
    )
    paired = client.post("/auth/pair", json={"label": "laptop"})
    assert paired.status_code == 409
    assert paired.json()["detail"]["error"] == "auth_mode_conflict"
    local = {"authorization": f"Bearer {auth.mint_local('local-test')}"}
    assert client.get("/workspaces", headers=local).json()["detail"]["error"] == (
        "auth_mode_conflict"
    )
