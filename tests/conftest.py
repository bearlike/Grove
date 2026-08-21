"""Shared pytest fixtures: tmp state, real git repo, fake tmux."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

import pytest

from grove.core import tmux as tmux_mod
from grove.core.agents.base import AgentVersionProbe
from grove.core.agents.claude_code import ClaudeCodeAdapter
from grove.core.agents.codex import CodexAdapter
from grove.core.config import GroveConfig
from grove.core.container_infra import ProjectInfra
from grove.core.container_netfilter import NetfilterPayload
from grove.core.container_runtime import DockerCli
from grove.core.container_tmux import TmuxPayload
from grove.core.devcontainer import (
    DevcontainerCli,
    DevcontainerConfig,
    DevcontainerProbe,
    DevcontainerWorkspace,
    ProgressCallback,
    ProgressEvent,
    ReadConfigurationResult,
    UpResult,
)
from grove.core.errors import DevcontainerError, TmuxError
from grove.core.preflight import CheckResult, HostPreflight
from grove.core.tmux import HostAttach

#: The in-container workspace folder `FakeCli.up` reports — the nested-cwd
#: rule is resolved against it, so tests assert offsets from it.
FAKE_REMOTE_FOLDER = "/workspaces/repo"

#: What ``ContainerRuntimeState.inspect_argv`` really prints, captured verbatim
#: against a live, an exited and a restarted container — only the image name is
#: substituted for the fixtures' own. Shared by every test that fakes the
#: liveness read, because the shape is the point: four tab-separated fields and
#: a TRAILING NEWLINE, which is exactly what a parser written against a
#: hand-typed payload forgets to survive.
#:
#: The last field is ``.State.StartedAt``, and the three payloads carry the
#: real relationship between them rather than three invented strings: a
#: STOPPED container keeps the start it had, and a container brought back with
#: a bare ``docker start`` reports a NEW one — including docker's own variable
#: nanosecond width, which a hand-written pair would have tidied into equal
#: lengths.
DOCKER_INSPECT_STARTED_AT = "2026-07-31T08:35:12.131856305Z"
DOCKER_INSPECT_RESTARTED_AT = "2026-07-31T08:35:26.60390878Z"
DOCKER_INSPECT_RUNNING = f"true\trunning\tghcr.io/example/dev:1\t{DOCKER_INSPECT_STARTED_AT}\n"
DOCKER_INSPECT_STOPPED = f"false\texited\tghcr.io/example/dev:1\t{DOCKER_INSPECT_STARTED_AT}\n"
DOCKER_INSPECT_RESTARTED = f"true\trunning\tghcr.io/example/dev:1\t{DOCKER_INSPECT_RESTARTED_AT}\n"

# ─── network side effects neutralized ───────────────────────────────────────


@pytest.fixture(autouse=True)
def _offline_published_ranges(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may fetch a provider's published CIDR ranges.

    `EgressPolicy.derive` takes `fetch_ranges` with a real-network default, so
    every caller that does not inject one reaches `api.github.com/meta`. That
    made the derived-allowlist assertions depend on whether the request happened
    to succeed inside the run: alone they passed, and in a full suite they
    failed once the fetch returned 52 extra CIDRs. Flaky in the worst direction,
    because the green reading was the one produced by the network being slow.

    Autouse rather than per-test for the reason the container epic already
    established: a new pervasive external dependency needs a pervasive
    neutralizer, not an opt-in each new test has to remember. A test that wants
    ranges injects `fetch_ranges` directly, which bypasses this seam.

    Patches the HTTP call rather than the function, so the function's own
    best-effort path still runs and returns `()`. A test exercising the fetch
    itself re-patches `httpx.get` after this fixture and wins, which patching
    the function by name would have blocked.
    """

    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("network disabled under pytest (conftest._offline_published_ranges)")

    monkeypatch.setattr("grove.core.container_policy.httpx.get", _refuse)


@pytest.fixture(autouse=True)
def _offline_release_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may hit GitHub's releases API.

    Patches the module-level fetch seam so a default ``ReleaseChecker`` (the one
    the daemon/TUI build with no injected ``fetcher``) reports "unknown" instead
    of reaching the network. Tests that want a specific result inject a
    ``fetcher`` directly, which bypasses this seam. Same discipline as the fake
    tmux/git boundaries — real I/O never runs under pytest.
    """
    monkeypatch.setattr(
        "grove.core.release.fetch_latest_release_tag",
        lambda repo, *, installed="": None,
    )


@pytest.fixture(autouse=True)
def _offline_codex_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may shell out to a real ``codex`` for its model catalog.

    Patches the module-level subprocess seam so a default ``CodexAdapter``
    (the registry singleton) reads a fixed in-memory catalog instead of running
    ``codex debug models``. Same discipline as ``_offline_release_check`` and the
    fake tmux/git seams — real I/O never runs under pytest. A test that wants a
    specific catalog (or an unreadable one) re-patches ``_probe_codex_models``,
    or exercises the pure ``_parse_codex_models`` directly.
    """
    catalog = (
        '{"models":['
        '{"slug":"gpt-5.5","visibility":"list","priority":0},'
        '{"slug":"gpt-5.4","visibility":"list","priority":1},'
        '{"slug":"codex-auto-review","visibility":"hide","priority":2}'
        "]}"
    )
    monkeypatch.setattr(
        "grove.core.agents.codex._probe_codex_models",
        lambda binary: catalog,
    )


