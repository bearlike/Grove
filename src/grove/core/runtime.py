"""Which runtime a workspace gets, and how a container one is provisioned.

One question, two halves. :class:`RuntimeResolver` answers *host or container*
— a decision tree, evaluated **before any side effect** so a
refusal or a fallback costs no rollback. :class:`ContainerProvisioner` then
performs the container half against the ``@devcontainers/cli`` boundary
(``devcontainer.py``), streaming its log to disk as it runs.

The tree, and why each arm is what it is:

======  =============================================  ==========================
arm     condition                                      outcome
======  =============================================  ==========================
1       caller asked for ``host``                       host, **no warning** — a
                                                        recorded choice is not a
                                                        fallback
2       ``customizations.grove.requires_container``      loud refusal
        and the caller asked for host
3       repo has no ``devcontainer.json``               container, on Grove's
                                                        packaged default config
                                                        (a *notice*, not a
                                                        degradation)
4       the container runtime is UNAVAILABLE            host, loudly, reason
        (CLI missing / engine unreachable)               persisted
5/6     ``devcontainer up`` fails                       **fatal** — never a
                                                        silent downgrade
======  =============================================  ==========================

    Fallback is for UNAVAILABILITY only. Absence gets the default container.
    Failure is fatal.

Arm 5 is the load-bearing asymmetry: an environment that *exists but is broken*
must not degrade to host, because that hides real breakage and quietly voids the
isolation contract the workspace was created under. The container is KEPT for
diagnosis while the workspace itself rolls back.

Dependencies flow inward: this imports ``config``/``devcontainer``/``container``/
``paths``/``workspace``; the manager imports it, never the reverse.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, ClassVar

from loguru import logger

from grove.core import paths
from grove.core.config import AgentKind, GroveConfig
from grove.core.container_decor import DecorPayload, DecorPlan
from grove.core.container_infra import slugify_project
from grove.core.container_netfilter import NetfilterPayload
from grove.core.container_policy import (
    AgentSharePlan,
    CuratedGitConfig,
    EgressPolicy,
    ResourceLimits,
    TrustStamp,
)
from grove.core.container_runtime import ContainerObservation, ContainerRuntimeState, DockerCli
from grove.core.container_tmux import TmuxPayload, TmuxRuntimePlan
from grove.core.devcontainer import (
    OVERRIDE_CONFIG_RELPATH,
    DefaultDevcontainerConfig,
    DevcontainerCli,
    GroveOverlay,
    ProgressEvent,
    ReadConfigurationResult,
    UpResult,
)
from grove.core.env_source import EnvSource
from grove.core.errors import ContainerError, ContainerRequired, DevcontainerError
from grove.core.git import GitRepo
from grove.core.preflight import HostPreflight
from grove.core.workspace import Runtime

#: The devcontainer CLI's feature lockfile, written beside the config it read.
_LOCKFILE_NAME = "devcontainer-lock.json"


class ProvisionFailed(DevcontainerError):
    """A provision that failed but left an identifiable container behind.

    Lives here rather than in ``errors.py`` because it carries a
    :class:`ContainerRuntimeState`, and the errors module deliberately depends
    on nothing in ``grove.core`` — the dependency would run the wrong way and
    close a cycle. Subclasses :class:`DevcontainerError` so every existing
    ``except ContainerError`` arm keeps working unchanged; the subtype exists
    only for the identity.

    **The identity is the point, and it is more than the id already on the base
    class.** A caller has to PERSIST what came up — the container id, its
    labels, the config it was built from — so the workspace record can say *my
    container exists and its lifecycle hooks never reported success*, which is
    what reconciliation turns into OFFLINE and what a later teardown needs to
    name it. Reconstructing that from the id alone would be the caller guessing
    at facts the mint already knows. Same rule as the base class's own
    ``container_id``, one level up: a runtime boundary whose failure leaves a
    resource behind puts the handle on the error, not in a log line.
    """

    def __init__(self, message: str, *, container: ContainerRuntimeState) -> None:
        super().__init__(message, container_id=container.container_id or None)
        self.container = container


@dataclass(frozen=True, slots=True)
class RuntimeDecision:
    """The resolved runtime for one create, plus what the user must be told.

    Three outputs rather than one enum because the *reason* is as durable as the
    choice: ``fallback_reason`` is persisted for the workspace's lifetime (it is
    the only thing that later authorizes a ``respawn`` promotion), while
    ``notice`` is a one-shot, non-alarming line about running on Grove's default
    config. Never both — a fallback is a degradation, a notice is not.
    """

    runtime: Runtime
    fallback_reason: str | None = None
    notice: str | None = None
    config_path: Path | None = None
    """The ``devcontainer.json`` to hand the CLI: ``None`` when the repo has its
    own (the CLI discovers it from the workspace folder), else the packaged
    default (arm 3)."""

    @property
    def is_container(self) -> bool:
        return self.runtime is Runtime.CONTAINER


class RuntimeResolver:
    """Resolves host-vs-container for a create, and probes before it decides.

    Side effects are bounded and read-only — the shared :class:`HostPreflight`
    checks and (only when it can change the answer) a Docker-free
    ``read-configuration``. Both run before the worktree exists, which is what
    makes arm 4's fallback free and arm 2's refusal clean.

    Arm 4 asks ``HostPreflight`` rather than probing on its own, which is what
    makes the doc'd invariant TRUE rather than aspirational: what ``grove
    doctor`` reports and what a create requires are one definition, so a check
    added there is a check a create honors. It used to run its OWN pair of
    probes (``devcontainer --version`` + ``docker version``) against
    preflight's (``docker info``), and the two drifted in both directions — a
    doctor that passed while creates fell back, and the reverse.

    Both boundaries are injected so a test never needs a real CLI or a real
    engine; production passes neither and gets the real ones.
    """

    #: The standard locations the devcontainer spec looks for a configuration.
    #: A repo with none of these is arm 3 — Grove's own default config, still
    #: containerized. Grove does NOT parse these files; their mere existence is
    #: the question, and the CLI owns every byte inside them.
    CONFIG_LOCATIONS: ClassVar[tuple[str, ...]] = (
        ".devcontainer/devcontainer.json",
        ".devcontainer.json",
    )

    def __init__(
        self,
        cfg: GroveConfig,
        *,
        cli: DevcontainerCli | None = None,
        preflight: HostPreflight | None = None,
    ) -> None:
        self._cfg = cfg
        self._cli = (
            cli if cli is not None else DevcontainerCli(timeout=cfg.container.up_timeout_seconds)
        )
        # The default shares THIS resolver's CLI, so the arm-2 configuration
        # read and the arm-4 CLI check can never disagree about which binary
        # answered.
        self._preflight = (
            preflight if preflight is not None else HostPreflight(cfg, devcontainer_cli=self._cli)
        )

    @property
    def cli(self) -> DevcontainerCli:
        return self._cli

    def resolve(self, *, requested: Runtime | None, repo_root: Path) -> RuntimeDecision:
        """Walk the D5 tree for one create. Raises only on arm 2 (the refusal)."""
        wants_container = (
            self._cfg.container.enabled if requested is None else requested is Runtime.CONTAINER
        )
        config_path = self.project_config(repo_root)

        if not wants_container:
            # Arm 2 before arm 1: a committed layer may RAISE the floor, and a
            # refusal is better than silently handing back the host runtime the
            # project has declared unsafe. The read is Docker-free and only
            # happens when the answer could change (a config exists, a CLI can
            # parse it) — never a reason to fail an ordinary host create.
            self._assert_host_allowed(repo_root, config_path)
            # Arm 1: an explicit choice is not a fallback, so no reason, no
            # warning, and `respawn` will never auto-promote it.
            return RuntimeDecision(runtime=Runtime.HOST)

        unavailable = self._unavailable_reason()
        if unavailable is not None:
            # Arm 4: loud, persisted, and reversible with `grove respawn` once
            # the runtime is installed/started.
            logger.warning("runtime fallback to host for {}: {}", repo_root, unavailable)
            return RuntimeDecision(runtime=Runtime.HOST, fallback_reason=unavailable)

        return self.container_decision(repo_root)

    def container_decision(self, repo_root: Path) -> RuntimeDecision:
        """The container arm on its own — WHICH config, with no host/container choice.

        Split out because ``resume``/``respawn`` must re-provision an already-
        container workspace without re-running the tree (re-resolving would let
        a config flip change an existing workspace's isolation). They need only
        the arm-3 answer: the project's own config, or Grove's packaged default.
        """
        if self.project_config(repo_root) is not None:
            return RuntimeDecision(runtime=Runtime.CONTAINER)
        # Arm 3: absence is NOT a degradation — the workspace is still
        # containerized, on Grove's packaged config.
        return RuntimeDecision(
            runtime=Runtime.CONTAINER,
            notice=(
                "no devcontainer.json in this repo — running on Grove's default container "
                "config; run `grove init devcontainer` to commit a project-owned one"
            ),
            config_path=DefaultDevcontainerConfig.path(self._cfg.container),
        )

    def project_config(self, repo_root: Path) -> Path | None:
        """The repo's own ``devcontainer.json``, or ``None`` (arm 3)."""
        for relative in self.CONFIG_LOCATIONS:
            candidate = repo_root / relative
            if candidate.is_file():
                return candidate
        return None

    def _assert_host_allowed(self, repo_root: Path, config_path: Path | None) -> None:
        """Refuse a host create the project's committed config forbids (arm 2)."""
        if config_path is None or not self._cli.probe().available:
            return
        try:
            resolved = self._cli.read_configuration(repo_root)
        except Exception as exc:  # a broken/unreadable config never blocks host
            logger.debug("requires_container check skipped for {}: {}", repo_root, exc)
            return
        if resolved.effective.requires_container:
            raise ContainerRequired(
                f"{config_path} declares customizations.grove.requires_container; "
                "this project refuses to run its agents on the host"
            )

    def _unavailable_reason(self) -> str | None:
        """Why the container runtime can't be used here, or ``None`` if it can.

        The FIRST failing container-scoped preflight check, rendered with its
        own detail and hint — so the sentence persisted on the workspace is the
        same sentence ``grove doctor`` prints, and a user told to run doctor
        sees the row that caused their fallback rather than a paraphrase.
        """
        for check in self._preflight.container_ready():
            if check.ok:
                continue
            hint = f" — {check.hint}" if check.hint else ""
            return f"container runtime unavailable: {check.name}: {check.detail}{hint}"
        return None


class ContainerProvisioner:
    """Brings up one workspace's devcontainer and records what the CLI reported.

    The side-effect half of this module: read the project's complete
    configuration, overlay Grove's additions, write the complete override inside
    the worktree, ``up``, and persist the CLI's own answers as
    :class:`ContainerRuntimeState`. Nothing about the container is recomputed
    from Grove's own guesses.

    Failure raises ``DevcontainerError`` (carrying the ``containerId`` when the
    CLI reported one) and the container is deliberately left running: the caller
    rolls the workspace back, the container stays diagnosable.
    """

    #: Everything a provision leaves inside the worktree, as gitignore patterns.
    #: None of it is ever the user's to keep, but all of it sits in the working
    #: tree, so git has to be told — otherwise `git worktree remove` refuses on
    #: untracked files and `pause`/`kill` fail on every containerized workspace.
    #:
    #: The last two are the CLI's OWN artifact, not Grove's: `devcontainer up`
    #: writes a feature lockfile beside whichever config it was given, so the
    #: path follows the config's directory — the override config's
    #: `.devcontainer/` for a Grove-supplied config, the worktree root for a
    #: project whose config is a top-level `.devcontainer.json`. Both are listed
    #: because both are reachable; anchoring each one (rather than a bare
    #: filename pattern) keeps the exclude from swallowing a same-named file
    #: elsewhere in the user's tree. A lockfile the project TRACKS is unaffected
    #: — `info/exclude` only ever applies to untracked paths.
    GENERATED_EXCLUDES: ClassVar[tuple[str, ...]] = (
        f"/{OVERRIDE_CONFIG_RELPATH.as_posix()}",
        f"/{EgressPolicy.SCRIPT_RELPATH}",
        f"/{OVERRIDE_CONFIG_RELPATH.parent.as_posix()}/{_LOCKFILE_NAME}",
        f"/{_LOCKFILE_NAME}",
    )

    def __init__(
        self,
        cfg: GroveConfig,
        *,
        repo_root: Path,
        cli: DevcontainerCli | None = None,
        docker: DockerCli | None = None,
    ) -> None:
        self._cfg = cfg
        # The project slug is a create-time label value, and it is derived once
        # here rather than threaded per call: a provisioner belongs to one repo
        # for its whole life, so re-deriving it at every `provision` would be a
        # parameter that can only ever hold the same value.
        self._project_slug = slugify_project(repo_root)
        self._cli = (
            cli if cli is not None else DevcontainerCli(timeout=cfg.container.up_timeout_seconds)
        )
        # The docker boundary is separate from the devcontainer one: the CLI
        # creates the container, docker is the only thing that can say what it
        # was actually given. Injected for the same reason `cli` is — a test of
        # the mint must not depend on an engine being up.
        self._docker = docker if docker is not None else DockerCli()

    def share_plan(self, workspace_id: str, *, kind: AgentKind) -> AgentSharePlan:
        """The agent-config share for one workspace — pure, and asked TWICE.

        The provisioner consumes its mounts; the manager consumes its ``env`` for
        the launch spec, because the mount and the env var pointing at it are two
        halves of one fact and must be derived from the same plan. Pure and
        deterministic, so computing it at both sites is cheaper and safer than
        threading one instance through the create path.
        """
        return AgentSharePlan.from_config(
            self._cfg.container,
            kind=kind,
            home=Path.home(),
            workspace_config_dir=paths.agent_workspace_config_dir(workspace_id),
        )

    def _trust_stamp(
        self, resolved: ReadConfigurationResult, *, worktree: Path
    ) -> TrustStamp | None:
        """The pre-``up`` trust stamp for this workspace, or ``None`` when there
        is none to file.

        Two ways to get nothing, and only one of them is a decision:
        ``container.agent_config.trust`` turned off (the operator asked for the
        prompt back), or a CLI that reported no ``workspaceFolder`` — which is
        loud, because the workspace will then meet an interactive dialog with
        nobody there to answer it, and that is not a diagnosis anyone recovers
        from a silent start.
        """
        if not self._cfg.container.agent_config.trust:
            return None
        folder = resolved.workspace.workspace_folder if resolved.workspace else None
        if not folder:
            logger.warning(
                "container: read-configuration reported no workspaceFolder for {} — "
                "the agent's first launch may block on an unanswerable trust prompt",
                worktree,
            )
            return None
        return TrustStamp.for_worktree(folder, worktree=worktree)

    def tmux_plan(self) -> TmuxRuntimePlan:
        """The in-container tmux plan, BUILDING Grove's bundle if it is missing.

        The one place the payload can be built, and the create path is the right
        one: it is the moment a container is being provisioned anyway, the
        provision log is already collecting minutes of image-build output, and
        the result is cached per host so it happens once. ``grove doctor``
        deliberately does not build — a preflight reports, it never provisions
        (its own contract), and a diagnostic command that silently spends two
        minutes compiling would be a surprise nobody asked for.

        It builds for THIS HOST's architecture, which is what almost every
        container runs. A container on a foreign platform is served after the
        fact by :meth:`_resolve_tmux`, once the probe has said which one — and
        that works because the mount is a live view of the cache directory, not
        a copy of it.

        Best-effort throughout: a build that cannot run yields a plan with no
        mounts, the image's own tmux is used if it has one, and otherwise the
        launch composes a bare ``exec``. The workspace never fails over its
        multiplexer.
        """
        cfg = self._cfg.container.tmux
        payload = TmuxPayload.resolve(cfg)
        if cfg.enabled:
            payload.build(
                docker_bin=self._cfg.container.docker_bin,
                timeout=self._cfg.container.up_timeout_seconds,
            )
        return TmuxRuntimePlan.from_config(cfg, payload=payload)

    def decor_plan(self) -> DecorPlan:
        """Grove's terminal-decor bundle for this container, or an empty plan.

        The third sibling of :meth:`tmux_plan` and :meth:`netfilter_payload`,
        and the only one with nothing to build: the assets are static text
        shipped in the wheel, so resolving them is a few `exists` probes rather
        than a Docker invocation. An operator-supplied ``payload`` takes the
        same road and is never written to.

        Decor is cosmetic, so it degrades silently to nothing — a missing asset
        costs a status bar, never a workspace.
        """
        decor = self._cfg.container.decor
        return DecorPlan.from_config(
            enabled=decor.enabled,
            statusline=decor.statusline,
            tmux_conf=decor.tmux_conf,
            payload=DecorPayload.resolve(decor.payload),
        )

    def netfilter_payload(self, egress: EgressPolicy) -> NetfilterPayload | None:
        """Grove's static ``iptables`` bundle, BUILDING it if it is missing.

        The tmux twin of :meth:`tmux_plan`, and built on the create path for the
        same reasons: a container is being provisioned anyway, the provision log
        is already collecting build output, and the result is cached per host so
        it happens once. ``grove doctor`` reports it and never builds it.

        ``None`` for ``egress.mode = "open"``, which runs no firewall script at
        all — so there is nothing for the bundle to serve, and both the build and
        the mount would be inert machinery in somebody else's image. One gate,
        here, rather than a condition at each of the two consumers.

        Best-effort otherwise: a build that cannot run yields a payload with no
        mounts, the image's own tools are used if it has any, and failing those
        the script's fail-closed refusal names what is missing.
        """
        if not egress.fail_closed:
            return None
        payload = NetfilterPayload.resolve()
        payload.build(
            docker_bin=self._cfg.container.docker_bin,
            timeout=self._cfg.container.up_timeout_seconds,
        )
        return payload

    def provision(
        self,
        *,
        workspace_id: str,
        worktree: Path,
        decision: RuntimeDecision,
        log_path: Path,
        kind: AgentKind = "generic",
        remote_urls: Iterable[str] = (),
        base: ContainerRuntimeState | None = None,
        container_env: EnvSource | None = None,
    ) -> ContainerRuntimeState:
        """Provision the workspace's container; report what came up.

        The returned identity's ``provisioned`` flag is the outcome: ``True``
        when ``up`` reported success, ``False`` when it failed but still left a
        container behind (see :meth:`_invoke_up`). Everything else still
        raises, and an unprovisioned return is not a soft failure — the caller
        refuses the workspace exactly as it does for a raise. Recording it is
        what lets the record name the container the failure left running, and
        what makes ``ContainerState.UNPROVISIONED`` reachable at all.

        THE single ``devcontainer up`` executor — create, resume and respawn all
        land here rather than each driving the CLI their own way. That is not
        just tidiness: the complete override config is written INSIDE the
        worktree (relative ``build.dockerfile`` / ``dockerComposeFile`` paths
        must resolve against the config's own directory), and ``pause`` deletes
        the worktree, so a resume that reused the persisted override path would
        point ``--override-config`` at a file that no longer exists. Only a path
        that REGENERATES the override can honestly bring a container back.

        Being the single executor is also what makes the autonomy policy hold on
        every start rather than only at create: the egress script is rewritten
        and re-applied here, so a resume cannot bring the agent back inside a
        container whose firewall is silently absent.

        *base* is the workspace's previous identity on a re-provision, threaded
        so the mint keeps what an idempotent ``up`` does not re-report.

        *container_env* is the resolved ``container.env_file`` /
        ``env_command`` mapping. It reaches the CLI as
        ``--secrets-file``, which is what puts it in the environment of the
        project's own ``postCreate`` / ``postStart`` hooks — the phase that
        installs dependencies and therefore the phase that needs a private
        index token or a proxy credential. The agent gets the same values by a
        different road (the launch env), because the CLI carries secrets into
        lifecycle hooks only and never persists them into the container.
        """
        # ONE label producer for the whole codebase. The create path stamps
        # exactly what every later enumeration filters on — `grove.managed`,
        # `grove.scope`, `grove.project` and `grove.workspace` together, because
        # the teardown sweeps filter on scope while the identity paths filter on
        # the workspace id. Stamping only the identity pair is what made the
        # workspace-teardown filter match nothing that existed.
        labels = ContainerRuntimeState.labels_for(workspace_id, project_slug=self._project_slug)
        share = self.share_plan(workspace_id, kind=kind)
        egress = EgressPolicy.derive(
            self._cfg.container.egress,
            kind=kind,
            remote_urls=remote_urls,
            # The telemetry endpoint is a destination like any other, and the
            # firewall is the layer that decides whether the exporter's packets
            # leave. Passed here rather than restated in config so the address
            # the agent exports to and the address the firewall admits are the
            # same fact.
            telemetry=self._cfg.telemetry,
        )
        tmux = self.tmux_plan()
        netfilter = self.netfilter_payload(egress)
        decor = self.decor_plan()
        with _ProvisionLog.open(log_path) as log:
            log.line(f"devcontainer provision for workspace {workspace_id}")
            log.line(f"worktree: {worktree}")
            log.line(f"config: {decision.config_path or 'project-owned (discovered)'}")
            # Both artifacts below are written INSIDE the worktree by necessity,
            # so git must be told they are Grove's — else `pause`/`kill` cannot
            # remove the worktree and the user's `git status` is never clean.
            GitRepo(worktree).ensure_excluded(*self.GENERATED_EXCLUDES)
            # Read the configuration BEFORE seeding: the trust stamp is filed
            # under the container's own workspace folder, and only the CLI knows
            # what that resolves to. `read-configuration` is pure — no engine,
            # no container — so nothing about this ordering costs a start.
            resolved = self._cli.read_configuration(worktree, config=decision.config_path)
            trust = self._trust_stamp(resolved, worktree=worktree)
            share.seed(trust=trust)
            log.line(f"agent config share: {share.share} ({len(share.mounts)} mount(s))")
            if trust is not None:
                log.line(
                    f"trust stamp: {trust.workspace_folder} "
                    f"({len(trust.mcp_servers)} mcp server(s) approved)"
                )
            egress.write_script(worktree)
            netfilter_detail = netfilter.detail if netfilter is not None else "not needed"
            log.line(f"egress: {egress.mode} (netfilter payload: {netfilter_detail})")
            log.line(f"decor: {'mounted' if decor.enabled else 'none'}")
            complete = self._overlay(share, egress, tmux, netfilter, decor).apply(
                resolved.effective,
                git_common_dir=GroveOverlay.worktree_common_dir(worktree),
            )
            override = complete.write_override(worktree)
            log.line(f"override config: {override}")
            # The secrets file lives OUTSIDE the worktree and only for the
            # duration of this `up` — the worktree is a git checkout that is
            # also bind-mounted into the container, so a secrets file there
            # would be one `git add -A` from being committed. Key names and a
            # count are logged; a value never is, here or anywhere else.
            env = container_env if container_env is not None else EnvSource({})
            with env.secrets_file(workspace_id) as secrets:
                if secrets is not None:
                    log.line(
                        f"container env: {len(env.values)} variable(s) "
                        f"({', '.join(sorted(env.values))})"
                    )
                result, failure = self._invoke_up(
                    worktree, labels=labels, override=override, secrets=secrets, log=log
                )
            log.line(f"container {result.container_id or '-'} at {result.remote_workspace_folder}")
            observed: ContainerObservation | None = None
            tmux_command = ""
            if failure is None:
                self._supervise(result.container_id or "", log=log)
                observed = self._observe(result.container_id or "")
                if observed is not None:
                    log.line(f"image: {observed.image or '-'}")
                tmux_command = self._resolve_tmux(
                    tmux, worktree, labels=labels, override=override, log=log
                )
            else:
                # Nothing after `up` describes a container that is ready: the
                # supervision assertions, the image read and the tmux probe all
                # speak about an environment whose lifecycle hooks never
                # reported success. Skipping them keeps the log about the
                # failure instead of burying it under probes of a half-built
                # container.
                log.line(f"up did NOT succeed: {failure}")
        # The ONE mint site for a container identity, so every fail-closed rule
        # on that type (a compose stack Grove cannot prove it created is never
        # named by a project-scoped command) applies to create, resume and
        # respawn alike. `base` carries a re-provision's previous record forward,
        # so facts the CLI does not re-report on an idempotent `up` are not
        # silently blanked. A FAILED `up` is minted here too: `provisioned`
        # follows `outcome`, so the failure has an identity instead of only a
        # log line.
        container = ContainerRuntimeState.from_up_result(
            result,
            expected_project=base.compose_project if base is not None else None,
            base=base,
            declared_mounts=complete.mounts,
            observed=observed,
            config_path=str(decision.config_path or override),
            override_config_path=str(override),
            config_hash=complete.fingerprint(),
            id_labels=labels,
            tmux_command=tmux_command,
            # Gated on the tmux command as well as on the mount: `-f` is a tmux
            # flag, so a config recorded for a container with no reachable tmux
            # would be carried by every launch and used by none. Recording only
            # what BOTH halves support keeps the field's promise — that the file
            # named here is really there — which is what lets the launch pass it
            # without re-probing.
            tmux_conf=str(decor.tmux_conf) if decor.tmux_conf and tmux_command else "",
        )
        if failure is not None:
            # Raised OUTSIDE the log context on purpose: `_ProvisionLog.open`
            # re-wraps a `DevcontainerError` to add its diagnosis, and *failure*
            # already carries that diagnosis — re-entering it would append it
            # twice and drop the identity this subclass exists to carry.
            raise ProvisionFailed(failure, container=container)
        return container

    def _invoke_up(
        self,
        worktree: Path,
        *,
        labels: Mapping[str, str],
        override: Path,
        secrets: Path | None,
        log: _ProvisionLog,
    ) -> tuple[UpResult, str | None]:
        """Run ``devcontainer up``; answer ``(result, failure_detail)``.

        The seam that gives ``provisioned=False`` a producer. The CLI boundary
        RAISES on ``outcome != "success"``, and it does so before this module
        can mint anything — so until this arm existed, no failed ``up`` could
        ever persist an identity: ``ContainerRuntimeState.provisioned`` was a
        field that was only ever ``True``, ``ProvisionStatus.FAILED`` had no
        writer, and ``ContainerState.UNPROVISIONED`` had no way to be reached.
        A whole substate that only tests could construct.

        What the failed run genuinely leaves behind is a CONTAINER: the CLI
        reports ``outcome:"error"`` alongside a ``containerId``, which is why
        the error type carries it. Turning that id back into an identity is what
        lets the workspace record say *this container exists and its lifecycle
        hooks never reported success* — the one substate that a plain engine
        listing calls healthy while the egress firewall is down.

        **Nothing here softens the failure.** Answering rather than raising
        moves only WHERE the raise happens: the caller mints, then raises
        :class:`ProvisionFailed` carrying what it minted. The detail returned is
        the log's own :meth:`_ProvisionLog.diagnose` output rather than the bare
        CLI message, because the reason a lifecycle hook refused is printed to
        the container's stderr and never appears in the error object —
        taking the diagnosis here is what keeps that true now that the raise has
        moved out of the log's context manager.

        A raise with no container id at all (the process died before reporting
        one) is left to propagate: there is no handle to record, so there is no
        identity to mint and nothing this arm can add.
        """
        try:
            return (
                self._cli.up(
                    worktree,
                    id_labels=labels,
                    override_config=override,
                    secrets_file=secrets,
                    on_progress=log.progress,
                ),
                None,
            )
        except DevcontainerError as exc:
            if exc.container_id is None:
                raise
            logger.warning(
                "container: up failed but left container {} — recording it unprovisioned",
                exc.container_id[:12],
            )
            # Synthesized from the one fact the boundary hands back on failure.
            # Everything else a successful `up` reports (the remote workspace
            # folder, the compose project) is carried forward by `base` on a
            # re-provision and is genuinely unknown on a first one.
            return (
                UpResult.model_validate({"outcome": "error", "containerId": exc.container_id}),
                log.diagnose(exc),
            )

    def _observe(self, container_id: str) -> ContainerObservation | None:
        """Read the container ``up`` just produced, or ``None`` — never a raise.

        One fork per provision, feeding every ownership answer the mint makes
        (which volumes, which compose stack, which image). Best-effort like every
        other read on this path: an unreadable engine costs a leaked volume and a
        conservatively-scoped teardown, both recoverable, where raising would
        fail a container that is otherwise up and correct.
        """
        if not container_id:
            return None
        try:
            argv = ContainerRuntimeState.observe_argv(
                container_id, docker_bin=self._cfg.container.docker_bin
            )
        except ContainerError as exc:
            logger.warning("container: {}", exc)
            return None
        observed = ContainerObservation.from_json(self._docker.read(argv))
        if observed is None:
            logger.warning("container: could not inspect {} after up", container_id[:12])
        return observed

    def _resolve_tmux(
        self,
        plan: TmuxRuntimePlan,
        worktree: Path,
        *,
        labels: Mapping[str, str],
        override: Path,
        log: _ProvisionLog,
    ) -> str:
        """Which tmux this container's agent will run under. Never raises.

        Probed through ``devcontainer exec`` — the same road the agent's own
        launch takes — so the answer is about the REMOTE USER's ``PATH``, not
        the image's default user's. A ``docker exec`` probe would be faster and
        routinely wrong on exactly the images that set ``remoteUser``.

        Runs after ``up`` because it needs a container to ask — for the image's
        tmux AND for the ARCHITECTURE, which is the one fact only the container
        can state. That is also why Grove's whole cache root is mounted rather
        than one binary: the mount had to be decided before ``up``, so it covers
        every architecture rather than betting on one.

        **A foreign-platform container is served by building AFTER the probe**,
        which works only because a bind mount is a live view of the host
        directory — the new binary appears inside the already-running container
        with no second ``up``. That is the case an Apple Silicon host running an
        amd64 image (or the reverse) actually hits, and an amd64 binary simply
        cannot execute in an arm64 container.

        A probe that cannot run reads as "no image tmux, unknown architecture",
        which degrades to no tmux rather than to a binary that cannot execute.
        """
        if not plan.enabled:
            log.line("in-container tmux: disabled by config")
            return ""
        arch, image_has_tmux = "", False
        try:
            code, output = self._cli.exec(
                worktree,
                list(plan.PROBE_COMMAND),
                id_labels=labels,
                override_config=override,
            )
            if code == 0:
                arch, image_has_tmux = plan.parse_probe(output)
        except DevcontainerError as exc:
            logger.debug("container tmux: probe failed: {}", exc)
        if arch and not (image_has_tmux and plan.prefer_image):
            # Only now is the target architecture known. Almost always already
            # cached (it is the host's); a build here is the cross-platform case.
            TmuxPayload.resolve(self._cfg.container.tmux).build(
                arch=arch,
                docker_bin=self._cfg.container.docker_bin,
                timeout=self._cfg.container.up_timeout_seconds,
            )
        command = plan.command_for(arch=arch, image_has_tmux=image_has_tmux)
        if not command:
            log.line(
                f"in-container tmux: UNAVAILABLE (container arch {arch or 'unknown'}) — the "
                "image ships none and Grove has no payload for it; the agent will not "
                "survive a detached client"
            )
            return ""
        source = "image" if command == plan.IMAGE_COMMAND else f"grove payload, linux/{arch}"
        log.line(f"in-container tmux: {command} ({source})")
        return command

    def _overlay(
        self,
        share: AgentSharePlan,
        egress: EgressPolicy,
        tmux: TmuxRuntimePlan,
        netfilter: NetfilterPayload | None,
        decor: DecorPlan,
    ) -> GroveOverlay:
        """Fold the autonomy policy into Grove's additive overlay.

        The git env is the one place two producers collide: both
        ``GroveOverlay.git_env`` and ``CuratedGitConfig.to_env`` write
        ``GIT_CONFIG_COUNT``, and a merged pair would leave the count naming
        fewer entries than were written — git then silently ignores the tail.
        The curated form wins because it re-emits ``safe.directory`` itself, so
        the numbering stays consistent and nothing is lost; ``apply`` gives
        ``remote_env`` last word over ``git_env``, which is exactly that
        precedence.
        """
        remote_env = {
            **CuratedGitConfig.curate(GitRepo.global_config()).to_env(),
            **share.env,
            # `TERMINFO_DIRS` for Grove's mounted bundle. It rides
            # `remoteEnv` rather than the launch spec's env because it has to
            # apply to EVERY exec into this container, not just the agent's:
            # the tmux server is started by one exec and every later attach is
            # another, and a client attaching without it dies with "can't find
            # terminfo database" on any image that ships no terminfo of its own.
            **tmux.env,
        }
        return GroveOverlay(
            # Grove OWNS this container's lifecycle, so docker must never restart
            # one behind it. The default is already `no`, but a project's own
            # `runArgs` can set otherwise — and that is not merely a state-machine
            # desync: a policy of `always` brings a PAUSED container back when the
            # docker daemon restarts, and a docker-level restart does not re-run
            # `postStartCommand`, so the agent would return with NO egress
            # firewall. Grove wins here by construction rather than by refusing
            # the project: these append after the project's own runArgs, and
            # docker takes the last occurrence of a non-repeatable flag.
            run_args=("--restart", "no"),
            # A multi-hour agent session forks constantly (git, package managers,
            # test runners), and any subprocess outliving its parent reparents to
            # PID 1. The CLI's own default entrypoint reaps, but it ends in
            # `exec "$@"` — so with `overrideCommand: false`, or a compose
            # service, the project's command becomes PID 1 and a non-reaping one
            # (`sleep infinity`, the documented compose pattern) leaks a zombie
            # per orphan until the container hits its `--pids-limit`. This is a
            # default, not a decision — see the field on GroveOverlay.
            init=True,
            # Without these the firewall script cannot touch iptables — and it
            # fails closed, so the start fails rather than proceeding unbounded.
            cap_add=EgressPolicy.CAP_ADD if egress.fail_closed else (),
            # Through the CONFIG, never `up --mount`: the share mounts are
            # read-only and the CLI's flag parser rejects that option outright,
            # failing the whole invocation. Grove's tmux and netfilter bundles
            # are read-only for the same reason and take the same road.
            mounts=(
                *share.mount_flags,
                *tmux.mount_flags,
                *(netfilter.mount_flags if netfilter is not None else ()),
                *decor.mount_flags,
            ),
            remote_env=remote_env,
            post_start_command=egress.post_start_command(),
        )

    def _supervise(self, container_id: str, *, log: _ProvisionLog) -> None:
        """Assert Grove's supervision properties on the container ``up`` created.

        THE post-`up` application point, and the reason Grove writes no compose
        file at all. The devcontainer CLI creates the container whether
        the project is one image or a compose stack, and ``up`` reports the
        workspace service's own container id either way — so a command against
        that id covers both runtimes, while a generated compose override would
        be a new file-writing surface that must live in the worktree, be
        regenerated per provision, and join :attr:`GENERATED_EXCLUDES` or
        ``pause``/``kill`` break outright on the untracked file.

        Both commands are best-effort, for the same reason and with an
        asymmetry worth naming. A cgroup the host kernel does not support must
        not fail a workspace whose container is otherwise up; the restart
        assertion is closer to the isolation contract, but its hazard only
        materializes if the docker daemon later restarts while the workspace is
        paused — so rolling back a working environment over it would cost more
        than it protects. Egress, which is exposed the instant the agent runs,
        still fails closed. Every outcome is logged either way, so an unapplied
        property is visible rather than assumed.
        """
        if not container_id:
            return
        docker_bin = self._cfg.container.docker_bin
        try:
            restart = ContainerRuntimeState.restart_policy_argv(container_id, docker_bin=docker_bin)
        except ContainerError as exc:
            log.line(f"restart policy NOT asserted: {exc}")
            logger.warning("container: {}", exc)
        else:
            self._docker_update(restart, what="restart policy", log=log)
        limits = ResourceLimits.from_config(self._cfg.container.resources)
        self._docker_update(
            limits.docker_update_argv(container_id, docker_bin=docker_bin),
            what="resource limits",
            log=log,
        )

    def _docker_update(self, argv: Sequence[str], *, what: str, log: _ProvisionLog) -> None:
        """Run one best-effort ``docker update``; never raise, always report.

        An empty argv is "nothing configured", which is silence rather than a
        no-op line: an uncapped workspace has no resource story to tell.
        """
        if not argv:
            return
        try:
            result = subprocess.run(
                list(argv), capture_output=True, text=True, check=False, shell=False, timeout=60
            )
        except (subprocess.SubprocessError, OSError) as exc:
            log.line(f"{what} NOT applied: {exc}")
            logger.warning("container: could not apply {}: {}", what, exc)
            return
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no output"
            log.line(f"{what} NOT applied: {detail}")
            logger.warning("container: could not apply {}: {}", what, detail)
            return
        log.line(f"{what} applied: {' '.join(argv[2:-1])}")


class _ProvisionLog:
    """The provisioning log, written line-by-line AS the provision runs.

    Not buffered and flushed at the end: the failure that most needs an artifact
    is a cold build that never returns, and a raise before the writer runs
    leaves nothing to diagnose. Every write is flushed, so a timeout, a kill -9,
    or a daemon restart still leaves the tail. Best-effort by construction — an
    unwritable log must never mask the provision it describes.

    It is also the provision's DIAGNOSER. A lifecycle hook that refuses
    prints its reason to the container's stderr, which lands here — while the
    CLI's own final error object carries only the command that failed, so the
    error Grove raised read ``Command failed: /bin/sh -c sudo -n bash
    .devcontainer/.grove-egress.sh || …`` while the sentence naming the cause and
    the remedy sat in this file, unmentioned. :meth:`diagnose` closes that,
    generically: it is not an iptables special case but a rule about Grove's own
    marker (:attr:`MARKER`) wherever it appears in a container's output.
    """

    MARKER: ClassVar[str] = "grove:"
    """The prefix every message Grove's own in-container scripts print carries.

    Already the convention across the egress script and the tmux fallback, and
    it is what makes the diagnosis selectable at all: the log's raw TAIL is a
    Node stack trace from the CLI, so "print the last few lines" would surface
    exactly the least useful part of the artifact.
    """

    MAX_NOTICES: ClassVar[int] = 5
    """Bounded, because a raised error is a paragraph, not a log file. Deduped
    too: the ``postStartCommand`` runs ``sudo -n bash … || bash …``, so a refusal
    is printed once per arm and the identical sentence would otherwise appear
    twice in the error."""

    def __init__(self, handle: IO[str] | None, path: Path) -> None:
        self._handle = handle
        self._path = path
        self._notices: dict[str, None] = {}

    @classmethod
    @contextmanager
    def open(cls, path: Path) -> Iterator[_ProvisionLog]:
        handle: IO[str] | None = None
        try:
            paths.ensure_dir(path.parent)
            handle = path.open("w", encoding="utf-8")
        except OSError as exc:
            logger.warning("could not open provision log {}: {}", path, exc)
        log = cls(handle, path)
        try:
            yield log
        except DevcontainerError as exc:
            # Re-raised rather than handled at each call site: read-configuration
            # and up can both fail with a container-side reason, and the id must
            # survive — an `outcome:error` container still exists on the host.
            raise DevcontainerError(log.diagnose(exc), container_id=exc.container_id) from exc
        finally:
            if handle is not None:
                handle.close()

    def line(self, text: str) -> None:
        if self._handle is None:
            return
        stamp = datetime.now(tz=UTC).strftime("%H:%M:%S")
        try:
            self._handle.write(f"[{stamp}] {text}\n")
            self._handle.flush()
        except OSError as exc:  # pragma: no cover - disk full / closed handle
            logger.debug("provision log write failed: {}", exc)
            self._handle = None

    def progress(self, event: ProgressEvent) -> None:
        """Sink for the CLI's line-delimited progress — the build's real output."""
        text = event.text.strip()
        if not text:
            return
        self.line(text)
        # Captured as it streams rather than re-read from the file, so the
        # diagnosis survives a log Grove could not open at all.
        for candidate in text.splitlines():
            stripped = candidate.strip()
            if stripped.startswith(self.MARKER) and len(self._notices) < self.MAX_NOTICES:
                self._notices.setdefault(stripped, None)

    def diagnose(self, exc: DevcontainerError) -> str:
        """*exc*'s message, plus whatever the container itself said about it.

        The log path is always named: a provision failure the marker does not
        cover (an image build, a mount the engine rejected) is still diagnosable
        from the artifact, and "where do I look" is the one thing a user cannot
        derive.
        """
        return "\n".join([str(exc), *self._notices, f"(full provision log: {self._path})"])


__all__ = ["ContainerProvisioner", "ProvisionFailed", "RuntimeDecision", "RuntimeResolver"]
