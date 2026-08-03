"""How a containerized agent gets a tmux INSIDE its own namespace.

One question: *which tmux does this workspace's agent run under, and how does it
get there?* Before this, the multiplexer sat on the far side of the namespace
boundary from the process it multiplexed — the agent ran as
``devcontainer exec … -- claude`` typed into a HOST pane, so the PTY died with
the host client while the in-container process survived with no way back to it.
A tmux started inside the container owns the agent's lifetime instead, and the
host pane becomes a viewport onto it.

Deliberately NOT folded into ``container_policy``: that module answers *what the
agent shares, where it may reach, what it may consume* — the blast-radius
question. Which multiplexer the agent runs under is a runtime-plumbing question
with a different lifetime and a different owner, and a module that answers two
questions is the junk drawer this tree keeps warning about. It does reuse that
module's :class:`~grove.core.container_policy.MountPlan`, because "one bind
mount, described but not applied" is the same fact either way.

Five classes, in two groups — *getting* a tmux in there, and *talking to* it:

* :class:`TmuxPayload` — Grove's own static tmux + terminfo bundle on the HOST.
  Owns the build recipe (pure text), the cache location, and the one impure
  method that builds it.
* :class:`TmuxRuntimePlan` — the inert description of how that bundle (or the
  image's own tmux) reaches one workspace's container: the mounts to add, the
  env to export, and which binary the launch should finally invoke.
* :class:`TmuxEntry` — one ``new-session -A`` entry into that tmux, as tokens.
  Pure, and the piece with two consumers: the agent launch and the interactive
  shell (``grove shell`` / the attach layout's shell window) enter the SAME
  in-container server, differing only in session name and payload.
* :class:`ContainerTmux` — the READ/STEER side: the agent's pane
  now lives behind a namespace boundary, so observing and steering it is a
  ``docker exec`` away rather than a tmux fork away. Owns that argv and the one
  probe that answers whether the agent is still alive in it.
* :class:`ContainerPaneLiveness` — the memo in front of that probe, because the
  poll path asks it per workspace per tick and the answer is not free.

Dependencies flow inward: this imports ``config``/``paths``/``container_policy``/
``container_runtime``/``tmux``; the provisioner, the manager and the launch
backend import it, never the reverse.
"""

from __future__ import annotations

import platform
import shlex
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import ClassVar

from loguru import logger

from grove.core import paths, tmux
from grove.core.config import ContainerConfig, ContainerTmuxConfig
from grove.core.container_policy import MountPlan
from grove.core.container_runtime import ContainerRuntimeState, DockerCli
from grove.core.errors import ContainerError

#: Where Grove's tmux bundle lands INSIDE the container. Sibling of
#: ``/grove/agent-config`` and ``/grove/control``: a third thing Grove owns
#: inside somebody else's image, kept in the one namespace it already occupies
#: rather than scattered into ``/usr/local/bin``, where it would shadow a
#: package manager's own installs and outlive any reasoning about who put it
#: there.
CONTAINER_TMUX_ROOT = PurePosixPath("/grove/tmux")


