"""Pure-unit coverage for the incremental transcript cache (daemon-CPU fix).

The cache's contract: a path-set's records are folded exactly once per line
ever (append-only steady state), a truncated/rotated/vanished file resets its
state, a trailing partial line is deferred until complete, and the LRU byte
budget bounds memory. The counting folder pins "no byte is parsed twice" —
the invariant that collapsed the daemon's per-tick cost from O(history) to
O(delta).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from grove.core.agents.transcript_cache import ResultMemo, TranscriptCache


class _CountingFolder:
    """A trivial appender that counts ``add`` calls — the re-parse detector.

    It also records the ``source`` each line arrived under. A folder that
    merges by a provider id relies on that being the file the line was read
    from (Claude's split-block merge is scoped by it), and a cache handing over
    the wrong path would otherwise be exactly as green as one handing over the
    right one.
    """

    instances: ClassVar[list[_CountingFolder]] = []

    def __init__(self) -> None:
        self._records: list[dict] = []
        self.sources: list[str] = []
        self.adds = 0
        _CountingFolder.instances.append(self)

    def add(self, raw: dict, source: str) -> None:
        self.adds += 1
        self._records.append(raw)
        self.sources.append(source)

    def records(self) -> list[dict]:
        return self._records


def _cache(**kwargs: int) -> TranscriptCache:
    _CountingFolder.instances = []
    return TranscriptCache(_CountingFolder, **kwargs)


def _write_lines(path: Path, objs: list[dict], *, trailing_partial: str = "") -> None:
    payload = "".join(json.dumps(o) + "\n" for o in objs) + trailing_partial
    path.write_text(payload, encoding="utf-8")


def _append_lines(path: Path, objs: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        for o in objs:
            fh.write(json.dumps(o) + "\n")


def test_append_only_growth_parses_each_line_once(tmp_path: Path) -> None:
    cache = _cache()
    f = tmp_path / "t.jsonl"
    _write_lines(f, [{"n": 1}, {"n": 2}])

    first = cache.read([f])
    assert [r["n"] for r in first] == [1, 2]

    _append_lines(f, [{"n": 3}])
    second = cache.read([f])
    assert [r["n"] for r in second] == [1, 2, 3]

    # One folder, 3 adds total: the prefix was never re-parsed.
    assert len(_CountingFolder.instances) == 1
    assert _CountingFolder.instances[0].adds == 3


def test_each_line_is_folded_under_the_file_it_came_from(tmp_path: Path) -> None:
    """Lines from one file share a ``source``; lines from another do not.

    Claude's fold scopes its split-block merge key by exactly this value — a
    same-id line arriving from a SECOND file is another thread's replay, not a
    continuation — so a cache that passed a constant, the first path, or the
    path-set key would silently re-merge across files and duplicate content
    blocks. Nothing else in this module would notice.

    Asserted as a partition rather than against a literal, because the value is
    a file IDENTITY and not a path string; pinning the representation would
    make the test fail for a correct change.
    """
    cache = _cache()
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    _write_lines(a, [{"n": 1}, {"n": 2}])
    _write_lines(b, [{"n": 3}])

    cache.read([a, b])

    sources = _CountingFolder.instances[0].sources
    assert len(sources) == 3
    assert sources[0] == sources[1]  # both lines of a.jsonl
    assert sources[2] != sources[0]  # b.jsonl is a different source


def test_one_file_reached_through_two_paths_is_one_source(tmp_path: Path) -> None:
    """A transcript reachable under two names must fold as ONE source.

    ``_ClaudeHome.locate`` globs each project directory and de-dupes its hits
    lexically, so a symlinked directory yields the same inode under two
    distinct strings. Keyed by string, those aliases are two sources — and the
    cross-file scope that exists to stop a replay merging would instead be the
    thing that duplicates it, for any record the global uuid guard cannot drop.
    """
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    transcript = real_dir / "t.jsonl"
    _write_lines(transcript, [{"n": 1}])
    alias_dir = tmp_path / "alias"
    alias_dir.symlink_to(real_dir, target_is_directory=True)

    cache = _cache()
    cache.read([transcript, alias_dir / "t.jsonl"])

    assert len(set(_CountingFolder.instances[0].sources)) == 1


def test_unchanged_file_costs_no_adds(tmp_path: Path) -> None:
    cache = _cache()
    f = tmp_path / "t.jsonl"
    _write_lines(f, [{"n": 1}])
    cache.read([f])
    cache.read([f])
    assert _CountingFolder.instances[0].adds == 1


def test_returned_list_is_a_snapshot_copy(tmp_path: Path) -> None:
    cache = _cache()
    f = tmp_path / "t.jsonl"
    _write_lines(f, [{"n": 1}])
    out = cache.read([f])
    out.append({"n": "junk"})
    assert [r["n"] for r in cache.read([f])] == [1]


def test_truncation_resets_state(tmp_path: Path) -> None:
    cache = _cache()
    f = tmp_path / "t.jsonl"
    _write_lines(f, [{"n": 1}, {"n": 2}])
    cache.read([f])

    _write_lines(f, [{"n": 9}])  # shorter: truncated/rewritten
    out = cache.read([f])
    assert [r["n"] for r in out] == [9]
    # A fresh folder was built for the reset state.
    assert len(_CountingFolder.instances) == 2


def test_partial_trailing_line_is_deferred_until_complete(tmp_path: Path) -> None:
    cache = _cache()
    f = tmp_path / "t.jsonl"
    _write_lines(f, [{"n": 1}], trailing_partial='{"n": 2')

    assert [r["n"] for r in cache.read([f])] == [1]

    with f.open("a", encoding="utf-8") as fh:
        fh.write("}\n")
    assert [r["n"] for r in cache.read([f])] == [1, 2]
    # The completed line folded exactly once.
    assert _CountingFolder.instances[0].adds == 2


def test_bad_lines_and_blanks_are_skipped(tmp_path: Path) -> None:
    cache = _cache()
    f = tmp_path / "t.jsonl"
    f.write_text('{"n": 1}\n\nnot json\n[1, 2]\n{"n": 2}\n', encoding="utf-8")
    assert [r["n"] for r in cache.read([f])] == [1, 2]


def test_missing_file_yields_nothing_then_appears(tmp_path: Path) -> None:
    cache = _cache()
    f = tmp_path / "t.jsonl"
    assert cache.read([f]) == []
    _write_lines(f, [{"n": 1}])
    assert [r["n"] for r in cache.read([f])] == [1]


def test_vanished_file_resets_state(tmp_path: Path) -> None:
    cache = _cache()
    f = tmp_path / "t.jsonl"
    _write_lines(f, [{"n": 1}])
    cache.read([f])
    f.unlink()
    assert cache.read([f]) == []


def test_multi_path_sets_are_independent(tmp_path: Path) -> None:
    cache = _cache()
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    _write_lines(a, [{"n": 1}])
    _write_lines(b, [{"n": 2}])
    assert [r["n"] for r in cache.read([a])] == [1]
    assert [r["n"] for r in cache.read([a, b])] == [1, 2]
    # Two distinct states (one per path tuple), each folded independently.
    assert len(_CountingFolder.instances) == 2


def test_byte_budget_evicts_least_recently_used(tmp_path: Path) -> None:
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    _write_lines(a, [{"n": 1}])
    _write_lines(b, [{"n": 2}])
    # Budget below the combined source size: reading b must evict a's state.
    budget = a.stat().st_size + b.stat().st_size - 1
    cache = _cache(max_source_bytes=budget)
    cache.read([a])
    cache.read([b])
    cache.read([a])  # rebuilt after eviction
    assert len(_CountingFolder.instances) == 3


def test_clear_resets_everything(tmp_path: Path) -> None:
    cache = _cache()
    f = tmp_path / "t.jsonl"
    _write_lines(f, [{"n": 1}])
    cache.read([f])
    cache.clear()
    cache.read([f])
    assert len(_CountingFolder.instances) == 2


# ─── ResultMemo ──────────────────────────────────────────────────────────────


def test_memo_hits_on_unchanged_stats(tmp_path: Path) -> None:
    memo = ResultMemo()
    f = tmp_path / "t.jsonl"
    _write_lines(f, [{"n": 1}])
    calls = 0

    def compute() -> object:
        nonlocal calls
        calls += 1
        return object()

    first = memo.get_or_compute(("k",), [f], compute)
    second = memo.get_or_compute(("k",), [f], compute)
    assert first is second
    assert calls == 1


def test_memo_recomputes_when_file_changes(tmp_path: Path) -> None:
    memo = ResultMemo()
    f = tmp_path / "t.jsonl"
    _write_lines(f, [{"n": 1}])
    memo.get_or_compute(("k",), [f], lambda: 1)
    _append_lines(f, [{"n": 2}])
    assert memo.get_or_compute(("k",), [f], lambda: 2) == 2


def test_memo_recomputes_when_path_set_changes(tmp_path: Path) -> None:
    memo = ResultMemo()
    f = tmp_path / "t.jsonl"
    # No transcript yet: the empty path set memoizes...
    assert memo.get_or_compute(("k",), [], lambda: "empty") == "empty"
    # ...but the file appearing changes the signature, so it recomputes.
    _write_lines(f, [{"n": 1}])
    assert memo.get_or_compute(("k",), [f], lambda: "full") == "full"


def test_memo_bounded_by_maxsize(tmp_path: Path) -> None:
    memo = ResultMemo(maxsize=2)
    f = tmp_path / "t.jsonl"
    _write_lines(f, [{"n": 1}])
    calls = 0

    def compute() -> int:
        nonlocal calls
        calls += 1
        return calls

    memo.get_or_compute(("a",), [f], compute)
    memo.get_or_compute(("b",), [f], compute)
    memo.get_or_compute(("c",), [f], compute)  # evicts ("a",)
    assert memo.get_or_compute(("a",), [f], compute) == 4
