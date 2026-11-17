"""#967: one request id and one trace context per request, continued across a hop."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.auth import SessionStore
from grove.core.config import GroveConfig
from grove.core.store import JsonWorkspaceStore
from grove.core.trace import SpanRecord
from grove.daemon import build_app

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
PARENT = "00f067aa0ba902b7"
Audited = tuple[TestClient, list[SpanRecord], str]


@pytest.fixture
def audited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Audited]:
    """A real daemon whose span sink records what it is handed, plus a local bearer."""
    spans: list[SpanRecord] = []

    class _Sink:
        def __call__(self, record: SpanRecord) -> None:
            spans.append(record)

        def flush(self) -> bool:
            return True

    monkeypatch.setattr("grove.daemon.app.build_span_sink", lambda _cfg: _Sink())
    auth = SessionStore(path=tmp_path / "auth.json")
    cfg = GroveConfig.model_validate({"reaper": {"enabled": False}})
    app = build_app(cfg=cfg, store=JsonWorkspaceStore(path=tmp_path / "s.json"), auth_store=auth)
    with TestClient(app) as client:
        yield client, spans, auth.mint_local("local-test")


def _audit_spans(spans: list[SpanRecord], request_id: str) -> list[SpanRecord]:
    return [s for s in spans if s.attributes.get("grove.request.id") == request_id]


def test_a_forwarded_request_keeps_its_id_and_continues_the_callers_trace(
    audited: Audited,
) -> None:
    client, spans, token = audited
    response = client.get(
        "/whoami",
        headers={
            "authorization": f"Bearer {token}",
            "x-request-id": "req-1",
            "traceparent": f"00-{TRACE}-{PARENT}-01",
        },
    )
    assert response.headers["x-request-id"] == "req-1"
    (span,) = _audit_spans(spans, "req-1")
    assert f"{span.trace_id:032x}" == TRACE
    assert span.parent_span_id is not None and f"{span.parent_span_id:016x}" == PARENT
    assert span.attributes["grove.principal.subject"] == "local"


def test_a_request_with_neither_header_gets_a_fresh_id_and_root(audited: Audited) -> None:
    client, spans, token = audited
    response = client.get("/whoami", headers={"authorization": f"Bearer {token}"})
    request_id = response.headers["x-request-id"]
    assert len(request_id) == 32
    (span,) = _audit_spans(spans, request_id)
    assert span.parent_span_id is None


def test_an_unauthenticated_request_emits_no_audit_span(audited: Audited) -> None:
    client, spans, _ = audited
    response = client.get("/healthz")
    assert not _audit_spans(spans, response.headers["x-request-id"])