@dataclass(frozen=True, slots=True)
class TmuxPayload:
    """Grove's static tmux + minimal terminfo bundle, on the host filesystem.

    **Built on demand into a host cache, by Docker.** The three ways to obtain a
    static binary were weighed and this is the boring one:

    * *Vendoring it in the repo/wheel* means a multi-megabyte opaque blob PER
      ARCHITECTURE, reviewed by nobody, shipped to every user including the ones
      who never touch containers.
    * *Downloading a prebuilt* needs an artifact host to publish, sign and keep
      alive forever; upstream tmux publishes no static build.
    * *Building it* needs a container runtime — which a container workspace
      **already hard-requires**, so it adds no dependency at all. It also makes
      the per-architecture problem a ``--platform`` flag rather than a matrix of
      artifacts to publish. And the recipe is auditable text in this file
      instead of a blob.

    The cost is one ~2-minute build, once per host per :attr:`VERSION` per
    architecture, paid inside a provision that is already pulling images. It
    degrades honestly: a build that cannot run leaves :attr:`available` False,
    the launch composes a bare ``exec``, and the workspace loses
    persistence rather than failing.

    **The cache is one directory per tmux version holding
    ``bin/<arch>/tmux`` beside a shared ``terminfo/``** — the layout an
    operator-supplied ``payload`` must also use. One root, because one container
    mount has to serve whatever platform the container turns out to run, and the
    architecture cannot be known before ``up``.

    The build recipe is code, not config, for the same reason a pinned
    dependency is: it is a reproducibility statement, and bumping it is a change
    a reviewer should see. What an operator might legitimately want to vary —
    *whether* to use it at all, and *whose* bundle — cascades from
    :class:`~grove.core.config.ContainerTmuxConfig`.
    """

    root: Path
    managed: bool = True
    """Whether Grove may BUILD this bundle. False for an operator-supplied
    ``container.tmux.payload``: a directory the operator curated is theirs, and
    silently overwriting it with Grove's own build would be the opposite of
    supplying one."""

    VERSION: ClassVar[str] = "3.5a"
    """The tmux release built. Also the cache key, so a bump rebuilds rather
    than serving a stale binary out of an identically-named directory."""

    BUILDER_IMAGE: ClassVar[str] = "alpine:3.20"
    """musl + the static libraries. Glibc cannot link a genuinely static binary
    that still resolves users and DNS; musl can, and tmux needs neither at
    runtime. A musl-static tmux runs unmodified on glibc images — verified on
    ``debian``, ``ubuntu`` and ``alpine`` bases."""

    TERMINFO_IMAGE: ClassVar[str] = "debian:trixie-slim"
    """A SECOND stage, purely to harvest terminfo — and the split is not
    gratuitous.

    Alpine's ``ncurses-terminfo`` is missing entries real people's terminals
    use: ``rxvt-unicode``/``rxvt-unicode-256color`` are absent from both 3.20
    (ncurses 6.4) and 3.22 (6.5), while Debian's ``ncurses-term`` carries them.
    Since the entries are just compiled data files copied into the payload,
    harvesting them from a distro that packages them properly costs one cached
    image and nothing else — and the stage is architecture-agnostic, so it is
    built once no matter how many platforms the binary is built for."""

    RELEASE_URL: ClassVar[str] = (
        "https://github.com/tmux/tmux/releases/download/{version}/tmux-{version}.tar.gz"
    )

    TERMINFO_SOURCES: ClassVar[tuple[str, ...]] = (
        "/usr/share/terminfo",
        "/lib/terminfo",
        "/etc/terminfo",
    )
    """Every terminfo tree in :attr:`TERMINFO_IMAGE`, unioned — the WHOLE database.

    **Shipping all ~1,800 entries is the smaller program even though it is the
    larger artifact, and that is the trade being made.** A curated subset saves
    ~1.5 MiB on a mount that is read-only, built once and shared by every
    workspace; it costs a maintained list of terminal names — policy baked into
    code, which this codebase forbids — plus a standing supply of "we forgot
    terminal X" bugs, each of which surfaces as a HARD attach refusal for that
    user. Measured: full database 1,814 entries / 2.06 MiB, a broad curated set
    244 entries / 482 KiB. No list, no policy, no future maintenance wins.

    Why terminfo is required at all, and why the CLIENT half is what forces
    breadth: ``new-session -d`` and ``capture-pane`` work with NO terminfo
    database, so a payload without one looks perfectly healthy until a client
    ATTACHES — and an absent entry for the human's own ``TERM`` makes tmux
    refuse outright (``missing or unsuitable terminal: <TERM>``), which is an
    unusable workspace rather than degraded colour. A minimal 11-entry bundle
    was measured refusing ``alacritty``, ``xterm-kitty``, ``foot``,
    ``xterm-ghostty`` and ``screen.xterm-256color``.

    **All three trees, unioned — sourcing only the biggest silently drops real
    entries.** Debian reports three defaults and the split is not by importance:
    ``/usr/share/terminfo`` holds the bulk from ``ncurses-term``, but
    ``screen.xterm-256color`` and ``rxvt-unicode-256color`` live ONLY in
    ``/lib/terminfo``. A bundle built from the bulk tree alone looks complete
    and is not.
    """

    TERMINFO_ALIASES: ClassVar[Mapping[str, str]] = {
        "xterm-kitty": "kitty",
        "xterm-ghostty": "ghostty",
    }
    """Filenames two terminals ANNOUNCE that no ncurses release packages.

    Not a curation list — the entries themselves are already in the full
    database above; this only gives them the name their terminal actually sends.
    kitty and ghostty ship their own terminfo as ``xterm-kitty`` /
    ``xterm-ghostty`` while ncurses packages the identical entries as ``kitty``
    / ``ghostty``, so without this the two most popular modern terminals fall
    through to the ``term_fallback`` substitution despite a perfectly good entry
    being present. Copying the compiled file under the announced name works
    because **ncurses resolves an entry by FILENAME and does not re-verify the
    name recorded inside it** (verified: ``k/kitty`` copied to ``x/xterm-kitty``
    answers ``tput -T xterm-kitty``). Recompiling with a real alias would need
    ``tic``, hence an ncurses runtime in a stage that otherwise only copies
    files.
    """

    BINARY_NAME: ClassVar[str] = "tmux"
    TERMINFO_DIRNAME: ClassVar[str] = "terminfo"
    BIN_DIRNAME: ClassVar[str] = "bin"

    MACHINE_ARCH: ClassVar[Mapping[str, str]] = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    """``uname -m`` (and :func:`platform.machine`) → the docker platform name.

    Both spellings of each appear in the wild — the kernel says ``x86_64`` /
    ``aarch64`` where docker says ``amd64`` / ``arm64``, and macOS says
    ``arm64`` for the same machine Linux calls ``aarch64``. An architecture
    absent from this map yields no binary at all, which degrades to the image's
    own tmux rather than to a binary that cannot execute.
    """

    @classmethod
    def host_arch(cls) -> str:
        """This machine's docker platform name, or ``""`` if unrecognized."""
        return cls.MACHINE_ARCH.get(platform.machine().lower(), "")

    @classmethod
    def resolve(cls, cfg: ContainerTmuxConfig) -> TmuxPayload:
        """The bundle this configuration points at — operator's, else Grove's."""
        configured = cfg.payload.strip()
        if configured:
            return cls(root=Path(configured).expanduser(), managed=False)
        return cls(root=paths.container_tmux_payload_dir(cls.VERSION))

    def binary_for(self, arch: str) -> Path:
        """The tmux built for *arch*.

        **The binary is per-architecture and the terminfo bundle is not**, which
        is why they sit in different places under one root. The binary executes
        inside the container, so an amd64 build cannot run in an arm64
        container at all — and the two are genuinely independent, since an
        Apple Silicon host routinely runs amd64 images under emulation and an
        amd64 host can be told to run arm64 ones. Compiled terminfo is
        capability DATA — escape strings and flags, nothing CPU-specific —
        verified by driving a real arm64 tmux under emulation against an
        x86-64-sourced bundle at 256 colours with a real SIGWINCH reflow. So
        building per-arch terminfo would be pure waste.
        """
        return self.root / self.BIN_DIRNAME / arch / self.BINARY_NAME

    @property
    def terminfo(self) -> Path:
        return self.root / self.TERMINFO_DIRNAME

    def has_binary(self, arch: str) -> bool:
        return bool(arch) and self.binary_for(arch).is_file()

    @property
    def has_terminfo(self) -> bool:
        return self.terminfo.is_dir()

    @property
    def available(self) -> bool:
        """Whether this host's own architecture is served. Never builds anything.

        Keyed to the HOST arch because that is what a provision produces up
        front and what ``grove doctor`` can honestly speak about — doctor
        inspects the host and cannot know what platform some future container
        will run. A foreign-arch container is served by a build the provisioner
        triggers once it knows, which is a different question from this one.
        """
        return self.has_terminfo and self.has_binary(self.host_arch())

    @property
    def detail(self) -> str:
        """One line fit for a preflight row or a provision log."""
        arch = self.host_arch() or "unknown arch"
        if self.available:
            return f"tmux {self.VERSION} ({arch}) at {self.root}"
        origin = "not built yet" if self.managed else "configured payload missing"
        return f"{origin} ({self.root}, {arch})"

    @classmethod
    def dockerfile(cls) -> str:
        """The build recipe, as text. Pure — testable without a Docker daemon.

        A ``scratch`` final stage exists so the whole build can be exported with
        ``--output type=local``: one command produces the files on the host,
        where the alternative (build an image, ``create`` a container, ``cp`` out
        of it, ``rm`` it) is four commands and three ways to leak a container.

        Three stages, because the two halves want different distros: a
        musl-static tmux needs alpine, and a complete terminfo database needs a
        distro that packages one (:attr:`TERMINFO_IMAGE`). The terminfo stage
        only ever copies files.
        """
        url = cls.RELEASE_URL.format(version=cls.VERSION)
        out = f"/out/{cls.TERMINFO_DIRNAME}"
        aliases = " ".join(f"{name}={source}" for name, source in cls.TERMINFO_ALIASES.items())
        return "\n".join(
            [
                f"FROM {cls.BUILDER_IMAGE} AS build",
                # `bison` is not optional despite tmux shipping a generated
                # parser: configure hard-fails with "yacc not found" before it
                # ever looks.
                "RUN apk add --no-cache bison build-base curl libevent-dev libevent-static "
                "ncurses-dev ncurses-static",
                "WORKDIR /src",
                f'RUN curl -fsSL -o tmux.tar.gz "{url}" && tar xzf tmux.tar.gz',
                f"WORKDIR /src/tmux-{cls.VERSION}",
                'RUN ./configure --enable-static CFLAGS="-static" LDFLAGS="-static" '
                '&& make -j"$(nproc)" && strip tmux',
                # A separate stage purely to COPY compiled terminfo out of a
                # distro that packages it fully. Nothing is recompiled: the
                # entries are already compiled data, `tic` would need an ncurses
                # runtime, and ncurses looks an entry up at
                # `<dir>/<first-char>/<name>` — so preserving the tree verbatim
                # IS the format.
                f"FROM {cls.TERMINFO_IMAGE} AS terminfo",
                "RUN apt-get update && apt-get install -y --no-install-recommends ncurses-term "
                "&& rm -rf /var/lib/apt/lists/*",
                f"RUN mkdir -p {out} "
                f"&& for dir in {' '.join(cls.TERMINFO_SOURCES)}; do "
                f'if [ -d "$dir" ]; then cp -R "$dir/." {out}/; fi; done '
                f"&& for pair in {aliases}; do "
                "name=${pair%%=*}; src=${pair#*=}; "
                'nf=$(printf %s "$name" | cut -c1); sf=$(printf %s "$src" | cut -c1); '
                f'if [ -f "{out}/$sf/$src" ]; then mkdir -p "{out}/$nf" '
                f'&& cp "{out}/$sf/$src" "{out}/$nf/$name"; '
                'else echo "grove: no terminfo entry $src to alias as $name" >&2; fi; done '
                f'&& echo "grove: bundled $(find {out} -type f | wc -l) terminfo entries" >&2',
                "FROM scratch AS payload",
                f"COPY --from=build /src/tmux-{cls.VERSION}/tmux /{cls.BINARY_NAME}",
                f"COPY --from=terminfo {out}/ /{cls.TERMINFO_DIRNAME}/",
                "",
            ]
        )

    def build_argv(
        self, context: Path, *, arch: str, docker_bin: str = "docker"
    ) -> tuple[str, ...]:
        """The one ``docker build`` that produces this bundle, into *context*.

        *context* holds the generated Dockerfile and doubles as the (empty)
        build context — the recipe copies nothing from the host, so there is
        nothing to send. The export destination is inside it rather than
        :attr:`root` itself, because a half-written cache is worse than an
        absent one: :meth:`build` moves the finished pieces into place.

        ``--platform`` is the ONLY thing that varies per architecture; the
        recipe itself is arch-agnostic (verified: the same Dockerfile builds a
        working aarch64 static tmux, 1,320,600 bytes against 1,308,336 for
        x86-64).
        """
        return (
            docker_bin,
            "build",
            "--platform",
            f"linux/{arch}",
            "--file",
            str(context / "Dockerfile"),
            "--target",
            "payload",
            "--output",
            f"type=local,dest={context / 'out'}",
            str(context),
        )

    def build(self, *, arch: str = "", docker_bin: str = "docker", timeout: float = 900.0) -> bool:
        """Build what *arch* needs if it is missing. Returns whether it is served.

        The single impure method here, and best-effort by contract: a failed
        build costs the workspace its in-container tmux (and therefore its
        persistence), never the workspace. Every failure is logged with the
        builder's own tail, because the remedy — a proxy, a missing buildkit, a
        moved upstream tarball — is only ever in that output.

        Concurrency-safe and idempotent: the export lands in a private temporary
        directory INSIDE the cache root (so the moves into place are same-
        filesystem renames rather than cross-device copies) and each piece is
        moved only if it is still missing, so two racing provisions cannot
        interleave and the loser simply finds the winner's artifact.
        """
        target = arch or self.host_arch()
        if not target:
            logger.warning(
                "container tmux: unrecognized architecture {!r}; no payload will be built",
                platform.machine(),
            )
            return False
        if self.has_terminfo and self.has_binary(target):
            return True
        if not self.managed:
            logger.warning(
                "container tmux: configured payload {} has no {}/{}/{}; the agent will fall "
                "back to the image's own tmux, or to no tmux at all",
                self.root,
                self.BIN_DIRNAME,
                target,
                self.BINARY_NAME,
            )
            return False
        if target != self.host_arch():
            # An emulated cross-build took ~4m25s when measured, against ~2min
            # native. Said out loud because the alternative is a user watching
            # a create sit there with no idea why.
            logger.info(
                "container tmux: building {} tmux under emulation on a {} host — "
                "this is slow, and happens once",
                target,
                self.host_arch() or "unknown",
            )
        logger.info("container tmux: building static tmux {} for linux/{}", self.VERSION, target)
        paths.ensure_dir(self.root)
        with tempfile.TemporaryDirectory(dir=self.root, prefix=".build-") as raw:
            context = Path(raw)
            (context / "Dockerfile").write_text(self.dockerfile(), encoding="utf-8")
            argv = self.build_argv(context, arch=target, docker_bin=docker_bin)
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
                logger.warning("container tmux: build could not run: {}", exc)
                return False
            if result.returncode != 0:
                logger.warning(
                    "container tmux: build failed: {}",
                    "\n".join(result.stderr.strip().splitlines()[-10:]) or "no output",
                )
                return False
            self._install(context / "out", arch=target)
        served = self.has_terminfo and self.has_binary(target)
        if served:
            logger.info("container tmux: built {}", self.binary_for(target))
        return served

    def _install(self, exported: Path, *, arch: str) -> None:
        """Move a completed export into the cache, piece by piece.

        Per-piece rather than wholesale because the two pieces have different
        lifetimes: the terminfo bundle is shared by every architecture and is
        written once, while a binary is minted per architecture into the same
        root. Anything already present is left alone — a concurrent provision
        that got there first is not a failure.
        """
        try:
            if not self.has_terminfo and (exported / self.TERMINFO_DIRNAME).is_dir():
                (exported / self.TERMINFO_DIRNAME).rename(self.terminfo)
            binary = exported / self.BINARY_NAME
            if not self.has_binary(arch) and binary.is_file():
                paths.ensure_dir(self.binary_for(arch).parent)
                binary.rename(self.binary_for(arch))
        except OSError as exc:
            # Lost a race, or a filesystem said no. The cache may be valid
            # anyway — the caller re-reads it rather than trusting this.
            logger.debug("container tmux: could not install build into {}: {}", self.root, exc)


