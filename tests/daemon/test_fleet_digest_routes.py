"""`GET /fleet/digest` and `POST /mailboxes/batch` through the real app, lifespan included.

The workspaces are PAUSED so reconciliation needs no tmux and the projection is
the real one the daemon builds. The engine's own behaviour (keys, paging,
idempotency) is pinned in `tests/core/test_fleet_digest.py`; these pin the HTTP
contract: shapes and the refusal codes.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus
from grove.daemon import build_app
from tests.daemon.conftest import daemon_test_config

SENDER = "f" * 32
A, B = "a" * 32, "b" * 32


def _state(workspace_id: str, repo_root: Path) -> WorkspaceState:
    now = datetime.now(tz=UTC)
    return WorkspaceState(
        id=workspace_id,
        title=f"ws-{workspace_id[:4]}",
        repo_root=str(repo_root),
        branch=f"b-{workspace_id[:4]}",
        base_branch="main",
        worktree_path=str(repo_root / ".grove" / "worktrees" / workspace_id),
        tmux_session=f"grove-{workspace_id[:4]}",
        agent_name="claude",
        status=WorkspaceStatus.PAUSED,  # PAUSED: reconcile needs no tmux
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def client(tmp_state_dir: Path) -> Iterator[TestClient]:
    repo = tmp_state_dir / "repo"
    store = JsonWorkspaceStore()
    for state in (_state(A, repo), _state(B, repo)):
        store.save(state)
    app = build_app(cfg=daemon_test_config(), store=store)
    with TestClient(app) as test_client:
        yield test_client


def test_the_digest_lists_every_workspace(client: TestClient) -> None:
    response = client.get("/fleet/digest")

    assert response.status_code == 200
    body = response.json()
    assert [row["workspace_id"] for row in body["rows"]] == [A, B]
    assert body["totals"]["workspaces"] == 2
    assert body["rows"][0]["flags"] == ["offline"]
    assert body["cursor"] and body["resync_required"] is False


def test_since_round_trips_over_http(client: TestClient) -> None:
    first = client.get("/fleet/digest").json()
    unchanged = client.get("/fleet/digest", params={"since": first["cursor"]})

    assert unchanged.status_code == 200
    assert unchanged.json()["rows"] == [] and unchanged.json()["resync_required"] is False


def test_a_narrowed_read_reports_its_scope(client: TestClient) -> None:
    body = client.get("/fleet/digest", params={"workspaces": f"{B},{A}"}).json()
    assert body["scope"] == [A, B]
    assert [row["workspace_id"] for row in body["rows"]] == [A, B]


@pytest.mark.parametrize(
    "params",
    [
        {"workspaces": "abc,def"},
        {"workspaces": ","},
        {"since": "%%%"},
        {"page": "bm90LWpzb24"},
    ],
)
def test_malformed_ids_and_tokens_are_422(client: TestClient, params: dict[str, str]) -> None:
    assert client.get("/fleet/digest", params=params).status_code == 422


def _batch(*recipients: str, request_id: str = "req-0001", body: str = "Rebase.") -> dict:
    return {
        "request_id": request_id,
        "sender": {"workspace_id": SENDER},
        "recipients": [{"workspace_id": r} for r in recipients],
        "subject": "Rebase",
        "body": body,
    }


def test_a_batch_answers_one_receipt_per_recipient_in_order(client: TestClient) -> None:
    response = client.post("/mailboxes/batch", json=_batch(B, A))

    assert response.status_code == 200
    body = response.json()
    # Both are PAUSED: the single-send road refuses them, and so does the batch.
    assert [(r["recipient"]["workspace_id"], r["stage"]) for r in body["receipts"]] == [
        (B, "rejected"),
        (A, "rejected"),
    ]
    assert body["replayed"] is False


def test_a_retried_batch_replays_and_a_changed_one_conflicts(client: TestClient) -> None:
    assert client.post("/mailboxes/batch", json=_batch(A)).json()["replayed"] is False
    assert client.post("/mailboxes/batch", json=_batch(A)).json()["replayed"] is True

    conflict = client.post("/mailboxes/batch", json=_batch(A, body="Something else."))
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["error"] == "batch_conflict"


def test_a_batch_over_the_contract_cap_is_422(client: TestClient) -> None:
    ids = [f"{i:032x}" for i in range(1, 502)]  # `MailboxBatchRequest` allows 500
    response = client.post("/mailboxes/batch", json=_batch(*ids))
    assert response.status_code == 422


def test_both_routes_need_the_daemon_bearer(tmp_state_dir: Path) -> None:
    app = build_app(
        cfg=daemon_test_config().model_copy(
            update={"auth": daemon_test_config().auth.model_copy(update={"enabled": True})}
        ),
        store=JsonWorkspaceStore(),
    )
    with TestClient(app) as client:
        assert client.get("/fleet/digest").status_code == 401
        assert client.post("/mailboxes/batch", json=_batch(A)).status_code == 401
