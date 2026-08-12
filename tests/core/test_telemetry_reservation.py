"""Grove owns the telemetry environment of the agents it launches.

One tap per workspace. A Grove workspace exports through Grove, so the OTel
exporter vocabulary is reserved at the launch boundary: Grove sets those
variables or nobody does, and a destination configured on the agent — in
``agents[].env``, or leaked in from the environment Grove itself was started
with — is replaced rather than honoured.

The measured failure this closes is two producers describing one session. A
harness whose own trace exporter was switched on beside Grove's replay emitted a
second, inverted trace tree whose observations carried no readable input or
output at all, because its content rides span events and a traces-only backend
maps none of them. Nothing errored: both streams arrived, both looked healthy,
and every turn appeared twice.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from loguru import logger

from grove.core.config import GroveConfig, TelemetryConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from tests.core.test_launch_backend import FakeLaunchBackend

_CREDENTIALS = ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")


@pytest.fixture(autouse=True)
def _no_ambient_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A parent Grove workspace exports the very variables under test.

    The reservation reads the environment Grove is running under, so a suite run
    from inside an instrumented workspace would otherwise assert against its
    parent's endpoint rather than the one the test wrote.
    """
    for name in (*TelemetryConfig().reserved_env, "OTEL_RESOURCE_ATTRIBUTES", *_CREDENTIALS):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def warnings_logged() -> Iterator[list[str]]:
    """Collect loguru WARNING messages (loguru does not reach pytest's caplog)."""
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    yield messages
    logger.remove(sink_id)


# ─── the reservation itself: pure, no environment, no launch ────────────────


def _enabled(**kwargs: object) -> TelemetryConfig:
    return TelemetryConfig(enabled=True, **kwargs)  # type: ignore[arg-type]


def test_a_disabled_section_reserves_nothing() -> None:
    """Grove exports nothing on this agent's behalf, so taking its variables
    away would leave it unable to export at all — ownership that delivers
    silence. Same for a runtime left out of `passthrough_kinds`."""
    claimed = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://elsewhere.example"}

    off = TelemetryConfig().reserve("claude_code", grove={}, claimed=claimed)
    unlisted = _enabled(passthrough_kinds=("codex",)).reserve(
        "claude_code", grove={}, claimed=claimed
    )

    for reservation in (off, unlisted):
        assert reservation.unset == ()
        assert reservation.displaced == ()
        assert reservation.apply(claimed) == claimed


def test_an_agent_configured_destination_is_removed_and_reported() -> None:
    """The rule that changes: `agents[].env` is the most specific layer and wins
    everywhere else in a launch, and for a reserved name it does not — the value
    being reserved is precisely the one an agent would set for itself."""
    cfg = _enabled()
    grove = {"OTEL_EXPORTER_OTLP_ENDPOINT": "https://lf.example/api/public/otel"}
    claimed = {
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://someone-elses-collector.example",
        "OTEL_TRACES_EXPORTER": "otlp",
    }

    reservation = cfg.reserve("claude_code", grove=grove, claimed=claimed)
    applied = reservation.apply({**claimed, "PATH": "/usr/bin"})

    assert applied["OTEL_EXPORTER_OTLP_ENDPOINT"] == grove["OTEL_EXPORTER_OTLP_ENDPOINT"]
    assert "OTEL_TRACES_EXPORTER" not in applied
    assert applied["PATH"] == "/usr/bin", "the reservation touches nothing else"
    assert set(reservation.displaced) == set(claimed)


def test_a_value_that_already_agrees_is_not_reported_as_displaced() -> None:
    """The loudness is only worth having if it names a real disagreement — an
    operator exporting the same endpoint Grove derives has changed nothing and
    must not be warned at every launch, forever."""
    grove = {"OTEL_EXPORTER_OTLP_ENDPOINT": "https://lf.example/api/public/otel"}

    reservation = _enabled().reserve("claude_code", grove=grove, claimed=dict(grove))

    assert reservation.displaced == ()


def test_grove_content_ownership_forces_the_agents_trace_exporter_off() -> None:
    """`content_owner` and the reservation are ONE policy: naming Grove the
    content owner says Grove's replay IS that runtime's trace, so the harness's
    own trace exporter is off even when Grove's own telemetry source asked for
    it — that source is the remaining way two trees could still be configured."""
    grove = {
        "OTEL_EXPORTER_OTLP_ENDPOINT": "https://lf.example/api/public/otel",
        "OTEL_TRACES_EXPORTER": "otlp",
    }
    cfg = _enabled(content_owner={"claude_code": "grove"})

    owned = cfg.reserve("claude_code", grove=grove, claimed={})
    external = _enabled().reserve("claude_code", grove=grove, claimed={})

    assert owned.env["OTEL_TRACES_EXPORTER"] == "none"
    assert external.env["OTEL_TRACES_EXPORTER"] == "otlp"


def test_identity_is_not_a_destination_and_is_never_reserved() -> None:
    """`OTEL_RESOURCE_ATTRIBUTES` says who the agent is, not where its telemetry
    goes — a prefix match would have swallowed it, and with it the `grove.*`
    stamp that makes an agent's own spans findable as this workspace's."""
    cfg = TelemetryConfig()

    assert "OTEL_RESOURCE_ATTRIBUTES" not in cfg.reserved_env
    assert not any(name.startswith("TRACE") for name in cfg.reserved_env)