@dataclass(frozen=True, slots=True)
class TmuxRuntimePlan:
    """How one workspace's container gets a tmux — described, not applied.

    Pure, like the planners in ``container_policy``: it turns cascaded
    ``container.tmux`` config plus the host bundle's availability into a mount
    list, an env map and a probe, and the provisioner applies them. The one
    thing it cannot answer alone is whether the IMAGE ships its own tmux —
    that is a fact about a container that does not exist until ``up`` has run,
    which is why :meth:`command_for` takes it as an argument rather than
    guessing.

    The plan is built BEFORE ``up`` (its mounts have to be in the override
    config) while the choice of binary is made AFTER (the probe needs a running
    container). So Grove mounts its whole cache ROOT — every architecture it has
    built, plus the shared terminfo — and only the SELECTION is deferred. Two
    consequences worth stating, because both are what make the ordering work at
    all: a mount that turns out unused because the image had its own tmux costs
    a few MB of read-only bind rather than a second ``up``, and a binary built
    *after* ``up`` (for a container whose architecture the host does not share)
    appears inside the container immediately, because a bind mount is a live
    view of the host directory rather than a copy.
    """

    enabled: bool
    prefer_image: bool
    mounts: tuple[MountPlan, ...] = ()
    env: dict[str, str] = None  # type: ignore[assignment]

    PROBE_COMMAND: ClassVar[tuple[str, ...]] = (
        "sh",
        "-c",
        "uname -m; if command -v tmux >/dev/null 2>&1; then echo tmux; fi",
    )
    """One exec answering both questions the selection needs: which architecture
    the container runs, and whether it already has a tmux.

    **The `if`/`fi` is load-bearing and must not be shortened back to `&&`.**
    A `;`-chained `sh -c` script exits with the status of its LAST command, and
    `command -v <missing>` exits **127** (POSIX "not found"), so the `&&` form
    makes the whole probe exit non-zero on precisely the images this mechanism
    exists to serve — every image that does not already ship tmux. A caller
    that gates on a zero exit before parsing would throw away the `uname -m`
    line sitting in the output, record "container arch unknown", and silently
    fall back to a bare exec: the entire in-container tmux mechanism inert, with
    the workspace still working and nothing logged as an error. `if`/`fi` yields 0
    when no branch is taken, so the exit status answers "did the probe run"
    rather than "was tmux found" — which is the only question the gate is
    entitled to ask, since absence of tmux is a legitimate ANSWER here, not a
    failure. Found by driving a real `grove create`; the unit tests could not
    see it because they script the boundary's return value and so never produced
    a 127.

    Run through ``devcontainer exec`` rather than ``docker exec`` on purpose —
    the agent is launched through the CLI too, so the probe sees the SAME remote
    user and therefore the same ``PATH``; a docker-level probe answers for the
    image's default user, which on a devcontainer is routinely not the one the
    agent runs as. And the architecture is read from INSIDE the container rather
    than inferred from the host or from a ``--platform`` flag, because that is
    the only place the answer is a fact.
    """

    #: What the image's own tmux is invoked as. A bare name, resolved through
    #: the remote user's PATH by the same shell that just proved it is there.
    IMAGE_COMMAND: ClassVar[str] = "tmux"

    #: ncurses reads this as a colon-separated search path in which an EMPTY
    #: entry means "then the compiled-in default". The trailing colon is
    #: therefore load-bearing: without it, mounting Grove's bundle would HIDE an
    #: image's own terminfo database rather than supplement it.
    TERMINFO_ENV: ClassVar[str] = "TERMINFO_DIRS"

    def __post_init__(self) -> None:
        if self.env is None:
            object.__setattr__(self, "env", {})

    @classmethod
    def from_config(cls, cfg: ContainerTmuxConfig, *, payload: TmuxPayload) -> TmuxRuntimePlan:
        """Plan the tmux runtime for one workspace. Pure over an inspected bundle."""
        if not cfg.enabled:
            return cls(enabled=False, prefer_image=cfg.prefer_image)
        if not payload.has_terminfo:
            # Nothing of Grove's worth mounting: a binary with no terminfo
            # cannot attach, which is the whole point. The image may still have
            # its own tmux, which `command_for` will find — not yet a
            # degradation.
            return cls(enabled=True, prefer_image=cfg.prefer_image)
        return cls(
            enabled=True,
            prefer_image=cfg.prefer_image,
            mounts=(MountPlan(source=payload.root, target=CONTAINER_TMUX_ROOT, readonly=True),),
            env={cls.TERMINFO_ENV: f"{CONTAINER_TMUX_ROOT / payload.TERMINFO_DIRNAME}:"},
        )

    @staticmethod
    def parse_probe(output: str) -> tuple[str, bool]:
        """``(container architecture, does the image have tmux)`` from the probe.

        Pure, so the selection logic is testable without a container — which is
        the only honest way to test it. An ``execve`` experiment cannot be: the
        kernel checks an ELF's machine type against the REAL host CPU, not the
        container's declared platform, so a foreign-arch binary bind-mounted
        into a nominally-foreign container runs natively on a same-arch host and
        the experiment passes for the wrong reason.

        An unrecognized architecture yields ``""`` — no Grove binary, degrade to
        the image's own tmux — rather than a guess that cannot execute.
        """
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        machine = lines[0].lower() if lines else ""
        return TmuxPayload.MACHINE_ARCH.get(machine, ""), "tmux" in lines[1:]

    @property
    def mount_flags(self) -> tuple[str, ...]:
        """Every mount as a ``--mount`` value, for the override config's array."""
        return tuple(mount.to_flag() for mount in self.mounts)

    def payload_command(self, arch: str) -> str:
        """The mounted binary for *arch*, or ``""`` when none is mounted."""
        if not self.mounts or not arch:
            return ""
        return str(CONTAINER_TMUX_ROOT / TmuxPayload.BIN_DIRNAME / arch / TmuxPayload.BINARY_NAME)

    def command_for(self, *, arch: str, image_has_tmux: bool) -> str:
        """The tmux binary the launch should invoke, or ``""`` for none.

        ``""`` is the honest answer for "this container cannot run tmux", and
        the launch backend composes a bare ``exec`` for it. That is
        the degradation the whole design is allowed to make: the workspace
        works, it just does not survive its client.
        """
        if not self.enabled:
            return ""
        if image_has_tmux and self.prefer_image:
            return self.IMAGE_COMMAND
        return self.payload_command(arch) or (self.IMAGE_COMMAND if image_has_tmux else "")


