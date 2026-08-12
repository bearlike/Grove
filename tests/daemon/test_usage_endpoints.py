"""GET/POST /usage/* — the bounded read routes over the usage-audit engine.

`grove.core.usage.UsageService` is a concurrently-developed engine package
(tracker `#445`/`#446`); this suite exercises `build_usage_router` against an
INJECTED fake implementing `UsageServiceProtocol`, per the router's own
docstring on why it is typed against a structural surface rather than the
concrete class. Two fixtures:

- `usage_client` mounts the router directly (bare app, no auth) against a
  `FakeUsageService` — the business-logic tests (query binding, pagination,
  coalescing).
- `test_unauthenticated_request_is_refused` goes through the REAL
  `build_app()` with auth enabled, proving the daemon's normal
  `dependencies=auth_dep` gate actually covers this router (the two-line
  `app.py` integration point).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from grove.core.config import GroveConfig
from grove.core.contracts.usage import (
    UsageActivityView,
    UsageBreakdownView,
    UsageDimension,
    UsageFilters,
    UsageFindingsView,
    UsageFindingView,
    UsageMetric,
    UsageQuotasView,
    UsageRefreshView,
    UsageSeriesGroupView,
    UsageSeriesPointView,
    UsageSeriesView,
    UsageSessionPageView,
    UsageSessionRowView,
    UsageSessionSort,
    UsageSummaryView,
)
from grove.core.errors import CapabilityUnavailable
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from grove.daemon.usage import (
    UsageServiceProtocol,
    _default_usage_service,
    _UnavailableUsageService,
    _UsageRefreshCoalescer,
    build_usage_router,
)
from tests.daemon.conftest import daemon_test_config


@dataclass
class FakeUsageService:
    """Records every call it receives and answers with canned views."""

    summary_view: UsageSummaryView = field(default_factory=UsageSummaryView)
    activity_view: UsageActivityView = field(
        default_factory=lambda: UsageActivityView(metric="tokens")
    )
    sessions_view: UsageSessionPageView = field(default_factory=UsageSessionPageView)
    breakdown_view: UsageBreakdownView = field(
        default_factory=lambda: UsageBreakdownView(dimension="provider")
    )
    series_view: UsageSeriesView = field(
        default_factory=lambda: UsageSeriesView(metric="tokens", dimension="model")
    )
    quotas_view: UsageQuotasView = field(default_factory=UsageQuotasView)
    findings_view: UsageFindingsView = field(default_factory=UsageFindingsView)
    refresh_view: UsageRefreshView = field(default_factory=UsageRefreshView)
    # Fires once the fake's `refresh` has been entered — lets a test block a
    # concurrent second call until it has verifiably joined the first.
    refresh_started: threading.Event = field(default_factory=threading.Event)
    refresh_release: threading.Event | None = None

    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = field(default_factory=list)

    def summary(self, filters: UsageFilters) -> UsageSummaryView:
        self.calls.append(("summary", (filters,), {}))
        return self.summary_view

    def activity(self, filters: UsageFilters, *, metric: UsageMetric) -> UsageActivityView:
        self.calls.append(("activity", (filters,), {"metric": metric}))
        return self.activity_view

    def sessions(
        self,
        filters: UsageFilters,
        *,
        cursor: str | None,
        limit: int,
        sort: UsageSessionSort,
    ) -> UsageSessionPageView:
        self.calls.append(
            ("sessions", (filters,), {"cursor": cursor, "limit": limit, "sort": sort})
        )
        return self.sessions_view

    def breakdown(self, filters: UsageFilters, *, dimension: UsageDimension) -> UsageBreakdownView:
        self.calls.append(("breakdown", (filters,), {"dimension": dimension}))
        return self.breakdown_view

    def series(
        self, filters: UsageFilters, *, dimension: UsageDimension, metric: UsageMetric
    ) -> UsageSeriesView:
        self.calls.append(("series", (filters,), {"dimension": dimension, "metric": metric}))
        return self.series_view

    def quotas(self) -> UsageQuotasView:
        self.calls.append(("quotas", (), {}))
        return self.quotas_view

    def findings(self, filters: UsageFilters) -> UsageFindingsView:
        self.calls.append(("findings", (filters,), {}))
        return self.findings_view

    def refresh(self, *, force: bool) -> UsageRefreshView:
        self.calls.append(("refresh", (), {"force": force}))
        self.refresh_started.set()
        if self.refresh_release is not None:
            self.refresh_release.wait(timeout=5)
        return self.refresh_view

    def close(self) -> None:
        self.calls.append(("close", (), {}))


def _build_client(fake: UsageServiceProtocol) -> TestClient:
    cfg = daemon_test_config()
    registry = RepoRegistry(cfg=cfg, store=JsonWorkspaceStore())
    app = FastAPI()
    app.include_router(build_usage_router(cfg=cfg, registry=registry, usage_service=fake))
    return TestClient(app)


@pytest.fixture
def fake_service() -> FakeUsageService:
    return FakeUsageService()


@pytest.fixture
def usage_client(fake_service: FakeUsageService) -> Iterator[TestClient]:
    with _build_client(fake_service) as client:
        yield client


# ─── filter binding, shared across the read routes ──────────────────────────


def test_summary_forwards_query_filters(
    usage_client: TestClient, fake_service: FakeUsageService
) -> None:
    resp = usage_client.get(
        "/usage/summary",
        params={
            "since": "2026-01-01T00:00:00+00:00",
            "until": "2026-02-01T00:00:00+00:00",
            "tz": "America/Los_Angeles",
            "provider": "claude_code",
            "account": "acct-1",
            "project": "/repo",
            "model": "opus",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == fake_service.summary_view.model_dump(mode="json")
    (method, args, _kwargs) = fake_service.calls[0]
    assert method == "summary"
    filters = args[0]
    assert isinstance(filters, UsageFilters)
    assert filters.provider == "claude_code"
    assert filters.account == "acct-1"
    assert filters.project == "/repo"
    assert filters.model == "opus"
    assert filters.tz == "America/Los_Angeles"


def test_summary_defaults_filters_when_omitted(
    usage_client: TestClient, fake_service: FakeUsageService
) -> None:
    resp = usage_client.get("/usage/summary")
    assert resp.status_code == 200
    filters = fake_service.calls[0][1][0]
    assert filters.since is None
    assert filters.until is None
    assert filters.tz == "UTC"
    assert filters.provider is None


def test_day_filter_rejects_malformed_value(usage_client: TestClient) -> None:
    """`day` is a `YYYY-MM-DD`-patterned field on the frozen contract — a bad
    value must 422 at the query boundary, not reach the engine."""
    resp = usage_client.get("/usage/summary", params={"day": "not-a-date"})
    assert resp.status_code == 422


# ─── /usage/activity ─────────────────────────────────────────────────────────


def test_activity_defaults_metric_to_tokens(
    usage_client: TestClient, fake_service: FakeUsageService
) -> None:
    resp = usage_client.get("/usage/activity")
    assert resp.status_code == 200
    _method, _args, kwargs = fake_service.calls[0]
    assert kwargs["metric"] == "tokens"


def test_activity_accepts_metric_query_param(
    usage_client: TestClient, fake_service: FakeUsageService
) -> None:
    resp = usage_client.get("/usage/activity", params={"metric": "cost"})
    assert resp.status_code == 200
    assert fake_service.calls[0][2]["metric"] == "cost"


def test_activity_rejects_unknown_metric(usage_client: TestClient) -> None:
    resp = usage_client.get("/usage/activity", params={"metric": "not-a-metric"})
    assert resp.status_code == 422


# ─── /usage/sessions ─────────────────────────────────────────────────────────


def test_sessions_forwards_cursor_limit_sort(
    usage_client: TestClient, fake_service: FakeUsageService
) -> None:
    resp = usage_client.get(
        "/usage/sessions", params={"cursor": "opaque-1", "limit": 10, "sort": "cost"}
    )
    assert resp.status_code == 200
    kwargs = fake_service.calls[0][2]
    assert kwargs == {"cursor": "opaque-1", "limit": 10, "sort": "cost"}


def test_sessions_default_limit_and_sort(
    usage_client: TestClient, fake_service: FakeUsageService
) -> None:
    resp = usage_client.get("/usage/sessions")
    assert resp.status_code == 200
    kwargs = fake_service.calls[0][2]
    assert kwargs["cursor"] is None
    assert kwargs["sort"] == "recent"
    assert kwargs["limit"] > 0


def test_sessions_limit_is_clamped_to_configured_max(
    usage_client: TestClient, fake_service: FakeUsageService
) -> None:
    """The handler clamps oversized pages to the configured policy maximum."""
    cfg = daemon_test_config()
    over_limit = cfg.usage.max_sessions_per_page + 1
    resp = usage_client.get("/usage/sessions", params={"limit": over_limit})
    assert resp.status_code == 200
    assert fake_service.calls[0][2]["limit"] == cfg.usage.max_sessions_per_page


def test_sessions_row_shape_round_trips(usage_client: TestClient) -> None:
    fake = FakeUsageService(
        sessions_view=UsageSessionPageView(
            rows=(UsageSessionRowView(session_id="s1", provider="codex"),),
            next_cursor="opaque-2",
        )
    )
    with _build_client(fake) as client:
        resp = client.get("/usage/sessions")
    body = resp.json()
    assert body["next_cursor"] == "opaque-2"
    assert body["rows"][0]["session_id"] == "s1"
    assert body["rows"][0]["provider"] == "codex"


# ─── /usage/breakdowns ────────────────────────────────────────────────────────


def test_breakdowns_requires_dimension(usage_client: TestClient) -> None:
    resp = usage_client.get("/usage/breakdowns")
    assert resp.status_code == 422


def test_breakdowns_forwards_dimension(
    usage_client: TestClient, fake_service: FakeUsageService
) -> None:
    resp = usage_client.get("/usage/breakdowns", params={"dimension": "model"})
    assert resp.status_code == 200
    assert fake_service.calls[0][2] == {"dimension": "model"}


def test_breakdowns_rejects_unknown_dimension(usage_client: TestClient) -> None:
    resp = usage_client.get("/usage/breakdowns", params={"dimension": "nonsense"})
    assert resp.status_code == 422


# ─── /usage/series ────────────────────────────────────────────────────────────


def test_series_requires_a_dimension(usage_client: TestClient) -> None:
    assert usage_client.get("/usage/series").status_code == 422


def test_series_defaults_metric_to_tokens_and_forwards_the_dimension(
    usage_client: TestClient, fake_service: FakeUsageService
) -> None:
    resp = usage_client.get("/usage/series", params={"dimension": "account"})
    assert resp.status_code == 200
    method, _args, kwargs = fake_service.calls[0]
    assert method == "series"
    assert kwargs == {"dimension": "account", "metric": "tokens"}


def test_series_serves_the_same_dimension_vocabulary_as_breakdowns(
    usage_client: TestClient, fake_service: FakeUsageService
) -> None:
    """One route for all six axes, per `UsageDimension`'s own contract — a
    per-card enum is how two surfaces come to disagree about what a project is.
    """
    for dimension in ("provider", "account", "project", "model", "tool", "token_class"):
        assert usage_client.get("/usage/series", params={"dimension": dimension}).status_code == 200
    assert usage_client.get("/usage/series", params={"dimension": "plan"}).status_code == 422
    assert [call[2]["dimension"] for call in fake_service.calls] == [
        "provider",
        "account",
        "project",
        "model",
        "tool",
        "token_class",
    ]


def test_series_forwards_the_metric_and_refuses_an_unknown_one(
    usage_client: TestClient, fake_service: FakeUsageService
) -> None:
    resp = usage_client.get("/usage/series", params={"dimension": "tool", "metric": "tool_calls"})
    assert resp.status_code == 200
    assert fake_service.calls[0][2]["metric"] == "tool_calls"
    assert (
        usage_client.get(
            "/usage/series", params={"dimension": "tool", "metric": "vibes"}
        ).status_code
        == 422
    )


def test_series_forwards_the_shared_filters(
    usage_client: TestClient, fake_service: FakeUsageService
) -> None:
    resp = usage_client.get(
        "/usage/series",
        params={
            "dimension": "model",
            "since": "2026-08-04T00:00:00+00:00",
            "tz": "Europe/London",
            "provider": "codex",
        },
    )
    assert resp.status_code == 200
    filters = fake_service.calls[0][1][0]
    assert isinstance(filters, UsageFilters)
    assert filters.tz == "Europe/London"
    assert filters.provider == "codex"


def test_series_carries_the_spine_and_its_null_points_across_the_wire(
    usage_client: TestClient, fake_service: FakeUsageService
) -> None:
    """A `null` point is the whole contract: a client that received only the
    measured days would draw a chart whose gaps close up, and a zero would claim
    a measurement nobody took.
    """
    fake_service.series_view = UsageSeriesView(
        metric="tokens",
        dimension="account",
        days=("2026-08-09", "2026-08-10"),
        groups=(
            UsageSeriesGroupView(
                key="acct-1",
                label="Personal (max)",
                total=100.0,
                points=(
                    UsageSeriesPointView(day="2026-08-09", value=100.0),
                    UsageSeriesPointView(day="2026-08-10", value=None),
                ),
            ),
        ),
        truncated=True,
    )
    body = usage_client.get("/usage/series", params={"dimension": "account"}).json()

    assert body["days"] == ["2026-08-09", "2026-08-10"]
    assert body["groups"][0]["label"] == "Personal (max)"
    assert [point["value"] for point in body["groups"][0]["points"]] == [100.0, None]
    assert body["truncated"] is True


# ─── /usage/quotas ────────────────────────────────────────────────────────────


def test_quotas_takes_no_filters(usage_client: TestClient, fake_service: FakeUsageService) -> None:
    resp = usage_client.get("/usage/quotas", params={"provider": "codex"})
    assert resp.status_code == 200
    method, args, kwargs = fake_service.calls[0]
    assert method == "quotas"
    assert args == ()
    assert kwargs == {}


# ─── /usage/findings ──────────────────────────────────────────────────────────


def test_findings_forwards_filters(
    usage_client: TestClient, fake_service: FakeUsageService
) -> None:
    resp = usage_client.get("/usage/findings", params={"provider": "codex"})
    assert resp.status_code == 200
    method, args, kwargs = fake_service.calls[0]
    assert method == "findings"
    assert args[0].provider == "codex"
    assert kwargs == {}
    assert resp.json()["findings"] == []
    assert isinstance(resp.json()["coverage"], dict)


def _findings(count: int) -> UsageFindingsView:
    """A ranked page as the service builds it — `total` is the COMPLETE count."""
    return UsageFindingsView(
        findings=tuple(
            UsageFindingView(kind="recurring_tool_failure", title=f"f{i}") for i in range(count)
        ),
        total=count,
    )


def test_findings_caps_the_page_and_says_how_many_it_withheld(
    usage_client: TestClient, fake_service: FakeUsageService
) -> None:
    """The measured real response was 2475 findings / 1.4 MB — a cap it must report."""
    fake_service.findings_view = _findings(2475)
    body = usage_client.get("/usage/findings").json()
    assert len(body["findings"]) == 100  # the documented default
    assert body["total"] == 2475  # nothing is silently dropped
    assert body["findings"][0]["title"] == "f0"  # ranked head, not an arbitrary slice


def test_findings_limit_is_bounded_so_a_client_cannot_ask_for_everything(
    usage_client: TestClient, fake_service: FakeUsageService
) -> None:
    fake_service.findings_view = _findings(2475)
    assert len(usage_client.get("/usage/findings", params={"limit": 5}).json()["findings"]) == 5
    assert usage_client.get("/usage/findings", params={"limit": 501}).status_code == 422
    assert usage_client.get("/usage/findings", params={"limit": 0}).status_code == 422


def test_findings_under_the_limit_are_returned_whole_with_a_matching_total(
    usage_client: TestClient, fake_service: FakeUsageService
) -> None:
    """`total == len(findings)` is the client's signal that nothing was withheld."""
    fake_service.findings_view = _findings(3)
    body = usage_client.get("/usage/findings").json()
    assert len(body["findings"]) == 3
    assert body["total"] == 3


