"""The pluggable LaunchBackend seam.

Two halves:

* A ``FakeLaunchBackend`` injected into ``WorkspaceManager`` captures the exact
  ``LaunchSpec`` the manager assembles and proves that when a non-tmux backend is
  used, NO tmux session/layout work happens — the container/headless swap point.
* ``TmuxLaunchBackend`` (the default) unpacks a spec back into the ``tmux``
  side-effect calls byte-for-byte, so behavior is identical to the pre-seam
  inline calls (the rest of the manager/daemon suite exercises this default).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import ClassVar

import pytest

from grove import __version__
from grove.core import paths
from grove.core import tmux as tmux_mod
from grove.core.agents.base import AgentVersionProbe
from grove.core.agents.brief import AgentBrief
from grove.core.agents.registry import get_adapter
from grove.core.config import GroveConfig
from grove.core.container_policy import CONTAINER_CONTROL_ROOT, AgentSharePlan
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.launch import (
    AgentExit,
    DevcontainerLaunchBackend,
    HeadlessLaunchBackend,
    HostNamespaceBackend,
    LaunchSpec,
    TmuxLaunchBackend,
)
from grove.core.manager import WorkspaceManager
from grove.core.phase import PhaseFile
from grove.core.store import JsonWorkspaceStore
from tests.conftest import FakeTmux


class FakeLaunchBackend(HostNamespaceBackend):
    """Captures every LaunchSpec instead of touching tmux (the DI seam).

    Conforms to the whole `LaunchBackend` Protocol, not just the members this
    file happens to exercise: a generic capture-only stand-in, not
    simulating headless or container semantics, so it declares `provides_pane`
    True (the tmux-like common case) and inherits the host-namespace bridge —
    identity `control_path`, `for_launch` context — from the same base the two
    real host backends use, so the fake cannot drift from them. Without
    `transcript_context`, `_launch` raises `AttributeError` on every `create`
    this fake drives."""

    provides_pane: ClassVar[bool] = True

    def __init__(self) -> None:
        self.specs: list[LaunchSpec] = []

    def launch(self, spec: LaunchSpec) -> None:
        self.specs.append(spec)


@pytest.fixture(autouse=True)
def _isolate_resource_attributes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A parent Grove workspace must not stamp its identity into test launches."""
    monkeypatch.delenv("OTEL_RESOURCE_ATTRIBUTES", raising=False)


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
    # The hermetic launch env rides the spec, not an AgentSpec — plus the
    # per-agent phase path Grove composes for every launch.
    # `OTEL_RESOURCE_ATTRIBUTES` is asserted separately below: its VALUE embeds
    # the tmp worktree path and the minted session id, so pinning it literally
    # here would assert the fixture rather than the contract.
    assert {k: v for k, v in spec.env.items() if k != "OTEL_RESOURCE_ATTRIBUTES"} == {
        "FOO": "bar",
        PhaseFile.PATH_ENV: str(
            PhaseFile.path_for(state.worktree_path, PhaseFile.key_for(state.id))
        ),
        # …and the first-turn brief, published the same way and for the same
        # reason: the hook process inherits the agent's env, and the settings
        # file it lives in is host-global so it cannot carry a per-workspace
        # answer.
        AgentBrief.PATH_ENV: str(paths.agent_brief_path()),
    }
    # Identity stamping rides every launch, telemetry credentials or not — and
    # the correlation attribute is what later joins Grove's own spans to the
    # agent's independently-traced ones, so both are part of this contract.
    attrs = spec.env["OTEL_RESOURCE_ATTRIBUTES"]
    assert f"grove.workspace.id={state.id}" in attrs
    assert f"langfuse.session.id={state.agent_session_id}" in attrs
    assert spec.env_unset == ("CLAUDE_CONFIG_DIR",)

    # No tmux was invoked: the backend replaced create_session + layout entirely.
    assert fake_tmux.sessions == set()
    assert fake_tmux.layouts == []


