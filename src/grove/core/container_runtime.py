"""Container lifecycle: the workspace's container identity and the verbs over it.

One question: *what container does this workspace own, and how is it paused,
resumed, inspected and destroyed without ever naming a container Grove did not
create?* :class:`ContainerRuntimeState` is the identity (Pydantic — it crosses
daemon/TUI/webapp/MCP) **and** the argv chokepoint; :class:`ContainerLifecycle`
sequences the verbs over one such identity, taking the docker CLI by injection;
:class:`ContainerLiveness` is the memoized read of the one verb a RENDER path
calls (``status``), and it exists because that path runs per workspace per poll.

Why the argv builders are methods on the state rather than free helpers: this
host runs dozens of unrelated containers, so the teardown invariant —

> No Grove teardown command may name a container it did not create.

— has to be *enforceable in one place* rather than remembered at every call
site. Every enumeration goes through :meth:`ContainerRuntimeState.filters`
(which always carries ``label=grove.managed=1``), every teardown through
:meth:`ContainerRuntimeState.teardown_argv` (which refuses anything but a full
64-character id or a compose project this workspace's own container reported).
``docker rm -f`` resolves its argument as a *prefix*, which is why the 12-char
short form is banned outright on teardown paths: log short, pass long.

**Ownership is recorded, never inferred from a name.** The compose arm
used to demand a reserved ``grove-`` prefix on the project name — but Grove does
not mint that name, the devcontainer CLI does, from the worktree basename plus
``_devcontainer``. The check could therefore never pass for any compose
devcontainer, so the conservative fallback was not a rare safety net: it was the
only arm that ever ran, and being label-filtered it could not see the stack's
siblings (the CLI id-labels the primary service only), its network, or its
volumes. A `mongo-1` with ``restart: unless-stopped`` outlived every `kill`.
The prefix is gone; :meth:`ContainerRuntimeState.from_up_result` now VERIFIES
the reported name against the ``com.docker.compose.project`` label on the
Grove-labelled container that ``up`` just produced. That is evidence rather than
convention — and it is strictly stronger, since a prefix would have trusted any
stack somebody else happened to name ``grove-*``. **Generalize: a conservative
fallback whose trigger condition is always true has quietly become the primary
path, and nothing reports it as such.**

A second invariant governs how the identity is FILLED, and violating it costs
exactly this:

> Every field here is what the CLI reported. Nothing about a container is
> recomputed from Grove's own guesses.

The nested-project bug is what recomputation costs — the agent's in-container
workdir was re-derived from a mount root Grove assumed, so a nested project
collapsed to the mount root, while ``up`` had reported the true
``remoteWorkspaceFolder`` all along (see :meth:`ContainerRuntimeState.workdir`).

Fail-closed everywhere the identity is uncertain: an ``up`` whose compose
project cannot be VERIFIED against the container it produced drops the
project-scoped path entirely and falls back to label-filtered per-container
removal (see :meth:`ContainerRuntimeState.from_up_result`), because a wrong
project name is a command that tears down somebody else's stack. That arm still
tears down everything it can see and says loudly what it could not reach —
"cannot tell" must never read as "safe to skip".

A third invariant governs what a recorded fact is allowed to claim:

> A provisioning success is a fact about ONE START of one container, never
> about the container.

``postStartCommand`` — the egress firewall — runs per start, so a container
somebody brings back with a bare ``docker start`` is a provisioned id with no
boundary in it. :attr:`ContainerRuntimeState.provisioned_start` records which
start the success applies to and :meth:`ContainerLifecycle.status` compares, so
the difference surfaces as ``UNPROVISIONED`` → OFFLINE → "respawn me".

**Why there is no ``docker events`` listener, having considered one.** The
tempting design is a long-lived stream that watches for a Grove-labelled
container starting and re-applies the firewall itself. It was rejected on three
counts, and the first is decisive. **The evidence here is durable, not
transient:** the container carries its own ``.State.StartedAt`` for as long as
it lives, so a comparison made at READ time answers correctly no matter who was
listening, whether the daemon was running, or how long ago it happened — a
listener would add a gap (events fire only while something is subscribed) that
the stateless comparison does not have, and would then need a
reconcile-on-startup pass to close the gap it introduced, i.e. exactly this
code. Second, "re-apply the firewall" is not a small repair: the hook is
composed into a generated override config that lives in the worktree, so the
only honest re-application is ``devcontainer up`` — which is ``respawn``, a verb
that relaunches the agent, cannot run for a PAUSED workspace (the worktree is
gone), and which Grove deliberately never triggers unattended. Third, a
background repairer racing the user's own verbs is a real amplification
hazard. So detection is wired here, at zero new processes, threads or
forks (one more field in a read that already happens), and repair stays the
existing user-triggered ``respawn`` — which genuinely re-applies the policy,
because the devcontainer CLI re-runs ``postStartCommand`` whenever its marker
disagrees with the container's current ``StartedAt`` (read off the 0.88.0
bundle and verified end to end).

Dependencies flow inward: this module imports ``errors`` (and ``devcontainer``
for annotations only — see the ``TYPE_CHECKING`` guard below); the
manager imports it, never the reverse. It is the ONLY module in ``src/grove``
allowed to invoke ``docker ps`` or ``docker compose`` — a grep test enforces it.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, ClassVar

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, model_validator

from grove.core.errors import ContainerError

if TYPE_CHECKING:
    # Annotation only, and the guard is load-bearing rather than tidy: this type
    # is a field on `contracts.views`, which `devcontainer` reaches through
    # `git` — a runtime import would close that cycle. Postponed annotations
    # (`from __future__ import annotations`) mean the hint is never evaluated.
    from grove.core.devcontainer import UpResult

#: Seconds Grove gives the agent to exit after it is signalled, and docker's own
#: ``-t`` grace on top of that. 30 s is docker's default and is kept for both.
#:
#: **What it guarantees and what it does not.** It is the budget for
#: :attr:`ContainerRuntimeState.SHUTDOWN_SCRIPT`, which signals the agent
#: process INSIDE the namespace and waits for its tmux session to end — that
#: wait is the one that actually elapses, and the only reason a pause can be
#: said not to truncate an agent mid-write. Passed to ``docker stop -t`` it
#: guarantees close to nothing, and the docstring used to claim otherwise:
#: it is an upper bound on how long docker waits for **PID 1**, and the
#: devcontainer CLI's entrypoint (``sh -c '… trap "exit 0" 15; exec "$@"; …'``)
#: traps SIGTERM and exits *at once* — measured 0.15 s against a nominal 30 s.
#: Worse, ``docker stop`` signals PID 1 only, while a process the CLI ``exec``ed
#: into the container reports ``PPID 0``: it is in the namespace but not in PID
#: 1's tree, so it was never sent anything and died of namespace teardown. Both
#: facts are independent, so tuning this number could never have fixed either.
#: Where there is no in-container tmux to address, that is still exactly what
#: happens — see :meth:`ContainerLifecycle.shutdown_agent`.
STOP_TIMEOUT_SECONDS = 30

_FULL_ID_LENGTH = 64


class InfrastructureScope(StrEnum):
    """The two lifetimes a Grove-managed container object can have.

    Encoded verbatim as the ``grove.scope`` label value on every object Grove
    creates, so a filter built from this enum and a filter built by a label READ
    always agree. It lives beside the label KEYS rather than with the
    infrastructure classes that consume it (``container_infra``) for the same
    reason the argv builders live on the identity: one vocabulary, one module,
    or the create path and the teardown path drift.
    """

    PROJECT = "project"
    """The prebuilt image. Outlives every workspace of the project; reclaimed
    only by removing the project."""

    WORKSPACE = "workspace"
    """The workspace's own container and volumes. Removed by ``kill``."""


class ContainerState(StrEnum):
    """The five mutually-exclusive substates of a workspace's container.

    Deliberately NOT merged into ``WorkspaceStatus``: this is the container
    axis. Equally deliberately NOT a field on
    :class:`ContainerRuntimeState` — it is what :meth:`ContainerLifecycle.status`
    RETURNS from a live read, never something persisted. A stored copy is stale
    the moment the user stops the container by hand, and a stale substate is
    worse than none because every surface renders it as current.
    """

    ABSENT = "absent"
    """No container with this identity exists (never created, or destroyed)."""

    RUNNING = "running"
    """Up, provisioned, and attachable — the only state that permits an exec."""

    STOPPED = "stopped"
    """The container exists but is not running (a pause, or a host reboot)."""

    UNPROVISIONED = "unprovisioned"
    """Running, but THIS start's lifecycle hooks never reported success.

    It earns its own state because it is the ONLY one that looks healthy to
    ``docker ps`` while the egress firewall is down and dependencies are half
    installed — exactly the shape an autonomous agent must not be let into.

    Two ways in, and the second is why the wording says *this start*.
    Provisioning may never have reported success at all
    (:attr:`ContainerRuntimeState.provisioned`), or it reported success for a
    **different start of the same container**: ``postStartCommand`` runs per
    start and in Grove's hardened configuration that hook *is* the egress
    firewall, so a container somebody brought back with a bare ``docker start``
    is running the agent's blast-radius boundary with no boundary in it. Both
    are the same fact — the environment is not the one Grove provisioned — and
    both take the same remedy, which is why this is one state rather than two.
    """

    MISSING_IMAGE = "missing_image"
    """No container and its image is not present locally — a rebuild, not a start."""


