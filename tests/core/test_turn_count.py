"""The durable parse-fact cache (`TurnCountCache`).

Real transcripts in a sandboxed Claude config dir, read through the real
adapter — the numbers this cache stores are only worth anything if they are the
adapter's own, so nothing here fakes the parse. What IS pinned is the
cache's discipline: the fingerprint that decides a hit, that a miss answers
nothing rather than answering stale, that ``0`` survives as ``0``, that every
fact rides one entry, and that every failure degrades to "not measured" instead
of raising into a listing.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from grove.core.agents import get_adapter
from grove.core.agents.claude_code import _ClaudeHome
from grove.core.agents.model import SessionRef
from grove.core.contracts.usage import DurationView
from grove.core.session_duration import duration_of
from grove.core.turn_count import CountKey, TurnCountCache

SID = "11111111-1111-4111-8111-111111111111"
OTHER_SID = "22222222-2222-4222-8222-222222222222"


def _counts(cache: TurnCountCache, refs: Sequence[SessionRef]) -> Mapping[CountKey, int]:
    """Just the turn counts — the cache's original column, still its own fact."""
    return {key: facts.turns for key, facts in cache.facts_for(refs).items()}


@pytest.fixture
def claude_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cfg = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return cfg


def _turn(sid: str, cwd: Path, n: int) -> str:
    return (
        f'{{"type":"user","uuid":"h-{sid[:4]}-{n}","timestamp":"2026-06-09T08:0{n}:00.000Z",'
        f'"isSidechain":false,"cwd":"{cwd}","gitBranch":"main",'
        f'"message":{{"role":"user","content":"prompt {n}"}}}}\n'
    )


def _write(claude_home: Path, sid: str, cwd: Path, *, turns: int) -> Path:
    """A real-shaped transcript: a cwd-less preamble line, then ``turns`` prompts."""
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sid}.jsonl"
    path.write_text(
        '{"type":"mode","mode":"normal"}\n' + "".join(_turn(sid, cwd, i) for i in range(turns)),
        encoding="utf-8",
    )
    return path


def _ref(sid: str, cwd: Path, path: Path | None) -> SessionRef:
    return SessionRef(
        session_id=sid,
        adapter_kind="claude_code",
        cwd=str(cwd),
        transcript_path=path,
        birth=None,
        mtime=path.stat().st_mtime if path is not None else 0.0,
    )


@pytest.fixture
def cache(tmp_path: Path) -> TurnCountCache:
    return TurnCountCache(path=tmp_path / "session-turns.json")


def test_a_cold_cache_answers_nothing_and_a_fill_answers_the_adapters_own_number(
    cache: TurnCountCache, claude_home: Path, tmp_path: Path
) -> None:
    """The whole contract in one test: before the fill the row is honestly
    uncounted; after it, the cached number IS ``parse_activity``'s, not a
    second count of anything."""
    cwd = tmp_path / "repo"
    path = _write(claude_home, SID, cwd, turns=3)
    refs = [_ref(SID, cwd, path)]

    assert _counts(cache, refs) == {}

    assert cache.fill(refs) == 1
    assert _counts(cache, refs) == {("claude_code", SID): 3}
    assert get_adapter("claude_code").parse_activity(cwd, SID).human_turns == 3


def test_an_unchanged_transcript_is_never_read_twice(
    cache: TurnCountCache, claude_home: Path, tmp_path: Path
) -> None:
    cwd = tmp_path / "repo"
    path = _write(claude_home, SID, cwd, turns=2)
    refs = [_ref(SID, cwd, path)]
    assert cache.fill(refs) == 1

    assert cache.fill(refs) == 0  # nothing counted: the fingerprint still matches


def test_a_grown_transcript_answers_nothing_until_it_is_recounted(
    cache: TurnCountCache, claude_home: Path, tmp_path: Path
) -> None:
    """A stale number is worse than none — a session that gained a turn must
    read as uncounted, never as its previous count."""
    cwd = tmp_path / "repo"
    path = _write(claude_home, SID, cwd, turns=2)
    assert cache.fill([_ref(SID, cwd, path)]) == 1

    with path.open("a", encoding="utf-8") as handle:
        handle.write(_turn(SID, cwd, 9))
    grown = [_ref(SID, cwd, path)]

    assert _counts(cache, grown) == {}
    assert cache.fill(grown) == 1
    assert _counts(cache, grown) == {("claude_code", SID): 3}


def test_a_replaced_transcript_that_kept_its_mtime_is_still_a_miss(
    cache: TurnCountCache, claude_home: Path, tmp_path: Path
) -> None:
    """The half of the fingerprint ``mtime`` alone cannot carry: a restored or
    copied file whose timestamp was preserved."""
    cwd = tmp_path / "repo"
    path = _write(claude_home, SID, cwd, turns=2)
    assert cache.fill([_ref(SID, cwd, path)]) == 1
    stat = path.stat()

    _write(claude_home, SID, cwd, turns=5)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    assert _counts(cache, [_ref(SID, cwd, path)]) == {}


