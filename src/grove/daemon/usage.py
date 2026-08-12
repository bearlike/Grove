"""Bounded read routes over the usage-audit engine (`grove.core.usage`).

The sibling of `activity.py`'s `/activity` + `/events` routes, pointed at the
past instead of the present: those answer *what is happening right now* over
an unbounded-lifetime SSE stream; these answer *what happened*, over
request/response reads that are always paginated or aggregated. Nothing here
rides `/events` — see `daemon/CLAUDE.md` on why a historical query never joins
the live activity bus.

`UsageServiceProtocol` is a structural type, not an import of the concrete
`grove.core.usage.UsageService` — that engine module is a concurrently
developed package (see the `#445`/`#446` tracker issues), so this router is
typed against the SURFACE the daemon calls rather than the class that
implements it. Production resolves the real service lazily in
`_default_usage_service`; tests inject a fake directly into
`build_usage_router`. This is the same injectable-dependency shape
`build_app` already uses for `notification_broker` / `release_checker` /
`status_publisher`, just scoped to one router instead of the whole app.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Protocol

from fastapi import APIRouter, Body, Depends, Query
from loguru import logger
from pydantic import BaseModel

from grove.core import usage as usage_engine
from grove.core.contracts.usage import (
    UsageActivityView,
    UsageBashInsightView,
    UsageBreakdownView,
    UsageDimension,
    UsageFilters,
    UsageFindingsView,
    UsageMetric,
    UsageQuotasView,
    UsageRefreshView,
    UsageSeriesView,
    UsageSessionPageView,
    UsageSessionSort,
    UsageSummaryView,
)
from grove.core.errors import CapabilityUnavailable

if TYPE_CHECKING:
    from grove.core.config import GroveConfig
    from grove.core.registry import RepoRegistry


class UsageServiceProtocol(Protocol):
    """The engine seam this router calls.

    Mirrors `grove.core.usage.UsageService`'s public surface. Every method is
    a blocking, synchronous call (SQLite + quota-collector I/O) — every route
    below offloads it via `asyncio.to_thread`, the same helper every other
    blocking manager call in this daemon uses.
    """

    def summary(self, filters: UsageFilters) -> UsageSummaryView: ...

    def activity(self, filters: UsageFilters, *, metric: UsageMetric) -> UsageActivityView: ...

    def sessions(
        self,
        filters: UsageFilters,
        *,
        cursor: str | None,
        limit: int,
        sort: UsageSessionSort,
    ) -> UsageSessionPageView: ...

    def breakdown(
        self, filters: UsageFilters, *, dimension: UsageDimension
    ) -> UsageBreakdownView: ...

    def series(
        self, filters: UsageFilters, *, dimension: UsageDimension, metric: UsageMetric
    ) -> UsageSeriesView: ...

    def quotas(self) -> UsageQuotasView: ...

    def refresh(self, *, force: bool) -> UsageRefreshView: ...

    def findings(self, filters: UsageFilters) -> UsageFindingsView: ...

    def bash_commands(self, filters: UsageFilters) -> UsageBashInsightView: ...

    def close(self) -> None: ...


_SessionLimit = Annotated[int, Query(ge=1)]

# Findings are RANKED, so a client renders a head and the tail is noise by
# construction — which is why this is a `limit` rather than the cursor
# `/usage/sessions` carries. A cursor there pages an audit TABLE a human
# genuinely scrolls; here every page would re-run all six detectors over the
# whole filtered range for rows nobody reads, making the server busier to send
# less. The withheld count rides the payload as `UsageFindingsView.total`, so
# nothing is silently dropped, and each finding's `evidence_filters` reproduces
# it on the cursor-paginated sessions route.
_FINDINGS_PAGE_MAX = 500
_FINDINGS_PAGE_DEFAULT = 100
_FindingLimit = Annotated[int, Query(ge=1, le=_FINDINGS_PAGE_MAX)]


class _UnavailableUsageService:
    """Stand-in used when the real engine can't be constructed.

    Every method raises `CapabilityUnavailable`, which the daemon's app-level
    `GroveError` handler (registered once in `app.py`, independent of which
    router raises) already turns into 501 with the standard envelope — the
    same shape every other capability-gap route in this daemon uses, so a
    client sees one failure vocabulary rather than a usage-specific one.
    """

    def __init__(self, detail: str) -> None:
        self._detail = detail

    def _unavailable(self) -> CapabilityUnavailable:
        return CapabilityUnavailable(f"usage engine unavailable: {self._detail}")

    def summary(self, filters: UsageFilters) -> UsageSummaryView:
        del filters
        raise self._unavailable()

    def activity(self, filters: UsageFilters, *, metric: UsageMetric) -> UsageActivityView:
        del filters, metric
        raise self._unavailable()

    def sessions(
        self,
        filters: UsageFilters,
        *,
        cursor: str | None,
        limit: int,
        sort: UsageSessionSort,
    ) -> UsageSessionPageView:
        del filters, cursor, limit, sort
        raise self._unavailable()

    def breakdown(self, filters: UsageFilters, *, dimension: UsageDimension) -> UsageBreakdownView:
        del filters, dimension
        raise self._unavailable()

    def series(
        self, filters: UsageFilters, *, dimension: UsageDimension, metric: UsageMetric
    ) -> UsageSeriesView:
        del filters, dimension, metric
        raise self._unavailable()

    def quotas(self) -> UsageQuotasView:
        raise self._unavailable()

    def refresh(self, *, force: bool) -> UsageRefreshView:
        del force
        raise self._unavailable()

    def findings(self, filters: UsageFilters) -> UsageFindingsView:
        del filters
        raise self._unavailable()

    def bash_commands(self, filters: UsageFilters) -> UsageBashInsightView:
        del filters
        raise self._unavailable()

    def close(self) -> None:
        """The unavailable stand-in owns no resources."""


def _default_usage_service(*, cfg: GroveConfig, registry: RepoRegistry) -> UsageServiceProtocol:
    """Construct the real engine service, degrading to the unavailable stub on failure.

    `grove.core.usage` lands from a separate, concurrently-developed story
    (`#445` indexing, `#446` quota collectors); a daemon built against an
    in-progress engine package must still start and serve every OTHER route.
    An import failure or a constructor signature mismatch here degrades ONLY
    the usage routes (501 on each) rather than the whole process — the same
    "one malformed source degrades itself alone, the rest still answer"
    contract the wire types already encode for a single broken transcript.
    Once `core.usage` is complete this simply succeeds and the stub is never
    built.
    """
    try:
        return usage_engine.UsageService(cfg=cfg, registry=registry)
    except Exception as exc:
        logger.warning("usage engine unavailable, /usage/* will 501: {}", exc)
        return _UnavailableUsageService(str(exc))


class _RefreshBody(BaseModel):
    """`POST /usage/refresh` body — `force` bypasses the mtime/size skip.

    Defaults to an empty instance so a client may POST with no body at all
    (the common case: "refresh with whatever policy is sane").
    """

    force: bool = False


class _UsageRefreshCoalescer:
    """Single-flight guard around `UsageService.refresh`, specialized to it.

    `_poll_coalescer.py::_PollCoalescer` wraps a `Callable[[], None]` shared by
    the activity poll's two fire-and-forget triggers — neither caller wants
    the result back, so a lost return value costs nothing. A refresh caller
    DOES want the result (the view IS the response), and a joining caller must
    be told it joined (`coalesced=True`) rather than silently wearing the
    initiator's answer, which `_PollCoalescer`'s bare `None` return has no way
    to express. Reusing it here would mean bolting a generic return channel
    and a per-caller-role flag onto a primitive the activity poll already
    depends on — reimplementing the same single-flight shape (no `await`
    between the check and the set, `asyncio.shield` so a disconnected caller
    never cancels a run others are joining) is cheaper and safer than widening
    a shared file two other routes rely on.
    """

    def __init__(self, refresh: Callable[..., UsageRefreshView]) -> None:
        self._refresh = refresh
        self._inflight: asyncio.Future[UsageRefreshView] | None = None

    def _clear_completed(self, completed: asyncio.Future[UsageRefreshView]) -> None:
        """Release the shared future only after the executor job is actually done."""
        if self._inflight is completed:
            self._inflight = None

    async def run(self, *, force: bool) -> UsageRefreshView:
        inflight = self._inflight
        if inflight is not None:
            result = await asyncio.shield(inflight)
            return result.model_copy(update={"coalesced": True})
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(None, lambda: self._refresh(force=force))
        self._inflight = fut
        fut.add_done_callback(self._clear_completed)
        return await asyncio.shield(fut)


def build_usage_router(
    *,
    cfg: GroveConfig,
    registry: RepoRegistry,
    usage_service: UsageServiceProtocol | None = None,
) -> APIRouter:
    """Build the `/usage/*` router.

    Mounted by `app.py` with `dependencies=auth_dep` at inclusion time, like
    every other authenticated route group — this router carries no auth logic
    of its own, which is what keeps the app.py integration to one import plus
    one `include_router` call. `usage_service` is the test-injection seam;
    production always passes `None` and gets `_default_usage_service`.
    """
    if usage_service is None:
        usage_service = _default_usage_service(cfg=cfg, registry=registry)

    router = APIRouter(prefix="/usage", tags=["usage"])
    refresh_coalescer = _UsageRefreshCoalescer(usage_service.refresh)
    # `max_sessions_per_page` is startup policy, so it stays out of the module-
    # scoped Pydantic annotation and clamps in the handler. Putting a closure
    # value in `Query(le=...)` makes OpenAPI generation fail on an unresolved
    # forward reference.
    max_page = cfg.usage.max_sessions_per_page
    default_page = min(50, max_page)

    @router.get("/summary", response_model=UsageSummaryView)
    async def usage_summary(filters: Annotated[UsageFilters, Depends()]) -> UsageSummaryView:
        """Totals, comparisons and data freshness for the filtered range."""
        return await asyncio.to_thread(usage_service.summary, filters)

    @router.get("/activity", response_model=UsageActivityView)
    async def usage_activity(
        filters: Annotated[UsageFilters, Depends()],
        metric: Annotated[UsageMetric, Query()] = "tokens",
    ) -> UsageActivityView:
        """The daily heatmap/time-series buckets for one metric."""
        return await asyncio.to_thread(usage_service.activity, filters, metric=metric)

    @router.get("/sessions", response_model=UsageSessionPageView)
    async def usage_sessions(
        filters: Annotated[UsageFilters, Depends()],
        cursor: Annotated[str | None, Query()] = None,
        limit: _SessionLimit = default_page,
        sort: Annotated[UsageSessionSort, Query()] = "recent",
    ) -> UsageSessionPageView:
        """One cursor-paginated page of the session audit table."""
        bounded_limit = min(limit, max_page)
        return await asyncio.to_thread(
            usage_service.sessions, filters, cursor=cursor, limit=bounded_limit, sort=sort
        )

    @router.get("/breakdowns", response_model=UsageBreakdownView)
    async def usage_breakdowns(
        filters: Annotated[UsageFilters, Depends()],
        dimension: Annotated[UsageDimension, Query()],
    ) -> UsageBreakdownView:
        """Composition along one dimension (provider/account/project/model/tool/token_class)."""
        return await asyncio.to_thread(usage_service.breakdown, filters, dimension=dimension)

    @router.get("/series", response_model=UsageSeriesView)
    async def usage_series(
        filters: Annotated[UsageFilters, Depends()],
        dimension: Annotated[UsageDimension, Query()],
        metric: Annotated[UsageMetric, Query()] = "tokens",
    ) -> UsageSeriesView:
        """One metric per day, split by one dimension — a breakdown that kept time.

        Deliberately the same `UsageDimension` vocabulary `/breakdowns` serves
        rather than a per-card enum: "which plan/model/tool is eating the week"
        is the same question along three axes, and a route per axis is how six
        cards come to disagree about what a project is.

        The response publishes its own `days` spine, so a client zips its points
        against it rather than joining on the date — a day nothing reported is a
        `null` point, never a zero.
        """
        return await asyncio.to_thread(
            usage_service.series, filters, dimension=dimension, metric=metric
        )

    @router.get("/quotas", response_model=UsageQuotasView)
    async def usage_quotas() -> UsageQuotasView:
        """Latest account/window snapshots — host-wide, never filtered by range."""
        return await asyncio.to_thread(usage_service.quotas)

    @router.get("/findings", response_model=UsageFindingsView)
    async def usage_findings(
        filters: Annotated[UsageFilters, Depends()],
        limit: _FindingLimit = _FINDINGS_PAGE_DEFAULT,
    ) -> UsageFindingsView:
        """Deterministic, metadata-only findings over filtered evidence.

        Ranked highest-first and capped at ``limit`` (default 100, max 500).
        ``total`` reports how many the detectors produced, so a truncated page
        is visible as one — measured on a real host, the uncapped response was
        2475 findings and 1.4 MB.
        """
        view = await asyncio.to_thread(usage_service.findings, filters)
        if len(view.findings) <= limit:
            return view
        # `total` already carries the full count from the service, so slicing
        # here cannot lose it.
        return view.model_copy(update={"findings": view.findings[:limit]})

    @router.get("/bash-commands", response_model=UsageBashInsightView)
    async def usage_bash_commands(
        filters: Annotated[UsageFilters, Depends()],
    ) -> UsageBashInsightView:
        """Shell time ranked by the command that LED each call.

        The response is already a bounded top-N (`usage.max_breakdown_rows`)
        with the whole population's `total_calls`/`total_ms` beside it, so it
        takes no `limit` of its own — the limit+total instrument, applied
        server-side because the cap is index policy rather than a client
        preference.

        Two counters on every row are the honesty of the ranking, not decoration:
        `censored_calls` are durations sitting at the harness's own timeout
        ceiling (when it gave up, not what the command cost) and
        `background_calls` returned a handle instantly, biasing long work down.
        """
        return await asyncio.to_thread(usage_service.bash_commands, filters)

    @router.post("/refresh", response_model=UsageRefreshView)
    async def usage_refresh(
        body: Annotated[_RefreshBody | None, Body()] = None,
    ) -> UsageRefreshView:
        """Coalesced, off-loop refresh — returns status, never the dataset.

        Two simultaneous web/TUI opens calling this at once must not launch
        two host scans: the second call joins the first's in-flight run via
        `_UsageRefreshCoalescer` and gets the same result back with
        `coalesced=True`.
        """
        return await refresh_coalescer.run(force=body.force if body is not None else False)

    return router
