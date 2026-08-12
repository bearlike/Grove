"""One metric over days, split by one dimension — the breakdown that kept time.

``UsageQuery.activity`` is one metric per day with no split; ``UsageQuery.
breakdown`` is one total per group with no days. This is the cell between them,
and it exists because "which model is eating the week" is a question neither can
answer.

**Its own module rather than a sixth method on ``UsageQuery``** because it asks
its question differently rather than merely asking a different question: every
other aggregate folds time away, so its SQL groups by a facet alone, while this
one JOINs a generated calendar and keeps both axes. The shared parts are reached
through the seams that already exist — ``session_filter_sql`` for the facet
filter, ``day_boundaries`` for the calendar, ``PriceBook`` for money, and an
injected ``coverage`` callable — so nothing here is a second definition of a rule
somebody else owns.

Three rules are load-bearing, and each is a way this chart lies confidently if
skipped:

* **A day on which a group reported nothing is ``None``, never ``0``.** A bar of
  height zero says *measured, and it was nothing*; the honest answer for a day a
  group did not appear on is an absent bar. This is the same rule the token
  classes already encode, applied to a second axis.
* **The ``days`` spine is published rather than derived.** A renderer that builds
  its own axis from the points it received draws a chart whose gaps close up, so
  a quiet Sunday silently becomes a narrower week.
* **A capped tail says so.** ``truncated`` is what stops "we only run three
  models" being a conclusion drawn from a row limit.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Final

from grove.core.contracts.usage import (
    UsageCoverageView,
    UsageDimension,
    UsageFilters,
    UsageMetric,
    UsageSeriesGroupView,
    UsageSeriesPointView,
    UsageSeriesView,
)
from grove.core.usage._intervals import ActiveIntervals
from grove.core.usage._pricing import PriceBook, TokenCounts
from grove.core.usage._store import DayBucket, UsageStore, day_boundaries, resolve_zone
from grove.core.usage.query import DAY_EVENT_JOIN, day_calendar_sql, session_filter_sql

_EVENT_JOIN: Final = DAY_EVENT_JOIN
"""The one join every read here makes: events, their session, and the day they
land in. ``UsageQuery.activity`` needs the identical join for its own calendar
grouping, so the string lives there and this is the local name for it — two
copies is how the two routes come to bucket the same events differently."""

_KEY_EXPRESSION: Final[dict[str, str]] = {
    "provider": "s.provider",
    "account": "s.account_id",
    "project": "s.project",
    # The EVENT's model, not the session's. `UsageQuery` reads a session's
    # `models` array and excludes switched-model sessions from the model
    # breakdown, because a whole-session total cannot be split after the fact.
    # A series is built from the events themselves, so each generation already
    # carries the model that produced it and no exclusion is needed.
    "model": "e.model",
    "tool": "e.tool_name",
}
"""SQL for the grouping key, per dimension. ``token_class`` is deliberately
absent: its groups are COLUMNS rather than a value, so it folds separately."""

_TOKEN_CLASSES: Final = (
    "fresh_input",
    "cache_read",
    "cache_creation",
    "reasoning",
    "output",
    "provider_total",
)

_UNKNOWN: Final = "unknown"
"""The key a row with no grouping value carries — stated, never dropped. A
session with no account is still spend."""


class UsageSeriesQuery:
    """A metric over days, split by one dimension, from the derived cache.

    Constructed with the store it reads, the price book money comes from, the
    config that caps its tail, and the ``coverage`` seam every aggregate answer
    carries — injected rather than re-derived so a series and a summary can never
    disagree about what the answer is based on.
    """

    def __init__(
        self,
        *,
        store: UsageStore,
        prices: PriceBook,
        cfg: Any,
        coverage: Callable[[], UsageCoverageView],
    ) -> None:
        self._store = store
        self._prices = prices
        self._cfg = cfg
        self._coverage = coverage

    def series(
        self, filters: UsageFilters, *, dimension: UsageDimension, metric: UsageMetric
    ) -> UsageSeriesView:
        """``metric`` per day of ``filters``' range, split by ``dimension``."""
        buckets = self._spine(filters)
        if not buckets:
            return UsageSeriesView(
                metric=metric, dimension=dimension, tz=filters.tz, coverage=self._coverage()
            )
        if dimension == "token_class":
            groups, truncated = self._token_class_groups(filters, buckets)
        else:
            groups, truncated = self._grouped(filters, buckets, dimension=dimension, metric=metric)
        return UsageSeriesView(
            metric=metric,
            dimension=dimension,
            tz=filters.tz,
            days=tuple(bucket.day for bucket in buckets),
            groups=groups,
            truncated=truncated,
            coverage=self._coverage(),
        )

    # ── the calendar ────────────────────────────────────────────────────────

    def _spine(self, filters: UsageFilters) -> tuple[DayBucket, ...]:
        """The contiguous day axis the whole answer is drawn on.

        Derived exactly the way ``UsageQuery.activity`` derives its buckets, so
        a client asking both routes for one range gets one axis: an explicit
        ``since``/``until`` wins, a ``day`` narrows to that single calendar day,
        and an absent bound falls back to the store's own extent rather than to
        a window size invented here.
        """
        selected = _parse_day(filters.day)
        if selected is not None:
            zone = resolve_zone(filters.tz)
            start = datetime.combine(selected, time.min, tzinfo=zone)
            return day_boundaries(start, start, filters.tz)
        earliest = _dt(self._store.scalar("SELECT MIN(ts) FROM usage_events WHERE ts IS NOT NULL"))
        latest = _dt(self._store.scalar("SELECT MAX(ts) FROM usage_events WHERE ts IS NOT NULL"))
        now = datetime.now(UTC)
        since = filters.since or earliest or now
        until = filters.until or (latest + timedelta(seconds=1) if latest is not None else now)
        # The range is half-open, so the last instant belongs to the previous
        # day — without the step back, a midnight `until` grows an empty day.
        last = until - timedelta(microseconds=1) if until > since else until
        return day_boundaries(since, last, filters.tz)

    # ── the grouped dimensions ──────────────────────────────────────────────

    def _grouped(
        self,
        filters: UsageFilters,
        buckets: tuple[DayBucket, ...],
        *,
        dimension: UsageDimension,
        metric: UsageMetric,
    ) -> tuple[tuple[UsageSeriesGroupView, ...], bool]:
        cells: dict[str, dict[str, list[Any]]] = {}
        for row in self._session_days(filters, buckets, dimension=dimension):
            key = str(row["group_key"] or _UNKNOWN)
            cells.setdefault(key, {}).setdefault(str(row["day"]), []).append(row)
        costs = self._costs(filters, buckets, dimension=dimension) if metric == "cost" else {}
        spans = (
            self._intervals(filters, buckets, dimension=dimension)
            if metric == "active_minutes"
            else {}
        )
        labels = self._labels(dimension, cells)
        candidates: list[UsageSeriesGroupView] = []
        for key, days in cells.items():
            points = [
                self._value(
                    metric,
                    days.get(bucket.day, []),
                    cost=costs.get((key, bucket.day)),
                    intervals=spans.get((key, bucket.day), {}),
                )
                for bucket in buckets
            ]
            candidates.append(
                UsageSeriesGroupView(
                    key=key,
                    label=labels.get(key, key),
                    total=_sum_measured(points),
                    points=tuple(
                        UsageSeriesPointView(day=bucket.day, value=value)
                        for bucket, value in zip(buckets, points, strict=True)
                    ),
                )
            )
        return _capped(candidates, self._cfg.usage.max_breakdown_rows)

    def _value(
        self,
        metric: UsageMetric,
        rows: list[Any],
        *,
        cost: float | None,
        intervals: Mapping[tuple[str, str], ActiveIntervals],
    ) -> float | None:
        """One cell: the metric over the sessions that reported on this day.

        ``None`` for a day with no rows at all, and — for the measured metrics —
        ``None`` again when any contributing session is missing the measurement,
        which is the complete-or-null rule every other aggregate here obeys. One
        measured session plus one unmeasured one is not a total.
        """
        if not rows:
            return None
        if metric == "cost":
            return cost
        if metric == "sessions":
            return float(len(rows))
        if metric == "tool_calls":
            return float(sum(int(row["tool_calls"] or 0) for row in rows))
        if metric == "active_minutes":
            return _union_minutes(rows, intervals)
        return _token_total(rows)

    def _labels(self, dimension: UsageDimension, cells: dict[str, Any]) -> dict[str, str]:
        """Display names for the keys, where the key is not one.

        Only ``account`` needs it: an ``account_id`` is an opaque local hash by
        construction, so a client filtering by the key would otherwise have to
        show the hash. Every other dimension's key IS its label.
        """
        if dimension != "account" or not cells:
            return {}
        placeholders = ",".join("?" for _ in cells)
        rows = self._store.query(
            f"SELECT account_id, label FROM accounts WHERE account_id IN ({placeholders})",
            list(cells),
        )
        return {str(row["account_id"]): str(row["label"]) for row in rows}

    # ── token classes: the dimension whose groups are columns ───────────────

    def _token_class_groups(
        self, filters: UsageFilters, buckets: tuple[DayBucket, ...]
    ) -> tuple[tuple[UsageSeriesGroupView, ...], bool]:
        """One series per token class, whatever ``metric`` was asked for.

        A token class has no active minutes, no sessions of its own and no
        separable cost — the class IS the measurement — so this dimension
        answers in tokens and the metric on the response says so. Mirrors
        ``UsageQuery``'s own token-class breakdown, including its rule that a
        class sums the sessions that MEASURED it: a Claude session reporting no
        ``reasoning`` means *not reported*, and must not blank the class for the
        Codex sessions that did report it.
        """
        rows = self._session_days(filters, buckets, dimension=None)
        per_day: dict[str, list[Any]] = {}
        for row in rows:
            per_day.setdefault(str(row["day"]), []).append(row)
        groups: list[UsageSeriesGroupView] = []
        for name in _TOKEN_CLASSES:
            points = [_class_total(per_day.get(bucket.day, []), name) for bucket in buckets]
            if all(value is None for value in points):
                continue
            groups.append(
                UsageSeriesGroupView(
                    key=name,
                    label=name.replace("_", " "),
                    total=_sum_measured(points),
                    points=tuple(
                        UsageSeriesPointView(day=bucket.day, value=value)
                        for bucket, value in zip(buckets, points, strict=True)
                    ),
                )
            )
        return _capped(groups, self._cfg.usage.max_breakdown_rows)

    # ── the three reads ─────────────────────────────────────────────────────

    def _scope(
        self,
        filters: UsageFilters,
        buckets: tuple[DayBucket, ...],
        *,
        dimension: UsageDimension | None,
    ) -> tuple[str, str, list[Any]]:
        """The calendar CTE, the compiled facet filter, and their parameters.

        Every read below is the same window seen at a different grain, so the
        window is compiled once: three copies of a filter is three chances for
        one of them to answer about a slightly different set of events.

        The calendar arrives as a ``VALUES`` CTE rather than as arithmetic on
        ``ts``, because a day is a local-midnight span in the reader's zone —
        23 or 25 hours across a DST transition — and no expression over epoch
        seconds can say that.
        """
        where, params = session_filter_sql(filters, alias="s", timestamp_column="e.ts")
        if dimension == "tool":
            # A tool series is about named tools; an event that named none is
            # not an anonymous tool, it is not a tool call.
            where += " AND e.tool_name IS NOT NULL"
        calendar, calendar_params = day_calendar_sql(buckets)
        return calendar, where, [*calendar_params, *params]

    def _session_days(
        self,
        filters: UsageFilters,
        buckets: tuple[DayBucket, ...],
        *,
        dimension: UsageDimension | None,
    ) -> list[Any]:
        """One row per (day, group, session): the grain every fold above needs.

        The grain is deliberately the SESSION rather than the group, because
        completeness is a per-session property — ``SUM`` silently ignores NULL,
        so a day holding one measured session and one unmeasured one would
        otherwise report a confident partial total.

        **No duration column, deliberately.** ``SUM(duration_ms)`` is the labour
        total (``execution_ms``), not the wall clock: a session's intervals
        include every sub-agent it spawned and those run concurrently, so the
        sum exceeds the lifespan that contained it. The union is folded in from
        :meth:`_intervals` instead, which is what keeps this route's
        ``active_minutes`` equal to ``/usage/summary``'s for the same range.
        """
        key_expression = _KEY_EXPRESSION[dimension] if dimension is not None else "NULL"
        calendar, where, params = self._scope(filters, buckets, dimension=dimension)
        return self._store.query(
            f"{calendar} SELECT bucket.day day, {key_expression} group_key, "
            f"e.session_id session_id, e.source_id source_id, "
            f"SUM(CASE WHEN e.kind IN ('tool_call','file_edit') THEN 1 ELSE 0 END) tool_calls, "
            f"SUM(e.fresh_input) fresh_input, "
            f"SUM(e.cache_read) cache_read, SUM(e.cache_creation) cache_creation, "
            f"SUM(e.reasoning) reasoning, SUM(e.output) output, "
            f"SUM(e.provider_total) provider_total "
            f"{_EVENT_JOIN} "
            f"WHERE {where} GROUP BY bucket.day, group_key, e.session_id, e.source_id",
            params,
        )

    def _intervals(
        self,
        filters: UsageFilters,
        buckets: tuple[DayBucket, ...],
        *,
        dimension: UsageDimension,
    ) -> dict[tuple[str, str], dict[tuple[str, str], ActiveIntervals]]:
        """Each (group, day)'s active intervals, per session inside it.

        A second read rather than a cleverer aggregate, for the reason
        ``UsageQuery._event_intervals`` gives: no SQL aggregate merges overlaps
        without a second copy of the merge living in SQL, where it would drift
        from the one in ``_intervals.py``. An event's ``ts`` is the END of its
        interval (a generation is stamped when it finished, a tool result when
        it returned), so the start is ``ts - duration_ms``.

        Kept per SESSION rather than merged per cell so the fold can apply the
        same reducer the summary applies — union within a session, sum across
        them — instead of inventing a third answer for one number.

        Reads via :meth:`UsageStore.query_tuples`, same reasoning as
        ``UsageQuery._event_intervals``: this walks every EVENT in the range,
        not every session, and named ``Row`` access re-resolves each column by
        string on every row for no benefit once the ``SELECT`` order is fixed.
        """
        key_expression = _KEY_EXPRESSION[dimension]
        calendar, where, params = self._scope(filters, buckets, dimension=dimension)
        rows = self._store.query_tuples(
            f"{calendar} SELECT bucket.day day, {key_expression} group_key, "
            f"e.session_id session_id, e.source_id source_id, e.ts ts, e.duration_ms duration_ms "
            f"{_EVENT_JOIN} "
            f"WHERE {where} AND e.ts IS NOT NULL AND e.duration_ms IS NOT NULL",
            params,
        )
        collected: dict[tuple[str, str], dict[tuple[str, str], list[tuple[int, int]]]] = {}
        for day, group_key, session_id, source_id, ts, duration_ms in rows:
            cell = (str(group_key or _UNKNOWN), str(day))
            session = (str(session_id), str(source_id))
            finished = int(ts) * 1000
            collected.setdefault(cell, {}).setdefault(session, []).append(
                (finished - int(duration_ms), finished)
            )
        return {
            cell: {session: ActiveIntervals.of(spans) for session, spans in sessions.items()}
            for cell, sessions in collected.items()
        }

    def _costs(
        self,
        filters: UsageFilters,
        buckets: tuple[DayBucket, ...],
        *,
        dimension: UsageDimension,
    ) -> dict[tuple[str, str], float | None]:
        """Money per (group, day), priced per MODEL and only then added.

        A separate read from :meth:`_session_days` because cost needs a finer
        grain — a range spanning two models has no single price — and folding
        that grain into the main query would give every tool-call event, which
        carries no model and no tokens, a row of its own and turn every day
        null.
        """
        key_expression = _KEY_EXPRESSION[dimension]
        calendar, where, params = self._scope(filters, buckets, dimension=dimension)
        rows = self._store.query(
            f"{calendar} SELECT bucket.day day, {key_expression} group_key, e.model model, "
            f"SUM(e.fresh_input) fresh_input, SUM(e.cache_read) cache_read, "
            f"SUM(e.cache_creation) cache_creation, SUM(e.output) output "
            f"{_EVENT_JOIN} "
            f"WHERE {where} AND (e.fresh_input IS NOT NULL OR e.cache_read IS NOT NULL "
            f"OR e.cache_creation IS NOT NULL OR e.output IS NOT NULL) "
            f"GROUP BY bucket.day, group_key, e.model",
            params,
        )
        priced: dict[tuple[str, str], list[tuple[str | None, TokenCounts]]] = {}
        for row in rows:
            cell = (str(row["group_key"] or _UNKNOWN), str(row["day"]))
            priced.setdefault(cell, []).append(
                (
                    row["model"],
                    TokenCounts(
                        fresh_input=row["fresh_input"],
                        cache_read=row["cache_read"],
                        cache_creation=row["cache_creation"],
                        output=row["output"],
                    ),
                )
            )
        return {
            cell: (float(total) if (total := self._prices.total(groups)) is not None else None)
            for cell, groups in priced.items()
        }


