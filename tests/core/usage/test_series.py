"""`UsageSeriesQuery` — a metric over days, split by one dimension.

The rules pinned here are the ones a happy-path render cannot see: that a day a
group did not report is `None` rather than `0`, that the published spine keeps
its empty days, that one unmeasured session poisons a day's total rather than
shrinking it, and that a dropped tail says so.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from grove.core.config import GroveConfig
from grove.core.contracts.usage import UsageCoverageView, UsageFilters
from grove.core.usage._pricing import PriceBook
from grove.core.usage._store import UsageStore
from grove.core.usage.query import UsageQuery
from grove.core.usage.series import UsageSeriesQuery


def _epoch(day: int, hour: int = 12) -> int:
    return int(datetime(2026, 8, day, hour, tzinfo=UTC).timestamp())


def _at(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 8, day, hour, tzinfo=UTC)


def _series(tmp_path: Path, **usage: object) -> tuple[UsageStore, UsageSeriesQuery]:
    cfg = GroveConfig.model_validate({"usage": usage}) if usage else GroveConfig()
    store = UsageStore(tmp_path / "usage.db")
    return store, UsageSeriesQuery(
        store=store,
        prices=PriceBook(cfg.usage.pricing),
        cfg=cfg,
        coverage=UsageCoverageView,
    )


def _session(
    store: UsageStore,
    *,
    session_id: str,
    provider: str = "claude_code",
    account_id: str = "acct-max",
    project: str = "/repo/a",
) -> None:
    with store.write() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sources(source_id, provider, root, label) "
            "VALUES('source', ?, '/profile', 'profile')",
            (provider,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO accounts(account_id, provider, label) VALUES(?, ?, ?)",
            (account_id, provider, f"{account_id} (max)"),
        )
        conn.execute(
            "INSERT INTO sessions(session_id, source_id, provider, cwd, project, account_id, "
            "models, cost_provenance, parser_health) "
            "VALUES(?, 'source', ?, ?, ?, ?, '[]', 'unknown', 'ok')",
            (session_id, provider, project, project, account_id),
        )


_SEQ = {"n": 0}


def _generation(
    store: UsageStore,
    *,
    session_id: str,
    ts: int,
    model: str = "model-a",
    fresh_input: int | None = None,
    cache_read: int | None = None,
    cache_creation: int | None = None,
    output: int | None = None,
    provider_total: int | None = None,
    duration_ms: int | None = None,
) -> None:
    _SEQ["n"] += 1
    with store.write() as conn:
        conn.execute(
            "INSERT INTO usage_events(session_id, source_id, seq, ts, kind, model, "
            "fresh_input, cache_read, cache_creation, output, provider_total, duration_ms) "
            "VALUES(?, 'source', ?, ?, 'generation', ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                _SEQ["n"],
                ts,
                model,
                fresh_input,
                cache_read,
                cache_creation,
                output,
                provider_total,
                duration_ms,
            ),
        )


def _tool_call(store: UsageStore, *, session_id: str, ts: int, tool: str | None) -> None:
    _SEQ["n"] += 1
    with store.write() as conn:
        conn.execute(
            "INSERT INTO usage_events(session_id, source_id, seq, ts, kind, tool_name) "
            "VALUES(?, 'source', ?, ?, 'tool_call', ?)",
            (session_id, _SEQ["n"], ts, tool),
        )


def _window(days: int = 3) -> UsageFilters:
    return UsageFilters(since=_at(10, 0), until=_at(10 + days, 0))


# ── the spine ────────────────────────────────────────────────────────────────


def test_the_published_spine_keeps_days_nothing_ran_on(tmp_path: Path) -> None:
    """A renderer that rebuilt the axis from its points would close this gap,
    and a quiet Sunday would silently become a narrower week."""
    store, series = _series(tmp_path)
    _session(store, session_id="s1")
    _generation(store, session_id="s1", ts=_epoch(10), provider_total=100)
    _generation(store, session_id="s1", ts=_epoch(12), provider_total=300)

    view = series.series(_window(), dimension="model", metric="tokens")

    assert view.days == ("2026-08-10", "2026-08-11", "2026-08-12")
    (group,) = view.groups
    assert tuple(point.day for point in group.points) == view.days


def test_a_day_a_group_did_not_report_is_null_not_zero(tmp_path: Path) -> None:
    """A bar of height zero claims a measurement. The honest answer is no bar."""
    store, series = _series(tmp_path)
    _session(store, session_id="s1")
    _generation(store, session_id="s1", ts=_epoch(10), provider_total=100)
    _generation(store, session_id="s1", ts=_epoch(12), provider_total=300)

    (group,) = series.series(_window(), dimension="model", metric="tokens").groups

    assert [point.value for point in group.points] == [100.0, None, 300.0]
    assert group.total == 400.0


def test_a_range_with_no_days_answers_empty_rather_than_raising(tmp_path: Path) -> None:
    _store, series = _series(tmp_path)
    view = series.series(
        UsageFilters(since=_at(12, 0), until=_at(10, 0)), dimension="model", metric="tokens"
    )
    assert view.days == ()
    assert view.groups == ()


def test_a_day_filter_narrows_the_spine_to_that_calendar_day(tmp_path: Path) -> None:
    store, series = _series(tmp_path)
    _session(store, session_id="s1")
    _generation(store, session_id="s1", ts=_epoch(10), provider_total=100)
    _generation(store, session_id="s1", ts=_epoch(11), provider_total=200)

    view = series.series(UsageFilters(day="2026-08-11"), dimension="model", metric="tokens")

    assert view.days == ("2026-08-11",)
    assert [point.value for point in view.groups[0].points] == [200.0]


def test_the_zone_decides_where_a_day_starts(tmp_path: Path) -> None:
    """22:00 UTC on the 10th is the 11th in Sydney — the bucket must follow the
    reader's zone, which is exactly why no `day` column is stored."""
    store, series = _series(tmp_path)
    _session(store, session_id="s1")
    _generation(store, session_id="s1", ts=_epoch(10, 22), provider_total=100)

    view = series.series(
        UsageFilters(since=_at(10, 0), until=_at(12, 0), tz="Australia/Sydney"),
        dimension="model",
        metric="tokens",
    )

    measured = {
        point.day: point.value for point in view.groups[0].points if point.value is not None
    }
    assert measured == {"2026-08-11": 100.0}


