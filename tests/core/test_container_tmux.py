"""The agent runs under a tmux INSIDE its container.

Three layers, matching the module's own split:

* the pure payload/plan (no Docker, no filesystem beyond `tmp_path`),
* the launch COMPOSITION — what the pane line actually says, including the
  regression pin that a container with no tmux still gets the plain form,
* the WIRING through the real manager and provisioner, because a plan that is
  never applied is the shape this subsystem has already shipped five times
  (`core/CLAUDE.md`: grep for members whose only callers are tests).

Everything runs against the shared fake container boundaries — no Docker, no
devcontainer CLI, no network. The one measured fact that cannot be asserted
here (a mounted static tmux really survives its exec client and really needs
the terminfo bundle to attach) was verified against a real container before
this was written; what these tests pin is that Grove composes and applies it.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

import pytest

from grove.core.config import ContainerConfig, ContainerTmuxConfig, GroveConfig
from grove.core.container_agent import ContainerAgentEntry
from grove.core.container_runtime import ContainerRuntimeState
from grove.core.container_shell import ContainerShell
from grove.core.container_tmux import (
    CONTAINER_TMUX_ROOT,
    ContainerPaneLiveness,
    ContainerTmux,
    PaneReading,
    TmuxPayload,
    TmuxRuntimePlan,
)
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.devcontainer import OVERRIDE_CONFIG_RELPATH, DevcontainerConfig
from grove.core.errors import ContainerError
from grove.core.launch import DevcontainerLaunchBackend, LaunchSpec
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.tmux import PaneReport
from grove.core.workspace import Runtime, WorkspaceState
from tests.conftest import FAKE_REMOTE_FOLDER, FakeCli, FakePreflight, FakeTmux


def _bundle(root: Path, *arches: str) -> Path:
    """A payload directory shaped like a real build's cache.

    Shared `terminfo/` beside a per-architecture `bin/<arch>/tmux`, which is the
    layout the mount has to serve because the container's platform is unknown
    until after `up`.
    """
    (root / TmuxPayload.TERMINFO_DIRNAME / "x").mkdir(parents=True)
    for arch in arches or (TmuxPayload.host_arch(),):
        binary = root / TmuxPayload.BIN_DIRNAME / arch / TmuxPayload.BINARY_NAME
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b"\x7fELF")
    return root


# ─── TmuxPayload: where the binary comes from ───────────────────────────────


def test_an_empty_payload_setting_resolves_to_groves_own_managed_cache(tmp_path: Path) -> None:
    payload = TmuxPayload.resolve(ContainerTmuxConfig())

    assert payload.managed is True
    # The autouse offline fixture redirects the cache under tmp_path; asserting
    # the KEY rather than the prefix keeps this about the layout, not the
    # sandbox. Version only: the architecture split lives INSIDE the root,
    # because one mount must serve whatever platform the container runs.
    assert payload.root.name == TmuxPayload.VERSION


def test_a_configured_payload_is_the_operators_and_grove_never_builds_over_it(
    tmp_path: Path,
) -> None:
    """`managed=False` is the whole point: a curated directory stays curated."""
    payload = TmuxPayload.resolve(ContainerTmuxConfig(payload=str(tmp_path / "vetted")))

    assert payload.managed is False
    assert payload.root == tmp_path / "vetted"
    # No bundle there, and `build` must report failure WITHOUT spawning anything
    # — proven by the real method here, with the fixture's stub bypassed.
    assert TmuxPayload.build(payload) is False


def test_availability_is_this_hosts_arch_on_disk_and_reading_it_builds_nothing(
    tmp_path: Path,
) -> None:
    payload = TmuxPayload(root=tmp_path / "p")
    assert payload.available is False

    _bundle(tmp_path / "p")
    assert payload.available is True
    assert str(payload.root) in payload.detail


def test_a_binary_without_terminfo_is_not_a_usable_bundle(tmp_path: Path) -> None:
    """An attach with no terminfo is refused, so a lone binary serves nobody."""
    root = tmp_path / "p"
    binary = root / TmuxPayload.BIN_DIRNAME / TmuxPayload.host_arch() / TmuxPayload.BINARY_NAME
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"\x7fELF")

    assert TmuxPayload(root=root).available is False


def test_the_binary_is_per_arch_and_the_terminfo_bundle_is_shared(tmp_path: Path) -> None:
    """Machine code is architecture-specific; capability DATA is not.

    Verified by running a genuine arm64 tmux under emulation against an
    x86-64-sourced terminfo bundle: 256 colours, correct full-screen render,
    real SIGWINCH reflow. So per-arch terminfo would be pure waste, while a
    single binary would simply fail to execute on the other platform.
    """
    payload = TmuxPayload(root=_bundle(tmp_path, "amd64"))

    assert payload.has_binary("amd64") is True
    assert payload.has_binary("arm64") is False
    # One terminfo tree, outside the per-arch directories, serving both.
    assert payload.terminfo == tmp_path / TmuxPayload.TERMINFO_DIRNAME
    assert payload.has_terminfo is True
    assert payload.terminfo.parent == payload.root
    assert payload.binary_for("amd64").parent.parent.name == TmuxPayload.BIN_DIRNAME


@pytest.mark.parametrize(
    ("machine", "expected"),
    [("x86_64", "amd64"), ("aarch64", "arm64"), ("arm64", "arm64"), ("mips64", "")],
)
def test_kernel_and_docker_spell_architectures_differently(
    monkeypatch: pytest.MonkeyPatch, *, machine: str, expected: str
) -> None:
    """`uname -m` says x86_64/aarch64 where docker says amd64/arm64."""
    monkeypatch.setattr("grove.core.container_tmux.platform.machine", lambda: machine)

    assert TmuxPayload.host_arch() == expected


def test_the_build_recipe_pins_what_the_failed_builds_taught(tmp_path: Path) -> None:
    """The Dockerfile is code, so the lessons compiled into it are worth a test.

    Each assertion is a build that actually failed, or a bundle that was
    actually incomplete:

    * `bison` — alpine's configure dies with "yacc not found" before it ever
      looks at the parser tmux ships.
    * all three terminfo trees — `screen.xterm-256color` and
      `rxvt-unicode-256color` live ONLY in `/lib/terminfo`, so a bundle sourced
      from the bulk tree alone looks complete and is not.
    * the aliases — kitty and ghostty announce names no ncurses packages.
    """
    del tmp_path
    recipe = TmuxPayload.dockerfile()

    assert "bison" in recipe
    assert f"tmux-{TmuxPayload.VERSION}.tar.gz" in recipe
    assert "--enable-static" in recipe
    for tree in TmuxPayload.TERMINFO_SOURCES:
        assert tree in recipe
    assert "/lib/terminfo" in TmuxPayload.TERMINFO_SOURCES
    for name, source in TmuxPayload.TERMINFO_ALIASES.items():
        assert f"{name}={source}" in recipe
    # A `scratch` export stage is what lets ONE `docker build` put files on the
    # host; without it the build needs create/cp/rm and three ways to leak.
    assert "FROM scratch AS payload" in recipe
    # Two distros, because the halves want different ones: musl for a static
    # binary, a distro that packages a full terminfo database for the entries.
    assert f"FROM {TmuxPayload.BUILDER_IMAGE} AS build" in recipe
    assert f"FROM {TmuxPayload.TERMINFO_IMAGE} AS terminfo" in recipe


def test_the_bundle_ships_the_whole_database_rather_than_a_curated_list() -> None:
    """No name list means no policy in code and no "we forgot terminal X" bug.

    The failure mode a curated set produces is a HARD attach refusal for
    whoever was left out, so the cost asymmetry is entirely on the side of
    breadth — measured, ~2 MiB of entries against a 1.3 MB binary on a
    read-only mount built once per host.
    """
    assert not hasattr(TmuxPayload, "TERMINFO_ENTRIES")
    # The aliases are the ONE exception and are not curation: both entries are
    # already in the full database, under a different filename.
    assert set(TmuxPayload.TERMINFO_ALIASES) == {"xterm-kitty", "xterm-ghostty"}


def test_the_build_exports_to_a_sibling_never_straight_into_the_cache(
    tmp_path: Path,
) -> None:
    """A half-written cache is worse than an absent one — hence export + rename."""
    payload = TmuxPayload(root=tmp_path / "cache")
    argv = payload.build_argv(tmp_path / "ctx", arch="arm64", docker_bin="podman")

    assert argv[0] == "podman"
    assert f"type=local,dest={tmp_path / 'ctx' / 'out'}" in argv
    assert str(payload.root) not in " ".join(argv)
    # The ONLY thing that varies per architecture — the recipe itself is
    # arch-agnostic (a verified aarch64 build differs by 0.9% in size).
    assert "linux/arm64" in argv


# ─── TmuxRuntimePlan: which tmux, and what crosses the boundary ─────────────


_AMD64_TMUX = f"{CONTAINER_TMUX_ROOT}/bin/amd64/tmux"


def test_a_disabled_plan_mounts_nothing_and_names_no_command(tmp_path: Path) -> None:
    plan = TmuxRuntimePlan.from_config(
        ContainerTmuxConfig(enabled=False), payload=TmuxPayload(root=_bundle(tmp_path))
    )

    assert plan.mounts == ()
    assert plan.env == {}
    assert plan.command_for(arch="amd64", image_has_tmux=True) == ""


def test_an_available_payload_mounts_read_only_and_points_terminfo_at_it(
    tmp_path: Path,
) -> None:
    plan = TmuxRuntimePlan.from_config(
        ContainerTmuxConfig(), payload=TmuxPayload(root=_bundle(tmp_path))
    )

    (mount,) = plan.mounts
    # The whole cache ROOT, not one binary: the mount is decided before `up`
    # while the container's architecture is only knowable after it.
    assert mount.source == tmp_path
    assert mount.target == CONTAINER_TMUX_ROOT
    # Read-only is why this cannot ride `up --mount` (the CLI rejects the whole
    # invocation on the option) and has to go through the override config.
    assert mount.readonly is True
    assert "readonly" in mount.to_flag()
    # The TRAILING COLON is load-bearing: ncurses reads an empty entry as "then
    # the compiled-in default", so without it Grove's bundle would HIDE an
    # image's own database rather than supplement it.
    assert plan.env["TERMINFO_DIRS"] == f"{CONTAINER_TMUX_ROOT}/{TmuxPayload.TERMINFO_DIRNAME}:"


def test_a_payload_with_no_terminfo_is_not_worth_mounting(tmp_path: Path) -> None:
    """Nothing of Grove's to offer, but the IMAGE may still have its own tmux."""
    plan = TmuxRuntimePlan.from_config(
        ContainerTmuxConfig(), payload=TmuxPayload(root=tmp_path / "absent")
    )

    assert plan.mounts == ()
    assert plan.command_for(arch="amd64", image_has_tmux=True) == "tmux"
    assert plan.command_for(arch="amd64", image_has_tmux=False) == ""