def test_reserved_unset_names_exactly_what_grove_left_unset() -> None:
    """The inherited half. `apply` can only correct the environment Grove
    composes; a pane also inherits the tmux server's, so every reserved name
    Grove did not set has to be cleared there too."""
    cfg = _enabled()
    grove = {"OTEL_EXPORTER_OTLP_ENDPOINT": "https://lf.example/api/public/otel"}

    reservation = cfg.reserve("claude_code", grove=grove, claimed={})
    unset = cfg.reserved_unset("claude_code", reservation.apply({}))

    assert set(unset) == set(cfg.reserved_env) - {"OTEL_EXPORTER_OTLP_ENDPOINT"}
    assert cfg.reserved_unset("mewbo", {}) == (), "a kind Grove does not export for"


# ─── the launch boundary: the real composition path ─────────────────────────


def _cfg(tmp_path: Path, **telemetry: object) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "telemetry": {"enabled": True, **telemetry},
            "agents": [
                {
                    "name": "claude",
                    "command": "claude",
                    "kind": "claude_code",
                    # An operator who wired their agent's telemetry by hand,
                    # which is exactly what the reservation takes over.
                    "env": {
                        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://my-own-collector.example",
                        "OTEL_TRACES_EXPORTER": "otlp",
                        "FOO": "bar",
                    },
                }
            ],
        }
    )


def _launch(
    tmp_repo: Path, tmp_path: Path, cfg: GroveConfig, slot: str
) -> tuple[dict[str, str], tuple[str, ...]]:
    backend = FakeLaunchBackend()
    WorkspaceManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=JsonWorkspaceStore(path=tmp_path / f"{slot}.json"),
        launch_backend=backend,
    ).create(CreateWorkspaceRequest(agent_name="claude", title=f"t-{slot}"))
    spec = backend.specs[0]
    return dict(spec.env), spec.env_unset


@pytest.fixture
def _credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_HOST", "http://lf.example")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")


@pytest.mark.usefixtures("_credentials", "fake_tmux")
def test_a_launch_carries_groves_destination_and_clears_every_other(
    tmp_repo: Path, tmp_path: Path
) -> None:
    """The whole position in one launch: Grove's endpoint reaches the agent, the
    agent's own does not, and the reserved names Grove leaves unset are cleared
    out of the environment the pane would otherwise inherit — so an exporter
    cannot be switched on behind Grove's back by either road."""
    env, env_unset = _launch(tmp_repo, tmp_path, _cfg(tmp_path), "one")

    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://lf.example/api/public/otel"
    assert "OTEL_TRACES_EXPORTER" not in env
    assert "OTEL_TRACES_EXPORTER" in env_unset
    assert env["FOO"] == "bar", "the reservation is scoped to telemetry, not to agent env"


@pytest.mark.usefixtures("_credentials", "fake_tmux")
def test_grove_owned_content_launches_no_second_exporter(tmp_repo: Path, tmp_path: Path) -> None:
    """Where Grove owns the content, the harness's native exporter is not
    switched on beside the replay and its trace exporter is pinned off. The
    stated cost: Claude Code's beta span is the only carrier of `ttft_ms`, so
    that measurement is unavailable for a runtime named here until Grove ingests
    the native stream rather than suppressing it."""
    cfg = _cfg(tmp_path, content_owner={"claude_code": "grove"})

    env, _ = _launch(tmp_repo, tmp_path, cfg, "owned")

    assert env["OTEL_TRACES_EXPORTER"] == "none"
    assert "CLAUDE_CODE_ENABLE_TELEMETRY" not in env
    assert "OTEL_METRICS_EXPORTER" not in env
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://lf.example/api/public/otel"


@pytest.mark.usefixtures("_credentials", "fake_tmux")
def test_external_content_ownership_keeps_the_runtimes_own_switch(
    tmp_repo: Path, tmp_path: Path
) -> None:
    """The default owner is unchanged by the reservation: a harness whose own
    emitter carries the content still gets its exporter turned on, because the
    reservation is about the DESTINATION, not about silencing the agent."""
    env, _ = _launch(tmp_repo, tmp_path, _cfg(tmp_path), "external")

    assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
    assert env["OTEL_METRICS_EXPORTER"] == "otlp"


@pytest.mark.usefixtures("_credentials", "fake_tmux")
def test_the_takeover_is_announced_by_name_and_never_by_value(
    tmp_repo: Path, tmp_path: Path, warnings_logged: list[str]
) -> None:
    """Loud, never silent. A user who configured their agent's telemetry
    themselves is told Grove is now the tap — and told it in variable NAMES,
    because a telemetry value can be a credential."""
    _launch(tmp_repo, tmp_path, _cfg(tmp_path), "loud")

    said = "\n".join(warnings_logged)
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" in said
    assert "telemetry.env_file" in said
    assert "my-own-collector.example" not in said


@pytest.mark.usefixtures("fake_tmux")
def test_a_workspace_grove_does_not_export_for_keeps_its_own_wiring(
    tmp_repo: Path, tmp_path: Path
) -> None:
    """With telemetry off Grove is not the tap for anything, so an agent that
    was exporting on its own before Grove existed keeps doing so — the
    reservation must not read as a blanket ban on agent telemetry."""
    cfg = _cfg(tmp_path)
    disabled = cfg.model_copy(update={"telemetry": TelemetryConfig(enabled=False)})

    env, env_unset = _launch(tmp_repo, tmp_path, disabled, "off")

    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://my-own-collector.example"
    assert env["OTEL_TRACES_EXPORTER"] == "otlp"
    assert env_unset == ()
