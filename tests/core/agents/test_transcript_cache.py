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
import threading
import time
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import BaseModel

import grove.core.agents.transcript_cache as module
from grove.core.agents.transcript_cache import ResultMemo, TranscriptCache


class _Payload(BaseModel):
    body: str


class _ProjectionFolder:
    """Folder whose derived immutable snapshot appears only during projection."""

    instances: ClassVar[list[_ProjectionFolder]] = []

    def __init__(self) -> None:
        self._records: list[dict] = []
        _ProjectionFolder.instances.append(self)

    def add(self, raw: dict, source: str) -> dict:
        del source
        self._records.append(raw)
        return raw

    def records(self) -> list[dict]:
        return self._records


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

    def add(self, raw: dict, source: str) -> dict:
        self.adds += 1
        self._records.append(raw)
        self.sources.append(source)
        return raw

    def records(self) -> list[dict]:
        return self._records


def _cache(**kwargs: int) -> TranscriptCache:
    _CountingFolder.instances = []
    return TranscriptCache(_CountingFolder, **kwargs)


def _projection_cache(**kwargs: int) -> TranscriptCache:
    _ProjectionFolder.instances = []
    return TranscriptCache(_ProjectionFolder, **kwargs)


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


def test_retained_byte_budget_evicts_a_single_oversized_state(tmp_path: Path) -> None:
    """An oversized result is returned, but is not retained for the next read."""
    cache = _cache(max_retained_bytes=1)
    transcript = tmp_path / "t.jsonl"
    _write_lines(transcript, [{"body": "enough decoded data to exceed one byte"}])

    assert cache.read([transcript])
    assert cache.read([transcript])

    # A singleton used to survive forever because eviction required two states.
    assert len(_CountingFolder.instances) == 2


def test_retained_byte_budget_evicts_empty_states(tmp_path: Path) -> None:
    """Missing transcripts still consume state bookkeeping and cannot accumulate."""
    cache = _cache(max_retained_bytes=1)
    missing = tmp_path / "not-yet-created.jsonl"

    assert cache.read([missing]) == []
    assert cache.read([missing]) == []

    assert len(_CountingFolder.instances) == 2


def test_ingest_streams_complete_lines_without_reading_the_whole_delta(
    tmp_path: Path, monkeypatch: object
) -> None:
    """The append path may read a bounded prefix, never an unbounded delta."""
    transcript = tmp_path / "t.jsonl"
    _write_lines(transcript, [{"n": number} for number in range(3)])
    original_open = Path.open

    class _Reader:
        def __init__(self, wrapped: object) -> None:
            self._wrapped = wrapped

        def __enter__(self) -> object:
            self._wrapped.__enter__()  # type: ignore[union-attr]
            return self

        def __exit__(self, *args: object) -> None:
            self._wrapped.__exit__(*args)  # type: ignore[union-attr]

        def read(self, size: int = -1) -> bytes:
            assert size >= 0, "incremental ingest must not read an entire delta at once"
            return self._wrapped.read(size)  # type: ignore[union-attr,no-any-return]

        def readline(self, size: int = -1) -> bytes:
            assert size == -1, "a line must not be split before its newline"
            return self._wrapped.readline(size)  # type: ignore[union-attr,no-any-return]

        def seek(self, offset: int) -> int:
            return self._wrapped.seek(offset)  # type: ignore[union-attr,no-any-return]

    def guarded_open(path: Path, *args: object, **kwargs: object) -> _Reader:
        return _Reader(original_open(path, *args, **kwargs))  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", guarded_open)  # type: ignore[union-attr]
    assert [record["n"] for record in _cache().read([transcript])] == [0, 1, 2]


def test_cache_rejects_nonpositive_budgets() -> None:
    with pytest.raises(ValueError, match="max_retained_bytes"):
        _cache(max_retained_bytes=0)
    with pytest.raises(ValueError, match="max_source_bytes"):
        _cache(max_source_bytes=-1)
    with pytest.raises(ValueError, match="max_bytes"):
        ResultMemo(max_bytes=0)
    with pytest.raises(ValueError, match="maxsize"):
        ResultMemo(maxsize=-1)