@pytest.mark.parametrize(
    ("prefer_image", "image_has_tmux", "expected"),
    [
        (True, True, "tmux"),
        (True, False, _AMD64_TMUX),
        # prefer_image=False is the fleet-standardization posture: Grove's own
        # build everywhere, even where the image ships one.
        (False, True, _AMD64_TMUX),
        (False, False, _AMD64_TMUX),
    ],
)
def test_which_tmux_wins_is_config_not_a_hard_coded_branch(
    tmp_path: Path, *, prefer_image: bool, image_has_tmux: bool, expected: str
) -> None:
    plan = TmuxRuntimePlan.from_config(
        ContainerTmuxConfig(prefer_image=prefer_image),
        payload=TmuxPayload(root=_bundle(tmp_path, "amd64")),
    )

    assert plan.command_for(arch="amd64", image_has_tmux=image_has_tmux) == expected


def test_the_selected_binary_is_the_CONTAINERS_architecture(tmp_path: Path) -> None:
    """Selection is asserted, not `execve` — and that is deliberate.

    The kernel checks an ELF's machine type against the REAL host CPU rather
    than the container's declared platform, so a wrong-arch binary bind-mounted
    into a nominally-foreign container runs natively on a same-arch host: an
    execution experiment would pass for the wrong reason on any single-arch
    machine. The honest test is that Grove picks the right path.
    """
    plan = TmuxRuntimePlan.from_config(
        ContainerTmuxConfig(), payload=TmuxPayload(root=_bundle(tmp_path, "amd64", "arm64"))
    )

    assert plan.command_for(arch="arm64", image_has_tmux=False).endswith("/bin/arm64/tmux")
    assert plan.command_for(arch="amd64", image_has_tmux=False).endswith("/bin/amd64/tmux")
    # An architecture the probe could not name yields NO binary rather than a
    # guess: a wrong one cannot execute, which is worse than none.
    assert plan.command_for(arch="", image_has_tmux=False) == ""
    assert plan.command_for(arch="", image_has_tmux=True) == "tmux"


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("x86_64\n", ("amd64", False)),
        ("x86_64\ntmux\n", ("amd64", True)),
        ("aarch64\ntmux\n", ("arm64", True)),
        ("  aarch64  \n", ("arm64", False)),
        ("mips64\ntmux\n", ("", True)),
        ("", ("", False)),
    ],
)
def test_one_probe_answers_both_questions(output: str, expected: tuple[str, bool]) -> None:
    """Architecture and image-tmux come back from a single exec, parsed purely."""
    assert TmuxRuntimePlan.parse_probe(output) == expected


