"""The `@devcontainers/cli` boundary: argv shape, JSON parsing, overlay.

No Docker, no network, no `devcontainer` binary is ever required. The pure
parts (overlay synthesis, fingerprint, custom-mount detection, the packaged
default config) are tested directly; the subprocess boundary is tested against
a recorder that stands in for `subprocess.run` / `subprocess.Popen`.

The recorder PASSES THROUGH any argv that is not the devcontainer binary — the
`subprocess` module is process-wide, so patching it would otherwise also
intercept `grove.core.git`'s own calls in the same test (tests/CLAUDE.md).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import IO

import pytest

from grove.core import devcontainer as dc
from grove.core.config import ContainerConfig
from grove.core.devcontainer import (
    OVERRIDE_CONFIG_RELPATH,
    BuildResult,
    DefaultDevcontainerConfig,
    DevcontainerCli,
    DevcontainerConfig,
    DevcontainerMount,
    GroveOverlay,
    ProgressEvent,
    ReadConfigurationResult,
    UpResult,
)
from grove.core.errors import DevcontainerError
from grove.core.git import GitRepo
from tests.conftest import MergedEnvelope

_REAL_RUN = subprocess.run

# A project config that must survive overlay synthesis intact: features,
# mounts, hooks, and a property Grove does not model at all.
_PROJECT_CONFIG = {
    "name": "acme",
    "image": "acme/dev:1",
    "features": {"ghcr.io/devcontainers/features/go:1": {"version": "1.23"}},
    "mounts": ["source=acme-cache,target=/cache,type=volume"],
    "remoteEnv": {"ACME_MODE": "dev"},
    "postCreateCommand": "make bootstrap",
    "updateContentCommand": "make deps",
    "customizations": {"vscode": {"extensions": ["golang.go"]}},
    "forwardPorts": [8080],
}


#: A REAL `devcontainer read-configuration --include-merged-configuration`
#: payload, captured from `@devcontainers/cli` 0.88.0 against a worktree
#: declaring all five lifecycle hooks, `customizations.grove`, and two
#: Features — one of which (`git-lfs`) contributes a `postCreateCommand` of its
#: own, which is what makes `postCreateCommands` an array of TWO.
#:
#: The merged envelope is a different schema from a hand-authored config: it
#: carries no `postCreateCommand` at all (renamed to a plural array) and no
#: `customizations.grove` OBJECT (a list of per-layer objects instead). No
#: config a test author writes by hand has those shapes — only the CLI's own
#: output does, and every fake in the suite returns a `configuration`-only
#: result, i.e. the one arm production never gets.
#:
#: Three faithful edits: absolute host paths rewritten to `/repo/wt`, the
#: Features' long Copilot instruction prose replaced by `…` (English text, no
#: bearing on any shape asserted here), and `featuresConfiguration` dropped
#: since `ReadConfigurationResult` ignores it. Everything else is verbatim —
#: including `init`/`privileged` appearing as `false` in the merged form though
#: the raw config never mentions either (difference C, a known live bug).
_READ_CONFIGURATION = {
    "configuration": {
        "name": "probe",
        "image": "mcr.microsoft.com/devcontainers/base:ubuntu-24.04",
        "features": {
            "ghcr.io/devcontainers/features/node:1": {},
            "ghcr.io/devcontainers/features/git-lfs:1": {},
        },
        "customizations": {
            "grove": {"requires_container": True},
            "vscode": {"extensions": ["ms-python.python"]},
        },
        "onCreateCommand": "echo project-onCreate",
        "updateContentCommand": "echo project-updateContent",
        "postCreateCommand": "bash .devcontainer/bootstrap.sh create",
        "postStartCommand": "bash .devcontainer/bootstrap.sh start",
        "postAttachCommand": {"a": "echo attach-a", "b": ["echo", "attach-b"]},
        "mounts": ["source=acme-cache,target=/cache,type=volume"],
        "runArgs": ["--restart", "unless-stopped"],
        "remoteEnv": {"ACME_MODE": "dev"},
        "containerEnv": {"ACME_BUILT": "1"},
        "forwardPorts": [8080],
        "shutdownAction": "none",
        "securityOpt": ["seccomp=unconfined"],
        "capAdd": ["SYS_PTRACE"],
        "remoteUser": "vscode",
        "updateRemoteUserUID": True,
        "configFilePath": {
            "$mid": 1,
            "fsPath": "/repo/wt/.devcontainer/devcontainer.json",
            "path": "/repo/wt/.devcontainer/devcontainer.json",
            "scheme": "vscode-fileHost",
        },
    },
    "mergedConfiguration": {
        "name": "probe",
        "image": "mcr.microsoft.com/devcontainers/base:ubuntu-24.04",
        "features": {
            "ghcr.io/devcontainers/features/node:1": {},
            "ghcr.io/devcontainers/features/git-lfs:1": {},
        },
        "mounts": ["source=acme-cache,target=/cache,type=volume"],
        "runArgs": ["--restart", "unless-stopped"],
        "remoteEnv": {"ACME_MODE": "dev"},
        "containerEnv": {"ACME_BUILT": "1"},
        "forwardPorts": [8080],
        "securityOpt": ["seccomp=unconfined"],
        "capAdd": ["SYS_PTRACE"],
        "remoteUser": "vscode",
        "updateRemoteUserUID": True,
        "configFilePath": {
            "$mid": 1,
            "fsPath": "/repo/wt/.devcontainer/devcontainer.json",
            "path": "/repo/wt/.devcontainer/devcontainer.json",
            "scheme": "vscode-fileHost",
        },
        "init": False,
        "privileged": False,
        "customizations": {
            "vscode": [
                {"settings": {"github.copilot.chat.codeGeneration.instructions": [{"text": "…"}]}},
                {"settings": {"github.copilot.chat.codeGeneration.instructions": [{"text": "…"}]}},
                {
                    "extensions": ["dbaeumer.vscode-eslint"],
                    "settings": {
                        "github.copilot.chat.codeGeneration.instructions": [{"text": "…"}]
                    },
                },
                {"extensions": ["ms-python.python"]},
            ],
            "grove": [{"requires_container": True}],
        },
        "onCreateCommands": ["echo project-onCreate"],
        "updateContentCommands": ["echo project-updateContent"],
        "postCreateCommands": [
            "/usr/local/share/pull-git-lfs-artifacts.sh",
            "bash .devcontainer/bootstrap.sh create",
        ],
        "postStartCommands": ["bash .devcontainer/bootstrap.sh start"],
        "postAttachCommands": [{"a": "echo attach-a", "b": ["echo", "attach-b"]}],
        "portsAttributes": {},
        "shutdownAction": "none",
    },
    "workspace": {
        "workspaceFolder": "/workspaces/dcprobe",
        "workspaceMount": "type=bind,source=/repo/wt,target=/workspaces/dcprobe",
    },
}


class _Recorder:
    """Stand-in for `subprocess.run` that records devcontainer argv only."""

    def __init__(self, *replies: SimpleNamespace) -> None:
        self.calls: list[list[str]] = []
        self._replies = list(replies)

    def __call__(self, argv: Sequence[str], **kwargs: object):  # type: ignore[no-untyped-def]
        if not argv or Path(str(argv[0])).name != "devcontainer":
            return _REAL_RUN(argv, **kwargs)  # type: ignore[call-overload]
        self.calls.append([str(a) for a in argv])
        return (
            self._replies.pop(0)
            if self._replies
            else SimpleNamespace(returncode=0, stdout="", stderr="")
        )

    @property
    def argv(self) -> list[str]:
        return self.calls[-1]


#: Captured at import, i.e. before the autouse offline fixture replaces it.
_REAL_PROBE = DevcontainerCli.probe


class _FakePopen:
    """Minimal `subprocess.Popen` stand-in: scripted stdout/stderr + exit code."""

    def __init__(self, stdout: str, stderr_lines: Sequence[str], returncode: int = 0) -> None:
        self._stdout = stdout
        self.stderr: IO[str] | None = None  # replaced per-instance below
        self._stderr_lines = list(stderr_lines)
        self.returncode = returncode

    def __call__(self, argv: Sequence[str], **kwargs: object) -> _FakePopen:
        self.argv = [str(a) for a in argv]
        self.stderr = iter(self._stderr_lines)  # type: ignore[assignment]
        return self

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        # `timeout` is accepted because the real streaming verbs bound
        # themselves with `container.up_timeout_seconds` — a cold build
        # that never returns must not outlive the client's own deadline.
        del timeout
        return self._stdout, ""


def _cli(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> DevcontainerCli:
    monkeypatch.setattr(dc.subprocess, "run", recorder)
    return DevcontainerCli()


def _stream_cli(monkeypatch: pytest.MonkeyPatch, popen: _FakePopen) -> DevcontainerCli:
    monkeypatch.setattr(dc.subprocess, "Popen", popen)
    return DevcontainerCli()


def _project() -> DevcontainerConfig:
    return DevcontainerConfig.model_validate(_PROJECT_CONFIG)


# ─── the config model: nothing the CLI told us may be dropped ───────────────


def test_unmodelled_properties_survive_a_round_trip() -> None:
    """`--override-config` REPLACES, so a lossy round-trip silently drops hooks."""
    restored = json.loads(_project().to_json())

    assert restored["features"] == _PROJECT_CONFIG["features"]
    assert restored["postCreateCommand"] == "make bootstrap"
    assert restored["updateContentCommand"] == "make deps"
    assert restored["customizations"] == _PROJECT_CONFIG["customizations"]
    assert restored["forwardPorts"] == [8080]


def test_fingerprint_is_key_order_independent_but_content_sensitive() -> None:
    reordered = dict(reversed(list(_PROJECT_CONFIG.items())))
    assert DevcontainerConfig.model_validate(reordered).fingerprint() == _project().fingerprint()

    changed = _project()
    changed.run_args = ["--cpus=2"]
    assert changed.fingerprint() != _project().fingerprint()


def test_custom_workspace_mount_is_detected() -> None:
    """A custom `workspaceMount` makes the CLI ignore its own flag."""
    assert not _project().has_custom_workspace_mount

    tuned = DevcontainerConfig.model_validate(
        {**_PROJECT_CONFIG, "workspaceMount": "source=/src,target=/w,type=bind"}
    )
    assert tuned.has_custom_workspace_mount


def test_write_override_lands_inside_the_worktree(tmp_path: Path) -> None:
    """Relative `build.dockerfile` / local features resolve next to the config."""
    written = _project().write_override(tmp_path)

    assert written == tmp_path / OVERRIDE_CONFIG_RELPATH
    assert json.loads(written.read_text())["features"] == _PROJECT_CONFIG["features"]


# ─── overlay synthesis (pure) ───────────────────────────────────────────────


def test_overlay_is_additive_and_keeps_the_project_config_complete() -> None:
    overlay = GroveOverlay(
        run_args=("--cpus=2",),
        init=True,
        security_opt=("no-new-privileges",),
        cap_add=("SYS_PTRACE",),
        shutdown_action="none",
        mounts=(DevcontainerMount.bind(Path("/host/cache"), Path("/cache2")),),
        remote_env={"GROVE_WORKSPACE": "abc"},
    )

    merged = overlay.apply(_project())
    payload = json.loads(merged.to_json())

    # Everything the project declared is still there.
    assert payload["features"] == _PROJECT_CONFIG["features"]
    assert payload["postCreateCommand"] == "make bootstrap"
    assert "source=acme-cache,target=/cache,type=volume" in payload["mounts"]
    assert payload["remoteEnv"]["ACME_MODE"] == "dev"
    # Plus everything Grove added.
    assert payload["runArgs"] == ["--cpus=2"]
    assert payload["init"] is True
    assert payload["securityOpt"] == ["no-new-privileges"]
    assert payload["capAdd"] == ["SYS_PTRACE"]
    assert payload["shutdownAction"] == "none"
    assert payload["remoteEnv"]["GROVE_WORKSPACE"] == "abc"


def test_overlay_forwards_git_identity_by_env_and_never_mounts_gitconfig() -> None:
    """A curated env, not the host `~/.gitconfig` (credentials, signing keys)."""
    overlay = GroveOverlay(git_author_name="Grove Bot", git_author_email="bot@grove.local")
    payload = json.loads(overlay.apply(_project()).to_json())
    env = payload["remoteEnv"]

    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "safe.directory"
    assert env["GIT_CONFIG_VALUE_0"] == "*"
    assert env["GIT_AUTHOR_NAME"] == env["GIT_COMMITTER_NAME"] == "Grove Bot"
    assert env["GIT_AUTHOR_EMAIL"] == "bot@grove.local"
    assert not any("gitconfig" in json.dumps(m) for m in payload["mounts"])


def test_overlay_leaves_identity_unset_when_the_caller_supplied_none() -> None:
    env = json.loads(GroveOverlay().apply(_project()).to_json())["remoteEnv"]
    assert "GIT_AUTHOR_NAME" not in env
    assert env["GIT_CONFIG_KEY_0"] == "safe.directory"


def test_init_is_a_default_the_project_can_override() -> None:
    """Reaping is a courtesy to the unspecified case, not part of the contract.

    Verified against Docker 29.6.1 + `@devcontainers/cli` 0.88.0: the CLI emits
    `--init` (and compose's `init: true`) ONLY when the config asks, and its own
    default entrypoint ends in `exec "$@"` — so with `overrideCommand: false`
    the project's command becomes PID 1 and a non-reaping one leaks a zombie
    per orphaned subprocess. An image running systemd is the legitimate reason
    a project says `false`, so an explicit project value still wins.
    """
    assert json.loads(GroveOverlay(init=True).apply(_project()).to_json())["init"] is True

    for pinned in (True, False):
        project = DevcontainerConfig.model_validate({**_PROJECT_CONFIG, "init": pinned})
        merged = json.loads(GroveOverlay(init=True).apply(project).to_json())
        assert merged["init"] is pinned


def test_overlay_run_args_land_after_the_projects_own() -> None:
    """Docker takes the LAST occurrence of a non-repeatable flag, so order is the win.

    This is how Grove asserts a restart policy over a project that set its own
    (verified end-to-end: appending `--restart no` after `--restart
    unless-stopped` yields `RestartPolicy=no`).
    """
    project = DevcontainerConfig.model_validate(
        {**_PROJECT_CONFIG, "runArgs": ["--restart", "unless-stopped"]}
    )
    merged = json.loads(GroveOverlay(run_args=("--restart", "no")).apply(project).to_json())

    assert merged["runArgs"] == ["--restart", "unless-stopped", "--restart", "no"]


# ─── the worktree landmine, against a REAL linked worktree ──────────────────


def test_linked_worktree_gets_a_common_dir_mount_at_the_same_absolute_path(
    tmp_repo: Path, tmp_path: Path
) -> None:
    """A worktree's `.git` is a POINTER FILE; without the common dir, git breaks."""
    worktree = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feature", str(worktree)],
        cwd=tmp_repo,
        check=True,
        capture_output=True,
    )
    assert (worktree / ".git").is_file(), "a linked worktree's .git is a file, not a dir"

    common = GroveOverlay.worktree_common_dir(worktree)
    assert common == (tmp_repo / ".git").resolve()

    merged = GroveOverlay().apply(_project(), git_common_dir=common)
    mounts = [m for m in merged.mounts if isinstance(m, DevcontainerMount)]
    assert [(m.source, m.target) for m in mounts] == [(str(common), str(common))], (
        "source and target must be the SAME absolute path — the .git pointer file "
        "names the common dir absolutely"
    )
    assert merged.remote_env["GIT_COMMON_DIR"] == str(common)


