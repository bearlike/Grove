"""``GET /workspaces/{id}/provision``: the provisioning progress tail.

The fetch-on-demand half of the provisioning axis. Whether a workspace is
PROVISIONING rides the activity stream (it is the reconciled status); the
headline and log tail do not, because reading them is a file read per
workspace and the poll walks the whole host every couple of seconds.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import ProvisionStatus
from grove.daemon import build_app
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config


@pytest.fixture
def store() -> JsonWorkspaceStore:
    return JsonWorkspaceStore()


@pytest.fixture
def daemon(
    tmp_state_dir: Path,
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    store: JsonWorkspaceStore,
) -> Iterator[TestClient]:
    app = build_app(cfg=daemon_test_config(), store=store)
    with TestClient(app) as client:
        yield client


def _await_activity_row(
    daemon: TestClient,
    workspace_id: str,
    *,
    status: str | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Poll ``/activity`` until the projection carries this workspace (at *status*).

    The snapshot is an event-driven projection, not a per-request scan: a create
    and a direct store write are both applied by the source owner on its own
    task, so a read taken in the same breath legitimately precedes them. Waiting
    is the honest assertion — the contract is that the row ARRIVES, not that it
    is already there — and an explicit timeout keeps a genuine never-arrives
    from passing as a slow one.

    Waiting on the STATUS rather than merely on the row's presence matters for
    the same reason: a workspace mutated behind the daemon's back is already in
    the projection under its previous status, so a presence-only wait returns
    the stale row immediately and asserts against the value it was racing.
    """
    deadline = time.monotonic() + timeout
    seen: str | None = None
    while True:
        snapshot = daemon.get("/activity").json()
        for group in snapshot["projects"]:
            for row in group["workspaces"]:
                if row["state"]["id"] != workspace_id:
                    continue
                seen = row["state"]["status"]
                if status is None or seen == status:
                    return cast("dict[str, Any]", row)
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"workspace {workspace_id} never reached the activity projection"
                f"{f' at status {status!r} (last saw {seen!r})' if status else ''} "
                f"within {timeout}s"
            )
        time.sleep(0.05)


def _create_ws(daemon: TestClient, tmp_repo: Path) -> str:
    body = daemon.post(
        "/workspaces",
        json={
            "agent_name": "claude",
            "title": "provision test",
            "repo_root": str(tmp_repo),
            "branch_plan": {"kind": "auto"},
        },
    ).json()
    return str(body["id"])


def test_provision_progress_of_a_host_workspace_is_an_empty_200(
    daemon: TestClient, tmp_repo: Path
) -> None:
    """ "Nothing to report" is a real answer, not a 404 — the ``/phase``
    precedent. A host workspace never provisions and has no log."""
    ws_id = _create_ws(daemon, tmp_repo)

    resp = daemon.get(f"/workspaces/{ws_id}/provision")

    assert resp.status_code == 200
    assert resp.json() == {"elapsed_ms": None, "headline": "", "lines": []}


def test_provision_progress_reports_the_tail_and_the_elapsed_time(
    daemon: TestClient, tmp_repo: Path, store: JsonWorkspaceStore, tmp_path: Path
) -> None:
    ws_id = _create_ws(daemon, tmp_repo)
    log = tmp_path / "provision.log"
    log.write_text("[+] Building 0.4s\n\ngrove: applying egress allowlist\n", encoding="utf-8")
    started = datetime.now(UTC) - timedelta(seconds=30)
    store.save(
        replace(
            store.get(ws_id),
            provision_status=ProvisionStatus.PROVISIONING,
            provision_started_at=started.isoformat(),
            provision_log_path=str(log),
        )
    )

    body = daemon.get(f"/workspaces/{ws_id}/provision").json()

    assert body["headline"] == "grove: applying egress allowlist"
    assert body["lines"] == ["[+] Building 0.4s", "grove: applying egress allowlist"]
    assert body["elapsed_ms"] is not None
    assert body["elapsed_ms"] >= 30_000


def test_provision_progress_of_an_unknown_workspace_is_404(daemon: TestClient) -> None:
    resp = daemon.get("/workspaces/nope/provision")

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "workspace_not_found"


def test_provision_log_path_never_reaches_the_wire(
    daemon: TestClient, tmp_repo: Path, store: JsonWorkspaceStore, tmp_path: Path
) -> None:
    """A host path, like ``init_log_path``. It must be absent from BOTH the
    state view and the progress view — the tail is how a client reads the log."""
    ws_id = _create_ws(daemon, tmp_repo)
    log = tmp_path / "provision.log"
    log.write_text("grove: building\n", encoding="utf-8")
    store.save(replace(store.get(ws_id), provision_log_path=str(log)))

    assert "provision_log_path" not in daemon.get(f"/workspaces/{ws_id}").json()
    assert "provision_log_path" not in daemon.get(f"/workspaces/{ws_id}/provision").json()


def test_the_activity_snapshot_carries_the_provisioning_facts(
    daemon: TestClient, tmp_repo: Path, store: JsonWorkspaceStore
) -> None:
    """Provisioning STREAMS for free and the headline deliberately does not.

    PROVISIONING is the workspace's reconciled status, which is already the
    first element of ``WorkspaceActivity.fingerprint`` — so entering and
    leaving it emits a delta with no change to the poll. The dashboard row
    embeds the same ``WorkspaceStateView``, so the started-at rides along and a
    client renders its own elapsed clock. The headline is NOT here on purpose:
    a live build rewrites it constantly, so putting it in the ~1 Hz fingerprint
    would re-emit every workspace on every tick AND cost a log read per
    workspace per tick. It is fetched from ``GET .../provision`` instead.
    """
    ws_id = _create_ws(daemon, tmp_repo)
    started = datetime.now(UTC) - timedelta(seconds=5)
    store.save(
        replace(
            store.get(ws_id),
            provision_status=ProvisionStatus.PROVISIONING,
            provision_started_at=started.isoformat(),
        )
    )

    state = _await_activity_row(daemon, ws_id, status="provisioning")["state"]
    assert state["status"] == "provisioning"
    assert state["provision_status"] == "provisioning"
    assert state["provision_started_at"] is not None
    assert "headline" not in state