# ── module helpers ──────────────────────────────────────────────────────────


def _capped(
    groups: list[UsageSeriesGroupView], limit: int
) -> tuple[tuple[UsageSeriesGroupView, ...], bool]:
    """The largest ``limit`` groups, and whether a tail was dropped.

    An unmeasured group sorts last rather than as zero: it has nothing to say,
    and it must never displace a group that does.
    """

    def rank(group: UsageSeriesGroupView) -> tuple[bool, float, str]:
        return group.total is None, -(group.total or 0.0), group.key

    ordered = sorted(groups, key=rank)
    return tuple(ordered[:limit]), len(ordered) > limit


def _sum_measured(points: list[float | None]) -> float | None:
    """The window total: what was measured, or ``None`` if nothing was.

    Deliberately not "null if any day is null" — a day a group did not appear on
    is an absence, not a hole in a measurement, so a group that ran on two days
    of seven has an honest two-day total.
    """
    measured = [point for point in points if point is not None]
    return float(sum(measured)) if measured else None


def _union_minutes(
    rows: list[Any], intervals: Mapping[tuple[str, str], ActiveIntervals]
) -> float | None:
    """Wall-clock active minutes for one cell — UNION within a session, then sum.

    The two reducers over one interval set answer different questions, and only
    the union can be compared against a clock: a session's intervals span every
    sub-agent it spawned, and those run concurrently, so ``SUM(duration_ms)``
    reports more active time than the session's own lifespan. Summing the
    per-session unions afterwards is exactly what ``/usage/summary`` does for
    the same range, which is what stops the two routes disagreeing.

    ``None`` when any contributing session carried no duration-bearing event at
    all: a session Grove could not time is unmeasured, never instantaneous.
    """
    unions = [
        intervals.get((str(row["session_id"]), str(row["source_id"])), ActiveIntervals()).union_ms()
        for row in rows
    ]
    if any(union is None for union in unions):
        return None
    return sum(union for union in unions if union is not None) / 60_000


