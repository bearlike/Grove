"""Event-owned catalog and gallery memo indexes.

The host catalog head-reads every discovered session and walks ``/proc``; the
gallery adds one ``git ls-files`` per worktree. These tests pin the replacement
for their former TTL: one bootstrap read, then only explicit event mutations or
explicitly requested reconciliation can make either full scan happen. The
counting stub keeps the parse-fact worker hermetic while retaining its
single-flight contract.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core.agents import SessionRef
from grove.core.gallery import GalleryItem
from grove.core.process import LiveRuntime
from grove.core.sessions import CatalogEntry
from grove.daemon._catalog import _CatalogMemo, _GalleryMemo


def _entry(
    session_id: str,
    *,
    kind: str = "claude_code",
    cwd: str | None = "/w",
    path: Path | None = None,
    mtime: float = 1.0,
) -> CatalogEntry:
    return CatalogEntry(
        ref=SessionRef(
            session_id=session_id,
            adapter_kind=kind,
            cwd=cwd,
            transcript_path=path or Path(f"/tmp/{session_id}.jsonl"),
            birth=datetime(2026, 7, 27, tzinfo=UTC),
            mtime=mtime,
        ),
        provenance="fs_discovered",
        project=None,
    )


class _CountingCatalog:
    """Records whole scans and count passes the memo lets through."""

    def __init__(self, rows: tuple[CatalogEntry, ...]) -> None:
        self.rows = rows
        self.scans = 0
        self.counted = 0
        self.release = threading.Event()
        self.entered = threading.Event()
        self.finished = threading.Event()

    def scan(self, *, limit: int | None = None) -> tuple[CatalogEntry, ...]:
        self.scans += 1
        return self.rows if limit is None else self.rows[:limit]

    def count_turns(
        self, entries: Sequence[CatalogEntry], *, stop: Callable[[], bool] | None = None
    ) -> int:
        del entries, stop
        self.counted += 1
        self.entered.set()
        self.release.wait(timeout=5)
        self.finished.set()
        return 1


class _CountingGallery:
    def __init__(self, items: tuple[GalleryItem, ...]) -> None:
        self.items_to_return = items
        self.scans = 0
        self.scanned_rows: list[tuple[CatalogEntry, ...]] = []

    def scan(self, rows: tuple[CatalogEntry, ...]) -> tuple[GalleryItem, ...]:
        self.scans += 1
        self.scanned_rows.append(rows)
        return self.items_to_return


def _item(item_id: str, *, path: Path, modified_at: datetime | None = None) -> GalleryItem:
    return GalleryItem(
        id=item_id,
        path=path,
        relative_path=path.name,
        repo_root=Path("/repo"),
        repo_name="repo",
        worktree_path=Path("/repo"),
        size_bytes=1,
        modified_at=modified_at or datetime(2026, 7, 27, tzinfo=UTC),
        digest="0" * 64,
        pages=1,
    )


def _memo(catalog: _CountingCatalog) -> _CatalogMemo:
    return _CatalogMemo(catalog)  # type: ignore[arg-type]


def test_reads_bootstrap_once_and_never_expire_on_elapsed_time() -> None:
    catalog = _CountingCatalog((_entry("a"), _entry("b")))
    memo = _memo(catalog)

    assert [entry.ref.session_id for entry in memo.rows()] == ["a", "b"]
    assert memo.find(kind="claude_code", cwd="/w", session_id="b") is not None
    assert memo.rows() == memo.rows()

    assert catalog.scans == 1
    assert memo.generation == 1


def test_known_session_append_or_change_updates_only_that_record() -> None:
    catalog = _CountingCatalog((_entry("a"),))
    memo = _memo(catalog)
    memo.rows()

    changed = _entry("a", mtime=9.0)
    appended = _entry("b", mtime=10.0)
    assert memo.invalidate(entry=changed) is True
    assert memo.invalidate(entry=appended) is True

    rows = memo.rows()
    assert [(row.ref.session_id, row.ref.mtime) for row in rows] == [("b", 10.0), ("a", 9.0)]
    assert catalog.scans == 1
    assert memo.generation == 3


def test_known_session_deletion_by_path_removes_only_that_record() -> None:
    first = _entry("a", path=Path("/tmp/a.jsonl"))
    second = _entry("b", path=Path("/tmp/b.jsonl"))
    catalog = _CountingCatalog((first, second))
    memo = _memo(catalog)
    memo.rows()

    assert memo.invalidate(path=Path("/tmp/a.jsonl")) is True
    assert [row.ref.session_id for row in memo.rows()] == ["b"]
    assert catalog.scans == 1


def test_unknown_file_event_explicitly_invalidates_then_next_read_reconciles() -> None:
    catalog = _CountingCatalog((_entry("a"),))
    memo = _memo(catalog)
    memo.rows()
    catalog.rows = (_entry("b", mtime=2.0), _entry("a"))

    memo.on_file_events(object())
    assert catalog.scans == 1  # an edge does not perform I/O on its callback thread
    assert [row.ref.session_id for row in memo.rows()] == ["b", "a"]
    assert catalog.scans == 2


def test_reconcile_is_an_explicit_whole_scan() -> None:
    catalog = _CountingCatalog((_entry("a"),))
    memo = _memo(catalog)
    memo.rows()
    catalog.rows = (_entry("b"),)

    assert [row.ref.session_id for row in memo.reconcile()] == ["b"]
    assert catalog.scans == 2


def test_find_matches_on_all_three_coordinates() -> None:
    catalog = _CountingCatalog(
        (
            _entry("dup", kind="claude_code", cwd="/w"),
            _entry("dup", kind="codex", cwd="/other"),
        )
    )
    memo = _memo(catalog)

    hit = memo.find(kind="codex", cwd="/other", session_id="dup")
    assert hit is not None
    assert hit.ref.adapter_kind == "codex"
    assert memo.find(kind="codex", cwd="/w", session_id="dup") is None
    assert memo.find(kind="claude_code", cwd="/w", session_id="nope") is None


def test_find_never_matches_a_row_that_recorded_no_cwd() -> None:
    catalog = _CountingCatalog((replace(_entry("a"), ref=_entry("a", cwd=None).ref),))
    memo = _memo(catalog)

    assert memo.find(kind="claude_code", cwd="/w", session_id="a") is None


def test_gallery_rejoins_when_catalog_generation_changes() -> None:
    catalog = _CountingCatalog((_entry("a"),))
    sessions = _memo(catalog)
    first = _item("a", path=Path("/repo/first.drawio"))
    gallery_source = _CountingGallery((first,))
    gallery = _GalleryMemo(gallery_source, sessions)  # type: ignore[arg-type]

    assert gallery.items() == (first,)
    sessions.invalidate(entry=_entry("b", mtime=2.0))
    assert gallery.items() == (first,)

    assert catalog.scans == 1
    assert gallery_source.scans == 2
    assert [row.ref.session_id for row in gallery_source.scanned_rows[-1]] == ["b", "a"]


def test_known_diagram_update_and_deletion_change_only_that_item() -> None:
    catalog = _CountingCatalog((_entry("a"),))
    sessions = _memo(catalog)
    first = _item("a", path=Path("/repo/first.drawio"))
    second = _item(
        "b",
        path=Path("/repo/second.drawio"),
        modified_at=datetime(2026, 7, 28, tzinfo=UTC),
    )
    gallery_source = _CountingGallery((first,))
    gallery = _GalleryMemo(gallery_source, sessions)  # type: ignore[arg-type]
    gallery.items()

    assert gallery.invalidate(item=second) is True
    assert gallery.invalidate(path=first.path) is True
    assert [item.id for item in gallery.items()] == ["b"]
    assert gallery_source.scans == 1


def test_gallery_file_event_invalidates_without_scanning_on_callback_thread() -> None:
    catalog = _CountingCatalog((_entry("a"),))
    sessions = _memo(catalog)
    first = _item("a", path=Path("/repo/first.drawio"))
    second = _item("b", path=Path("/repo/second.drawio"))
    gallery_source = _CountingGallery((first,))
    gallery = _GalleryMemo(gallery_source, sessions)  # type: ignore[arg-type]
    gallery.items()
    gallery_source.items_to_return = (second,)

    gallery.on_file_events(object())
    assert gallery_source.scans == 1
    assert gallery.items() == (second,)
    assert gallery_source.scans == 2


def test_the_turn_count_pass_is_single_flight_and_never_blocks_the_scan() -> None:
    catalog = _CountingCatalog((_entry("a"),))
    memo = _memo(catalog)
    try:
        memo.rows()
        memo.count_turns_in_background()
        assert catalog.entered.wait(timeout=5)
        memo.count_turns_in_background()
        memo.count_turns_in_background()
        assert catalog.counted == 1
    finally:
        catalog.release.set()
        memo.close()


def test_changed_session_defers_its_next_turn_count_until_the_flight_finishes() -> None:
    catalog = _CountingCatalog((_entry("a"),))
    memo = _memo(catalog)
    try:
        memo.rows()
        memo.count_turns_in_background()
        assert catalog.entered.wait(timeout=5)
        memo.invalidate(entry=_entry("a", mtime=2.0))
        memo.count_turns_in_background()
        assert catalog.counted == 1

        catalog.release.set()
        assert memo._counting is not None
        memo._counting.result(timeout=5)
        memo.count_turns_in_background()
        assert memo._counting is not None
        memo._counting.result(timeout=5)
        assert catalog.counted == 2
    finally:
        catalog.release.set()
        memo.close()


def test_close_stops_scheduling_and_does_not_wait_for_a_pass_in_flight() -> None:
    catalog = _CountingCatalog((_entry("a"),))
    memo = _memo(catalog)
    memo.rows()

    memo.close()
    memo.count_turns_in_background()

    assert catalog.counted == 0
    catalog.release.set()


def test_nothing_is_scheduled_before_a_scan_has_produced_rows() -> None:
    catalog = _CountingCatalog(())
    memo = _memo(catalog)
    try:
        memo.count_turns_in_background()
        assert catalog.counted == 0
    finally:
        memo.close()


def test_liveness_is_refolded_per_read_not_frozen_at_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A process starting after bootstrap must still read as live.

    Liveness is the one field on a catalog row that is not a fact about a
    FILE, so no filesystem event can carry it: an agent starts and exits with
    nothing for the event sources to observe. A `live` bit cached at bootstrap
    therefore reported a RUNNING agent as dead for as long as nothing
    unrelated happened to touch its transcript — which on a quiet session is
    forever. Mutation-tested: caching the bit turns this red.
    """
    entry = _entry("s1", cwd="/w")
    catalog = _CountingCatalog((entry,))
    memo = _CatalogMemo(catalog)  # type: ignore[arg-type]
    running: list[LiveRuntime] = []
    monkeypatch.setattr("grove.core.process.list_agent_runtimes", lambda **_: tuple(running))
    monkeypatch.setattr(
        "grove.core.sessions.SessionCatalog.fold_liveness",
        staticmethod(
            lambda entries, runtimes, *, now: tuple(
                replace(e, live=any(r.cwd == Path(e.ref.cwd or "") for r in runtimes))
                for e in entries
            )
        ),
    )
    try:
        assert memo.rows()[0].live is False  # bootstrap: nothing running
        running.append(
            LiveRuntime(
                pid=1,
                kind="claude_code",
                cwd=Path("/w"),
                started_at=datetime(2026, 7, 27, tzinfo=UTC),
            )
        )
        # No file changed and no generation moved, so a cached bit stays false.
        assert memo.rows()[0].live is True
        assert catalog.scans == 1, "re-folding liveness must not cost another scan"
    finally:
        memo.close()