@dataclass(frozen=True, slots=True)
class ContainerObservation:
    """What ONE ``docker inspect`` of the just-created container reported.

    In-process state rather than a wire shape — a plain dataclass, not Pydantic:
    it lives for the few milliseconds between ``up`` and the mint, and nothing
    outside this module ever sees it. One read answers the three questions the
    mint cannot answer from ``up``'s own output, and answering them together is
    the point: three separate reads would be three forks, three failure modes,
    and three parameters on :meth:`ContainerRuntimeState.from_up_result`.

    * ``labels`` — which compose project the container itself says it is in, and
      whether it carries Grove's own ``grove.managed`` stamp.
    * ``mounts`` — which volumes have this container's lifetime.
    * ``image`` — what it actually runs, which ``up`` never reports.
    * ``started_at`` — WHICH START the provisioning that just succeeded
      applies to. Read here rather than in a fork of its own precisely because
      this observation already exists: the whole detection costs one more
      template field in a read that was happening anyway.

    Parsing lives here because the format string that produces it lives on the
    identity beside it; splitting the two is how a format change silently starts
    returning nothing while every consumer reads that as "there was nothing".
    """

    labels: Mapping[str, str] = field(default_factory=dict)
    mounts: tuple[Mapping[str, object], ...] = ()
    image: str = ""
    started_at: str = ""

    @classmethod
    def from_json(cls, raw: str | None) -> ContainerObservation | None:
        """Parse :attr:`ContainerRuntimeState.OBSERVE_FORMAT` output, or ``None``.

        ``None`` is the honest "could not tell", and it is deliberately NOT an
        empty observation: every consumer treats it conservatively (no volume is
        claimed, no compose project is trusted), whereas an empty value would
        read as "we looked and there was nothing there" — the difference between
        skipping a teardown because we are blind and skipping it because there is
        genuinely nothing to do.
        """
        if not raw or not raw.strip():
            return None
        try:
            payload = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        # Each field degrades on its own: docker renders absent labels as
        # `null`, and a partial read is still worth more than nothing.
        labels = payload.get("labels")
        mounts = payload.get("mounts")
        image = payload.get("image")
        started = payload.get("started")
        return cls(
            labels=(
                {str(key): str(value) for key, value in labels.items()}
                if isinstance(labels, dict)
                else {}
            ),
            mounts=(
                tuple(entry for entry in mounts if isinstance(entry, Mapping))
                if isinstance(mounts, list)
                else ()
            ),
            image=image if isinstance(image, str) else "",
            started_at=started if isinstance(started, str) else "",
        )