def test_main_checkout_needs_no_extra_mount(tmp_repo: Path) -> None:
    """The ordinary case stays untouched: the common dir IS the worktree's .git."""
    assert GitRepo(tmp_repo).common_dir() == (tmp_repo / ".git").resolve()
    assert GroveOverlay.worktree_common_dir(tmp_repo) is None

    merged = GroveOverlay().apply(_project(), git_common_dir=None)
    assert merged.mounts == _project().mounts


# ─── the packaged default config (L0) ───────────────────────────────────────


def test_packaged_default_config_is_self_contained() -> None:
    """A config outside the worktree cannot resolve relative paths — so it has none."""
    raw = json.loads(DefaultDevcontainerConfig.path().read_text(encoding="utf-8"))

    assert raw["image"], "the default must be image-based"
    assert "build" not in raw and "dockerComposeFile" not in raw
    assert raw["features"], "toolchains + agent CLIs ship as features"
    assert all(not fid.startswith("./") for fid in raw["features"]), "no local-path features"

    loaded = DefaultDevcontainerConfig.load()
    assert loaded.image == raw["image"]


def test_packaged_default_config_builds_no_runtime_from_source() -> None:
    """The default IS the first-run experience, so its cold build cannot be minutes.

    Measured on the reference host (Docker 29.6.1, CLI 0.88.0, 2026-08-02): a
    `python:1` feature pinned to a concrete `"3.12"` COMPILES CPython, one
    `RUN` layer of **249.7 s** out of a 384 s feature build. `"os-provided"`
    takes Ubuntu 24.04's own packaged 3.12.3 — the same minor version — in
    44.5 s, and the whole build drops to 93 s.

    A version pin is the trigger, so that is what this asserts. `installTools`
    rides along because the linter/formatter set it installs is an opinion no
    repo without a `.devcontainer/` has expressed.
    """
    features = json.loads(DefaultDevcontainerConfig.path().read_text(encoding="utf-8"))["features"]
    python = next(opts for fid, opts in features.items() if "/features/python:" in fid)

    assert python["version"] == "os-provided", "a pinned version compiles CPython from source"
    assert python["installTools"] is False