def test_a_session_with_no_human_turn_counts_zero_and_zero_is_not_null(
    cache: TurnCountCache, claude_home: Path, tmp_path: Path
) -> None:
    """The nullability contract's other end: a real 0 must reach the wire as 0,
    or a client cannot tell "nothing was asked here" from "not counted"."""
    cwd = tmp_path / "repo"
    path = _write(claude_home, SID, cwd, turns=0)

    assert cache.fill([_ref(SID, cwd, path)]) == 1
    assert _counts(cache, [_ref(SID, cwd, path)]) == {("claude_code", SID): 0}


def test_a_row_with_no_cwd_is_never_counted(
    cache: TurnCountCache, claude_home: Path, tmp_path: Path
) -> None:
    """~2 % of real Claude transcripts never reveal a cwd, and the adapters
    resolve a session BY cwd — so there is nothing to read it under, and the
    honest answer is permanently null rather than a fabricated zero."""
    cwd = tmp_path / "repo"
    path = _write(claude_home, SID, cwd, turns=2)
    cwdless = SessionRef(
        session_id=SID,
        adapter_kind="claude_code",
        cwd=None,
        transcript_path=path,
        birth=None,
        mtime=path.stat().st_mtime,
    )

    assert cache.fill([cwdless]) == 0
    assert _counts(cache, [cwdless]) == {}


def test_a_vanished_session_is_pruned_from_the_file(
    cache: TurnCountCache, claude_home: Path, tmp_path: Path
) -> None:
    """The file is bounded by the sessions that exist, not by everything the
    host has ever run."""
    cwd = tmp_path / "repo"
    first = _write(claude_home, SID, cwd, turns=1)
    second = _write(claude_home, OTHER_SID, cwd, turns=2)
    assert cache.fill([_ref(SID, cwd, first), _ref(OTHER_SID, cwd, second)]) == 2

    survivor = [_ref(OTHER_SID, cwd, second)]
    cache.fill(survivor)

    assert _counts(cache, survivor) == {("claude_code", OTHER_SID): 2}
    assert SID not in (tmp_path / "session-turns.json").read_text(encoding="utf-8")


def test_stop_ends_the_pass_between_sessions(
    cache: TurnCountCache, claude_home: Path, tmp_path: Path
) -> None:
    """Shutdown must not sit through a cold pass — and what was counted before
    the flag fired is still durable."""
    cwd = tmp_path / "repo"
    refs = [
        _ref(SID, cwd, _write(claude_home, SID, cwd, turns=1)),
        _ref(OTHER_SID, cwd, _write(claude_home, OTHER_SID, cwd, turns=2)),
    ]
    calls = iter([False, True, True, True])

    assert cache.fill(refs, stop=lambda: next(calls)) == 1
    assert len(_counts(cache, refs)) == 1


def test_an_unreadable_cache_file_degrades_to_no_counts(tmp_path: Path, claude_home: Path) -> None:
    """A corrupt or foreign file must read as "nothing is counted" — the state
    that shipped before this cache existed — never as an exception inside a
    listing."""
    path = tmp_path / "session-turns.json"
    path.write_text("{not json at all", encoding="utf-8")
    cwd = tmp_path / "repo"
    refs = [_ref(SID, cwd, _write(claude_home, SID, cwd, turns=2))]

    cache = TurnCountCache(path=path)
    assert _counts(cache, refs) == {}
    # And it recovers by overwriting, rather than staying broken forever.
    assert cache.fill(refs) == 1
    assert _counts(cache, refs) == {("claude_code", SID): 2}


def test_a_missing_transcript_is_not_counted(
    cache: TurnCountCache, claude_home: Path, tmp_path: Path
) -> None:
    cwd = tmp_path / "repo"
    path = _write(claude_home, SID, cwd, turns=1)
    ref = _ref(SID, cwd, path)
    path.unlink()

    assert cache.fill([ref]) == 0
    assert _counts(cache, [ref]) == {}


def _worked(sid: str, cwd: Path) -> str:
    """A prompt and the reply it produced — one measurable generation interval."""
    return (
        f'{{"type":"user","uuid":"w-{sid[:4]}","timestamp":"2026-06-09T09:00:00.000Z",'
        f'"isSidechain":false,"cwd":"{cwd}","gitBranch":"main",'
        f'"message":{{"role":"user","content":"do the thing"}}}}\n'
        f'{{"type":"assistant","uuid":"wa-{sid[:4]}","requestId":"wr-{sid[:4]}",'
        f'"isSidechain":false,"cwd":"{cwd}","timestamp":"2026-06-09T09:02:00.000Z",'
        f'"message":{{"id":"wm-{sid[:4]}","role":"assistant","model":"claude-opus-5",'
        '"stop_reason":"end_turn","usage":{"input_tokens":3,"output_tokens":1},'
        '"content":[{"type":"text","text":"done"}]}}\n'
    )