@dataclass(frozen=True, slots=True)
class TmuxEntry:
    """One ``tmux new-session -A -s <session>`` entry into a container, as tokens.

    Pure argv composition, and deliberately ignorant of *what* it is entering
    with: the agent launch enters with the agent command wrapped in an ``sh -c``
    that forwards its decoration, ``grove shell`` enters with an interactive
    shell. Both want the identical ``-A`` semantics, the identical session-name
    discipline and the identical TERM-fallback retry, so the composition lives
    once here rather than being re-derived per caller — the two would drift, and
    :meth:`fallback_script` is fifteen lines of measured behaviour that must not
    be copied.

    ``command == ""`` means the container has no reachable tmux at all, and the
    honest answer is then the target itself: the caller's command runs directly,
    dying with its client. That degradation is the whole design's escape hatch —
    the workspace works, it just is not persistent.
    """

    command: str
    """The tmux binary INSIDE the container (``ContainerRuntimeState.tmux_command``)."""

    session: str
    """The in-container session name ``-A`` keys on — a reattach identity."""

    term_fallback: str = ""
    """``TERM`` to retry a refused attach with; empty disables the retry."""

    detached: bool = False
    """Start the session without attaching a client (``-d``).

    The mode an *additional* agent is started in: the user asked for a second
    agent in this container, not to be dropped into it, and the
    same ``-A`` line attaches them later when they ask. It also disables the
    TERM-fallback wrapper by construction — that whole mechanism exists because
    a CLIENT can be refused a terminal, and a detached start has no client to
    refuse.
    """

    conf: str = ""
    """Grove's decor config INSIDE the container (``ContainerRuntimeState.tmux_conf``).

    Empty means the image's own defaults, which is what every workspace got
    before decor existed. Read off the RECORD rather than recomposed from config
    because the file only exists if the decor bundle was actually mounted at
    provision — ``tmux -f`` on a missing file is a hard start failure, so a
    launch that derived this path from config alone would turn a cosmetic
    feature into a broken workspace on any host whose bundle went missing. Same
    reasoning, and the same provision-time-fact shape, as :attr:`command`.
    """

    def tokens(self, target: Sequence[str]) -> list[str]:
        """The RAW tokens that run *target* under this session.

        Raw, never shell-quoted: a caller composing a tmux pane line quotes them
        itself, and a caller handing them to ``execvp`` must not. Quoting here
        would be wrong for exactly one of the two consumers, and silently.

        An EMPTY *target* is legitimate and means "attach to what is already
        there": ``-A`` attaches to an existing session and ignores any trailing
        command anyway, so a caller that has already established the session
        exists passes nothing rather than re-deriving a command tmux will throw
        away. If that session vanished in the race between the
        check and this exec, tmux creates an empty one running the image's
        default shell — a benign degradation, and the honest alternative to
        guessing which agent used to live there.
        """
        if not self.command:
            return list(target)
        detach = ("-d",) if self.detached else ()
        # `-f` is a SERVER option, so it has to precede the command word rather
        # than ride `new-session`'s own flags. Folding it into `args` keeps the
        # TERM-fallback retry honest for free: `fallback_script` re-emits this
        # same tuple, so the retry cannot lose the config the first attempt had.
        conf = ("-f", self.conf) if self.conf else ()
        args = (*conf, "new-session", "-A", *detach, "-s", self.session, *target)
        if not self.term_fallback or self.detached:
            return [self.command, *args]
        return ["sh", "-c", self.fallback_script(args), "grove"]

    def fallback_script(self, args: Sequence[str]) -> str:
        """Attach, and on a terminal refusal retry ONCE with a known-good ``TERM``.

        Structural, not defensive: several real terminals ship terminfo entries
        no ncurses release packages (``wezterm``, ``contour``, ``wayst``,
        depending on the distro), so no bundle can cover everyone — and the
        failure mode is not degraded colour but ``missing or unsuitable
        terminal: <TERM>``, an attach REFUSAL that leaves the workspace
        unusable. A substituted ``TERM`` was verified through a real pty with a
        real curses app to render, reflow on SIGWINCH, detach and reattach
        correctly.

        **The retry is gated on the session NOT existing, and that gate is what
        makes it safe.** ``-A`` creates when nothing is there, so a bare retry
        after a normal session end would silently RELAUNCH whatever the session
        was running. Measured: a refused client takes its whole server down with
        it, leaving no session and no server — so "no session" cleanly separates
        "never got a terminal" from "ran and then something else went wrong",
        and the latter's status is propagated untouched.

        The ``sleep`` is not padding. That dying server is still tearing its
        socket down, and a retry inside that window fails with ``server exited
        unexpectedly`` — deterministically, on every measured run, which makes
        it the difference between a fallback that works and one that only looks
        like it does. One second is ample (an immediate second attempt already
        succeeded 3/3) and is paid only on a path that is already degraded.

        A shell function rather than the invocation twice: the inner command is
        long, and two copies is how the two would come to differ.
        """
        invocation = " ".join(shlex.quote(token) for token in args)
        return "\n".join(
            [
                f"__grove_tmux={shlex.quote(self.command)}",
                f'__grove_launch() {{ "$__grove_tmux" {invocation} "$@"; }}',
                '__grove_launch "$@" && exit 0',
                f'"$__grove_tmux" has-session -t {shlex.quote(self.session)} 2>/dev/null && exit 1',
                'echo "grove: tmux could not start a client for TERM=$TERM; retrying once '
                f"with TERM={self.term_fallback} (set container.tmux.term_fallback to change or "
                'disable this)" >&2',
                f"TERM={shlex.quote(self.term_fallback)}",
                "export TERM",
                "sleep 1",
                '__grove_launch "$@"',
            ]
        )


