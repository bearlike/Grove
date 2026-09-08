"""JsonWorkspaceStore snapshot cache behavior.

Each test names the cache break it catches: deleting the stat-signature check,
the workspace/repository indexes, or the defensive copy would change the
observable read or mutation behavior below.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from grove.core import paths
from grove.core.errors import GroveError, WorkspaceNotFound
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus


def _state(workspace_id: str, repo_root: str) -> WorkspaceState:
    now = datetime.now(tz=UTC)
    return WorkspaceState(
        id=workspace_id,
        title=f"title-{workspace_id}",
        repo_root=repo_root,
        branch=f"grove/{workspace_id}",
        base_branch="HEAD",
        worktree_path=f"{repo_root}/.worktrees/{workspace_id}",
        tmux_session=f"grove-{workspace_id}",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )


def test_unchanged_snapshot_reads_parse_once_and_return_independent_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing the snapshot cache would reread all three records per accessor."""
    path = tmp_path / "state.json"
    writer = JsonWorkspaceStore(path)
    writer.save(_state("one", "/repo/a"))
    writer.save(_state("two", "/repo/a"))
    writer.save(_state("three", "/repo/b"))

    reads = 0
    parsed_record_counts: list[int] = []
    real_open = Path.open
    real_json_load = json.load

    def count_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        nonlocal reads
        if self == path:
            reads += 1
        return real_open(self, *args, **kwargs)

    def count_json_load(*args: Any, **kwargs: Any) -> Any:
        data = real_json_load(*args, **kwargs)
        parsed_record_counts.append(len(data["workspaces"]))
        return data

    monkeypatch.setattr(Path, "open", count_open)
    monkeypatch.setattr("grove.core.store.json.load", count_json_load)

    reader = JsonWorkspaceStore(path)
    states = reader.load_all()
    states[0].title = "caller-owned mutation"

    assert reader.get("one").title == "title-one"
    assert {state.id for state in reader.for_repo(Path("/repo/a"))} == {"one", "two"}
    assert sorted(reader.list_repo_roots()) == [Path("/repo/a"), Path("/repo/b")]
    assert reads == 1
    assert parsed_record_counts == [3]

    reader.invalidate()
    assert {state.id for state in reader.load_all()} == {"one", "two", "three"}
    assert reads == 2
    assert parsed_record_counts == [3, 3]


def test_second_store_write_and_external_deletion_refresh_cached_reader(tmp_path: Path) -> None:
    """Dropping signature comparison would hide the other instance's atomic replacement."""
    path = tmp_path / "state.json"
    writer = JsonWorkspaceStore(path)
    reader = JsonWorkspaceStore(path)

    writer.save(_state("alpha", "/repo/a"))
    assert reader.get("alpha").id == "alpha"

    writer.save(_state("beta", "/repo/b"))
    assert {state.id for state in reader.load_all()} == {"alpha", "beta"}

    path.unlink()
    assert reader.load_all() == []
    with pytest.raises(WorkspaceNotFound):
        reader.get("alpha")


def test_replacement_during_read_never_labels_old_bytes_as_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replacing the post-read stat with descriptor stat makes this return stale data."""
    path = tmp_path / "state.json"
    writer = JsonWorkspaceStore(path)
    writer.save(_state("alpha", "/repo/a"))
    old_generation = path.read_text(encoding="utf-8")
    writer.save(_state("beta", "/repo/b"))
    new_generation = path.read_text(encoding="utf-8")
    paths.write_atomic(path, old_generation)

    real_json_load = json.load
    replaced = False

    def replace_after_read(*args: Any, **kwargs: Any) -> Any:
        nonlocal replaced
        data = real_json_load(*args, **kwargs)
        if not replaced:
            replaced = True
            paths.write_atomic(path, new_generation)
        return data

    monkeypatch.setattr("grove.core.store.json.load", replace_after_read)
    reader = JsonWorkspaceStore(path)

    assert {state.id for state in reader.load_all()} == {"alpha"}
    assert {state.id for state in reader.load_all()} == {"alpha", "beta"}


def test_corrupt_external_replacement_raises_instead_of_serving_cached_state(
    tmp_path: Path,
) -> None:
    """Dropping the changed-file reload would incorrectly return the prior valid snapshot."""
    path = tmp_path / "state.json"
    writer = JsonWorkspaceStore(path)
    reader = JsonWorkspaceStore(path)
    writer.save(_state("alpha", "/repo/a"))
    assert reader.get("alpha").id == "alpha"

    paths.write_atomic(path, "{not valid json\n")

    with pytest.raises(GroveError, match="corrupt state file"):
        reader.load_all()
