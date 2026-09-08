"""``GET /workspaces/{id}/history``: durable workspace facts stay private.

The route is a per-request SQLite read rather than an activity-stream field: the
history grows without bound, while a workspace that predates recording still
has an honest empty answer.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.auth import SessionStore
from grove.core.config import GroveConfig
from grove.core.contracts.tickets import TicketRef
from grove.core.phase import PhaseReport, TaskPhase, TicketClaim
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace_history import WorkspaceHistoryStore
from grove.daemon import build_app
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config

_AT = datetime(2026, 9, 13, 12, tzinfo=UTC)


@pytest.fixture
def history_store(tmp_path: Path) -> WorkspaceHistoryStore:
    return WorkspaceHistoryStore(tmp_path / "workspace-history.sqlite3")


@pytest.fixture
def daemon(
    tmp_state_dir: Path,
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    history_store: WorkspaceHistoryStore,
) -> Iterator[TestClient]:
    del tmp_state_dir, tmp_repo, fake_tmux
    # Injected into the WORKSPACE store as well, not only as the route's reader:
    # that store is what records a name on save and the tombstone on delete, so
    # a fixture supplying only the route's copy would leave the two halves
    # writing and reading different instances — which is exactly how the
    # kill-then-read test below passed against a tombstone nobody wrote.
    app = build_app(
        cfg=daemon_test_config(),
        store=JsonWorkspaceStore(history=history_store),
        history_store=history_store,
    )
    with TestClient(app) as client:
        yield client


def _create_workspace(daemon: TestClient, tmp_repo: Path) -> str:
    response = daemon.post(
        "/workspaces",
        json={
            "agent_name": "claude",
            "title": "history route test",
            "repo_root": str(tmp_repo),
            "branch_plan": {"kind": "auto"},
        },
    )
    response.raise_for_status()
    return str(response.json()["id"])


def _report(
    phase: TaskPhase,
    *,
    blocked: bool = False,
    note: str | None = None,
    tickets: tuple[TicketClaim, ...] = (),
) -> PhaseReport:
    return PhaseReport(
        phase=phase,
        blocked=blocked,
        note=note,
        updated_at=_AT,
        tickets=tickets,
    )


def test_creating_a_workspace_records_its_name_and_nothing_else(
    daemon: TestClient, tmp_repo: Path
) -> None:
    """`create` saves the record, and the save IS the name recorder.

    So a fresh workspace has a name and one name-history row, and no progress
    or tickets — it has claimed nothing and is attached to nothing yet.
    """
    workspace_id = _create_workspace(daemon, tmp_repo)

    response = daemon.get(f"/workspaces/{workspace_id}/history")

    assert response.status_code == 200
    body = response.json()
    assert body["name"]["title"] == "history route test"
    assert body["name"]["deleted_at"] is None
    assert [entry["title"] for entry in body["names"]] == ["history route test"]
    assert body["progress"] == []
    assert body["tickets"] == []


def test_history_serializes_recorded_facts_in_store_order(
    daemon: TestClient, tmp_repo: Path, history_store: WorkspaceHistoryStore
) -> None:
    workspace_id = _create_workspace(daemon, tmp_repo)
    first = _AT
    renamed = first + timedelta(seconds=1)
    planned = first + timedelta(seconds=2)
    verified = first + timedelta(seconds=3)
    second_ticket = first + timedelta(seconds=4)

    history_store.record_name(
        workspace_id,
        title="Initial title",
        description="first description",
        repo_root=str(tmp_repo),
        now=first,
    )
    history_store.record_name(
        workspace_id,
        title="Renamed title",
        description="current description",
        repo_root=str(tmp_repo),
        now=renamed,
    )
    history_store.record_progress(
        workspace_id,
        _report(
            "planning",
            note="mapping the route",
            tickets=(TicketClaim(ticket="gitea:687", phase="planning"),),
        ),
        now=planned,
    )
    history_store.record_progress(
        workspace_id,
        _report(
            "verifying",
            blocked=True,
            note="running checks",
            tickets=(TicketClaim(ticket="gitea:688", phase="implementing"),),
        ),
        now=verified,
    )
    history_store.record_tickets(
        workspace_id,
        [TicketRef(provider="gitea", id="687")],
        now=planned,
    )
    history_store.record_tickets(
        workspace_id,
        [TicketRef(provider="gitea", id="688", kind="pull_request")],
        now=second_ticket,
    )

    response = daemon.get(f"/workspaces/{workspace_id}/history")

    assert response.status_code == 200
    body = response.json()
    # `first_seen` belongs to the CREATE, which recorded the real title before
    # this test renamed anything — so the assertions here cover what the test
    # itself controls. The create's own title is the oldest name-history row.
    assert body["name"]["workspace_id"] == workspace_id
    assert body["name"]["title"] == "Renamed title"
    assert body["name"]["description"] == "current description"
    assert body["name"]["repo_root"] == str(tmp_repo)
    assert body["name"]["last_seen"] == "2026-09-13T12:00:01Z"
    assert body["name"]["deleted_at"] is None
    # The create's own row is recorded at REAL wall-clock time, which is newer
    # than this test's fixed `_AT`, so it sorts first — the ordering is correct
    # (newest recorded_at first) and the fixture's clock is the anomaly. Assert
    # the pair this test controls keeps its own relative order.
    titles = [entry["title"] for entry in body["names"]]
    assert titles.index("Renamed title") < titles.index("Initial title")
    assert set(titles) == {"Renamed title", "Initial title", "history route test"}
    assert [entry["phase"] for entry in body["progress"]] == [
        "verifying",
        "implementing",
        "planning",
        "planning",
    ]
    assert [entry["recorded_at"] for entry in body["progress"]] == [
        "2026-09-13T12:00:03Z",
        "2026-09-13T12:00:03Z",
        "2026-09-13T12:00:02Z",
        "2026-09-13T12:00:02Z",
    ]
    assert [entry["ticket_key"] for entry in body["tickets"]] == ["gitea:687", "gitea:688"]
    assert body["tickets"][1]["kind"] == "pull_request"


def test_history_returns_a_tombstone_for_a_known_workspace(
    daemon: TestClient, tmp_repo: Path, history_store: WorkspaceHistoryStore
) -> None:
    workspace_id = _create_workspace(daemon, tmp_repo)
    deleted_at = _AT + timedelta(minutes=1)
    history_store.record_name(
        workspace_id,
        title="Deleted workspace",
        description=None,
        repo_root=str(tmp_repo),
        now=_AT,
    )
    history_store.mark_deleted(workspace_id, now=deleted_at)

    response = daemon.get(f"/workspaces/{workspace_id}/history")

    assert response.status_code == 200
    name = response.json()["name"]
    assert name["workspace_id"] == workspace_id
    assert name["title"] == "Deleted workspace"
    assert name["description"] is None
    assert name["repo_root"] == str(tmp_repo)
    assert name["last_seen"] == "2026-09-13T12:01:00Z"
    assert name["deleted_at"] == "2026-09-13T12:01:00Z"


def test_history_preserves_absent_progress_note_and_ticket_key_as_null(
    daemon: TestClient, tmp_repo: Path, history_store: WorkspaceHistoryStore
) -> None:
    workspace_id = _create_workspace(daemon, tmp_repo)
    history_store.record_progress(workspace_id, _report("implementing"), now=_AT)

    response = daemon.get(f"/workspaces/{workspace_id}/history")

    assert response.status_code == 200
    assert response.json()["progress"] == [
        {
            "recorded_at": "2026-09-13T12:00:00Z",
            "phase": "implementing",
            "blocked": False,
            "note": None,
            "ticket_key": None,
        }
    ]


def test_history_survives_the_workspace_being_killed(
    daemon: TestClient, tmp_repo: Path, history_store: WorkspaceHistoryStore
) -> None:
    """The motivating case, driven through the real create/kill verbs.

    Missing from this file until the route was verified by hand: every other
    test here reads a LIVE workspace, so all of them passed while the one
    question the store exists to answer returned 404.
    """
    workspace_id = _create_workspace(daemon, tmp_repo)
    history_store.record_progress(
        workspace_id, _report("implementing", note="wiring the durable store")
    )

    daemon.post(
        f"/workspaces/{workspace_id}/kill", json={"delete_branch": False}
    ).raise_for_status()

    response = daemon.get(f"/workspaces/{workspace_id}/history")
    assert response.status_code == 200
    body = response.json()
    assert body["name"]["title"] == "history route test"
    # The tombstone is what lets a client say "gone" rather than implying live.
    assert body["name"]["deleted_at"] is not None
    assert [entry["note"] for entry in body["progress"]] == ["wiring the durable store"]


def test_history_for_a_workspace_the_store_never_saw_is_an_empty_200(
    daemon: TestClient,
) -> None:
    """Deliberately NOT a 404, unlike every sibling per-workspace read.

    `kill` deletes the record, and answering for a workspace whose record is
    gone is this store's whole purpose — a `_manager_for` gate here (the
    obvious copy from `/todo`) made a killed workspace's history unreachable
    through the only route that serves it. So an id with nothing recorded and
    an id that never existed give the same honest empty answer; a reader
    holding an id off a usage row cannot tell those apart and does not need to.
    """
    response = daemon.get("/workspaces/nope/history")

    assert response.status_code == 200
    assert response.json() == {"name": None, "names": [], "progress": [], "tickets": []}


def test_history_requires_auth(tmp_path: Path) -> None:
    app = build_app(
        cfg=GroveConfig(),
        store=JsonWorkspaceStore(tmp_path / "state.json"),
        auth_store=SessionStore(path=tmp_path / "auth.json"),
        history_store=WorkspaceHistoryStore(tmp_path / "workspace-history.sqlite3"),
    )
    with TestClient(app) as daemon:
        response = daemon.get("/workspaces/nope/history")

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "auth_missing"
