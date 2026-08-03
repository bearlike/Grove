"""Grove supplies the iptables the standard devcontainer image does not.

Four layers, matching where the fix actually lives:

* the pure payload — cache layout, mount, and the two RECIPE facts that are
  invisible until a binary refuses to run in somebody else's image,
* the generated script's fallback block, whose ORDER relative to the
  fail-closed probe is the whole point,
* the WIRING through the real provisioner, because a mount that is never
  applied is a recurring shape for this subsystem
  (`core/CLAUDE.md`: grep for members whose only callers are tests),
* the DIAGNOSIS, which is what the residual refusal is worth.

Everything here runs against the shared fake container boundaries — no Docker,
no devcontainer CLI, no network. The facts that cannot be asserted without a
real engine were measured before this was written and are recorded in
`NetfilterPayload`'s own docstrings: a cold build takes 15.6 s, the binary is
statically linked and runs unmodified in
`mcr.microsoft.com/devcontainers/base:ubuntu-24.04`, and a real
`devcontainer up` against that image then applies a 68-rule OUTPUT chain whose
allowlist genuinely enforces.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from grove.core.config import EgressConfig, GroveConfig
from grove.core.container_netfilter import NetfilterPayload
from grove.core.container_policy import CONTAINER_NETFILTER_ROOT, EgressPolicy
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.devcontainer import OVERRIDE_CONFIG_RELPATH, DevcontainerConfig, ProgressEvent
from grove.core.errors import DevcontainerError, GroveError
from grove.core.manager import WorkspaceManager
from grove.core.preflight import HostPreflight
from grove.core.runtime import ContainerProvisioner, RuntimeDecision
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Runtime, WorkspaceState
from tests.conftest import FakeCli, FakePreflight, FakeTmux


def _bundle(root: Path, *machines: str) -> Path:
    """A payload directory shaped like a real build's cache."""
    for machine in machines or (NetfilterPayload.host_machine(),):
        for name in NetfilterPayload.BINARY_NAMES:
            binary = root / NetfilterPayload.BIN_DIRNAME / machine / name
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_bytes(b"\x7fELF")
    return root


# ─── NetfilterPayload: where the binaries come from ─────────────────────────


def test_the_payload_resolves_into_groves_own_versioned_state_cache(tmp_path: Path) -> None:
    payload = NetfilterPayload.resolve()

    # The autouse offline fixture redirects the cache root into tmp_path; what
    # is pinned here is that the VERSION is the key, so a bump rebuilds rather
    # than serving a stale binary out of an identically-named directory.
    assert payload.root.name == NetfilterPayload.VERSION
    assert payload.root.is_relative_to(tmp_path)


def test_a_payload_missing_either_binary_is_not_available(tmp_path: Path) -> None:
    machine = NetfilterPayload.host_machine()
    partial = tmp_path / "bin" / machine
    partial.mkdir(parents=True)
    (partial / "iptables").write_bytes(b"\x7fELF")

    # Both, because the v6 arm of the policy is not optional: a container with
    # iptables and no ip6tables fails closed just as hard as one with neither.
    assert NetfilterPayload(root=tmp_path).available is False


def test_an_unavailable_payload_mounts_nothing_rather_than_an_empty_directory(
    tmp_path: Path,
) -> None:
    payload = NetfilterPayload(root=tmp_path / "never-built")

    assert payload.mount_plan() is None
    assert payload.mount_flags == ()
    assert "not built yet" in payload.detail


def test_the_mount_carries_the_cache_ROOT_read_only(tmp_path: Path) -> None:
    payload = NetfilterPayload(root=_bundle(tmp_path))

    plan = payload.mount_plan()

    assert plan is not None
    # The ROOT, not one architecture's directory: the mount has to be in the
    # override config before `up`, while what the container can execute is only
    # knowable after it — so the container resolves that itself.
    assert plan.source == tmp_path
    assert plan.target == CONTAINER_NETFILTER_ROOT
    assert plan.readonly is True
    assert payload.mount_flags == (plan.to_flag(),)


def test_the_recipe_forces_the_static_link_through_make_not_configure() -> None:
    recipe = NetfilterPayload.dockerfile()

    # libtool performs the final link and EATS a plain `-static` (it reads it as
    # "static against libtool libraries"), so `configure LDFLAGS=-static` yields
    # a musl-DYNAMIC binary that runs in the builder and dies with `not found`
    # in an Ubuntu image — a missing loader that reads as a missing file. And
    # `-all-static` at configure time breaks configure's own compiler test.
    assert 'make -j"$(nproc)" LDFLAGS="-all-static"' in recipe
    assert "LDFLAGS" not in recipe.split("RUN ./configure")[1].split("\n")[0]