def _token_total(rows: list[Any]) -> float | None:
    """One cell's tokens, or ``None`` if any session in it is unmeasured."""
    totals = [_measured_total(row) for row in rows]
    if any(total is None for total in totals):
        return None
    return float(sum(total for total in totals if total is not None))


def _measured_total(row: Any) -> int | None:
    """One session-day's token total, or ``None`` when it cannot be totalled.

    Mirrors the rule ``UsageQuery`` applies to its own event groups: the
    provider's own total wins where it stated one, otherwise the classes are
    summed and a missing class means the row is unmeasured rather than smaller.
    ``reasoning`` is excluded from the requirement because providers that fold
    it into ``output`` never report it, and demanding it would blank every
    Claude session.
    """
    if row["provider_total"] is not None:
        return int(row["provider_total"])
    required = ("fresh_input", "cache_read", "cache_creation", "output")
    if any(row[column] is None for column in required):
        return None
    return sum(int(row[column]) for column in required) + int(row["reasoning"] or 0)


def _class_total(rows: list[Any], name: str) -> float | None:
    """One token class on one day, summed over the sessions that measured it."""
    measured = [row[name] for row in rows if row[name] is not None]
    return float(sum(int(value) for value in measured)) if measured else None


def _parse_day(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _dt(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value, UTC) if value is not None else None