@pytest.fixture(autouse=True)
def _offline_tool_versions(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No test may shell out to a real ``claude``/``codex`` for its version.

    Every launch stamps the agent's reported version onto the trace resource, so
    an unpatched suite would run a real subprocess on each ``create()`` — the
    same discipline as ``_offline_codex_models``. The memo is process-lifetime
    by design, which makes it test state as well: cleared on both sides so a
    case that DOES exercise the probe cannot inherit or leak a cached answer.
    """
    AgentVersionProbe.clear_cache()
    monkeypatch.setattr(
        AgentVersionProbe,
        "probe",
        classmethod(lambda cls, argv: None),
    )
    yield
    AgentVersionProbe.clear_cache()


@pytest.fixture(autouse=True)
def _offline_container_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No test may require a devcontainer CLI, a docker daemon, or an image pull.

    Containers are the DEFAULT runtime, so an unpatched suite would have every
    ``create()`` shell out to ``devcontainer``/``docker`` — the exact thing the
    fake-tmux/fake-git discipline forbids, and on a developer box with Docker
    installed it would really build images. Every container-scoped preflight
    check reports failure, so the decision tree takes arm 4 and every existing
    test keeps getting the host workspace it was written for, with an honest
    persisted ``runtime_fallback_reason``. Autouse for the same reason as the
    init-log redirect: the blast radius is every test that creates a workspace,
    not just the ones that know to ask.

    A test that wants the container path injects its own fake CLI/preflight into
    ``WorkspaceManager`` (last-wins DI), never by re-patching these.

    The engine half is neutralized at ``HostPreflight._run`` — preflight's ONE
    subprocess boundary, and since the create probe now consumes
    ``container_ready()`` (rather than owning a second pair of probes), stubbing
    that boundary covers `grove doctor` and the create path together. It is
    stubbed rather than the public ``container_ready`` so a test that means to
    exercise real preflight aggregation still can, by re-patching the same
    boundary with its own scripted results — which `tests/core/test_preflight.py`
    and `tests/cli/test_doctor_command.py` already do.

    **The create path is not the only door.** The daemon wires
    ``ProjectInfra.registration_hook`` as ``RepoRegistry.on_project_registered``,
    so merely REGISTERING a repo — which every daemon test does, without ever
    creating a workspace — fired a real `devcontainer build`. That is not a
    hypothetical: it built six `grove/*-dev` images on the developer host and
    made `tests/daemon/test_projects.py` take 107s for 7 tests. Neutralized at
    ``warm_quietly``, the hook's whole body and the only thing it ever calls:
    stubbing there covers the config read, the image probe and the build in one
    place, and nothing else calls it.

    **The provision path opens a THIRD door.** `ContainerProvisioner`
    builds Grove's static-tmux bundle with a real `docker build` the first time
    a container is provisioned, caching it in the user's own state dir — so
    every test that injects a `FakeCli` and takes the container arm compiled
    tmux on the developer's machine and left 1.4 MB in `~/.local/state/grove`.
    Both halves are neutralized: the cache is redirected into `tmp_path` (a
    developer who HAS built the bundle must not get different answers from one
    who has not — that is determinism, not just isolation) and the builder is
    stubbed to report what is on disk without spawning anything. A test that
    wants the tmux arm supplies its own bundle through `container.tmux.payload`,
    which is the real config seam. Pinned by
    `test_offline_guarantee.py`, which asserts the guarantee this docstring
    makes rather than trusting it.

    **Nor is the WRITE path the only door.** Status reconciliation
    reads container liveness, so merely LISTING a container workspace reaches
    ``docker``. ``DockerCli.read_result`` — the one boundary every best-effort
    docker read funnels through — answers ``None`` ("docker could not be run"),
    which is the honest reading on a machine with no docker and, crucially, the
    same answer on a developer box that HAS docker: without it the suite's
    verdict for a container workspace would depend on whether the machine
    running it happens to have a docker binary on PATH. A test that means to
    exercise liveness re-patches this same boundary with captured payloads —
    `tests/core/test_container_liveness.py`.

    **The provision path also builds a static `iptables` bundle**, for images
    that ship none. Neutralized identically — cache into `tmp_path`, builder
    reports what is on disk — because the reasons are identical: a real
    `docker build` on the developer's machine, and a verdict that would
    otherwise depend on whether that developer had ever provisioned a
    container before.
    """
    monkeypatch.setattr(
        "grove.core.devcontainer.DevcontainerCli.probe",
        lambda self: DevcontainerProbe(binary=self.binary, available=False),
    )
    monkeypatch.setattr(HostPreflight, "_run", staticmethod(lambda argv: None))
    monkeypatch.setattr(
        "grove.core.paths.container_tmux_payload_dir",
        lambda tag: tmp_path / "container-tmux" / tag,
    )
    monkeypatch.setattr(TmuxPayload, "build", lambda self, **kwargs: self.available)
    monkeypatch.setattr(
        "grove.core.paths.container_netfilter_payload_dir",
        lambda tag: tmp_path / "container-netfilter" / tag,
    )
    monkeypatch.setattr(NetfilterPayload, "build", lambda self, **kwargs: self.available)

    async def _no_prebuild(self: ProjectInfra) -> None:
        del self

    monkeypatch.setattr(ProjectInfra, "warm_quietly", _no_prebuild)

    def _no_docker(self: DockerCli, argv: Sequence[str]) -> None:
        del self, argv

    monkeypatch.setattr(DockerCli, "read_result", _no_docker)


@pytest.fixture(autouse=True)
def _fresh_transcript_caches() -> None:
    """Each test starts with empty transcript caches.

    The incremental transcript cache + result memo are module-level singletons
    (they must outlive adapter instances in the daemon), so without a reset
    they'd accumulate state across the whole suite — and a same-size same-ns
    rewrite of a fixture path could read stale. Clearing is the adapters' own
    public ``clear_caches`` seam, not a private patch.
    """
    ClaudeCodeAdapter.clear_caches()
    CodexAdapter.clear_caches()


# ─── on-disk paths redirected to a tmpdir ───────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_agent_hook_paths(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auto-redirect the hook-settings + sidecar dirs so every test is sandboxed.

    ``HooksConfig.enabled`` defaults ``True``, so an unconfigured
    ``GroveConfig()`` writes real files on every ``claude_code``
    ``manager.create()``/``resume()``/``respawn()`` — the hook-only settings
    file AND the ingest-token sibling `ClaudeHook.ensure_ingest_token`
    persists — unless redirected. Autouse (unlike the opt-in `tmp_state_dir`
    below) because the blast radius is every test in the suite, not just the
    ones that already know to ask for isolation; a test that wants its OWN
    path (e.g. to assert the file's contents) still wins by monkeypatching
    the same attrs afterward — `monkeypatch.setattr` on an already-patched
    attribute just overwrites, last call wins.
    """
    base = tmp_path_factory.mktemp("agent-hook-state")
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: base / "agent-sidecars")
    monkeypatch.setattr(
        "grove.core.paths.agent_hooks_settings_path",
        lambda: base / "claude-hooks-settings.json",
    )
    # Its container-only sibling is written by the SAME `_ensure_control_files`
    # pass and resolves through the same raw `platformdirs` call, so leaving it
    # out would put a real file in the user's config dir on every containerized
    # create — the exact escape this fixture exists to close, one file over.
    monkeypatch.setattr(
        "grove.core.paths.agent_container_settings_path",
        lambda: base / "claude-container-settings.json",
    )
    # The per-workspace agent-config dir is the third host path a test reaches
    # WITHOUT asking: `AgentSharePlan.seed()` creates it (and copies
    # `.claude.json` into it) on every containerized provision, and it resolves
    # through `platformdirs.user_state_dir` directly — so the opt-in
    # `tmp_state_dir`, which only redirects `user_state_path`, never covered it.
    # Any test driving a real provision was writing into the developer's live
    # `~/.local/state/grove/agent-config/<uuid>/`, one directory per test run.
    monkeypatch.setattr(
        "grove.core.paths.agent_workspace_config_dir",
        lambda workspace_id: base / "agent-config" / workspace_id,
    )
    # The first-turn brief is rendered by `_launch_env` on every claude_code
    # host launch — another raw `platformdirs` config path, and `brief.enabled`
    # defaults True, so it is reached exactly as widely as the hook settings.
    monkeypatch.setattr(
        "grove.core.paths.agent_brief_path",
        lambda workspace_id: base / "agent-briefs" / f"{workspace_id}.md",
    )
    # The handover log is the same class of hazard one directory over: any test
    # that reaches `PickupEngine()` or the daemon's `AssigneePoller` without
    # injecting a path resolves it through raw `platformdirs`, and a claim is a
    # WRITE — a test ticket recorded in the developer's live handover log would
    # then suppress a real pickup, silently and permanently.
    monkeypatch.setattr("grove.core.paths.user_handover_path", lambda: base / "handovers.json")
    # The usage cache is the same shape again: `usage.enabled` defaults True and
    # the indexer resolves this through raw `platformdirs`, so any test that
    # builds a `UsageService` without injecting a path would index the
    # developer's REAL host transcripts into their live state dir — slow, and it
    # makes a metrics assertion depend on whose laptop ran the suite.
    monkeypatch.setattr("grove.core.paths.usage_db_path", lambda: base / "usage.sqlite3")
    # The quota ledger is the usage cache's durable sibling and is reached the
    # same way — any `UsageService`/`QuotaCollector` built without an injected
    # path writes it. Unlike the cache it is not disposable: a test writing a
    # fixture cool-off into the developer's live ledger would suppress a real
    # quota read for as long as that cool-off lasts.
    monkeypatch.setattr("grove.core.paths.quota_state_path", lambda: base / "quota-state.json")
    # Historical telemetry checkpoints are durable production state: a test
    # must never suppress a later real export by writing a fixture trace id.
    monkeypatch.setattr(
        "grove.core.paths.telemetry_ledger_path",
        lambda: base / "telemetry-exports.sqlite3",
    )
    # The session turn-count cache is reached by every `SessionCatalog()` built
    # without an injected cache — which is every daemon and TUI test that lists
    # sessions — and it is a WRITE path: an unredirected fill would count the
    # developer's real transcripts into their live state dir, and a fixture
    # session id counted there would then answer a later real scan.
    monkeypatch.setattr("grove.core.paths.session_turns_path", lambda: base / "session-turns.json")


@pytest.fixture(autouse=True)
def init_logs(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Auto-redirect the per-workspace init log so no test writes a real one.

    `init_log_path` is the ONE `platformdirs` path a test can still reach
    without asking for isolation: `create()`/`resume()`/`respawn()` resolve it
    unconditionally whenever `init_script.enabled`, and both the real
    `tmux.run_init_script` and `FakeTmux`'s stand-in write to whatever they are
    handed. Left unpatched the suite deposited its fixture text (`all good`,
    `boom`) into the developer's live `~/.local/state/grove/logs/`, where it
    masquerades as production diagnostics — test artifacts were the first
    "evidence" found while debugging a real user-reported init failure.

    Autouse for the same reason as `_isolated_agent_hook_paths` above: the blast
    radius is any test that enables `init_script`, not just the ones that know
    to ask. Requestable by name (hence no leading underscore) for the tests that
    assert on the log the manager left behind — see `test_init_fail_fast.py`.
    A test wanting its own path re-patches the same attr afterward; last call
    wins (`test_workspace_lifecycle.py` does exactly that).
    """
    logs = tmp_path_factory.mktemp("init-logs")
    monkeypatch.setattr(
        "grove.core.paths.init_log_path",
        lambda workspace_id: logs / f"{workspace_id}-init.log",
    )
    # The provision log is the init log's twin in every respect that
    # matters here: resolved unconditionally on the create path, written by the
    # engine to a platformdirs location. Redirected alongside it rather than in
    # its own fixture so there is one answer to "where do workspace logs go
    # under test".
    monkeypatch.setattr(
        "grove.core.paths.provision_log_path",
        lambda workspace_id: logs / f"{workspace_id}-provision.log",
    )
    return logs


@pytest.fixture
def tmp_state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect platformdirs paths so tests don't touch real ~/ ."""
    state = tmp_path / "state"
    config = tmp_path / "config"
    state.mkdir()
    config.mkdir()
    monkeypatch.setattr("grove.core.paths.user_state_path", lambda: state / "state.json")
    monkeypatch.setattr("grove.core.paths.user_config_path", lambda: config / "config.json")
    monkeypatch.setattr("grove.core.paths.user_schema_path", lambda: config / "config.schema.json")
    monkeypatch.setattr("grove.core.paths.user_auth_path", lambda: config / "auth.json")
    monkeypatch.setattr(
        "grove.core.paths.user_webapp_sessions_path",
        lambda: config / "webapp-sessions.json",
    )
    return state


# ─── real git repo in a tmpdir ──────────────────────────────────────────────


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """A real git repo with one commit on `main`. Returns the canonical path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@grove.local")
    _git(repo, "config", "user.name", "Grove Test")
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init", "--no-verify")
    return repo.resolve()


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


# ─── fake container boundaries (in-memory; no Docker, no CLI, no network) ───


class MergedEnvelope:
    """The devcontainer CLI's own reshaping of a config into its merged form.

    Production ALWAYS reads with ``--include-merged-configuration``, so
    ``ReadConfigurationResult.effective`` always resolves to the merged arm. A
    fake that answers with a ``configuration`` only sends every test down the one
    arm the read path cannot reach — **not weak coverage, coverage of a different
    program, reported with full confidence.**

    The three reshapings are transcribed from a REAL captured payload
    (``test_devcontainer.py::_READ_CONFIGURATION``, `@devcontainers/cli` 0.88.0),
    and :func:`test_the_fake_reshapes_exactly_what_the_real_cli_reshapes` pins
    them to it — so a CLI upgrade that reshapes a fourth key fails loudly once
    instead of silently changing what the fake pretends.

    **Declared here rather than reused from ``DevcontainerConfig``**, which owns
    the same two maps for the *undo* direction. Importing them would couple the
    double to the code under test: production's idea of the CLI's shape would
    become the fake's idea of it too, and a test could never catch the two
    disagreeing — which is the entire failure this class exists to end.
    """

    #: Difference A — five hooks renamed to plural ARRAYS. The singular key is
    #: gone from the merged envelope entirely, so a reader looking for it finds
    #: nothing and drops the project's whole setup.
    LIFECYCLE_HOOKS: ClassVar[Mapping[str, str]] = {
        "onCreateCommand": "onCreateCommands",
        "updateContentCommand": "updateContentCommands",
        "postCreateCommand": "postCreateCommands",
        "postStartCommand": "postStartCommands",
        "postAttachCommand": "postAttachCommands",
    }

    #: Difference C — keys no layer authored, materialized with the CLI's own
    #: default. Indistinguishable from a pin, which is why a bare `init: true`
    #: in a project's own config can silently stop applying.
    MATERIALIZED_DEFAULTS: ClassVar[Mapping[str, Any]] = {
        "init": False,
        "privileged": False,
        "portsAttributes": {},
    }

    @classmethod
    def of(cls, config: DevcontainerConfig) -> DevcontainerConfig:
        """The merged envelope a real CLI would return for *config*.

        Built from what the test author actually WROTE (``exclude_unset``), the
        way the CLI merges the file rather than a model's defaults. No Features
        are resolved — the fake has none — so the arrays it produces hold the
        project's own command alone, where a real one may carry a Feature's
        first. That is a difference in CONTENT, not in shape, and shape is what
        every consumer of this envelope branches on.
        """
        payload = config.model_dump(by_alias=True, exclude_unset=True)
        for singular, plural in cls.LIFECYCLE_HOOKS.items():
            if singular in payload:
                payload[plural] = [payload.pop(singular)]
        # Difference B — each namespace becomes a LIST of per-layer objects, so
        # `customizations.grove.requires_container` is not where a reader of the
        # spec's own shape would look for it.
        namespaces = payload.get("customizations")
        if isinstance(namespaces, dict):
            payload["customizations"] = {
                name: value if isinstance(value, list) else [value]
                for name, value in namespaces.items()
            }
        return DevcontainerConfig.model_validate({**cls.MATERIALIZED_DEFAULTS, **payload})


class FakeCli(DevcontainerCli):
    """A scripted ``DevcontainerCli``: no subprocess, no Docker, no network.

    Subclasses the real class rather than duck-typing it so the manager's own
    type annotations stay honest and a signature change here breaks loudly.
    Shared here rather than per-module because every container test needs the
    same two boundaries, and two copies would drift the moment one verb changes.
    """

    def __init__(
        self,
        *,
        available: bool = True,
        config: DevcontainerConfig | None = None,
        container_id: str = "ctr-abc",
        up_fails: str | None = None,
        failed_container_id: str = "",
        merged: DevcontainerConfig | bool = True,
        image_has_tmux: bool = False,
        container_arch: str = "x86_64",
        workspace_folder: str = "/workspaces/repo",
    ) -> None:
        super().__init__()
        self._available = available
        #: What `read-configuration` reports as the container's own workspace
        #: folder. Non-empty by default because the real CLI always resolves
        #: one, and it is the key the trust stamp is filed under; `""` is the
        #: opt-out for a test of what happens when the CLI answers nothing.
        self._workspace_folder = workspace_folder
        #: What `uname -m` answers INSIDE the container. Defaults to the
        #: kernel's own spelling rather than docker's (`x86_64`, not `amd64`),
        #: because that is what the probe really reads — a fake that spoke
        #: docker's vocabulary would never exercise the translation.
        self._container_arch = container_arch
        # Defaults to the MEASURED reality rather than the convenient one: no
        # common base image ships tmux (`devcontainers/base:ubuntu-24.04`,
        # `python:3.11-trixie`, `node:22-bookworm` were all checked), so a fake
        # that answered "present" would put every container test on an arm real
        # workspaces almost never take.
        self._image_has_tmux = image_has_tmux
        self._config = config if config is not None else DevcontainerConfig(name="project")
        # `True` (the default) derives the envelope production always receives.
        # A config pins an exact merged form — for a test that needs a Feature's
        # contribution, which the fake resolves none of. `False` is the RAW-ONLY
        # opt-out, and it should stay rare and loud: it is the arm a real
        # `read-configuration --include-merged-configuration` never returns, so
        # a test on it is a test of a program production does not run.
        self._merged = merged
        self._container_id = container_id
        #: Public so a test can make `up` START failing partway through — a
        #: re-provision that breaks on an EXISTING workspace is a different
        #: story from one that never came up, and it is the only one where the
        #: record survives to record the failure.
        self.up_fails = up_fails
        self.failed_container_id = failed_container_id
        self.reads: list[tuple[Path, Path | None]] = []
        self.ups: list[dict[str, Any]] = []
        self.execs: list[dict[str, Any]] = []

    def probe(self) -> DevcontainerProbe:
        return DevcontainerProbe(binary=self.binary, available=self._available, version="0.88.0")

    def read_configuration(
        self,
        workspace_folder: Path,
        *,
        config: Path | None = None,
        include_merged: bool = True,
    ) -> ReadConfigurationResult:
        self.reads.append((workspace_folder, config))
        # Reported on every arm, as the real CLI does — the workspace envelope
        # is independent of the merge flag.
        workspace = (
            DevcontainerWorkspace(workspaceFolder=self._workspace_folder)
            if self._workspace_folder
            else None
        )
        # `include_merged` is honored rather than discarded, because the real
        # CLI honors it — and production passes it True, which is the whole
        # point: the default path here must be the default path there.
        if not include_merged or self._merged is False:
            return ReadConfigurationResult(configuration=self._config, workspace=workspace)
        merged = MergedEnvelope.of(self._config) if self._merged is True else self._merged
        return ReadConfigurationResult(
            configuration=self._config, mergedConfiguration=merged, workspace=workspace
        )

    def up(
        self,
        workspace_folder: Path,
        *,
        id_labels: Mapping[str, str] = {},
        config: Path | None = None,
        override_config: Path | None = None,
        secrets_file: Path | None = None,
        remote_env: Mapping[str, str] = {},
        additional_features: str = "",
        skip_post_attach: bool = True,
        on_progress: ProgressCallback | None = None,
    ) -> UpResult:
        del config, skip_post_attach
        self.ups.append(
            {
                "workspace_folder": workspace_folder,
                "id_labels": dict(id_labels),
                "override_config": override_config,
                "remote_env": dict(remote_env),
                "additional_features": additional_features,
                # The path AND the parsed contents, captured here because the
                # real caller deletes the file the moment `up` returns — a
                # test that only kept the path could never assert what
                # crossed. Mode is captured for the same reason.
                "secrets_file": secrets_file,
                "secrets": (
                    json.loads(secrets_file.read_text(encoding="utf-8"))
                    if secrets_file is not None
                    else None
                ),
                "secrets_mode": (
                    secrets_file.stat().st_mode & 0o777 if secrets_file is not None else None
                ),
            }
        )
        if on_progress is not None:
            on_progress(ProgressEvent(type="text", text="pulling base image"))
        if self.up_fails is not None:
            raise DevcontainerError(self.up_fails, container_id=self.failed_container_id or None)
        return UpResult.model_validate(
            {
                "outcome": "success",
                "containerId": self._container_id,
                "remoteUser": "vscode",
                "remoteWorkspaceFolder": FAKE_REMOTE_FOLDER,
            }
        )

    def exec(
        self,
        workspace_folder: Path,
        argv: Sequence[str],
        *,
        id_labels: Mapping[str, str] = {},
        config: Path | None = None,
        override_config: Path | None = None,
        remote_env: Mapping[str, str] = {},
    ) -> tuple[int, str]:
        """The in-container probe boundary, scripted rather than spawned.

        Unoverridden, this inherits the REAL implementation and shells out to
        `devcontainer exec` from the middle of the provisioner — watch the
        process boundary, not just the diff, to catch a fake that silently
        stops overriding a boundary production starts calling.

        Answers with `(code, stdout)` and never a raise, because that is what
        the real verb does: it reserves its exception for a failure to invoke
        the CLI itself, and a probe distinguishing those two is the whole reason
        the split exists. The stdout is shaped like the probe's real output —
        `uname -m` then an optional `tmux` line — rather than like production's
        model of it.

        `remote_env` is RECORDED, not discarded: an exec has no other way to
        carry env, so "the additional agent crossed under the same hermetic
        env as the workspace's own" is only assertable if the fake keeps it.
        A double that swallowed it would let an extra agent launch with no
        config-dir pointer and stay green.
        """
        del config
        self.execs.append(
            {
                "workspace_folder": workspace_folder,
                "argv": list(argv),
                "id_labels": dict(id_labels),
                "override_config": override_config,
                "remote_env": dict(remote_env),
            }
        )
        lines = [self._container_arch]
        if self._image_has_tmux:
            lines.append("tmux")
        return 0, "".join(f"{line}\n" for line in lines)


class FakePreflight(HostPreflight):
    """Scripted container readiness — the create path's arm-4 probe, no docker.

    Only ``container_ready`` is overridden, because that is the whole surface
    ``RuntimeResolver`` consumes; ``all_checks``/``host_ready`` stay the real
    (unused here) aggregation.
    """

    def __init__(
        self,
        *,
        ready: bool = True,
        detail: str = "engine is unreachable",
        devcontainer_cli: DevcontainerCli | None = None,
    ) -> None:
        self._ready = ready
        self._detail = detail
        self._devcontainer_cli = devcontainer_cli

    def container_ready(self) -> list[CheckResult]:
        checks = [
            CheckResult(
                name="docker daemon",
                ok=self._ready,
                detail="server 27.0.0" if self._ready else self._detail,
                hint="" if self._ready else "start the Docker daemon",
                required_for="container",
            )
        ]
        if self._devcontainer_cli is not None:
            # The REAL check, over whatever CLI the test injected — a
            # `FakeCli(available=False)` must still fall the create path back to
            # host, and faking that arm too would prove nothing.
            checks.append(self._devcontainer_cli_check())
        return checks


# ─── fake tmux backend (in-memory; same module surface as the real one) ─────


class FakeTmux:
    """In-memory stand-in for grove.core.tmux. Tracks calls for assertions."""

    def __init__(self) -> None:
        self.sessions: set[str] = set()
        self.layouts: list[tuple[str, str]] = []  # (session_name, command)
        # session_name → cwd the session was created in, and → worktree the
        # layout windows were rooted in. Lets nested-cwd tests assert the agent
        # session starts in the project subdir while the worktree/branch anchor
        # at the repo root.
        self.session_cwds: dict[str, Path] = {}
        self.layout_worktrees: dict[str, Path] = {}
        # (session_name, launch_decoration) — lets correlation tests assert the
        # `--session-id <uuid>` argv the manager threaded in from the adapter.
        self.launch_decorations: list[tuple[str, list[str]]] = []
        # (session_name, env, env_unset) — lets hermetic-profile tests assert
        # the manager threads the agent's launch env through to the layout.
        self.launch_envs: list[tuple[str, dict[str, str], tuple[str, ...]]] = []
        # (session_name, exit_suffix) — the shell fragment that installs the
        # agent-exit recorder. Empty when nothing asked for one.
        self.exit_suffixes: list[tuple[str, str]] = []
        # (session_name, shell_command) — what window 0 runs. Empty for a host
        # workspace (a plain host shell, unchanged); the `devcontainer exec …`
        # line for a container one.
        self.shell_commands: list[tuple[str, str]] = []
        self.init_calls: list[tuple[Path, dict[str, str]]] = []
        self.init_exit_code: int = 0  # tests can override
        # Set to an exception to make run_init_script RAISE instead of returning
        # an exit code. The real one raises — and writes no log — for a
        # mutually exclusive `inline`+`path` config, a missing script file, or a
        # timeout, all of which happen before the subprocess ever starts.
        self.init_raises: Exception | None = None
        self.init_stdout: str = ""  # written to log_path on each run
        self.init_stderr: str = ""
        # Tests set entries to hand specific snapshot text back from peek().
        # Keys are tmux targets (e.g. "session-name:agent").
        self.snapshots: dict[str, str] = {}
        # session → window names, in tmux index order. Mirrors what real
        # tmux would report: create_session adds "0", build_workspace_layout
        # rewrites it to [shell, agent]. Tests can poke this directly to
        # simulate sessions that were reorganized externally.
        self.windows: dict[str, list[str]] = {}
        # tmux pane_activity (seconds-ago). Indexed by target spec
        # ("sess:agent"). Default 0 (just-now activity → ACTIVE) so tests
        # that don't care about the activity dimension keep working. Tests
        # exercising IDLE override per-target with a larger value.
        self.activity_seconds_ago: dict[str, int] = {}
        # Default response for any target without an explicit entry. Set to
        # ``None`` to simulate tmux returning no activity timestamp at all.
        self.default_activity_seconds_ago: int | None = 0
        # (target, text) per send_text call — steering tests assert both the
        # resolved pane target and that refusal paths never inject at all.
        self.sent_texts: list[tuple[str, str]] = []
        # (target, ops) per send_keys call — question-answer tests assert the
        # keystroke op list the Claude adapter built landed at the resolved pane,
        # and that refusal paths never reach the injection seam at all.
        self.sent_keys: list[tuple[str, list[Any]]] = []
        # Which tmux SERVER each read/steer addressed: `("tmux",)` for this
        # host, a `docker exec … <tmux>` prefix for a container workspace
        # whose agent pane lives inside its container. Recorded rather than
        # merely accepted, because "the container road was taken" is the whole
        # assertion for that case — a fake that swallowed it would let the
        # manager steer the wrong tmux and stay green.
        self.commands: list[tuple[str, tuple[str, ...]]] = []

    def send_text(
        self,
        target: str,
        text: str,
        *,
        settle_ms: int = 200,
        command: Sequence[str] = tmux_mod.DEFAULT_TMUX_COMMAND,
    ) -> None:
        del settle_ms  # the fake has no paste-window race to guard against
        self.sent_texts.append((target, text))
        self.commands.append((target, tuple(command)))

    def send_keys(
        self,
        target: str,
        ops: Any,
        *,
        settle_ms: int = 200,
        command: Sequence[str] = tmux_mod.DEFAULT_TMUX_COMMAND,
    ) -> None:
        del settle_ms
        self.sent_keys.append((target, list(ops)))
        self.commands.append((target, tuple(command)))

    def has_session(self, name: str) -> bool:
        return name in self.sessions

    def create_session(self, name: str, cwd: Path, *, history_limit: int = 50_000) -> None:
        del history_limit
        if name in self.sessions:
            raise TmuxError(f"session already exists: {name}")
        self.session_cwds[name] = cwd
        self.sessions.add(name)
        # Real tmux always creates one initial window. build_workspace_layout
        # below renames it; until then the placeholder mirrors that state.
        self.windows[name] = ["0"]

    def kill_session(self, name: str) -> None:
        self.sessions.discard(name)
        self.windows.pop(name, None)

    def build_workspace_layout(
        self,
        session_name: str,
        *,
        cfg: GroveConfig,
        worktree: Path,
        command: str,
        decoration: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
        env_unset: Sequence[str] = (),
        exit_suffix: str = "",
        shell_command: str = "",
    ) -> None:
        self.layout_worktrees[session_name] = worktree
        self.layouts.append((session_name, command))
        # Recorded for the same reason `exit_suffix` is: it is what makes
        # window 0 an IN-CONTAINER shell, and a fake that swallowed it would
        # let the producer be unwired while every test stayed green.
        self.shell_commands.append((session_name, shell_command))
        self.launch_decorations.append((session_name, list(decoration)))
        self.launch_envs.append((session_name, dict(env or {}), tuple(env_unset)))
        # Recorded rather than merely accepted: the suffix is what installs the
        # agent-exit recorder, so a test asserting the producer is wired needs
        # to see it here. A fake that silently swallowed it would let an
        # orphaned child process go undetected.
        self.exit_suffixes.append((session_name, exit_suffix))
        # Mirrors real `build_workspace_layout`: rename window 0 → shell,
        # add an `agent` window. Tests that want a session reorganized
        # externally (no `agent`, weirdly named windows, etc.) overwrite
        # this list directly.
        self.windows[session_name] = [
            cfg.tmux.shell_window_name,
            cfg.tmux.agent_window_name,
        ]

    def run_init_script(
        self,
        cfg: Any,
        *,
        worktree: Path,
        repo_root: Path,
        extra_env: dict[str, str] | None = None,
        log_path: Path | None = None,
    ) -> int:
        del cfg, repo_root
        self.init_calls.append((worktree, dict(extra_env or {})))
        if self.init_raises is not None:
            raise self.init_raises
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(
                f"--- stdout ---\n{self.init_stdout}--- stderr ---\n{self.init_stderr}",
                encoding="utf-8",
            )
        return self.init_exit_code

    def capture_pane_snapshot(
        self,
        target: str,
        *,
        history_lines: int = 500,
        command: Sequence[str] = tmux_mod.DEFAULT_TMUX_COMMAND,
    ) -> str:
        del history_lines
        self.commands.append((target, tuple(command)))
        return self.snapshots.get(target, "")

    def list_windows(self, session: str) -> list[str]:
        return list(self.windows.get(session, []))

    def pane_activity_seconds_ago(self, target: str) -> int | None:
        if target in self.activity_seconds_ago:
            return self.activity_seconds_ago[target]
        return self.default_activity_seconds_ago

    def attach_instruction(self, session_name: str) -> HostAttach:
        return HostAttach(tmux_session=session_name, inside_outer_tmux=False)


@pytest.fixture
def fake_tmux(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeTmux]:
    """Replace every grove.core.tmux module function with a FakeTmux instance."""
    fake = FakeTmux()
    monkeypatch.setattr(tmux_mod, "has_session", fake.has_session)
    monkeypatch.setattr(tmux_mod, "create_session", fake.create_session)
    monkeypatch.setattr(tmux_mod, "kill_session", fake.kill_session)
    monkeypatch.setattr(tmux_mod, "build_workspace_layout", fake.build_workspace_layout)
    monkeypatch.setattr(tmux_mod, "run_init_script", fake.run_init_script)
    monkeypatch.setattr(tmux_mod, "capture_pane_snapshot", fake.capture_pane_snapshot)
    monkeypatch.setattr(tmux_mod, "send_text", fake.send_text)
    monkeypatch.setattr(tmux_mod, "send_keys", fake.send_keys)
    monkeypatch.setattr(tmux_mod, "list_windows", fake.list_windows)
    monkeypatch.setattr(tmux_mod, "pane_activity_seconds_ago", fake.pane_activity_seconds_ago)
    monkeypatch.setattr(tmux_mod, "attach_instruction", fake.attach_instruction)
    yield fake