# ─── /usage/refresh ───────────────────────────────────────────────────────────


def test_refresh_forwards_force_flag(
    usage_client: TestClient, fake_service: FakeUsageService
) -> None:
    resp = usage_client.post("/usage/refresh", json={"force": True})
    assert resp.status_code == 200
    assert fake_service.calls[0] == ("refresh", (), {"force": True})
    assert resp.json()["coalesced"] is False


def test_refresh_defaults_to_no_body(
    usage_client: TestClient, fake_service: FakeUsageService
) -> None:
    resp = usage_client.post("/usage/refresh")
    assert resp.status_code == 200
    assert fake_service.calls[0][2] == {"force": False}


def test_concurrent_refresh_calls_are_coalesced() -> None:
    """Two simultaneous refreshes must not launch two host scans.

    Thread A's request enters `FakeUsageService.refresh` and blocks there
    (via `refresh_release`) until the main thread lets it go; while it is
    blocked, a second refresh is issued and must join the SAME in-flight
    run rather than starting a second one. Proves both the single
    executor call (`len(fake.calls) == 1`) and the per-caller
    `coalesced` flag (initiator ``False``, joiner ``True``).
    """
    release = threading.Event()
    fake = FakeUsageService(
        refresh_view=UsageRefreshView(indexed_sources=3), refresh_release=release
    )
    with _build_client(fake) as client:
        results: dict[str, dict[str, Any]] = {}

        def _call_a() -> None:
            results["a"] = client.post("/usage/refresh").json()

        thread_a = threading.Thread(target=_call_a)
        thread_a.start()
        assert fake.refresh_started.wait(timeout=5), "first refresh never started"

        results["b"] = client.post("/usage/refresh").json()
        release.set()
        thread_a.join(timeout=5)

    assert len(fake.calls) == 1, "a joining caller must not trigger a second scan"
    assert results["a"]["coalesced"] is False
    assert results["b"]["coalesced"] is True
    # Both callers see the SAME underlying result, just tagged differently.
    assert results["a"]["indexed_sources"] == 3
    assert results["b"]["indexed_sources"] == 3