@dataclass(frozen=True, slots=True)
class PaneReading:
    """One reading of a container workspace's agent pane.

    Two levels of absence, kept apart on purpose — the same distinction
    ``DockerCli.read_result`` draws and for the same reason: ``report is
    None`` means *tmux answered, and there is no such session* (the agent's
    server is gone), while a `PaneReading` that never arrives at all — the
    ``None`` :meth:`ContainerTmux.read` itself returns — means *docker could not
    be run*, which is no answer and must never be cached or acted on as death.
    """

    report: tmux.PaneReport | None

    @property
    def alive(self) -> bool:
        """Whether the agent's own process is still running in that pane."""
        return self.report is not None and not self.report.dead

    def age_seconds(self) -> int | None:
        """Seconds since the pane last produced output; ``None`` when unknown."""
        return self.report.activity_seconds_ago() if self.report is not None else None


@dataclass(frozen=True, slots=True)
class ContainerTmux:
    """The tmux server INSIDE one workspace's container — read it, steer it, end it.

    Moving the multiplexer across the namespace boundary made the agent
    survive its host client and left
    Grove with no way to look at or talk to what survived. Everything Grove does
    to that agent pane goes through here, and all of it is ``docker exec``
    against the container id ``up`` reported.

    **Why ``docker exec`` and not the devcontainer CLI.** The provision-time
    probe deliberately goes through ``devcontainer exec`` (only that road
    answers for the remote user's ``PATH``), and this deliberately does not.
    Measured on this host (Docker 29.6.1 + `@devcontainers/cli` 0.88.0, 10 runs
    each): ``devcontainer exec … capture-pane`` **484 ms
    median**, ``docker exec -u <user> … capture-pane`` **60.4 ms**, a host
    ``tmux capture-pane`` **3.5 ms**, and ``docker inspect`` (what liveness
    already pays) **14.4 ms**. A read on the poll path at half a second per
    workspace is not a cost question, it is a different product; at 60 ms with
    a memo in front of it (:class:`ContainerPaneLiveness`) it is affordable.
    The devcontainer CLI's own knowledge is not needed here anyway: the
    container id, the remote user and the tmux binary are all already recorded
    on :class:`~grove.core.container_runtime.ContainerRuntimeState`.

    Constructed only through :meth:`for_container`, which returns ``None`` for
    every workspace that has no in-container tmux to talk to — a host
    workspace, or a container whose image had no tmux and whose launch
    therefore composed a bare exec. That single ``None`` is what
    keeps host behaviour byte-identical: the caller never builds a reader, so
    nothing ever reaches for docker.
    """

    pane: tmux.TmuxPane
    """The agent pane, bound to the ``docker exec … <tmux>`` argv that reaches it."""

    #: How a container pane is rendered to humans and audit events. Deliberately
    #: NOT a bare session name: ``grove-x:agent`` is something a reader can hand
    #: to their own tmux, and the in-container session name is meaningless on
    #: this host, so it is labelled rather than passed off as attachable.
    LABEL_PREFIX: ClassVar[str] = "container"

    #: A read on a render path is bounded far tighter than a lifecycle command,
    #: for the reason :class:`~grove.core.container_runtime.ContainerLiveness`
    #: records: a wedged docker must cost one slow frame, never a stall in every
    #: surface that lists workspaces.
    READ_TIMEOUT_SECONDS: ClassVar[float] = 5.0

    @classmethod
    def for_container(
        cls,
        container: ContainerRuntimeState | None,
        *,
        cfg: ContainerConfig,
        session: str = "",
    ) -> ContainerTmux | None:
        """The reader for one agent pane in this container, or ``None`` if there is none.

        ``None`` for a host workspace (no container), for a container whose
        record names no tmux (``tmux_command`` empty — the honest degradation
        the launch already makes), and for a record whose id cannot legally name
        a container: ``exec_argv`` validates the full 64-char form for the same
        reason teardown does, and a reader is not a good enough reason to relax
        a rule that exists so Grove cannot address somebody else's container.

        *session* selects WHICH in-container session to read. Empty means the
        workspace's own agent (``container.tmux.session``), so the primary
        path is unchanged. A named
        one is an ADDITIONAL agent the user started in the same container: the
        reads and steers are identical, only the target differs, which is the
        whole reason multiple agents needed no second reader class.
        """
        if container is None or not container.tmux_command:
            return None
        target = session or cfg.tmux.session
        try:
            prefix = container.exec_argv([container.tmux_command], docker_bin=cfg.docker_bin)
        except ContainerError as exc:
            logger.warning("container tmux: cannot address this workspace's container: {}", exc)
            return None
        return cls(
            pane=tmux.TmuxPane(
                target=target,
                command=tuple(prefix),
                label=f"{cls.LABEL_PREFIX}:{container.short_id}:{target}",
            )
        )

    def read(self, *, docker: DockerCli | None = None) -> PaneReading | None:
        """What tmux says about the agent pane, or ``None`` if docker could not be run.

        Never raises — every caller is a render or reconcile path. One
        invocation answers liveness, the agent's exit status and the pane's last
        activity together (:attr:`tmux.PaneReport.FORMAT`), because at 60 ms an
        invocation is the unit of cost worth batching.
        """
        cli = docker if docker is not None else DockerCli(timeout=self.READ_TIMEOUT_SECONDS)
        argv = [
            *self.pane.command,
            "list-panes",
            "-t",
            self.pane.target,
            "-F",
            tmux.PaneReport.FORMAT,
        ]
        result = cli.read_result(argv)
        if result is None:
            return None
        if result.returncode != 0:
            # tmux answering "no server running" / "can't find session" IS the
            # answer: the agent's own tmux is gone. Distinct from the branch
            # above, which is docker never having run at all.
            return PaneReading(report=None)
        return PaneReading(report=tmux.PaneReport.parse(result.stdout))

    def list_sessions(
        self, *, docker: DockerCli | None = None
    ) -> tuple[tmux.SessionReport, ...] | None:
        """Every session on this container's tmux server, or ``None`` if unreadable.

        The enumeration behind "which agents are running in this container".
        It is deliberately a READ of the container rather than a field on the
        workspace record: the tmux server already knows, it is the
        only thing that can be right after an agent ends on its own, and a
        persisted list would be a migration plus a way to go stale.

        Answers about the SERVER, so it ignores :attr:`pane` ``target`` — the
        instance is reused only for the ``docker exec`` argv that reaches it.
        ``None`` keeps the same two-level meaning as :meth:`read`: docker never
        ran. A server with no sessions answers ``()`` through the non-zero-exit
        arm, which is the honest empty roster.
        """
        cli = docker if docker is not None else DockerCli(timeout=self.READ_TIMEOUT_SECONDS)
        argv = [*self.pane.command, "list-sessions", "-F", tmux.SessionReport.FORMAT]
        result = cli.read_result(argv)
        if result is None:
            return None
        if result.returncode != 0:
            return ()
        return tmux.SessionReport.parse_all(result.stdout)

    def end_session(self, *, docker: DockerCli | None = None) -> None:
        """Kill the agent session inside the container. Best-effort, never raises.

        Called before a relaunch when the previous agent left a DEAD pane behind
        (``remain-on-exit``). Without it the relaunch is inert and silently
        so: measured, ``tmux new-session -A`` against a session whose
        only pane is dead exits **1** and starts nothing, so ``resume`` and
        ``respawn`` would both "succeed" and leave the workspace looking at the
        corpse of the previous agent. Killing the session first is what keeps
        ``-A``'s attach-or-create honest once a pane can outlive its process.
        """
        cli = docker if docker is not None else DockerCli(timeout=self.READ_TIMEOUT_SECONDS)
        cli.read_result([*self.pane.command, "kill-session", "-t", self.pane.target])


