"""Bounded read projections over the regenerable usage cache."""

# SQL stays next to the contract view it builds; long query strings are clearer
# here than when split into a second catalogue of fragments.
# ruff: noqa: E501, F405

from __future__ import annotations

import base64
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Final

from grove.core.contracts.usage import *  # noqa: F403
from grove.core.usage._intervals import ActiveIntervals
from grove.core.usage._pricing import PriceBook, TokenCounts
from grove.core.usage._store import DayBucket, UsageStore, day_boundaries, resolve_zone
from grove.core.usage.quota._projection import WindowBurnRate

DAY_EVENT_JOIN: Final = (
    "FROM usage_events e "
    "JOIN sessions s ON s.session_id=e.session_id AND s.source_id=e.source_id "
    "JOIN bucket ON e.ts >= bucket.day_start AND e.ts < bucket.day_end"
)
"""The one join every calendar-grained read makes: events, their session, and
the day they land in. Written once so the grains cannot end up over different
rows, and public because ``series.py`` asks the same question at a second
grain."""


def day_calendar_sql(buckets: Sequence[DayBucket]) -> tuple[str, list[Any]]:
    """The day spine as a CTE, plus its parameters — bound, never interpolated.

    A day is a local-midnight span in the READER's zone — 23 or 25 hours across
    a DST transition — so no expression over epoch seconds can derive it; the
    boundaries have to be carried into SQL as values. Handing SQLite the whole
    calendar at once is what lets a calendar-range answer be ONE grouped query
    instead of one query per day.
    """
    values = ", ".join("(?,?,?)" for _ in buckets)
    params = [value for bucket in buckets for value in (bucket.day, bucket.start, bucket.end)]
    return f"WITH bucket(day, day_start, day_end) AS (VALUES {values})", params


def _dt(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value, UTC) if value is not None else None


def _window_start(window: SubscriptionWindowView, declared: Mapping[str, int]) -> datetime | None:
    """When this window opened, or ``None`` when nobody said how long it is.

    A window is published as a reset instant plus, sometimes, a duration — so
    the start is derivable only where both exist. The Claude endpoint reports no
    duration at all, which is why that whole provider's windows carry no
    projection rather than one built on an assumed five hours or seven days.

    *declared* is ``usage.quota.window_seconds``, the operator's own answer for
    exactly that gap, and it is consulted **only** where the provider was
    silent. The precedence matters more than it looks: a provider that publishes
    a duration has just told you the current truth, while a config value was
    written once and is what goes stale when a boundary moves. Grove will not
    assume a window; it will use one you assert.
    """
    if window.resets_at is None:
        return None
    seconds = window.window_seconds or declared.get(window.label)
    if not seconds or seconds <= 0:
        return None
    return window.resets_at - timedelta(seconds=seconds)


def _tokens(row: Any, prefix: str = "") -> TokenClassesView:
    return TokenClassesView(
        fresh_input=row[f"{prefix}fresh_input"],
        cache_read=row[f"{prefix}cache_read"],
        cache_creation=row[f"{prefix}cache_creation"],
        reasoning=row[f"{prefix}reasoning"],
        output=row[f"{prefix}output"],
        provider_total=row[f"{prefix}provider_total"],
    )


def _subagent_tokens(row: Any) -> TokenClassesView | None:
    """The sub-agent-only portion of a session's totals, or ``None`` when
    nothing sidechain-attributed was measured for it.

    ``row``'s combined ``fresh_input``/``cache_read``/etc (read via the bare
    ``_tokens(row)``) already include this delegated work — the projector
    sums root and sub-agent messages together into ONE total, per the
    attribution rule. These ``subagent_*`` columns are the only place that
    portion is separated back out, so this is a partition of an existing sum,
    never a second read or a second ingest path. All-``None`` reads as
    genuinely unmeasured (never a fabricated ``0``), collapsed to a bare
    ``None`` rather than a `TokenClassesView` of nothing.
    """
    view = _tokens(row, prefix="subagent_")
    if all(
        getattr(view, field) is None
        for field in (
            "fresh_input",
            "cache_read",
            "cache_creation",
            "reasoning",
            "output",
            "provider_total",
        )
    ):
        return None
    return view


_TOKEN_COLUMNS = ("fresh_input", "cache_read", "cache_creation", "reasoning", "output")
_BREAKDOWN_MEASURE_COLUMNS = ("provider_total", *_TOKEN_COLUMNS, "active_ms")


def _complete_sum(column: str) -> str:
    """SQL sum that stays null when any selected session lacks the measurement."""
    return f"CASE WHEN COUNT(*)=0 OR COUNT({column})<COUNT(*) THEN NULL ELSE SUM({column}) END"


def _complete_total() -> str:
    return _complete_sum("provider_total")


def _complete_breakdown_sums(prefix: str = "") -> str:
    return ", ".join(
        f"{_complete_sum(f'{prefix}{column}')} {column}" for column in _BREAKDOWN_MEASURE_COLUMNS
    )


def _group_total(row: Any) -> int | None:
    return int(row["provider_total"]) if row["provider_total"] is not None else None


def _measured_total(row: Any) -> int | None:
    provider_total = _group_total(row)
    if provider_total is not None:
        return provider_total
    required = ("fresh_input", "cache_read", "cache_creation", "output")
    if any(row[column] is None for column in required):
        return None
    return sum(int(row[column]) for column in required) + int(row["reasoning"] or 0)


def _measured_sum(values: list[int | None]) -> tuple[float, bool]:
    """Sum what was measured, and say whether the answer is worth publishing.

    The second element is *not* completeness. A day where nineteen sessions of
    twenty reported tokens still spent those tokens, so the sum stands and the
    day is drawn; only a day that HAS sessions and measured none of them is
    withheld, because there the sum is a fabricated zero. An empty list is a
    genuine zero — no sessions spent no tokens — and keeps its answer.

    This is `ticketRollup`'s rule at a second call site: an unmeasured member
    scoring zero can only ever understate, which is the honest failure mode for
    a partial read, while dropping it lets everything that remains overstate.
    """
    measured = [value for value in values if value is not None]
    return float(sum(measured)), not values or bool(measured)


def _complete_group_sum(groups: list[Any], column: str) -> int | None:
    if not groups or any(row[column] is None for row in groups):
        return None
    return sum(int(row[column]) for row in groups)


def _complete_group_total(groups: list[Any]) -> int | None:
    totals = [_group_total(row) for row in groups]
    measured = [total for total in totals if total is not None]
    if not totals or len(measured) != len(totals):
        return None
    return sum(measured)


def _has_temporal_filter(filters: UsageFilters) -> bool:
    if filters.since is not None or filters.until is not None:
        return True
    if filters.day is None:
        return False
    try:
        date.fromisoformat(filters.day)
    except ValueError:
        return False
    return True