@pytest.mark.asyncio
async def test_cancelled_initiator_keeps_refresh_single_flight() -> None:
    """Disconnecting the first caller must not expose a still-running scan."""
    release = threading.Event()
    fake = FakeUsageService(refresh_release=release)
    coalescer = _UsageRefreshCoalescer(fake.refresh)

    initiator = asyncio.create_task(coalescer.run(force=False))
    assert await asyncio.to_thread(fake.refresh_started.wait, 5)
    initiator.cancel()
    with pytest.raises(asyncio.CancelledError):
        await initiator

    joiner = asyncio.create_task(coalescer.run(force=True))
    await asyncio.sleep(0.05)
    assert len(fake.calls) == 1
    release.set()
    result = await joiner

    assert result.coalesced is True
    assert fake.calls[0] == ("refresh", (), {"force": False})


# ─── the engine seam not being ready yet ─────────────────────────────────────


def test_unavailable_service_raises_capability_unavailable() -> None:
    """`_UnavailableUsageService` (what `_default_usage_service` falls back to
    while `grove.core.usage` is still under construction) fails every method
    with `CapabilityUnavailable` — the daemon's app-level `GroveError` handler
    already maps that to 501 for every OTHER capability-gap route, so this
    router needs no route-local `except GroveError` of its own."""
    stub = _UnavailableUsageService("boom")
    with pytest.raises(CapabilityUnavailable):
        stub.summary(UsageFilters())
    with pytest.raises(CapabilityUnavailable):
        stub.refresh(force=False)
    with pytest.raises(CapabilityUnavailable):
        stub.findings(UsageFilters())
    with pytest.raises(CapabilityUnavailable):
        stub.series(UsageFilters(), dimension="model", metric="tokens")


