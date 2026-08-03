"""POST /issue-ops/events — the CI-forwarder ingest route.

The engine's routing is covered exhaustively in ``tests/core/issueops``; here we
pin only the daemon seam: the request body validates as an ``IssueOpsEvent``, the
engine is invoked with it, and its ``IssueOpsOutcome`` comes back as a 202. A
capturing fake engine isolates the route from the engine internals.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.contracts.issueops import IssueOpsEvent, IssueOpsOutcome
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config


class _FakeEngine:
    def __init__(self, outcome: IssueOpsOutcome) -> None:
        self.outcome = outcome
        self.received: list[IssueOpsEvent] = []

    def handle(self, event: IssueOpsEvent) -> IssueOpsOutcome:
        self.received.append(event)
        return self.outcome


def _event_body(**over: object) -> dict[str, object]:
    body: dict[str, object] = {
        "provider": "gitea",
        "owner": "acme",
        "repo": "widget",
        "issue_number": 42,
        "issue_title": "Fix the login",
        "issue_body": "It breaks.",
        "issue_url": "https://git.example/acme/widget/issues/42",
        "comment_id": "comment-100",
        "comment_body": "@grove fix it",
        "actor": "alice",
        "actor_permission": "write",
    }
    body.update(over)
    return body


@pytest.fixture
def engine() -> _FakeEngine:
    return _FakeEngine(IssueOpsOutcome(action="created", workspace_id="ws-1"))


@pytest.fixture
def daemon(tmp_state_dir: Path, fake_tmux: FakeTmux, engine: _FakeEngine) -> Iterator[TestClient]:
    del fake_tmux
    store = JsonWorkspaceStore()
    app = build_app(cfg=daemon_test_config(), store=store, issue_ops_engine=engine)  # type: ignore[arg-type]
    with TestClient(app) as client:
        yield client


def test_ingest_routes_the_event_and_returns_the_outcome(
    daemon: TestClient, engine: _FakeEngine
) -> None:
    resp = daemon.post("/issue-ops/events", json=_event_body())
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"action": "created", "code": None, "workspace_id": "ws-1"}
    assert len(engine.received) == 1
    assert engine.received[0].comment_id == "comment-100"
    assert engine.received[0].issue_number == 42


def test_ingest_rejects_a_malformed_event(daemon: TestClient) -> None:
    resp = daemon.post("/issue-ops/events", json={"provider": "gitea"})  # missing required fields
    assert resp.status_code == 422


def test_ingest_reflects_a_refusal_outcome(daemon: TestClient, engine: _FakeEngine) -> None:
    engine.outcome = IssueOpsOutcome(action="refused", code="insufficient_permission")
    resp = daemon.post("/issue-ops/events", json=_event_body(actor_permission="read"))
    assert resp.status_code == 202
    assert resp.json()["code"] == "insufficient_permission"