def test_packaged_default_config_adds_nothing_its_base_image_already_ships() -> None:
    """A feature that reinstalls what the base image has is pure cold-build tax.

    `mcr.microsoft.com/devcontainers/base:ubuntu-24.04` is itself built with
    `common-utils` applied, so it already carries the `vscode` user, `sudo`,
    `zsh`, `jq`, `iproute2` and a source-built git 2.51.1 (verified against the
    image on 2026-08-02). Re-declaring those two features cost a measured
    58.2 s + 19.3 s per cold build and changed nothing but the login shell.
    """
    features = json.loads(DefaultDevcontainerConfig.path().read_text(encoding="utf-8"))["features"]

    assert not [fid for fid in features if "/features/common-utils:" in fid]
    assert not [fid for fid in features if "/features/git:" in fid]


def test_container_default_config_overrides_the_packaged_one(tmp_path: Path) -> None:
    custom = tmp_path / "mine.json"
    custom.write_text(json.dumps({"image": "acme/base:2"}), encoding="utf-8")
    cfg = ContainerConfig.model_validate({"default_config": str(custom)})

    assert DefaultDevcontainerConfig.path(cfg) == custom
    assert DefaultDevcontainerConfig.load(cfg).image == "acme/base:2"


def test_unreadable_default_config_raises_the_narrow_error(tmp_path: Path) -> None:
    cfg = ContainerConfig.model_validate({"default_config": str(tmp_path / "nope.json")})
    with pytest.raises(DevcontainerError):
        DefaultDevcontainerConfig.load(cfg)


