"""How a container gets the ``iptables`` its egress policy needs.

One question: *the image ships no firewall tools — where do they come from?*

The most standard devcontainer base image there is,
``mcr.microsoft.com/devcontainers/base:ubuntu-24.04``, contains **no
``iptables`` at all** (measured). Grove's default egress allowlist is applied
as a ``postStartCommand`` under ``set -e``, so on that image the hook fails
and ``devcontainer up`` fails with it: with containers on by default, Grove's
*default configuration* could not start a workspace for a large class of
ordinary projects.

**Three directions were weighed and this is the one that makes the default
configuration WORK rather than merely explain itself.**

* *Preflight the image and refuse.* Honest, but the check cannot honestly run
  before ``up`` — the image may not exist yet (a ``build.dockerfile`` or a
  compose service), so "check first" degrades to "build first, then check", and
  the outcome is still a workspace the user cannot create. The refusal path is
  kept (:meth:`~grove.core.container_policy.EgressPolicy._preflight_lines`
  already fails closed and names the remedy) — it is the residual arm, not the
  answer.
* *Degrade to ``egress.mode: open``.* Refused outright. The container is the
  blast-radius boundary the whole autonomy posture rests on, and a workspace
  that quietly runs an autonomous agent with no egress policy is worse than one
  that fails to start.
* *Grove contributes the binaries.* Which is what this module does, reusing the
  same mechanism established for tmux verbatim: build a static binary once per
  host with Docker, cache it in the state dir, bind-mount it read-only, and let
  the container pick it up. A container workspace already hard-requires a
  container runtime, so the build adds no dependency — and unlike the tmux
  bundle (whose absence costs a convenience) this one is what stands between the
  user and a workspace that will not start at all.

Deliberately NOT folded into ``container_policy``: that module is three *pure*
planners plus two named create-time I/O methods, and a ``docker build``
subprocess does not belong in it. This module is the netfilter twin of
``container_tmux`` and follows its shape closely on purpose — one class, the
recipe as auditable text, one impure :meth:`NetfilterPayload.build`.

Dependencies flow inward: this imports ``paths`` and ``container_policy`` (for
:class:`~grove.core.container_policy.MountPlan`); the provisioner and the
preflight import it, never the reverse.
"""

from __future__ import annotations

import platform
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from loguru import logger

from grove.core import paths
from grove.core.container_policy import (
    CONTAINER_NETFILTER_BIN,
    CONTAINER_NETFILTER_ROOT,
    MountPlan,
)