@pytest.mark.parametrize("has_tmux", [True, False])
def test_probe_exits_zero_whether_or_not_the_image_has_tmux(has_tmux: bool) -> None:
    """The probe's EXIT STATUS answers "did it run", never "was tmux found".

    Runs `PROBE_COMMAND` through a real `sh` — the producer — because this is
    exactly the bug the pure `parse_probe` tests above cannot see. The caller
    gates on a zero exit before parsing, and the original `&&` form exited
    **127** whenever tmux was absent (a `;`-chained script takes its last
    command's status, and `command -v <missing>` is POSIX 127). That is the
    common case this epic targets, so the arch line was discarded, selection
    recorded "unknown", and the whole in-container tmux mechanism went silently
    inert while the workspace still worked.

    Absence of tmux is a legitimate ANSWER, not a failure, so it must not be
    reported through the channel reserved for "the exec did not happen".
    Scripting the boundary's return value cannot catch this; only running it can.
    """
    argv = list(TmuxRuntimePlan.PROBE_COMMAND)
    # Bend the probe's own text to the case under test rather than depending on
    # whether the machine running the suite happens to have tmux installed.
    absent = "grove_definitely_not_a_real_binary_xyz"
    if not has_tmux:
        argv[-1] = argv[-1].replace("command -v tmux", f"command -v {absent}")
    done = subprocess.run(argv, capture_output=True, text=True, check=False)

    assert done.returncode == 0, f"probe must exit 0; got {done.returncode}: {done.stderr!r}"
    arch, image_has_tmux = TmuxRuntimePlan.parse_probe(done.stdout)
    assert arch, "the architecture line must survive and parse"
    assert image_has_tmux is has_tmux


# ─── the launch composition (story 2) ───────────────────────────────────────