def test_configure_evicts_existing_oversized_state(tmp_path: Path) -> None:
    cache = _cache()
    transcript = tmp_path / "t.jsonl"
    _write_lines(transcript, [{"n": 1}])
    cache.read([transcript])

    cache.configure(max_retained_bytes=1)
    cache.read([transcript])

    assert len(_CountingFolder.instances) == 2


def test_memo_configure_evicts_existing_entries(tmp_path: Path) -> None:
    memo = ResultMemo()
    transcript = tmp_path / "t.jsonl"
    _write_lines(transcript, [{"n": 1}])
    calls = 0

    def compute() -> object:
        nonlocal calls
        calls += 1
        return object()

    memo.get_or_compute(("k",), [transcript], compute)
    memo.configure(max_bytes=1)
    memo.get_or_compute(("k",), [transcript], compute)
    assert calls == 2


def test_memo_does_not_retain_a_value_larger_than_its_byte_budget(tmp_path: Path) -> None:
    memo = ResultMemo(max_bytes=1)
    transcript = tmp_path / "t.jsonl"
    _write_lines(transcript, [{"n": 1}])
    calls = 0

    def compute() -> object:
        nonlocal calls
        calls += 1
        return object()

    memo.get_or_compute(("large",), [transcript], compute)
    memo.get_or_compute(("large",), [transcript], compute)
    assert calls == 2


def test_memo_evicts_a_smallest_key_when_values_exceed_its_byte_budget(tmp_path: Path) -> None:
    memo = ResultMemo(maxsize=2, max_bytes=200)
    transcript = tmp_path / "t.jsonl"
    _write_lines(transcript, [{"n": 1}])
    calls = 0

    def compute() -> str:
        nonlocal calls
        calls += 1
        return "x" * 80

    memo.get_or_compute(("a",), [transcript], compute)
    memo.get_or_compute(("b",), [transcript], compute)
    memo.get_or_compute(("a",), [transcript], compute)
    assert calls == 3


def test_memo_counts_a_pydantic_payload_beyond_its_object_shell(tmp_path: Path) -> None:
    memo = ResultMemo(max_bytes=2_048)
    transcript = tmp_path / "t.jsonl"
    _write_lines(transcript, [{"n": 1}])
    calls = 0

    def compute() -> _Payload:
        nonlocal calls
        calls += 1
        return _Payload(body="x" * 4_096)

    memo.get_or_compute(("model",), [transcript], compute)
    memo.get_or_compute(("model",), [transcript], compute)
    assert calls == 2


def test_project_runs_under_the_fold_lock_and_charges_retained_roots(tmp_path: Path) -> None:
    cache = _cache(max_retained_bytes=1)
    transcript = tmp_path / "t.jsonl"
    _write_lines(transcript, [{"n": 1}])
    projects = 0

    def project(folder: _CountingFolder) -> tuple[tuple[dict, ...], tuple[dict, ...]]:
        nonlocal projects
        projects += 1
        product = tuple(folder.records())
        return product, product

    assert cache.project([transcript], project) == ({"n": 1},)
    assert cache.project([transcript], project) == ({"n": 1},)
    assert projects == 2
    assert len(_CountingFolder.instances) == 2


def test_project_charges_only_new_roots_not_its_full_cached_snapshot(tmp_path: Path) -> None:
    cache = _projection_cache(max_retained_bytes=2_500)
    transcript = tmp_path / "t.jsonl"
    _write_lines(transcript, [{"body": "x" * 700}])

    def project(folder: _ProjectionFolder) -> tuple[tuple[dict, ...], None]:
        snapshot = tuple(folder.records())
        folder.snapshot = snapshot  # type: ignore[attr-defined]
        return snapshot, None

    assert len(cache.project([transcript], project)) == 1
    _append_lines(transcript, [{"body": "y" * 700}])
    assert len(cache.project([transcript], project)) == 2

    # Folder shallow accounting charges its retained tuple without a history walk.
    assert len(_ProjectionFolder.instances) == 1


