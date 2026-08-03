"""``HostPreflight`` — the shared engine seam behind `grove doctor` and the
create-path arm-4 probe.

Every subprocess/`which` call is stubbed; no test needs docker, tmux, git, or
network to actually be present on the runner.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from grove.core import preflight as pf
from grove.core.config import AgentSpec, ContainerConfig, ContainerTmuxConfig, GroveConfig
from grove.core.container_tmux import TmuxPayload
from grove.core.devcontainer import DevcontainerProbe
from grove.core.preflight import HostPreflight

#: Captured at import time, BEFORE the suite-wide offline-container fixture
#: stubs the subprocess boundary out — the one test that means to exercise the
#: real boundary restores this.
_REAL_RUN = HostPreflight.__dict__["_run"]


def _cfg(*, container_enabled: bool = False, agents: list[AgentSpec] | None = None) -> GroveConfig:
    kwargs: dict[str, object] = {"container": ContainerConfig(enabled=container_enabled)}
    if agents is not None:
        kwargs["agents"] = agents
    return GroveConfig(**kwargs)


def _completed(
    *, returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class _FakeDevcontainerCli:
    """Duck-typed stand-in — avoids DevcontainerCli's own subprocess call."""

    def __init__(self, probe: DevcontainerProbe) -> None:
        self._probe = probe

    def probe(self) -> DevcontainerProbe:
        return self._probe


def _which_map(monkeypatch: pytest.MonkeyPatch, present: set[str]) -> None:
    monkeypatch.setattr(
        pf.shutil, "which", lambda binary: f"/usr/bin/{binary}" if binary in present else None
    )


def test_docker_daemon_ok_parses_server_version(monkeypatch: pytest.MonkeyPatch) -> None:
    _which_map(monkeypatch, {"docker", "git", "tmux"})
    monkeypatch.setattr(
        HostPreflight,
        "_run",
        staticmethod(
            lambda argv: (
                _completed(stdout='{"ServerVersion": "27.1.0"}')
                if argv[:2] == ["docker", "info"]
                else _completed(stdout="docker compose v2")
            )
        ),
    )
    result = next(c for c in HostPreflight(_cfg()).all_checks() if c.name == "docker daemon")
    assert result.ok is True
    assert "27.1.0" in result.detail
    assert result.required_for == "container"


def test_docker_daemon_missing_binary_carries_a_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    _which_map(monkeypatch, set())
    result = next(c for c in HostPreflight(_cfg()).all_checks() if c.name == "docker daemon")
    assert result.ok is False
    assert "not found" in result.detail
    assert "docker.com" in result.hint


def test_docker_daemon_present_but_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    _which_map(monkeypatch, {"docker"})
    monkeypatch.setattr(
        HostPreflight,
        "_run",
        staticmethod(
            lambda argv: _completed(returncode=1, stderr="Cannot connect to the Docker daemon")
        ),
    )
    result = next(c for c in HostPreflight(_cfg()).all_checks() if c.name == "docker daemon")
    assert result.ok is False
    assert "Cannot connect" in result.detail
    assert result.hint


def test_docker_daemon_timeout_reports_ok_false_not_a_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _which_map(monkeypatch, {"docker"})
    monkeypatch.setattr(HostPreflight, "_run", staticmethod(lambda argv: None))
    result = next(c for c in HostPreflight(_cfg()).all_checks() if c.name == "docker daemon")
    assert result.ok is False


def test_compose_v2_absent_is_container_scoped_note(monkeypatch: pytest.MonkeyPatch) -> None:
    _which_map(monkeypatch, {"docker"})
    monkeypatch.setattr(
        HostPreflight, "_run", staticmethod(lambda argv: _completed(returncode=1, stderr=""))
    )
    result = next(c for c in HostPreflight(_cfg()).all_checks() if c.name == "compose v2")
    assert result.ok is False
    assert result.required_for == "container"
    assert "devcontainer.json" in result.hint or "compose" in result.hint.lower()