class ContainerPaneLiveness:
    """The POLL path's reader of :meth:`ContainerTmux.read` — memoized.

    Same shape, same reasoning and same failure discipline as
    :class:`~grove.core.container_runtime.ContainerLiveness`, which memoizes
    ``docker inspect`` beside it; the two answer different questions about the
    same workspace (*is the container up* vs *is the agent alive in it*) and
    both run per workspace per poll, re-entered by the daemon's hook-ingest
    route on every agent event.

    **The window is longer than liveness's 5 s, and the arithmetic is the
    argument.** This read costs 60.4 ms against ``inspect``'s 14.4 ms
    (measured — see :class:`ContainerTmux`), so an identical window would make
    it four times the bill for an answer that ages far more slowly: an agent
    that has exited is not less exited ten seconds later, and the ACTIVE/IDLE
    edge it also feeds is judged against a 30 s activity threshold. At 24
    container workspaces this is ~14% of one core rather than ~29%.

    A ``None`` — docker could not be run — is never cached, so recovery is
    immediate rather than a window later. Expired entries are swept on each
    miss so a daemon running for days cannot leak one per container ever seen.
    No lock: the daemon reconciles from several executor threads and the worst a
    lost write costs is one extra fork.
    """

    TTL_SECONDS: ClassVar[float] = 10.0

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._docker = DockerCli(timeout=ContainerTmux.READ_TIMEOUT_SECONDS)
        self._cache: dict[tuple[str, ...], tuple[float, PaneReading]] = {}

    def reading_for(self, container_tmux: ContainerTmux) -> PaneReading | None:
        """This agent pane's reading, or ``None`` when it could not be read.

        Keyed by the argv itself — container id, remote user, tmux binary and
        session in one tuple — because that argv IS the identity of the thing
        being read, and a key derived from anything less could serve one
        workspace's answer to another after a rebuild.
        """
        key = (*container_tmux.pane.command, container_tmux.pane.target)
        now = self._clock()
        cached = self._cache.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]
        reading = container_tmux.read(docker=self._docker)
        if reading is None:
            return None
        self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
        self._cache[key] = (now + self.TTL_SECONDS, reading)
        return reading


__all__ = [
    "CONTAINER_TMUX_ROOT",
    "ContainerPaneLiveness",
    "ContainerTmux",
    "PaneReading",
    "TmuxEntry",
    "TmuxPayload",
    "TmuxRuntimePlan",
]
