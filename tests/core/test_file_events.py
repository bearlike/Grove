"""Shared native filesystem watches stay bounded and recover visibly."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from grove.core.file_events import (
    FileEvent,
    FileEventBatch,
    FileEventKind,
    FileEventRecovery,
    FileEventRecoveryReason,
    FileEventSource,
)


class _WatchFactory:
    def __init__(self, watches: list[AsyncIterator[set[tuple[int, str]]]]) -> None:
        self._watches = iter(watches)
        self.calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    def __call__(self, *paths: str, **kwargs: Any) -> AsyncIterator[set[tuple[int, str]]]:
        self.calls.append((paths, kwargs))
        return next(self._watches)


async def _changes(*batches: set[tuple[int, str]]) -> AsyncIterator[set[tuple[int, str]]]:
    for batch in batches:
        yield batch
    await asyncio.Event().wait()


async def _fails(error: Exception) -> AsyncIterator[set[tuple[int, str]]]:
    raise error
    yield set()


@pytest.mark.asyncio
async def test_roots_are_canonicalized_and_parent_watched_without_polling(tmp_path: Path) -> None:
    watched = tmp_path / "transcript.jsonl"
    watched.write_text("")
    events: list[FileEventBatch] = []
    factory = _WatchFactory([_changes({(2, str(watched))})])
    source = FileEventSource(
        [watched, tmp_path / "." / watched.name],
        events.append,
        debounce_ms=100,
        max_age_ms=500,
        watcher=factory,
    )

    await source.start()
    await _wait_for(lambda: len(events) == 1)
    await source.aclose()

    assert source.roots == (watched.resolve(),)
    assert factory.calls[0][0] == (str(tmp_path.resolve()),)
    assert factory.calls[0][1] == {
        "force_polling": False,
        "recursive": False,
        "step": 100,
        "debounce": 500,
        "yield_on_timeout": True,
        "rust_timeout": 1000,
    }
    assert events == [FileEventBatch((FileEvent(FileEventKind.MODIFIED, watched.resolve()),))]


@pytest.mark.asyncio
async def test_explicit_directory_root_filters_by_containment_and_can_opt_into_recursion(
    tmp_path: Path,
) -> None:
    watched = tmp_path / "transcripts"
    watched.mkdir()
    changed = watched / "nested" / "turn.jsonl"
    outside = tmp_path / "outside.jsonl"
    events: list[FileEventBatch] = []
    factory = _WatchFactory([_changes({(2, str(changed)), (2, str(outside))})])
    source = FileEventSource([watched], events.append, recursive=True, watcher=factory)

    await source.start()
    await _wait_for(lambda: len(events) == 1)
    await source.aclose()

    assert source.roots == (watched.resolve(),)
    assert factory.calls[0][0] == (str(watched.resolve()),)
    assert factory.calls[0][1]["recursive"] is True
    assert events == [FileEventBatch((FileEvent(FileEventKind.MODIFIED, changed.resolve()),))]


@pytest.mark.asyncio
async def test_a_root_that_does_not_exist_yet_admits_its_descendants(tmp_path: Path) -> None:
    """A PROFILE DIRECTORY THAT DOES NOT EXIST YET IS THE ORDINARY CASE.

    A provider creates ``<config>/projects`` on its FIRST session, routinely
    after the daemon started. Classification happens once at construction, so an
    absent root was filed as a FILE root forever — and file containment is exact
    equality, so every transcript later written beneath it was rejected. On a
    fresh profile the catalog therefore never saw a session again until restart,
    which is the case a watch on that root exists to serve.

    The nearest existing ancestor is watched either way; only what is ADMITTED
    changes, and a root that is genuinely a file still matches only itself.
    """
    absent = tmp_path / "config" / "projects"
    (tmp_path / "config").mkdir()
    transcript = absent / "proj" / "session.jsonl"
    outside = tmp_path / "config" / "elsewhere.jsonl"
    events: list[FileEventBatch] = []
    factory = _WatchFactory([_changes({(2, str(transcript)), (2, str(outside))})])
    source = FileEventSource([absent], events.append, recursive=True, watcher=factory)

    await source.start()
    await _wait_for(lambda: len(events) == 1)
    await source.aclose()

    # The ancestor is what can be armed, but containment still filters on the
    # real root — a sibling of the absent directory must not be admitted.
    assert factory.calls[0][0] == (str((tmp_path / "config").resolve()),)
    assert events == [FileEventBatch((FileEvent(FileEventKind.MODIFIED, transcript.resolve()),))]


@pytest.mark.asyncio
async def test_large_paths_are_rejected_before_batch_tuple_allocation(tmp_path: Path) -> None:
    watched = tmp_path / "transcript.jsonl"
    watched.write_text("")
    recoveries: list[FileEventRecovery] = []
    source = FileEventSource(
        [watched],
        lambda _: None,
        on_recovery=recoveries.append,
        max_batch_bytes=1,
        watcher=_WatchFactory([_changes({(2, str(watched))})]),
    )

    await source.start()
    await _wait_for(lambda: len(recoveries) == 1)
    await source.aclose()

    assert recoveries == [
        FileEventRecovery(FileEventRecoveryReason.OVERLOADED, reconnects_remaining=2)
    ]


@pytest.mark.asyncio
async def test_raw_changes_split_before_allocating_a_batch_larger_than_configured(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.jsonl"
    first.write_text("")
    second = tmp_path / "second.jsonl"
    second.write_text("")
    events: list[FileEventBatch] = []
    source = FileEventSource(
        [first, second],
        events.append,
        max_batch_items=1,
        watcher=_WatchFactory([_changes({(2, str(first)), (1, str(second))})]),
    )

    await source.start()
    await _wait_for(lambda: len(events) == 2)
    await source.aclose()

    assert {batch.events[0].path for batch in events} == {first.resolve(), second.resolve()}
    assert all(len(batch.events) == 1 for batch in events)


@pytest.mark.asyncio
async def test_watcher_errors_emit_a_rescan_signal_then_use_only_the_reconnect_budget(
    tmp_path: Path,
) -> None:
    watched = tmp_path / "transcript.jsonl"
    recoveries: list[FileEventRecovery] = []
    factory = _WatchFactory([_fails(RuntimeError("watch queue overflow")), _fails(OSError("gone"))])
    source = FileEventSource(
        [watched],
        lambda _: None,
        on_recovery=recoveries.append,
        max_reconnects=1,
        reconnect_delay_seconds=0,
        watcher=factory,
    )

    await source.start()
    await _wait_for(lambda: source.stopped)
    await source.aclose()

    assert len(factory.calls) == 2
    assert [recovery.reason for recovery in recoveries] == [
        FileEventRecoveryReason.WATCHER_FAILURE,
        FileEventRecoveryReason.WATCHER_FAILURE,
    ]
    assert [recovery.reconnects_remaining for recovery in recoveries] == [1, 0]


@pytest.mark.asyncio
async def test_close_is_idempotent_and_never_starts_a_second_watcher(tmp_path: Path) -> None:
    watched = tmp_path / "transcript.jsonl"
    factory = _WatchFactory([_changes()])
    source = FileEventSource([watched], lambda _: None, watcher=factory)

    await source.start()
    await _wait_for(lambda: len(factory.calls) == 1)
    await source.start()
    await source.aclose()
    await source.aclose()

    assert len(factory.calls) == 1
    assert source.closed is True


@pytest.mark.parametrize(
    ("keyword", "value"),
    [
        ("max_batch_items", 0),
        ("max_batch_bytes", 0),
        ("debounce_ms", 0),
        ("max_age_ms", 0),
        ("max_reconnects", -1),
        ("reconnect_delay_seconds", -1),
    ],
)
def test_invalid_knobs_are_refused_before_startup(tmp_path: Path, keyword: str, value: int) -> None:
    with pytest.raises(ValueError):
        FileEventSource([tmp_path / "watch"], lambda _: None, **{keyword: value})


async def _wait_for(predicate: Any) -> None:
    for _ in range(20):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition did not become true")