def test_devcontainer_cli_check_reuses_probe_not_reimplemented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _which_map(monkeypatch, set())
    fake = _FakeDevcontainerCli(DevcontainerProbe(binary="devcontainer", available=False))
    preflight = HostPreflight(_cfg(), devcontainer_cli=fake)  # type: ignore[arg-type]
    result = next(c for c in preflight.all_checks() if c.name == "@devcontainers/cli")
    assert result.ok is False
    assert "npm install" in result.hint


def test_devcontainer_cli_check_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeDevcontainerCli(
        DevcontainerProbe(binary="devcontainer", available=True, version="0.71.0")
    )
    preflight = HostPreflight(_cfg(), devcontainer_cli=fake)  # type: ignore[arg-type]
    result = next(c for c in preflight.all_checks() if c.name == "@devcontainers/cli")
    assert result.ok is True
    assert result.detail == "0.71.0"


def test_git_and_tmux_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    _which_map(monkeypatch, {"git", "tmux"})
    monkeypatch.setattr(
        HostPreflight, "_run", staticmethod(lambda argv: _completed(stdout="git version 2.43.0"))
    )
    checks = {c.name: c for c in HostPreflight(_cfg()).all_checks()}
    assert checks["git"].ok is True
    assert checks["git"].required_for == "all"
    assert checks["tmux"].required_for == "host"


def test_git_missing_is_required_for_all(monkeypatch: pytest.MonkeyPatch) -> None:
    _which_map(monkeypatch, set())
    result = next(c for c in HostPreflight(_cfg()).all_checks() if c.name == "git")
    assert result.ok is False
    assert result.required_for == "all"


def test_tmux_missing_is_host_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    _which_map(monkeypatch, set())
    result = next(c for c in HostPreflight(_cfg()).all_checks() if c.name == "tmux")
    assert result.ok is False
    assert result.required_for == "host"


def test_agent_cli_checks_one_per_distinct_host_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    _which_map(monkeypatch, {"claude"})
    agents = [
        AgentSpec(name="claude", command="claude", kind="claude_code"),
        AgentSpec(name="claude-alt", command="claude --model x", kind="claude_code"),
        AgentSpec(name="codex", command="codex", kind="codex"),
        AgentSpec(name="shell", command="bash", kind="generic"),
        AgentSpec(name="mewbo", command="ignored", kind="mewbo"),
    ]
    results = HostPreflight(_cfg(agents=agents)).all_checks()
    agent_checks = {c.name: c for c in results if c.name.startswith("agent CLI")}
    # `claude` and `claude-alt` share the same binary → one check, not two.
    assert len(agent_checks) == 2
    assert agent_checks["agent CLI (claude)"].ok is True
    assert agent_checks["agent CLI (codex)"].ok is False
    assert "generic" not in "".join(agent_checks)
    assert all(c.required_for == "host" for c in agent_checks.values())


def test_container_ready_subset(monkeypatch: pytest.MonkeyPatch) -> None:
    _which_map(monkeypatch, {"docker", "git", "tmux"})
    monkeypatch.setattr(HostPreflight, "_run", staticmethod(lambda argv: _completed(returncode=1)))
    fake = _FakeDevcontainerCli(DevcontainerProbe(binary="devcontainer", available=False))
    preflight = HostPreflight(_cfg(), devcontainer_cli=fake)  # type: ignore[arg-type]
    subset = {c.name for c in preflight.container_ready()}
    assert subset == {"docker daemon", "compose v2", "@devcontainers/cli", "git"}


def test_host_ready_subset(monkeypatch: pytest.MonkeyPatch) -> None:
    # GroveConfig() defaults to the built-in agent roster (claude + codex are
    # `claude_code`/`codex` kind, so they each get their own host-scoped check).
    _which_map(monkeypatch, {"git", "tmux"})
    monkeypatch.setattr(HostPreflight, "_run", staticmethod(lambda argv: _completed()))
    preflight = HostPreflight(_cfg())
    subset = {c.name for c in preflight.host_ready()}
    assert subset == {"git", "tmux", "agent CLI (claude)", "agent CLI (codex)"}