def test_every_launch_stamps_who_ran_it_and_which_builds(
    monkeypatch: pytest.MonkeyPatch, tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The identity half of the resource: a reader of an exported trace can tell
    it came from Grove, which Grove, and which build of the agent produced it.

    The agent's version is the only one of the three that no code here can
    know — it is read from the tool itself at the launch boundary, ONCE per
    binary per process, and a second create must not pay a second subprocess.
    """
    probed: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        AgentVersionProbe,
        "probe",
        classmethod(lambda cls, argv: probed.append(argv) or "2.1.226 (Claude Code)"),
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    backend = FakeLaunchBackend()
    mgr = WorkspaceManager(
        repo_root=tmp_repo, cfg=_cfg(tmp_path), store=store, launch_backend=backend
    )

    mgr.create(CreateWorkspaceRequest(agent_name="claude", title="first"))
    mgr.create(CreateWorkspaceRequest(agent_name="claude", title="second"))

    assert probed == [("claude", "--version")]
    for spec in backend.specs:
        attrs = spec.env["OTEL_RESOURCE_ATTRIBUTES"]
        assert "grove.agent.version=2.1.226%20%28Claude%20Code%29" in attrs
        assert "grove.orchestrator.name=grove" in attrs
        assert f"grove.orchestrator.version={__version__}" in attrs


def test_a_tool_that_cannot_report_a_version_still_launches(
    monkeypatch: pytest.MonkeyPatch, tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A missing or wedged binary must cost the launch nothing but the one
    attribute — the whole reason the probe is best-effort and bounded."""
    monkeypatch.setattr(AgentVersionProbe, "probe", classmethod(lambda cls, argv: None))
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    backend = FakeLaunchBackend()
    mgr = WorkspaceManager(
        repo_root=tmp_repo, cfg=_cfg(tmp_path), store=store, launch_backend=backend
    )

    mgr.create(CreateWorkspaceRequest(agent_name="claude", title="unanswerable"))

    attrs = backend.specs[0].env["OTEL_RESOURCE_ATTRIBUTES"]
    assert "grove.agent.version" not in attrs
    # …and the rest of the identity is untouched by that absence.
    assert "grove.orchestrator.name=grove" in attrs


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
        kind="claude_code",
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
            # No exit record on this spec, so nothing is appended.
            "exit_suffix": "",
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
    rides the launch env — Claude Code flips its exporter on,
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


# ─── the agent-exit recorder ─────────────────────────────────────────────────


def test_a_real_shell_records_the_agent_commands_exit(tmp_path: Path) -> None:
    """RUNS the composed shell, rather than asserting on its text.

    The producer is the half that has to be true: a status field nothing sets
    is worse than no field. So this executes the exact suffix
    `build_workspace_layout` appends and reads it back through the same class
    that will parse it in production — which is what pins the format and its
    parser together.
    """
    bash = shutil.which("bash")
    assert bash is not None, "bash is required to exercise the recorder"
    record = tmp_path / "spaced dir" / "ws1.exit"
    record.parent.mkdir()

    # Every case is an EXTERNAL command, which is what an agent always is
    # (`claude`, `codex`, a wrapper). The limit worth knowing: anything that
    # terminates the recording shell itself — the `exit` builtin, or a signal
    # delivered to that shell — leaves no record at all. That is the safe
    # direction, since absence already means "no information", never failure.
    for command, expected in (
        ("true", 0),
        ("false", 1),
        ("sh -c 'exit 3'", 3),
        ("sh -c 'kill -TERM $$'", 143),
    ):
        record.unlink(missing_ok=True)
        subprocess.run(
            [bash, "-c", f"{command}{AgentExit.record_suffix(record)}"],
            check=False,
            capture_output=True,
        )
        assert AgentExit.read(record) == AgentExit(code=expected), command


def test_no_record_means_the_agent_has_not_exited(tmp_path: Path) -> None:
    """Absence is "no information", never failure — this is the anti-flake rule.

    A slow-starting agent has written nothing, and that must read exactly like
    a healthy running one. Making this a property of the shape rather than a
    timeout is what keeps `_settle`'s flake fix intact.
    """
    assert AgentExit.read(tmp_path / "never-written.exit") is None
    # A half-written or corrupt record degrades the same way rather than raising
    # on the per-tick read path.
    junk = tmp_path / "junk.exit"
    junk.write_text("", encoding="utf-8")
    assert AgentExit.read(junk) is None
    junk.write_text("not-a-number", encoding="utf-8")
    assert AgentExit.read(junk) is None


def test_a_clean_exit_is_not_a_failure() -> None:
    """Quitting your agent is not an error and must never be reported as one."""
    assert AgentExit(code=0).failed is False
    assert AgentExit(code=1).failed is True
    assert AgentExit(code=127).reason == "agent exited with status 127"


def test_prepare_creates_the_directory_and_drops_a_stale_record(tmp_path: Path) -> None:
    """Both halves, because both are "the recorder can actually record".

    The directory half was found on a real workspace, not in review: the writer
    is a shell redirect, so a missing directory made the pane answer `zsh: no
    such file or directory` while Grove saw a perfectly composed suffix and no
    record. The clear half is what makes `respawn` — the remedy for this very
    failure — not read as dead the instant it relaunches.
    """
    record = tmp_path / "agent-exits" / "ws1.exit"
    assert not record.parent.exists()

    AgentExit.prepare(record)
    assert record.parent.is_dir()

    record.write_text("127\n", encoding="utf-8")
    AgentExit.prepare(record)
    assert AgentExit.read(record) is None
    # Idempotent, and never raises: failing to prepare must not fail a launch.
    AgentExit.prepare(record)


def test_the_recorder_is_appended_after_the_decoration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ordering is the property. `command` and `decoration` are joined inside
    `build_workspace_layout`, so the suffix can only be appended there — a
    caller that tried would record the exit of the wrong thing."""
    record = tmp_path / "ws1.exit"
    spec = LaunchSpec(
        session_name="test-sess",
        cwd=tmp_path,
        command="claude",
        decoration=("--session-id", "abc"),
        env={},
        env_unset=(),
        cfg=GroveConfig(),
        worktree=tmp_path,
        kind="claude_code",
        exit_record=record,
    )
    sent: list[str] = []
    monkeypatch.setattr(tmux_mod, "create_session", lambda *a, **k: None)
    monkeypatch.setattr(
        tmux_mod,
        "build_workspace_layout",
        lambda _s, **kw: sent.append(
            f"{kw['command']} {' '.join(kw['decoration'])}{kw['exit_suffix']}"
        ),
    )

    TmuxLaunchBackend().launch(spec)

    assert sent == [f"claude --session-id abc; echo $? > {record}"]


def test_a_spec_with_no_record_appends_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`exit_record=None` is the off switch, and appends nothing — a headless
    backend has no shell to record with."""
    spec = LaunchSpec(
        session_name="s",
        cwd=tmp_path,
        command="claude",
        decoration=(),
        env={},
        env_unset=(),
        cfg=GroveConfig(),
        worktree=tmp_path,
        kind="claude_code",
    )
    suffixes: list[str] = []
    monkeypatch.setattr(tmux_mod, "create_session", lambda *a, **k: None)
    monkeypatch.setattr(
        tmux_mod, "build_workspace_layout", lambda _s, **kw: suffixes.append(kw["exit_suffix"])
    )

    TmuxLaunchBackend().launch(spec)

    assert suffixes == [""]


# ─── control-file translation across the container boundary ─────────────────


def _container_plan(tmp_path: Path, *, present: tuple[Path, ...]) -> AgentSharePlan:
    on_disk = set(present)
    return AgentSharePlan.plan(
        kind="claude_code",
        share="full",
        home=tmp_path / "home",
        workspace_config_dir=tmp_path / "state" / "ws1",
        control_files=AgentSharePlan.default_control_files(),
        exists=lambda path: path in on_disk,
    )


def test_no_untranslated_host_control_path_can_reach_a_container_launch(
    tmp_path: Path,
) -> None:
    """The invariant, stated over ALL of Grove's control files rather than one flag.

    An untranslated host path (e.g. `--settings`) is one instance of a class of
    bug that also threatens `--channels` and `--mcp-config` — which is why the
    backend answers one `control_path` rather than three flag-specific
    rewrites. What is pinned is that this backend
    NEVER hands back a host path: every control file is either translated into
    the container's own namespace or reported unreachable, so a caller that
    emits whatever it gets back cannot name a file the agent has no way to open.
    """
    backend = DevcontainerLaunchBackend()
    # Both classes of control file: the ones a container may be handed, and the
    # ones deliberately never mounted because their CONTENT runs a host process
    # — the second class is what keeps the "unreachable" arm alive here.
    every = (
        *AgentSharePlan.default_control_files(),
        *AgentSharePlan.host_process_control_files(),
    )
    # Half mounted, half dropped — both arms of the answer in one plan.
    plan = _container_plan(tmp_path, present=every[:1])

    answers = {path: backend.control_path(path, share_plan=plan) for path in every}

    assert all(
        answer is None or answer.startswith(f"{CONTAINER_CONTROL_ROOT}/")
        for answer in answers.values()
    ), answers
    assert not any(answer == str(path) for path, answer in answers.items())
    # And the mounted one really is usable, or this passes by refusing everything.
    assert answers[every[0]] == str(CONTAINER_CONTROL_ROOT / every[0].name)


def test_a_container_launch_with_no_share_plan_reaches_nothing(tmp_path: Path) -> None:
    """`None` plan → `None` path, never a host fallback.

    A spec with no plan is an unprovisioned or plan-less launch; there is no
    mount table to translate through, and the host path is precisely the answer
    that kills the agent. Refusing the flag degrades the status axis; emitting
    an unopenable one loses the whole workspace.
    """
    backend = DevcontainerLaunchBackend()

    assert backend.control_path(tmp_path / "grove" / "hooks.json") is None
    assert backend.control_path(tmp_path / "grove" / "hooks.json", share_plan=None) is None


def test_host_backends_keep_the_identity_answer(tmp_path: Path) -> None:
    """Same filesystem, so every control file is reachable and none is rewritten
    — and a plan (which a host launch never carries) cannot change that."""
    control = tmp_path / "grove" / "hooks-settings.json"
    plan = _container_plan(tmp_path, present=())

    for backend in (TmuxLaunchBackend(), HeadlessLaunchBackend()):
        assert backend.control_path(control) == str(control)
        assert backend.control_path(control, share_plan=plan) == str(control)


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
