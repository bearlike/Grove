"""The receiver config knob: defaults, cascade, and independence from export.

`TelemetryConfig.receiver` is a mounting decision for the daemon's OTLP/HTTP
ingest endpoint, kept deliberately separate from `TelemetryConfig.enabled`
(which gates Grove's own *export*): a harness can be pointed at the receiver
with export off, and export can run with the receiver never mounted.
"""

from __future__ import annotations

from pathlib import Path

from grove.core.config import GroveConfig, TelemetryConfig, TelemetryReceiverConfig, load_config
from grove.core.telemetry.receiver import DEFAULT_QUEUE_CAPACITY, DEFAULT_WORKERS


def test_receiver_defaults_are_off_and_byte_identical_to_no_knob_at_all() -> None:
    cfg = TelemetryConfig()
    assert cfg.receiver.enabled is False
    assert cfg.receiver.path == "/otlp"


def test_receiver_queue_and_worker_defaults_match_otlp_ingests_own() -> None:
    """`OtlpIngest`'s constructor defaults are the ones a config-less mount
    already runs with — the knob must not silently change that behaviour."""
    cfg = TelemetryReceiverConfig()
    assert cfg.queue_capacity == DEFAULT_QUEUE_CAPACITY
    assert cfg.workers == DEFAULT_WORKERS


def test_receiver_is_independent_of_telemetry_export() -> None:
    """Enabling the receiver must not flip `enabled` (Grove's own export), and
    vice versa — the two answer different questions."""
    receiving_only = TelemetryConfig(receiver=TelemetryReceiverConfig(enabled=True))
    assert receiving_only.enabled is False
    assert receiving_only.receiver.enabled is True

    exporting_only = TelemetryConfig(enabled=True)
    assert exporting_only.receiver.enabled is False


def test_receiver_cascades_like_every_other_nested_field(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    cfg = load_config(
        tmp_repo,
        env={},
        cli_overrides={
            "telemetry": {"receiver": {"enabled": True, "path": "/ingest", "queue_capacity": 512}}
        },
    )
    assert cfg.telemetry.receiver.enabled is True
    assert cfg.telemetry.receiver.path == "/ingest"
    assert cfg.telemetry.receiver.queue_capacity == 512
    # A field left unmentioned in the overlay keeps its default.
    assert cfg.telemetry.receiver.workers == DEFAULT_WORKERS


def test_receiver_appears_in_the_published_schema() -> None:
    sections = GroveConfig.model_fields.keys()
    assert "telemetry" in sections
    assert "receiver" in TelemetryConfig.model_fields
