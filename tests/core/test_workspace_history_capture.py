"""The two write seams, driven through REAL engine paths rather than the store.

`test_workspace_history.py` pins the store's own behaviour. This file pins the
thing that file cannot see: that the store is actually REACHED — once by
`JsonWorkspaceStore.save` for a name, and once by `ActivityService`'s tick for a
phase claim. A store whose producers never call it is the shape this repo's
guide calls a member that reads as handled in review and in a green suite.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from grove.core.activity import ActivityService
from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.phase import PhaseFile
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace_history import WorkspaceHistoryStore
from tests.conftest import FakeTmux


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "t@grove.local"],
        ["config", "user.name", "Grove Test"],
    ):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--no-verify"], cwd=path, check=True, capture_output=True
    )
    return path.resolve()


def _env(
    tmp_path: Path, history: WorkspaceHistoryStore
) -> tuple[ActivityService, RepoRegistry, Path]:
    cfg = GroveConfig.model_validate(
        {"tmux": {"session_prefix": "test-"}, "hooks": {"enabled": False}}
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json", history=history)
    registry = RepoRegistry(cfg=cfg, store=store)
    return (
        ActivityService(registry=registry, history=history),
        registry,
        _init_repo(tmp_path / "repo"),
    )


def test_create_records_the_workspace_name_through_the_real_store(
    fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A `create` is enough — no explicit history call anywhere in the engine."""
    history = WorkspaceHistoryStore(tmp_path / "history.sqlite3")
    _, registry, repo = _env(tmp_path, history)

    created = registry.get(repo).create(
        CreateWorkspaceRequest(agent_name="claude", title="persist titles")
    )

    recorded = history.history_for(created.id)
    assert recorded.name is not None
    assert recorded.name.title == "persist titles"
    assert recorded.name.repo_root == str(repo)
    # Still live, so no tombstone yet — the field that lets a client tell
    # "finished and torn down" from "still running".
    assert recorded.name.deleted_at is None


def test_rename_then_kill_keeps_every_name_the_workspace_ever_had(
    fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The whole point: the names outlive the record `kill` deletes."""
    history = WorkspaceHistoryStore(tmp_path / "history.sqlite3")
    _, registry, repo = _env(tmp_path, history)
    mgr = registry.get(repo)
    created = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="c618ba38b7"))

    mgr.update(created.id, title="usage: persist titles", description="the real work")
    mgr.kill(created.id)

    # The record is genuinely gone — this is what used to erase the title.
    assert all(
        state.id != created.id for state in JsonWorkspaceStore(tmp_path / "state.json").load_all()
    )

    recorded = history.history_for(created.id)
    assert recorded.name is not None
    assert recorded.name.title == "usage: persist titles"
    assert recorded.name.description == "the real work"
    assert recorded.name.deleted_at is not None
    assert [change.title for change in recorded.names] == [
        "usage: persist titles",
        "c618ba38b7",
    ]


def test_the_tick_records_the_phase_claim_and_dedupes_across_polls(
    fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A note is overwritten on the next transition, so the tick is its only record.

    Also the dedupe at its real call site: `snapshot()` reads the phase file on
    every poll, so an unchanged claim must not accumulate a row per tick. Three
    snapshots of one claim is the cheapest version of the ~86,400-per-day
    failure this dedupe exists to prevent.
    """
    history = WorkspaceHistoryStore(tmp_path / "history.sqlite3")
    service, registry, repo = _env(tmp_path, history)
    created = registry.get(repo).create(
        CreateWorkspaceRequest(agent_name="claude", title="phase reporter")
    )
    PhaseFile.write(
        Path(created.worktree_path),
        PhaseFile.key_for(created.id),
        "build",
        "wiring the store",
    )

    service.bootstrap()
    for _ in range(3):
        service.snapshot()

    claims = history.history_for(created.id).progress
    assert [(c.phase, c.note, c.ticket_key) for c in claims] == [
        ("build", "wiring the store", None)
    ]

    # A real transition appends; the previous claim is still there, which is the
    # history nothing else in Grove keeps.
    PhaseFile.write(
        Path(created.worktree_path),
        PhaseFile.key_for(created.id),
        "verify",
    )
    service.refresh_workspace(str(repo), created.id)
    assert {c.phase for c in history.history_for(created.id).progress} == {
        "build",
        "verify",
    }


def test_a_broken_history_store_never_breaks_a_create_or_a_snapshot(
    fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Best-effort is the contract: a lost row must not cost a workspace.

    The store is pointed at a DIRECTORY, so every connection attempt fails —
    the closest stand-in for an unwritable state dir that needs no permission
    games. `create` must still return a workspace and `snapshot` must still
    describe it.
    """
    unwritable = tmp_path / "not-a-file"
    unwritable.mkdir()
    history = WorkspaceHistoryStore(unwritable)
    service, registry, repo = _env(tmp_path, history)

    created = registry.get(repo).create(
        CreateWorkspaceRequest(agent_name="claude", title="survives a dead store")
    )
    service.bootstrap()
    snap = service.snapshot()

    assert created.title == "survives a dead store"
    assert snap.total_workspaces == 1
    # And the read degrades to empty rather than raising.
    assert history.history_for(created.id).is_empty


def test_constructing_a_store_creates_no_history_file(tmp_path: Path) -> None:
    """Every `grove` CLI verb builds a `JsonWorkspaceStore`, so an eager
    connection would put a SQLite file plus its two WAL sidecars on disk just
    to run `grove ls`. Asserted on the DIRECTORY rather than on a private
    attribute, so the laziness is pinned by its observable effect.
    """
    history_path = tmp_path / "history.sqlite3"
    JsonWorkspaceStore(path=tmp_path / "state.json", history=WorkspaceHistoryStore(history_path))

    assert not history_path.exists()
    assert not list(tmp_path.glob("history.sqlite3*"))