def test_one_parse_stores_both_facts_and_the_duration_is_the_adapters_own(
    cache: TurnCountCache, claude_home: Path, tmp_path: Path
) -> None:
    """Turns and durations ride ONE entry from ONE parse — the reason there is
    no second cache — and the stored clock is `duration_of` over the adapter's
    own spine, which is exactly what the project-scoped listing computes."""
    cwd = tmp_path / "repo"
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{SID}.jsonl"
    path.write_text('{"type":"mode","mode":"normal"}\n' + _worked(SID, cwd), encoding="utf-8")
    refs = [_ref(SID, cwd, path)]

    assert cache.fill(refs) == 1
    facts = cache.facts_for(refs)[("claude_code", SID)]

    assert facts.turns == 1
    assert facts.duration.active_ms == 2 * 60_000
    assert facts.duration == duration_of(get_adapter("claude_code").read_messages(cwd, SID))


def test_a_session_that_did_no_measurable_work_is_still_timed(
    cache: TurnCountCache, claude_home: Path, tmp_path: Path
) -> None:
    """ "No measurable WORK" is a row whose active fields are null, not a missing
    row — the caller's own `None` has to keep meaning "not measured yet".

    Confidence stays `derived` here, and that is the honest answer rather than a
    loose assertion: this spine carries timestamps, so the elapsed SPAN was
    measured even though no active interval could be paired out of it. Only a
    spine with no timestamps at all is `unknown`, which is the case the sibling
    test in `test_session_duration.py` pins. Reading `active_ms is None` as
    "nothing was measured" is exactly the conflation the three-number duration
    exists to prevent.
    """
    cwd = tmp_path / "repo"
    refs = [_ref(SID, cwd, _write(claude_home, SID, cwd, turns=2))]

    assert cache.fill(refs) == 1
    facts = cache.facts_for(refs)[("claude_code", SID)]

    assert facts.turns == 2
    assert facts.duration.active_ms is None
    assert facts.duration.elapsed_span_ms is not None
    assert facts.duration.confidence == "derived"


def test_an_entry_written_before_durations_existed_reads_as_unmeasured(
    cache: TurnCountCache, claude_home: Path, tmp_path: Path
) -> None:
    """The file is derived from transcripts Grove can re-read, so a widened
    entry REBUILDS rather than migrating: a half-entry must answer nothing
    (never a count with a fabricated duration) and be re-parsed by the next
    pass."""
    cwd = tmp_path / "repo"
    path = _write(claude_home, SID, cwd, turns=2)
    refs = [_ref(SID, cwd, path)]
    stat = path.stat()
    old = {"mtime": stat.st_mtime, "size": stat.st_size, "turns": 2}
    (tmp_path / "session-turns.json").write_text(
        json.dumps({"sessions": {f"claude_code\t{SID}": old}}), encoding="utf-8"
    )

    assert cache.facts_for(refs) == {}
    assert cache.fill(refs) == 1
    assert cache.facts_for(refs)[("claude_code", SID)].turns == 2


def test_an_entry_whose_duration_predates_a_NESTED_field_is_refused_too(
    cache: TurnCountCache, claude_home: Path, tmp_path: Path
) -> None:
    """The hole the entry-level guard could not see, and why the file carries a shape.

    A finished session's transcript never changes, so its ``(mtime, size)``
    matches forever — the property that makes this cache cheap. That makes a
    widened ``SessionFacts`` permanently wrong rather than briefly wrong: every
    pre-existing row keeps answering with the new members absent, and absent
    reads as *not measured*, indistinguishable from a session that genuinely
    had nothing to measure.

    ``_decode`` alone cannot catch it. It refuses an entry missing the
    ``duration`` KEY, but a ``duration`` dict missing newly-added nullable
    FIELDS validates clean — asserted below, because that is the exact step
    that made the entry-level guard insufficient, and a future reader will
    otherwise assume the guard covers this.
    """
    cwd = tmp_path / "repo"
    path = _write(claude_home, SID, cwd, turns=2)
    refs = [_ref(SID, cwd, path)]
    stat = path.stat()

    # A duration written before `generation_ms`/`tool_ms` existed.
    stale_duration = {
        "active_ms": 1000,
        "execution_ms": 1000,
        "elapsed_span_ms": 2000,
        "confidence": "derived",
    }
    # The step that defeats an entry-level guard: this is NOT rejected.
    assert DurationView.model_validate(stale_duration).generation_ms is None

    (tmp_path / "session-turns.json").write_text(
        json.dumps(
            {
                "shape": "turns,duration|active_ms,confidence,elapsed_span_ms,execution_ms",
                "sessions": {
                    f"claude_code\t{SID}": {
                        "mtime": stat.st_mtime,
                        "size": stat.st_size,
                        "turns": 2,
                        "duration": stale_duration,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    # Refused wholesale, so the next pass re-parses rather than serving a row
    # whose split reads "not measured" for the life of the file.
    assert cache.facts_for(refs) == {}
    assert cache.fill(refs) == 1

    # And the rewrite carries the CURRENT shape, so the re-measured entry is a
    # hit rather than being discarded again on every later pass. Asserted
    # through a second `facts_for` rather than by reading the file, because
    # "the next read accepts it" is the property; the key's spelling is not.
    assert cache.facts_for(refs)[("claude_code", SID)].turns == 2
    assert cache.fill(refs) == 0
