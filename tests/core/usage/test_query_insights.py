"""Query and insight behavior over the frozen usage cache schema."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.usage import UsageFilters, UsageMetric
from grove.core.usage._pricing import PriceBook
from grove.core.usage._store import UsageStore
from grove.core.usage.insights import InsightEngine
from grove.core.usage.query import UsageQuery

METRICS: tuple[UsageMetric, ...] = (
    "tokens",
    "sessions",
    "tool_calls",
    "active_minutes",
    "cost",
)


def _epoch(day: int, hour: int = 12) -> int:
    return int(datetime(2026, 8, day, hour, tzinfo=UTC).timestamp())


def _insert_session(
    store: UsageStore,
    *,
    session_id: str,
    provider: str = "claude_code",
    model: str = "model-a",
    at: int | None,
    provider_total: int | None = None,
    fresh_input: int | None = None,
    output: int | None = None,
    project: str = "/repo/a",
    subagent_fresh_input: int | None = None,
    subagent_cache_read: int | None = None,
) -> None:
    with store.write() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sources(source_id, provider, root, label) "
            "VALUES('source', ?, '/profile', 'profile')",
            (provider,),
        )
        conn.execute(
            "INSERT INTO sessions("
            "session_id, source_id, provider, cwd, project, started_at, last_event_at, "
            "fresh_input, output, provider_total, subagent_fresh_input, subagent_cache_read, "
            "models, cost_provenance, parser_health"
            ") "
            "VALUES(?, 'source', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', 'ok')",
            (
                session_id,
                provider,
                project,
                project,
                at,
                at,
                fresh_input,
                output,
                provider_total,
                subagent_fresh_input,
                subagent_cache_read,
                json.dumps([model]),
            ),
        )
        conn.execute(
            "INSERT INTO usage_events("
            "session_id, source_id, seq, ts, kind, model, fresh_input, output, provider_total"
            ") VALUES(?, 'source', 1, ?, 'generation', ?, ?, ?, ?)",
            (session_id, at, model, fresh_input, output, provider_total),
        )


def _query(tmp_path: Path) -> tuple[UsageStore, UsageQuery]:
    cfg = GroveConfig.model_validate(
        {
            "usage": {
                "max_sessions_per_page": 2,
                "pricing": {"models": {"model-a": {"input": 1, "output": 2}}},
            }
        }
    )
    store = UsageStore(tmp_path / "usage.db")
    return store, UsageQuery(store=store, prices=PriceBook(cfg.usage.pricing), cfg=cfg)


def test_summary_and_session_cost_never_turn_unmeasured_tokens_into_zero(tmp_path: Path) -> None:
    store, query = _query(tmp_path)
    _insert_session(store, session_id="unknown", at=_epoch(8))

    unknown = query.summary(UsageFilters())
    assert unknown.tokens.provider_total is None
    assert unknown.cost is None
    assert query.sessions(UsageFilters(), cursor=None, limit=10, sort="recent").rows[0].cost is None

    _insert_session(
        store,
        session_id="measured",
        at=_epoch(9),
        fresh_input=1_000_000,
        output=500_000,
        provider_total=1_500_000,
    )
    assert query.summary(UsageFilters()).cost is None
    measured = query.summary(UsageFilters(since=datetime(2026, 8, 9, tzinfo=UTC)))
    assert measured.cost is not None
    assert measured.cost.amount == "2.000000"
    store.close()


def test_partial_cost_bearing_token_classes_do_not_produce_an_estimate(tmp_path: Path) -> None:
    store, query = _query(tmp_path)
    _insert_session(
        store,
        session_id="partial",
        at=_epoch(9),
        fresh_input=None,
        output=500_000,
        provider_total=1_500_000,
    )

    row = query.sessions(UsageFilters(), cursor=None, limit=10, sort="recent").rows[0]
    assert row.cost is None
    assert query.summary(UsageFilters()).cost is None
    store.close()


def test_session_row_separates_subagent_tokens_from_the_combined_total(tmp_path: Path) -> None:
    """A session's ``tokens`` stays the combined (root + delegated) total, and
    ``subagent_tokens`` is the delegated portion alone — never merged into one
    opaque figure, and ``None`` rather than a zeroed view when nothing
    sidechain-attributed was measured."""
    store, query = _query(tmp_path)
    _insert_session(
        store,
        session_id="with-subagents",
        at=_epoch(9),
        fresh_input=1_000,
        output=200,
        subagent_fresh_input=700,
        subagent_cache_read=50_000,
    )
    _insert_session(
        store,
        session_id="root-only",
        at=_epoch(9),
        fresh_input=1_000,
        output=200,
    )

    rows = {
        row.session_id: row
        for row in query.sessions(UsageFilters(), cursor=None, limit=10, sort="recent").rows
    }

    with_subagents = rows["with-subagents"]
    assert with_subagents.tokens.fresh_input == 1_000  # combined total, unchanged shape
    assert with_subagents.subagent_tokens is not None
    assert with_subagents.subagent_tokens.fresh_input == 700
    assert with_subagents.subagent_tokens.cache_read == 50_000
    assert with_subagents.subagent_tokens.output is None  # not reported, not zero

    assert rows["root-only"].subagent_tokens is None  # unmeasured, never a zeroed view
    store.close()


def test_filters_are_exact_and_cursor_pagination_has_no_gaps(tmp_path: Path) -> None:
    store, query = _query(tmp_path)
    _insert_session(store, session_id="a", at=_epoch(7), provider_total=100, model="model-a")
    _insert_session(store, session_id="b", at=_epoch(8), provider_total=100, model="model-a-pro")
    _insert_session(store, session_id="c", at=_epoch(9), provider_total=None, model="model-a")

    exact = query.sessions(UsageFilters(model="model-a"), cursor=None, limit=10, sort="recent")
    assert {row.session_id for row in exact.rows} == {"a", "c"}

    ids: list[str] = []
    cursor: str | None = None
    while True:
        page = query.sessions(UsageFilters(), cursor=cursor, limit=1, sort="tokens")
        ids.extend(row.session_id for row in page.rows)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert ids == ["b", "a", "c"]
    assert len(ids) == len(set(ids))

    # The wire validates shape, not calendar truth; an impossible date degrades
    # to the remaining filters instead of raising out of an ordinary read.
    impossible = query.summary(UsageFilters(day="2026-99-99"))
    assert impossible.sessions == 3
    store.close()


def test_cursor_paginates_rows_with_unmeasured_sort_and_time_values(tmp_path: Path) -> None:
    store, query = _query(tmp_path)
    _insert_session(store, session_id="known-time", at=_epoch(9))
    _insert_session(store, session_id="null-time-a", at=None)
    _insert_session(store, session_id="null-time-b", at=None)

    ids: list[str] = []
    cursor: str | None = None
    while True:
        page = query.sessions(UsageFilters(), cursor=cursor, limit=1, sort="tokens")
        ids.extend(row.session_id for row in page.rows)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert ids == ["known-time", "null-time-b", "null-time-a"]
    store.close()


def test_temporal_aggregates_follow_event_days_and_reject_partial_totals(tmp_path: Path) -> None:
    store, query = _query(tmp_path)
    _insert_session(
        store,
        session_id="spanning",
        at=_epoch(9),
        fresh_input=150,
        output=0,
    )
    with store.write() as conn:
        conn.execute("DELETE FROM usage_events WHERE session_id='spanning'")
        conn.executemany(
            "INSERT INTO usage_events("
            "session_id, source_id, seq, ts, kind, model, fresh_input, cache_read, "
            "cache_creation, output"
            ") VALUES('spanning', 'source', ?, ?, 'generation', 'model-a', ?, 0, 0, 0)",
            [(1, _epoch(8), 100), (2, _epoch(9), 50)],
        )

    activity = query.activity(
        UsageFilters(
            since=datetime(2026, 8, 8, tzinfo=UTC),
            until=datetime(2026, 8, 10, tzinfo=UTC),
        ),
        metric="tokens",
    )
    assert [(bucket.day, bucket.value) for bucket in activity.buckets] == [
        ("2026-08-08", 100.0),
        ("2026-08-09", 50.0),
    ]
    day_row = query.sessions(
        UsageFilters(day="2026-08-08"), cursor=None, limit=10, sort="recent"
    ).rows[0]
    assert day_row.tokens.fresh_input == 100
    assert day_row.tokens.provider_total is None

    _insert_session(store, session_id="unmeasured", at=_epoch(9))
    summary = query.summary(
        UsageFilters(
            since=datetime(2026, 8, 8, tzinfo=UTC),
            until=datetime(2026, 8, 10, tzinfo=UTC),
        )
    )
    assert summary.tokens.provider_total is None
    assert summary.tokens.fresh_input is None
    # The SUMMARY stays all-or-null above, because it has a null to return.
    # The heatmap does not — see the partial-day test below — so the same day
    # publishes the 50 tokens it can support rather than vanishing.
    partial_activity = query.activity(
        UsageFilters(
            since=datetime(2026, 8, 9, tzinfo=UTC),
            until=datetime(2026, 8, 10, tzinfo=UTC),
        ),
        metric="tokens",
    )
    assert [(bucket.day, bucket.value) for bucket in partial_activity.buckets] == [
        ("2026-08-09", 50.0)
    ]
    store.close()


def test_a_partly_measured_day_is_published_and_an_unmeasured_one_is_absent(
    tmp_path: Path,
) -> None:
    """A heatmap has no third state, so a dropped bucket is a POSITIVE claim.

    ``activity`` used to skip any day where a single session lacked the
    measurement, which renders a day of real work exactly like a day nobody
    worked — worse than the fabricated zero the package refuses, because a gap
    at least reads as a gap. Measured on the documentation demo corpus, the
    tokens heatmap lit 4 of 24 active days while ``sessions`` and
    ``tool_calls`` lit all 24.

    So a day with ANY measured session publishes the sum it can support
    (`ticketRollup`'s rule: an understatement can only understate), and only a
    day whose sessions were all unmeasured stays absent, since there the sum
    would be a fabricated zero. ``active_minutes`` shares the shape and the
    fix; ``cost`` deliberately does not.
    """
    store, query = _query(tmp_path)
    _insert_session(store, session_id="measured", at=_epoch(8), provider_total=900)
    _insert_session(store, session_id="partner", at=_epoch(8))  # same day, unmeasured
    _insert_session(store, session_id="dark", at=_epoch(9))  # a whole day of nothing measured
    with store.write() as conn:
        conn.execute("UPDATE usage_events SET duration_ms=60000 WHERE session_id='measured'")

    window = UsageFilters(
        since=datetime(2026, 8, 8, tzinfo=UTC), until=datetime(2026, 8, 10, tzinfo=UTC)
    )

    tokens = query.activity(window, metric="tokens")
    assert [(bucket.day, bucket.value, bucket.sessions) for bucket in tokens.buckets] == [
        ("2026-08-08", 900.0, 2)
    ]
    minutes = query.activity(window, metric="active_minutes")
    assert [(bucket.day, bucket.value) for bucket in minutes.buckets] == [("2026-08-08", 1.0)]

    # Both axes that were never at risk keep reporting every active day, which
    # is what made the tokens gap read as missing data rather than as a bug.
    for metric in ("sessions", "tool_calls"):
        days = [bucket.day for bucket in query.activity(window, metric=metric).buckets]
        assert days == ["2026-08-08", "2026-08-09"]
    store.close()


def test_a_day_with_no_sessions_at_all_still_reports_zero(tmp_path: Path) -> None:
    """An idle day is a measurement, not a gap — nothing ran, so nothing was
    spent, and withholding it would punch a hole in the calendar the heatmap
    draws. Only a day that HAS sessions and measured none of them is absent."""
    store, query = _query(tmp_path)
    _insert_session(store, session_id="measured", at=_epoch(8), provider_total=10)

    buckets = query.activity(
        UsageFilters(
            since=datetime(2026, 8, 8, tzinfo=UTC), until=datetime(2026, 8, 11, tzinfo=UTC)
        ),
        metric="tokens",
    ).buckets

    assert [(bucket.day, bucket.value) for bucket in buckets] == [
        ("2026-08-08", 10.0),
        ("2026-08-09", 0.0),
        ("2026-08-10", 0.0),
    ]
    store.close()


def _counted_reads(
    store: UsageStore, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[str], list[str]]:
    """Record every SQL read the store serves, split by row shape.

    Patched on the INSTANCE rather than the class, so ``scalar`` — which routes
    through ``self.query`` — is counted through the same wrapper the query
    object's own reads are.
    """
    rows: list[str] = []
    tuples: list[str] = []
    for name, log in (("query", rows), ("query_tuples", tuples)):
        original = getattr(store, name)

        def wrapper(
            sql: str, params: Sequence[Any] = (), _original: Any = original, _log: list[str] = log
        ) -> Any:
            _log.append(sql)
            return _original(sql, params)

        monkeypatch.setattr(store, name, wrapper)
    return rows, tuples


def test_a_calendar_range_costs_the_same_reads_however_many_days_it_spans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`activity` used to issue a fresh grouped-plus-intervals PAIR per day.

    Over a 365-day window that is roughly 730 round trips across the whole
    event corpus, and it is what made the usage page time out in the browser:
    measured on a real 1.34M-event cache, one request cost 30.7s for `tokens`
    and 74.3s for `cost` (which paid a second pair per day inside `_event_cost`)
    against 11.2s for the `summary` beside it, which is not per-day.

    The day is a GROUP BY column now, so the read count is a property of the
    QUESTION rather than of the calendar. Asserted as an equality between a
    3-day and a 90-day window rather than against a fixed number, because a
    number written here is a number somebody has to maintain — and the fixed
    part is exactly what regresses.
    """
    store, query = _query(tmp_path)
    for day in (8, 9, 20):
        _insert_session(store, session_id=f"s{day}", at=_epoch(day), provider_total=100)
    with store.write() as conn:
        conn.execute("UPDATE usage_events SET duration_ms=30000")

    def reads(since: int, until: int, metric: UsageMetric) -> tuple[int, int]:
        """(SQL reads issued, buckets returned) for one window and metric."""
        rows, tuples = _counted_reads(store, monkeypatch)
        view = query.activity(
            UsageFilters(
                since=datetime(2026, 8, since, tzinfo=UTC),
                until=datetime(2026, 8, until, tzinfo=UTC),
            ),
            metric=metric,
        )
        monkeypatch.undo()
        return len(rows) + len(tuples), len(view.buckets)

    for metric in METRICS:
        short_reads, short_days = reads(8, 11, metric)
        long_reads, long_days = reads(1, 31, metric)
        # The window really did grow, or the equality below proves nothing.
        assert short_days < long_days
        assert short_reads == long_reads, f"{metric} scales its reads with the calendar"
    store.close()


def test_only_the_union_metric_pays_for_the_per_event_interval_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`active_ms` is the one column that cannot come out of an aggregate, so it
    needs a second read walking every EVENT rather than every session — 594,960
    rows against 1,301 on the reference corpus. Four of the five metrics never
    look at the union, and they used to pay for it anyway.

    The withheld value is an ABSENT key, never a `None`: `None` is contracted as
    *this session was never timed*, so a default would let a metric that forgot
    to ask report a day of real work as unmeasured.
    """
    store, query = _query(tmp_path)
    _insert_session(store, session_id="timed", at=_epoch(8), provider_total=100)
    with store.write() as conn:
        conn.execute("UPDATE usage_events SET duration_ms=90000")

    window = UsageFilters(
        since=datetime(2026, 8, 8, tzinfo=UTC), until=datetime(2026, 8, 10, tzinfo=UTC)
    )
    for metric in METRICS:
        _, tuples = _counted_reads(store, monkeypatch)
        view = query.activity(window, metric=metric)
        monkeypatch.undo()
        assert len(tuples) == (1 if metric == "active_minutes" else 0), metric
        if metric == "active_minutes":
            # Mutation guard: the read is still doing its job, so a fix that
            # skipped it for every metric would fail here rather than pass
            # quietly with a heatmap of empty days.
            assert [bucket.value for bucket in view.buckets] == [1.5, 0.0]
    store.close()


def test_day_buckets_are_grouped_in_the_readers_zone_not_the_databases(tmp_path: Path) -> None:
    """A day is a local-midnight span in the zone the READER asked for, so the
    boundaries have to be carried into SQL as values.

    The tempting shortcut once the grouping moves into SQL is a `date(ts)`,
    which is tz-naive: it would re-bucket every event for every non-UTC caller
    while the query got faster, which is a correctness regression wearing a
    performance fix's clothes. Both events below sit on 2026-08-09 in UTC and
    straddle midnight in New York.
    """
    store, query = _query(tmp_path)
    _insert_session(store, session_id="evening", at=_epoch(9, 2), provider_total=7)
    _insert_session(store, session_id="afternoon", at=_epoch(9, 20), provider_total=11)

    def days(tz: str) -> dict[str, float]:
        view = query.activity(
            UsageFilters(
                since=datetime(2026, 8, 8, tzinfo=UTC),
                until=datetime(2026, 8, 10, tzinfo=UTC),
                tz=tz,
            ),
            metric="tokens",
        )
        return {bucket.day: bucket.value for bucket in view.buckets}

    assert days("UTC") == {"2026-08-08": 0.0, "2026-08-09": 18.0}
    # 02:00 UTC is the previous evening in New York, which pulls the whole
    # spine back a day and splits the two events across it.
    assert days("America/New_York") == {
        "2026-08-07": 0.0,
        "2026-08-08": 7.0,
        "2026-08-09": 11.0,
    }
    store.close()


def test_model_breakdown_does_not_double_attribute_multi_model_sessions(tmp_path: Path) -> None:
    store, query = _query(tmp_path)
    _insert_session(store, session_id="single", at=_epoch(9), provider_total=100)
    _insert_session(store, session_id="switched", at=_epoch(9), provider_total=200)
    with store.write() as conn:
        conn.execute(
            "UPDATE sessions SET models=? WHERE session_id='switched'",
            (json.dumps(["model-a", "model-b"]),),
        )

    rows = query.breakdown(UsageFilters(), dimension="model").rows

    assert [(row.key, row.tokens.provider_total) for row in rows] == [("model-a", 100)]
    store.close()


def test_model_latency_averages_only_the_rows_own_sessions(tmp_path: Path) -> None:
    """Every column on a model row must describe the SAME population.

    `_model_breakdown` restricts to single-model sessions — attributing a
    session-level token sum to one of several models it used would be a guess.
    The latency read shipped without that restriction, so it averaged across
    every session that touched the model, including the multi-model ones whose
    tokens the row deliberately excludes. Measured on the reference host,
    `claude-sonnet-5` published 8429 ms over 41,648 calls on a row whose own 24
    sessions support 6532 ms over 121 — a call count 344x larger than the row
    it decorated.

    A row must be internally coherent before it is large.
    """
    store, query = _query(tmp_path)
    _insert_session(store, session_id="single", at=_epoch(9), provider_total=100)
    _insert_session(store, session_id="switched", at=_epoch(9), provider_total=200)
    with store.write() as conn:
        conn.execute(
            "UPDATE sessions SET models=? WHERE session_id='switched'",
            (json.dumps(["model-a", "model-b"]),),
        )
        # The single-model session waited 10s; the excluded one 100ms. Folding
        # them together would report ~5s and two calls.
        conn.execute("UPDATE usage_events SET duration_ms=10000 WHERE session_id='single'")
        conn.execute("UPDATE usage_events SET duration_ms=100 WHERE session_id='switched'")

    row = query.breakdown(UsageFilters(), dimension="model").rows[0]

    assert row.key == "model-a"
    assert row.latency.calls == 1  # not 2 — the switched session is not this row
    assert row.latency.avg_ms == 10_000  # not ~5050
    store.close()


def test_a_date_filtered_model_breakdown_still_carries_its_latency(tmp_path: Path) -> None:
    """`breakdown` forks on `_has_temporal_filter` before it ever reaches
    `_model_breakdown`, so a merge living inside that method covered only the
    untimed route: any `since`/`until`/`day` silently dropped the column to its
    field default. `avg_ms=None` is contracted as *never measured*, so the
    degraded answer was indistinguishable from an honest one — and invisible on
    the audit page, which passes no filters, until the first range picker."""
    store, query = _query(tmp_path)
    _insert_session(store, session_id="single", at=_epoch(9), provider_total=100)
    with store.write() as conn:
        conn.execute("UPDATE usage_events SET duration_ms=7000 WHERE session_id='single'")

    untimed = query.breakdown(UsageFilters(), dimension="model").rows[0]
    timed = query.breakdown(UsageFilters(day="2026-08-09"), dimension="model").rows[0]

    assert untimed.latency.avg_ms == 7000
    assert timed.latency.avg_ms == 7000
    assert timed.latency.calls == 1
    store.close()


def test_grouped_breakdowns_never_sum_partial_measurements(tmp_path: Path) -> None:
    store, query = _query(tmp_path)
    _insert_session(
        store,
        session_id="measured",
        at=_epoch(9),
        fresh_input=80,
        output=20,
        provider_total=100,
    )
    _insert_session(store, session_id="unknown", at=_epoch(9))

    for dimension in ("provider", "project", "model"):
        row = query.breakdown(UsageFilters(), dimension=dimension).rows[0]
        assert row.tokens.provider_total is None
        assert row.tokens.fresh_input is None
        assert row.tokens.output is None

    store.close()


def test_insight_thresholds_and_filters_come_from_config(tmp_path: Path) -> None:
    cfg = GroveConfig.model_validate(
        {"usage": {"insights": {"min_occurrences": 3, "retry_window_seconds": 30}}}
    )
    store = UsageStore(tmp_path / "usage.db")
    _insert_session(store, session_id="bad", at=_epoch(9))
    with store.write() as conn:
        conn.executemany(
            "INSERT INTO usage_events("
            "session_id, source_id, seq, ts, kind, tool_name, target, failure_category, is_error"
            ") "
            "VALUES('bad', 'source', ?, ?, 'tool_result', 'Read', '/missing', 'not_found', 1)",
            [(index, _epoch(9) + index) for index in range(3)],
        )
    engine = InsightEngine(store=store, cfg=cfg)

    kinds = {finding.kind for finding in engine.findings(UsageFilters()).findings}
    assert "recurring_tool_failure" in kinds
    assert "retry_loop" in kinds
    assert engine.findings(UsageFilters(provider="codex")).findings == ()

    quieter = GroveConfig.model_validate({"usage": {"insights": {"min_occurrences": 4}}})
    quiet_kinds = {
        finding.kind
        for finding in InsightEngine(store=store, cfg=quieter).findings(UsageFilters()).findings
    }
    assert "recurring_tool_failure" not in quiet_kinds
    store.close()