# ─── read-configuration ─────────────────────────────────────────────────────


def test_read_configuration_argv_and_merged_preference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = {
        "configuration": {"image": "raw:1"},
        "mergedConfiguration": {"image": "merged:1", "features": {"x": {}}},
        "workspace": {"workspaceFolder": "/workspaces/acme"},
    }
    recorder = _Recorder(SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""))
    cli = _cli(monkeypatch, recorder)

    result = cli.read_configuration(tmp_path, config=tmp_path / "dc.json")

    assert recorder.argv == [
        "devcontainer",
        "read-configuration",
        "--workspace-folder",
        str(tmp_path),
        "--include-merged-configuration",
        "--config",
        str(tmp_path / "dc.json"),
        "--log-format",
        "json",
    ]
    assert isinstance(result, ReadConfigurationResult)
    assert result.effective.image == "merged:1", "merged carries the features' metadata"
    assert result.workspace is not None
    assert result.workspace.workspace_folder == "/workspaces/acme"


def test_read_configuration_without_merged_falls_back_to_the_raw_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = _Recorder(
        SimpleNamespace(returncode=0, stdout='{"configuration": {"image": "raw:1"}}', stderr="")
    )
    cli = _cli(monkeypatch, recorder)
    assert cli.read_configuration(tmp_path).effective.image == "raw:1"