# ── the dimensions ───────────────────────────────────────────────────────────


def test_account_groups_carry_the_operator_label_not_the_opaque_id(tmp_path: Path) -> None:
    """An `account_id` is a local hash by construction; a client filters by the
    key and must not have to show it."""
    store, series = _series(tmp_path)
    _session(store, session_id="s1", account_id="acct-max")
    _generation(store, session_id="s1", ts=_epoch(10), provider_total=100)

    (group,) = series.series(_window(), dimension="account", metric="tokens").groups

    assert group.key == "acct-max"
    assert group.label == "acct-max (max)"


def test_model_groups_split_a_switched_model_session_by_event(tmp_path: Path) -> None:
    """A series is built from events, each of which already names the model that
    produced it — so unlike a whole-session breakdown it needs no exclusion."""
    store, series = _series(tmp_path)
    _session(store, session_id="s1")
    _generation(store, session_id="s1", ts=_epoch(10), model="opus", provider_total=100)
    _generation(store, session_id="s1", ts=_epoch(10), model="haiku", provider_total=7)

    view = series.series(_window(), dimension="model", metric="tokens")

    assert {group.key: group.total for group in view.groups} == {"opus": 100.0, "haiku": 7.0}


def test_a_tool_series_counts_calls_and_ignores_events_that_named_no_tool(tmp_path: Path) -> None:
    """Tokens are attached to generations, never to tools, so `tool_calls` is
    the metric this dimension can answer honestly."""
    store, series = _series(tmp_path)
    _session(store, session_id="s1")
    _tool_call(store, session_id="s1", ts=_epoch(10), tool="Bash")
    _tool_call(store, session_id="s1", ts=_epoch(10), tool="Bash")
    _tool_call(store, session_id="s1", ts=_epoch(11), tool="Read")
    _tool_call(store, session_id="s1", ts=_epoch(11), tool=None)

    view = series.series(_window(), dimension="tool", metric="tool_calls")

    assert {group.key: [point.value for point in group.points] for group in view.groups} == {
        "Bash": [2.0, None, None],
        "Read": [None, 1.0, None],
    }


