"""#967: the daemon's spans survive a crash or a dead collector, bounded and idempotent."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core.telemetry.outbox import OutboxSink
from grove.core.trace import SpanRecord

T = datetime(2026, 10, 21, tzinfo=UTC)


class _Collector:
    """An exporter-backed sink double whose flush is acknowledged or not, on demand."""

    def __init__(self, *, accepts: bool) -> None:
        self.accepts = accepts
        self.seen: list[int] = []

    def __call__(self, record: SpanRecord) -> None:
        self.seen.append(record.span_id)

    def flush(self) -> bool:
        if not self.accepts:
            raise RuntimeError("collector unreachable")
        return True


def _span(span_id: int, trace_id: int = 1) -> SpanRecord:
    return SpanRecord(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=None,
        name="n",
        kind="tool",
        start_time=T,
        end_time=T,
        attributes={"grove.request.id": "r", "tags": ("a", "b")},
    )


def test_spans_a_dead_collector_never_acknowledged_are_sent_first_next_start(
    tmp_path: Path,
) -> None:
    path = tmp_path / "outbox.sqlite3"
    down = OutboxSink(_Collector(accepts=False), path=path)
    down(_span(1))
    down(_span(2))
    assert down.flush() is False
    down.close()

    up = _Collector(accepts=True)
    OutboxSink(up, path=path)

    assert up.seen == [1, 2]
    # Acknowledged on the replay's flush, so a third start re-sends nothing.
    third = _Collector(accepts=True)
    OutboxSink(third, path=path)
    assert third.seen == []


def test_re_enqueueing_a_span_replaces_it_rather_than_duplicating(tmp_path: Path) -> None:
    path = tmp_path / "outbox.sqlite3"
    down = OutboxSink(_Collector(accepts=False), path=path)
    down(_span(7))
    down(_span(7))
    down.close()
    up = _Collector(accepts=True)
    OutboxSink(up, path=path)
    assert up.seen == [7]


def test_a_full_outbox_drops_the_oldest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(OutboxSink, "CAPACITY", 2)
    path = tmp_path / "outbox.sqlite3"
    down = OutboxSink(_Collector(accepts=False), path=path)
    for span_id in (1, 2, 3):
        down(_span(span_id))
    down.close()
    up = _Collector(accepts=True)
    OutboxSink(up, path=path)
    assert up.seen == [2, 3]