class UsageQuery:
    """Construct frozen wire views from parameterized, capped SQLite reads."""

    def __init__(self, *, store: UsageStore, prices: PriceBook, cfg: Any) -> None:
        self.store, self.prices, self.cfg = store, prices, cfg
        self.refresh_cost_cache()

    def summary(self, filters: UsageFilters) -> UsageSummaryView:
        if _has_temporal_filter(filters):
            return self._temporal_summary(filters)
        where, params = self._where(filters)
        row = self.store.query(
            f"SELECT COUNT(*) sessions, SUM(turns) turns, SUM(tool_calls) tool_calls, SUM(tool_failures) tool_failures, SUM(files_changed) files_changed, {_complete_sum('active_ms')} active_ms, {_complete_sum('execution_ms')} execution_ms, {_complete_sum('generation_ms')} generation_ms, {_complete_sum('tool_ms')} tool_ms, {_complete_sum('elapsed_span_ms')} elapsed_span_ms, COUNT(DISTINCT account_id) accounts, COUNT(DISTINCT project) projects, {_complete_sum('fresh_input')} fresh_input, {_complete_sum('cache_read')} cache_read, {_complete_sum('cache_creation')} cache_creation, {_complete_sum('reasoning')} reasoning, {_complete_sum('output')} output, {_complete_total()} provider_total FROM sessions WHERE {where}",
            params,
        )[0]
        event_where, event_params = self._event_where(filters)
        cost = self._cost(where, params)
        coverage = self.coverage().model_copy(update={"cost_available": cost is not None})
        return UsageSummaryView(
            since=filters.since,
            until=filters.until,
            tz=filters.tz,
            sessions=row["sessions"] or 0,
            turns=row["turns"] or 0,
            tokens=_tokens(row),
            duration=DurationView(
                active_ms=row["active_ms"],
                execution_ms=row["execution_ms"],
                generation_ms=row["generation_ms"],
                tool_ms=row["tool_ms"],
                elapsed_span_ms=row["elapsed_span_ms"],
                confidence="derived" if row["elapsed_span_ms"] is not None else "unknown",
            ),
            tools=UsageToolStatsView(
                calls=row["tool_calls"] or 0,
                failures=row["tool_failures"] or 0,
                distinct_tools=self.store.scalar(
                    f"SELECT COUNT(DISTINCT e.tool_name) FROM usage_events e JOIN sessions s ON s.session_id=e.session_id AND s.source_id=e.source_id WHERE {event_where} AND e.tool_name IS NOT NULL",
                    event_params,
                )
                or 0,
            ),
            files_changed=row["files_changed"] or 0,
            cost=cost,
            accounts=row["accounts"] or 0,
            projects=row["projects"] or 0,
            coverage=coverage,
        )

    def sessions(
        self, filters: UsageFilters, *, cursor: str | None, limit: int, sort: UsageSessionSort
    ) -> UsageSessionPageView:
        limit = min(max(1, limit), self.cfg.usage.max_sessions_per_page)
        where, params = self._session_where(filters, alias="s")
        expression = {
            "recent": "s.last_event_at",
            "tokens": "s.provider_total",
            "cost": "CAST(s.cost_amount AS REAL)",
            "duration": "s.elapsed_span_ms",
            "tool_failures": "s.tool_failures",
        }[sort]
        after = _decode_cursor(cursor, sort)
        if after is not None:
            key, last_event, session_id, source_id = after
            id_after = "s.session_id < ? OR (s.session_id = ? AND s.source_id < ?)"
            if last_event is None:
                time_after = f"s.last_event_at IS NULL AND ({id_after})"
                time_params: list[Any] = [session_id, session_id, source_id]
            else:
                time_after = (
                    "s.last_event_at < ? OR s.last_event_at IS NULL OR "
                    f"(s.last_event_at = ? AND ({id_after}))"
                )
                time_params = [last_event, last_event, session_id, session_id, source_id]
            if key is None:
                where += f" AND {expression} IS NULL AND ({time_after})"
                params.extend(time_params)
            else:
                where += (
                    f" AND ({expression} < ? OR {expression} IS NULL OR "
                    f"({expression} = ? AND ({time_after})))"
                )
                params.extend((key, key, *time_params))
        rows = self.store.query(
            f"SELECT s.*, a.label account_label, {expression} sort_key FROM sessions s LEFT JOIN accounts a ON a.account_id=s.account_id WHERE {where} ORDER BY ({expression} IS NULL), {expression} DESC, s.last_event_at DESC, s.session_id DESC, s.source_id DESC LIMIT ?",
            [*params, limit + 1],
        )
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            tail = page[-1]
            next_cursor = _encode_cursor(
                sort,
                tail["sort_key"],
                tail["last_event_at"],
                tail["session_id"],
                tail["source_id"],
            )
        bounded = (
            {(row["session_id"], row["source_id"]): row for row in self._event_groups(filters)}
            if _has_temporal_filter(filters)
            else {}
        )
        return UsageSessionPageView(
            rows=tuple(
                self._session(row, bounded.get((row["session_id"], row["source_id"])))
                for row in page
            ),
            next_cursor=next_cursor,
            sort=sort,
            coverage=self.coverage(),
        )

    def activity(self, filters: UsageFilters, *, metric: UsageMetric) -> UsageActivityView:
        """One metric per calendar day, over the whole filtered range.

        **The reads are per RANGE, never per day.** This loop used to issue a
        fresh ``_event_groups`` + ``_event_intervals`` pair inside it, so a
        year-wide window was ~730 round trips over the same event corpus and
        the page it feeds timed out in the browser: measured on a real 1.34M-
        event cache, one request cost 30.7 s for ``tokens`` and 74.3 s for
        ``cost`` (which paid a third and fourth query per day), against 11.2 s
        for the ``summary`` beside it that is not per-day. The day is a
        GROUP BY column now — carried into SQL by :func:`day_calendar_sql`,
        because only the reader's zone can say where a day starts — and the
        buckets are assembled in Python from the one result set. **A per-bucket
        query over a calendar range is O(days) round trips; if a new answer
        needs a calendar, group by the spine rather than looping it.**
        """
        earliest = self.store.scalar("SELECT MIN(ts) FROM usage_events WHERE ts IS NOT NULL")
        latest = self.store.scalar("SELECT MAX(ts) FROM usage_events WHERE ts IS NOT NULL")
        since = filters.since or _dt(earliest) or self._now()
        latest_at = _dt(latest)
        until = filters.until or (
            latest_at + timedelta(seconds=1) if latest_at is not None else self._now()
        )
        bucket_until = until - timedelta(microseconds=1) if until > since else until
        spine = day_boundaries(since, bucket_until, filters.tz)
        # Only one metric reads the interval union, and it is the read that
        # walks every EVENT rather than every session — so asking for it
        # unconditionally made four of the five metrics pay the largest query
        # on the page for a column nothing on their path looks at.
        daily = self._daily_event_groups(filters, spine, with_active_ms=metric == "active_minutes")
        costs = self._daily_event_cost(filters, spine, daily) if metric == "cost" else {}
        buckets: list[UsageActivityBucketView] = []
        cost_complete = True
        cost_sessions = 0
        for bucket in spine:
            groups = daily.get(bucket.day, [])
            sessions = len(groups)
            value, reportable = self._activity_value(metric, groups, cost=costs.get(bucket.day))
            if metric == "cost" and sessions:
                cost_sessions += sessions
                cost_complete = cost_complete and reportable
            if not reportable:
                continue
            buckets.append(
                UsageActivityBucketView(
                    day=bucket.day,
                    value=value,
                    sessions=sessions,
                )
            )
        coverage = self.coverage()
        if metric == "cost":
            coverage = coverage.model_copy(
                update={"cost_available": cost_complete and cost_sessions > 0}
            )
        return UsageActivityView(
            metric=metric,
            tz=filters.tz,
            buckets=tuple(buckets),
            total=sum(bucket.value for bucket in buckets),
            max_value=max((bucket.value for bucket in buckets), default=0.0),
            coverage=coverage,
        )

    def _activity_value(
        self,
        metric: UsageMetric,
        groups: list[dict[str, Any]],
        *,
        cost: MoneyView | None,
    ) -> tuple[float, bool]:
        """This day's value, and whether the bucket may be drawn at all.

        A dropped bucket is not the same refusal as a null: the heatmap has no
        third state, so a day withheld here renders exactly like a day nobody
        worked. That makes all-or-nothing the WRONG rule for a sum — one
        unmeasured session out of twenty turned a day of work into a day of
        idleness, a positive false claim where the package's own rule only ever
        promises to say *unknown*. Hence `_measured_sum`: a partial day reports
        what it can support, and only a day with sessions and nothing measured
        stays absent.

        ``cost`` keeps all-or-nothing, and the difference is that it already
        HAS the missing third state — a range cost is complete or unavailable
        (`coverage.cost_available`), never a partial sum, because a half-priced
        total presented as money is wrong in a way a token count is not.

        ``cost`` arrives already priced for this day (:meth:`_daily_event_cost`)
        rather than being fetched here, because a read issued from inside the
        calendar loop is a read per day.
        """
        if metric == "tokens":
            return _measured_sum([_measured_total(row) for row in groups])
        if metric == "active_minutes":
            minutes, reportable = _measured_sum([row["active_ms"] for row in groups])
            return minutes / 60_000, reportable
        if metric == "sessions":
            return float(len(groups)), True
        if metric == "tool_calls":
            return float(sum(row["tool_calls"] or 0 for row in groups)), True
        if not groups:
            return 0.0, True
        return (float(cost.amount), True) if cost is not None else (0.0, False)

    def breakdown(self, filters: UsageFilters, *, dimension: UsageDimension) -> UsageBreakdownView:
        view = self._grouped(filters, dimension)
        # Applied HERE rather than inside `_model_breakdown`, because that
        # method serves only the untimed route: `breakdown` forks on
        # `_has_temporal_filter` first, so a model breakdown carrying any date
        # bound went down `_temporal_breakdown` and never met the merge at all.
        # The column then fell back to its field default — `avg_ms=None`, which
        # is contracted as *never measured* — so `?dimension=model&since=…`
        # reported every model as unmeasurable rather than as unimplemented.
        # Invisible on the audit page, which passes no filters today, and
        # waiting for the first range picker anyone adds.
        return self._with_model_latency(view, filters) if dimension == "model" else view

    def _grouped(self, filters: UsageFilters, dimension: UsageDimension) -> UsageBreakdownView:
        """The rows for one dimension, before any per-dimension decoration."""
        if dimension == "tool":
            return self._tool_breakdown(filters)
        if _has_temporal_filter(filters):
            if dimension == "token_class":
                return self._temporal_token_breakdown(filters)
            return self._temporal_breakdown(filters, dimension)
        if dimension == "token_class":
            return self._token_breakdown(filters)
        if dimension == "model":
            return self._model_breakdown(filters)
        column = {"provider": "provider", "account": "account_id", "project": "project"}[dimension]
        where, params = self._where(filters)
        rows = self.store.query(
            f"SELECT {column} key, COUNT(*) sessions, {_complete_breakdown_sums()}, SUM(tool_calls) tool_calls, SUM(tool_failures) tool_failures, CASE WHEN COUNT(cost_amount)<COUNT(*) THEN NULL ELSE SUM(CAST(cost_amount AS REAL)) END cost_amount FROM sessions WHERE {where} GROUP BY {column} ORDER BY sessions DESC LIMIT ?",
            [*params, self.cfg.usage.max_breakdown_rows + 1],
        )
        return self._breakdown_view(dimension, rows)

    def _with_model_latency(
        self, view: UsageBreakdownView, filters: UsageFilters
    ) -> UsageBreakdownView:
        """Decorate model rows with the average wait their own sessions support."""
        latency = self._model_generation_latency(filters)
        if not latency:
            return view
        return view.model_copy(
            update={
                "rows": tuple(
                    row.model_copy(update={"latency": latency[row.key]})
                    if row.key in latency
                    else row
                    for row in view.rows
                )
            }
        )

    def quotas(self, accounts: tuple[BillingAccountView, ...]) -> UsageQuotasView:
        available = any(account.status in {"ok", "stale"} for account in accounts)
        coverage = self.coverage().model_copy(update={"quota_available": available})
        return UsageQuotasView(
            accounts=tuple(self._projected(account) for account in accounts),
            coverage=coverage,
        )

    def _projected(self, account: BillingAccountView) -> BillingAccountView:
        """The account with each window's burn rate attached.

        Derived here rather than stored, because a projection is a statement
        about *now* against a reading taken earlier: persisting one would serve
        an answer that ages while the numbers it was built from do not. It costs
        no provider request, which is the constraint that shapes this whole
        package.
        """
        quota = self.cfg.usage.quota
        burn = WindowBurnRate(
            clock=self._now,
            tight_percent=quota.burn_tight_percent,
            over_percent=quota.burn_over_percent,
        )
        windows = tuple(
            window.model_copy(
                update={
                    "projection": burn.project(
                        used_percent=window.used_percent,
                        starts_at=_window_start(window, quota.window_seconds),
                        ends_at=window.resets_at,
                        tokens_used=self._window_tokens(account.account_id, window),
                    )
                }
            )
            for window in account.windows
        )
        return account.model_copy(update={"windows": windows})

    def _window_tokens(self, account_id: str, window: SubscriptionWindowView) -> int | None:
        """Tokens this account has measurably spent inside the window's bounds.

        Reuses `_event_groups` so the window inherits every rule temporal
        aggregates already obey — event timestamps rather than session endings,
        and a per-session completeness check that keeps Codex's one cumulative
        report per session from reading as a partial total. All-or-null for the
        reason the whole package is: one unmeasured session in the window makes
        a confident sum wrong in the direction nobody checks.
        """
        starts_at = _window_start(window, self.cfg.usage.quota.window_seconds)
        if starts_at is None or window.resets_at is None:
            return None
        end = min(self._now(), window.resets_at)
        measured = [
            _measured_total(row)
            for row in self._event_groups(
                UsageFilters(account=account_id),
                start=int(starts_at.timestamp()),
                end=int(end.timestamp()),
            )
        ]
        if not measured or None in measured:
            return None
        return sum(total for total in measured if total is not None)

    def coverage(self) -> UsageCoverageView:
        refresh = self.store.meta("last_refresh_at")
        rows = self.store.query(
            "SELECT src.source_id, src.provider, src.label, src.health, src.detail, src.last_indexed_at, COUNT(s.session_id) session_count FROM sources src LEFT JOIN sessions s ON s.source_id=src.source_id GROUP BY src.source_id"
        )
        bounds = self.store.query(
            "SELECT MIN(started_at) earliest, MAX(last_event_at) latest FROM sessions"
        )[0]
        return UsageCoverageView(
            sources=tuple(
                UsageSourceView(
                    source_id=row["source_id"],
                    provider=row["provider"],
                    label=row["label"],
                    health=row["health"],
                    detail=row["detail"],
                    last_indexed_at=_dt(row["last_indexed_at"]),
                    session_count=row["session_count"] or 0,
                )
                for row in rows
            ),
            degraded_source_count=sum(row["health"] != "ok" for row in rows),
            earliest_event_at=_dt(bounds["earliest"]),
            latest_event_at=_dt(bounds["latest"]),
            last_refresh_at=_dt(int(refresh)) if refresh else None,
            cost_available=self.prices.has_prices,
            quota_available=bool(self.store.scalar("SELECT COUNT(*) FROM quota_snapshots")),
        )

    def _cost(self, where: str, params: list[Any]) -> MoneyView | None:
        rows = self.store.query(
            f"SELECT models, fresh_input, cache_read, cache_creation, output "
            f"FROM sessions WHERE {where}",
            params,
        )
        if not rows:
            return None
        total = Decimal(0)
        for row in rows:
            models = _models(row["models"])
            amount = self.prices.amount(
                models[0] if len(models) == 1 else None,
                TokenCounts(
                    fresh_input=row["fresh_input"],
                    cache_read=row["cache_read"],
                    cache_creation=row["cache_creation"],
                    output=row["output"],
                ),
            )
            if amount is None:
                return None
            total += amount
        return self.prices.money(total)

    def _temporal_summary(self, filters: UsageFilters) -> UsageSummaryView:
        groups = self._event_groups(filters)
        sessions = len(groups)
        event_where, event_params = self._event_where(filters)
        distinct_tools = self.store.scalar(
            f"SELECT COUNT(DISTINCT e.tool_name) FROM usage_events e JOIN sessions s ON s.session_id=e.session_id AND s.source_id=e.source_id WHERE {event_where} AND e.tool_name IS NOT NULL",
            event_params,
        )
        tokens = TokenClassesView(
            fresh_input=_complete_group_sum(groups, "fresh_input"),
            cache_read=_complete_group_sum(groups, "cache_read"),
            cache_creation=_complete_group_sum(groups, "cache_creation"),
            reasoning=_complete_group_sum(groups, "reasoning"),
            output=_complete_group_sum(groups, "output"),
            provider_total=_complete_group_sum(groups, "provider_total"),
        )
        active = _complete_group_sum(groups, "active_ms")
        execution = _complete_group_sum(groups, "execution_ms")
        cost = self._event_cost(filters)
        coverage = self.coverage().model_copy(update={"cost_available": cost is not None})
        return UsageSummaryView(
            since=filters.since,
            until=filters.until,
            tz=filters.tz,
            sessions=sessions,
            turns=sum(row["turns"] or 0 for row in groups),
            tokens=tokens,
            duration=DurationView(
                active_ms=active,
                execution_ms=execution,
                generation_ms=_complete_group_sum(groups, "generation_ms"),
                tool_ms=_complete_group_sum(groups, "tool_ms"),
                elapsed_span_ms=None,
                confidence="derived" if active is not None else "unknown",
            ),
            tools=UsageToolStatsView(
                calls=sum(row["tool_calls"] or 0 for row in groups),
                failures=sum(row["tool_failures"] or 0 for row in groups),
                distinct_tools=distinct_tools or 0,
            ),
            files_changed=sum(row["files_changed"] or 0 for row in groups),
            cost=cost,
            accounts=len({row["account_id"] for row in groups if row["account_id"]}),
            projects=len({row["project"] for row in groups if row["project"]}),
            coverage=coverage,
        )

    def _day_scope(
        self, filters: UsageFilters, buckets: Sequence[DayBucket]
    ) -> tuple[str, str, list[Any]]:
        """The calendar CTE, the compiled facet filter, and their parameters.

        The three calendar-grained reads below are one window seen at three
        grains, so the window is compiled once: three copies of a filter is
        three chances for one of them to answer about a slightly different set
        of events. Calendar parameters lead because the CTE leads the SQL.
        """
        where, params = self._event_where(filters)
        calendar, calendar_params = day_calendar_sql(buckets)
        return calendar, where, [*calendar_params, *params]

    def _daily_event_groups(
        self, filters: UsageFilters, buckets: Sequence[DayBucket], *, with_active_ms: bool
    ) -> dict[str, list[dict[str, Any]]]:
        """:meth:`_event_groups`' shape for every day of the spine, in ONE read.

        The per-session grain is load-bearing rather than incidental:
        completeness is a property of a session (``SUM`` silently ignores NULL),
        so a day folded in SQL would report one measured session plus one
        unmeasured one as a confident partial total. The fold up to a day's
        value stays in ``_activity_value``, which is the only thing that knows
        the partial-day rule.

        Only the columns ``activity`` actually consumes are selected, because
        this read scans the whole range rather than one day of it.

        ``with_active_ms`` gates the second, far larger read — measured on a
        1.34M-event cache, the grouped aggregate here returns 1,301 rows for
        1.34 s while the interval read returns 594,960 for 1.11 s plus a Python
        merge, so the metrics that never look at the union were paying twice
        over. **Withheld means the key is ABSENT, never ``None``**: ``None`` is
        contracted as *this session was never timed*, so defaulting it would let
        a metric that forgot to ask report a real day as unmeasured. A missing
        key raises, which is the honest failure for a question nobody asked.
        """
        if not buckets:
            return {}
        calendar, where, params = self._day_scope(filters, buckets)
        rows = self.store.query(
            f"{calendar} SELECT bucket.day day, e.session_id session_id, e.source_id source_id, "
            f"SUM(CASE WHEN e.kind IN ('tool_call','file_edit') THEN 1 ELSE 0 END) tool_calls, "
            f"SUM(e.fresh_input) fresh_input, SUM(e.cache_read) cache_read, "
            f"SUM(e.cache_creation) cache_creation, SUM(e.reasoning) reasoning, "
            f"SUM(e.output) output, SUM(e.provider_total) provider_total "
            f"{DAY_EVENT_JOIN} "
            f"WHERE {where} GROUP BY bucket.day, e.session_id, e.source_id",
            params,
        )
        intervals = self._daily_event_intervals(calendar, where, params) if with_active_ms else {}
        daily: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            group = dict(row)
            day = str(group["day"])
            if with_active_ms:
                spans = intervals.get(day, {}).get(
                    (row["session_id"], row["source_id"]), ActiveIntervals()
                )
                group["active_ms"] = spans.union_ms()
            daily[day].append(group)
        return daily

    def _daily_event_intervals(
        self, calendar: str, where: str, params: list[Any]
    ) -> dict[str, dict[tuple[str, str], ActiveIntervals]]:
        """Each (day, session)'s active intervals, over the whole spine at once.

        Still a second read rather than a cleverer aggregate, for the reason
        :meth:`_event_intervals` gives: no SQL aggregate merges overlaps without
        a second copy of the merge living in SQL, where it would drift from
        ``_intervals.py``. What changed is only how many times it is issued —
        once per range instead of once per day.

        A session's intervals are kept per DAY rather than merged across the
        range, because that is the population each bucket's ``active_ms``
        describes; an event's ``ts`` is the END of its interval, so the start is
        ``ts - duration_ms``. Reads via :meth:`UsageStore.query_tuples` for
        :meth:`_event_intervals`' reason — this is a per-EVENT loop, and named
        ``sqlite3.Row`` access re-resolves each column by string on every row.
        """
        rows = self.store.query_tuples(
            f"{calendar} SELECT bucket.day, e.session_id, e.source_id, e.ts, e.duration_ms "
            f"{DAY_EVENT_JOIN} "
            f"WHERE {where} AND e.ts IS NOT NULL AND e.duration_ms IS NOT NULL",
            params,
        )
        collected: dict[str, dict[tuple[str, str], list[tuple[int, int]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for day, session_id, source_id, ts, duration_ms in rows:
            finished = int(ts) * 1000
            collected[str(day)][(session_id, source_id)].append(
                (finished - int(duration_ms), finished)
            )
        return {
            day: {key: ActiveIntervals.of(spans) for key, spans in sessions.items()}
            for day, sessions in collected.items()
        }

    def _daily_event_cost(
        self,
        filters: UsageFilters,
        buckets: Sequence[DayBucket],
        daily: Mapping[str, list[dict[str, Any]]],
    ) -> dict[str, MoneyView | None]:
        """:meth:`_event_cost`'s answer for every day of the spine, in ONE read.

        Money keeps its all-or-nothing rule here, unlike the summed metrics:
        a day where one contributing session reported no token evidence, or
        whose model has no configured price, is ``None`` rather than a partial
        sum — and ``coverage.cost_available`` is where that ``None`` is said out
        loud. Priced per (day, session, MODEL) before anything is added, because
        a day spanning two models has no single price.

        Days absent from *daily* are absent here too: a day with no sessions is
        a genuine zero the caller answers without consulting a price at all.
        """
        if not buckets:
            return {}
        calendar, where, params = self._day_scope(filters, buckets)
        rows = self.store.query(
            f"{calendar} SELECT bucket.day day, e.session_id session_id, e.source_id source_id, "
            f"e.model model, SUM(e.fresh_input) fresh_input, SUM(e.cache_read) cache_read, "
            f"SUM(e.cache_creation) cache_creation, SUM(e.output) output "
            f"{DAY_EVENT_JOIN} "
            f"WHERE {where} AND (e.fresh_input IS NOT NULL OR e.cache_read IS NOT NULL OR "
            f"e.cache_creation IS NOT NULL OR e.output IS NOT NULL) "
            f"GROUP BY bucket.day, e.session_id, e.source_id, e.model",
            params,
        )
        priced: dict[str, list[Any]] = defaultdict(list)
        for row in rows:
            priced[str(row["day"])].append(row)
        costs: dict[str, MoneyView | None] = {}
        for day, groups in daily.items():
            if not groups:
                continue
            measured = priced.get(day, [])
            if len({(row["session_id"], row["source_id"]) for row in measured}) != len(groups):
                costs[day] = None
                continue
            costs[day] = self.prices.money(
                self.prices.total(
                    (
                        row["model"],
                        TokenCounts(
                            fresh_input=row["fresh_input"],
                            cache_read=row["cache_read"],
                            cache_creation=row["cache_creation"],
                            output=row["output"],
                        ),
                    )
                    for row in measured
                )
            )
        return costs

    def _event_groups(
        self, filters: UsageFilters, *, start: int | None = None, end: int | None = None
    ) -> list[dict[str, Any]]:
        """Per-session aggregates over the events inside the filtered window.

        Returns plain dicts rather than rows because ``active_ms`` cannot come
        out of an aggregate: ``SUM(duration_ms)`` is the sum reducer — the
        labour total, ``execution_ms`` — and the union is folded in here from
        the same events' own endpoints.
        """
        where, params = self._event_where(filters)
        if start is not None:
            where += " AND e.ts >= ?"
            params.append(start)
        if end is not None:
            where += " AND e.ts < ?"
            params.append(end)
        rows = self.store.query(
            f"SELECT s.session_id, s.source_id, s.provider, s.account_id, s.project, s.models, s.turns, "
            f"MIN(e.ts) range_start, MAX(e.ts) range_end, "
            f"COUNT(DISTINCT CASE WHEN e.kind='file_edit' AND e.target IS NOT NULL THEN e.target END) files_changed, "
            f"SUM(CASE WHEN e.kind IN ('tool_call','file_edit') THEN 1 ELSE 0 END) tool_calls, "
            f"SUM(CASE WHEN e.kind='tool_result' THEN e.is_error ELSE 0 END) tool_failures, "
            f"SUM(e.duration_ms) execution_ms, "
            # The two halves of `execution_ms` above, at event level. Exact
            # rather than approximate: only these three kinds ever carry a
            # `duration_ms` (a `tool_call`/`file_edit` row's duration lives on
            # the correlated result), so the two cases below cover every row
            # the unqualified SUM sees. Bare `SUM(CASE … END)` with no
            # `ELSE 0` for the reason `_tool_breakdown` states — NULL is *this
            # kind of work was never timed here*, which a zero would claim was
            # measured.
            f"SUM(CASE WHEN e.kind IN ('generation','subagent') THEN e.duration_ms END) generation_ms, "
            f"SUM(CASE WHEN e.kind='tool_result' THEN e.duration_ms END) tool_ms, "
            f"SUM(e.fresh_input) fresh_input, "
            f"SUM(e.cache_read) cache_read, SUM(e.cache_creation) cache_creation, "
            f"SUM(e.reasoning) reasoning, SUM(e.output) output, SUM(e.provider_total) provider_total "
            f"FROM usage_events e JOIN sessions s ON s.session_id=e.session_id AND s.source_id=e.source_id "
            f"WHERE {where} GROUP BY s.session_id, s.source_id",
            params,
        )
        intervals = self._event_intervals(where, params)
        groups: list[dict[str, Any]] = []
        for row in rows:
            group = dict(row)
            key = (row["session_id"], row["source_id"])
            group["active_ms"] = intervals.get(key, ActiveIntervals()).union_ms()
            groups.append(group)
        return groups

    def _event_intervals(
        self, where: str, params: list[Any]
    ) -> dict[tuple[str, str], ActiveIntervals]:
        """Each session's active intervals inside the already-compiled window.

        A second read rather than a cleverer aggregate: no SQL aggregate merges
        overlaps without a second copy of the merge living in SQL, where it
        would drift from the projector's. An event's ``ts`` is the END of its
        interval (an assistant generation is stamped when it finished, a tool
        result when it returned), so the start is ``ts - duration_ms``.

        Measured on the reference cache (612k events, 182 MB): a week-wide
        window is 84k rows at ~130 ms, beside the ~107 ms the grouped aggregate
        above already costs. A per-request seam, never the ~1 Hz tick.

        Reads via :meth:`UsageStore.query_tuples` rather than :meth:`query`:
        this is the one loop in the package that walks every EVENT rather than
        every session (up to ~330k rows for a wide window), and named
        ``sqlite3.Row`` access re-resolves each column by string on every row
        for no benefit once the ``SELECT`` order is fixed. The reduction
        itself — sort, merge, sum — is unchanged; only how a row's four values
        reach Python is different, so ``active_ms``/``execution_ms`` cannot
        move.
        """
        rows = self.store.query_tuples(
            f"SELECT e.session_id, e.source_id, e.ts, e.duration_ms FROM usage_events e "
            f"JOIN sessions s ON s.session_id=e.session_id AND s.source_id=e.source_id "
            f"WHERE {where} AND e.ts IS NOT NULL AND e.duration_ms IS NOT NULL",
            params,
        )
        collected: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
        for session_id, source_id, ts, duration_ms in rows:
            finished = int(ts) * 1000
            collected[(session_id, source_id)].append((finished - int(duration_ms), finished))
        return {key: ActiveIntervals.of(spans) for key, spans in collected.items()}

    def _event_cost(self, filters: UsageFilters) -> MoneyView | None:
        """The whole filtered range's cost, all-or-nothing.

        Day-bounded costing lives in :meth:`_daily_event_cost`; the ``start``/
        ``end`` parameters this method used to carry existed only for the
        activity loop that called it once per day, so they left with it rather
        than staying as a seam with no producer.
        """
        groups = self._event_groups(filters)
        if not groups:
            return None
        where, params = self._event_where(filters)
        rows = self.store.query(
            f"SELECT e.session_id, e.source_id, e.model, SUM(e.fresh_input) fresh_input, "
            f"SUM(e.cache_read) cache_read, SUM(e.cache_creation) cache_creation, SUM(e.output) output "
            f"FROM usage_events e JOIN sessions s ON s.session_id=e.session_id AND s.source_id=e.source_id "
            f"WHERE {where} AND (e.fresh_input IS NOT NULL OR e.cache_read IS NOT NULL OR "
            f"e.cache_creation IS NOT NULL OR e.output IS NOT NULL) "
            f"GROUP BY e.session_id, e.source_id, e.model",
            params,
        )
        measured_sessions = {(row["session_id"], row["source_id"]) for row in rows}
        if len(measured_sessions) != len(groups):
            return None
        total = self.prices.total(
            (
                row["model"],
                TokenCounts(
                    fresh_input=row["fresh_input"],
                    cache_read=row["cache_read"],
                    cache_creation=row["cache_creation"],
                    output=row["output"],
                ),
            )
            for row in rows
        )
        return self.prices.money(total)

    def refresh_cost_cache(self) -> None:
        """Recompute sortable cache columns from the current startup price book."""
        rows = self.store.query(
            "SELECT session_id, source_id, models, fresh_input, cache_read, cache_creation, output FROM sessions"
        )
        updates: list[tuple[str | None, str, str, str, str]] = []
        for row in rows:
            models = _models(row["models"])
            amount = self.prices.amount(
                models[0] if len(models) == 1 else None,
                TokenCounts(
                    fresh_input=row["fresh_input"],
                    cache_read=row["cache_read"],
                    cache_creation=row["cache_creation"],
                    output=row["output"],
                ),
            )
            updates.append(
                (
                    format(amount, "f") if amount is not None else None,
                    self.prices.currency,
                    "estimated" if amount is not None else "unknown",
                    row["session_id"],
                    row["source_id"],
                )
            )
        if updates:
            with self.store.write() as conn:
                conn.executemany(
                    "UPDATE sessions SET cost_amount=?, cost_currency=?, cost_provenance=? WHERE session_id=? AND source_id=?",
                    updates,
                )

    def _session(self, row: Any, bounded: Any | None = None) -> UsageSessionRowView:
        models = _models(row["models"])
        token_row = bounded or row
        amount = (
            None
            if bounded is not None
            else self.prices.amount(
                models[0] if len(models) == 1 else None,
                TokenCounts(
                    fresh_input=row["fresh_input"],
                    cache_read=row["cache_read"],
                    cache_creation=row["cache_creation"],
                    output=row["output"],
                ),
            )
        )
        cost = self.prices.money(amount)
        active_ms = bounded["active_ms"] if bounded is not None else row["active_ms"]
        source = bounded if bounded is not None else row
        execution_ms = source["execution_ms"]
        range_start = bounded["range_start"] if bounded is not None else row["started_at"]
        range_end = bounded["range_end"] if bounded is not None else row["last_event_at"]
        return UsageSessionRowView(
            session_id=row["session_id"],
            provider=row["provider"],
            cwd=row["cwd"],
            project=row["project"],
            account_id=row["account_id"],
            account_label=row["account_label"],
            source_id=row["source_id"],
            models=models,
            started_at=_dt(range_start),
            last_event_at=_dt(range_end),
            duration=DurationView(
                active_ms=active_ms,
                execution_ms=execution_ms,
                # Both halves come from whichever source `execution_ms` did —
                # the whole-session row, or the range-bounded event
                # reconstruction — so the partition can never be assembled
                # from two different populations.
                generation_ms=source["generation_ms"],
                tool_ms=source["tool_ms"],
                elapsed_span_ms=(
                    max(0, (range_end - range_start) * 1000)
                    if bounded is not None and range_start is not None and range_end is not None
                    else row["elapsed_span_ms"]
                ),
                confidence=(
                    "derived"
                    if bounded is not None and (active_ms is not None or range_end is not None)
                    else row["duration_confidence"]
                ),
            ),
            turns=row["turns"],
            tool_calls=bounded["tool_calls"] if bounded is not None else row["tool_calls"],
            tool_failures=(
                bounded["tool_failures"] if bounded is not None else row["tool_failures"]
            ),
            files_changed=bounded["files_changed"] if bounded is not None else row["files_changed"],
            tokens=_tokens(token_row),
            # The subagent_* columns live only on the whole-session row, not
            # on a range-bounded event reconstruction (`bounded`), so a
            # date-filtered slice honestly reports unmeasured here rather
            # than guessing a range-scoped delegated share.
            subagent_tokens=None if bounded is not None else _subagent_tokens(row),
            cost=cost,
            parser_health=row["parser_health"],
            parser_detail=row["parser_detail"],
        )

    def _where(self, filters: UsageFilters, *, alias: str = "") -> tuple[str, list[Any]]:
        return self._session_where(filters, alias=alias)

    def _session_where(self, filters: UsageFilters, *, alias: str = "") -> tuple[str, list[Any]]:
        if not _has_temporal_filter(filters):
            return session_filter_sql(filters, alias=alias)
        timeless = filters.model_copy(update={"since": None, "until": None, "day": None})
        where, params = session_filter_sql(timeless, alias=alias)
        outer = alias or "sessions"
        event_filters = UsageFilters(
            since=filters.since,
            until=filters.until,
            tz=filters.tz,
            day=filters.day,
        )
        event_where, event_params = session_filter_sql(
            event_filters, alias="event_session", timestamp_column="range_event.ts"
        )
        where += (
            " AND EXISTS (SELECT 1 FROM usage_events range_event JOIN sessions event_session "
            "ON event_session.session_id=range_event.session_id AND "
            "event_session.source_id=range_event.source_id WHERE "
            f"range_event.session_id={outer}.session_id AND range_event.source_id={outer}.source_id "
            f"AND {event_where})"
        )
        params.extend(event_params)
        return where, params

    def _event_where(self, filters: UsageFilters) -> tuple[str, list[Any]]:
        return session_filter_sql(filters, alias="s", timestamp_column="e.ts")

    def _tool_breakdown(self, filters: UsageFilters) -> UsageBreakdownView:
        """Per-tool call counts, failures and measured time.

        ``active_ms`` sums only the ``tool_result`` rows, because that is where
        the projector writes a correlated duration — a ``tool_call`` row has
        none, so folding it in would divide a real total by a population that
        includes rows which could never contribute. Written as a bare
        ``SUM(CASE … END)`` with no ``ELSE 0``: SQLite ignores NULL inputs and
        returns NULL when every one of them is NULL, which is the difference
        between *this tool was never timed* and *this tool took no time*.

        This column is what makes a built-in-versus-MCP split answerable
        client-side off one prefix test, rather than needing its own route: an
        MCP tool is identifiable by its ``mcp__`` name prefix, so the
        classification is a property of ``key`` and belongs wherever the rows
        are rendered, not in a second aggregation here.
        """
        where, params = self._event_where(filters)
        rows = self.store.query(
            f"SELECT e.tool_name key, COUNT(DISTINCT e.session_id || ':' || e.source_id) sessions, SUM(CASE WHEN e.kind IN ('tool_call','file_edit') THEN 1 ELSE 0 END) tool_calls, SUM(e.is_error) tool_failures, SUM(CASE WHEN e.kind='tool_result' THEN e.duration_ms END) active_ms FROM usage_events e JOIN sessions s ON s.session_id=e.session_id AND s.source_id=e.source_id WHERE {where} AND e.tool_name IS NOT NULL GROUP BY e.tool_name ORDER BY tool_calls DESC LIMIT ?",
            [*params, self.cfg.usage.max_breakdown_rows + 1],
        )
        limit = self.cfg.usage.max_breakdown_rows
        return UsageBreakdownView(
            dimension="tool",
            rows=tuple(
                UsageBreakdownRowView(
                    key=row["key"],
                    label=row["key"],
                    sessions=row["sessions"] or 0,
                    tool_calls=row["tool_calls"] or 0,
                    tool_failures=row["tool_failures"] or 0,
                    active_ms=row["active_ms"],
                )
                for row in rows[:limit]
            ),
            truncated=len(rows) > limit,
            coverage=self.coverage(),
        )

    def _model_breakdown(self, filters: UsageFilters) -> UsageBreakdownView:
        where, params = self._where(filters, alias="s")
        rows = self.store.query(
            f"SELECT model.value key, COUNT(*) sessions, {_complete_breakdown_sums('s.')}, SUM(s.tool_calls) tool_calls, SUM(s.tool_failures) tool_failures, CASE WHEN COUNT(s.cost_amount)<COUNT(*) THEN NULL ELSE SUM(CAST(s.cost_amount AS REAL)) END cost_amount FROM sessions s JOIN json_each(s.models) model WHERE {where} AND json_array_length(s.models)=1 GROUP BY model.value ORDER BY sessions DESC LIMIT ?",
            [*params, self.cfg.usage.max_breakdown_rows + 1],
        )
        return self._breakdown_view("model", rows)

    def _model_generation_latency(self, filters: UsageFilters) -> dict[str, GenerationLatencyView]:
        """Average per-call model wait, by model, over the ROW'S OWN sessions.

        ``AND json_array_length(s.models)=1`` is the load-bearing clause, and it
        shipped missing. Every other figure on a model row comes from
        ``_model_breakdown``, which restricts to single-model sessions because
        attributing a session-level token SUM to one of several models it used
        would be a guess. This read did not, so a row said "24 sessions"
        beside an average taken over 41,648 generation calls — measured on the
        reference host, `claude-sonnet-5` published **8429 ms over 41,648
        calls** where its own 24 sessions support **6532 ms over 121**. A 29%
        error, and a call count 344x larger than the row it decorates.

        **A row must be internally coherent before it is large.** The tempting
        defence — that an event carries its own exact ``model`` so there is
        nothing to guess — is true and beside the point: the question is not
        whether the average is computable, it is *which population every column
        on this row describes*. Publishing one column over a different
        population than its neighbours is the "degraded answer indistinguishable
        from the confident one" failure this package names repeatedly, and
        ``GenerationLatencyView.calls``'s own docstring ("an average of one call
        and an average of a thousand are not the same claim") is exactly what
        invites the misreading.

        Widening ``_model_breakdown`` instead was rejected: it would change what
        the token columns mean, which is a far larger claim than a latency
        column is worth.

        ``kind IN ('generation','subagent')`` matches ``execution_ms``'s own
        population — root and sub-agent calls both count.
        """
        where, params = self._where(filters, alias="s")
        rows = self.store.query(
            f"SELECT e.model key, COUNT(*) calls, AVG(e.duration_ms) avg_ms FROM usage_events e JOIN sessions s ON s.session_id=e.session_id AND s.source_id=e.source_id WHERE {where} AND json_array_length(s.models)=1 AND e.kind IN ('generation','subagent') AND e.duration_ms IS NOT NULL AND e.model IS NOT NULL GROUP BY e.model",
            params,
        )
        return {
            str(row["key"]): GenerationLatencyView(
                avg_ms=round(row["avg_ms"]), calls=int(row["calls"])
            )
            for row in rows
        }

    def _token_breakdown(self, filters: UsageFilters) -> UsageBreakdownView:
        where, params = self._where(filters)
        names = (
            "fresh_input",
            "cache_read",
            "cache_creation",
            "reasoning",
            "output",
            "provider_total",
        )
        selects = [
            f"SELECT '{name}' key, COUNT({name}) sessions, SUM({name}) provider_total, NULL fresh_input, NULL cache_read, NULL cache_creation, NULL reasoning, NULL output, NULL active_ms, 0 tool_calls, 0 tool_failures FROM sessions WHERE {where}"
            for name in names
        ]
        rows = self.store.query(" UNION ALL ".join(selects), params * len(selects))
        return UsageBreakdownView(
            dimension="token_class",
            rows=tuple(
                UsageBreakdownRowView(
                    key=row["key"],
                    label=row["key"].replace("_", " "),
                    sessions=row["sessions"] or 0,
                    tokens=TokenClassesView(**{row["key"]: row["provider_total"]}),
                )
                for row in rows
                if row["sessions"]
            ),
            coverage=self.coverage(),
        )

    def _breakdown_view(self, dimension: UsageDimension, rows: list[Any]) -> UsageBreakdownView:
        limit = self.cfg.usage.max_breakdown_rows
        return UsageBreakdownView(
            dimension=dimension,
            rows=tuple(
                UsageBreakdownRowView(
                    key=str(row["key"] or "unknown"),
                    label=str(row["key"] or "unknown"),
                    sessions=row["sessions"] or 0,
                    tokens=_tokens(row),
                    active_ms=row["active_ms"],
                    tool_calls=row["tool_calls"] or 0,
                    tool_failures=row["tool_failures"] or 0,
                    cost=self.prices.money(
                        Decimal(str(row["cost_amount"])) if row["cost_amount"] is not None else None
                    ),
                )
                for row in rows[:limit]
            ),
            truncated=len(rows) > limit,
            coverage=self.coverage(),
        )

    def _temporal_breakdown(
        self, filters: UsageFilters, dimension: UsageDimension
    ) -> UsageBreakdownView:
        cohorts: dict[str, list[Any]] = {}
        for row in self._event_groups(filters):
            keys: tuple[str, ...]
            if dimension == "provider":
                keys = (str(row["provider"]),)
            elif dimension == "account":
                keys = (str(row["account_id"] or "unknown"),)
            elif dimension == "project":
                keys = (str(row["project"] or "unknown"),)
            else:
                models = _models(row["models"])
                keys = models if len(models) == 1 else ()
            for key in keys:
                cohorts.setdefault(key, []).append(row)
        ordered = sorted(cohorts.items(), key=lambda item: (-len(item[1]), item[0]))
        limit = self.cfg.usage.max_breakdown_rows
        rows: list[UsageBreakdownRowView] = []
        for key, members in ordered[:limit]:
            evidence = filters.model_copy(
                update={
                    "provider": key if dimension == "provider" else filters.provider,
                    "account": key
                    if dimension == "account" and key != "unknown"
                    else filters.account,
                    "project": key
                    if dimension == "project" and key != "unknown"
                    else filters.project,
                    "model": key if dimension == "model" else filters.model,
                }
            )
            rows.append(
                UsageBreakdownRowView(
                    key=key,
                    label=key,
                    sessions=len(members),
                    tokens=TokenClassesView(
                        fresh_input=_complete_group_sum(members, "fresh_input"),
                        cache_read=_complete_group_sum(members, "cache_read"),
                        cache_creation=_complete_group_sum(members, "cache_creation"),
                        reasoning=_complete_group_sum(members, "reasoning"),
                        output=_complete_group_sum(members, "output"),
                        provider_total=_complete_group_total(members),
                    ),
                    active_ms=_complete_group_sum(members, "active_ms"),
                    tool_calls=sum(row["tool_calls"] or 0 for row in members),
                    tool_failures=sum(row["tool_failures"] or 0 for row in members),
                    cost=self._event_cost(evidence),
                )
            )
        return UsageBreakdownView(
            dimension=dimension,
            rows=tuple(rows),
            truncated=len(ordered) > limit,
            coverage=self.coverage(),
        )

    def _temporal_token_breakdown(self, filters: UsageFilters) -> UsageBreakdownView:
        groups = self._event_groups(filters)
        rows: list[UsageBreakdownRowView] = []
        for name in (*_TOKEN_COLUMNS, "provider_total"):
            measured = [row[name] for row in groups if row[name] is not None]
            if measured:
                rows.append(
                    UsageBreakdownRowView(
                        key=name,
                        label=name.replace("_", " "),
                        sessions=len(measured),
                        tokens=TokenClassesView(**{name: sum(int(value) for value in measured)}),
                    )
                )
        return UsageBreakdownView(
            dimension="token_class",
            rows=tuple(rows),
            coverage=self.coverage(),
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)


def session_filter_sql(
    filters: UsageFilters,
    *,
    alias: str = "",
    timestamp_column: str | None = None,
) -> tuple[str, list[Any]]:
    """Compile the shared range/facet filter for session or joined-event SQL."""
    prefix = f"{alias}." if alias else ""
    clauses: list[str] = ["1=1"]
    params: list[Any] = []
    for field, value in (("provider", filters.provider), ("account_id", filters.account)):
        if value:
            clauses.append(f"{prefix}{field} = ?")
            params.append(value)
    if filters.project:
        clauses.append(f"({prefix}project = ? OR {prefix}cwd = ?)")
        params.extend((filters.project, filters.project))
    if filters.model:
        clauses.append(
            f"EXISTS (SELECT 1 FROM json_each({prefix}models) model_filter WHERE model_filter.value = ?)"
        )
        params.append(filters.model)
    since = int(filters.since.timestamp()) if filters.since else None
    until = int(filters.until.timestamp()) if filters.until else None
    if filters.day:
        try:
            selected_day = date.fromisoformat(filters.day)
        except ValueError:
            selected_day = None
        if selected_day is not None:
            zone = resolve_zone(filters.tz)
            since = int(datetime.combine(selected_day, time.min, tzinfo=zone).timestamp())
            until = int(
                datetime.combine(
                    selected_day + timedelta(days=1), time.min, tzinfo=zone
                ).timestamp()
            )
    if since is not None:
        clauses.append(f"{prefix}last_event_at >= ?")
        params.append(since)
    if until is not None:
        clauses.append(f"{prefix}last_event_at < ?")
        params.append(until)
    timestamp = timestamp_column or f"{prefix}last_event_at"
    clauses = [clause.replace(f"{prefix}last_event_at", timestamp) for clause in clauses]
    return " AND ".join(clauses), params


def _models(raw: str | None) -> tuple[str, ...]:
    try:
        values = json.loads(raw or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    return (
        tuple(value for value in values if isinstance(value, str))
        if isinstance(values, list)
        else ()
    )


def _encode_cursor(
    sort: UsageSessionSort,
    key: float | int | None,
    last: int | None,
    sid: str,
    source_id: str,
) -> str:
    return base64.urlsafe_b64encode(json.dumps([sort, key, last, sid, source_id]).encode()).decode()


def _decode_cursor(
    value: str | None, sort: UsageSessionSort
) -> tuple[float | int | None, int | None, str, str] | None:
    if not value:
        return None
    try:
        raw = json.loads(base64.urlsafe_b64decode(value.encode()))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if (
        isinstance(raw, list)
        and len(raw) == 5
        and raw[0] == sort
        and (isinstance(raw[1], (int, float)) or raw[1] is None)
        and (isinstance(raw[2], int) or raw[2] is None)
        and isinstance(raw[3], str)
        and isinstance(raw[4], str)
    ):
        return raw[1], raw[2], raw[3], raw[4]
    return None