def test_default_runtime_checks_follows_container_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _which_map(monkeypatch, {"docker", "git", "tmux"})
    monkeypatch.setattr(HostPreflight, "_run", staticmethod(lambda argv: _completed()))
    fake = _FakeDevcontainerCli(
        DevcontainerProbe(binary="devcontainer", available=True, version="0.1")
    )

    host_default = HostPreflight(_cfg(container_enabled=False), devcontainer_cli=fake)  # type: ignore[arg-type]
    assert {c.name for c in host_default.default_runtime_checks()} == {
        "git",
        "tmux",
        "agent CLI (claude)",
        "agent CLI (codex)",
    }

    container_default = HostPreflight(_cfg(container_enabled=True), devcontainer_cli=fake)  # type: ignore[arg-type]
    assert {c.name for c in container_default.default_runtime_checks()} == {
        "docker daemon",
        "compose v2",
        "@devcontainers/cli",
        "git",
    }


def test_all_checks_never_raises_on_a_stubbed_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docs promise: bounded side effects, pure aggregation above — a boundary
    failure degrades a single check, it never propagates."""
    _which_map(monkeypatch, {"docker", "git", "tmux"})
    # The suite-wide offline fixture stubs `_run` out entirely, which would make
    # this test pass without ever reaching `subprocess.run` — the exact thing it
    # exists to exercise. Restore the real one first.
    monkeypatch.setattr(HostPreflight, "_run", _REAL_RUN)

    def _real_run_but_erroring(*args: object, **kwargs: object) -> None:
        raise OSError("boom")

    monkeypatch.setattr(pf.subprocess, "run", _real_run_but_erroring)
    results = HostPreflight(_cfg()).all_checks()
    assert all(isinstance(c, pf.CheckResult) for c in results)
    assert any(not c.ok for c in results)


# ─── the optional scope: reported, never required ───────────────────────────


def _tmux_check(cfg: GroveConfig, monkeypatch: pytest.MonkeyPatch) -> pf.CheckResult:
    _which_map(monkeypatch, {"docker", "git", "tmux"})
    monkeypatch.setattr(HostPreflight, "_run", staticmethod(lambda argv: _completed()))
    return next(c for c in HostPreflight(cfg).all_checks() if c.name == "in-container tmux")


def test_a_missing_tmux_payload_can_never_fall_a_create_back_to_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reason ``optional`` exists at all.

    ``RuntimeResolver`` turns the FIRST failing ``container``-scoped check into
    a fall back to the host runtime. Filing an unbuilt convenience bundle under
    that scope would silently strip a workspace of its isolation — so this
    asserts the scope AND the subsets it must stay out of, because the scope
    string on its own is not what has the effect.
    """
    check = _tmux_check(_cfg(container_enabled=True), monkeypatch)

    assert check.ok is False
    assert check.required_for == "optional"
    preflight = HostPreflight(_cfg(container_enabled=True))
    assert "in-container tmux" not in {c.name for c in preflight.container_ready()}
    assert "in-container tmux" not in {c.name for c in preflight.host_ready()}
    assert "in-container tmux" not in {c.name for c in preflight.default_runtime_checks()}


def test_a_built_payload_reports_where_it_is(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = tmp_path / "bundle"
    # The real layout: a shared terminfo tree beside a per-architecture binary.
    (bundle / "terminfo").mkdir(parents=True)
    binary = bundle / "bin" / TmuxPayload.host_arch() / "tmux"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"\x7fELF")
    cfg = GroveConfig(container=ContainerConfig(tmux=ContainerTmuxConfig(payload=str(bundle))))

    check = _tmux_check(cfg, monkeypatch)

    assert check.ok is True
    assert str(bundle) in check.detail
    assert check.hint == ""


def test_an_operator_payload_that_is_missing_says_so_rather_than_promising_a_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Grove never builds over a directory the operator supplied, so the hint
    must not claim that it will."""
    cfg = GroveConfig(
        container=ContainerConfig(tmux=ContainerTmuxConfig(payload=str(tmp_path / "gone")))
    )

    check = _tmux_check(cfg, monkeypatch)

    assert check.ok is False
    assert "container.tmux.payload" in check.hint


def test_disabling_the_feature_is_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = GroveConfig(container=ContainerConfig(tmux=ContainerTmuxConfig(enabled=False)))

    check = _tmux_check(cfg, monkeypatch)

    assert check.ok is True
    assert "disabled" in check.detail
