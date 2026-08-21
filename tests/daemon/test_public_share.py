"""Unauthenticated public-share routes remain read-only and narrowly scoped."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.config import GiteaTicketConfig, GroveConfig, TicketsConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.contracts.tickets import TicketRef, TicketSelector
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState
from grove.daemon import build_app
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config


@pytest.fixture
def shared_workspace(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> tuple[WorkspaceManager, WorkspaceState]:
    del fake_tmux
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    manager = WorkspaceManager(repo_root=tmp_repo, cfg=GroveConfig(), store=store)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="shared work"))
    shared = manager.update(state.id, share=True)
    assert shared.share_token is not None
    return manager, shared


@pytest.fixture
def public_client(
    shared_workspace: tuple[WorkspaceManager, WorkspaceState],
) -> Iterator[TestClient]:
    manager, _ = shared_workspace
    # Auth stays enabled: `/public` is the one intentionally unauthenticated namespace.
    app = build_app(cfg=GroveConfig(), store=manager.store)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def workspace_client(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> Iterator[tuple[TestClient, WorkspaceState]]:
    del fake_tmux
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    manager = WorkspaceManager(repo_root=tmp_repo, cfg=daemon_test_config(), store=store)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="private work"))
    app = build_app(cfg=daemon_test_config(), store=store)
    with TestClient(app) as client:
        yield client, state


def test_public_workspace_is_readable_without_authorization(
    public_client: TestClient, shared_workspace: tuple[WorkspaceManager, WorkspaceState]
) -> None:
    _, state = shared_workspace
    assert state.share_token is not None

    response = public_client.get(f"/public/{state.share_token}")

    assert "authorization" not in response.request.headers
    assert response.status_code == 200, response.text
    assert response.json()["peek"]["state"]["id"] == state.id


def _ticket_config() -> GroveConfig:
    return GroveConfig(
        tickets=TicketsConfig(
            gitea=GiteaTicketConfig(enabled=True, owner="o", repo="r", token_env="T")
        )
    )


def _write_ticket_config(repo: Path) -> None:
    grove_dir = repo / ".grove"
    grove_dir.mkdir()
    (grove_dir / "config.json").write_text(
        json.dumps(
            {
                "tickets": {
                    "gitea": {
                        "enabled": True,
                        "owner": "o",
                        "repo": "r",
                        "token_env": "T",
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def _shared_workspace_with_ticket(
    repo: Path, *, title: str = "shared work", store: JsonWorkspaceStore | None = None
) -> tuple[WorkspaceManager, WorkspaceState]:
    cfg = _ticket_config()
    _write_ticket_config(repo)
    manager = WorkspaceManager(
        repo_root=repo,
        cfg=cfg,
        store=store if store is not None else JsonWorkspaceStore(path=repo.parent / "state.json"),
    )
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title=title))
    manager.attach_ticket(state.id, TicketSelector(provider="gitea", id="42"))
    shared = manager.update(state.id, share=True)
    assert shared.share_token is not None
    return manager, shared


def _second_repo(tmp_path: Path) -> Path:
    other = tmp_path / "other-repo"
    subprocess.run(["git", "init", "-b", "main", other], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", other, "config", "user.email", "test@grove.local"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", other, "config", "user.name", "Grove Test"],
        check=True,
        capture_output=True,
    )
    (other / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "-C", other, "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", other, "commit", "-m", "init", "--no-verify"],
        check=True,
        capture_output=True,
    )
    return other.resolve()


def test_public_ticket_resolution_exposes_only_allowed_ticket_fields(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fake_tmux
    manager, state = _shared_workspace_with_ticket(
        tmp_repo, store=JsonWorkspaceStore(path=tmp_path / "state.json")
    )
    app = build_app(cfg=_ticket_config(), store=manager.store)

    with TestClient(app) as client:
        resolver = client.app.state.registry.get(tmp_repo).ticket_providers.get("gitea")
        monkeypatch.setattr(
            resolver,
            "get_ticket",
            lambda ticket_id: TicketRef(
                provider="gitea",
                id=ticket_id,
                title="Resolved public ticket",
                url="https://tickets.example.test/o/r/issues/42",
                status="open",
                assignee="private tracker identity",
            ),
        )
        assert state.share_token is not None
        response = client.get(f"/public/{state.share_token}")

    assert response.status_code == 200, response.text
    ref = response.json()["peek"]["state"]["ticket_refs"][0]
    # Exhaustive equality, not a subset check: a new field on `TicketRef`
    # reaches this payload by default, so only comparing the WHOLE dict forces
    # somebody to decide whether it may be published. `draft` was admitted on
    # exactly that review — it is the same class of fact as `status`, which is
    # already here, and unlike the deliberately-stripped `url` and `assignee`
    # it discloses neither the private tracker's host nor anyone's identity.
    assert ref == {
        "provider": "gitea",
        "id": "42",
        "kind": "issue",
        "title": "Resolved public ticket",
        "url": None,
        "status": "open",
        "assignee": None,
        "ambiguous": False,
        "draft": False,
    }


def test_public_ticket_resolution_failure_degrades_to_the_bare_ref(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fake_tmux
    manager, state = _shared_workspace_with_ticket(
        tmp_repo, store=JsonWorkspaceStore(path=tmp_path / "state.json")
    )
    app = build_app(cfg=_ticket_config(), store=manager.store)

    with TestClient(app) as client:
        resolver = client.app.state.registry.get(tmp_repo).ticket_providers.get("gitea")

        def get_ticket(ticket_id: str) -> TicketRef:
            del ticket_id
            raise OSError

        monkeypatch.setattr(resolver, "get_ticket", get_ticket)
        assert state.share_token is not None
        response = client.get(f"/public/{state.share_token}")

    assert response.status_code == 200, response.text
    ref = response.json()["peek"]["state"]["ticket_refs"][0]
    assert ref["title"] is None
    assert ref["status"] is None
    assert ref["url"] is None
    assert ref["assignee"] is None


def test_public_ticket_resolution_is_memoized_across_polls(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fake_tmux
    manager, state = _shared_workspace_with_ticket(
        tmp_repo, store=JsonWorkspaceStore(path=tmp_path / "state.json")
    )
    app = build_app(cfg=_ticket_config(), store=manager.store)
    fetches = 0

    with TestClient(app) as client:
        resolver = client.app.state.registry.get(tmp_repo).ticket_providers.get("gitea")

        def get_ticket(ticket_id: str) -> TicketRef:
            nonlocal fetches
            fetches += 1
            return TicketRef(provider="gitea", id=ticket_id, title="Resolved", status="open")

        monkeypatch.setattr(resolver, "get_ticket", get_ticket)
        assert state.share_token is not None
        for _ in range(3):
            response = client.get(f"/public/{state.share_token}")
            assert response.status_code == 200, response.text

    assert fetches == 1


def test_public_ticket_memo_is_scoped_to_the_repository(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fake_tmux
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    _, first = _shared_workspace_with_ticket(tmp_repo, title="first shared work", store=store)
    other_repo = _second_repo(tmp_path)
    _, second = _shared_workspace_with_ticket(other_repo, title="second shared work", store=store)
    app = build_app(cfg=_ticket_config(), store=store)

    with TestClient(app) as client:
        first_resolver = client.app.state.registry.get(tmp_repo).ticket_providers.get("gitea")
        second_resolver = client.app.state.registry.get(other_repo).ticket_providers.get("gitea")
        monkeypatch.setattr(
            first_resolver,
            "get_ticket",
            lambda ticket_id: TicketRef(
                provider="gitea", id=ticket_id, title="First repository ticket"
            ),
        )
        monkeypatch.setattr(
            second_resolver,
            "get_ticket",
            lambda ticket_id: TicketRef(
                provider="gitea", id=ticket_id, title="Second repository ticket"
            ),
        )
        assert first.share_token is not None
        assert second.share_token is not None
        first_response = client.get(f"/public/{first.share_token}")
        second_response = client.get(f"/public/{second.share_token}")

    assert first_response.status_code == 200, first_response.text
    assert second_response.status_code == 200, second_response.text
    assert first_response.json()["peek"]["state"]["ticket_refs"][0]["title"] == (
        "First repository ticket"
    )
    assert second_response.json()["peek"]["state"]["ticket_refs"][0]["title"] == (
        "Second repository ticket"
    )


def test_public_unknown_and_revoked_tokens_have_the_same_not_found_response(
    public_client: TestClient, shared_workspace: tuple[WorkspaceManager, WorkspaceState]
) -> None:
    manager, state = shared_workspace
    assert state.share_token is not None

    unknown = public_client.get("/public/no-such-token")
    manager.update(state.id, share=False)
    revoked = public_client.get(f"/public/{state.share_token}")

    assert unknown.status_code == revoked.status_code == 404
    assert unknown.json()["detail"]["error"] == "share_not_found"
    assert revoked.json()["detail"]["error"] == "share_not_found"
    assert unknown.json() == revoked.json()


def test_authenticated_workspace_namespace_remains_closed_without_a_bearer(
    public_client: TestClient, shared_workspace: tuple[WorkspaceManager, WorkspaceState]
) -> None:
    _, state = shared_workspace

    response = public_client.get(f"/workspaces/{state.id}")

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "auth_missing"


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
def test_public_namespace_has_no_mutating_routes(
    public_client: TestClient,
    shared_workspace: tuple[WorkspaceManager, WorkspaceState],
    method: str,
) -> None:
    _, state = shared_workspace
    assert state.share_token is not None

    response = public_client.request(method, f"/public/{state.share_token}", json={})

    assert response.status_code == 405


@pytest.mark.parametrize("params", [{"last": 1}, {"after_turn": 0}])
def test_public_turns_accepts_each_window_parameter_individually(
    public_client: TestClient,
    shared_workspace: tuple[WorkspaceManager, WorkspaceState],
    params: dict[str, int],
) -> None:
    _, state = shared_workspace
    assert state.share_token is not None

    response = public_client.get(f"/public/{state.share_token}/turns", params=params)

    assert response.status_code == 200, response.text


def test_public_turns_rejects_ambiguous_windows(
    public_client: TestClient, shared_workspace: tuple[WorkspaceManager, WorkspaceState]
) -> None:
    _, state = shared_workspace
    assert state.share_token is not None

    response = public_client.get(
        f"/public/{state.share_token}/turns",
        params={"last": 1, "after_turn": 0},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "invalid_turn_window"


def test_patch_share_returns_a_token_and_refuses_an_empty_update(
    workspace_client: tuple[TestClient, WorkspaceState],
) -> None:
    client, state = workspace_client

    shared = client.patch(f"/workspaces/{state.id}", json={"share": True})
    empty = client.patch(f"/workspaces/{state.id}", json={})

    assert shared.status_code == 200, shared.text
    assert shared.json()["share_token"]
    assert empty.status_code == 422
