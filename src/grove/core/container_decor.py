"""How a person attached to a container workspace can TELL they are in one.

One question: *what chrome does Grove put inside the container so the human
attached to it is not guessing where they are and what it costs?*

Three things were broken or absent, and they share one cause — the container is
somebody else's filesystem, so anything Grove wants visible in there has to be
carried in deliberately:

* The user's ``~/.claude/settings.json`` is bind-mounted ``:ro`` and its
  ``statusLine.command`` names a script beside it on the HOST that no mount
  carries — so the statusline silently vanishes in every container workspace.
  Silently is the operative word: Claude Code reports nothing, the bar is simply
  not there.
* Grove's in-container tmux ships no config at all, so its status bar is
  the bare default and carries no marker distinguishing it from the host tmux
  the same human is also attached to.
* ``/proc/loadavg`` is **not namespaced**: inside a container it reports the
  HOST's run queue. A workspace Grove explicitly caps (its own devcontainer runs
  ``--cpus=1.5 --memory=8g``) would render a number that looks like a
  measurement of itself and measures a machine it cannot see — worse than no
  number, which is why :mod:`resources.sh` reads the cgroup and says so in a
  comment.

So Grove ships its own assets as package data and bind-mounts them read-only at
:data:`CONTAINER_DECOR_ROOT`, the fourth Grove-owned root inside the container
alongside ``/grove/tmux``, ``/grove/control`` and ``/grove/netfilter``.

Deliberately NOT folded into ``container_policy``: that module answers the
blast-radius question (*what may this agent reach and consume*), and cosmetics
are not a security boundary. It is the decor sibling of ``container_tmux`` and
``container_netfilter``, and follows their shape — a payload that describes
where the assets are, a pure plan that turns configuration into mounts, and
:class:`~grove.core.container_policy.MountPlan` reused rather than re-invented.
Unlike either sibling it builds NOTHING: the assets are text in this package, so
there is no impure method here at all.

**Decor is cosmetic and must never fail a provision.** An incomplete payload
plans nothing and logs one warning; the workspace comes up looking like it did
before this module existed.

Dependencies flow inward: this imports ``container_policy`` only; the launch
composition imports it, never the reverse.
"""

from __future__ import annotations

import importlib.resources
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import ClassVar

from loguru import logger

from grove.core.container_policy import MountPlan

#: Where Grove's decor lands INSIDE the container. Sibling of ``/grove/tmux``,
#: ``/grove/control`` and ``/grove/netfilter``: a fourth thing Grove owns inside
#: somebody else's image, kept in the one namespace it already occupies rather
#: than scattered into ``/etc`` or the remote user's home, where it would fight
#: whatever the image or the user already put there.
CONTAINER_DECOR_ROOT = PurePosixPath("/grove/decor")


@dataclass(frozen=True, slots=True)
class DecorPayload:
    """The directory holding Grove's decor assets, on the host filesystem.

    Grove's own copy is package DATA — three short shell/tmux files versioned
    with the code that mounts them — so unlike the tmux and netfilter payloads
    there is nothing to build, nothing to cache, and no architecture to key
    on. That is the whole reason this class is small: the hard part of those
    two was obtaining a binary, and text has no equivalent.

    An operator may point ``container.decor.payload`` at their own directory
    (``managed=False``), which is why :attr:`available` inspects the filesystem
    rather than trusting the packaged tree — a curated directory missing an
    asset must degrade, not raise.
    """

    root: Path
    managed: bool = True
    """False for an operator-supplied directory. Grove never writes into either
    one, so this only shapes what :attr:`detail` says went wrong — "not packaged"
    and "the directory you configured is incomplete" send a reader to different
    places."""

    TMUX_CONF_NAME: ClassVar[str] = "tmux.conf"
    STATUSLINE_NAME: ClassVar[str] = "statusline.sh"
    RESOURCES_NAME: ClassVar[str] = "resources.sh"

    ASSET_NAMES: ClassVar[tuple[str, ...]] = (TMUX_CONF_NAME, STATUSLINE_NAME, RESOURCES_NAME)
    """Every asset a complete payload owes. ``resources.sh`` is in here despite
    having no plan field of its own: both other assets shell out to it, so a
    payload without it mounts a tmux bar and a statusline that each render one
    empty segment forever."""

    PACKAGE_DIR: ClassVar[str] = "decor"

    @classmethod
    def bundled(cls) -> Path:
        """The packaged decor directory inside the installed ``grove`` package.

        ``importlib.resources`` rather than ``Path(__file__).parent``, matching
        the ``BundledSkills.root()`` precedent this repo already set for package
        data — it is the seam that keeps working if the distribution is ever
        served from somewhere other than a plain directory on disk.
        """
        return Path(str(importlib.resources.files("grove.core") / cls.PACKAGE_DIR))

    @classmethod
    def resolve(cls, payload: str) -> DecorPayload:
        """The payload this configuration points at — operator's, else Grove's."""
        configured = payload.strip()
        if configured:
            return cls(root=Path(configured).expanduser(), managed=False)
        return cls(root=cls.bundled())

    def has(self, name: str) -> bool:
        return (self.root / name).is_file()

    @property
    def available(self) -> bool:
        """Whether every asset is present. All-or-nothing on purpose.

        A partial mount is the worst outcome available: ``tmux.conf`` present
        without ``resources.sh`` gives a status bar with a permanently blank
        segment, which reads as a broken feature rather than an absent one.
        """
        return all(self.has(name) for name in self.ASSET_NAMES)

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(name for name in self.ASSET_NAMES if not self.has(name))

    @property
    def detail(self) -> str:
        """One line fit for a preflight row or a provision log."""
        if self.available:
            return f"decor at {self.root}"
        origin = "packaged decor incomplete" if self.managed else "configured decor incomplete"
        return f"{origin} ({self.root}, missing {', '.join(self.missing)})"