def _spec(
    tmp_path: Path,
    *,
    tmux_command: str,
    subpath: str = "",
    cfg: GroveConfig | None = None,
) -> LaunchSpec:
    worktree = tmp_path / "wt"
    cwd = worktree / subpath if subpath else worktree
    cwd.mkdir(parents=True, exist_ok=True)
    return LaunchSpec(
        session_name="test-sess",
        cwd=cwd,
        command="claude --dangerously-skip-permissions",
        decoration=("--session-id", "abc 123"),
        env={},
        env_unset=(),
        cfg=cfg if cfg is not None else GroveConfig(),
        worktree=worktree,
        kind="claude_code",
        container=ContainerRuntimeState(
            container_id="c" * 64,
            remote_workspace_folder=FAKE_REMOTE_FOLDER,
            id_labels={"grove.workspace": "ws1"},
            provisioned=True,
            tmux_command=tmux_command,
        ),
    )


def _agent_start(spec: LaunchSpec) -> dict[str, object]:
    """The agent's own ``devcontainer exec``, as the backend issued it.

    A container that can run tmux gets NO host session at all: the agent is
    started DETACHED inside the container, so everything this section pins —
    ``-A``, the session name, the nested ``cd``, ``remain-on-exit``, the
    decoration hand-off — lands as exec argv rather than as a host pane
    line. The whole exec record is returned rather than just its argv, because
    the identity it was aimed at is part of the composition too.

    Raw tokens, never a joined line: the decoration's embedded space survives as
    ONE argv element or not at all, and a join is exactly what would hide that.
    """
    cli = FakeCli()
    DevcontainerLaunchBackend(cli=cli).launch(spec)
    return cli.execs[0]


def _agent_argv(spec: LaunchSpec) -> list[str]:
    argv: list[str] = list(_agent_start(spec)["argv"])  # type: ignore[call-overload]
    return argv


def _attach_argv(spec: LaunchSpec) -> list[str]:
    """What a HUMAN entering that same session composes to.

    The launch is detached and a detached start has no client to be refused a
    terminal, so the TERM fallback is carried by the ATTACH — the same
    :class:`ContainerAgentEntry` with ``detached=False`` and no command.
    That `WorkspaceManager.attach` really composes this one is pinned through
    the real manager in ``test_container_pane.py``; here it is the composition.
    """
    assert spec.container is not None
    return ContainerAgentEntry(
        container=spec.container,
        cfg=spec.cfg,
        worktree=spec.worktree,
        cwd=spec.cwd,
        session=spec.cfg.container.tmux.session,
        cli=FakeCli(),
    ).argv(detached=False)


def _pane_line(monkeypatch: pytest.MonkeyPatch, spec: LaunchSpec) -> str:
    """The full pane line of the DEGRADED arm: the backend's command plus the
    decoration, joined exactly as ``tmux.build_workspace_layout`` joins them
    (shell-quoted argv).

    Reproduced rather than asserted on the parts, because the decoration
    surviving that join is precisely what the composition has to keep working.

    Only ``tmux_command == ""`` reaches this. A container with a tmux in it
    lays out no host session, so a caller passing one here would read
    ``captured[0]`` on a list nothing ever appended to.
    """
    from grove.core import tmux as tmux_mod  # noqa: PLC0415 - local to this helper

    captured: list[dict[str, object]] = []
    monkeypatch.setattr(tmux_mod, "create_session", lambda *a, **k: None)
    monkeypatch.setattr(tmux_mod, "build_workspace_layout", lambda name, **kw: captured.append(kw))
    DevcontainerLaunchBackend(cli=FakeCli()).launch(spec)
    kwargs = captured[0]
    decoration: tuple[str, ...] = kwargs["decoration"]  # type: ignore[assignment]
    return " ".join([str(kwargs["command"]), *(shlex.quote(token) for token in decoration)])


#: Config with the TERM fallback OFF, so a test about the tmux invocation reads
#: the invocation rather than the wrapper around it.
_NO_FALLBACK = GroveConfig.model_validate({"container": {"tmux": {"term_fallback": ""}}})


def test_the_agent_launches_under_new_session_dash_a(tmp_path: Path) -> None:
    """`-A` is the keystone: attach-if-exists makes resume/respawn idempotent.

    It is a DETACHED `-A -d` start inside the container rather than a host pane
    line — the user asked for a workspace, not to be dropped into one, and
    `attach` enters the same session later on the same `-A`.
    """
    start = _agent_start(_spec(tmp_path, tmux_command=_AMD64_TMUX, cfg=_NO_FALLBACK))
    argv: list[str] = list(start["argv"])  # type: ignore[call-overload]

    # The exec road, aimed at the container `up` created. The `devcontainer
    # exec` prefix is the CLI's own; what crosses is these tokens plus the
    # identity they are resolved against, and disagreeing on the identity would
    # reach a different container entirely.
    assert start["id_labels"] == {"grove.workspace": "ws1"}
    assert argv[:6] == [_AMD64_TMUX, "new-session", "-A", "-d", "-s", "agent"]
    # The agent command rides INSIDE an `sh -c` so the decoration can reach it
    # as argv — tmux execvp's a multi-argument command rather than re-parsing.
    assert "exec claude --dangerously-skip-permissions" in argv[8]
    # And a decoration value with a space survives as ONE element, which is
    # exactly what the `"$@"` forwarding after the `grove` placeholder buys.
    assert argv[-3:] == ["grove", "--session-id", "abc 123"]


