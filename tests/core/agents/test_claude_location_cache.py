"""Coverage for Claude Code's incremental transcript-location index.

Location is independent of transcript parsing: a known session can move between
project directories while a live session gains nested subagents. These tests
exercise only the public adapter surface and real temporary directories, so the
index has to maintain its own dependencies rather than rely on a mocked glob.
"""

from __future__ import annotations

import concurrent.futures
import os
from pathlib import Path

import pytest

from grove.core.agents.claude_code import ClaudeCodeAdapter, _ClaudeHome


def _path(config: Path, cwd: Path, session_id: str, *, folder: str | None = None) -> Path:
    """Create the parent for one main transcript and return its path."""
    project = config / "projects" / (folder or _ClaudeHome.encode_cwd(cwd))
    project.mkdir(parents=True, exist_ok=True)
    return project / f"{session_id}.jsonl"


def _write_main(path: Path, cwd: Path) -> None:
    path.write_text(f'{{"type":"user","cwd":"{cwd}"}}\n', encoding="utf-8")


@pytest.fixture(autouse=True)
def _clear_location_cache() -> None:
    ClaudeCodeAdapter.clear_caches()
    yield
    ClaudeCodeAdapter.clear_caches()


@pytest.fixture
def config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    return root


def test_empty_subagents_directory_observes_its_first_worker(config: Path) -> None:
    adapter = ClaudeCodeAdapter()
    cwd = Path("/workspace/empty-subagents")
    session_id = "11111111-1111-4111-8111-111111111111"
    main = _path(config, cwd, session_id)
    _write_main(main, cwd)
    subagents = main.parent / session_id / "subagents"
    subagents.mkdir(parents=True)

    assert adapter.locate_transcripts(cwd, session_id) == [main]

    worker = subagents / "agent-first.jsonl"
    worker.write_text('{"type":"assistant"}\n', encoding="utf-8")

    assert adapter.locate_transcripts(cwd, session_id) == [main, worker]


def test_new_nested_subagent_directory_is_observed(config: Path) -> None:
    adapter = ClaudeCodeAdapter()
    cwd = Path("/workspace/nested-worker")
    session_id = "22222222-2222-4222-8222-222222222222"
    main = _path(config, cwd, session_id)
    _write_main(main, cwd)
    (main.parent / session_id / "subagents").mkdir(parents=True)
    assert adapter.locate_transcripts(cwd, session_id) == [main]

    worker = main.parent / session_id / "subagents" / "workflows" / "wf-1" / "agent-nested.jsonl"
    worker.parent.mkdir(parents=True)
    worker.write_text('{"type":"assistant"}\n', encoding="utf-8")

    assert worker in adapter.locate_transcripts(cwd, session_id)