class ContainerRuntimeState(BaseModel):
    """One workspace's container identity, and every argv that may name it.

    ``None`` on a :class:`~grove.core.workspace.WorkspaceState` means host mode;
    a value means the workspace's agent lives in a container.

    Every field here is a DURABLE fact about the container's identity — what
    ``up`` reported, and what a later command may name. Nothing observable is
    stored, because a stored copy is a second source of truth that can disagree:
    :attr:`is_compose` is derived (``compose_project is not None`` — the mode
    discriminator), and the live substate is
    :meth:`ContainerLifecycle.status`'s return value rather than a field. It
    used to be a field, written once at provision and never refreshed, so a
    container the user stopped by hand still read RUNNING everywhere the record
    is rendered.
    """

    # Frozen as well as strict: every field is a fact `up` reported, and the
    # only legitimate "change" is minting a new record from a new result. It is
    # also what lets `contracts.views` SHARE this object with the engine record
    # instead of copying it.
    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Fields this model USED to persist, dropped on read instead of rejected.
    #:
    #: ``extra="forbid"`` is defense in depth — it is what makes "the live
    #: substate is never a persisted field" enforceable — but it also means
    #: DELETING a field breaks every record already on disk, and
    #: ``_decode_container`` re-validates loudly on purpose: a dropped container
    #: record orphans a real container nothing can later name. So a retirement
    #: has to be spelled out rather than silently tolerated, and it belongs on
    #: the model that owns its own schema history rather than in the store.
    #: Membership means "this key was ours and is not any more"; an unknown key
    #: Grove never wrote still fails loudly, which is the part worth keeping.
    RETIRED_FIELDS: ClassVar[frozenset[str]] = frozenset({"compose_services"})

    @model_validator(mode="before")
    @classmethod
    def _drop_retired_fields(cls, data: object) -> object:
        """Let a record written by an older Grove load without its retired keys."""
        if not isinstance(data, dict) or not cls.RETIRED_FIELDS & data.keys():
            return data
        return {key: value for key, value in data.items() if key not in cls.RETIRED_FIELDS}

    #: THE label vocabulary — every Grove-created container object carries all
    #: four, and every filter is built from them. Two questions, one label set:
    #: *identity* (managed + workspace: which workspace owns this?) and
    #: *lifetime* (managed + scope + project: when may this be collected?). They
    #: reconcile by UNION, not by choosing one — a workspace container is
    #: legitimately both, and stamping only the identity pair is what made
    #: ``grove.scope`` a label every teardown filter required and no created
    #: object carried.
    #:
    #: ``grove.managed`` is stamped by every path that creates a container —
    #: single-container via ``--id-label``, compose via labels on EVERY service
    #: of the generated override — so an enumeration filtered on it cannot see a
    #: foreign one.
    MANAGED_LABEL_KEY: ClassVar[str] = "grove.managed"
    MANAGED_LABEL_VALUE: ClassVar[str] = "1"
    SCOPE_LABEL_KEY: ClassVar[str] = "grove.scope"
    PROJECT_LABEL_KEY: ClassVar[str] = "grove.project"
    WORKSPACE_LABEL_KEY: ClassVar[str] = "grove.workspace"
    #: Compose's own project label. Teardown by project name is the one residual
    #: case that takes a *name* rather than an id, and this is what closes it:
    #: the name is trusted only when the Grove-labelled container ``up``
    #: produced says, itself, that it belongs to that project. The reserved
    #: ``grove-`` prefix this replaced was a convention Grove never got to
    #: apply — the CLI mints the name — so it rejected every real stack.
    COMPOSE_PROJECT_LABEL: ClassVar[str] = "com.docker.compose.project"
    #: The single ``docker inspect`` the mint runs, as a docker template that
    #: emits a JSON OBJECT rather than a tab-separated line: the mount table is
    #: itself JSON, so any in-band separator is a parse waiting to break on a
    #: path with a tab in it. Parsed by :meth:`ContainerObservation.from_json`.
    OBSERVE_FORMAT: ClassVar[str] = (
        '{"labels":{{json .Config.Labels}},'
        '"mounts":{{json .Mounts}},'
        '"image":{{json .Config.Image}},'
        '"started":{{json .State.StartedAt}}}'
    )
    #: The devcontainer spec's own per-container lifetime marker, and the ONLY
    #: signal :meth:`owned_volumes_in` trusts. A configuration that writes it
    #: into a mount source is declaring "mint this volume for THIS container" —
    #: the CLI substitutes a per-container hash at ``up`` time, so the resulting
    #: name is unreferenced the moment the container is gone. A stable name
    #: (``devc-<project>-uv``, a compose project's own volume) says the opposite,
    #: which is exactly the distinction a teardown has to make.
    DEVCONTAINER_ID_TOKEN: ClassVar[str] = "${devcontainerId}"
    #: The graceful shutdown, run INSIDE the container's own namespace.
    #:
    #: ``$1`` is the in-container tmux binary, ``$2`` the agent's session name,
    #: ``$3`` the seconds to wait. It signals the process group of every pane in
    #: that session and then waits for the session itself to disappear, which is
    #: tmux telling us the pane process is gone — not a ``kill -0`` poll, which
    #: a zombie awaiting its reaper would answer "alive" for the whole budget.
    #:
    #: **Why the signal cannot be delivered from outside.** ``docker stop``
    #: signals PID 1, and the agent is not in PID 1's tree (an ``exec``ed process
    #: reports ``PPID 0``), so no host-side docker verb can reach it — see
    #: :data:`STOP_TIMEOUT_SECONDS`. ``tmux kill-session`` runs in the namespace
    #: and would reach it, but sends SIGHUP and returns immediately; this script
    #: sends SIGTERM and *waits*, and only falls back to ``kill-session`` once
    #: the budget is spent, which is the same shape as docker's own grace.
    #:
    #: Scoped to the AGENT session on purpose. An interactive shell (the shell
    #: window, ``grove shell``) has nothing to flush, and bash ignores SIGTERM
    #: when interactive — including it would spend the entire budget on every
    #: pause waiting for a process that is never going to leave.
    #:
    #: The group form is tried first because the agent spawns children (tmux
    #: makes each pane process a session leader, so its pid is its group id) and
    #: falls back to the bare pid for a shell whose ``kill`` builtin reads a
    #: leading ``-`` as an option. Exit status answers "did the shutdown run",
    #: never "was anything found": an absent session is a legitimate answer.
    SHUTDOWN_SCRIPT: ClassVar[str] = """
tmux=$1
session=$2
deadline=$3
"$tmux" has-session -t "$session" 2>/dev/null || exit 0
for pid in $("$tmux" list-panes -s -t "$session" -F '#{pane_pid}' 2>/dev/null); do
  kill -TERM "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
done
waited=0
while [ "$waited" -lt "$deadline" ]; do
  "$tmux" has-session -t "$session" 2>/dev/null || exit 0
  sleep 1
  waited=$((waited + 1))
done
"$tmux" kill-session -t "$session" 2>/dev/null
exit 0
"""

    container_id: str = ""
    """The container's FULL 64-character id, verbatim from ``.Id``.

    Never truncated: ``docker rm -f`` resolves its argument as a prefix, so a
    12-char short form is a command that can match a container Grove never
    created. Empty only before the first successful ``up``.
    """

    image_ref: str = ""
    """The image the container runs, as reported by ``up``/``inspect``."""

    compose_project: str | None = None
    """The compose project name — **non-null IS the mode discriminator**.

    ``None`` for a single-container workspace, otherwise whatever the CLI
    reported, verified or not. Mode and OWNERSHIP are deliberately two fields
    (:attr:`compose_owned` is the second): conflating them is what made the
    conservative fallback silent, because a stack Grove could not vouch for read
    as "not a stack at all" and teardown reported a clean single-container
    removal while the rest of the stack kept running.
    """

    compose_owned: bool = False
    """Whether Grove may aim a PROJECT-SCOPED command at :attr:`compose_project`.

    True only when the name was verified at mint time against the
    ``com.docker.compose.project`` label on the Grove-labelled container ``up``
    produced. Ownership recorded at creation is knowledge; ownership inferred
    from a name pattern at teardown is a guess, and the ``grove-`` prefix this
    replaced was a guess Grove never even got to make — the CLI mints the name,
    so it was wrong for every real stack. False degrades teardown to
    label-filtered per-container removal, which reaches only the primary
    service, and says so out loud.
    """

    remote_user: str = ""
    """The user the agent runs as inside the container, as ``up`` reported it."""

    remote_workspace_folder: str = ""
    """The workspace folder as the container sees it, as ``up`` reported it.

    The root :meth:`workdir` resolves the agent's cwd against — never a mount
    root Grove assumed."""

    config_path: str = ""
    """The devcontainer.json this container was created from (host path)."""

    override_config_path: str | None = None
    """The generated complete override config, when one was used."""

    config_hash: str = ""
    """Identity of the resolved configuration — prebuild key and drift input.

    Drift is computed **on demand** (``grove show``, the status detail endpoint,
    ``respawn --rebuild``) by recomputing the hash and comparing; nothing
    recomputes it on a timer and nothing auto-rebuilds, because rebuilding
    destroys a running agent's container because somebody saved a file.
    """

    provisioned: bool = False
    """Whether lifecycle provisioning reported ``outcome:success`` for THIS id.

    Stored against the container id it applies to (a rebuild mints a new id and
    resets it), which is what keeps :attr:`ContainerState.UNPROVISIONED` honest.
    An id is necessary and NOT sufficient — see :attr:`provisioned_start`.
    """

    provisioned_start: str = ""
    """The container's ``.State.StartedAt`` at the moment provisioning succeeded.

    **The witness that binds provisioning to a START rather than to an id.**
    ``postStartCommand`` runs on every start of a container, and in
    Grove's hardened configuration that hook *is* the egress firewall — so an
    id-scoped :attr:`provisioned` answered "yes" for a container somebody
    stopped and brought back with a bare ``docker start``, which runs no hook
    at all. That workspace inspected RUNNING, reconciled healthy, and Grove
    would have let a permission-skipping agent into a blast-radius boundary
    with no egress policy in it. ``--restart no`` (asserted post-``up``, see
    :meth:`restart_policy_argv`) closes only the docker-INITIATED path; a human
    or a script running ``docker start`` is an entirely ordinary thing to do to
    a stopped container and no create-time property can prevent it.

    **Why an identity and not a timestamp comparison.** The obvious form is
    "when did Grove provision" measured against ``.State.StartedAt``, which
    makes the answer depend on two clocks agreeing — Grove's host clock and the
    docker daemon's, which are the same clock only until someone points
    ``DOCKER_HOST`` at a remote engine. Recording docker's OWN value for the
    start Grove provisioned turns an ordering question into an equality one:
    both sides of the comparison are produced by the same clock, and any
    restart necessarily changes the value (verified against Docker 29.6.1:
    a stop keeps the old value, a ``start`` writes a new one).

    ``""`` means Grove cannot tell which start was provisioned — a record
    written before this field existed, or a mint whose ``docker inspect`` could
    not be read. :meth:`ContainerLifecycle.status` treats that as
    UNPROVISIONED, deliberately: this whole field exists because a container
    that may have no firewall must not read attachable, and "cannot tell" is
    exactly that case. The cost is one ``respawn`` per pre-existing container
    workspace, which re-provisions and records the witness; the alternative is
    shipping the fix and leaving every workspace that already exists exposed.

    **Adding this field is a MIGRATION** — the model is ``extra="forbid"`` and
    ``_decode_container`` re-validates loudly — but the *additive* direction is
    the safe one: a default lets every record already on disk load unchanged.
    :attr:`RETIRED_FIELDS` is the seam for the other direction; nothing goes
    there for an addition. Being a Pydantic field it is also a WIRE change
    (``WorkspaceView.container`` ships it to daemon clients), additive and
    optional, so ``webapp/lib/grove/types.gen.ts`` needs regenerating.
    """

    id_labels: dict[str, str] = Field(default_factory=dict)
    """The identity labels ``up``/``exec``/enumeration all agree on."""

    tmux_conf: str = ""
    """Grove's decor tmux config INSIDE this container, or ``""`` for none.

    A provision-time fact for the same reason :attr:`tmux_command` is: whether
    the decor bundle was mounted depends on config AND on whether the assets
    were actually present on the host, and only the provision knows both. The
    launch must not re-derive it from config, because ``tmux -f`` on a path that
    is not there fails the start outright — so a cosmetic feature would take the
    workspace with it on any host missing its bundle.

    Additive with a default, which is the safe direction of this model's
    ``extra="forbid"`` migration rule: every record already on disk loads
    unchanged. Being a Pydantic field it also ships on the wire through
    ``WorkspaceView.container``, so ``webapp/lib/grove/types.gen.ts`` is
    regenerated in the same change.
    """

    tmux_command: str = ""
    """The tmux binary the agent is launched under INSIDE this container.

    ``""`` means none is reachable, and the launch composes a bare
    ``exec`` — a workspace whose agent dies with its client, which is a
    degradation and never a failure.

    This is a durable fact rather than an observation, and the distinction is
    the one this class's docstring draws. It is a property of the IMAGE (does it
    ship tmux?) crossed with what Grove mounted, both of which are fixed for the
    lifetime of a container id: a rebuild mints a new record and re-probes, and
    every re-provision (resume, respawn) runs the probe again. What it is NOT is
    live substate — nothing about whether a tmux server is currently running is
    stored, for exactly the reason ``status`` stopped being a field.

    Recorded rather than re-derived at launch because the answer needs a RUNNING
    container to probe, and the launch backend has only a command line to
    compose. Deciding it in-shell (``$(command -v tmux || echo …)``) was the
    alternative and was rejected: it hides the decision from the provision log
    and from anyone reading the pane, for no gain the probe does not already
    give.
    """

    owned_volumes: list[str] = Field(default_factory=list)
    """Volumes whose lifetime is THIS container's, removed by name on teardown.

    Named explicitly because ``compose down`` is NEVER invoked with ``-v``: a
    project's own declared volumes may hold a database the user cares about, and
    a teardown flag cannot tell those apart from ours. Only what Grove minted is
    Grove's to delete.

    Filled at the one mint site by :meth:`owned_volumes_in`, from the container's
    real mount table. It was a field nothing ever wrote — the guard below and
    the teardown loop were both correct and both iterated
    an empty list, so every container workspace leaked its per-container volume
    (the docker-in-docker feature's ``dind-var-lib-docker-*``, which holds the
    nested daemon's entire image store, is gigabytes). **A safety guard on a path
    no producer reaches reads as "handled" in review and in a green suite while
    the behaviour is "never happens"** — the tests passed because they injected
    the state the production path never produced. Test the PRODUCER, not just the
    guard, whenever a guard is the whole design.
    """

    # ─── derived facts (never stored) ──────────────────────────────────────

    @property
    def is_compose(self) -> bool:
        """Whether this workspace is a compose stack rather than one container.

        The MODE, independent of whether Grove may act on the stack — see
        :attr:`compose_owned` for that. They used to be one field, so an
        unverifiable stack rendered everywhere as a plain container.
        """
        return self.compose_project is not None

    def provisioned_for(self, started_at: str) -> bool:
        """Whether the start *started_at* names is the one Grove provisioned.

        Pure, and on the identity rather than in the lifecycle for the reason
        every argv builder is here: the rule is what makes a fact about a
        container safe to act on, so it belongs with the fact. The lifecycle
        supplies the live reading; this decides what it means.

        False for an EMPTY witness on either side — an unreadable start is not
        a matching one. That is the fail-closed direction and it is the point:
        the consequence of a false negative is a workspace that asks for a
        ``respawn`` it did not strictly need, the consequence of a false
        positive is an autonomous agent inside an unfirewalled container.
        """
        return bool(self.provisioned_start) and started_at == self.provisioned_start

    @property
    def short_id(self) -> str:
        """The 12-char form — for LOGS ONLY. Never pass this to a command."""
        return self.container_id[:12]

    @property
    def override_config(self) -> Path | None:
        """The complete config ``up`` used, as a path — an exec MUST agree with it.

        On the identity rather than on each caller because "which configuration
        is this container's" is a fact about the container, and every exec into
        it (the agent launch, an interactive shell) has to answer it the same
        way or the CLI resolves a different container than the one Grove made.
        """
        return Path(self.override_config_path) if self.override_config_path else None

    def workdir(self, *, worktree: Path, cwd: Path) -> str:
        """The agent's cwd INSIDE the container — reported root plus relative offset.

        :attr:`remote_workspace_folder` is the CLI's own answer, never a guess,
        and the agent cwd's path is taken relative to the worktree ROOT — which
        is why the launch spec carries both and never conflates them. Re-deriving
        this from a mount root Grove assumed is exactly what collapsed a nested
        project to the mount root. A cwd outside the worktree (impossible today,
        cheap to be honest about) degrades to the workspace folder rather than
        leaking a host-absolute path into the container.
        """
        return self.container_path(worktree=worktree, path=cwd)

    def container_path(self, *, worktree: Path, path: Path) -> str:
        """Any host path under the worktree, as the CONTAINER sees it.

        :meth:`workdir` is this question asked about the agent's cwd; the
        phase file asks it about the agent's phase file, which is a plain file
        under the same bind-mounted worktree. Generalized rather than copied,
        because the one thing that must not be re-derived is the
        ``remote_workspace_folder`` anchor — recomputing a mount root Grove
        assumed is exactly the bug this method exists to avoid.
        """
        root = self.remote_workspace_folder
        try:
            rel = path.resolve().relative_to(worktree.resolve())
        except ValueError:
            return root
        if not rel.parts:
            return root
        return str(PurePosixPath(root) / PurePosixPath(rel.as_posix()))

    @classmethod
    def labels_for(cls, workspace_id: str, *, project_slug: str = "") -> dict[str, str]:
        """The labels a Grove workspace container is created with — all four.

        The create path stamps these (``--id-label`` per entry, or onto every
        service of the generated compose override); every later enumeration
        filters on them. One producer so the two can never disagree — and the
        set has to be the UNION, because the teardown filters ask about scope
        while the exec/adoption paths ask about workspace identity.

        *project_slug* is optional only so a caller reasoning purely about
        identity (a test, a fallback re-mint from an enumerated id) need not
        invent one; the create path always passes it, and an empty value simply
        stamps an empty project label rather than dropping the key — a missing
        key would make the object invisible to a project-scoped sweep, which is
        the failure mode this whole method exists to prevent.
        """
        return {
            cls.MANAGED_LABEL_KEY: cls.MANAGED_LABEL_VALUE,
            cls.SCOPE_LABEL_KEY: InfrastructureScope.WORKSPACE.value,
            cls.PROJECT_LABEL_KEY: project_slug,
            cls.WORKSPACE_LABEL_KEY: workspace_id,
        }

    @staticmethod
    def filter_flags(**labels: str) -> list[str]:
        """THE ``--filter label=k=v`` producer — every docker query is built here.

        A `@staticmethod` on the type that owns the label NAMES, so a query can
        never be assembled from a vocabulary that has drifted from what the
        create path stamps. Callers pass ``**{KEY: value}`` because the keys are
        dotted.
        """
        flags: list[str] = []
        for key, value in labels.items():
            flags += ["--filter", f"label={key}={value}"]
        return flags

    @classmethod
    def managed_filter(cls) -> list[str]:
        """The ``grove.managed=1`` anchor, alone — every enumeration starts here.

        Named because "anchor the query on the managed label" is asserted in
        several places and spelling the key/value pair out at each one is how a
        query eventually ships without it.
        """
        return cls.filter_flags(**{cls.MANAGED_LABEL_KEY: cls.MANAGED_LABEL_VALUE})

    # ─── compose-stack ownership (evidence, never a name pattern) ──────────

    @classmethod
    def owns_project(
        cls,
        reported: str | None,
        *,
        expected: str | None = None,
        observed: ContainerObservation | None = None,
    ) -> bool:
        """Whether this workspace may aim a project-scoped command at *reported*.

        Four refusals, each of which used to be — or would have been — a way to
        aim ``compose down`` at somebody else's stack:

        * ``up`` reported no project at all → a single-container workspace.
        * A re-provision reported a DIFFERENT project than the record already
          holds. An idempotent ``up`` does not rename a stack, so a change here
          means the identity moved under us.
        * The container could not be read. This is the "cannot tell" arm, and it
          is loud rather than silent: teardown still removes what the labels can
          see, and the log names what it could not reach.
        * The container ``up`` produced does not carry Grove's own
          ``grove.managed`` stamp, or does not itself claim membership of the
          reported project. Either way the CLI's report is not corroborated by
          the only object Grove can prove it created.

        Only the last check is new machinery; it is also the whole fix. Grove
        does not mint the project name — the CLI derives it from the worktree
        basename — so the reserved-prefix test this replaced could never pass,
        and the "conservative" fallback was the only arm that ever ran.
        """
        if reported is None:
            return False
        if expected is not None and reported != expected:
            logger.warning(
                "container: devcontainer reported compose project {!r}, expected {!r} — "
                "refusing project-scoped teardown for this workspace",
                reported,
                expected,
            )
            return False
        if observed is None:
            logger.warning(
                "container: could not read the container behind compose project {!r} — refusing "
                "project-scoped teardown; its sibling services, network and volumes will be left "
                "on the host for you to remove",
                reported,
            )
            return False
        if observed.labels.get(cls.MANAGED_LABEL_KEY) != cls.MANAGED_LABEL_VALUE:
            logger.warning(
                "container: the container behind compose project {!r} carries no {} label — "
                "refusing project-scoped teardown for a stack Grove cannot prove it created",
                reported,
                cls.MANAGED_LABEL_KEY,
            )
            return False
        actual = observed.labels.get(cls.COMPOSE_PROJECT_LABEL)
        if actual != reported:
            logger.warning(
                "container: devcontainer reported compose project {!r} but its own container is "
                "labelled {!r} — refusing project-scoped teardown",
                reported,
                actual,
            )
            return False
        return True

    # ─── volume ownership (lifetime, never attachment) ─────────────────────

    @staticmethod
    def _mount_source(entry: object) -> str:
        """The ``source`` of one declared mount, in any of the three shapes.

        A configuration's ``mounts`` array is heterogeneous by spec — a
        docker-style ``k=v`` string, a JSON object, or (once read) a
        ``DevcontainerMount``. Duck-typed rather than isinstance'd against that
        model because importing it at runtime closes the cycle the
        ``TYPE_CHECKING`` guard at the top of this module exists to break.
        """
        if isinstance(entry, str):
            for part in entry.split(","):
                key, _, value = part.partition("=")
                if key.strip() in {"source", "src"}:
                    return value.strip()
            return ""
        if isinstance(entry, Mapping):
            return str(entry.get("source") or entry.get("src") or "")
        return str(getattr(entry, "source", "") or "")

    @classmethod
    def _per_container_patterns(cls, declared: Iterable[object]) -> list[tuple[str, str]]:
        """The declared sources that name a per-container volume, split around the token.

        Refuses two shapes rather than guessing, because both would widen a
        removal: a source naming the token twice (no unambiguous split), and a
        source that is the bare token (empty prefix AND suffix would match every
        volume attached to the container). Under-deleting leaks disk; the other
        direction destroys data.
        """
        patterns: list[tuple[str, str]] = []
        for entry in declared:
            source = cls._mount_source(entry)
            if cls.DEVCONTAINER_ID_TOKEN not in source:
                continue
            parts = source.split(cls.DEVCONTAINER_ID_TOKEN)
            if len(parts) != 2 or not (parts[0] or parts[1]):
                logger.warning(
                    "container: mount source {!r} is too broad to attribute a volume to this "
                    "container — leaving it for the user to remove",
                    source,
                )
                continue
            patterns.append((parts[0], parts[1]))
        return patterns

    @classmethod
    def owned_volumes_in(
        cls,
        *,
        declared: Iterable[object],
        attached: Iterable[Mapping[str, object]],
        project: str | None = None,
    ) -> list[str]:
        """Which of a container's volumes Grove may remove with it.

        The question is LIFETIME, not attachment. *declared* is the complete
        configuration's own ``mounts`` array — the one Grove wrote — and
        *attached* is ``docker inspect``'s mount table for the container that
        came out of it. A volume is Grove's only when its name is the
        instantiation of a source carrying
        :attr:`DEVCONTAINER_ID_TOKEN`: the spec's own way to say "mint this per
        dev container", substituted by the CLI at ``up`` time.

        Everything else survives, and the three cases matter for different
        reasons: a **shared cache** (``devc-<project>-uv``) is declared to
        outlive every workspace and is written concurrently by the project's
        other live containers, so removing one is a cross-workspace outage; a
        **compose project's own volume** may hold a database, which is why
        ``compose down`` is never given ``-v``; a **bind** is a host directory
        that was never Grove's to begin with. A stable name appearing literally
        in *declared* is excluded outright, ahead of any pattern match, so a
        pathological pattern (``cache-${devcontainerId}`` beside a literal
        ``cache-shared``) still cannot reach a name somebody wrote down.

        *project* is the OWNED compose project, when there is one: compose names
        the volumes of a stack ``<project>_<declared-name>``, so without it the
        same feature volume that is ``dind-var-lib-docker-<hash>`` on a single
        container is ``<project>_dind-var-lib-docker-<hash>`` in a stack and
        matches nothing. The prefix is only ever stripped to TEST a candidate —
        the name removed is the real one — and the literal exclusion is applied
        to the stripped form too, so a stack's own ``<project>_mongo-data`` is
        unreachable by both routes.
        """
        patterns = cls._per_container_patterns(declared)
        if not patterns:
            return []
        literals = {
            source
            for source in (cls._mount_source(entry) for entry in declared)
            if source and cls.DEVCONTAINER_ID_TOKEN not in source
        }
        stack_prefix = f"{project}_" if project else ""
        owned: list[str] = []
        for entry in attached:
            if entry.get("Type") != "volume":
                continue
            name = str(entry.get("Name") or "")
            if not name or name in owned:
                continue
            candidates = [name]
            if stack_prefix and name.startswith(stack_prefix) and len(name) > len(stack_prefix):
                candidates.append(name[len(stack_prefix) :])
            if any(candidate in literals for candidate in candidates):
                continue
            if any(
                cls._instantiates(candidate, prefix, suffix)
                for candidate in candidates
                for prefix, suffix in patterns
            ):
                owned.append(name)
        return owned

    @staticmethod
    def _instantiates(name: str, prefix: str, suffix: str) -> bool:
        """Whether *name* is *prefix* + a non-empty substitution + *suffix*."""
        return (
            name.startswith(prefix)
            and name.endswith(suffix)
            and len(name) > len(prefix) + len(suffix)
        )

    @classmethod
    def from_up_result(
        cls,
        result: UpResult,
        *,
        expected_project: str | None = None,
        base: ContainerRuntimeState | None = None,
        declared_mounts: Iterable[object] = (),
        observed: ContainerObservation | None = None,
        **fields: object,
    ) -> ContainerRuntimeState:
        """Mint (or refresh) the identity from a ``devcontainer up`` result.

        THE one place ownership is established, for all three of the things a
        teardown may name: the container (``up``'s own id), the compose stack
        (:meth:`owned_project_in`) and the volumes
        (:meth:`owned_volumes_in`). Establishing all three here is the design
        rather than a convenience — ownership recorded at creation is knowledge,
        ownership re-derived at teardown from a name is a guess, and both guesses
        this replaced (a ``grove-`` project prefix, an empty volume list) were
        wrong every single time.

        ``provisioned`` follows ``outcome`` and is bound to the id in this same
        result — a new id is never provisioned by an older run's success — and
        ``provisioned_start`` binds it to that id's current START as well, so a
        container restarted outside Grove stops claiming a firewall it lost.

        *declared_mounts* is the complete config Grove wrote; *observed* is the
        single ``docker inspect`` of the container that came out of it (``None``
        when it could not be read, which every rule below treats as "cannot
        tell"). An empty answer keeps *base*'s value rather than blanking it,
        because on a re-provision a lost entry is a silent leak while a stale one
        merely fails a best-effort removal.
        """
        project = result.compose_project_name
        owned = cls.owns_project(project, expected=expected_project, observed=observed)
        merged = (base or cls()).model_copy(deep=True)
        return merged.model_copy(
            update={
                "container_id": result.container_id or "",
                "compose_project": project,
                "compose_owned": owned,
                # `up` never reports the image, so without this read the field
                # backing MISSING_IMAGE-vs-ABSENT was permanently empty.
                "image_ref": (observed.image if observed else "") or merged.image_ref,
                "remote_user": result.remote_user or merged.remote_user,
                "remote_workspace_folder": (
                    result.remote_workspace_folder or merged.remote_workspace_folder
                ),
                "provisioned": result.outcome == "success",
                # Bound to the START, not just the id. Read off the same
                # observation as everything else here, and deliberately NOT
                # carried forward from `base`: a re-provision that could not
                # read the container has lost track of which start its hooks
                # ran for, and keeping the previous answer would assert a
                # firewall Grove can no longer prove is there.
                "provisioned_start": (observed.started_at if observed else ""),
                "owned_volumes": cls.owned_volumes_in(
                    declared=declared_mounts,
                    attached=observed.mounts if observed else (),
                    # Only an OWNED project may be stripped from a volume name:
                    # an unverified one is a name Grove has no reason to trust,
                    # and trusting it here would claim volumes on the strength of
                    # the very report the stack arm just refused.
                    project=project if owned else None,
                )
                or merged.owned_volumes,
                **fields,
            }
        )

    # ─── argv builders — the invariant's single enforcement point ──────────

    def filters(self) -> list[str]:
        """``--filter`` flags for ANY enumeration. A ``docker ps`` without these is a bug.

        Always carries ``label=grove.managed=1``; when the identity knows its
        workspace it also narrows to ``grove.scope=workspace`` and that id — the
        same triple ``GroveLabels.workspace_teardown_filter`` builds, which is
        the point: the teardown sweep and this enumeration must not be two
        different questions. Because this is the one chokepoint, widening the
        filter set later is a one-line change every enumeration inherits.
        """
        labels = {self.MANAGED_LABEL_KEY: self.MANAGED_LABEL_VALUE}
        workspace = self.id_labels.get(self.WORKSPACE_LABEL_KEY, "")
        if workspace:
            labels[self.SCOPE_LABEL_KEY] = InfrastructureScope.WORKSPACE.value
            labels[self.WORKSPACE_LABEL_KEY] = workspace
        return self.filter_flags(**labels)

    def teardown_argv(self, *, docker_bin: str = "docker") -> list[str]:
        """The ONE teardown command for this identity — id, or the owned project.

        Compose tears down by project (``down --remove-orphans``, and **never**
        ``-v``: the stack's declared volumes are the project's, not Grove's);
        single-container tears down by full id. Both paths validate first and
        raise :class:`~grove.core.errors.ContainerError` rather than emit a
        command that could name a foreign container — refusing to tear down is
        always recoverable, tearing down the wrong thing is not.

        The compose arm is what reaches a stack's SIBLING services and its
        network: the CLI id-labels the primary service only, so the label-filtered
        fallback can see neither, and a `mongo-1` carrying
        ``restart: unless-stopped`` would survive every ``kill`` for as long as
        this arm is unreachable. Its declared volumes still survive on
        purpose, and ``-v`` is asserted absent by test.
        """
        if self.compose_project is not None:
            self._require_owned_project()
            return [
                docker_bin,
                "compose",
                "-p",
                self.compose_project,
                "down",
                "--remove-orphans",
            ]
        self._require_full_id("teardown")
        return [docker_bin, "rm", "-f", self.container_id]

    def shutdown_argv(self, *, session: str, docker_bin: str = "docker") -> list[str] | None:
        """Signal the agent in its OWN namespace and wait — ``None`` if unreachable.

        The command that makes the grace in :data:`STOP_TIMEOUT_SECONDS` real,
        and it runs :attr:`SHUTDOWN_SCRIPT` rather than a docker verb because no
        docker verb can address the agent at all.

        ``None`` means the guarantee genuinely does not apply: this workspace has
        no in-container tmux (:attr:`tmux_command` empty — no tmux binary was
        ever recorded, or an image where none was reachable), so there is
        nothing that can name the agent's process from here and the stop
        degrades to what it always was. Said out loud by the caller rather than
        papered over.

        ``docker exec`` rather than ``devcontainer exec``: this module is the one
        allowed to invoke docker, importing the devcontainer CLI at runtime would
        close a cycle, and the id is a fact this identity already holds. The
        ``-u`` is not a nicety — the tmux server's socket is per-uid, so a
        shutdown running as anyone else cannot see the session, let alone signal
        it. The single-container form is used even for a compose stack: the agent
        lives in the workspace service, which is the container this id names.
        """
        if not self.tmux_command:
            return None
        self._require_full_id("shut down the agent inside")
        user = ["-u", self.remote_user] if self.remote_user else []
        return [
            docker_bin,
            "exec",
            *user,
            self.container_id,
            "sh",
            "-c",
            self.SHUTDOWN_SCRIPT,
            "grove",
            self.tmux_command,
            session,
            str(STOP_TIMEOUT_SECONDS),
        ]

    def stop_argv(self, *, docker_bin: str = "docker") -> list[str]:
        """The graceful-stop command backing ``pause`` (id or project scoped).

        Its ``-t`` is the SECOND half of a pause's shutdown and much the weaker
        one — see :data:`STOP_TIMEOUT_SECONDS` for what it does not buy. The half
        that reaches the agent is :meth:`shutdown_argv`, which runs first.
        """
        timeout = str(STOP_TIMEOUT_SECONDS)
        if self.compose_project is not None:
            self._require_owned_project()
            return [docker_bin, "compose", "-p", self.compose_project, "stop", "-t", timeout]
        self._require_full_id("stop")
        return [docker_bin, "stop", "-t", timeout, self.container_id]

    def volume_argv(self, volume: str, *, docker_bin: str = "docker") -> list[str]:
        """Remove one volume Grove itself created (never a project-declared one)."""
        if volume not in self.owned_volumes:
            raise ContainerError(f"refusing to remove volume {volume!r}: not owned by Grove")
        return [docker_bin, "volume", "rm", volume]

    def enumerate_argv(self, *, docker_bin: str = "docker") -> list[str]:
        """List the FULL ids of this workspace's containers — the fallback teardown input.

        ``--no-trunc`` is load-bearing: the truncated form docker prints by
        default is a prefix, and a prefix is exactly what must never reach
        ``docker rm``.
        """
        return [docker_bin, "ps", "-a", "-q", "--no-trunc", *self.filters()]

    def exec_argv(self, command: Sequence[str], *, docker_bin: str = "docker") -> list[str]:
        """Run *command* inside this container AS THE AGENT'S OWN USER.

        The cheap road into a provisioned container, and the counterpart to
        ``DevcontainerCli.exec_argv``: that one is the CLI's, resolves the whole
        configuration, and costs **484 ms** per invocation (`@devcontainers/cli`
        0.88.0 — it is a Node program that re-reads the config on every call);
        this one is a plain ``docker exec`` at **60 ms**. The difference decides
        which paths may use which: a launch composes the CLI's form once, while
        a read on the poll path has to use this one, memoized, or it is not
        affordable at all.

        ``-u`` is not a nicety. The agent's tmux server binds a socket under
        ``/tmp/tmux-<uid>/``, so an exec that lands as the image's default user
        cannot see it — measured verbatim: ``error connecting to
        /tmp/tmux-0/default (No such file or directory)`` against a perfectly
        healthy server owned by uid 1000. :attr:`remote_user` is the CLI's own
        report of who the agent runs as, so it is the only honest answer;
        empty (a record from before it was recorded) omits the flag rather than
        guessing, and degrades to whatever the image says.
        """
        self._require_full_id("exec in")
        user = ["-u", self.remote_user] if self.remote_user else []
        return [docker_bin, "exec", *user, self.container_id, *command]

    def inspect_argv(self, *, docker_bin: str = "docker") -> list[str]:
        """One batched read of every fact status needs — a single fork, not four.

        ``.State.StartedAt`` rides along for :attr:`provisioned_start`:
        widening the format string of a read that already happens is the whole
        cost of detecting a container brought back outside Grove — no second
        command, no listener, no thread.
        """
        self._require_full_id("inspect")
        fmt = "{{.State.Running}}\t{{.State.Status}}\t{{.Config.Image}}\t{{.State.StartedAt}}"
        return [docker_bin, "inspect", "-f", fmt, self.container_id]

    @classmethod
    def observe_argv(cls, container_id: str, *, docker_bin: str = "docker") -> list[str]:
        """The ONE mint-time read behind every ownership answer — see :attr:`OBSERVE_FORMAT`.

        A ``@classmethod`` taking the id because it runs before the identity
        exists: the whole point is to answer what the record being minted will
        own. It still validates the full 64-char id like every other command that
        names a container, so the one path that reaches docker before there is a
        state to ask cannot skip the rule.
        """
        cls._assert_full_id(container_id, "read the container behind")
        return [docker_bin, "inspect", "-f", cls.OBSERVE_FORMAT, container_id]

    @classmethod
    def restart_policy_argv(cls, container_id: str, *, docker_bin: str = "docker") -> list[str]:
        """Assert ``--restart no`` on the workspace's container, after ``up``.

        Grove owns this container's lifecycle, so docker must never bring it back
        on its own. The overlay's ``runArgs`` says the same thing at CREATION —
        and works, for a single container. **Compose ignores ``runArgs``
        entirely**, so a project writing ``restart: always`` on its workspace
        service won outright, and that is not a cosmetic desync: an ``always``
        container resurrects a PAUSED workspace when the docker daemon restarts,
        and a docker-level restart does not re-run ``postStartCommand`` — so the
        agent comes back with **no egress firewall**.

        Asserted post-hoc rather than by generating a compose override, because
        the update is one command against a container id Grove already holds,
        while a compose file would be a whole new file-writing surface (see
        :class:`~grove.core.runtime.ContainerProvisioner`). It leaves a window
        between ``up`` and this command in which the project's policy is live;
        closing it would require the daemon to restart inside that window, which
        is not worth a new generated file to prevent.

        Only the WORKSPACE service is asserted on. A sibling that restarts
        itself is the project's business and breaches no isolation Grove
        promises — and teardown removes the whole stack regardless.
        """
        cls._assert_full_id(container_id, "assert the restart policy of")
        return [docker_bin, "update", "--restart", "no", container_id]

    # ─── validation (fail-closed) ──────────────────────────────────────────

    def _require_full_id(self, action: str) -> None:
        """A command may only name a full 64-char id Grove recorded itself."""
        self._assert_full_id(self.container_id, action)

    @staticmethod
    def _assert_full_id(container_id: str, action: str) -> None:
        """The rule itself, reachable before an identity exists (``observe_argv``)."""
        if len(container_id) != _FULL_ID_LENGTH:
            raise ContainerError(
                f"refusing to {action}: container id {container_id!r} is not a full "
                f"{_FULL_ID_LENGTH}-character id (docker resolves a short id as a PREFIX)"
            )

    def _require_owned_project(self) -> None:
        """A project-scoped command may only name a stack this identity VERIFIED.

        Two local anchors, and the second is the one that survives a corrupted
        record: :attr:`compose_owned` is written only by :meth:`owns_project`,
        and only together with the full id of the Grove-labelled container that
        corroborated the name — so requiring both here means a record that has
        lost either half can never emit a ``compose down``.

        **Do not swap this back for a name check, and here is why the obvious
        simpler option was strictly worse.** A reserved ``grove-`` prefix reads
        as the cheaper guard — no read, no stored flag, one ``startswith``. It
        was wrong in BOTH directions at once, which is what makes it worth a
        paragraph rather than a line. It *accepted* what it should not: any
        stack a stranger happened to name ``grove-*`` satisfied it, and a
        ``compose down`` is the one Grove command that can destroy a service it
        never created. And it *rejected* everything it should have accepted:
        Grove does not mint the project name, the devcontainer CLI derives it
        from the worktree basename plus ``_devcontainer``, so no real stack
        could ever pass — the fallback was not a safety net but the only arm
        that ever ran, and being label-filtered it reached the primary service
        alone. This check is a weaker *statement* than that one looked
        and a far stronger *guarantee* than it was, because it rests on what the
        container reported rather than on a convention nothing enforced. The
        general rule: a guard over a name someone else mints is a guess wearing
        a check's clothing — verify against the object, or do not claim it.
        """
        if not self.compose_project or not self.compose_owned:
            raise ContainerError(
                f"refusing a project-scoped command for {self.compose_project!r}: Grove could not "
                "verify this stack is its own"
            )
        self._require_full_id(f"run a project-scoped command for {self.compose_project!r}")