def test_the_in_container_session_name_comes_from_config(tmp_path: Path) -> None:
    """It is a reattach identity, so it has to be reachable without a code change."""
    cfg = GroveConfig.model_validate(
        {"container": {"tmux": {"session": "codex-2", "term_fallback": ""}}}
    )
    argv = _agent_argv(_spec(tmp_path, tmux_command="tmux", cfg=cfg))

    assert argv[:6] == ["tmux", "new-session", "-A", "-d", "-s", "codex-2"]


def test_an_unknown_client_TERM_retries_once_and_says_so(tmp_path: Path) -> None:
    """Structural, not defensive: some terminals ship entries no ncurses has.

    An absent entry is a hard attach REFUSAL (`missing or unsuitable
    terminal`), so without this the workspace has no viewport at all. Verified
    end to end in a real container with the image's own terminfo masked.

    Read off the ATTACH: the wrapper exists because a CLIENT can be refused a
    terminal, and the launch starts the session with no client at all.
    """
    line = " ".join(_attach_argv(_spec(tmp_path, tmux_command=_AMD64_TMUX)))

    assert "new-session -A -s agent" in line
    # Visible, never a silent substitution: a user being emulated as another
    # terminal has to be told, and told which knob changes it.
    assert "grove: tmux could not start a client for TERM=$TERM" in line
    assert "container.tmux.term_fallback" in line
    assert "TERM=xterm-256color" in line
    # The gate that makes the retry safe: `-A` CREATES when nothing is there,
    # so retrying after a normal session end would relaunch the agent. A
    # refused client leaves no session, which is the discriminator.
    assert "has-session -t agent" in line
    # The dying server's socket outlives it by a moment, and a retry inside
    # that window fails with "server exited unexpectedly" — measured every run.
    assert "sleep 1" in line


def test_the_term_fallback_is_config_and_an_empty_value_disables_it(tmp_path: Path) -> None:
    """One field rather than a flag plus a value — "enabled with no TERM" means
    nothing, and a bool alongside a string can express exactly that."""
    cfg = GroveConfig.model_validate({"container": {"tmux": {"term_fallback": "foot"}}})
    line = " ".join(_attach_argv(_spec(tmp_path, tmux_command=_AMD64_TMUX, cfg=cfg)))
    assert "TERM=foot" in line

    off = " ".join(_attach_argv(_spec(tmp_path, tmux_command=_AMD64_TMUX, cfg=_NO_FALLBACK)))
    assert "has-session" not in off
    assert "grove: tmux could not start" not in off


def test_a_nested_project_cds_inside_the_tmux_command(tmp_path: Path) -> None:
    """The `cd` has to land in the agent's own shell, not the tmux client's."""
    argv = _agent_argv(_spec(tmp_path, tmux_command="tmux", subpath="services/api"))

    assert argv[:4] == ["tmux", "new-session", "-A", "-d"]
    assert f"cd {FAKE_REMOTE_FOLDER}/services/api && exec claude" in argv[8]