def test_the_recipe_selects_the_legacy_backend_and_exports_both_names() -> None:
    recipe = NetfilterPayload.dockerfile()

    # `--disable-nftables` is what removes libmnl/libnftnl from the static link.
    # Safe because this bundle is only ever reached in an image with no iptables
    # at all — therefore no nested dockerd with an opinion about the backend.
    assert "--disable-nftables" in recipe
    # One multi-call binary dispatching on argv[0], copied under each name
    # rather than symlinked: a copy survives any export or filesystem that
    # treats links differently.
    for name in NetfilterPayload.BINARY_NAMES:
        assert f"{NetfilterPayload.MULTI_BINARY} /{name}" in recipe


def test_the_build_takes_no_platform_flag(tmp_path: Path) -> None:
    argv = NetfilterPayload(root=tmp_path).build_argv(tmp_path, docker_bin="docker")

    # Deliberately unlike the tmux payload, which CAN build a foreign
    # architecture after `up` has said what the container runs. This bundle is
    # consumed DURING `up` by the postStartCommand, so there is no "after" to
    # build in — a foreign-platform container falls through to the image's own
    # tools and then to the fail-closed refusal.
    assert "--platform" not in argv
    assert argv[0] == "docker"
    assert f"type=local,dest={tmp_path / 'out'}" in argv


def test_a_built_payload_short_circuits_the_build_without_spawning_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("build must not spawn when the cache is already served")

    monkeypatch.setattr("grove.core.container_netfilter.subprocess.run", _explode)
    payload = NetfilterPayload(root=_bundle(tmp_path))

    # Idempotence is what makes the create-path build cost one provision rather
    # than every provision.
    assert payload.build() is True


# ─── the script: a fallback, and its position relative to the refusal ───────


def _script(mode: str = "allowlist") -> str:
    return EgressPolicy.derive(EgressConfig(mode=mode), kind="claude_code").firewall_script()


def test_the_script_reaches_for_groves_bundle_only_when_the_image_has_none() -> None:
    script = _script()

    # A FALLBACK, never an override: an image with its own iptables keeps it,
    # because that binary matches the netfilter backend the rest of that image
    # (a nested dockerd included) was built against.
    assert "if ! command -v iptables >/dev/null 2>&1; then" in script
    assert f"for dir in {CONTAINER_NETFILTER_ROOT}/bin/*; do" in script


def test_the_bundle_is_selected_by_executing_it_not_by_naming_an_architecture() -> None:
    script = _script()

    # The payload directory is named by the BUILD host's `uname -m` and a
    # container can run a different one. Asking each candidate to state its
    # version needs no architecture map on either side and SKIPS a wrong-arch
    # binary instead of picking it and dying at `Exec format error`.
    assert '"$dir/iptables" --version >/dev/null 2>&1 || continue' in script


def test_the_bundle_lines_run_BEFORE_the_fail_closed_probe() -> None:
    script = _script()

    # Order is the whole point: probing first would refuse on exactly the image
    # the bundle exists to serve.
    assert script.index(str(CONTAINER_NETFILTER_ROOT)) < script.index('command -v "$bin"')


def test_open_mode_still_generates_no_script_at_all() -> None:
    assert _script(mode="open") == ""


def test_the_refusal_still_names_the_binary_and_both_remedies() -> None:
    script = _script()

    # The residual arm: an image missing `ip`, a container on an architecture
    # the host could not build for, or a host where the build could not run.
    assert "container.egress.mode = 'open'" in script
    assert "apt-get install -y iptables iproute2" in script
    assert "exit 1" in script


# ─── wiring: the mount actually reaches `up` ────────────────────────────────


def _cfg(tmp_path: Path, **container: object) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "container": {"enabled": True, **container},
            "agents": [{"name": "claude", "command": "claude", "kind": "claude_code"}],
        }
    )


def _manager(tmp_repo: Path, tmp_path: Path, cli: FakeCli, **container: object) -> WorkspaceManager:
    return WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path, **container),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=cli,
        preflight=FakePreflight(),
    )


def _create(manager: WorkspaceManager) -> WorkspaceState:
    return manager.create(CreateWorkspaceRequest(agent_name="claude", title="ws"))


def _override_mounts(state: WorkspaceState) -> list[str]:
    override = state.worktree_path / OVERRIDE_CONFIG_RELPATH
    return list(DevcontainerConfig.model_validate_json(override.read_text()).mounts)


