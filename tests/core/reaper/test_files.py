"""The files collector: per-workspace residue no record owns, and a transcript is never residue."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core import paths
from grove.core.errors import GroveError
from grove.core.reaper import FilesCollector, Ledger, ResourceRef
from grove.core.workspace import WorkspaceState, WorkspaceStatus

#: Captured at import, before the autouse fixture stubs it out for every test.
_FOUND = FilesCollector.found

LIVE_ID = "a" * 32
GONE_ID = "b" * 32
LIVE_SESSION = "11111111-1111-1111-1111-111111111111"
GONE_SESSION = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def collector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FilesCollector:
    # The one accessor the autouse fixtures leave on the real state dir.
    monkeypatch.setattr(
        "grove.core.paths.agent_exit_path", lambda ws: tmp_path / "agent-exits" / f"{ws}.exit"
    )
    monkeypatch.setattr(FilesCollector, "found", _FOUND)
    return FilesCollector()


def _touch(path: Path, size: int = 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def _states(tmp_path: Path) -> list[WorkspaceState]:
    now = datetime.now(UTC)
    return [
        WorkspaceState(
            id=LIVE_ID,
            title="t",
            repo_root=str(tmp_path),
            branch="b",
            base_branch="main",
            worktree_path=str(tmp_path),
            tmux_session="s",
            agent_name="claude",
            status=WorkspaceStatus.PAUSED,
            created_at=now,
            updated_at=now,
            agent_session_id=LIVE_SESSION,
        )
    ]


def _candidates(collector: FilesCollector, tmp_path: Path) -> set[Path]:
    return {Path(ref.key) for ref in Ledger.candidates(collector.found(), _states(tmp_path))}


@pytest.mark.parametrize(
    "accessor",
    [
        "agent_exit_path",
        "init_log_path",
        "provision_log_path",
        "native_worker_config_path",
        "agent_brief_path",
    ],
)
def test_a_workspace_file_is_a_candidate_only_when_no_record_names_its_workspace(
    collector: FilesCollector, tmp_path: Path, accessor: str
) -> None:
    # Resolved at call time, so it reads whatever the fixtures redirected.
    resolve = getattr(paths, accessor)
    live = _touch(resolve(LIVE_ID))
    gone = _touch(resolve(GONE_ID))
    assert _candidates(collector, tmp_path) == {gone}
    assert live.exists()


def test_a_sidecar_is_owned_through_its_session(collector: FilesCollector, tmp_path: Path) -> None:
    sidecars = paths.agent_sidecar_dir()
    launched = _touch(sidecars / f"{LIVE_SESSION}.json")
    stranger = _touch(sidecars / f"{GONE_SESSION}.json")
    adopted = _touch(sidecars / "33333333-3333-3333-3333-333333333333.json")
    # A session the usage store recorded under a live workspace is that workspace's.
    with sqlite3.connect(paths.usage_db_path()) as db:
        db.execute("CREATE TABLE sessions (session_id TEXT, workspace_id TEXT)")
        db.execute("INSERT INTO sessions VALUES (?, ?)", (adopted.stem, LIVE_ID))
    assert _candidates(collector, tmp_path) == {stranger}
    assert launched.exists()


def test_an_agent_config_dir_with_a_real_transcript_is_kept_and_never_removed(
    collector: FilesCollector, tmp_path: Path
) -> None:
    seeded = paths.agent_workspace_config_dir(GONE_ID)
    _touch(seeded / ".claude.json")
    _touch(seeded / "projects" / "-repo" / "empty.jsonl", FilesCollector.TRANSCRIPT_FLOOR)
    talked = paths.agent_workspace_config_dir("c" * 32)
    transcript = _touch(
        talked / "projects" / "-repo" / "s.jsonl", FilesCollector.TRANSCRIPT_FLOOR + 1
    )
    assert _candidates(collector, tmp_path) == {seeded}
    assert collector.kept == (talked,)

    collector.remove(next(ref for ref in collector.found() if ref.key == str(seeded)))
    assert not seeded.exists()
    # A transcript written after the mark still wins at the moment of acting.
    with pytest.raises(GroveError, match="transcript"):
        collector.remove(ResourceRef("files", str(talked), talked.name))
    assert transcript.exists()


def test_remove_refuses_a_path_outside_the_six_directories(
    collector: FilesCollector, tmp_path: Path
) -> None:
    backup = _touch(paths.user_auth_path().parent / "config.json.bak.1")
    with pytest.raises(GroveError):
        collector.remove(ResourceRef("files", str(backup), GONE_ID))
    assert backup.exists()