def test_project_does_not_recharge_a_replaced_snapshot_while_idle(tmp_path: Path) -> None:
    cache = _projection_cache(max_retained_bytes=1_800)
    transcript = tmp_path / "t.jsonl"
    _write_lines(transcript, [{"body": "x" * 1_000}])

    def project(folder: _ProjectionFolder) -> tuple[tuple[dict, ...], None]:
        snapshot = tuple(folder.records())
        folder.snapshot = snapshot  # type: ignore[attr-defined]
        return snapshot, None

    assert len(cache.project([transcript], project)) == 1
    assert len(cache.project([transcript], project)) == 1
    assert len(_ProjectionFolder.instances) == 1


def test_disjoint_path_tuples_fold_concurrently(tmp_path: Path) -> None:
    """Two workspaces' transcripts must not serialize behind each other.

    The daemon folds one transcript per workspace on its poll thread while
    request threads fold others; under a single instance lock every one of those
    waited on whichever fold happened to be running, which is the contention this
    cache was measured spending most of its wall time in.

    Proved by DEADLOCK rather than by timing: the two folders block until both
    have entered, so a cache that serializes them cannot finish at all. A
    stopwatch assertion would be a flake on a loaded host.
    """
    both_inside = threading.Barrier(2, timeout=5)

    class _BarrierFolder:
        def __init__(self) -> None:
            self._records: list[dict] = []

        def add(self, raw: dict, source: str) -> dict:
            del source
            both_inside.wait()
            self._records.append(raw)
            return raw

        def records(self) -> list[dict]:
            return self._records

    cache = TranscriptCache(_BarrierFolder)
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    _write_lines(a, [{"n": 1}])
    _write_lines(b, [{"n": 2}])

    failures: list[BaseException] = []

    def read(path: Path) -> None:
        try:
            cache.read([path])
        except BaseException as exc:  # reported, not swallowed
            failures.append(exc)

    threads = [threading.Thread(target=read, args=(p,)) for p in (a, b)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not [t for t in threads if t.is_alive()], "a fold did not finish"
    assert not failures, failures


def test_one_path_tuple_still_folds_under_a_single_lock(tmp_path: Path) -> None:
    """Per-entry locking must not let two threads fold ONE transcript at once.

    The point of striping is disjoint keys; the SAME key still has exactly one
    fold, or two threads advance one cursor and each parses half the appended
    bytes into the same folder.
    """
    concurrent = 0
    peak = 0
    guard = threading.Lock()

    class _WitnessFolder:
        def __init__(self) -> None:
            self._records: list[dict] = []

        def add(self, raw: dict, source: str) -> dict:
            del source
            nonlocal concurrent, peak
            with guard:
                concurrent += 1
                peak = max(peak, concurrent)
            time.sleep(0.02)
            with guard:
                concurrent -= 1
            self._records.append(raw)
            return raw

        def records(self) -> list[dict]:
            return self._records

    cache = TranscriptCache(_WitnessFolder)
    transcript = tmp_path / "t.jsonl"
    _write_lines(transcript, [{"n": i} for i in range(4)])

    threads = [threading.Thread(target=lambda: cache.read([transcript])) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert peak == 1
    # Each line folded exactly once, by whichever thread won the lock.
    assert len(cache.read([transcript])) == 4


def test_the_folder_capacity_is_measured_once_per_chunk_not_once_per_line(
    tmp_path: Path,
) -> None:
    """The accounting walk must not scale with the number of appended lines.

    ``_folder_shallow_size`` is O(fields) but calls ``sys.getsizeof`` on each,
    and it ran after EVERY admitted record — so a session catching up on 4000
    lines paid it 4000 times to observe a handful of list growths. It decides
    eviction, and eviction is decided per read, so per-chunk is the honest
    granularity.
    """
    calls = 0
    real = module._folder_shallow_size

    def counted(folder: object) -> int:
        nonlocal calls
        calls += 1
        return real(folder)  # type: ignore[arg-type]

    cache = _cache()
    transcript = tmp_path / "t.jsonl"
    _write_lines(transcript, [{"n": 0}])
    cache.read([transcript])

    _append_lines(transcript, [{"n": i} for i in range(1, 51)])
    module._folder_shallow_size = counted  # type: ignore[assignment]
    try:
        assert len(cache.read([transcript])) == 51
    finally:
        module._folder_shallow_size = real  # type: ignore[assignment]

    # One chunk ingested plus the post-projection charge — never one per line.
    assert calls <= 4, calls


def test_concurrent_memo_misses_compute_once(tmp_path: Path) -> None:
    """A cold key under concurrent readers runs its projection ONCE.

    ``compute`` deliberately runs outside the memo's lock (it re-enters the fold
    cache, and holding both would invert the lock order), which left the ~1 Hz
    poll and a request free to miss the same cold key and each run a full
    projection — the second result discarded. The per-key gate closes that
    without re-introducing the inversion.
    """
    memo = ResultMemo()
    transcript = tmp_path / "t.jsonl"
    _write_lines(transcript, [{"n": 1}])
    computes = 0
    guard = threading.Lock()
    start = threading.Barrier(4, timeout=5)

    def compute() -> str:
        nonlocal computes
        with guard:
            computes += 1
        time.sleep(0.05)
        return "value"

    results: list[str] = []

    def read() -> None:
        start.wait()
        results.append(memo.get_or_compute(("k",), [transcript], compute))

    threads = [threading.Thread(target=read) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert results == ["value"] * 4
    assert computes == 1


def test_a_failed_memo_compute_leaves_no_gate_behind(tmp_path: Path) -> None:
    """A raising ``compute`` must not leak its gate into an unbounded map.

    Mutation-tested, and the FIRST version of this test was vacuous in exactly
    the way this repo keeps re-learning: it asserted that the next read still
    succeeds, which it does either way — a stranded gate is released by its own
    ``with`` as the exception unwinds, so nothing blocks. The real defect is
    that ``_inflight`` is keyed by memo key and bounded by nothing (unlike
    ``_entries``, which has a maxsize), so a projection that raises — a
    transcript vanishing mid-read, an ordinary event — grows it for the life of
    the process. Assert the MAP, not the recovery.
    """
    memo = ResultMemo()
    transcript = tmp_path / "t.jsonl"
    _write_lines(transcript, [{"n": 1}])

    def boom() -> str:
        raise RuntimeError("projection failed")

    for i in range(5):
        with pytest.raises(RuntimeError):
            memo.get_or_compute((f"k{i}",), [transcript], boom)

    assert memo._inflight == {}
    assert memo.get_or_compute(("k0",), [transcript], lambda: "recovered") == "recovered"


def test_the_cache_counts_what_an_operator_needs_to_read_it(tmp_path: Path) -> None:
    """Hits, misses, resets, evictions and parsed bytes, as counts not samples.

    A profiler cannot distinguish a cold parse from a budget-driven re-parse —
    both are ``json.loads`` in a stack sample — so the question "is this budget
    too small for this fleet" was unanswerable without these.
    """
    cache = _cache()
    transcript = tmp_path / "t.jsonl"
    _write_lines(transcript, [{"n": 1}, {"n": 2}])

    cache.read([transcript])
    assert cache.metrics.misses == 1
    assert cache.metrics.hits == 0
    assert cache.metrics.parsed_bytes > 0

    cache.read([transcript])
    assert cache.metrics.hits == 1

    # Truncation is unsalvageable, so the fold rebuilds: a RESET, not a miss.
    # It must SHRINK — a same-size rewrite is the one change the cursor
    # genuinely cannot see, so an equal-length fixture would assert nothing.
    _write_lines(transcript, [{"n": 9}])
    cache.read([transcript])
    assert cache.metrics.resets == 1
    assert cache.metrics.misses == 1
