"""GET /usage/bash-commands — the shell-time ranking's wire surface.

A separate file from `test_usage_endpoints.py` because that suite's
`FakeUsageService` is shared by every other usage route; this one carries the
smallest fake that can answer this route, in the same injected shape.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from grove.core.contracts.usage import (
    UsageBashCommandView,
    UsageBashInsightView,
    UsageFilters,
    UsageRefreshView,
)
from grove.core.errors import CapabilityUnavailable
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.daemon.usage import _UnavailableUsageService, build_usage_router
from tests.daemon.conftest import daemon_test_config


@dataclass
class _FakeBashService:
    view: UsageBashInsightView = field(
        default_factory=lambda: UsageBashInsightView(
            commands=(
                UsageBashCommandView(
                    executable="pytest",
                    calls=12,
                    total_ms=600_000,
                    avg_ms=50_000,
                    censored_calls=1,
                    background_calls=2,
                ),
            ),
            unattributed_calls=3,
            unattributed_ms=900,
            total_calls=15,
            total_ms=600_900,
        )
    )
    seen: list[UsageFilters] = field(default_factory=list)

    def bash_commands(self, filters: UsageFilters) -> UsageBashInsightView:
        self.seen.append(filters)
        return self.view

    def refresh(self, *, force: bool) -> UsageRefreshView:
        """`build_usage_router` binds the coalescer to this at construction, so
        it must exist even for a fake that only answers one route."""
        del force
        return UsageRefreshView()


@pytest.fixture
def service() -> _FakeBashService:
    return _FakeBashService()


@pytest.fixture
def client(service: _FakeBashService) -> Iterator[TestClient]:
    cfg = daemon_test_config()
    app = FastAPI()
    app.include_router(
        build_usage_router(
            cfg=cfg,
            registry=RepoRegistry(cfg=cfg, store=JsonWorkspaceStore()),
            usage_service=service,  # type: ignore[arg-type]
        )
    )
    with TestClient(app) as opened:
        yield opened


def test_the_route_serves_the_ranking_with_both_caveat_counters(
    client: TestClient, service: _FakeBashService
) -> None:
    """`censored_calls` and `background_calls` must survive to the wire.

    They are the two ways this ranking lies, and a client cannot reconstruct
    either from the numbers beside them.
    """
    resp = client.get("/usage/bash-commands")
    assert resp.status_code == 200
    body = resp.json()
    assert body["commands"][0]["executable"] == "pytest"
    assert body["commands"][0]["censored_calls"] == 1
    assert body["commands"][0]["background_calls"] == 2
    assert body["unattributed_calls"] == 3
    assert body["unattributed_ms"] == 900
    assert body["total_calls"] == 15
    assert body["total_ms"] == 600_900
    assert service.seen == [UsageFilters()]


def test_the_route_binds_the_standard_usage_filters(
    client: TestClient, service: _FakeBashService
) -> None:
    """The same filter vocabulary every other `/usage/*` read takes — this is
    the sibling of `/usage/findings`, not a new endpoint family."""
    resp = client.get(
        "/usage/bash-commands",
        params={"provider": "claude_code", "project": "/repo", "since": "2026-01-01T00:00:00Z"},
    )
    assert resp.status_code == 200
    assert service.seen[0].provider == "claude_code"
    assert service.seen[0].project == "/repo"


def test_the_unavailable_stand_in_refuses_this_route_too() -> None:
    """Every method on the stand-in raises, or one route 500s where the rest
    501 with the daemon's standard envelope."""
    with pytest.raises(CapabilityUnavailable):
        _UnavailableUsageService("no engine").bash_commands(UsageFilters())