@dataclass(frozen=True, slots=True)
class NetfilterPayload:
    """Grove's static ``iptables``/``ip6tables`` pair, on the host filesystem.

    Built on demand into a host cache by Docker, for the same three reasons the
    tmux bundle is (vendoring ships an opaque per-architecture blob to every
    user; a prebuilt needs an artifact host to publish and keep alive forever,
    and upstream netfilter publishes no static build; building needs only the
    container runtime a container workspace already requires).

    **A fallback, never an override.** The generated firewall script reaches for
    this bundle only when the image provides no ``iptables`` of its own. An
    image that ships one keeps it, because that binary matches the netfilter
    backend the rest of that image was built against — notably a nested
    ``dockerd``, whose own ``DOCKER-USER`` rules have to compose with Grove's.

    **No configuration knob, and that is a decision.**
    ``container.tmux.payload`` exists because that bundle carries a curated
    terminfo database an operator may legitimately want to own. This one is two
    copies of one binary with no policy inside it, and the opt-out that actually
    matters — ``container.egress.mode = "open"`` — already exists one config
    line away.

    Measured end to end on the reference host (Docker 29.6.1,
    ``@devcontainers/cli`` 0.88.0): a cold ``--no-cache`` build takes
    **15.6 s** and yields 575 KB per copy, and a real ``devcontainer up`` against
    ``mcr.microsoft.com/devcontainers/base:ubuntu-24.04`` — which contains no
    ``iptables`` at all — then succeeds with a 68-rule ``OUTPUT`` chain, the
    ``DOCKER-USER`` arm, the ``FORWARD`` jump and the ``ip6tables`` denial all
    applied, allowlisted destinations reachable and everything else blocked.
    That is a fraction of the image pull it sits beside, which is why it needs
    no progress plumbing of its own.
    """

    root: Path

    VERSION: ClassVar[str] = "1.8.10"
    """The netfilter release built. Also the cache key, so a bump rebuilds."""

    RELEASE_URL: ClassVar[str] = (
        "https://www.netfilter.org/projects/iptables/files/iptables-{version}.tar.xz"
    )

    BUILDER_IMAGE: ClassVar[str] = "alpine:3.20"
    """musl + static libs, the same builder the tmux payload uses. glibc cannot
    link a genuinely static binary; musl can, and the result runs unmodified on
    glibc images (verified on ``devcontainers/base:ubuntu-24.04``)."""

    MULTI_BINARY: ClassVar[str] = "xtables-legacy-multi"
    """Upstream builds one multi-call binary that dispatches on ``argv[0]``, so
    the payload is that file copied under each name below rather than a symlink:
    a copy survives any export, archive or filesystem that treats links
    differently, and half a megabyte twice is not worth the class of bug."""

    BINARY_NAMES: ClassVar[tuple[str, ...]] = ("iptables", "ip6tables")
    """Both, because the v6 arm of the policy is not optional — a
    workspace with ``iptables`` and no ``ip6tables`` fails closed just as hard."""

    BIN_DIRNAME: ClassVar[str] = CONTAINER_NETFILTER_BIN.name
    """Read off the container-side path the script globs, so the layout this
    builder writes and the directory that script walks cannot drift apart."""

    @classmethod
    def resolve(cls) -> NetfilterPayload:
        """The bundle for the current :attr:`VERSION`."""
        return cls(root=paths.container_netfilter_payload_dir(cls.VERSION))

    @staticmethod
    def host_machine() -> str:
        """This machine's own ``uname -m``, verbatim.

        Deliberately NOT normalized to a docker platform name the way the tmux
        payload's ``MACHINE_ARCH`` map does. Nothing here needs to *name* an
        architecture: the build has no ``--platform`` flag (it builds for this
        host, which is the only architecture it can serve, see :meth:`build`)
        and the container picks its directory by trying to *execute* what is in
        it. So the directory name is opaque, an unrecognized architecture is not
        a case to handle, and there is no second copy of that map to drift.
        """
        return platform.machine()

    def binary_dir(self, machine: str) -> Path:
        return self.root / self.BIN_DIRNAME / machine

    def has_binaries(self, machine: str) -> bool:
        return bool(machine) and all(
            (self.binary_dir(machine) / name).is_file() for name in self.BINARY_NAMES
        )

    @property
    def available(self) -> bool:
        """Whether this host's own architecture is served. Never builds."""
        return self.has_binaries(self.host_machine())

    @property
    def detail(self) -> str:
        """One line fit for a preflight row or a provision log."""
        machine = self.host_machine() or "unknown machine"
        if self.available:
            return f"iptables {self.VERSION} ({machine}) at {self.root}"
        return f"not built yet ({self.root}, {machine})"

    def mount_plan(self) -> MountPlan | None:
        """The read-only bind that carries this bundle into a container.

        ``None`` when nothing is built, so a provision that could not build one
        mounts nothing rather than an empty directory the script would then
        search in vain.

        The whole cache ROOT is mounted rather than one architecture's
        directory, for the same reason the tmux bundle mounts its root: the
        mount has to be in the override config before ``up``, while what the
        container can execute is only knowable after it. Here the container
        itself resolves that, so the extra directories are inert.
        """
        if not self.available:
            return None
        return MountPlan(source=self.root, target=CONTAINER_NETFILTER_ROOT, readonly=True)

    @property
    def mount_flags(self) -> tuple[str, ...]:
        """``--mount``-shaped values for the overlay, or ``()``."""
        plan = self.mount_plan()
        return (plan.to_flag(),) if plan is not None else ()

    @classmethod
    def dockerfile(cls) -> str:
        """The build recipe, as text. Pure — testable without a Docker daemon.

        A ``scratch`` final stage exists so the whole build exports with
        ``--output type=local``: one command puts the files on the host, where
        building an image and copying out of a container is four commands and
        three ways to leak one.

        Two recipe facts worth not rediscovering, both measured:

        * ``-static`` must reach the link through ``make LDFLAGS=-all-static``,
          not through ``configure``. libtool performs the final link and eats a
          plain ``-static`` (it reads it as "static against libtool libraries"),
          so the obvious ``LDFLAGS=-static`` yields a musl-*dynamic* binary that
          runs in the builder and dies with ``not found`` in an Ubuntu image —
          the failure looks like a missing file rather than a missing loader.
          Passing ``-all-static`` at ``configure`` time instead breaks
          configure's own compiler test (``cannot create executables``).
        * ``--disable-nftables`` selects the legacy backend, which is what
          removes the libmnl/libnftnl dependency from the static link. This
          bundle is only ever reached in an image with no ``iptables`` at all —
          therefore no nested ``dockerd`` with an opinion about the backend —
          so there is nothing for it to disagree with.
        """
        url = cls.RELEASE_URL.format(version=cls.VERSION)
        src = f"/src/iptables-{cls.VERSION}"
        copies = [
            f"COPY --from=build {src}/iptables/{cls.MULTI_BINARY} /{name}"
            for name in cls.BINARY_NAMES
        ]
        return "\n".join(
            [
                f"FROM {cls.BUILDER_IMAGE} AS build",
                "RUN apk add --no-cache bison build-base curl flex linux-headers",
                "WORKDIR /src",
                f'RUN curl -fsSL -o iptables.tar.xz "{url}" && tar xJf iptables.tar.xz',
                f"WORKDIR {src}",
                "RUN ./configure --disable-shared --enable-static --disable-nftables "
                "--disable-devel --disable-libipq --disable-connlabel",
                f'RUN make -j"$(nproc)" LDFLAGS="-all-static" && strip iptables/{cls.MULTI_BINARY}',
                "FROM scratch AS payload",
                *copies,
                "",
            ]
        )

    def build_argv(self, context: Path, *, docker_bin: str = "docker") -> tuple[str, ...]:
        """The one ``docker build`` that produces this bundle, into *context*.

        No ``--platform``: this builds for the host, which is the only
        architecture it can usefully serve. The tmux payload can build a foreign
        one after ``up`` has said what the container runs, because its mount is
        a live view of the cache; this bundle is consumed DURING ``up`` by the
        ``postStartCommand``, so there is no "after" to build in. A container on
        a foreign platform therefore finds nothing it can execute and falls
        through to the image's own tools, then to the fail-closed refusal —
        which is the honest outcome, not a silent one.
        """
        return (
            docker_bin,
            "build",
            "--file",
            str(context / "Dockerfile"),
            "--target",
            "payload",
            "--output",
            f"type=local,dest={context / 'out'}",
            str(context),
        )

    def build(self, *, docker_bin: str = "docker", timeout: float = 900.0) -> bool:
        """Build the bundle if it is missing. Returns whether it is served.

        The single impure method here, and best-effort by contract: a failed
        build leaves the image's own tools (or the fail-closed refusal) exactly
        as they were before this module existed. Every failure is logged with
        the builder's own tail, because the remedy — a proxy, a missing
        buildkit, a moved upstream tarball — is only ever in that output.

        Concurrency-safe and idempotent the same way the tmux build is: the
        export lands in a private temporary directory INSIDE the cache root, so
        the moves into place are same-filesystem renames, and each piece moves
        only if it is still missing — two racing provisions cannot interleave
        and the loser finds the winner's artifact.
        """
        machine = self.host_machine()
        if self.has_binaries(machine):
            return True
        logger.info("container egress: building static iptables {} for {}", self.VERSION, machine)
        paths.ensure_dir(self.root)
        with tempfile.TemporaryDirectory(dir=self.root, prefix=".build-") as raw:
            context = Path(raw)
            (context / "Dockerfile").write_text(self.dockerfile(), encoding="utf-8")
            argv = self.build_argv(context, docker_bin=docker_bin)
            try:
                result = subprocess.run(
                    list(argv),
                    capture_output=True,
                    text=True,
                    check=False,
                    shell=False,
                    timeout=timeout,
                )
            except (subprocess.SubprocessError, OSError) as exc:
                logger.warning("container egress: iptables build could not run: {}", exc)
                return False
            if result.returncode != 0:
                logger.warning(
                    "container egress: iptables build failed: {}",
                    "\n".join(result.stderr.strip().splitlines()[-10:]) or "no output",
                )
                return False
            self._install(context / "out", machine=machine)
        served = self.has_binaries(machine)
        if served:
            logger.info("container egress: built {}", self.binary_dir(machine))
        return served

    def _install(self, exported: Path, *, machine: str) -> None:
        """Move a completed export into the cache, binary by binary."""
        try:
            paths.ensure_dir(self.binary_dir(machine))
            for name in self.BINARY_NAMES:
                target = self.binary_dir(machine) / name
                source = exported / name
                if not target.is_file() and source.is_file():
                    source.rename(target)
                    target.chmod(0o755)
        except OSError as exc:
            # Lost a race, or a filesystem said no. The cache may be valid
            # anyway — the caller re-reads it rather than trusting this.
            logger.debug("container egress: could not install build into {}: {}", self.root, exc)


__all__ = ["NetfilterPayload"]
