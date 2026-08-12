"""The telemetry env source is carried, not re-derived.

The failure these pin is the one that made the whole tier inert on a real
host: Grove derived its own OTLP endpoint and its own exporter switches, and
they disagreed in every dimension with the pipeline the operator had already
proved works — traces off where the operator had them on, metrics and logs on
where the operator had them off, and an endpoint pointing straight at the
backend where the operator's own traces went through a collector.

Nothing reported a problem, because every individual value was plausible.
"""

from __future__ import annotations

from pathlib import Path

from grove.core.config import TelemetryConfig


def _source(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "telemetry.env"
    path.write_text(body, encoding="utf-8")
    return path


def _base(tmp_path: Path, extra: str = "") -> TelemetryConfig:
    body = (
        "LANGFUSE_HOST=https://lf.example\n"
        "LANGFUSE_PUBLIC_KEY=pk-test\n"
        "LANGFUSE_SECRET_KEY=sk-test\n"
    ) + extra
    return TelemetryConfig(enabled=True, env_file=str(_source(tmp_path, body)))


def test_an_operator_named_endpoint_beats_the_derived_one(tmp_path: Path) -> None:
    """A derived endpoint is Grove's guess from credentials; a named one is
    where the operator's traces demonstrably arrive. Grove cannot infer a
    collector sitting in front of the backend, so it must not overrule one."""
    cfg = _base(tmp_path, "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:4318\n")

    derived = cfg.derive_env({}, repo_root=tmp_path)

    assert derived["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] == "http://127.0.0.1:4318"


def test_exporter_switches_ride_through_verbatim(tmp_path: Path) -> None:
    """`none` is a real, deliberate value — a deployment that routes traces
    through a collector and deliberately silences metrics and logs must keep
    that shape, not have it quietly re-enabled."""
    cfg = _base(
        tmp_path,
        "OTEL_TRACES_EXPORTER=otlp\nOTEL_METRICS_EXPORTER=none\nOTEL_LOGS_EXPORTER=none\n",
    )

    derived = cfg.derive_env({}, repo_root=tmp_path)

    assert derived["OTEL_TRACES_EXPORTER"] == "otlp"
    assert derived["OTEL_METRICS_EXPORTER"] == "none"
    assert derived["OTEL_LOGS_EXPORTER"] == "none"


def test_grove_still_derives_what_the_source_did_not_say(tmp_path: Path) -> None:
    """Passthrough fills gaps rather than replacing the derivation: a source
    holding only credentials still gets a usable endpoint, so the simple
    deployment keeps working with no extra keys."""
    cfg = _base(tmp_path)

    derived = cfg.derive_env({}, repo_root=tmp_path)

    assert derived["OTEL_EXPORTER_OTLP_ENDPOINT"] == "https://lf.example/api/public/otel"
    assert "Authorization=Basic " in derived["OTEL_EXPORTER_OTLP_HEADERS"]


def test_a_non_telemetry_secret_in_the_source_never_reaches_the_agent(tmp_path: Path) -> None:
    """An injected value lands in the pane's scrollback and in `ps` for the
    same uid, so sharing a file with telemetry is not consent to be handed to
    every agent Grove launches."""
    cfg = _base(tmp_path, "DATABASE_PASSWORD=hunter2\nAWS_SECRET_ACCESS_KEY=nope\n")

    derived = cfg.derive_env({}, repo_root=tmp_path)

    assert "DATABASE_PASSWORD" not in derived
    assert "AWS_SECRET_ACCESS_KEY" not in derived
    assert "hunter2" not in "".join(derived.values())


def test_disabled_carries_nothing_through(tmp_path: Path) -> None:
    """Disabled must stay indistinguishable from absent — passthrough is not a
    back door around the switch."""
    cfg = TelemetryConfig(
        enabled=False, env_file=str(_source(tmp_path, "OTEL_TRACES_EXPORTER=otlp\n"))
    )

    assert cfg.derive_env({}, repo_root=tmp_path) == {}
