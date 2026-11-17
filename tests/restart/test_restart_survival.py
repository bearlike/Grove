"""Do X → SIGKILL the daemon → start it → assert what must have survived.

One test per invariant in #955. A row the engine does not satisfy today is
``xfail(strict=True)`` with the reason, so the day it is fixed the suite says so.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from grove.core.contracts.watches import WatchView
from grove.core.workspace import ProvisionStatus, WorkspaceState, WorkspaceStatus
from tests.restart.conftest import DaemonChild, kill9_and_restart


def _seed_workspace(
    daemon: DaemonChild, repo: Path, *, status: WorkspaceStatus, **fields: object
) -> WorkspaceState:
    """Write a workspace record the way another Grove process would."""
    now = datetime.now(tz=UTC)
    ws_id = uuid4().hex
    worktree = daemon.root / f"wt-{ws_id}"
    worktree.mkdir()
    state = WorkspaceState(
        id=ws_id,
        title="restart",
        repo_root=str(repo),
        branch=f"grove/{ws_id}",
        base_branch="main",
        worktree_path=str(worktree),
        tmux_session=f"grove-restart-{ws_id}",
        agent_name="claude",
        status=status,
        created_at=now,
        updated_at=now,
        **fields,  # type: ignore[arg-type]
    )
    daemon.store().save(state)
    return state


def _register_timer(daemon: DaemonChild, recipient: str, at: datetime) -> WatchView:
    resp = daemon.post(
        "/watches",
        {
            "recipient": {"workspace_id": recipient},
            "predicate": {"kind": "timer", "at": at.isoformat()},
            "every": 15,
        },
    )
    assert resp.status_code == 201, resp.text
    return WatchView.model_validate(resp.json())


def _watch(daemon: DaemonChild, watch_id: str) -> WatchView:
    rows = daemon.get("/watches").json()["watches"]
    return next(WatchView.model_validate(r) for r in rows if r["id"] == watch_id)


def _await_settled(daemon: DaemonChild, watch_id: str) -> WatchView:
    # A recovered row is re-armed with jitter across its own interval (15 s
    # here), so it settles within one interval of the start, not instantly.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        row = _watch(daemon, watch_id)
        if row.state != "pending":
            return row
        time.sleep(1)
    pytest.fail(f"watch {watch_id} never settled after the restart")


def test_pending_watch_survives_sigkill(daemon_child: DaemonChild, tmp_repo: Path) -> None:
    ws = _seed_workspace(daemon_child, tmp_repo, status=WorkspaceStatus.RUNNING)
    watch = _register_timer(daemon_child, ws.id, datetime.now(tz=UTC) + timedelta(hours=1))

    kill9_and_restart(daemon_child)

    after = _watch(daemon_child, watch.id)
    assert after.state == "pending"
    assert after.predicate == watch.predicate


def _overdue_after_restart(daemon: DaemonChild, repo: Path) -> WatchView:
    """A timer that comes due while the daemon is DOWN, settled after the start."""
    ws = _seed_workspace(daemon, repo, status=WorkspaceStatus.RUNNING)
    at = datetime.now(tz=UTC) + timedelta(seconds=2)
    watch = _register_timer(daemon, ws.id, at)
    daemon.kill9()
    time.sleep(max(0.0, (at - datetime.now(tz=UTC)).total_seconds()) + 1)
    daemon.start()
    return _await_settled(daemon, watch.id)


def test_overdue_watch_fires_once_after_restart(daemon_child: DaemonChild, tmp_repo: Path) -> None:
    row = _overdue_after_restart(daemon_child, tmp_repo)
    # The recipient has no live session, so delivery may be refused (`undeliverable`), but the
    # watch itself FIRED: the outcome is the timer's own, not the expiry's.
    assert row.state in {"fired", "undeliverable"}
    assert row.outcome is not None and row.outcome.ok
    settled_at = row.settled_at

    kill9_and_restart(daemon_child)
    time.sleep(16)
    # Once: a second start must not settle (and mail) it again.
    assert _watch(daemon_child, row.id).settled_at == settled_at


def test_overdue_watch_is_marked_late(daemon_child: DaemonChild, tmp_repo: Path) -> None:
    row = _overdue_after_restart(daemon_child, tmp_repo)
    assert row.outcome is not None
    assert "late" in row.outcome.summary.lower()


def test_bearer_minted_before_restart_validates_after(daemon_child: DaemonChild) -> None:
    daemon_child.token = daemon_child.auth().mint_local("restart-bearer")

    kill9_and_restart(daemon_child)

    assert daemon_child.get("/whoami").status_code == 200
    # Not vacuous: the child really enforces bearers.
    daemon_child.token = "grove_v1_" + "0" * 43
    assert daemon_child.get("/whoami").status_code == 401


def test_record_left_mid_create_is_resolved_at_start(
    daemon_child: DaemonChild, tmp_repo: Path
) -> None:
    ws = _seed_workspace(
        daemon_child,
        tmp_repo,
        status=WorkspaceStatus.RUNNING,
        provision_status=ProvisionStatus.PROVISIONING,
        provision_started_at=datetime.now(tz=UTC).isoformat(),
    )

    kill9_and_restart(daemon_child)

    assert daemon_child.store().get(ws.id).provision_status is not ProvisionStatus.PROVISIONING


def _eventually(predicate: Callable[[], object], what: str, timeout: float = 30.0) -> object:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if value := predicate():
            return value
        time.sleep(0.2)
    pytest.fail(f"timed out waiting for {what}")


def _provider(log: Path) -> dict[str, object]:
    try:
        return dict(json.loads(log.read_text()))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _native_restart_row(daemon: DaemonChild, log: Path, repo: Path, before_kill: str) -> None:
    """Native workspace → one message → SIGKILL + restart → next steer reaches the same provider.

    ``before_kill`` is the message sent just before the kill: an ordinary one, or
    ``slow:<s>``, which the fake answers that many seconds later, so its turn is
    still IN FLIGHT when the daemon dies.
    """
    ws = daemon.post(
        "/workspaces",
        {"agent_name": "claude", "title": "native restart", "repo_root": str(repo)},
    ).json()["id"]
    before = _eventually(lambda: (p := _provider(log)).get("initial") == 1 and p, "initial turn")
    assert daemon.post(f"/workspaces/{ws}/message", {"text": before_kill}).status_code == 204
    _eventually(lambda: before_kill in _provider(log).get("followups", []), "turn started")

    kill9_and_restart(daemon)

    codes: list[int] = []

    def steered() -> bool:
        codes.append(daemon.post(f"/workspaces/{ws}/message", {"text": "followup"}).status_code)
        return codes[-1] == 204

    # The owner registry lives in memory and is rebuilt only by the worker
    # re-registering: until it does, a steer is refused (409), never queued.
    _eventually(steered, "the worker re-registering with the restarted daemon")
    assert set(codes[:-1]) <= {409}, codes
    after = _eventually(
        lambda: (p := _provider(log)).get("followups") == [before_kill, "followup"] and p,
        "the next steer reaching the same provider",
    )
    assert isinstance(before, dict) and isinstance(after, dict)
    assert after["pid"] == before["pid"]


@pytest.mark.requires_tmux
def test_native_worker_survives_a_sigkill_and_takes_the_next_steer(
    native_daemon_child: tuple[DaemonChild, Path], tmp_repo: Path
) -> None:
    """#974: the provider outlives the daemon, the worker re-registers, the next steer lands."""
    daemon, log = native_daemon_child
    _native_restart_row(daemon, log, tmp_repo, before_kill="plain")


@pytest.mark.requires_tmux
def test_native_worker_takes_the_next_steer_after_a_sigkill_with_a_turn_in_flight(
    native_daemon_child: tuple[DaemonChild, Path], tmp_repo: Path
) -> None:
    daemon, log = native_daemon_child
    _native_restart_row(daemon, log, tmp_repo, before_kill="slow:6")