def test_a_session_with_no_account_is_stated_as_unknown_not_dropped(tmp_path: Path) -> None:
    store, series = _series(tmp_path)
    with store.write() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sources(source_id, provider, root, label) "
            "VALUES('source', 'codex', '/profile', 'profile')"
        )
        conn.execute(
            "INSERT INTO sessions(session_id, source_id, provider, models, parser_health) "
            "VALUES('s1', 'source', 'codex', '[]', 'ok')"
        )
    _generation(store, session_id="s1", ts=_epoch(10), provider_total=100)

    (group,) = series.series(_window(), dimension="account", metric="tokens").groups

    assert group.key == "unknown"
    assert group.total == 100.0


def test_token_class_groups_are_the_classes_and_absent_ones_never_appear(tmp_path: Path) -> None:
    """A class no provider reported is *not reported*, which is an absent series
    rather than a flat zero one."""
    store, series = _series(tmp_path)
    _session(store, session_id="s1")
    _generation(store, session_id="s1", ts=_epoch(10), fresh_input=10, output=4)

    view = series.series(_window(), dimension="token_class", metric="tokens")

    assert {group.key for group in view.groups} == {"fresh_input", "output"}
    assert {group.label for group in view.groups} == {"fresh input", "output"}


# ── the honesty rules ────────────────────────────────────────────────────────


def test_one_unmeasured_session_makes_the_whole_day_unmeasured(tmp_path: Path) -> None:
    """`SUM` ignores NULL, so a measured session beside an unmeasured one would
    otherwise report a confident partial total."""
    store, series = _series(tmp_path)
    _session(store, session_id="measured")
    _session(store, session_id="silent")
    _generation(store, session_id="measured", ts=_epoch(10), provider_total=100)
    _generation(store, session_id="silent", ts=_epoch(10))

    view = series.series(_window(), dimension="project", metric="tokens")

    assert [point.value for point in view.groups[0].points] == [None, None, None]
    assert view.groups[0].total is None


def test_a_day_total_prefers_the_providers_own_total_over_the_class_sum(tmp_path: Path) -> None:
    store, series = _series(tmp_path)
    _session(store, session_id="s1")
    _generation(
        store,
        session_id="s1",
        ts=_epoch(10),
        fresh_input=1,
        cache_read=1,
        cache_creation=1,
        output=1,
        provider_total=999,
    )

    (group,) = series.series(_window(), dimension="project", metric="tokens").groups

    assert group.points[0].value == 999.0


def test_active_minutes_is_all_or_null_per_day(tmp_path: Path) -> None:
    store, series = _series(tmp_path)
    _session(store, session_id="timed")
    _session(store, session_id="untimed")
    _generation(store, session_id="timed", ts=_epoch(10), duration_ms=120_000)
    _generation(store, session_id="timed", ts=_epoch(11), duration_ms=60_000)
    _generation(store, session_id="untimed", ts=_epoch(11))

    (group,) = series.series(_window(), dimension="project", metric="active_minutes").groups

    assert [point.value for point in group.points] == [2.0, None, None]


def test_active_minutes_is_the_union_so_concurrency_is_counted_once(tmp_path: Path) -> None:
    """Two overlapping intervals are 90 wall-clock minutes, not 120.

    A session's intervals span every sub-agent it spawned and those run at the
    same time, so `SUM(duration_ms)` reports more active time than the session
    that contained it — measured at a 2.26x concurrency factor on a real
    session. A duration named `active_*` is the union.
    """
    store, series = _series(tmp_path)
    _session(store, session_id="s1")
    _generation(store, session_id="s1", ts=_epoch(10, 12), duration_ms=3_600_000)
    _generation(store, session_id="s1", ts=_epoch(10, 12) + 1_800, duration_ms=3_600_000)

    (group,) = series.series(_window(), dimension="project", metric="active_minutes").groups

    assert group.points[0].value == 90.0


