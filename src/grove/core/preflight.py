"""Host preflight — one owning class, two consumers.

The container feature stands on host dependencies Grove does not
install (docker daemon, compose v2, ``@devcontainers/cli``), and the
existing host-mode stack already silently assumes others (tmux, git). This
module is the single place that checks for all of them, so ``grove doctor``
(``tui/cli_doctor.py``) and the create path's arm-4 probe render the
SAME results instead of drifting.

Report, don't install: every failed check carries an actionable ``hint``;
Grove never provisions host software. Each check is exactly one bounded
subprocess/``which`` call (side effect at the edge); :class:`HostPreflight`
itself is pure aggregation over the results.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Literal

from pydantic import BaseModel

from grove.core.config import AgentKind, GroveConfig
from grove.core.container_netfilter import NetfilterPayload
from grove.core.container_tmux import TmuxPayload
from grove.core.devcontainer import DevcontainerCli

_TIMEOUT_SECONDS = 10.0
"""Bounded so a hung daemon/CLI can't hang `grove doctor` (or the create path)."""

#: Which AgentKinds run as a host-installed CLI Grove itself must find on
#: PATH. `generic` is user-defined (nothing to check); `mewbo` is a remote
#: orchestrator reached over HTTP, not a local binary.
_HOST_AGENT_KINDS: frozenset[AgentKind] = frozenset({"claude_code", "codex"})

RequiredFor = Literal["container", "host", "all", "optional"]
"""Which runtime a check GATES — ``optional`` gates nothing.

``optional`` exists because a reported fact and a required one are different
things, and conflating them here would be actively destructive: every
``container``-scoped failure is what :meth:`RuntimeResolver._unavailable_reason`
turns into a fall back to the HOST runtime. So filing "Grove's in-container tmux
bundle is not built yet" under ``container`` would silently strip a workspace of
its isolation over a missing *convenience*. An ``optional`` row is rendered, is
never in ``container_ready``/``host_ready``, and therefore never moves a create.
"""


class CheckResult(BaseModel):
    """One measured fact about the host, never a guess."""

    name: str
    ok: bool
    detail: str
    """The measured fact: version, path, or the error that was observed."""
    hint: str = ""
    """Actionable remedy. Empty when ``ok`` — a passing check needs no advice."""
    required_for: RequiredFor


