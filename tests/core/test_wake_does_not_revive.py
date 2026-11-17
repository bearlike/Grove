"""A message that wakes a hibernated remote must not respawn it before its worker dials in (#1076).

Written from the incident on 2026-10-22: a message woke a hibernated workspace
from its snapshot, and two seconds later the steer path's auto-revive read the
not-yet-connected worker as OFFLINE and respawned the workspace from its pushed
branch. That discarded the restored workload and minted a new session, so the
pinned conversation the wake had just restored was lost.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grove.core.config import AgentSpec, CloudRunJobRuntime, DaemonConfig, GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import PaneNotFound
from grove.core.manager import WorkspaceManager
from grove.core.native_owners import OwnerUnavailable
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Runtime, WorkspaceState
from tests.core.test_runtimes_contract import FakeHibernatingDriver


class BootingWorker:
    """The daemon's steer client: no owner until the woken worker registers."""

    def __init__(self) -> None:
        self.registered = False
        self.delivered: list[str] = []

    def owner_connected(self, state: WorkspaceState) -> bool:
        del state
        return self.registered

    def send_message(self, state: WorkspaceState, text: str) -> None:
        del state
        if not self.registered:
            # The daemon client maps this refusal to PaneNotFound.
            raise PaneNotFound("no connected native owner") from OwnerUnavailable(
                "not_registered", "workspace has no connected native owner"
            )
        self.delivered.append(text)


@pytest.fixture
def repo(tmp_repo: Path, tmp_state_dir: Path) -> Path:
    del tmp_state_dir
    subprocess.run(
        ["git", "remote", "add", "origin", "https://git.example.test/acme/app.git"],
        cwd=tmp_repo,
        check=True,
    )
    return tmp_repo


def _hibernated(
    repo: Path, *, boot_grace: bool = True
) -> tuple[WorkspaceManager, FakeHibernatingDriver, BootingWorker, str]:
    cfg = GroveConfig(
        agents=[AgentSpec(name="claude", command="claude", kind="claude_code", native=True)],
        daemon=DaemonConfig(mailbox_url="https://grove.example.test"),
        runtimes={
            "gcp": CloudRunJobRuntime(project="p", region="r", job="j", image="grove-runtime:1")
        },
    )
    driver, worker = FakeHibernatingDriver(), BootingWorker()
    manager = WorkspaceManager(
        repo_root=repo,
        cfg=cfg,
        store=JsonWorkspaceStore(path=repo.parent / "state.json"),
        native_steer=worker,  # type: ignore[arg-type]
    )
    manager._runtimes.remote._factory = lambda name, block: driver
    if not boot_grace:
        manager._runtimes.remote._boot_seconds = None
    state = manager.create(
        CreateWorkspaceRequest(title="remote", agent_name="claude", runtime=Runtime.REMOTE)
    )
    # Created long ago, so only the wake itself can put it inside a boot bound.
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    manager._store.save(replace(state, created_at=long_ago, frozen_at=long_ago))
    manager.hibernate(state.id)
    return manager, driver, worker, state.id


def test_a_message_that_wakes_a_workspace_never_respawns_it(repo: Path) -> None:
    manager, driver, worker, workspace_id = _hibernated(repo)
    ups_before = len(driver.ups)

    with pytest.raises(PaneNotFound, match="waking"):
        manager.send_message(workspace_id, "what was the codeword?")

    assert len(driver.woken) == 1, "the message woke the snapshot"
    assert len(driver.ups) == ups_before, "a respawn replaced the woken workload"
    woken = manager._store.get(workspace_id)
    assert woken.remote is not None and not woken.remote.ref.startswith("snapshot:")

    # Once the woken worker dials in, the retry lands on the same workload.
    worker.registered = True
    manager.send_message(workspace_id, "what was the codeword?")
    assert worker.delivered == ["what was the codeword?"]
    assert manager._store.get(workspace_id).remote == woken.remote
    assert len(driver.ups) == ups_before


def test_a_wake_never_revives_even_when_its_worker_reads_offline(repo: Path) -> None:
    """The boot grace is one guard; this is the other. A woken worker can still
    read OFFLINE (slower than the bound, or no bound configured), and the call
    that just woke it must refuse rather than respawn what it restored."""
    manager, driver, _, workspace_id = _hibernated(repo, boot_grace=False)
    ups_before = len(driver.ups)

    with pytest.raises(PaneNotFound, match="waking"):
        manager.send_message(workspace_id, "what was the codeword?")

    assert len(driver.ups) == ups_before, "a respawn replaced the woken workload"


def test_a_hibernated_workspace_says_so_on_the_wire(repo: Path) -> None:
    """#1077: the view carried frozen_at only, so no API reader could see hibernation."""
    from grove.core.contracts.views import WorkspaceStateView  # noqa: PLC0415

    manager, _, _, workspace_id = _hibernated(repo)
    view = WorkspaceStateView.from_state(manager._store.get(workspace_id))
    assert view.hibernated_at is not None and view.frozen_at is not None