def test_default_usage_service_never_crashes_build_app() -> None:
    """Whether `grove.core.usage.UsageService` is importable and constructible
    from just ``(cfg, registry)`` or not — `#445`/`#446` land it separately
    from this story — `_default_usage_service` must return something that
    answers `.summary()`: either the real engine's data, or a
    `CapabilityUnavailable` 501 (the degrade-gracefully stub). Either way it
    must never propagate an `ImportError`/`TypeError` out of `build_app`.
    """
    cfg = daemon_test_config()
    registry = RepoRegistry(cfg=cfg, store=JsonWorkspaceStore())
    service = _default_usage_service(cfg=cfg, registry=registry)
    try:
        result = service.summary(UsageFilters())
    except CapabilityUnavailable:
        pass
    else:
        assert isinstance(result, UsageSummaryView)


# ─── auth wiring (the actual app.py integration, not the bare router) ───────


def test_unauthenticated_request_is_refused() -> None:
    """`/usage/*` is mounted with ``dependencies=auth_dep`` in `app.py`, same
    as every other gated route group — this is the one test that goes through
    the REAL `build_app()` rather than the bare-router fixture, to prove that
    wiring rather than merely asserting it by reading the source."""
    app = build_app(cfg=GroveConfig(), store=JsonWorkspaceStore())  # auth.enabled defaults True
    with TestClient(app) as client:
        resp = client.get("/usage/summary")
    assert resp.status_code == 401