class HostPreflight:
    """Runs the host-dependency checks the container + host runtimes need.

    Stateless aggregation over :meth:`_run` (docker/compose) and the reused
    :class:`~grove.core.devcontainer.DevcontainerCli.probe` — no check is
    reimplemented here that another module already owns.
    """

    def __init__(
        self, cfg: GroveConfig, *, devcontainer_cli: DevcontainerCli | None = None
    ) -> None:
        self._cfg = cfg
        self._devcontainer_cli = devcontainer_cli or DevcontainerCli()

    def all_checks(self) -> list[CheckResult]:
        """Every check, in the table's display order."""
        return [
            self._docker_daemon(),
            self._compose_v2(),
            self._devcontainer_cli_check(),
            self._git(),
            self._tmux(),
            self._container_tmux(),
            self._container_firewall(),
            *self._agent_clis(),
        ]

    def container_ready(self) -> list[CheckResult]:
        """The ``container`` subset — consumed by the create-path arm-4 probe."""
        return [c for c in self.all_checks() if c.required_for in ("container", "all")]

    def host_ready(self) -> list[CheckResult]:
        """The ``host`` subset — a host-mode create only needs these."""
        return [c for c in self.all_checks() if c.required_for in ("host", "all")]

    def default_runtime_checks(self) -> list[CheckResult]:
        """``all`` checks plus whichever runtime this config launches by default.

        The configured default runtime is ``container`` iff ``cfg.container.enabled``
        (the same switch the launch backend itself reads) — there is no separate
        "default runtime" field to invent; this reuses the one that already governs
        which `LaunchBackend` a create picks.
        """
        return self.container_ready() if self._cfg.container.enabled else self.host_ready()

    # ─── individual checks ──────────────────────────────────────────────

    def _docker_daemon(self) -> CheckResult:
        binary = self._cfg.container.docker_bin or "docker"
        if shutil.which(binary) is None:
            return CheckResult(
                name="docker daemon",
                ok=False,
                detail=f"{binary!r} not found on PATH",
                hint="install Docker: https://docs.docker.com/get-docker/",
                required_for="container",
            )
        result = self._run([binary, "info", "--format", "json"])
        if result is None:
            return CheckResult(
                name="docker daemon",
                ok=False,
                detail=f"`{binary} info` timed out or could not be run",
                hint="check the Docker daemon is running and responsive",
                required_for="container",
            )
        if result.returncode != 0:
            return CheckResult(
                name="docker daemon",
                ok=False,
                detail=self._tail(result.stderr),
                hint="start the Docker daemon (e.g. `systemctl start docker`, or Docker Desktop)",
                required_for="container",
            )
        server_version = "unknown"
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            server_version = str(payload.get("ServerVersion") or server_version)
        return CheckResult(
            name="docker daemon",
            ok=True,
            detail=f"server {server_version}",
            required_for="container",
        )

    def _compose_v2(self) -> CheckResult:
        binary = self._cfg.container.docker_bin or "docker"
        result = self._run([binary, "compose", "version"])
        if result is None or result.returncode != 0:
            detail = (
                self._tail(result.stderr)
                if result is not None
                else "`docker compose version` failed"
            )
            return CheckResult(
                name="compose v2",
                ok=False,
                detail=detail,
                hint=(
                    "install the Docker Compose v2 plugin: "
                    "https://docs.docker.com/compose/install/ "
                    "(only needed for devcontainer.json's dockerComposeFile projects)"
                ),
                required_for="container",
            )
        return CheckResult(
            name="compose v2", ok=True, detail=result.stdout.strip(), required_for="container"
        )

    def _devcontainer_cli_check(self) -> CheckResult:
        # Reused, not reimplemented — DevcontainerCli owns the binary name + probe shape.
        probe = self._devcontainer_cli.probe()
        if not probe.available:
            return CheckResult(
                name="@devcontainers/cli",
                ok=False,
                detail=f"{probe.binary!r} not found on PATH",
                hint=probe.INSTALL_HINT,
                required_for="container",
            )
        return CheckResult(
            name="@devcontainers/cli",
            ok=True,
            detail=probe.version or "present",
            required_for="container",
        )

    def _git(self) -> CheckResult:
        if shutil.which("git") is None:
            return CheckResult(
                name="git",
                ok=False,
                detail="'git' not found on PATH",
                hint="install git: https://git-scm.com/downloads",
                required_for="all",
            )
        result = self._run(["git", "--version"])
        detail = (
            result.stdout.strip() if result is not None and result.returncode == 0 else "present"
        )
        return CheckResult(name="git", ok=True, detail=detail, required_for="all")

    def _tmux(self) -> CheckResult:
        if shutil.which("tmux") is None:
            return CheckResult(
                name="tmux",
                ok=False,
                detail="'tmux' not found on PATH",
                hint="install tmux (e.g. `apt install tmux`, `brew install tmux`)",
                required_for="host",
            )
        result = self._run(["tmux", "-V"])
        detail = (
            result.stdout.strip() if result is not None and result.returncode == 0 else "present"
        )
        return CheckResult(name="tmux", ok=True, detail=detail, required_for="host")

    def _container_tmux(self) -> CheckResult:
        """Grove's in-container tmux bundle: is one on this host?

        ``optional`` by scope, and deliberately so — see :data:`RequiredFor`.
        An absent bundle costs a containerized agent its ability to survive a
        detached client; it never costs the workspace, and it must never be
        allowed to push a create onto the host runtime.

        Read-only: this reports what is on disk and never builds. A preflight
        that provisions would break its own contract, and the build belongs on
        the create path where a container is being provisioned anyway.

        The image's OWN tmux is invisible from here by construction — doctor
        inspects the host, and that is a property of somebody's image — so a
        not-ok row means "Grove has no fallback", never "this workspace will
        have no tmux".
        """
        cfg = self._cfg.container.tmux
        if not cfg.enabled:
            return CheckResult(
                name="in-container tmux",
                ok=True,
                detail="disabled (container.tmux.enabled = false)",
                required_for="optional",
            )
        payload = TmuxPayload.resolve(cfg)
        if payload.available:
            return CheckResult(
                name="in-container tmux",
                ok=True,
                detail=payload.detail,
                required_for="optional",
            )
        return CheckResult(
            name="in-container tmux",
            ok=False,
            detail=payload.detail,
            hint=(
                "Grove builds this on the next containerized create; until then a "
                "container whose image ships no tmux runs the agent without one, so "
                "the agent dies with its client"
                if payload.managed
                else f"point container.tmux.payload at a directory holding a static "
                f"{payload.BINARY_NAME!r} binary, or clear it to use Grove's own build"
            ),
            required_for="optional",
        )

    def _container_firewall(self) -> CheckResult:
        """Grove's static ``iptables`` bundle: is one on this host?

        ``optional`` by scope, for the same reason the tmux row is (see
        :data:`RequiredFor`) and with a sharper edge: a missing bundle is only
        fatal for an image that ships no ``iptables`` of its own, which doctor
        cannot see from the host — so filing it under ``container`` would push
        creates onto the HOST runtime for every project whose image is perfectly
        capable, trading real isolation for a fallback nobody needed.

        Read-only: reports what is on disk and never builds. The build belongs
        on the create path, where a container is being provisioned anyway.
        """
        if self._cfg.container.egress.mode == "open":
            return CheckResult(
                name="container firewall",
                ok=True,
                detail="not needed (container.egress.mode = 'open')",
                required_for="optional",
            )
        payload = NetfilterPayload.resolve()
        if payload.available:
            return CheckResult(
                name="container firewall",
                ok=True,
                detail=payload.detail,
                required_for="optional",
            )
        return CheckResult(
            name="container firewall",
            ok=False,
            detail=payload.detail,
            hint=(
                "Grove builds this on the next containerized create; until then an "
                "image that ships no iptables of its own cannot start a workspace at "
                f"container.egress.mode = '{self._cfg.container.egress.mode}' — install "
                "iptables in that image, or set container.egress.mode = 'open'"
            ),
            required_for="optional",
        )

    def _agent_clis(self) -> list[CheckResult]:
        """One check per distinct host-CLI agent binary declared in config.

        `AgentSpec.command`'s first token is the binary Grove actually invokes
        via tmux send-keys; container images install their own copy, so this is
        `host`-scoped only.
        """
        seen: dict[str, str] = {}
        for spec in self._cfg.agents:
            if spec.kind not in _HOST_AGENT_KINDS:
                continue
            binary = spec.command.split()[0] if spec.command.split() else spec.command
            seen.setdefault(binary, spec.name)
        results: list[CheckResult] = []
        for binary, agent_name in seen.items():
            found = shutil.which(binary)
            results.append(
                CheckResult(
                    name=f"agent CLI ({agent_name})",
                    ok=found is not None,
                    detail=found or f"{binary!r} not found on PATH",
                    hint=(
                        ""
                        if found
                        else f"install/configure {binary!r}, or remove the {agent_name!r} agent"
                    ),
                    required_for="host",
                )
            )
        return results

    # ─── shared subprocess boundary ──────────────────────────────────────

    @staticmethod
    def _run(argv: list[str]) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                argv,
                capture_output=True,
                text=True,
                check=False,
                shell=False,
                timeout=_TIMEOUT_SECONDS,
            )
        except (subprocess.SubprocessError, OSError):
            return None

    @staticmethod
    def _tail(stderr: str, *, lines: int = 5) -> str:
        """The last few stderr lines — enough to diagnose, short enough to print."""
        return "\n".join(stderr.strip().splitlines()[-lines:]) or "no stderr output"


__all__ = ["CheckResult", "HostPreflight", "RequiredFor"]