def test_worker_appearing_in_one_timestamp_tick_is_not_lost(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A coarse parent timestamp cannot make an unseen worker permanent."""
    adapter = ClaudeCodeAdapter()
    cwd = Path("/workspace/coarse-time")
    session_id = "abababab-abab-4bab-8bab-abababababab"
    main = _path(config, cwd, session_id)
    _write_main(main, cwd)
    subagents = main.parent / session_id / "subagents"
    subagents.mkdir(parents=True)
    assert adapter.locate_transcripts(cwd, session_id) == [main]

    original_stat = Path.stat
    frozen = subagents.stat().st_mtime_ns

    def coarse_stat(path: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        result = original_stat(path, *args, **kwargs)
        if path == subagents:
            return os.stat_result(
                (
                    result.st_mode,
                    result.st_ino,
                    result.st_dev,
                    result.st_nlink,
                    result.st_uid,
                    result.st_gid,
                    result.st_size,
                    result.st_atime,
                    result.st_mtime,
                    result.st_ctime,
                )
            )
        return result

    # The implementation must list the known subagent subtree rather than trust
    # this metadata. The forced coarse value makes a timestamp-only cache pass
    # despite the worker becoming visible.
    del frozen
    monkeypatch.setattr(Path, "stat", coarse_stat)
    worker = subagents / "agent-tick.jsonl"
    worker.write_text('{"type":"assistant"}\n', encoding="utf-8")
    assert worker in adapter.locate_transcripts(cwd, session_id)


def test_missing_projects_root_can_materialize_after_an_empty_lookup(config: Path) -> None:
    adapter = ClaudeCodeAdapter()
    cwd = Path("/workspace/late-root")
    session_id = "33333333-3333-4333-8333-333333333333"

    assert adapter.locate_transcripts(cwd, session_id) == []

    main = _path(config, cwd, session_id)
    _write_main(main, cwd)

    assert adapter.locate_transcripts(cwd, session_id) == [main]


def test_main_materializing_in_an_already_existing_directory_is_found(config: Path) -> None:
    """A session's first file cannot be cached as absent inside an existing root.

    Unlike the late-root case above, the encoded project directory (and the
    tracked ``projects/`` root above it) already exist before the main
    transcript is written. Neither directory's own metadata changes when a new
    file appears one level below the root, so a cached empty answer would have
    no dependency left to invalidate it and would stand for the whole
    revalidation window regardless of when the file actually appeared.
    """
    adapter = ClaudeCodeAdapter()
    cwd = Path("/workspace/existing-dir")
    session_id = "cdcdcdcd-cdcd-4cdc-8cdc-cdcdcdcdcdcd"
    project_dir = config / "projects" / _ClaudeHome.encode_cwd(cwd)
    project_dir.mkdir(parents=True)

    assert adapter.locate_transcripts(cwd, session_id) == []

    main = project_dir / f"{session_id}.jsonl"
    _write_main(main, cwd)

    # Immediately after, inside the SAME revalidation window as the first
    # (empty) lookup — this must not read the stale cached empty answer.
    assert adapter.locate_transcripts(cwd, session_id) == [main]


def test_relocated_main_is_found_without_rewalking_unrelated_projects(config: Path) -> None:
    adapter = ClaudeCodeAdapter()
    cwd = Path("/workspace/relocated")
    session_id = "44444444-4444-4444-8444-444444444444"
    source = _path(config, cwd, session_id, folder="old-location")
    _write_main(source, cwd)
    destination_dir = config / "projects" / "new-location"
    destination_dir.mkdir()
    for number in range(48):
        (config / "projects" / f"unrelated-{number}").mkdir()

    assert adapter.locate_transcripts(cwd, session_id) == [source]

    # A rename mutates both known holding directories. Correct incremental
    # tracking must locate the new home immediately, not wait for a timed sweep.
    destination = destination_dir / source.name
    source.rename(destination)
    assert adapter.locate_transcripts(cwd, session_id) == [destination]

    lookups = 0
    real_glob = Path.glob

    def count_project_globs(path: Path, pattern: str):
        nonlocal lookups
        if path == config / "projects":
            lookups += 1
        return real_glob(path, pattern)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(Path, "glob", count_project_globs)
    try:
        for _ in range(12):
            assert adapter.locate_transcripts(cwd, session_id) == [destination]
    finally:
        monkeypatch.undo()

    # A warm lookup lists one known subagent subtree, not every project folder.
    assert lookups == 0


def test_profile_scope_is_part_of_the_discovery_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    adapter = ClaudeCodeAdapter()
    cwd = Path("/workspace/profile")
    session_id = "55555555-5555-4555-8555-555555555555"
    first = tmp_path / "one"
    second = tmp_path / "two"

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(first))
    first_main = _path(first, cwd, session_id)
    _write_main(first_main, cwd)
    assert adapter.locate_transcripts(cwd, session_id) == [first_main]

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(second))
    second_main = _path(second, cwd, session_id)
    _write_main(second_main, cwd)
    assert adapter.locate_transcripts(cwd, session_id) == [second_main]


def test_parallel_location_reads_share_a_synchronized_index(config: Path) -> None:
    adapter = ClaudeCodeAdapter()
    cwd = Path("/workspace/concurrent")
    session_id = "66666666-6666-4666-8666-666666666666"
    main = _path(config, cwd, session_id)
    _write_main(main, cwd)
    worker = main.parent / session_id / "subagents" / "agent-live.jsonl"
    worker.parent.mkdir(parents=True)
    worker.write_text('{"type":"assistant"}\n', encoding="utf-8")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda _: adapter.locate_transcripts(cwd, session_id),
                range(48),
            )
        )

    assert all(result == [main, worker] for result in results)


def test_clear_caches_forces_a_fresh_location_scan(config: Path) -> None:
    adapter = ClaudeCodeAdapter()
    cwd = Path("/workspace/clear")
    session_id = "77777777-7777-4777-8777-777777777777"
    main = _path(config, cwd, session_id)
    _write_main(main, cwd)
    assert adapter.locate_transcripts(cwd, session_id) == [main]

    adapter.clear_caches()
    scans = 0
    real = _ClaudeHome.locate

    def count_locates(scan_cwd: Path, scan_session_id: str) -> list[Path]:
        nonlocal scans
        scans += 1
        return real(scan_cwd, scan_session_id)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(_ClaudeHome, "locate", staticmethod(count_locates))
    try:
        assert adapter.locate_transcripts(cwd, session_id) == [main]
    finally:
        monkeypatch.undo()

    assert scans == 1
