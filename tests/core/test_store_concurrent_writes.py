"""Two overlapping store mutations serialize instead of losing one.

`write_atomic` closed the corruption half — a reader never sees a blend. What it
could not close is the lost update: both writers read the same records, each
adds its own, and the second rename publishes a file missing the first one's
workspace. Grove runs the daemon, the TUI and a `grove` CLI verb over one
`state.json`, so that overlap is ordinary use.

The tests widen the read-modify-write window deliberately rather than racing and
hoping: a save that cannot overlap proves nothing. `flock` is held per open file
description, so two threads opening the sidecar separately exclude each other
exactly as two processes would — which is what makes this testable without
spawning any.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core import paths
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="advisory locking is a documented no-op on native Windows"
)

_OVERLAP_SECONDS = 0.25


def _state(ws_id: str) -> WorkspaceState:
    now = datetime.now(UTC)
    return WorkspaceState(
        id=ws_id,
        title=f"t-{ws_id}",
        repo_root="/repos/foo",
        branch=f"b-{ws_id}",
        base_branch="main",
        worktree_path=f"/repos/foo/.worktrees/{ws_id}",
        tmux_session=f"grove-{ws_id}",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )


def _persisted_ids(path: Path) -> set[str]:
    return set(json.loads(path.read_text(encoding="utf-8"))["workspaces"])


@pytest.fixture
def slow_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stretch every read-modify-write so two of them are guaranteed to overlap."""
    real = JsonWorkspaceStore.load_all

    def delayed(self: JsonWorkspaceStore) -> list[WorkspaceState]:
        records = real(self)
        time.sleep(_OVERLAP_SECONDS)
        return records

    monkeypatch.setattr(JsonWorkspaceStore, "load_all", delayed)


def _run_concurrently(*calls: threading.Thread) -> None:
    for thread in calls:
        thread.start()
    for thread in calls:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a store mutation never completed — deadlocked lock?"


def test_two_overlapping_saves_both_survive(tmp_path: Path, slow_read: None) -> None:
    path = tmp_path / "state.json"
    store = JsonWorkspaceStore(path)
    _run_concurrently(
        threading.Thread(target=store.save, args=(_state("alpha"),)),
        threading.Thread(target=store.save, args=(_state("beta"),)),
    )
    assert _persisted_ids(path) == {"alpha", "beta"}


def test_a_save_overlapping_a_delete_keeps_the_untouched_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slow_read: None
) -> None:
    path = tmp_path / "state.json"
    store = JsonWorkspaceStore(path)
    store.save(_state("doomed"))
    _run_concurrently(
        threading.Thread(target=store.delete, args=("doomed",)),
        threading.Thread(target=store.save, args=(_state("newcomer"),)),
    )
    # Whichever order the two take, "newcomer" was added by a writer that read
    # under the lock, so it cannot be erased by a delete that read before it.
    assert _persisted_ids(path) == {"newcomer"}


def test_the_lock_is_a_sidecar_so_the_atomic_rename_cannot_shed_it(tmp_path: Path) -> None:
    # The target is replaced by rename, so locking it by name would put two
    # writers on two different inodes and exclude nothing.
    path = tmp_path / "state.json"
    with paths.exclusive_lock(path):
        pass
    assert (tmp_path / "state.json.lock").exists()
    assert not path.exists()


def test_a_second_holder_waits_for_the_first(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    acquired = threading.Event()
    entered_second = threading.Event()

    def hold() -> None:
        with paths.exclusive_lock(path):
            acquired.set()
            time.sleep(_OVERLAP_SECONDS)

    def contend() -> None:
        acquired.wait(timeout=5)
        with paths.exclusive_lock(path):
            entered_second.set()

    holder = threading.Thread(target=hold)
    waiter = threading.Thread(target=contend)
    holder.start()
    waiter.start()
    acquired.wait(timeout=5)
    assert not entered_second.is_set(), "the second holder entered while the first held the lock"
    holder.join(timeout=5)
    waiter.join(timeout=5)
    assert entered_second.is_set()