def test_read_configuration_failure_raises_with_the_stderr_tail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = _Recorder(SimpleNamespace(returncode=1, stdout="", stderr="no devcontainer.json"))
    cli = _cli(monkeypatch, recorder)

    with pytest.raises(DevcontainerError, match=r"no devcontainer\.json"):
        cli.read_configuration(tmp_path)


# ─── the merged envelope is a different schema ──────────────────────────────


def _effective(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> DevcontainerConfig:
    """`effective` off the REAL captured `read-configuration` payload."""
    stdout = json.dumps(_READ_CONFIGURATION)
    recorder = _Recorder(SimpleNamespace(returncode=0, stdout=stdout, stderr=""))
    return _cli(monkeypatch, recorder).read_configuration(tmp_path).effective


def test_merged_lifecycle_hooks_are_folded_onto_their_spec_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Merged RENAMES the hooks, so a naive override would name them wrong.

    `mergedConfiguration` carries no `postCreateCommand`; it carries
    `postCreateCommands`, an array holding every Feature's hook AND the
    project's. `extra="allow"` then wrote those plural names faithfully into the
    override config — the value was never lost — but that file is parsed as a
    devcontainer.json, which knows only the singular names, so the project's
    whole setup sat in the file and never ran.
    """
    payload = json.loads(_effective(monkeypatch, tmp_path).to_json())

    assert payload["postCreateCommand"] == (
        "/usr/local/share/pull-git-lfs-artifacts.sh && bash .devcontainer/bootstrap.sh create"
    ), "the Feature's hook and the project's, in the CLI's own order, chained on &&"
    assert payload["postStartCommand"] == "bash .devcontainer/bootstrap.sh start"
    assert payload["onCreateCommand"] == "echo project-onCreate"
    assert payload["updateContentCommand"] == "echo project-updateContent"
    # Skipped at `up` (`--skip-post-attach`) but still carried: the override
    # replaces the project's config, so dropping it would delete the hook.
    assert payload["postAttachCommand"] == {"a": "echo attach-a", "b": ["echo", "attach-b"]}

    assert not [key for key in payload if key.endswith("Commands")], (
        "leaving the plural key alongside the singular gives the override "
        "reader two answers for one hook"
    )
    # The rest of the merged form is untouched — it is why merged is preferred.
    assert payload["features"] == _READ_CONFIGURATION["configuration"]["features"]
    assert payload["remoteUser"] == "vscode"


def test_groves_egress_hook_still_runs_first_and_gates_the_whole_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Isolation contract: the firewall precedes the SEQUENCE, not its head."""
    effective = _effective(monkeypatch, tmp_path)
    merged = GroveOverlay(post_start_command="bash .grove-egress.sh").apply(effective)

    assert merged.post_start_command == (
        "bash .grove-egress.sh && bash .devcontainer/bootstrap.sh start"
    )

    chained = DevcontainerConfig.model_validate(
        {"postStartCommands": ["start-db", "start-web"]}
    ).with_singular_lifecycle_hooks()
    guarded = GroveOverlay(post_start_command="firewall").apply(chained)

    assert guarded.post_start_command == "firewall && start-db && start-web"


def test_a_project_with_no_features_keeps_its_singular_hooks_untouched() -> None:
    """No plural keys in the input means the fold is provably a no-op."""
    raw = ReadConfigurationResult.model_validate(
        {"configuration": _READ_CONFIGURATION["configuration"]}
    )

    assert raw.effective is raw.configuration, "nothing to fold: the same object rides through"
    assert raw.effective.post_start_command == "bash .devcontainer/bootstrap.sh start"
    assert json.loads(raw.effective.to_json())["postCreateCommand"] == (
        "bash .devcontainer/bootstrap.sh create"
    )


def test_a_lone_command_rides_through_verbatim_whatever_its_shape() -> None:
    """The single-command case is every shape's escape hatch — including objects.

    An object of named PARALLEL commands has no sequential rendering, so it is
    only ever collapsed when it shares an array with something else.
    """
    parallel = {"db": "start-db", "web": ["npm", "start"]}
    folded = DevcontainerConfig.model_validate(
        {"postCreateCommands": [parallel], "onCreateCommands": [["echo", "one arg"]]}
    ).with_singular_lifecycle_hooks()
    payload = json.loads(folded.to_json())

    assert payload["postCreateCommand"] == parallel
    assert payload["onCreateCommand"] == ["echo", "one arg"], "an argv list is not stringified"


def test_several_commands_collapse_to_a_quoted_sequential_chain() -> None:
    """Every command survives; an argv list is quoted, never re-split on spaces."""
    folded = DevcontainerConfig.model_validate(
        {
            "postCreateCommands": [
                ["echo", "two words"],
                {"a": "run-a", "b": "run-b"},
                "make bootstrap",
            ]
        }
    ).with_singular_lifecycle_hooks()

    assert json.loads(folded.to_json())["postCreateCommand"] == (
        "echo 'two words' && run-a && run-b && make bootstrap"
    )


def test_an_empty_hook_array_yields_no_hook_at_all() -> None:
    """A Feature-less array is nothing to run — not an empty argv to fail on."""
    folded = DevcontainerConfig.model_validate(
        {"postCreateCommands": [], "image": "acme:1"}
    ).with_singular_lifecycle_hooks()
    payload = json.loads(folded.to_json())

    assert "postCreateCommand" not in payload
    assert "postCreateCommands" not in payload
    assert payload["image"] == "acme:1"


def test_a_materialized_default_is_not_a_project_decision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The merged envelope reports `init: false` though no layer asked.

    The overlay's `init` is a DEFAULT with a deliberate opt-out, gated on
    `merged.init is None` — a check the merged envelope alone makes permanently
    false, since it materializes `init` even when no layer set it. Reading off
    merged would make the reaper yield to a decision the project never made.
    The raw envelope is the only witness of what was actually written.
    """
    effective = _effective(monkeypatch, tmp_path)

    assert effective.init is None, "nobody wrote `init`, so nothing may read as a pin"
    assert GroveOverlay(init=True).apply(effective).init is True

    payload = json.loads(effective.to_json())
    assert "privileged" not in payload, "the same trap, disarmed before Grove reads the key"


def test_a_project_that_wrote_the_default_itself_still_wins() -> None:
    """The opt-out is real: tini in front of a systemd image breaks the container.

    `false` is also the CLI's own default, so this case is indistinguishable from
    the one above by VALUE — only the raw envelope separates them.
    """
    raw = dict(_READ_CONFIGURATION["configuration"], init=False)
    result = ReadConfigurationResult.model_validate(
        {"configuration": raw, "mergedConfiguration": _READ_CONFIGURATION["mergedConfiguration"]}
    )

    assert result.effective.init is False
    assert GroveOverlay(init=True).apply(result.effective).init is False


def test_a_features_contribution_survives_though_the_project_never_wrote_it() -> None:
    """Absence from the raw envelope does NOT mean nobody asked.

    A Feature can contribute `init: true` without the project naming it, which is
    the entire reason the merged form exists. Dropping every key the project did
    not write would delete exactly that — so a value is only dropped when it also
    equals the CLI's own default, which proves no layer asked for it.
    """
    merged = dict(_READ_CONFIGURATION["mergedConfiguration"], init=True)
    result = ReadConfigurationResult.model_validate(
        {"configuration": _READ_CONFIGURATION["configuration"], "mergedConfiguration": merged}
    )

    assert result.effective.init is True
    assert GroveOverlay(init=True).apply(result.effective).init is True


def test_the_raw_arm_is_its_own_witness_so_nothing_is_ever_dropped() -> None:
    """A CLI that returns no merged envelope must come through untouched."""
    raw = ReadConfigurationResult.model_validate(
        {"configuration": dict(_READ_CONFIGURATION["configuration"], init=False)}
    )

    assert raw.effective is raw.configuration
    assert raw.effective.init is False


def test_requires_container_is_read_off_the_merged_shape_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Merged RESHAPES `customizations`, so the L2 floor must not read off the
    object shape alone.

    A namespace's object becomes a LIST of per-layer objects in the merged
    envelope, and the read path asks the merged envelope — so a naive
    `isinstance(grove, dict)` check would answer "not required" for every real
    project, silently permitting `--runtime host` in a repo that refuses it.
    Any layer asking is enough: this is a capability REQUEST, which the floor
    always honours.
    """
    assert _effective(monkeypatch, tmp_path).requires_container

    raw = ReadConfigurationResult.model_validate(
        {"configuration": _READ_CONFIGURATION["configuration"]}
    )
    assert raw.effective.requires_container, "the object shape still answers"


@pytest.mark.parametrize(
    "customizations",
    [
        {},
        {"grove": {}},
        {"grove": []},
        {"grove": None},
        {"grove": "yes"},
        {"grove": ["yes"]},
        {"grove": [{"requires_container": False}]},
    ],
)
def test_a_broken_customizations_value_is_not_required_never_an_error(
    customizations: dict[str, object],
) -> None:
    """A committed file is untrusted input: a broken one must not block a host create."""
    config = DevcontainerConfig.model_validate({"customizations": customizations})
    assert config.requires_container is False


def test_the_merged_envelope_holds_no_shape_difference_this_module_does_not_know() -> None:
    """The enumeration in `DevcontainerConfig`'s docstring, pinned to the real payload.

    Two of the three differences were found by tripping over them. This asserts
    the *complete* diff of the captured payload, so a CLI upgrade that reshapes
    a fourth key fails here — loudly, once — instead of silently breaking one
    more property years from now.
    """
    raw = _READ_CONFIGURATION["configuration"]
    merged = _READ_CONFIGURATION["mergedConfiguration"]

    def shape(value: object) -> str:
        if isinstance(value, dict):
            return "object"
        if isinstance(value, list):
            return f"array[{shape(value[0]) if value else ''}]"
        return type(value).__name__

    def shapes(key: str) -> tuple[str | None, str | None]:
        return (
            shape(raw[key]) if key in raw else None,
            shape(merged[key]) if key in merged else None,
        )

    differences = {
        key: shapes(key) for key in raw.keys() | merged.keys() if shapes(key)[0] != shapes(key)[1]
    }

    assert differences == {
        # A — RENAMED: five hooks, singular string/object → plural array.
        "onCreateCommand": ("str", None),
        "onCreateCommands": (None, "array[str]"),
        "updateContentCommand": ("str", None),
        "updateContentCommands": (None, "array[str]"),
        "postCreateCommand": ("str", None),
        "postCreateCommands": (None, "array[str]"),
        "postStartCommand": ("str", None),
        "postStartCommands": (None, "array[str]"),
        "postAttachCommand": ("object", None),
        "postAttachCommands": (None, "array[object]"),
        # C — MATERIALIZED DEFAULTS: present though no layer mentions them.
        "init": (None, "bool"),
        "privileged": (None, "bool"),
        "portsAttributes": (None, "object"),
    }
    # B — RESHAPED is invisible to a top-level shape diff: `customizations` is an
    # object in both envelopes, and only its NAMESPACE values change shape. A
    # difference one level down is exactly the kind a key-set diff misses.
    assert isinstance(raw["customizations"]["grove"], dict)
    assert isinstance(merged["customizations"]["grove"], list)


def test_the_shared_fake_reshapes_exactly_what_the_real_cli_reshapes() -> None:
    """`MergedEnvelope` is pinned to the captured payload, not to production's maps.

    The test above pins the CLI's reshaping; this pins the test DOUBLE to the
    same payload — a `FakeCli` that answers with a `configuration` only sends
    every container test down the raw arm, the one arm
    `read-configuration --include-merged-configuration` never returns. That is
    not weak coverage, it is coverage of a different program.

    Deliberately compares against the captured payload rather than against
    `DevcontainerConfig`'s own maps. The model owns the same two mappings for
    the UNDO direction, and asserting the double against them would make
    production's belief about the CLI into the double's belief too — so the two
    could never be caught disagreeing, which is the whole failure being closed.
    A CLI upgrade that reshapes a fourth key now fails twice, loudly: once above
    for the shape, once here for the fake that does not reproduce it.
    """
    raw = _READ_CONFIGURATION["configuration"]
    real = _READ_CONFIGURATION["mergedConfiguration"]
    fake = MergedEnvelope.of(DevcontainerConfig.model_validate(raw)).model_dump(
        by_alias=True, exclude_unset=True
    )

    # A — the same five hooks are plural, and no singular survives in either.
    assert [key for key in real if key.endswith("Commands")] == [
        key for key in fake if key.endswith("Commands")
    ]
    assert not [key for key in {**real, **fake} if key in MergedEnvelope.LIFECYCLE_HOOKS]
    # B — every namespace is a list in both, one level down where a key diff misses it.
    assert {name: type(value).__name__ for name, value in fake["customizations"].items()} == {
        name: type(value).__name__ for name, value in real["customizations"].items()
    }
    # C — the materialized defaults are present, with the CLI's own values.
    assert all(
        real[key] == value == fake[key]
        for key, value in MergedEnvelope.MATERIALIZED_DEFAULTS.items()
    )
    # And nothing the real envelope carries is missing from the fake's. The
    # reverse is not asserted: a real merge resolves FEATURES, so its arrays
    # hold contributions the fake has none of — a difference in content, which
    # no consumer branches on, rather than in shape, which every consumer does.
    assert not real.keys() - fake.keys()


def test_a_missing_binary_narrows_to_the_devcontainer_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _boom(argv: Sequence[str], **kwargs: object) -> None:
        raise FileNotFoundError("devcontainer")

    monkeypatch.setattr(dc.subprocess, "run", _boom)
    with pytest.raises(DevcontainerError):
        DevcontainerCli().read_configuration(tmp_path)


# ─── up ─────────────────────────────────────────────────────────────────────


def _up_success(container_id: str = "ctr123") -> str:
    return json.dumps(
        {
            "outcome": "success",
            "containerId": container_id,
            "remoteUser": "vscode",
            "remoteWorkspaceFolder": "/workspaces/acme",
            "composeProjectName": None,
        }
    )


def test_up_argv_uses_direct_flags_and_parses_the_final_object(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    popen = _FakePopen(_up_success(), ['{"type":"text","text":"building"}'])
    cli = _stream_cli(monkeypatch, popen)

    result = cli.up(
        tmp_path,
        id_labels={"grove.managed": "1", "grove.workspace": "ws1"},
        override_config=tmp_path / "ovr.json",
        secrets_file=tmp_path / "secrets.json",
        remote_env={"GROVE_WORKSPACE": "ws1"},
    )

    assert popen.argv == [
        "devcontainer",
        "up",
        "--workspace-folder",
        str(tmp_path),
        "--id-label",
        "grove.managed=1",
        "--id-label",
        "grove.workspace=ws1",
        "--override-config",
        str(tmp_path / "ovr.json"),
        "--secrets-file",
        str(tmp_path / "secrets.json"),
        "--remote-env",
        "GROVE_WORKSPACE=ws1",
        # No `--mount`: the CLI's flag parser accepts only
        # type/source/target[/external] and rejects the whole invocation on
        # anything else, so every extra mount rides the override config.
        "--skip-post-attach",
        "--log-format",
        "json",
    ]
    assert isinstance(result, UpResult)
    assert (result.container_id, result.remote_user) == ("ctr123", "vscode")


def test_up_progress_events_reach_the_callback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The seam the daemon's SSE stream consumes; a non-JSON line is never lost."""
    popen = _FakePopen(
        _up_success(),
        ['{"type":"start","text":"Resolving Feature"}', "plain docker noise"],
    )
    cli = _stream_cli(monkeypatch, popen)
    seen: list[ProgressEvent] = []

    cli.up(tmp_path, on_progress=seen.append)

    assert [(e.type, e.text) for e in seen] == [
        ("start", "Resolving Feature"),
        ("raw", "plain docker noise"),
    ]


def test_up_error_outcome_surfaces_the_container_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed `up` can still have created a container — teardown needs its id."""
    failure = json.dumps(
        {"outcome": "error", "message": "postCreateCommand failed", "containerId": "ctr999"}
    )
    popen = _FakePopen(failure, [], returncode=1)
    cli = _stream_cli(monkeypatch, popen)

    with pytest.raises(DevcontainerError) as excinfo:
        cli.up(tmp_path)

    assert excinfo.value.container_id == "ctr999"
    assert "postCreateCommand failed" in str(excinfo.value)


def test_up_error_outcome_with_exit_zero_is_still_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    popen = _FakePopen(json.dumps({"outcome": "error", "containerId": "c1"}), [], returncode=0)
    with pytest.raises(DevcontainerError) as excinfo:
        _stream_cli(monkeypatch, popen).up(tmp_path)
    assert excinfo.value.container_id == "c1"


def test_up_without_any_json_result_reports_the_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    popen = _FakePopen("", ["cannot connect to the docker daemon"], returncode=1)
    with pytest.raises(DevcontainerError, match="docker daemon"):
        _stream_cli(monkeypatch, popen).up(tmp_path)


# ─── build / exec / probe ───────────────────────────────────────────────────


def test_build_argv_and_result(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    popen = _FakePopen(json.dumps({"outcome": "success", "imageName": ["reg/proj:abc"]}), [])
    cli = _stream_cli(monkeypatch, popen)

    result = cli.build(tmp_path, image_name="reg/proj:abc", cache_from=("reg/proj:cache",))

    assert popen.argv == [
        "devcontainer",
        "build",
        "--workspace-folder",
        str(tmp_path),
        "--image-name",
        "reg/proj:abc",
        "--cache-from",
        "reg/proj:cache",
        "--log-format",
        "json",
    ]
    assert isinstance(result, BuildResult)
    assert result.image_name == ["reg/proj:abc"]


def test_build_failure_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    popen = _FakePopen("", ["feature not found"], returncode=1)
    with pytest.raises(DevcontainerError, match="feature not found"):
        _stream_cli(monkeypatch, popen).build(tmp_path)


def test_exec_separates_argv_and_returns_the_inner_exit_code_and_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = _Recorder(SimpleNamespace(returncode=7, stdout="hi\n", stderr=""))
    cli = _cli(monkeypatch, recorder)

    code, output = cli.exec(
        tmp_path, ["bash", "-lc", "echo hi"], id_labels={"grove.workspace": "ws1"}
    )

    assert code == 7, "the inner command's exit is returned, not raised"
    # Both halves are real answers: its only caller is a probe that needs the
    # status AND what the command said, and inferring one from the other would
    # be guessing.
    assert output == "hi\n"
    assert recorder.argv == [
        "devcontainer",
        "exec",
        "--workspace-folder",
        str(tmp_path),
        "--id-label",
        "grove.workspace=ws1",
        "--",
        "bash",
        "-lc",
        "echo hi",
    ]


def test_probe_reports_presence_and_an_actionable_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The suite-wide `_offline_container_runtime` fixture stubs `probe` to
    # "unavailable" so no test shells out to a real devcontainer CLI; this is
    # the one test that exercises the real implementation, so it restores the
    # function captured at import time — before any fixture ran.
    monkeypatch.setattr(dc.DevcontainerCli, "probe", _REAL_PROBE)
    present = _Recorder(SimpleNamespace(returncode=0, stdout="0.88.0\n", stderr=""))
    probe = _cli(monkeypatch, present).probe()
    assert (probe.available, probe.version) == (True, "0.88.0")
    assert probe.detail == "devcontainer 0.88.0"

    def _missing(argv: Sequence[str], **kwargs: object) -> None:
        raise FileNotFoundError("devcontainer")

    monkeypatch.setattr(dc.subprocess, "run", _missing)
    absent = DevcontainerCli().probe()
    assert not absent.available
    assert "npm install -g @devcontainers/cli" in absent.detail
