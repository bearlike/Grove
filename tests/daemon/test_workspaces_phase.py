"""``/workspaces/{id}/phase``: the manual write/read seam over the task-phase
axis. GET is best-effort — 200 ``null`` for "not reported", never a 404. POST
is the loud orchestrator-facing write, including the per-ticket claim split
(``ticket=``) that lands on one attached ticket's entry and leaves the
workspace's own claim untouched.
"""

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
def daemon(
    tmp_state_dir: Path,
    tmp_repo: Path,
    fake_tmux: FakeTmux,
) -> Iterator[TestClient]:
    store = JsonWorkspaceStore()
    app = build_app(cfg=daemon_test_config(), store=store)
    with TestClient(app) as client:
        yield client


def _create_ws(daemon: TestClient, tmp_repo: Path, *, agent_name: str = "claude") -> str:
    body = daemon.post(
        "/workspaces",
        json={
            "agent_name": agent_name,
            "title": "phase test",
            "repo_root": str(tmp_repo),
            "branch_plan": {"kind": "auto"},
        },
    ).json()
    return str(body["id"])


def test_get_phase_returns_null_never_404_for_an_unreported_workspace(
    daemon: TestClient, tmp_repo: Path
) -> None:
    ws_id = _create_ws(daemon, tmp_repo)

    resp = daemon.get(f"/workspaces/{ws_id}/phase")

    assert resp.status_code == 200
    assert resp.json() is None


def test_post_phase_sets_the_workspace_claim(daemon: TestClient, tmp_repo: Path) -> None:
    ws_id = _create_ws(daemon, tmp_repo)

    resp = daemon.post(
        f"/workspaces/{ws_id}/phase",
        json={"phase": "planning", "note": "reading the ticket"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["phase"] == "planning"
    assert body["note"] == "reading the ticket"

    again = daemon.get(f"/workspaces/{ws_id}/phase")
    assert again.status_code == 200
    assert again.json()["phase"] == "planning"


def test_post_phase_with_ticket_sets_only_that_tickets_claim(
    daemon: TestClient, tmp_repo: Path
) -> None:
    ws_id = _create_ws(daemon, tmp_repo)
    seed = daemon.post(f"/workspaces/{ws_id}/phase", json={"phase": "implementing"})
    assert seed.status_code == 200
    attach = daemon.post(f"/workspaces/{ws_id}/tickets", json={"provider": "gitea", "id": "7"})
    assert attach.status_code == 200

    resp = daemon.post(
        f"/workspaces/{ws_id}/phase",
        json={"phase": "verifying", "note": "running gates", "ticket": "gitea:7"},
    )
    assert resp.status_code == 200

    # The response and a fresh GET both describe the WORKSPACE's own claim,
    # which a ticket-scoped write must leave alone.
    assert resp.json()["phase"] == "implementing"
    still = daemon.get(f"/workspaces/{ws_id}/phase")
    assert still.json()["phase"] == "implementing"

    # `PhaseView` carries only the workspace's own claim, so read the ticket's
    # claim back through the same manager seam the issueops publisher uses.
    registry = daemon.app.state.registry
    mgr = registry.get(tmp_repo)
    report = mgr.phase(ws_id)
    assert report is not None
    ticket_claim = report.for_ticket("gitea:7")
    assert ticket_claim is not None
    assert ticket_claim.phase == "verifying"
    assert ticket_claim.note == "running gates"


def test_post_phase_with_an_unattached_ticket_is_refused(
    daemon: TestClient, tmp_repo: Path
) -> None:
    ws_id = _create_ws(daemon, tmp_repo)

    resp = daemon.post(
        f"/workspaces/{ws_id}/phase",
        json={"phase": "planning", "ticket": "gitea:404"},
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "ticket_not_attached"

    # Refused before any write — the workspace still reports nothing.
    assert daemon.get(f"/workspaces/{ws_id}/phase").json() is None
