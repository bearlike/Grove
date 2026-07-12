"""The pluggable LaunchBackend seam (#145).

Two halves:

* A ``FakeLaunchBackend`` injected into ``WorkspaceManager`` captures the exact
  ``LaunchSpec`` the manager assembles and proves that when a non-tmux backend is
  used, NO tmux session/layout work happens — the container/headless swap point.
* ``TmuxLaunchBackend`` (the default) unpacks a spec back into the ``tmux``
  side-effect calls byte-for-byte, so behavior is identical to the pre-seam
  inline calls (the rest of the manager/daemon suite exercises this default).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grove.core import tmux as tmux_mod
from grove.core.agents.registry import get_adapter
from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.launch import LaunchSpec, TmuxLaunchBackend
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from tests.conftest import FakeTmux


class FakeLaunchBackend:
    """Captures every LaunchSpec instead of touching tmux (the DI seam)."""

    def __init__(self) -> None:
        self.specs: list[LaunchSpec] = []

    def launch(self, spec: LaunchSpec) -> None:
        self.specs.append(spec)


def _cfg(tmp_path: Path) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "agents": [
                {
                    "name": "claude",
                    "command": "claude",
                    "kind": "claude_code",
                    "env": {"FOO": "bar"},
                    "env_unset": ["CLAUDE_CONFIG_DIR"],
                }
            ],
        }
    )


def test_create_routes_the_assembled_command_through_the_backend(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The manager hands a fully-assembled LaunchSpec to the injected backend and
    performs NO tmux session work of its own — the container/headless swap point."""
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    backend = FakeLaunchBackend()
    mgr = WorkspaceManager(
        repo_root=tmp_repo, cfg=_cfg(tmp_path), store=store, launch_backend=backend
    )

    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="seam"))

    assert len(backend.specs) == 1
    spec = backend.specs[0]
    assert spec.session_name == state.tmux_session
    assert spec.cwd == Path(state.agent_cwd)
    assert spec.worktree == Path(state.agent_cwd)
    assert spec.command == "claude"
    # The composed decoration carries the deterministic-correlation flag with the
    # minted session id — assembled by _compose_launch + the adapter, verbatim.
    assert "--session-id" in spec.decoration
    assert state.agent_session_id in spec.decoration
    # The hermetic launch env (#82) rides the spec, not an AgentSpec.
    assert spec.env == {"FOO": "bar"}
    assert spec.env_unset == ("CLAUDE_CONFIG_DIR",)

    # No tmux was invoked: the backend replaced create_session + layout entirely.
    assert fake_tmux.sessions == set()
    assert fake_tmux.layouts == []


def test_tmux_backend_unpacks_the_spec_into_the_tmux_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The default backend delegates to the tmux module byte-identically — one
    create_session rooted at cwd, one layout with the same command/decoration/env."""
    spec = LaunchSpec(
        session_name="test-sess",
        cwd=tmp_path / "wt",
        command="claude",
        decoration=("--session-id", "abc"),
        env={"FOO": "bar"},
        env_unset=("CLAUDE_CONFIG_DIR",),
        cfg=GroveConfig(),
        worktree=tmp_path / "wt",
    )
    created: list[tuple[str, Path, int]] = []
    laid_out: list[dict[str, object]] = []

    def _create(name: str, cwd: Path, *, history_limit: int = 50_000) -> None:
        created.append((name, cwd, history_limit))

    def _layout(session_name: str, **kwargs: object) -> None:
        laid_out.append({"session_name": session_name, **kwargs})

    monkeypatch.setattr(tmux_mod, "create_session", _create)
    monkeypatch.setattr(tmux_mod, "build_workspace_layout", _layout)

    TmuxLaunchBackend().launch(spec)

    assert created == [("test-sess", tmp_path / "wt", GroveConfig().tmux.history_limit)]
    assert laid_out == [
        {
            "session_name": "test-sess",
            "cfg": spec.cfg,
            "worktree": tmp_path / "wt",
            "command": "claude",
            "decoration": ("--session-id", "abc"),
            "env": {"FOO": "bar"},
            "env_unset": ("CLAUDE_CONFIG_DIR",),
        }
    ]


def _telemetry_cfg(tmp_path: Path) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "telemetry": {"enabled": True, "host_env": "LANGFUSE_BASE_URL"},
            "agents": [
                {"name": "claude", "command": "claude", "kind": "claude_code"},
                {"name": "codex", "command": "codex", "kind": "codex"},
            ],
        }
    )


def test_telemetry_passthrough_injects_native_switch_only_with_a_resolved_endpoint(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With telemetry enabled AND creds resolved, the adapter's OWN native switch
    (#170 passthrough) rides the launch env — Claude Code flips its exporter on,
    Codex gets only the OTLP endpoint (it configures OTel via config.toml), and
    when the creds don't resolve nothing is enabled (never export to nowhere)."""
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://lf.example")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

    def _launch_env(agent_name: str, slot: str) -> dict[str, str]:
        store = JsonWorkspaceStore(path=tmp_path / f"{slot}.json")
        backend = FakeLaunchBackend()
        mgr = WorkspaceManager(
            repo_root=tmp_repo,
            cfg=_telemetry_cfg(tmp_path),
            store=store,
            launch_backend=backend,
        )
        mgr.create(CreateWorkspaceRequest(agent_name=agent_name, title=f"t-{slot}"))
        return dict(backend.specs[0].env)

    claude_env = _launch_env("claude", "c1")
    assert claude_env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://lf.example/api/public/otel"
    assert claude_env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
    assert claude_env["OTEL_METRICS_EXPORTER"] == "otlp"
    assert claude_env["OTEL_LOGS_EXPORTER"] == "otlp"

    codex_env = _launch_env("codex", "cx1")
    assert codex_env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://lf.example/api/public/otel"
    assert "CLAUDE_CODE_ENABLE_TELEMETRY" not in codex_env

    # Creds unresolved → derive_env empty → no endpoint → no native switch injected.
    for name in ("LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    dark = _launch_env("claude", "dark")
    assert "CLAUDE_CODE_ENABLE_TELEMETRY" not in dark
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in dark


def test_adapter_telemetry_env_is_provider_specific() -> None:
    """The native-telemetry switch is the adapter's own (provider boundary):
    only Claude Code has an env-driven one; the rest are no-ops."""
    assert get_adapter("claude_code").telemetry_env() == {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_LOGS_EXPORTER": "otlp",
    }
    assert get_adapter("codex").telemetry_env() == {}
    assert get_adapter("generic").telemetry_env() == {}
    assert get_adapter("mewbo").telemetry_env() == {}
