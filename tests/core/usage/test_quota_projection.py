"""Burn-rate projection: the arithmetic, its refusals, and the read-time wiring.

Every case here is one of the two things this feature can get wrong. Either it
publishes a pace it cannot support — from a window with no duration, from a
window that has barely opened, or from tokens nobody measured — or it reaches
for a provider to answer, which is the metered act the whole quota package
exists to avoid. The pure half is driven with a fixed clock and hand-built
windows; the wired half goes through `UsageQuery` over a real cache file and
never constructs a provider at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.usage import BillingAccountView, SubscriptionWindowView
from grove.core.usage._pricing import PriceBook
from grove.core.usage._store import UsageStore
from grove.core.usage.query import UsageQuery, _window_start
from grove.core.usage.quota._projection import WindowBurnRate

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
WEEK = timedelta(days=7)


def _burn(**overrides: float) -> WindowBurnRate:
    return WindowBurnRate(clock=lambda: NOW, **overrides)  # type: ignore[arg-type]


def _window(*, elapsed: timedelta, length: timedelta = WEEK) -> tuple[datetime, datetime]:
    """A window that opened ``elapsed`` ago and runs for ``length`` in total."""
    starts_at = NOW - elapsed
    return starts_at, starts_at + length


# ─── the arithmetic ─────────────────────────────────────────────────────────


def test_exactly_on_pace_projects_the_whole_window_and_reads_tight() -> None:
    """Half the window spent on half the quota lands precisely at the limit."""
    starts_at, ends_at = _window(elapsed=WEEK / 2)

    projection = _burn().project(used_percent=50.0, starts_at=starts_at, ends_at=ends_at)

    assert projection.elapsed_percent == pytest.approx(50.0)
    assert projection.burn_rate == pytest.approx(1.0)
    assert projection.projected_percent == pytest.approx(100.0)
    assert projection.verdict == "tight"


def test_a_comfortable_pace_reads_on_track_and_names_no_exhaustion_date() -> None:
    """A window with headroom must not hand a client a date to render.

    `exhausts_at` is read as "you run out then"; naming a moment this account
    never reaches is the confidently-wrong answer, not a helpful estimate.
    """
    starts_at, ends_at = _window(elapsed=WEEK / 2)

    projection = _burn().project(used_percent=20.0, starts_at=starts_at, ends_at=ends_at)

    assert projection.projected_percent == pytest.approx(40.0)
    assert projection.verdict == "on_track"
    assert projection.exhausts_at is None


def test_an_overrunning_pace_exhausts_before_the_window_resets() -> None:
    """Over the limit, the exhaustion instant must land inside the window."""
    starts_at, ends_at = _window(elapsed=WEEK / 2)

    projection = _burn().project(used_percent=80.0, starts_at=starts_at, ends_at=ends_at)

    assert projection.projected_percent == pytest.approx(160.0)
    assert projection.verdict == "over"
    assert projection.exhausts_at is not None
    assert starts_at < projection.exhausts_at < ends_at
    # 80% in half a week means the remaining 20% takes an eighth of a week.
    assert projection.exhausts_at == pytest.approx(NOW + WEEK / 8, abs=timedelta(seconds=1))


def test_a_pace_landing_exactly_on_the_limit_exhausts_at_the_reset() -> None:
    starts_at, ends_at = _window(elapsed=WEEK / 2)

    projection = _burn().project(used_percent=50.0, starts_at=starts_at, ends_at=ends_at)

    assert projection.exhausts_at == pytest.approx(ends_at, abs=timedelta(seconds=1))


def test_a_window_read_after_its_reset_caps_elapsed_at_the_whole_window() -> None:
    """A stale reading describes a window that is over, not one 140% elapsed.

    Letting elapsed run past 100 would deflate the projection of the very
    reading a client is being told is out of date.
    """
    starts_at, ends_at = _window(elapsed=WEEK * 1.4)

    projection = _burn().project(used_percent=63.0, starts_at=starts_at, ends_at=ends_at)

    assert projection.elapsed_percent == pytest.approx(100.0)
    assert projection.projected_percent == pytest.approx(63.0)
    assert projection.verdict == "on_track"


# ─── the refusals ───────────────────────────────────────────────────────────


def test_a_window_with_no_reported_duration_is_unknown_rather_than_assumed() -> None:
    """The Claude endpoint reports no window duration, so its windows have no start.

    Filling one in from the famous "5h"/"7d" boundaries would be Grove asserting
    a limit the provider has already moved once.
    """
    projection = _burn().project(used_percent=63.0, starts_at=None, ends_at=NOW + WEEK)

    assert projection.verdict == "unknown"
    assert projection.elapsed_percent is None
    assert projection.burn_rate is None
    assert projection.projected_percent is None
    assert projection.exhausts_at is None


def test_a_window_with_no_usage_figure_is_unknown_rather_than_zero() -> None:
    starts_at, ends_at = _window(elapsed=WEEK / 2)

    projection = _burn().project(used_percent=None, starts_at=starts_at, ends_at=ends_at)

    assert projection.verdict == "unknown"
    assert projection.burn_rate is None
    assert projection.projected_percent is None


def test_a_zero_length_window_divides_by_nothing_and_says_so() -> None:
    projection = _burn().project(used_percent=50.0, starts_at=NOW, ends_at=NOW)

    assert projection.verdict == "unknown"
    assert projection.elapsed_percent is None


def test_a_window_that_has_not_opened_yet_is_unknown_not_negative() -> None:
    """Clock skew must not produce a negative elapsed and an inverted pace."""
    projection = _burn().project(
        used_percent=50.0, starts_at=NOW + timedelta(hours=1), ends_at=NOW + WEEK
    )

    assert projection.verdict == "unknown"
    assert projection.elapsed_percent is None


def test_a_barely_started_window_withholds_the_pace_but_reports_the_elapsed() -> None:
    """Below the minimum elapsed fraction the extrapolation is rounding noise.

    Providers report whole-number percentages, so at 1% elapsed one unreported
    point of usage is worth a hundred points of projection. The elapsed fraction
    itself is measured, not extrapolated, so it still rides — it is what tells a
    client why the verdict is unknown.
    """
    starts_at, ends_at = _window(elapsed=WEEK / 100)

    projection = _burn().project(used_percent=3.0, starts_at=starts_at, ends_at=ends_at)

    assert projection.elapsed_percent == pytest.approx(1.0)
    assert projection.verdict == "unknown"
    assert projection.burn_rate is None
    assert projection.projected_percent is None


def test_the_pace_appears_the_moment_the_minimum_elapsed_fraction_is_reached() -> None:
    """The gate is a floor on evidence, not a permanent silence."""
    minimum = WindowBurnRate.MIN_ELAPSED_PERCENT / 100
    starts_at, ends_at = _window(elapsed=WEEK * minimum)

    projection = _burn().project(used_percent=5.0, starts_at=starts_at, ends_at=ends_at)

    assert projection.elapsed_percent == pytest.approx(WindowBurnRate.MIN_ELAPSED_PERCENT)
    assert projection.projected_percent == pytest.approx(50.0)
    assert projection.verdict == "on_track"


# ─── tokens: measured versus extrapolated ───────────────────────────────────


def test_token_evidence_extrapolates_the_window_allowance() -> None:
    """If 25% of a window cost X tokens, the whole window is about 4X."""
    starts_at, ends_at = _window(elapsed=WEEK / 2)

    projection = _burn().project(
        used_percent=25.0, starts_at=starts_at, ends_at=ends_at, tokens_used=1_000_000
    )

    assert projection.tokens_used == 1_000_000
    assert projection.tokens_available_estimate == 4_000_000


def test_no_token_evidence_leaves_both_token_fields_null_never_zero() -> None:
    """A budget nobody measured is *not reported*, which is not the same as none."""
    starts_at, ends_at = _window(elapsed=WEEK / 2)

    projection = _burn().project(used_percent=25.0, starts_at=starts_at, ends_at=ends_at)

    assert projection.tokens_used is None
    assert projection.tokens_available_estimate is None


def test_a_window_reporting_nothing_used_cannot_size_its_own_allowance() -> None:
    """Zero percent of an unknown budget is still an unknown budget."""
    starts_at, ends_at = _window(elapsed=WEEK / 2)

    projection = _burn().project(
        used_percent=0.0, starts_at=starts_at, ends_at=ends_at, tokens_used=5_000
    )

    assert projection.tokens_used == 5_000
    assert projection.tokens_available_estimate is None


def test_measured_tokens_survive_a_withheld_pace() -> None:
    """A measurement is not an extrapolation and does not share its gate."""
    starts_at, ends_at = _window(elapsed=WEEK / 100)

    projection = _burn().project(
        used_percent=2.0, starts_at=starts_at, ends_at=ends_at, tokens_used=1_000
    )

    assert projection.verdict == "unknown"
    assert projection.tokens_used == 1_000
    assert projection.tokens_available_estimate == 50_000


# ─── thresholds are config, not literals ────────────────────────────────────


def test_the_verdict_bands_follow_the_configured_thresholds() -> None:
    """The same pace reads differently for an operator with a different appetite."""
    starts_at, ends_at = _window(elapsed=WEEK / 2)
    facts = {"used_percent": 30.0, "starts_at": starts_at, "ends_at": ends_at}

    assert _burn().project(**facts).verdict == "on_track"  # type: ignore[arg-type]
    assert _burn(tight_percent=50.0).project(**facts).verdict == "tight"  # type: ignore[arg-type]
    assert (
        _burn(tight_percent=20.0, over_percent=40.0).project(**facts).verdict == "over"  # type: ignore[arg-type]
    )


def test_config_refuses_a_tight_threshold_above_the_over_threshold() -> None:
    """An unreachable band between the two is a config nobody meant to write."""
    with pytest.raises(ValueError, match="burn_tight_percent"):
        GroveConfig.model_validate(
            {"usage": {"quota": {"burn_tight_percent": 120, "burn_over_percent": 100}}}
        )


# ─── the read-time wiring ───────────────────────────────────────────────────


def _query(tmp_path: Path, **quota: float) -> tuple[UsageStore, UsageQuery]:
    cfg = GroveConfig.model_validate({"usage": {"quota": quota}}) if quota else GroveConfig()
    store = UsageStore(tmp_path / "usage.db")
    return store, UsageQuery(store=store, prices=PriceBook(cfg.usage.pricing), cfg=cfg)


def _record_session(
    store: UsageStore, *, session_id: str, account_id: str, at: datetime, output: int | None
) -> None:
    """One indexed session with a single generation, tokens optional."""
    epoch = int(at.timestamp())
    with store.write() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sources(source_id, provider, root, label) "
            "VALUES('source', 'codex', '/profile', 'profile')"
        )
        conn.execute(
            "INSERT INTO sessions(session_id, source_id, provider, account_id, started_at, "
            "last_event_at, output, models, cost_provenance, parser_health) "
            "VALUES(?, 'source', 'codex', ?, ?, ?, ?, '[\"model-a\"]', 'unknown', 'ok')",
            (session_id, account_id, epoch, epoch, output),
        )
        conn.execute(
            "INSERT INTO usage_events(session_id, source_id, seq, ts, kind, model, "
            "fresh_input, cache_read, cache_creation, output) VALUES(?, 'source', 1, ?, "
            "'generation', 'model-a', ?, 0, 0, ?)",
            (session_id, epoch, 0 if output is not None else None, output),
        )


def _account(*, window_seconds: int | None, elapsed: timedelta) -> BillingAccountView:
    now = datetime.now(UTC)
    resets_at = now - elapsed + timedelta(seconds=window_seconds or 0)
    return BillingAccountView(
        account_id="codex-test",
        provider="codex",
        label="test",
        billing_mode="subscription",
        status="ok",
        observed_at=now,
        windows=(
            SubscriptionWindowView(
                scope="weekly",
                label="primary",
                window_seconds=window_seconds,
                used_percent=25.0,
                remaining_percent=75.0,
                resets_at=resets_at if window_seconds else now + WEEK,
                observed_at=now,
            ),
        ),
    )


def test_quotas_attaches_a_projection_built_from_indexed_tokens(tmp_path: Path) -> None:
    """The wired read answers from the cache alone — no provider is constructed."""
    store, query = _query(tmp_path)
    try:
        _record_session(
            store,
            session_id="s1",
            account_id="codex-test",
            at=datetime.now(UTC) - timedelta(days=1),
            output=1_000,
        )
        window = query.quotas((_account(window_seconds=604_800, elapsed=WEEK / 2),)).accounts[0]
        projection = window.windows[0].projection

        assert projection is not None
        # 25% used at half elapsed extrapolates to half the window's budget.
        assert projection.elapsed_percent == pytest.approx(50.0, abs=0.1)
        assert projection.projected_percent == pytest.approx(50.0, abs=0.2)
        assert projection.verdict == "on_track"
        assert projection.tokens_used == 1_000
        assert projection.tokens_available_estimate == 4_000
    finally:
        store.close()


def test_one_unmeasured_session_in_the_window_makes_the_token_total_null(
    tmp_path: Path,
) -> None:
    """A partial sum is a confident wrong number; the pace half still answers."""
    store, query = _query(tmp_path)
    try:
        yesterday = datetime.now(UTC) - timedelta(days=1)
        _record_session(store, session_id="s1", account_id="codex-test", at=yesterday, output=1_000)
        _record_session(store, session_id="s2", account_id="codex-test", at=yesterday, output=None)
        projection = (
            query.quotas((_account(window_seconds=604_800, elapsed=WEEK / 2),))
            .accounts[0]
            .windows[0]
            .projection
        )

        assert projection is not None
        assert projection.tokens_used is None
        assert projection.tokens_available_estimate is None
        assert projection.projected_percent is not None
    finally:
        store.close()


def test_a_window_with_no_duration_is_projected_as_unknown_end_to_end(tmp_path: Path) -> None:
    """The whole Claude provider lands here, so it must be exercised end to end."""
    store, query = _query(tmp_path)
    try:
        _record_session(
            store,
            session_id="s1",
            account_id="codex-test",
            at=datetime.now(UTC) - timedelta(days=1),
            output=1_000,
        )
        projection = (
            query.quotas((_account(window_seconds=None, elapsed=WEEK / 2),))
            .accounts[0]
            .windows[0]
            .projection
        )

        assert projection is not None
        assert projection.verdict == "unknown"
        assert projection.tokens_used is None
    finally:
        store.close()


def test_an_operator_declared_duration_unlocks_a_provider_that_publishes_none() -> None:
    """Anthropic's usage endpoint reports a reset instant and no duration, so
    without a declared length every Claude window is honestly `unknown` however
    much of it has been spent. `usage.quota.window_seconds` is the operator
    asserting the boundary, which is a different act from Grove assuming it."""
    resets = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    window = SubscriptionWindowView(scope="weekly", label="weekly_all", resets_at=resets)

    assert _window_start(window, {}) is None
    assert _window_start(window, {"weekly_all": 604800}) == resets - timedelta(days=7)
    assert _window_start(window, {"other": 604800}) is None
    assert _window_start(window, {"weekly_all": 0}) is None


def test_the_provider_outranks_the_declared_duration() -> None:
    """A provider that publishes a duration has just told you the current
    truth; a config value was written once and is what goes stale when a
    boundary moves."""
    resets = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    window = SubscriptionWindowView(
        scope="weekly", label="primary", window_seconds=3600, resets_at=resets
    )
    assert _window_start(window, {"primary": 604800}) == resets - timedelta(hours=1)


def test_the_resolved_duration_is_published_whoever_supplied_it() -> None:
    """A client normalizing this window needs the SAME denominator as the verdict.

    Claude publishes no window duration, so its windows are projected against
    the operator's declared span — and a consumer that wanted to express the
    window as "tokens per day" had no way to learn which span that was, because
    ``SubscriptionWindowView.window_seconds`` stays the provider's own (absent) answer.
    """
    starts_at, ends_at = _window(elapsed=WEEK / 2)
    projection = _burn().project(used_percent=50.0, starts_at=starts_at, ends_at=ends_at)
    assert projection.resolved_window_seconds == int(WEEK.total_seconds())


def test_a_withheld_pace_still_publishes_the_resolved_duration() -> None:
    """Below MIN_ELAPSED_PERCENT the verdict is refused; the span is not a guess."""
    starts_at, ends_at = _window(elapsed=timedelta(minutes=30))
    projection = _burn().project(used_percent=3.0, starts_at=starts_at, ends_at=ends_at)
    assert projection.verdict == "unknown"
    assert projection.resolved_window_seconds == int(WEEK.total_seconds())


def test_no_bounds_means_no_resolved_duration() -> None:
    """Nobody said how long the window is, so Grove does not invent a span."""
    projection = _burn().project(used_percent=50.0, starts_at=None, ends_at=None)
    assert projection.resolved_window_seconds is None