def test_a_container_with_no_tmux_keeps_the_bare_form(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The honest degradation: the workspace works, it just dies with its client."""
    line = _pane_line(monkeypatch, _spec(tmp_path, tmux_command=""))

    assert "new-session" not in line
    assert line.endswith("-- claude --dangerously-skip-permissions --session-id 'abc 123'")


# ─── wiring: the plan reaches `up`, and the probe reaches the record ─────────


def _cfg(tmp_path: Path, **tmux: object) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "container": {"enabled": True, "tmux": tmux},
            "agents": [{"name": "claude", "command": "claude", "kind": "claude_code"}],
        }
    )


def _manager(tmp_repo: Path, tmp_path: Path, cli: FakeCli, **tmux: object) -> WorkspaceManager:
    return WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path, **tmux),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=cli,
        preflight=FakePreflight(),
    )


def _create(manager: WorkspaceManager, title: str = "ws") -> WorkspaceState:
    return manager.create(CreateWorkspaceRequest(agent_name="claude", title=title))


def _override(state: WorkspaceState) -> DevcontainerConfig:
    raw = (Path(state.worktree_path) / OVERRIDE_CONFIG_RELPATH).read_text(encoding="utf-8")
    return DevcontainerConfig.model_validate_json(raw)


def test_the_payload_mount_and_terminfo_reach_the_generated_override_config(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    """Testing the PRODUCER, not a hand-built plan (`core/CLAUDE.md`)."""
    del fake_tmux
    bundle = _bundle(tmp_path / "bundle")
    cli = FakeCli()
    manager = _manager(tmp_repo, tmp_path, cli, payload=str(bundle))

    state = _create(manager)

    override = _override(state)
    flags = [m for m in override.mounts if isinstance(m, str)]
    assert any(str(bundle) in flag and "readonly" in flag for flag in flags)
    # remoteEnv, not the launch spec's env: the tmux server is started by one
    # exec and every later attach is another, and both need the bundle.
    assert override.remote_env["TERMINFO_DIRS"].endswith(":")
    assert str(CONTAINER_TMUX_ROOT) in override.remote_env["TERMINFO_DIRS"]
    assert state.container is not None
    assert state.container.tmux_command == _AMD64_TMUX


def test_the_image_probe_goes_through_the_same_exec_road_the_agent_will(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    """A `docker exec` probe answers for the IMAGE's user, not the remote one.

    So the probe must carry the same identity `up` did — otherwise it could
    reach a different container entirely.
    """
    del fake_tmux
    cli = FakeCli()
    manager = _manager(tmp_repo, tmp_path, cli)

    state = _create(manager)

    (probe,) = cli.execs
    assert probe["argv"] == list(TmuxRuntimePlan.PROBE_COMMAND)
    assert probe["id_labels"] == cli.ups[0]["id_labels"]
    assert probe["override_config"] == cli.ups[0]["override_config"]
    assert state.container is not None


def test_an_image_that_ships_tmux_is_preferred_over_groves_bundle(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    del fake_tmux
    bundle = _bundle(tmp_path / "bundle")
    manager = _manager(tmp_repo, tmp_path, FakeCli(image_has_tmux=True), payload=str(bundle))

    state = _create(manager)

    assert state.container is not None
    assert state.container.tmux_command == "tmux"


def test_no_image_tmux_and_no_payload_records_nothing_rather_than_guessing(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    """`""` is the honest answer, and it is what the launch reads to degrade."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path, FakeCli())

    state = _create(manager)

    assert state.container is not None
    assert state.container.tmux_command == ""


def test_disabling_the_feature_skips_the_probe_entirely(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    del fake_tmux
    cli = FakeCli(image_has_tmux=True)
    manager = _manager(tmp_repo, tmp_path, cli, enabled=False)

    state = _create(manager)

    assert cli.execs == []
    assert state.container is not None
    assert state.container.tmux_command == ""


def test_a_reprovision_re_probes_rather_than_trusting_the_old_record(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    """The image can change under a workspace; `resume` re-provisions anyway."""
    del fake_tmux
    cli = FakeCli()
    manager = _manager(tmp_repo, tmp_path, cli, payload=str(_bundle(tmp_path / "bundle")))
    state = _create(manager)
    assert state.container is not None
    assert state.container.tmux_command == _AMD64_TMUX

    manager.pause(state.id)
    resumed = manager.resume(state.id)

    # Counted by SHAPE rather than by total, because a launch also execs (the
    # agent and the shell, detached inside the container) — a total would
    # drift with every future exec and stop being about the probe.
    probes = [e for e in cli.execs if e["argv"] == list(TmuxRuntimePlan.PROBE_COMMAND)]
    assert len(probes) == 2
    assert resumed.runtime is Runtime.CONTAINER
    assert resumed.container is not None
    assert resumed.container.tmux_command == _AMD64_TMUX


def test_a_record_without_tmux_command_still_loads(tmp_path: Path) -> None:
    """`extra="forbid"` means every new field has to default cleanly."""
    del tmp_path
    state = ContainerRuntimeState.model_validate({"container_id": "c" * 64})

    assert state.tmux_command == ""


def test_the_container_root_is_the_one_grove_already_owns() -> None:
    """A third thing under `/grove`, never `/usr/local/bin` in someone's image."""
    assert PurePosixPath("/grove/tmux") == CONTAINER_TMUX_ROOT


# ─── reaching that tmux again: read + steer (story 3) ───────────────────────

#: `tmux list-panes -F` output captured from a real tmux 3.5a inside a real
#: devcontainer — a live pane, then the same pane after its agent exited 7
#: under `remain-on-exit`. Pinned as payloads rather than paraphrased, because
#: the empty middle field of a LIVE pane (tmux emits `pane_dead_status` and
#: leaves it blank) is exactly the shape a hand-written double would "tidy up"
#: into something the parser never meets.
LIVE_PANE = "0||1785471784\n"
DEAD_PANE = "1|7|1785471399\n"


@pytest.mark.parametrize(
    ("raw", "dead", "status", "epoch"),
    [
        (LIVE_PANE, False, None, 1785471784),
        (DEAD_PANE, True, 7, 1785471399),
        ("0|\n", False, None, None),  # the two-field form of an older tmux
        ("1|127|1785471399\n", True, 127, 1785471399),  # execvp failed: no such binary
    ],
)
def test_the_pane_report_parses_what_tmux_actually_prints(
    raw: str, dead: bool, status: int | None, epoch: int | None
) -> None:
    report = PaneReport.parse(raw)

    assert report is not None
    assert (report.dead, report.exit_status, report.activity_epoch) == (dead, status, epoch)


@pytest.mark.parametrize("raw", ["", "\n", "   \n"])
def test_no_output_is_no_report_rather_than_a_live_pane(raw: str) -> None:
    """A server that is not running prints nothing — which must not read as alive."""
    assert PaneReport.parse(raw) is None


def test_the_exec_runs_as_the_agents_own_user() -> None:
    """Measured: without `-u` the exec cannot see the agent's tmux socket at all.

    A real run answered `error connecting to /tmp/tmux-0/default (No such file
    or directory)` against a healthy server owned by uid 1000 — the socket path
    is uid-keyed, so the image's default user is simply the wrong process.
    """
    identity = ContainerRuntimeState(container_id="c" * 64, remote_user="vscode")

    argv = identity.exec_argv(["tmux", "capture-pane"])

    assert argv == ["docker", "exec", "-u", "vscode", "c" * 64, "tmux", "capture-pane"]


def test_an_unrecorded_remote_user_omits_the_flag_rather_than_guessing() -> None:
    identity = ContainerRuntimeState(container_id="c" * 64)

    assert identity.exec_argv(["tmux"]) == ["docker", "exec", "c" * 64, "tmux"]


def test_an_exec_may_never_name_a_container_by_prefix() -> None:
    """The teardown rule, unrelaxed for a read: `docker exec` resolves prefixes too."""
    with pytest.raises(ContainerError):
        ContainerRuntimeState(container_id="c" * 12).exec_argv(["tmux"])


def _identity(**kwargs: object) -> ContainerRuntimeState:
    return ContainerRuntimeState.model_validate(
        {
            "container_id": "c" * 64,
            "remote_user": "vscode",
            "remote_workspace_folder": FAKE_REMOTE_FOLDER,
            "provisioned": True,
            "tmux_command": _AMD64_TMUX,
            **kwargs,
        }
    )


def test_the_agent_pane_is_addressed_through_the_container_never_the_host() -> None:
    container_tmux = ContainerTmux.for_container(_identity(), cfg=ContainerConfig())

    assert container_tmux is not None
    assert container_tmux.pane.target == "agent"
    assert container_tmux.pane.command == (
        "docker",
        "exec",
        "-u",
        "vscode",
        "c" * 64,
        _AMD64_TMUX,
    )
    # Unmistakable rather than attachable: the in-container session name means
    # nothing to a human's own tmux, so it is never rendered as if it did.
    assert container_tmux.pane.display.startswith("container:")


@pytest.mark.parametrize(
    "container",
    [None, _identity(tmux_command=""), _identity(container_id="c" * 12)],
    ids=["host workspace", "no tmux in the image", "unusable id"],
)
def test_there_is_no_reader_where_there_is_no_in_container_tmux(
    container: ContainerRuntimeState | None,
) -> None:
    """The single `None` that keeps every other runtime from reaching for docker."""
    assert ContainerTmux.for_container(container, cfg=ContainerConfig()) is None


class FakeDocker:
    """The docker process boundary, replaying captured `list-panes` output."""

    def __init__(self, *, stdout: str | None = LIVE_PANE, reachable: bool = True) -> None:
        self.stdout = stdout
        self.reachable = reachable
        self.calls: list[list[str]] = []

    def read_result(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str] | None:
        self.calls.append(list(argv))
        if not self.reachable:
            return None
        if self.stdout is None:
            return subprocess.CompletedProcess(
                list(argv), 1, "", "no server running on /tmp/tmux-1000/default\n"
            )
        return subprocess.CompletedProcess(list(argv), 0, self.stdout, "")


def _reader() -> ContainerTmux:
    container_tmux = ContainerTmux.for_container(_identity(), cfg=ContainerConfig())
    assert container_tmux is not None
    return container_tmux


def test_one_exec_answers_liveness_death_and_activity_together() -> None:
    """At 60 ms an invocation is the unit of cost, so it carries every field."""
    docker = FakeDocker(stdout=DEAD_PANE)

    reading = _reader().read(docker=docker)  # type: ignore[arg-type]

    assert reading is not None
    assert reading.alive is False
    assert reading.report is not None
    assert reading.report.exit_status == 7
    assert docker.calls == [
        [
            "docker",
            "exec",
            "-u",
            "vscode",
            "c" * 64,
            _AMD64_TMUX,
            "list-panes",
            "-t",
            "agent",
            "-F",
            PaneReport.FORMAT,
        ]
    ]


def test_a_session_that_is_gone_is_an_answer_and_an_unreadable_docker_is_not() -> None:
    """The dead-session-vs-unreachable-docker distinction, one level down —
    and both consumers depend on it.

    tmux answering "no server running" means the agent's own tmux is gone; a
    docker that never ran means nothing was learned, and reconciliation must not
    turn that into a verdict about a workspace.
    """
    assert _reader().read(docker=FakeDocker(stdout=None)) == PaneReading(report=None)  # type: ignore[arg-type]
    assert _reader().read(docker=FakeDocker(reachable=False)) is None  # type: ignore[arg-type]


def test_ending_the_session_names_it_explicitly() -> None:
    docker = FakeDocker()

    _reader().end_session(docker=docker)  # type: ignore[arg-type]

    assert docker.calls[0][-3:] == ["kill-session", "-t", "agent"]


def test_the_pane_read_is_memoized_and_a_non_answer_is_never_cached() -> None:
    """One exec per window per identity — the amplification bound per hook event.

    The window is longer than `ContainerLiveness`'s because this read costs four
    times an inspect (60.4 ms against 14.4 ms, measured) for an answer that ages
    far more slowly.
    """
    clock = iter([0.0, 1.0, ContainerPaneLiveness.TTL_SECONDS + 1.0, 0.0, 1.0])
    liveness = ContainerPaneLiveness(clock=lambda: next(clock))
    docker = FakeDocker()
    liveness._docker = docker  # type: ignore[assignment]
    reader = _reader()

    assert liveness.reading_for(reader) is not None
    assert liveness.reading_for(reader) is not None  # inside the window: no fork
    assert len(docker.calls) == 1
    assert liveness.reading_for(reader) is not None  # window elapsed
    assert len(docker.calls) == 2

    unreachable = ContainerPaneLiveness(clock=lambda: 0.0)
    unreachable._docker = FakeDocker(reachable=False)  # type: ignore[assignment]
    assert unreachable.reading_for(reader) is None
    assert unreachable.reading_for(reader) is None
    assert len(unreachable._docker.calls) == 2  # type: ignore[attr-defined]


# ─── the launch composition, once a pane can outlive its process ────────────


def test_the_launch_asks_tmux_to_keep_the_dead_pane(tmp_path: Path) -> None:
    """`remain-on-exit`, composed where it costs one line.

    The option is set from INSIDE the pane tmux just created (`$TMUX` is set
    there, so no target can be wrong), which is why moving the multiplexer into
    the container turns this into a prefix on a script that already existed
    rather than a rework of the host's hermetic-env machinery.

    With no host pane there is no host recorder, so this is the ONLY exit
    signal a container workspace has.
    """
    argv = _agent_argv(_spec(tmp_path, tmux_command=_AMD64_TMUX, cfg=_NO_FALLBACK))
    script = argv[8]

    assert f"{_AMD64_TMUX} set-option -w remain-on-exit on" in script
    # Still ahead of the agent, and the decoration still lands as its argv.
    assert script.index("remain-on-exit") < script.index("exec claude")
    assert argv[-3:] == ["grove", "--session-id", "abc 123"]


def test_the_dead_pane_and_the_TERM_fallback_are_now_mutually_exclusive(
    tmp_path: Path,
) -> None:
    """`remain-on-exit` and the TERM fallback can never co-occur, and the
    reason is worth pinning: a launch is DETACHED (it starts something whose
    death is worth keeping, and has no client to be refused a terminal) while
    an attach carries a client and starts nothing (so there is no script to
    prefix). Either composition growing the other's half would mean one of
    those two facts had broken.
    """
    spec = _spec(tmp_path, tmux_command=_AMD64_TMUX)

    start = " ".join(_agent_argv(spec))
    assert "set-option -w remain-on-exit on" in start
    assert "has-session -t agent" not in start  # the fallback's own safety gate

    attach = " ".join(_attach_argv(spec))
    assert "has-session -t agent" in attach
    assert "remain-on-exit" not in attach


def test_the_shell_never_keeps_its_dead_pane(tmp_path: Path) -> None:
    """The agent keeps its corpse; the SHELL must not, and the asymmetry is the point.

    A dead pane keeps its session alive, and `new-session -A` against one exits
    1 without starting anything (measured). The agent launch pays for that with
    an explicit clear before every relaunch — nothing does that for `grove
    shell`, so a user quitting their shell once would leave a session no later
    `grove shell` could ever enter again. Keeping a corpse is only safe where
    something is responsible for clearing it.
    """
    worktree = tmp_path / "wt"
    worktree.mkdir()
    shell = ContainerShell(
        container=ContainerRuntimeState(
            container_id="c" * 64,
            remote_workspace_folder=FAKE_REMOTE_FOLDER,
            provisioned=True,
            tmux_command=_AMD64_TMUX,
        ),
        cfg=GroveConfig(),
        worktree=worktree,
        cwd=worktree,
        cli=FakeCli(),
    )

    assert "remain-on-exit" not in shell.command


def test_a_container_with_no_tmux_asks_for_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A container with no tmux has none to ask, so it must not grow shell that fails."""
    line = _pane_line(monkeypatch, _spec(tmp_path, tmux_command=""))

    assert "remain-on-exit" not in line


def _launch_with(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, docker: FakeDocker) -> FakeDocker:
    from grove.core import tmux as tmux_mod  # noqa: PLC0415 - local to this helper

    monkeypatch.setattr(tmux_mod, "create_session", lambda *a, **k: None)
    monkeypatch.setattr(tmux_mod, "build_workspace_layout", lambda name, **kw: None)
    monkeypatch.setattr(
        "grove.core.container_runtime.DockerCli.read_result",
        lambda _self, argv: docker.read_result(argv),
    )
    DevcontainerLaunchBackend(cli=FakeCli()).launch(
        _spec(tmp_path, tmux_command=_AMD64_TMUX, cfg=_NO_FALLBACK)
    )
    return docker


def test_a_relaunch_clears_a_session_whose_agent_already_died(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Measured: `new-session -A` against a dead pane exits 1 and starts nothing.

    So keeping the corpse for inspection costs exactly this — without it,
    `resume` and `respawn` would report success and relaunch nothing at all.
    """
    docker = _launch_with(monkeypatch, tmp_path, FakeDocker(stdout=DEAD_PANE))

    assert [argv[-3:] for argv in docker.calls if "kill-session" in argv] == [
        ["kill-session", "-t", "agent"]
    ]


def test_a_relaunch_never_touches_a_session_whose_agent_is_ALIVE(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other half, and the dangerous one: `-A` exists to attach to this."""
    docker = _launch_with(monkeypatch, tmp_path, FakeDocker(stdout=LIVE_PANE))

    assert all("kill-session" not in argv for argv in docker.calls)


def test_an_unreadable_docker_leaves_the_session_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ "Could not tell" must never authorize killing a session that may be live."""
    docker = _launch_with(monkeypatch, tmp_path, FakeDocker(reachable=False))

    assert all("kill-session" not in argv for argv in docker.calls)