def test_a_containerized_create_mounts_the_bundle_into_the_override_config(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    del fake_tmux
    _bundle(NetfilterPayload.resolve().root)

    state = _create(_manager(tmp_repo, tmp_path, FakeCli()))

    assert any(f"target={CONTAINER_NETFILTER_ROOT}" in m for m in _override_mounts(state))


def test_open_egress_neither_builds_nor_mounts_the_bundle(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    del fake_tmux
    _bundle(NetfilterPayload.resolve().root)

    state = _create(_manager(tmp_repo, tmp_path, FakeCli(), egress={"mode": "open"}))

    # `open` runs no firewall script, so there is nothing for the bundle to
    # serve — mounting it anyway would be inert machinery in someone's image.
    assert not any(str(CONTAINER_NETFILTER_ROOT) in m for m in _override_mounts(state))


def test_a_workspace_whose_host_has_no_bundle_still_provisions(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    del fake_tmux

    state = _create(_manager(tmp_repo, tmp_path, FakeCli()))

    # Best-effort by contract: an unbuilt bundle costs the mount, never the
    # provision. What happens next is the image's own tools, and failing those,
    # the script's fail-closed refusal.
    assert not any(str(CONTAINER_NETFILTER_ROOT) in m for m in _override_mounts(state))


# ─── the diagnosis: what the residual refusal is worth ──────────────────────


class RefusingCli(FakeCli):
    """An `up` whose lifecycle hook refuses, as a real one does.

    The CLI prints the hook's stderr through its progress stream and then fails
    with an error object naming only the COMMAND — which is exactly why the
    refusal has to be picked up from the stream rather than from the raise.
    """

    REFUSAL = (
        "grove: egress mode 'allowlist' needs 'iptables', which this container "
        "image does not provide"
    )

    def up(self, *args: Any, **kwargs: Any) -> Any:
        on_progress = kwargs.get("on_progress")
        if on_progress is not None:
            # Twice, as the real `sudo -n bash … || bash …` shape produces.
            on_progress(ProgressEvent(type="text", text=self.REFUSAL))
            on_progress(ProgressEvent(type="text", text=self.REFUSAL))
        raise DevcontainerError(
            "devcontainer up failed: Command failed: /bin/sh -c sudo -n bash "
            ".devcontainer/.grove-egress.sh || bash .devcontainer/.grove-egress.sh",
            container_id="c" * 64,
        )


def test_the_provision_error_carries_the_containers_own_refusal_and_the_log_path(
    tmp_repo: Path, tmp_path: Path
) -> None:
    provisioner = ContainerProvisioner(_cfg(tmp_path), repo_root=tmp_repo, cli=RefusingCli())

    with pytest.raises(DevcontainerError) as raised:
        provisioner.provision(
            workspace_id="ws1",
            worktree=tmp_repo,
            decision=RuntimeDecision(runtime=Runtime.CONTAINER),
            log_path=tmp_path / "provision.log",
        )

    message = str(raised.value)
    # Before this, the user got the `Command failed:` line alone while the
    # sentence naming the cause sat in a file the error never mentioned.
    assert RefusingCli.REFUSAL in message
    assert "full provision log:" in message
    # Deduped: the hook prints once per `sudo`/fallback arm.
    assert message.count(RefusingCli.REFUSAL) == 1
    # The id survives the re-raise — an `outcome:error` container still exists
    # on the host, and losing it here leaks one nothing can later find.
    assert raised.value.container_id == "c" * 64


def test_a_failed_create_tells_the_user_what_the_container_refused(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path, RefusingCli())

    with pytest.raises(GroveError) as raised:
        _create(manager)

    # The manager already appended the log's PATH and TAIL; on a real provision
    # that tail is the CLI's own Node stack trace, so the cause only reaches the
    # user because it is now part of the error's own message.
    assert RefusingCli.REFUSAL in str(raised.value)


# ─── doctor ─────────────────────────────────────────────────────────────────


def test_doctor_reports_the_bundle_but_never_gates_a_create_on_it(tmp_path: Path) -> None:
    preflight = HostPreflight(_cfg(tmp_path), devcontainer_cli=FakeCli(available=False))

    row = next(c for c in preflight.all_checks() if c.name == "container firewall")

    # `optional`, and this is the load-bearing assertion: a container-scoped
    # failure is what `RuntimeResolver` turns into a fall back to the HOST
    # runtime, so filing it there would strip real isolation from every project
    # whose image ships a perfectly good iptables — which doctor cannot see.
    assert row.required_for == "optional"
    assert row not in preflight.container_ready()
    assert row.ok is False
    assert "container.egress.mode = 'open'" in row.hint


def test_doctor_says_the_bundle_is_not_needed_when_egress_is_open(tmp_path: Path) -> None:
    preflight = HostPreflight(
        _cfg(tmp_path, egress={"mode": "open"}), devcontainer_cli=FakeCli(available=False)
    )

    row = next(c for c in preflight.all_checks() if c.name == "container firewall")

    assert row.ok is True
    assert "container.egress.mode = 'open'" in row.detail


def test_doctor_reports_a_built_bundle_with_its_version_and_location() -> None:
    payload = NetfilterPayload.resolve()
    _bundle(payload.root)

    assert payload.available is True
    assert NetfilterPayload.VERSION in payload.detail
    assert str(payload.root) in payload.detail


def test_the_container_root_is_a_sibling_of_groves_other_roots() -> None:
    # One `/grove` namespace inside somebody else's image, rather than
    # scattering into `/usr/local/bin` where it would shadow a package
    # manager's own installs and outlive any reasoning about who put it there.
    assert CONTAINER_NETFILTER_ROOT.parent == PurePosixPath("/grove")
