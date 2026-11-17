"""Focused recovery tests for durable historical telemetry checkpoints."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from grove.core.config import TelemetryConfig
from grove.core.telemetry_backfill import (
    LangfuseTraceProbe,
    LiveAcknowledgements,
    TelemetryBackfillExporter,
    TelemetryCheckpointLedger,
)
from grove.core.trace import SpanRecord, TraceManifest

_TRACE_ID = 0x1234
_ROOT_ID = 0x10
_CHILD_ID = 0x20
_ENV = {
    "LANGFUSE_HOST": "https://langfuse.example",
    "LANGFUSE_PUBLIC_KEY": "pk-test",
    "LANGFUSE_SECRET_KEY": "sk-test",
}
_NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)
_DESTINATION = "langfuse-test-project"


@dataclass(slots=True)
class _Sink:
    on_flush: Callable[[], None] | None = None
    fail_flush: bool = False
    records: list[SpanRecord] = field(default_factory=list)
    flushes: int = 0

    def __call__(self, record: SpanRecord) -> None:
        self.records.append(record)

    def flush(self) -> None:
        self.flushes += 1
        if self.fail_flush:
            raise RuntimeError("scripted flush failure")
        if self.on_flush is not None:
            self.on_flush()


def _manifest(*, variant: str = "v1") -> TraceManifest:
    root = SpanRecord.agent(
        trace_id=_TRACE_ID,
        span_id=_ROOT_ID,
        parent_span_id=None,
        name="session:test",
        start_time=_NOW,
        end_time=_NOW,
    )
    child = SpanRecord.generation(
        trace_id=_TRACE_ID,
        span_id=_CHILD_ID,
        parent_span_id=_ROOT_ID,
        # The span name is now derived from `model`, so the variant that used
        # to distinguish "v1" from "v2" traces (and their fingerprints) rides
        # the model id instead of a free-form name.
        model=f"assistant-{variant}",
        start_time=_NOW,
        end_time=_NOW,
    )
    return TraceManifest(
        session_id="session-test",
        turn_id="turn-test",
        trace_id=_TRACE_ID,
        spans=(root, child),
    )


def _response(ids: set[int], *, cursor: str | None = None) -> httpx.Response:
    # The v2 observations list as Langfuse 4.48 returns it (captured 2026-10-02):
    # rows under `data`, the next page's cursor under `meta`, `{}` on the last page.
    return httpx.Response(
        200,
        json={
            "data": [{"id": f"{span_id:016x}", "type": "SPAN"} for span_id in sorted(ids)],
            "meta": {"cursor": cursor} if cursor else {},
        },
    )


def _probe(handler: Callable[[httpx.Request], httpx.Response]) -> LangfuseTraceProbe:
    return LangfuseTraceProbe(
        TelemetryConfig(enabled=True),
        env=_ENV,
        transport=httpx.MockTransport(handler),
    )


def _exporter(
    tmp_path: Path,
    *,
    probe: LangfuseTraceProbe,
    sink: _Sink,
) -> tuple[TelemetryCheckpointLedger, TelemetryBackfillExporter]:
    ledger = TelemetryCheckpointLedger(
        tmp_path / "telemetry-checkpoints.sqlite3", destination_id=_DESTINATION
    )
    return ledger, TelemetryBackfillExporter(ledger=ledger, probe=probe, sink=sink)


def test_completed_checkpoint_skips_probe_and_sink(tmp_path: Path) -> None:
    manifest = _manifest()
    ledger = TelemetryCheckpointLedger(
        tmp_path / "telemetry-checkpoints.sqlite3", destination_id=_DESTINATION
    )
    assert ledger.complete(manifest.trace_id, manifest.fingerprint).status == "completed"

    def unexpected_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected remote probe: {request.url}")

    sink = _Sink()
    exporter = TelemetryBackfillExporter(
        ledger=ledger,
        probe=_probe(unexpected_request),
        sink=sink,
    )
    result = exporter.export((manifest,), limit=1)

    assert result.skipped == 1
    assert result.manifests[0].detail == "trace already has a completed checkpoint"
    assert sink.records == []
    assert sink.flushes == 0


def test_checkpoint_is_scoped_to_the_remote_destination(tmp_path: Path) -> None:
    manifest = _manifest()
    path = tmp_path / "telemetry-checkpoints.sqlite3"
    first = TelemetryCheckpointLedger(path, destination_id="project-a")
    second = TelemetryCheckpointLedger(path, destination_id="project-b")

    assert first.complete(manifest.trace_id, manifest.fingerprint).status == "completed"
    assert second.check(manifest.trace_id, manifest.fingerprint).status == "pending"
    first.close()
    second.close()


def test_remote_complete_trace_is_checkpointed_without_export(tmp_path: Path) -> None:
    manifest = _manifest()
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return _response({_ROOT_ID, _CHILD_ID})

    sink = _Sink()
    ledger, exporter = _exporter(tmp_path, probe=_probe(handler), sink=sink)
    result = exporter.export((manifest,), limit=1)

    assert result.completed == 1
    assert result.emitted_spans == 0
    assert requested_paths == ["/api/public/v2/observations"]
    assert sink.flushes == 0
    assert ledger.check(manifest.trace_id, manifest.fingerprint).status == "completed"


def test_remote_partial_trace_emits_only_missing_observation(tmp_path: Path) -> None:
    manifest = _manifest()
    remote_ids = {_ROOT_ID}
    sink = _Sink(on_flush=lambda: remote_ids.add(_CHILD_ID))

    def handler(request: httpx.Request) -> httpx.Response:
        return _response(remote_ids)

    ledger, exporter = _exporter(tmp_path, probe=_probe(handler), sink=sink)
    result = exporter.export((manifest,), limit=1)

    assert result.completed == 1
    assert [record.span_id for record in sink.records] == [_CHILD_ID]
    assert sink.flushes == 1
    assert ledger.check(manifest.trace_id, manifest.fingerprint).status == "completed"


def test_completed_trace_hash_mismatch_is_degraded_and_never_overwritten(tmp_path: Path) -> None:
    original = _manifest(variant="v1")
    changed = _manifest(variant="v2")
    ledger = TelemetryCheckpointLedger(
        tmp_path / "telemetry-checkpoints.sqlite3", destination_id=_DESTINATION
    )
    ledger.complete(original.trace_id, original.fingerprint)

    def unexpected_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected remote probe: {request.url}")

    result = TelemetryBackfillExporter(
        ledger=ledger,
        probe=_probe(unexpected_request),
        sink=_Sink(),
    ).export((changed,), limit=1)

    assert result.degraded == 1
    assert result.manifests[0].detail == "manifest hash differs from the completed checkpoint"
    assert ledger.check(original.trace_id, original.fingerprint).status == "completed"
    assert ledger.check(changed.trace_id, changed.fingerprint).status == "mismatch"


def test_network_failure_degrades_without_export_or_checkpoint(tmp_path: Path) -> None:
    manifest = _manifest()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    sink = _Sink()
    ledger, exporter = _exporter(tmp_path, probe=_probe(handler), sink=sink)
    result = exporter.export((manifest,), limit=1)

    assert result.degraded == 1
    assert result.manifests[0].detail == "Langfuse trace probe failed"
    assert sink.records == []
    assert sink.flushes == 0
    assert ledger.check(manifest.trace_id, manifest.fingerprint).status == "pending"


def test_unexpected_remote_shape_degrades_without_export_or_checkpoint(tmp_path: Path) -> None:
    manifest = _manifest()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{}], "meta": {}})

    sink = _Sink()
    ledger, exporter = _exporter(tmp_path, probe=_probe(handler), sink=sink)
    result = exporter.export((manifest,), limit=1)

    assert result.degraded == 1
    assert result.manifests[0].detail == "Langfuse observations had an unexpected shape"
    assert sink.records == []
    assert ledger.check(manifest.trace_id, manifest.fingerprint).status == "pending"


def test_failed_flush_never_marks_completion(tmp_path: Path) -> None:
    manifest = _manifest()
    probe_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal probe_calls
        probe_calls += 1
        return _response(set())

    sink = _Sink(fail_flush=True)
    ledger, exporter = _exporter(tmp_path, probe=_probe(handler), sink=sink)
    result = exporter.export((manifest,), limit=1)

    assert result.degraded == 1
    assert result.manifests[0].detail == "telemetry export or flush failed"
    assert sink.flushes == 1
    assert probe_calls == 1
    assert ledger.check(manifest.trace_id, manifest.fingerprint).status == "pending"


def test_submitted_trace_waits_for_visibility_then_retries_only_still_missing(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    remote_ids: set[int] = set()

    def handler(request: httpx.Request) -> httpx.Response:
        return _response(remote_ids)

    first_sink = _Sink(on_flush=lambda: remote_ids.add(_ROOT_ID))
    ledger, first_exporter = _exporter(tmp_path, probe=_probe(handler), sink=first_sink)
    first = first_exporter.export((manifest,), limit=1)

    assert first.submitted == 1
    assert first.manifests[0].detail == "collector accepted trace; awaiting 1 observations"
    assert ledger.check(manifest.trace_id, manifest.fingerprint).status == "submitted"

    waiting_sink = _Sink()
    waiting = TelemetryBackfillExporter(
        ledger=ledger,
        probe=_probe(handler),
        sink=waiting_sink,
    ).export((manifest,), limit=1)
    assert waiting.submitted == 1
    assert waiting_sink.records == []

    second_sink = _Sink(on_flush=lambda: remote_ids.add(_CHILD_ID))
    second = TelemetryBackfillExporter(
        ledger=ledger,
        probe=_probe(handler),
        sink=second_sink,
        submission_grace=timedelta(0),
    ).export((manifest,), limit=1)

    assert second.completed == 1
    assert [record.span_id for record in second_sink.records] == [_CHILD_ID]
    assert second_sink.flushes == 1
    assert ledger.check(manifest.trace_id, manifest.fingerprint).status == "completed"


def test_probe_reads_every_page_of_a_large_trace(tmp_path: Path) -> None:
    """A trace larger than one page is read whole, or the rest is re-sent."""
    manifest = _manifest()
    cursors: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor")
        cursors.append(cursor)
        if cursor is None:
            return _response({_ROOT_ID}, cursor="page-2")
        return _response({_CHILD_ID})

    sink = _Sink()
    _ledger, exporter = _exporter(tmp_path, probe=_probe(handler), sink=sink)
    result = exporter.export((manifest,), limit=1)

    assert cursors == [None, "page-2"]
    assert result.completed == 1
    assert sink.records == []


def test_an_unavailable_trace_endpoint_is_never_read_as_an_absent_trace(tmp_path: Path) -> None:
    """A 404 is a missing ENDPOINT, never "nothing remote".

    A v4 deployment in events_only mode answers 404 for every trace on the v1
    read (measured 2026-10-02). Reading that as absence re-sent every trace whole.
    An absent trace is a 200 with no rows.
    """
    manifest = _manifest()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "not available in events_only mode"})

    sink = _Sink()
    ledger, exporter = _exporter(tmp_path, probe=_probe(handler), sink=sink)
    result = exporter.export((manifest,), limit=1)

    assert result.degraded == 1
    assert sink.records == []
    assert ledger.check(manifest.trace_id, manifest.fingerprint).status == "pending"


def test_live_acknowledgements_outlive_the_process_that_recorded_them(tmp_path: Path) -> None:
    """A restart must not re-send a turn the collector already accepted (#905)."""
    manifest = _manifest()
    path = tmp_path / "telemetry-checkpoints.sqlite3"
    first = LiveAcknowledgements(TelemetryCheckpointLedger(path, destination_id=_DESTINATION))
    first.accept(manifest)
    first.close()

    reopened = LiveAcknowledgements(TelemetryCheckpointLedger(path, destination_id=_DESTINATION))
    assert reopened.accepted(frozenset({_TRACE_ID, 0x9999})) == frozenset({_TRACE_ID})
    reopened.close()

    other = LiveAcknowledgements(TelemetryCheckpointLedger(path, destination_id="other-project"))
    assert other.accepted(frozenset({_TRACE_ID})) == frozenset()
    other.close()


def test_a_live_acknowledgement_is_submitted_so_backfill_still_verifies_it(
    tmp_path: Path,
) -> None:
    """The live path claims collector acceptance, never remote completeness."""
    manifest = _manifest()
    ledger = TelemetryCheckpointLedger(
        tmp_path / "telemetry-checkpoints.sqlite3", destination_id=_DESTINATION
    )
    LiveAcknowledgements(ledger).accept(manifest)

    assert ledger.check(manifest.trace_id, manifest.fingerprint).status == "submitted"


def test_live_acknowledgements_from_config_shares_the_probes_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live gate and backfill must key one row per trace, or each re-sends the other's."""
    monkeypatch.setattr(
        "grove.core.paths.telemetry_ledger_path", lambda: tmp_path / "ledger.sqlite3"
    )
    live = LiveAcknowledgements.from_config(TelemetryConfig(enabled=True), env=_ENV)
    assert live is not None
    live.accept(_manifest())
    live.close()

    probe = _probe(lambda request: _response(set()))
    assert probe.destination_id is not None
    ledger = TelemetryCheckpointLedger(
        tmp_path / "ledger.sqlite3", destination_id=probe.destination_id
    )
    assert ledger.recorded(frozenset({_TRACE_ID})) == frozenset({_TRACE_ID})