@dataclass(frozen=True, slots=True)
class DecorPlan:
    """How one workspace's container gets its decor — described, not applied.

    Pure, like the planners in ``container_policy`` and
    :class:`~grove.core.container_tmux.TmuxRuntimePlan`: cascaded configuration
    plus an inspected payload in, a mount list and two composed strings out. The
    caller applies them — the mount into the override config, :attr:`tmux_conf`
    into the in-container tmux invocation, :attr:`statusline_command` into the
    seeded agent settings.

    :meth:`from_config` takes plain booleans rather than a config model so this
    module needs no ``config`` import — the same shape ``AgentSharePlan.plan``
    uses, and it keeps the planner testable over injected inputs rather than
    over a constructed cascade.
    """

    mounts: tuple[MountPlan, ...] = ()
    tmux_conf: PurePosixPath | None = None
    """The container-side path for ``tmux -f``; ``None`` when not composed."""

    statusline_command: str = ""
    """The container-side ``statusLine.command``; ``""`` when not composed."""

    #: Both assets are invoked as an ARGUMENT to ``sh``, never executed
    #: directly. A file's mode is not reliably preserved through a wheel build
    #: or an operator's ``cp``, and the failure mode of a lost execute bit is
    #: exactly the one this whole module exists to fix: chrome that looks
    #: configured and is inert, with nothing reporting it. ``tmux.conf`` applies
    #: the same rule where it shells out to ``resources.sh``.
    INTERPRETER: ClassVar[str] = "sh"

    @classmethod
    def from_config(
        cls, *, enabled: bool, statusline: bool, tmux_conf: bool, payload: DecorPayload
    ) -> DecorPlan:
        """Plan the decor for one workspace. Pure over an inspected payload.

        One mount carries the whole directory rather than a bind per asset: the
        assets reference each other by sibling path (both shell out to
        ``resources.sh``), so mounting them individually would let a future
        third asset be silently absent from a directory that otherwise looks
        complete inside the container.

        **Turning off every consumer turns off the mount**, and that is one gate
        at the one place that knows the policy rather than a courtesy. Gating
        only what each asset is USED for would leave a read-only directory of
        Grove's inside somebody else's image with nothing reading it — inert
        machinery that reads as a live feature to whoever finds it, which this
        subsystem has already shipped once with its own static firewall bundle.
        """
        if not enabled or not (statusline or tmux_conf):
            return cls()
        if not payload.available:
            logger.warning(
                "container decor: {} — the workspace will start with no container marker",
                payload.detail,
            )
            return cls()
        return cls(
            mounts=(MountPlan(source=payload.root, target=CONTAINER_DECOR_ROOT, readonly=True),),
            tmux_conf=(CONTAINER_DECOR_ROOT / DecorPayload.TMUX_CONF_NAME if tmux_conf else None),
            statusline_command=(
                f"{cls.INTERPRETER} {CONTAINER_DECOR_ROOT / DecorPayload.STATUSLINE_NAME}"
                if statusline
                else ""
            ),
        )

    @property
    def mount_flags(self) -> tuple[str, ...]:
        """Every mount as a ``--mount`` value, for the override config's array."""
        return tuple(mount.to_flag() for mount in self.mounts)

    @property
    def enabled(self) -> bool:
        """Whether anything is mounted at all.

        Deliberately keyed on the mounts rather than on the config flag: a
        workspace whose payload was incomplete is not "decor enabled", it is a
        workspace with no decor, and every consumer wants that answer.
        """
        return bool(self.mounts)


__all__ = ["CONTAINER_DECOR_ROOT", "DecorPayload", "DecorPlan"]
