"""HTTP dependencies keep mailbox bearers out of ordinary daemon routes."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from grove.core.auth import Session, SessionStore
from grove.core.config import GroveConfig
from grove.core.contracts.mailboxes import MailboxAddress, MailboxIdentity
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from grove.daemon.auth import make_require_mailbox_session


@pytest.fixture
def auth_store(tmp_state_dir: Path) -> SessionStore:
    del tmp_state_dir
    return SessionStore()


@pytest.fixture
def daemon(auth_store: SessionStore) -> Iterator[TestClient]:
    app = build_app(cfg=GroveConfig(), store=JsonWorkspaceStore(), auth_store=auth_store)
    with TestClient(app) as client:
        yield client


def _identity() -> MailboxIdentity:
    return MailboxIdentity(
        address=MailboxAddress(workspace_id="a" * 32, agent="claude"),
        generation="b" * 32,
    )


def _ordinary_token(store: SessionStore) -> str:
    challenge = store.pair_init(label="operator")
    store.pair_approve(challenge.challenge_id)
    _, token = store.pair_poll(challenge.challenge_id)
    assert token is not None
    return token


def test_mailbox_session_is_denied_on_real_ordinary_routes(
    daemon: TestClient, auth_store: SessionStore
) -> None:
    token, session = auth_store.issue_mailbox_session(_identity(), label="native mailbox")
    headers = {"Authorization": f"Bearer {token}"}

    workspace = daemon.get("/workspaces", headers=headers)
    self_revoke = daemon.delete(f"/auth/sessions/{session.session_id}", headers=headers)

    for response in (workspace, self_revoke):
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["error"] == "mailbox_scope_denied"
    assert auth_store.validate(token).session_id == session.session_id


def test_mailbox_dependency_accepts_scoped_and_ordinary_sessions(
    auth_store: SessionStore,
) -> None:
    mailbox_token, mailbox_session = auth_store.issue_mailbox_session(
        _identity(), label="native mailbox"
    )
    ordinary_token = _ordinary_token(auth_store)
    dependency = make_require_mailbox_session(auth_store=auth_store, enabled=True)

    # The coordinator owns send policy; this dependency only admits the two
    # credential classes that its discover/status routes intentionally share.
    app = FastAPI()

    @app.get("/mailbox")
    async def mailbox(session: Session = Depends(dependency)) -> dict[str, bool]:  # noqa: B008
        return {"scoped": session.mailbox_identity is not None}

    with TestClient(app) as client:
        mailbox_response = client.get(
            "/mailbox", headers={"Authorization": f"Bearer {mailbox_token}"}
        )
        ordinary_response = client.get(
            "/mailbox", headers={"Authorization": f"Bearer {ordinary_token}"}
        )
        assert mailbox_response.json() == {"scoped": True}
        assert ordinary_response.json() == {"scoped": False}
    assert mailbox_session.mailbox_identity == _identity()