def test_active_minutes_agrees_with_the_summary_for_the_same_range(tmp_path: Path) -> None:
    """The defect this pins is a cross-route one: two surfaces reducing one
    interval set differently, each internally consistent and disagreeing."""
    store, series = _series(tmp_path)
    cfg = GroveConfig()
    query = UsageQuery(store=store, prices=PriceBook(cfg.usage.pricing), cfg=cfg)
    _session(store, session_id="s1")
    _session(store, session_id="s2")
    _generation(store, session_id="s1", ts=_epoch(10, 12), duration_ms=3_600_000)
    _generation(store, session_id="s1", ts=_epoch(10, 12) + 1_800, duration_ms=3_600_000)
    _generation(store, session_id="s2", ts=_epoch(10, 20), duration_ms=600_000)
    filters = UsageFilters(since=_at(10, 0), until=_at(11, 0))

    (group,) = series.series(filters, dimension="project", metric="active_minutes").groups
    summary = query.summary(filters)

    assert summary.duration.active_ms is not None
    assert group.points[0].value == summary.duration.active_ms / 60_000


def test_the_tail_is_capped_and_the_response_says_so(tmp_path: Path) -> None:
    """A capped list that looks complete is the "we only run two models"
    conclusion drawn from a row limit."""
    store, series = _series(tmp_path, max_breakdown_rows=2)
    _session(store, session_id="s1")
    for index, tokens in enumerate((10, 300, 200, 5)):
        _generation(
            store, session_id="s1", ts=_epoch(10), model=f"model-{index}", provider_total=tokens
        )

    view = series.series(_window(), dimension="model", metric="tokens")

    assert view.truncated is True
    assert [group.key for group in view.groups] == ["model-1", "model-2"]


def test_an_unmeasured_group_never_displaces_a_measured_one(tmp_path: Path) -> None:
    store, series = _series(tmp_path, max_breakdown_rows=1)
    _session(store, session_id="s1")
    _generation(store, session_id="s1", ts=_epoch(10), model="silent")
    _generation(store, session_id="s1", ts=_epoch(10), model="counted", provider_total=1)

    view = series.series(_window(), dimension="model", metric="tokens")

    assert [group.key for group in view.groups] == ["counted"]


def test_cost_is_priced_per_model_and_unknown_where_no_price_is_configured(
    tmp_path: Path,
) -> None:
    store, series = _series(
        tmp_path, pricing={"models": {"priced": {"input": 1_000_000, "output": 0}}}
    )
    _session(store, session_id="s1")
    _session(store, session_id="s2")
    _generation(store, session_id="s1", ts=_epoch(10), model="priced", fresh_input=2, output=0)
    _generation(store, session_id="s2", ts=_epoch(11), model="unpriced", fresh_input=2, output=0)

    (group,) = series.series(_window(), dimension="project", metric="cost").groups

    assert group.points[0].value == 2.0
    assert group.points[1].value is None


def test_a_filter_narrows_the_series_without_changing_its_axis(tmp_path: Path) -> None:
    store, series = _series(tmp_path)
    _session(store, session_id="claude", provider="claude_code", project="/repo/a")
    _session(store, session_id="codex", provider="codex", project="/repo/b")
    _generation(store, session_id="claude", ts=_epoch(10), provider_total=100)
    _generation(store, session_id="codex", ts=_epoch(11), provider_total=200)

    view = series.series(
        UsageFilters(since=_at(10, 0), until=_at(13, 0), provider="codex"),
        dimension="provider",
        metric="tokens",
    )

    assert view.days == ("2026-08-10", "2026-08-11", "2026-08-12")
    assert [group.key for group in view.groups] == ["codex"]


def test_the_coverage_seam_is_the_one_every_other_aggregate_answers_with(
    tmp_path: Path,
) -> None:
    """Injected rather than re-derived, so a series and a summary can never
    disagree about what the answer is based on."""
    store, _ = _series(tmp_path)
    marker = UsageCoverageView(degraded_source_count=7)
    query = UsageSeriesQuery(
        store=store,
        prices=PriceBook(GroveConfig().usage.pricing),
        cfg=GroveConfig(),
        coverage=lambda: marker,
    )

    view = query.series(_window(), dimension="model", metric="tokens")

    assert view.coverage.degraded_source_count == 7


def test_models_column_is_untouched_by_the_series(tmp_path: Path) -> None:
    """The series reads events, so a session whose `models` array is empty still
    reports every model its generations named."""
    store, series = _series(tmp_path)
    _session(store, session_id="s1")
    with store.write() as conn:
        conn.execute("UPDATE sessions SET models = ?", (json.dumps([]),))
    _generation(store, session_id="s1", ts=_epoch(10), model="opus", provider_total=5)

    (group,) = series.series(_window(), dimension="model", metric="tokens").groups

    assert group.key == "opus"