class DockerCli:
    """The ``docker`` subprocess boundary for lifecycle verbs — argv-only, ``shell=False``.

    Deliberately dumb: it runs argv the state built and narrows failures to
    :class:`~grove.core.errors.ContainerError`. It never assembles a command
    itself, because assembly is where the teardown invariant lives and that
    belongs on the identity. Injected into :class:`ContainerLifecycle`, so tests
    fake the process boundary and nothing else.
    """

    def __init__(self, *, timeout: float = 120.0) -> None:
        self._timeout = timeout

    def run(self, argv: Sequence[str], *, action: str) -> str:
        """Run a mutating command; raise on failure. Returns stdout."""
        result = self._invoke(argv)
        if result is None:
            raise ContainerError(f"failed to {action}: docker could not be invoked")
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no output"
            raise ContainerError(f"failed to {action}: {detail}")
        logger.debug("container: {} ok", action)
        return result.stdout

    def read(self, argv: Sequence[str]) -> str | None:
        """Best-effort read — ``None`` on any failure, never raises.

        Status is consulted on render paths, so an unreadable engine must
        degrade to "unknown" rather than break a poll (the ``peek`` discipline).
        """
        result = self.read_result(argv)
        if result is None or result.returncode != 0:
            return None
        return result.stdout

    def read_result(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str] | None:
        """The completed process, or ``None`` when docker could not be RUN AT ALL.

        The distinction :meth:`read` collapses, and the one a render path cannot
        afford to lose: a **non-zero exit is docker answering** — ``error:
        no such object: <id>``, which is exactly the fact a liveness check wants
        — whereas a binary that is not on this process's ``PATH`` (the
        documented systemd bare-PATH failure) or a docker that never returns is
        **no answer at all**. Collapsing them makes an unreadable engine
        indistinguishable from a host with no containers left, which would
        report every container workspace as dead while all of them keep running.

        :meth:`read` stays the seam for teardown enumeration, where both
        failures genuinely mean the same thing: nothing could be seen, so
        nothing is removed.
        """
        return self._invoke(argv)

    def _invoke(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                check=False,
                shell=False,
                timeout=self._timeout,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            logger.debug("container: docker invocation failed ({}): {}", argv[:3], exc)
            return None


class ContainerLifecycle:
    """The verbs over ONE workspace's container, sequenced in the safe order.

    Takes the identity plus the docker process boundary by injection, so the
    whole class is testable with a fake and no engine. It owns ordering, which
    is where the sharp edges are:

    * **kill is tmux-first.** Removing a container out from under the live
      ``devcontainer exec`` wedges the TTY, so the caller kills the HOST session
      before calling :meth:`teardown` — and teardown removes the container
      *before* the worktree, because the worktree is the container's bind mount.
    * **Both destructive verbs signal the agent before removing anything**
      (:meth:`shutdown_agent`), because the host session the caller just killed
      was only the viewport: the agent lives under a tmux INSIDE the
      container, and nothing outside the namespace can address it.
    * **respawn has no method here on purpose.** Respawn restarts the *agent*,
      not the environment; a container verb on that path would destroy a working
      environment to fix a dead pane.

    **Bringing a container UP is deliberately not a verb here** — it belongs to
    ``runtime.ContainerProvisioner``, which is the single ``devcontainer up``
    executor for create, resume and respawn alike. A lifecycle-owned resume
    would have to re-use the persisted ``override_config_path``, and that file
    is written INSIDE the worktree (relative ``build.dockerfile`` /
    ``dockerComposeFile`` paths must resolve, so it cannot live in a state dir)
    — which ``pause`` deletes along with the worktree. Only the provisioner
    regenerates it, so only the provisioner can honestly ``up``. Resume still
    goes through ``devcontainer up`` rather than ``docker start`` because
    ``postStartCommand`` runs on every start and, in the hardened config, that
    hook *is* the egress firewall — which is also why **``start`` is not a verb
    here and must not become one**: a container Grove starts without re-running
    the hook is exactly the state ``UNPROVISIONED`` exists to detect.
    """

    def __init__(
        self,
        state: ContainerRuntimeState,
        *,
        docker_bin: str = "docker",
        docker: DockerCli | None = None,
        agent_session: str = "",
    ) -> None:
        self._state = state
        self._docker_bin = docker_bin
        self._docker = docker or DockerCli()
        self._agent_session = agent_session

    @property
    def state(self) -> ContainerRuntimeState:
        """The identity this lifecycle acts on (refreshed by the verbs)."""
        return self._state

    # ─── verbs ─────────────────────────────────────────────────────────────

    def shutdown_agent(self) -> None:
        """Signal the agent inside the namespace and wait for it — best effort.

        The step every destructive verb here runs FIRST, because it is the only
        one that reaches the agent process at all: ``docker stop`` and
        ``docker rm -f`` both address PID 1, and an ``exec``ed agent is not in
        PID 1's tree. Without this a pause SIGKILLed the agent by namespace
        teardown, in 0.15 s, while reporting a 30-second grace.

        Best-effort by contract, like every other side effect on these paths: a
        container that cannot be exec'd into must not block reclaiming the
        worktree, which is what the user actually asked for. What it must never
        do is fail SILENTLY — a workspace with no in-container tmux
        (:meth:`ContainerRuntimeState.shutdown_argv` returning ``None``) is told
        so, because that is a real loss of the guarantee rather than a no-op.
        """
        argv = self._state.shutdown_argv(session=self._agent_session, docker_bin=self._docker_bin)
        if argv is None:
            logger.warning(
                "container: {} has no in-container tmux, so its agent cannot be signalled "
                "before the container goes — it will be killed without a chance to flush",
                self._describe(),
            )
            return
        try:
            self._docker.run(argv, action=f"shut down the agent in {self._describe()}")
        except ContainerError as exc:
            logger.warning("container: {}", exc)

    def pause(self) -> None:
        """Stop the container gracefully; the id survives so resume can reuse it.

        The agent is signalled and waited for FIRST (:meth:`shutdown_agent`) —
        the ``docker stop`` that follows never reaches it.

        Returns nothing: the identity is unchanged by a stop (that is the whole
        point — the id has to survive), and the new substate is not the caller's
        to persist. Ask :meth:`status` when you need it.
        """
        self.shutdown_agent()
        self._docker.run(
            self._state.stop_argv(docker_bin=self._docker_bin),
            action=f"stop container {self._state.short_id or self._state.compose_project}",
        )

    def teardown(self) -> None:
        """Destroy the container (or stack) and the volumes Grove itself created.

        Order is container → owned volumes; the caller removes the worktree
        after, because it is the container's bind mount. It matters that the
        volumes come SECOND on the compose path too: ``down`` is what releases
        the stack's references, so a volume removed before it would still be in
        use. Every command is validated by the identity before it is emitted, and
        a compose project this workspace could not prove it owns degrades to
        label-filtered per-container removal rather than a project-scoped
        ``down`` — which reaches the primary service and nothing else, so it says
        so rather than reporting a clean teardown.

        The agent gets the same in-namespace shutdown a pause gives it before
        anything is removed: ``docker rm -f`` is a SIGKILL with no grace
        at all, and a kill is the last moment an agent's final write can ever
        land.
        """
        self.shutdown_agent()
        try:
            argv = self._state.teardown_argv(docker_bin=self._docker_bin)
        except ContainerError as exc:
            logger.warning("container: {} — falling back to label-filtered removal", exc)
            self._teardown_by_label()
        else:
            self._docker.run(argv, action=f"tear down {self._describe()}")
        for volume in self._state.owned_volumes:
            try:
                self._docker.run(
                    self._state.volume_argv(volume, docker_bin=self._docker_bin),
                    action=f"remove volume {volume}",
                )
            except ContainerError as exc:
                # Best effort: a volume still in use must not abort the rest of
                # the teardown, and a leaked volume is recoverable by hand.
                logger.warning("container: {}", exc)

    def status(self) -> ContainerState | None:
        """One batched read of the container's substate — ``None`` if it could not be read.

        ``UNPROVISIONED`` is derived here rather than stored as a second flag:
        a running container whose provisioning does not apply looks perfectly
        healthy to ``docker ps``, and that is precisely the state an autonomous
        agent must not be exec'd into. Two things make provisioning apply, and
        the second is what matters most — the recorded success has to be for
        the container's CURRENT start, because ``postStartCommand`` (the egress
        firewall) runs per start and a bare ``docker start`` runs none of it.

        The ``None`` arm is the same "cannot tell" this module draws everywhere
        else (:meth:`ContainerObservation.from_json`, the compose-ownership
        refusals), and it is load-bearing because reconciliation acts on the
        answer: "docker never ran" must not arrive dressed as ``ABSENT``. A
        non-zero exit still means ABSENT, because that is docker saying so.
        """
        if not self._state.container_id:
            return self._absent_state()
        result = self._docker.read_result(self._state.inspect_argv(docker_bin=self._docker_bin))
        if result is None:
            return None
        if result.returncode != 0 or not result.stdout.strip():
            return self._absent_state()
        # Positional, and short-read tolerant: a docker that answers with fewer
        # fields than the format asked for is a boundary Grove misread, and the
        # missing witness must degrade to "cannot vouch" rather than IndexError
        # on the poll path.
        fields = result.stdout.split("\t")
        if fields[0].strip().lower() != "true":
            return ContainerState.STOPPED
        started_at = fields[3].strip() if len(fields) > 3 else ""
        if not self._state.provisioned or not self._state.provisioned_for(started_at):
            return ContainerState.UNPROVISIONED
        return ContainerState.RUNNING

    # ─── internal ──────────────────────────────────────────────────────────

    def _teardown_by_label(self) -> None:
        """Remove every container carrying this workspace's labels, by full id.

        The fail-closed path: with no trusted project name, identity comes from
        the labels only. Ids come back ``--no-trunc`` and are re-validated one by
        one, so a truncated or foreign line can never reach ``docker rm``.

        Its blind spot is the point of the warning: the devcontainer CLI stamps
        the id-labels on the PRIMARY service only, so for a stack this reaches
        that one container and leaves every sibling, the network and the volumes
        behind. Reporting a clean teardown here is how a running `mongo-1`
        can outlive a `kill`.
        """
        raw = self._docker.read(self._state.enumerate_argv(docker_bin=self._docker_bin))
        if raw is None:
            logger.warning("container: could not enumerate Grove containers for teardown")
            return
        if self._state.compose_project is not None:
            logger.warning(
                "container: removing this workspace's labelled containers only — its compose "
                "stack could not be verified, so sibling services, the network and any stack "
                "volumes are left on the host"
            )
        for line in raw.split():
            target = self._state.model_copy(
                update={"container_id": line.strip(), "compose_project": None}
            )
            try:
                argv = target.teardown_argv(docker_bin=self._docker_bin)
            except ContainerError as exc:
                logger.warning("container: {}", exc)
                continue
            try:
                self._docker.run(argv, action=f"remove container {target.short_id}")
            except ContainerError as exc:
                logger.warning("container: {}", exc)

    def _absent_state(self) -> ContainerState:
        """``ABSENT`` or ``MISSING_IMAGE`` — the distinction is start vs rebuild."""
        if not self._state.image_ref:
            return ContainerState.ABSENT
        raw = self._docker.read(
            [self._docker_bin, "image", "inspect", "-f", "{{.Id}}", self._state.image_ref]
        )
        return ContainerState.ABSENT if raw else ContainerState.MISSING_IMAGE

    def _describe(self) -> str:
        """Short, log-safe description — short id or project name, never a command."""
        if self._state.compose_project is not None:
            return f"compose project {self._state.compose_project}"
        return f"container {self._state.short_id}"


class ContainerLiveness:
    """The RENDER path's reader of :meth:`ContainerLifecycle.status` — memoized.

    Status reconciliation runs per workspace per poll, already pays three tmux
    forks there, and is re-entered by the daemon's hook-ingest route on every
    agent event — so the read this wraps is not free. Measured against Docker
    29.6.1: ``docker inspect`` of one container is **16.3 ms median** (min 15.3,
    max 17.1), against a 2 s poll. One entry
    per container identity, valid for :attr:`TTL_SECONDS`, collapses that to at
    most one fork per container workspace per window no matter how many callers
    reconcile in it — the same bounded-memo shape the daemon already uses for
    its catalog, and short enough that a container stopped by hand surfaces
    within one window rather than a poll later. Host workspaces never reach here
    at all, so their cost is unchanged.

    **``None`` means "could not tell", and it is deliberately not
    :attr:`ContainerState.ABSENT`** — the distinction
    :meth:`ContainerObservation.from_json` already draws, and it matters more on
    a render path than on a teardown one. A reconcile that cannot reach the
    engine must leave the runtime-independent answer alone: reporting every
    container workspace on the host as dead is far worse than reporting none of
    them, and "the docker CLI is missing from this process's PATH" is a
    *documented* Grove failure (a systemd unit's bare PATH) in which the
    containers keep running throughout. A "cannot tell" is never cached either,
    so recovery is immediate rather than a window later.

    No lock: the daemon reconciles from several executor threads, and the worst
    a lost write can cost here is one extra fork — every entry is independently
    derived, so there is no state two threads could corrupt between them.
    """

    #: How long one identity's substate is trusted. Well under the interval at
    #: which a human notices a workspace is wrong, and well over a poll — the
    #: memo exists to decouple the read from the caller's tick rate, not to hide
    #: a stopped container.
    TTL_SECONDS: ClassVar[float] = 5.0

    #: The read is on a render path, so it is bounded far tighter than the
    #: lifecycle default (120 s): a wedged docker must cost one slow frame, not
    #: a two-minute stall in every surface that lists workspaces.
    READ_TIMEOUT_SECONDS: ClassVar[float] = 5.0

    def __init__(
        self,
        *,
        docker_bin: str = "docker",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._docker_bin = docker_bin
        self._clock = clock
        self._docker = DockerCli(timeout=self.READ_TIMEOUT_SECONDS)
        # Keyed by the two fields the answer depends on: the id being inspected,
        # and the image `_absent_state` falls back to when there is no container.
        self._cache: dict[tuple[str, str], tuple[float, ContainerState]] = {}

    def state_of(self, state: ContainerRuntimeState) -> ContainerState | None:
        """This identity's live substate, or ``None`` when it cannot be read.

        Never raises: every caller is a render path. ``status()`` itself is
        best-effort, but the argv it builds still validates the recorded id
        (a short id is a command that can name a foreign container), so a
        corrupted record raises :class:`ContainerError` here rather than
        answering — which is a "could not tell", not a dead container.
        """
        key = (state.container_id, state.image_ref)
        now = self._clock()
        cached = self._cache.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]
        # The expired entry is still the last thing this process reported for
        # this container, which is what makes the warning below EDGE-triggered
        # rather than once per memo window: a workspace left unfirewalled
        # overnight is one log line, not seventeen thousand.
        previous = cached[1] if cached is not None else None
        try:
            live = ContainerLifecycle(
                state, docker_bin=self._docker_bin, docker=self._docker
            ).status()
        except ContainerError as exc:
            logger.warning("container: could not read the substate of this workspace: {}", exc)
            return None
        if live is None:
            logger.debug(
                "container: {!r} could not be run — leaving this workspace's status to the "
                "runtime-independent signals",
                self._docker_bin,
            )
            return None
        if live is ContainerState.UNPROVISIONED and previous is not live:
            # The one substate whose CAUSE a reader cannot guess from the
            # workspace's own status. OFFLINE says "respawn me", which is the
            # right and complete remedy (visibility that changes no remedy is
            # not worth the surface) — but "the container is running
            # and Grove will not use it" reads as a bug unless the log says
            # why. `previous is None` covers the first read after a daemon
            # start, so an already-restarted container is announced once there
            # too rather than only at the moment it happens.
            logger.warning(
                "container: {} is running but was not provisioned by the start it is on — its "
                "postStartCommand (the egress firewall) never ran for this start; the workspace "
                "will read offline until `grove respawn` re-provisions it",
                state.short_id or "this workspace's container",
            )
        # A daemon runs for days, so an entry per container ever seen would
        # leak. Expired entries are dropped on each miss; N is this repo's
        # container workspaces, so the sweep is cheaper than the fork it rides.
        self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
        self._cache[key] = (now + self.TTL_SECONDS, live)
        return live
