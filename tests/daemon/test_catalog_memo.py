"""_CatalogMemo — the TTL that keeps the host catalog request-scoped.

The invariant these pin is a cost one: the catalog head-reads every session in
every adapter's store and walks ``/proc``, so a UI interaction (list, then
drill into a row) must cost ONE scan, and nothing may turn it into a poll.
A counting stub stands in for the catalog, so the memo's own behavior is tested
without any filesystem.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from grove.core.agents import SessionRef
from grove.core.sessions import CatalogEntry
from grove.daemon._catalog import _CatalogMemo


def _entry(session_id: str, *, kind: str = "claude_code", cwd: str | None = "/w") -> CatalogEntry:
    return CatalogEntry(
        ref=SessionRef(
            session_id=session_id,
            adapter_kind=kind,
            cwd=cwd,
            transcript_path=Path("/tmp/x.jsonl"),
            birth=datetime(2026, 7, 27, tzinfo=UTC),
            mtime=1.0,
        ),
        provenance="fs_discovered",
        project=None,
    )


class _CountingCatalog:
    """Records how many real scans the memo let through."""

    def __init__(self, rows: tuple[CatalogEntry, ...]) -> None:
        self.rows = rows
        self.scans = 0

    def scan(self, *, limit: int | None = None) -> tuple[CatalogEntry, ...]:
        self.scans += 1
        return self.rows if limit is None else self.rows[:limit]


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _memo(catalog: _CountingCatalog, clock: _Clock) -> _CatalogMemo:
    return _CatalogMemo(catalog, ttl_seconds=5.0, clock=clock)  # type: ignore[arg-type]


def test_repeat_reads_inside_the_ttl_cost_one_scan() -> None:
    catalog = _CountingCatalog((_entry("a"), _entry("b")))
    clock = _Clock()
    memo = _memo(catalog, clock)

    memo.rows()
    clock.now += 4.9
    memo.rows()
    memo.find(kind="claude_code", cwd="/w", session_id="b")

    assert catalog.scans == 1


def test_the_ttl_expires_so_a_new_session_shows_up() -> None:
    catalog = _CountingCatalog((_entry("a"),))
    clock = _Clock()
    memo = _memo(catalog, clock)
    assert [e.ref.session_id for e in memo.rows()] == ["a"]

    catalog.rows = (_entry("b"), _entry("a"))
    clock.now += 5.0
    assert [e.ref.session_id for e in memo.rows()] == ["b", "a"]
    assert catalog.scans == 2


def test_the_memo_holds_the_unbounded_scan_so_a_wider_request_is_answerable() -> None:
    """Memoizing per-``limit`` would answer a 200-row request from a 1-row
    cache, so the memo caches everything and the route slices."""
    catalog = _CountingCatalog(tuple(_entry(f"s{i}") for i in range(10)))
    memo = _memo(catalog, _Clock())

    assert len(memo.rows()) == 10
    assert catalog.scans == 1


def test_find_matches_on_all_three_coordinates() -> None:
    catalog = _CountingCatalog(
        (
            _entry("dup", kind="claude_code", cwd="/w"),
            _entry("dup", kind="codex", cwd="/other"),
        )
    )
    memo = _memo(catalog, _Clock())

    hit = memo.find(kind="codex", cwd="/other", session_id="dup")
    assert hit is not None
    assert hit.ref.adapter_kind == "codex"
    # Any single coordinate off is a miss, never the other row.
    assert memo.find(kind="codex", cwd="/w", session_id="dup") is None
    assert memo.find(kind="claude_code", cwd="/w", session_id="nope") is None


def test_find_never_matches_a_row_that_recorded_no_cwd() -> None:
    """~2 % of Claude transcripts never reveal a cwd; such a row lists but is
    not drillable, and must not be matched by some other row's coordinates."""
    catalog = _CountingCatalog((replace(_entry("a"), ref=_entry("a", cwd=None).ref),))
    memo = _memo(catalog, _Clock())

    assert memo.find(kind="claude_code", cwd="/w", session_id="a") is None
