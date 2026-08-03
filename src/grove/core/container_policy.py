"""Autonomy policy for containerized workspaces: what the agent shares, where it
may reach, and what it may consume.

**Autonomy is the product.** Grove containerizes an agent so it can run *fully
autonomously* — `claude --dangerously-skip-permissions`, Codex full-auto — with
the damage it can do bounded to the workspace. Permission prompts are host
mode's safety mechanism; the container is container mode's. What is engineered
here is the **blast radius**, deliberately *not* a credential boundary: the
agent reaches the worktree, its mounts, its own dependencies and stack services,
and nothing else on the machine — while native host integration (settings,
sign-in, skills) is a *feature*, delivered through bind mounts.

> Nothing in this module may gate, refuse, or second-guess a relaxed-permissions
> agent. Grove inspects no agent argv and inserts no permission decision
> anywhere; the agent's own flags pass through untouched. The one thing that can
> fail a start is a firewall that verifiably did not apply (config integrity, not
> a permission gate) — and `egress.mode: open` is the supported way to opt out.

Three planners, one question each, all **pure**: :class:`AgentSharePlan` (which
host paths cross the boundary, and the env that points the agent at them),
:class:`EgressPolicy` (where the container may reach), :class:`ResourceLimits`
(what it may consume). Each turns cascaded `container.*` config into an inert
description — a mount list, a script, an argv — that the launch/create boundary
applies. The two I/O methods are named as such (:meth:`AgentSharePlan.seed`,
:meth:`EgressPolicy.write_script`) and both are create-time, never per-poll.

Every one of these is applied by `runtime.ContainerProvisioner.provision`, the
single `devcontainer up` executor — which is what makes the policy hold on
resume and respawn, not only at create:

* after the worktree exists: `AgentSharePlan.from_config(...)` → `.seed()`,
  its `mount_flags` handed to `up`, and its `env` folded into the launch spec
  (the existing set-and-unset env seam, no new typed field).
* before `up`: `EgressPolicy.derive(...)` → `.write_script(worktree)` and
  `.post_start_command()` into the generated overlay's `postStartCommand`,
  with `cap_add` from :attr:`EgressPolicy.CAP_ADD`.
* after `up`: `ResourceLimits.from_config(...).docker_update_argv(container)`,
  best-effort, and on EVERY runtime — the devcontainer CLI creates the
  container whether the project is one image or a compose stack, and `up`
  reports the workspace service's own container id either way, so one
  post-hoc application point covers both. Grove writes no compose file at
  all, deliberately.

Dependencies flow inward: this module imports `config`/`paths` plus the two
control-file path seams (`channel`/`permission`, themselves `config`+`paths`
leaves) — see :meth:`AgentSharePlan.default_control_files` for why the paths are
read off their writers rather than restated here — and one read-only adapter
scan (:meth:`TrustStamp.for_worktree`), for the same reason: the shape of a
tool's own config file has exactly one parser. It knows nothing of Docker, the
devcontainer CLI, or the manager.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import ClassVar
from urllib.parse import urlsplit

import httpx
from loguru import logger

from grove.core import channel, paths, permission
from grove.core.agents.claude_code import ClaudeCodeAdapter
from grove.core.config import (
    SHARE_RANK,
    AgentKind,
    AgentShare,
    ContainerConfig,
    EgressConfig,
    EgressMode,
    RangeSource,
    ResourcesConfig,
)

#: Where Grove's per-workspace agent config dir is mounted INSIDE the container.
#: Host paths are bind-mounted into it individually, so the agent sees one
#: coherent config directory whose share list is explicit and auditable, and
#: per-workspace state never lands in the user's real `~/.claude`.
CONTAINER_CONFIG_ROOT = PurePosixPath("/grove/agent-config")

#: Where GROVE's own control files (`--settings` / `--channels` / `--mcp-config`)
#: land inside the container. Separate from the agent-config root because
#: they answer a different question: that root is the user's agent configuration,
#: this is Grove's control plane over the agent, and the two have different
#: lifetimes, owners and write permissions.
CONTAINER_CONTROL_ROOT = PurePosixPath("/grove/control")

#: Where Grove's own static ``iptables``/``ip6tables`` bundle lands inside the
#: container. Declared HERE rather than beside its builder because
#: :meth:`EgressPolicy.firewall_script` has to name it, and a policy module that
#: imported its own payload builder would close a cycle — the builder imports
#: :class:`MountPlan` from here. It is the third ``/grove`` root, alongside the
#: agent-config and control ones above and ``/grove/tmux``.
CONTAINER_NETFILTER_ROOT = PurePosixPath("/grove/netfilter")

#: The per-architecture directory inside that root. Declared here and read back
#: by ``NetfilterPayload.BIN_DIRNAME`` rather than the other way round, so the
#: host layout the builder writes and the glob the script walks are one fact.
CONTAINER_NETFILTER_BIN = CONTAINER_NETFILTER_ROOT / "bin"


#: How long the published-ranges fetch may take before the provision gives up
#: on it. Short on purpose: this is one HTTP call on the create path, and the
#: allowlist is strictly better with the ranges but entirely functional without
#: them, so a slow provider must cost seconds rather than a workspace.
RANGE_FETCH_TIMEOUT_SECONDS = 5.0


def fetch_published_ranges(source: RangeSource) -> tuple[str, ...]:
    """Fetch one provider's published IPv4 CIDRs. Never raises.

    The create-time I/O for the DNS arm, run on the HOST where the network is
    unrestricted — the container's own firewall is what this call exists to
    build, so fetching from inside it would be circular.

    **IPv4 only**, and that filter is load-bearing rather than tidy: these
    payloads carry v6 prefixes too, `iptables -d` rejects a v6 address, and the
    generated script runs under `set -e` — so a single unfiltered v6 CIDR would
    abort the whole firewall and fail the container start. The v6 family is
    denied wholesale anyway, so nothing is lost by dropping them.

    Best-effort by contract: an unreachable provider, a changed payload shape or
    a timeout yields ``()``, which degrades the allowlist to its hostname
    entries — exactly today's behaviour — rather than failing a provision over
    an optimization.
    """
    try:
        response = httpx.get(source.url, timeout=RANGE_FETCH_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning(
            "egress: could not fetch published ranges from {} ({}); "
            "falling back to hostname resolution for that provider",
            source.url,
            exc,
        )
        return ()
    if not isinstance(payload, dict):
        logger.warning("egress: {} did not return a JSON object; ignoring", source.url)
        return ()
    wanted = source.keys or tuple(payload)
    found: dict[str, None] = {}
    for key in wanted:
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        for entry in values:
            if isinstance(entry, str) and ":" not in entry:
                found.setdefault(entry.strip(), None)
    logger.debug("egress: {} contributed {} IPv4 ranges", source.url, len(found))
    return tuple(found)


@dataclass(frozen=True, slots=True)
class MountPlan:
    """One bind mount, described but not applied.

    ``readonly`` is the host-persistence control, not a privacy one: a `:ro`
    settings/skills/plugin-seed mount is what stops a prompt-injected agent
    planting a hook or skill that would then run in every future *host* session.
    """

    source: Path
    target: PurePosixPath
    readonly: bool = True

    def to_flag(self) -> str:
        """The ``--mount`` value the devcontainer CLI and `docker` both accept."""
        flag = f"type=bind,source={self.source},target={self.target}"
        return f"{flag},readonly" if self.readonly else flag


@dataclass(frozen=True, slots=True)
class TrustStamp:
    """The one project folder a seeded agent config is told it already trusts.

    Claude Code gates the first launch in an unseen directory behind an
    interactive trust dialog, keyed on the **absolute cwd**. Every container
    workspace is a new absolute path inside a fresh namespace, and the agent
    Grove starts there is unattended — so the dialog is a prompt nobody is
    present to answer and the workspace never starts working.

    Pre-``up`` by necessity: the agent launch is a ``devcontainer exec`` and the
    dialog fires at its first start, so anything written afterwards races the
    very thing it exists to prevent.

    :attr:`mcp_servers` rides along because the project-scoped MCP approval
    lives in the SAME per-folder block — stamping trust alone would trade one
    blocking prompt for another.
    """

    workspace_folder: str
    mcp_servers: tuple[str, ...] = ()

    @classmethod
    def for_worktree(cls, workspace_folder: str, *, worktree: Path) -> TrustStamp:
        """The stamp for a container whose workspace folder the CLI has reported.

        *workspace_folder* is READ BACK from ``devcontainer read-configuration``,
        never derived: only the CLI knows what a project's ``workspaceFolder`` /
        ``workspaceMount`` resolve to, and a stamp filed under a path the agent
        does not actually run in is silently inert — indistinguishable from no
        stamp at all, at the moment nobody is watching.

        The server names come from the worktree's committed ``.mcp.json``
        through the adapter that already parses it. That is also exactly the
        registry the container can reach: the host's user-scoped one is dropped
        from the seed (:attr:`AgentSharePlan.SEED_DROP_KEYS`), so this list is
        the whole of what there is to approve.
        """
        return cls(
            workspace_folder=workspace_folder,
            mcp_servers=ClaudeCodeAdapter.project_mcp_servers(worktree),
        )


@dataclass(frozen=True, slots=True)
class AgentSharePlan:
    """The exact host↔container agent-config share for one workspace.

    Structural choice: the kind's config-dir env var points at Grove's
    per-workspace directory and host paths are mounted *into* it. That gives one
    coherent config dir in-container, an explicit share list, and — because the
    directory is per workspace — no cross-workspace state collision.

    Three levels, from :data:`grove.core.config.AgentShare`:

    * ``full`` — the measured "share what the host uses" payload, `:ro` for
      everything that is configuration and `:rw` for the credential file the tool
      rewrites on refresh. The agent works in-container exactly as on the host.
      One member of it is deliberately NOT bound inside the config dir: the
      host plugin root crosses as a read-only *seed* alongside it, see
      :attr:`PLUGIN_SEED_ENV`.
    * ``projects`` — transcripts only (Claude; Codex has none separable from its
      state dir, see :attr:`CODEX_NEVER`).
    * ``isolated`` — nothing mounted. The per-workspace dir still exists and is
      still pointed at, so the agent gets a clean config of its own; this is the
      untrusted-repository posture and it costs a separate sign-in.

    Env-forwarded API keys (`ANTHROPIC_API_KEY`, a `claude setup-token` value)
    are already expressible as ``isolated`` plus the existing `AgentSpec.env` —
    deliberately not a fourth mode.
    """

    kind: AgentKind
    share: AgentShare
    host_config_dir: Path
    container_config_dir: PurePosixPath
    mounts: tuple[MountPlan, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    env_unset: tuple[str, ...] = ()
    seed_source: Path | None = None
    seed_target: Path | None = None
    hook_spool_dir: Path | None = None
    """The host directory a containerized hook spools raw payloads into.
    Carried so :meth:`seed` can create it before ``up`` — a bind source Docker
    would otherwise materialize root-owned, leaving the agent unable to write
    the very events this mount exists to carry. ``None`` for a plan with no
    mounts at all."""

    #: Agent kind → the env var naming that kind's data root. Mirrors
    #: ``TranscriptContext.CONFIG_DIR_ENV`` by value rather than importing it:
    #: `workspace.py` is the *persistence* side of that map and importing it here
    #: would point a pure policy module at the store's model. A kind absent from
    #: the map (mewbo, generic) has no config dir and gets an empty plan.
    CONFIG_DIR_ENV: ClassVar[Mapping[AgentKind, str]] = {
        "claude_code": "CLAUDE_CONFIG_DIR",
        "codex": "CODEX_HOME",
    }

    #: Read-only share payload per kind, relative to the kind's config dir.
    #: Measured on a real host — configuration the agent reads, never
    #: state it writes. Claude's ``plugins`` is the one exception and is NOT
    #: here: it is the tool's own *writable* root, so it crosses through
    #: :attr:`PLUGIN_SEED_ENV` instead.
    SHARED_RO: ClassVar[Mapping[AgentKind, tuple[str, ...]]] = {
        "claude_code": ("settings.json", "skills", "commands", "teams", "CLAUDE.md"),
        "codex": ("config.toml", "skills", "plugins", "rules", "memories"),
    }

    #: The host directory holding a kind's installed plugins, and the container
    #: directory it is bound at — a SIBLING of the config dir's own ``plugins``,
    #: never that directory itself.
    PLUGIN_SEED_NAME: ClassVar[str] = "plugins"
    PLUGIN_SEED_TARGET: ClassVar[str] = "plugins-seed"

    #: Per kind, the env var naming read-only plugin roots. Claude Code ships a
    #: purpose-built seed mechanism, and using it is what makes a `:ro` share
    #: work at all: bound at ``$CLAUDE_CONFIG_DIR/plugins`` — the tool's own
    #: WRITABLE root — every marketplace refresh is a `rename()` into a
    #: read-only bind and fails ``EROFS``, and the host's
    #: ``known_marketplaces.json`` / ``installed_plugins.json`` record absolute
    #: HOST paths that do not exist in the container. Named as a seed root
    #: instead, the tool registers those marketplaces with ``autoUpdate: false``,
    #: resolves them IN PLACE at the seed location, and keeps its writable root
    #: at the default ``<config dir>/plugins`` — which is inside the `:rw`
    #: config-root bind, so anything installed in-container persists per
    #: workspace. Verified against Claude Code 2.1.220; the seed layout it
    #: expects (``known_marketplaces.json``, ``marketplaces/``, ``cache/``) is
    #: byte-for-byte the host layout, so the host directory seeds unmodified.
    #: A kind absent from the map shares nothing of the sort.
    PLUGIN_SEED_ENV: ClassVar[Mapping[AgentKind, str]] = {
        "claude_code": "CLAUDE_CODE_PLUGIN_SEED_DIR"
    }

    #: Writable single FILES: the credential stores. `:rw` is not a relaxation —
    #: OAuth refresh rewrites these (measured: within two days), so a `:ro` mount
    #: breaks the agent at the first refresh. Mounted as files, never as the
    #: enclosing directory, so nothing else in it crosses.
    SHARED_RW: ClassVar[Mapping[AgentKind, tuple[str, ...]]] = {
        "claude_code": (".credentials.json",),
        "codex": ("auth.json",),
    }

    #: Transcript directory shared at ``share: projects``. Claude writes one
    #: subdirectory per encoded cwd, so N workspaces do not contend.
    SHARED_PROJECTS: ClassVar[Mapping[AgentKind, tuple[str, ...]]] = {"claude_code": ("projects",)}

    #: Never mounted at ANY share level, `full` included. Correctness, not
    #: privacy: Codex keeps SQLite state (`state_*.sqlite`, its `-wal`/`-shm`
    #: siblings, `logs_*.sqlite`) whose concurrent use across containers over a
    #: bind mount corrupts the database, and `sessions/` sits in the same tree.
    #: Claude's `projects/`, `sessions/`, `history.jsonl` and caches are state
    #: too — shared only, and only for Claude, at ``share: projects``.
    CODEX_NEVER: ClassVar[tuple[str, ...]] = (
        "state_",
        "logs_",
        "sessions",
        "cache",
    )

    #: The host `~/.claude.json` is COPIED per workspace at create, never
    #: mounted. This is what makes `full` + full-auto defensible: sign-in carries
    #: (the OAuth stanza rides along), while an in-container `mcpServers` rewrite
    #: dies with the workspace instead of following the user into every future
    #: host session. One file copy at create.
    SEED_FILENAME: ClassVar[str] = ".claude.json"
    SEED_KINDS: ClassVar[tuple[AgentKind, ...]] = ("claude_code",)

    #: Dropped from the seeded copy. ``projects`` is the per-project history
    #: map: other workspaces' data (and the bulk of the file's bytes) — blast
    #: radius, and the seed exists to carry sign-in, not history.
    #:
    #: ``mcpServers`` is the HOST's user-scoped tool registry, and every entry
    #: in it is resolved against a filesystem and a network namespace this
    #: container does not have. Observed for real: Grove's own
    #: ``grove-mcp`` entry, a console script of the host's `uv tool` venv that
    #: nothing mounts or installs in-container, copied verbatim into the seed.
    #: **A registration that can never start is strictly worse than no
    #: registration** — depending on the client it is a silent capability gap or
    #: a startup error, and it reads as live configuration either way. Dropping
    #: the whole key rather than filtering by command shape is deliberate: the
    #: seed is written *before* ``up``, so nothing here can know the container's
    #: ``PATH``, and a guess is wrong in both directions (a bare ``npx`` may be
    #: absent, an absolute path may be present). The container keeps the two
    #: registries it can actually reach — the project's own committed
    #: ``.mcp.json``, which travels with the worktree, and this seed file
    #: itself, which is per-workspace and mounted `:rw`, so anything registered
    #: from inside the container survives that workspace's own restarts and dies
    #: with it.
    SEED_DROP_KEYS: ClassVar[tuple[str, ...]] = ("projects", "mcpServers")

    #: Per kind, the per-project-folder keys that say "this folder is already
    #: answered for" — the payload of a :class:`TrustStamp`. Claude Code keys
    #: them on the absolute cwd under the very ``projects`` map
    #: :attr:`SEED_DROP_KEYS` drops, so the stamp is written as a FRESH
    #: single-entry map rather than by relaxing that drop: the host's map names
    #: every repo the user works on, and not one of those paths exists in this
    #: container. One row today; a kind absent from the table gets no stamp,
    #: which is the honest answer for a tool with no such per-folder gate.
    TRUST_KEYS: ClassVar[Mapping[AgentKind, Mapping[str, object]]] = {
        "claude_code": {
            "hasTrustDialogAccepted": True,
            "hasCompletedProjectOnboarding": True,
            "projectOnboardingSeenCount": 1,
            "hasClaudeMdExternalIncludesApproved": True,
        }
    }

    #: Per kind, the key in that same block listing the project-scoped MCP
    #: servers the folder approves. A sibling table rather than a row in
    #: :attr:`TRUST_KEYS` because its value is the workspace's own, not a
    #: constant — the approval store is not separate, only the value is.
    TRUST_MCP_KEY: ClassVar[Mapping[AgentKind, str]] = {"claude_code": "enabledMcpjsonServers"}

    @classmethod
    def default_control_files(cls) -> tuple[Path, ...]:
        """Grove's control files that MEAN something inside a container.

        **Files, never their enclosing directory** — the same rule
        :attr:`SHARED_RW` follows for the credential stores, and here it is not
        a nicety: these are siblings in Grove's user config dir, which also
        holds ``auth.json`` and ``webapp-sessions.json``. The latter stores live
        daemon bearer tokens in plaintext, so a ``:ro`` mount of that directory
        would hand a relaxed-permissions agent control over every workspace in
        every repo on the machine — an escalation clean out of the blast radius
        this module exists to draw, and one no amount of read-only helps with.

        Resolved through each writer's own public path seam rather than by
        naming filenames here, so a file that moves moves once — and so this
        table cannot quietly describe a file Grove no longer writes.

        The hook settings are here because their *content* is namespace-agnostic
        by construction: ``ClaudeHook.hook_command`` probes for its entry point
        and spools when it is absent, so one rendered file installs working
        hooks on a host and in a container alike. That is the property
        this table selects on, and the two files that lack it are
        :meth:`host_process_control_files`.

        Its container-only twin is here for the opposite reason, and the pair is
        worth reading together. ``--settings`` is single-valued, so a
        container-only key cannot be added as a second layer — it has to be
        merged into the one file the launch passes. A ``statusLine`` naming a
        path under ``/grove`` is exactly the content the paragraph above rules
        out, so it goes in a SEPARATE rendered file that only a container launch
        ever names, leaving the shared one untouched. Both are mounted because
        this table is consumed only when planning a container; which of the two
        the launch actually points at is the manager's choice, not this table's.
        """
        return (paths.agent_hooks_settings_path(), paths.agent_container_settings_path())

    @classmethod
    def host_process_control_files(cls) -> tuple[Path, ...]:
        """Grove control files deliberately NOT mounted: they spawn a HOST process.

        ``--channels`` and ``--mcp-config`` both register a stdio server whose
        command is ``sys.executable`` — the *host* interpreter of whichever
        process rendered the file, running ``python -m grove.core.…``. Nothing
        installs Grove or its interpreter inside the container, so translating
        the path (which :meth:`container_control_path` would happily do, since
        the FILE is perfectly mountable) buys a registration naming a command
        that cannot exist. **Reachability of a control file is two questions,
        not one: can the agent open it, and can the agent run what it names.**
        A file that passes the first while failing the second is exactly the
        "config that looks live and isn't" this replaces.

        Withholding the mount is the whole fix, with no launch-side change:
        :meth:`container_control_path` answers ``None`` for anything not in the
        table, and ``None`` already means *omit the flag* at the one seam every
        control flag crosses. So a container launch composes neither
        ``--channels`` nor ``--mcp-config``/``--permission-prompt-tool``, and
        the agent is honestly told it has no Grove tool surface rather than
        handed a broken one. Pointing the gate of ``--permission-prompt-tool``
        at a server that can never answer would be the worst of the three
        outcomes — every tool call blocked on a prompt nothing can resolve —
        and a container is the runtime whose blast radius is *already* the
        safety mechanism the prompt exists to provide.

        Making these genuinely reachable is a real alternative, and it is not
        this seam's to take: it needs Grove inside the container (unlike the
        static tmux payload, these are clients of host state — the permission
        server reads the host config cascade, the channel receiver binds host
        loopback) or a deliberate widening of the daemon's loopback bind, which
        earlier work refused on purpose. Either would be a design change of its
        own; until one lands, absence is the honest answer.
        """
        return (channel.channel_settings_path(), permission.permission_mcp_config_path())

    @classmethod
    def plan(
        cls,
        *,
        kind: AgentKind,
        share: AgentShare,
        home: Path,
        workspace_config_dir: Path,
        control_files: Iterable[Path] | None = None,
        hook_spool_dir: Path | None = None,
        exists: Callable[[Path], bool] = Path.exists,
    ) -> AgentSharePlan:
        """Plan the share for one workspace. Pure — `exists` is the only probe.

        A missing host path is *dropped*, never mounted: Docker materializes a
        bind source that does not exist as a new **root-owned directory on the
        host**, so planning a mount for an absent `~/.claude/teams` would litter
        the user's config dir and, worse, shadow a file the tool later wants to
        create. `exists` is injected so the whole planner stays testable without
        a filesystem.

        `control_files` defaults to :meth:`default_control_files`; it is a
        parameter so the planner keeps its "pure over injected inputs" shape.
        The drop rule bites harder for these than for the share payload: a
        missing control FILE would be materialized as a root-owned *directory*
        at the exact path Grove's own writer later needs, so the workspace would
        not merely lose its hooks, it would poison the host path for every
        future launch on the machine. The launch boundary reads the mount table
        back (:meth:`container_control_path`) and omits the flag for anything
        that was dropped, which is what keeps the drop a degradation instead of
        an outage.

        `hook_spool_dir` defaults to :func:`grove.core.paths.agent_hook_spool_dir`
        for the same reason `control_files` defaults to Grove's own — a
        parameter keeps the planner pure over injected inputs.
        """
        var = cls.CONFIG_DIR_ENV.get(kind)
        hook_spool_dir = paths.agent_hook_spool_dir() if hook_spool_dir is None else hook_spool_dir
        container_dir = CONTAINER_CONFIG_ROOT / kind
        if var is None:
            # mewbo/generic: no config-dir concept, nothing to share or point at.
            # No control mounts either — the launch flags that would name them
            # are all claude_code's, so mounting here would be machinery for a
            # path that cannot be taken.
            return cls(
                kind=kind,
                share=share,
                host_config_dir=workspace_config_dir,
                container_config_dir=container_dir,
            )
        host_dir = cls._host_config_dir(kind, home)
        if control_files is None:
            # Say so when an ENABLED feature is inert here. The file only
            # exists once its launch-time writer has run, so `exists` is what
            # separates "the operator turned this on" from "off by default", and
            # a silently-skipped feature is how one ends up debugging the wrong
            # layer — the same reason the egress allowlist is loud about an
            # address it refuses to emit a rule for.
            withheld = [path.name for path in cls.host_process_control_files() if exists(path)]
            if withheld:
                logger.info(
                    "container: not mounting {} — they register a stdio server "
                    "run by this host's interpreter, which no container has; the "
                    "matching launch flags are omitted rather than pointed at a "
                    "command that cannot start",
                    ", ".join(withheld),
                )
        plugin_seed = cls._plugin_seed_mounts(
            kind, share, host_dir=host_dir, container_dir=container_dir, exists=exists
        )
        mounts = (
            # The config root itself, FIRST and writable: the per-workspace host
            # directory IS the host side of the container's config dir, and
            # without this bind everything the agent writes there — its
            # transcript above all, plus the seeded `.claude.json` — lives and
            # dies inside the container, unreadable from the host. The shared
            # host paths below then layer on top of it (Docker mounts a target
            # before its own children), which is also why this one is `:rw`:
            # it is Grove's own directory, not the user's config.
            MountPlan(source=workspace_config_dir, target=container_dir, readonly=False),
            *(
                MountPlan(source=host_dir / name, target=container_dir / name, readonly=readonly)
                for name, readonly in cls._shared_names(kind, share)
                if exists(host_dir / name) and not cls._forbidden(kind, name)
            ),
            # The plugin seed, beside the config dir rather than inside it.
            *plugin_seed,
            # Grove's control plane. `:ro` unconditionally: the agent
            # only ever READS these, and a writable mount would let a
            # prompt-injected one rewrite the hook set that runs in every future
            # HOST session — the same reasoning that keeps settings/skills `:ro`
            # above. Unconditional on share level too: `isolated` isolates the
            # agent from the USER's config, and this is not that.
            *(
                MountPlan(source=path, target=CONTAINER_CONTROL_ROOT / path.name, readonly=True)
                for path in (
                    cls.default_control_files() if control_files is None else control_files
                )
                if exists(path)
            ),
            # The control plane's RETURN path, and the one mount here bound at
            # its own host path rather than under `/grove`. That is what lets a
            # single rendered hook command work in both namespaces: the
            # settings file naming this directory is written once, on the
            # host, and a container agent that falls back to spooling writes to
            # the very path the host reader drains. The git-common-dir mount
            # already establishes the same-absolute-path idiom for the same
            # reason — a value that has to mean one thing on both sides of the
            # boundary cannot be translated, only preserved.
            #
            # `:rw` and not gated on `exists`: Grove owns and creates it in
            # `seed()`, which runs before `up`. Writable is not a widening —
            # every agent on this host already shares one unauthenticated
            # sidecar plane under the user's own uid, so a container agent
            # writing here has exactly the reach a host agent always had. The
            # tokens that made a directory bind unacceptable for the config
            # dir are not in this directory and never will be.
            MountPlan(
                source=hook_spool_dir,
                target=PurePosixPath(hook_spool_dir.as_posix()),
                readonly=False,
            ),
        )
        seed_source = seed_target = None
        if kind in cls.SEED_KINDS:
            # The TARGET exists at every share level, the SOURCE does not:
            # `isolated` carries no sign-in by design, but it is also the level
            # whose config dir is virgin — so it is the one that most certainly
            # meets the trust dialog, and it needs somewhere to be told it has
            # already answered.
            seed_target = workspace_config_dir / cls.SEED_FILENAME
            if share != "isolated":
                seed_source = home / cls.SEED_FILENAME
        env = {var: str(container_dir)}
        if plugin_seed:
            # Only ever exported for a seed that was actually planned: a var
            # naming a path nothing mounted is the "live-looking config that
            # isn't", one directory over.
            env[cls.PLUGIN_SEED_ENV[kind]] = str(plugin_seed[0].target)
        return cls(
            kind=kind,
            share=share,
            host_config_dir=workspace_config_dir,
            container_config_dir=container_dir,
            mounts=mounts,
            # Set-and-unset: the value is exported explicitly AND any
            # inherited one is cleared, because a pane inherits the tmux server's
            # env which inherited the daemon's. Same generic seam, no new field —
            # and it covers the plugin-seed var for exactly the same reason.
            env=env,
            env_unset=tuple(env),
            seed_source=seed_source,
            seed_target=seed_target,
            hook_spool_dir=hook_spool_dir,
        )

    @classmethod
    def from_config(
        cls,
        cfg: ContainerConfig,
        *,
        kind: AgentKind,
        home: Path,
        workspace_config_dir: Path,
        control_files: Iterable[Path] | None = None,
        hook_spool_dir: Path | None = None,
        exists: Callable[[Path], bool] = Path.exists,
    ) -> AgentSharePlan:
        """`plan` with the share level read off the resolved cascade."""
        return cls.plan(
            kind=kind,
            share=cfg.agent_config.share,
            home=home,
            workspace_config_dir=workspace_config_dir,
            control_files=control_files,
            hook_spool_dir=hook_spool_dir,
            exists=exists,
        )

    @property
    def mount_flags(self) -> tuple[str, ...]:
        """Every mount as a `--mount` value, in plan order."""
        return tuple(mount.to_flag() for mount in self.mounts)

    @property
    def transcript_host_dir(self) -> Path | None:
        """The HOST directory the container's config root reads back as, or ``None``.

        The host half of the namespace bridge: a transcript written in-container
        under :attr:`container_config_dir` is readable here only through a
        mount, and only this plan knows which one. ``None`` — nothing
        reachable — for a kind with no config dir at all, and for any plan whose
        config root is not itself bound, because a directory the agent never
        writes into is an ACTIVE wrong answer for a reader that scopes its own
        ``CLAUDE_CONFIG_DIR``/``CODEX_HOME`` to it.

        The one subtlety is the ``share: projects`` shadow: that level binds the
        real host transcript directory OVER the config root's own, so the agent's
        transcripts land in the user's `~/.claude`, not in this workspace's
        directory — the mount table is what says so, which is why the answer is
        read off :attr:`mounts` rather than assumed from the share level.

        Deliberately NOT the mirror of ``agent_cwd``: that one stays the
        container's own string, matched opaquely against what the transcript
        recorded. Only the config dir crosses the boundary.
        """
        if self.kind not in self.CONFIG_DIR_ENV:
            return None
        shadows = {
            self.container_config_dir / name for name in self.SHARED_PROJECTS.get(self.kind, ())
        }
        root: Path | None = None
        for mount in self.mounts:
            if mount.target in shadows:
                return mount.source.parent
            if mount.target == self.container_config_dir:
                root = mount.source
        return root

    def container_control_path(self, host_path: Path) -> PurePosixPath | None:
        """Where the container sees Grove's control file *host_path*, or ``None``.

        The answer is read off :attr:`mounts` — the actual table handed to
        ``devcontainer up`` — rather than recomputed from the same rule that
        built it. That is the whole point: ``None`` means *this file is not
        mounted*, and a caller that emits a flag anyway names a file the agent
        cannot open. Claude Code treats a missing ``--settings`` as **fatal**,
        so silently emitting one untranslated path takes down the whole
        workspace; an answer derived from a second copy of the rule could not
        have seen the one case that mattered (a control file that did not
        exist at plan time and so was dropped).
        """
        target = CONTAINER_CONTROL_ROOT / host_path.name
        return next(
            (
                mount.target
                for mount in self.mounts
                if mount.target == target and mount.source == host_path
            ),
            None,
        )

    def seed(self, *, trust: TrustStamp | None = None) -> Path | None:
        """Prepare this share on disk: the config dir, then the `.claude.json` copy.

        The create-time I/O. The directory is created even when there is nothing
        to seed, because it is a bind SOURCE: Docker materializes a missing one
        as a new root-owned directory on the host, which the in-container agent
        (mapped to the host uid) then cannot write into — the same hazard the
        `exists` probe in :meth:`plan` exists for, on Grove's own directory.

        The COPY is idempotent by existence — a resume must not overwrite the
        config the workspace has been running with, and re-seeding would
        silently revert an in-container change the user made on purpose. The
        *trust* stamp is the deliberate exception, and it is not a re-seed: it
        is folded into whatever the file already holds, on every provision, so a
        workspace created before the stamp existed and one whose ``.mcp.json``
        gained a server both get it at their next start. Refusing to write is
        still the fallback for a target that cannot be READ — the stamp is worth
        an interactive prompt, never somebody's config file.

        Best-effort otherwise: an unreadable or unparseable host file yields an
        empty seed rather than failing create, because a workspace with no
        sign-in carried is degraded, not broken. Returns the written (or kept)
        path, or ``None`` when this plan seeds nothing.
        """
        if self.mounts:
            self.host_config_dir.mkdir(parents=True, exist_ok=True)
            if self.hook_spool_dir is not None:
                # Same hazard as the config root above, one directory over: an
                # absent bind source is materialized root-owned, and the writer
                # here is a shell redirect in somebody else's container.
                self.hook_spool_dir.mkdir(parents=True, exist_ok=True)
        if self.seed_target is None:
            return None
        stamp = trust if self.stamps_trust(trust) else None
        payload: dict[str, object]
        if self.seed_target.exists():
            if stamp is None:
                return self.seed_target
            existing = self._read_object(self.seed_target)
            if existing is None:
                return self.seed_target
            payload = existing
        elif self.seed_source is not None:
            payload = self.curate(self._read_object(self.seed_source) or {})
        elif stamp is None:
            return None
        else:
            # `isolated`: nothing carries over, but the folder still has to be
            # told it is trusted, so the stamp alone is the whole file.
            payload = {}
        if stamp is not None:
            self.apply_trust(payload, stamp)
        self.seed_target.parent.mkdir(parents=True, exist_ok=True)
        self.seed_target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        logger.debug("agent config seed: wrote {}", self.seed_target)
        return self.seed_target

    def stamps_trust(self, trust: TrustStamp | None) -> bool:
        """Whether *trust* actually marks anything for this plan's kind."""
        return trust is not None and bool(trust.workspace_folder) and self.kind in self.TRUST_KEYS

    def apply_trust(self, payload: dict[str, object], trust: TrustStamp) -> None:
        """Fold *trust* into a seed *payload*, in place.

        A MERGE, not a replacement, at both levels: the tool writes its own
        per-folder bookkeeping (last session, costs) into the same entry while
        it runs, and a provision that dropped it would quietly reset the
        workspace's own history on every resume. Only the keys Grove is
        answering for are overwritten.
        """
        keys = self.TRUST_KEYS.get(self.kind)
        if keys is None or not trust.workspace_folder:
            return
        raw = payload.get("projects")
        projects: dict[str, object] = dict(raw) if isinstance(raw, dict) else {}
        prior = projects.get(trust.workspace_folder)
        entry: dict[str, object] = dict(prior) if isinstance(prior, dict) else {}
        entry.update(keys)
        mcp_key = self.TRUST_MCP_KEY.get(self.kind)
        if mcp_key is not None:
            entry[mcp_key] = list(trust.mcp_servers)
        projects[trust.workspace_folder] = entry
        payload["projects"] = projects

    @classmethod
    def curate(cls, raw: Mapping[str, object]) -> dict[str, object]:
        """The seeded copy's contents: everything but :attr:`SEED_DROP_KEYS`."""
        return {k: v for k, v in raw.items() if k not in cls.SEED_DROP_KEYS}

    @staticmethod
    def _read_object(path: Path) -> dict[str, object] | None:
        """*path* as a JSON object, or ``None`` when it is not readable as one."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("agent config seed: {} unreadable ({})", path, exc)
            return None
        return raw if isinstance(raw, dict) else None

    @classmethod
    def _host_config_dir(cls, kind: AgentKind, home: Path) -> Path:
        """The kind's real host config dir (the share source)."""
        return home / (".claude" if kind == "claude_code" else ".codex")

    @classmethod
    def _shared_names(cls, kind: AgentKind, share: AgentShare) -> tuple[tuple[str, bool], ...]:
        """(name, readonly) pairs this share level exposes, tightest level first."""
        if SHARE_RANK[share] <= SHARE_RANK["isolated"]:
            return ()
        projects = tuple((name, False) for name in cls.SHARED_PROJECTS.get(kind, ()))
        if share == "projects":
            return projects
        return (
            *((name, True) for name in cls.SHARED_RO.get(kind, ())),
            *((name, False) for name in cls.SHARED_RW.get(kind, ())),
        )

    @classmethod
    def _plugin_seed_mounts(
        cls,
        kind: AgentKind,
        share: AgentShare,
        *,
        host_dir: Path,
        container_dir: PurePosixPath,
        exists: Callable[[Path], bool],
    ) -> tuple[MountPlan, ...]:
        """The host plugin root as a read-only SEED, or ``()``.

        Zero or one mount, so the caller can splice it into the table and derive
        the env from the same value — the var must name a path that was actually
        bound, and building both off one answer is what keeps that true.

        Gated on the same rank as :attr:`SHARED_RO`, because a plugin root IS
        that payload: ``projects`` shares transcripts only and ``isolated``
        shares nothing. The ``exists`` drop rule applies unchanged — an absent
        host directory would otherwise be materialized root-owned on the host.
        """
        if kind not in cls.PLUGIN_SEED_ENV or SHARE_RANK[share] < SHARE_RANK["full"]:
            return ()
        source = host_dir / cls.PLUGIN_SEED_NAME
        if not exists(source):
            return ()
        return (MountPlan(source=source, target=container_dir / cls.PLUGIN_SEED_TARGET),)

    @classmethod
    def _forbidden(cls, kind: AgentKind, name: str) -> bool:
        """Whether *name* is on the never-mount list for *kind*.

        A belt-and-braces second gate on the tables above: the SQLite invariant
        is a data-corruption bug, not a preference, so it is enforced where the
        mount is emitted rather than only by which names the tables happen to
        list.
        """
        if kind != "codex":
            return False
        return any(name.startswith(prefix) for prefix in cls.CODEX_NEVER)


@dataclass(frozen=True, slots=True)
class CuratedGitConfig:
    """Git identity/config for the container, curated key by key.

    The host `~/.gitconfig` is **never mounted**: it carries credential helpers,
    signing keys and machine-local paths — the exact things a blast-radius
    boundary should not hand over, and half of them name host paths that do not
    exist in the container anyway. Instead an allowlist of settings is forwarded
    through git's own `GIT_CONFIG_COUNT`/`_KEY_n`/`_VALUE_n` protocol, which
    reuses the mechanism `GroveOverlay.git_env` already established for
    `safe.directory` and writes no file the project can see.

    `safe.directory` is always emitted: the mounted worktree is owned by the host
    uid, so without it every in-container git command fails on dubious ownership.
    """

    entries: tuple[tuple[str, str], ...] = ()

    #: Settings worth carrying. Identity so commits are attributed correctly,
    #: plus the few ergonomic defaults whose absence is confusing. Everything
    #: else — credential helpers above all — is deliberately left behind.
    ALLOWED: ClassVar[tuple[str, ...]] = (
        "user.name",
        "user.email",
        "user.signingkey",
        "init.defaultbranch",
        "core.editor",
        "pull.rebase",
        "push.default",
        "rebase.autostash",
    )

    #: Forced regardless of the host's own value.
    FORCED: ClassVar[tuple[tuple[str, str], ...]] = (("safe.directory", "*"),)

    @classmethod
    def curate(cls, host_entries: Mapping[str, str]) -> CuratedGitConfig:
        """Build from a host `git config --global --list` mapping (caller-read).

        Pure: the caller supplies the mapping, so this stays testable and the
        subprocess stays at the launch boundary where side effects belong.
        Unknown keys are dropped silently — an allowlist that warns about every
        credential helper it skips would be noise on every single launch.
        """
        allowed = {key.lower(): value for key, value in host_entries.items()}
        kept = tuple(
            (key, allowed[key]) for key in cls.ALLOWED if allowed.get(key, "").strip() != ""
        )
        return cls(entries=(*cls.FORCED, *kept))

    def to_env(self) -> dict[str, str]:
        """The `GIT_CONFIG_*` env carrying these settings into the container.

        Supersedes (not merges with) `GroveOverlay.git_env`'s single-entry
        `safe.directory` form: both write `GIT_CONFIG_COUNT`, so the caller must
        let this one win the dict merge — it re-emits `safe.directory` itself, so
        nothing is lost.
        """
        env = {"GIT_CONFIG_COUNT": str(len(self.entries))}
        for index, (key, value) in enumerate(self.entries):
            env[f"GIT_CONFIG_KEY_{index}"] = key
            env[f"GIT_CONFIG_VALUE_{index}"] = value
        return env


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    """Where a containerized agent may reach, as an in-container firewall.

    With credentials shared and permission prompts off, egress is the control
    that carries the weight — it bounds where a token can be *sent*. The
    allowlist is **derived**: agent plane for the workspace's kind, package
    indexes, the repo's own git remotes, the Grove plane, plus whatever
    `egress.allow` adds. Normal dev work therefore needs zero configuration, and
    a self-hosted LAN forge is first-class because it comes from `git remote`.

    One mechanism, not two: the firewall is applied *inside* the container on
    every start (`resume` re-runs it, since `devcontainer up` re-runs
    `postStartCommand`). The Grove-owned proxy sidecar was cut — a second
    parallel mechanism plus a host-scoped network plus a sidecar to manage,
    for the same guarantee.

    **Both address families are covered, asymmetrically and deliberately:**
    IPv4 gets the derived allowlist; IPv6 is denied outright. See
    :meth:`_ipv6_lines` for why denial rather than a mirrored allowlist. Before
    that, the allowlist was an IPv4 allowlist and `ip6tables -P OUTPUT` was
    `ACCEPT`, so anything reachable over v6 bypassed the boundary entirely —
    latent only because docker's bridge ships no working v6 route.

    Ceilings, stated rather than papered over: UDP/53 stays open in `allowlist`
    (DNS tunneling is not defended against), name-based filtering loses to
    domain fronting, and hostnames are resolved once at apply time so an
    allowed name whose address changes mid-session stops resolving to an allowed
    address. `ipset` is deliberately unused (absent on the reference host) —
    plain iptables chains only.
    """

    #: Narrow, not ``str``: every consumer below branches on this by comparison
    #: (``fail_closed``, ``post_start_command``, ``firewall_script``). A typo'd
    #: mode would read as "not open" and silently generate the allowlist
    #: firewall instead of raising — the wrong direction to fail in for a
    #: security control, and invisible at the call site.
    mode: EgressMode
    hosts: tuple[str, ...] = ()
    cidrs: tuple[str, ...] = ()

    #: Written inside the worktree, next to the generated override config, so the
    #: container sees it through the workspace mount with no extra bind.
    SCRIPT_RELPATH: ClassVar[PurePosixPath] = PurePosixPath(".devcontainer/.grove-egress.sh")

    #: The capabilities the script needs. The caller adds these to the overlay's
    #: `capAdd`; without them the script cannot apply and the start fails closed.
    CAP_ADD: ClassVar[tuple[str, ...]] = ("NET_ADMIN", "NET_RAW")

    #: Probed after applying to prove the default-DROP is real. A public resolver
    #: that is not on any derived list — reachable from an unfirewalled container,
    #: unreachable from a working one — so a silently-inert ruleset fails the
    #: start instead of pretending to be a boundary.
    VERIFY_BLOCKED: ClassVar[str] = "1.1.1.1/443"

    #: Binaries the script needs INSIDE the container, checked before it touches
    #: anything. None is in a base image by contract: `iptables` is absent
    #: from `python:*-slim`, `node:*`, alpine and plain `ubuntu` — and from
    #: `mcr.microsoft.com/devcontainers/base:ubuntu`, the image Grove's OWN
    #: packaged default names, which survives only because the Features chain
    #: happens to drag it in. `ip` (iproute2) is the same class of absence and
    #: the workspace-network loop needs it. `ip6tables` ships in the same
    #: distro package as `iptables`, so requiring it costs the user nothing
    #: beyond the remediation they already have to run — and keeping it in the
    #: one probe table means one diagnosis shape rather than two.
    REQUIRED_BINARIES: ClassVar[tuple[str, ...]] = ("iptables", "ip", "ip6tables")

    #: Present iff the kernel has an IPv6 stack at all. The v6 arm is gated on
    #: it because `ipv6.disable=1` leaves `ip6tables` installed but unable to
    #: initialize its table, and `set -e` would turn that into a failed
    #: container start on a machine that has *no IPv6 to leak through* — a
    #: refusal with nothing to refuse. This is not a fail-open escape hatch: no
    #: IPv6 stack is a verifiable absence of the thing being filtered, unlike a
    #: missing binary, which is an inability to filter.
    IPV6_STACK_PROBE: ClassVar[str] = "/proc/net/if_inet6"

    @classmethod
    def derive(
        cls,
        cfg: EgressConfig,
        *,
        kind: AgentKind,
        remote_urls: Iterable[str] = (),
        fetch_ranges: Callable[[RangeSource], tuple[str, ...]] = fetch_published_ranges,
    ) -> EgressPolicy:
        """Resolve the effective destination set for one workspace.

        `remote_urls` comes from `GitRepo.remote_urls()` at the boundary; only
        the host part is kept (a URL's path is not a destination).

        `fetch_ranges` is the one impure edge, injected so the planner stays
        testable without a network and defaulted so the provisioner needs no
        change. It is **additive**: a provider's published CIDRs join the
        hostname entries rather than replacing them, so a failed fetch degrades
        to the hostname-only allowlist. The hostnames it supplements are kept
        for the same reason — belt and braces cost one resolved `/32` each.
        """
        if cfg.mode == "open":
            return cls(mode="open")
        if cfg.mode == "deny":
            return cls(mode="deny")
        published: list[str] = []
        for source in cfg.range_sources:
            published.extend(fetch_ranges(source))
        raw: list[str] = [
            *cfg.agent_plane.get(kind, ()),
            *cfg.package_plane,
            *cfg.grove_plane,
            *(cls.remote_host(url) for url in remote_urls),
            *cfg.allow,
            *published,
        ]
        hosts: dict[str, None] = {}
        cidrs: dict[str, None] = {}
        for entry in raw:
            value = entry.strip()
            if not value:
                continue
            if cls.is_unusable(value):
                # A literal unspecified address is never a destination, and a
                # rule for one reads as an allow while permitting nothing.
                logger.warning("egress: skipping unusable destination {!r}", value)
                continue
            (cidrs if cls._is_address(value) else hosts).setdefault(value, None)
        return cls(mode="allowlist", hosts=tuple(hosts), cidrs=tuple(cidrs))

    @staticmethod
    def is_unusable(value: str) -> bool:
        """Whether *value* is an address no packet can usefully be sent to.

        The unspecified address (`0.0.0.0`, and any prefix of it) is the case
        that bites: **Pi-hole and AdGuard both answer a blocked name with
        `0.0.0.0` by default**, so a perfectly ordinary allowlist entry on an
        ad-blocking network resolves to it and becomes
        `-A OUTPUT -d 0.0.0.0/32 -j ACCEPT` — a rule that looks like an allow,
        permits nothing, and leaves the operator reading a chain that appears to
        contain the entry they asked for. Observed live in a real workspace.

        A hazard on other people's machines rather than a quirk of this one,
        which is why it is filtered here *and* in the generated script: this
        catches config-supplied literals, the shell catches whatever the
        container's own resolver hands back at apply time.
        """
        head = value.split("/", 1)[0].strip()
        return head in {"0.0.0.0", "::"}

    @property
    def fail_closed(self) -> bool:
        """Whether a firewall that failed to apply must fail the container start.

        True for every mode but `open`. This is config integrity — the user asked
        for a boundary and did not get one — and explicitly NOT a permission
        gate: `open` is a supported, un-nagged path, one config line away.
        """
        return self.mode != "open"

    def post_start_command(self) -> str:
        """The `postStartCommand` string that applies this policy, or ``""``.

        `sudo -n` because the remote user is typically not root while `iptables`
        requires `CAP_NET_ADMIN` in the container's user namespace; a container
        whose remote user IS root has a passthrough `sudo` or none, hence the
        fallback. Empty for `open` — nothing to run, so nothing can fail.
        """
        if self.mode == "open":
            return ""
        target = shlex.quote(str(self.SCRIPT_RELPATH))
        return f"sudo -n bash {target} || bash {target}"

    def write_script(self, worktree: Path) -> Path | None:
        """Write the firewall script into *worktree*. The other create-time I/O.

        Inside the worktree (next to the generated override config) so it rides
        the workspace mount the container already has — a script parked in a
        Grove state dir would need a bind mount of its own just to be runnable.
        """
        script = self.firewall_script()
        if not script:
            return None
        target = worktree / self.SCRIPT_RELPATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(script, encoding="utf-8")
        target.chmod(0o755)
        logger.debug("egress: wrote firewall script {} ({} mode)", target, self.mode)
        return target

    def _payload_lines(self) -> list[str]:
        """Fall back to Grove's own bundled ``iptables`` when the image has none.

        The most standard devcontainer base image there is ships no ``iptables``
        at all, so before this the DEFAULT configuration could not start a
        workspace for a large class of ordinary projects — it hit
        :meth:`_preflight_lines` and refused. Grove now builds a static pair and
        bind-mounts it (see
        :class:`~grove.core.container_netfilter.NetfilterPayload`); these lines
        are the container's half of that.

        **A fallback, never an override.** An image that provides its own
        ``iptables`` keeps it: that binary matches the netfilter backend the
        rest of the image was built against, which matters most where a nested
        ``dockerd`` writes its own ``DOCKER-USER`` rules that Grove's have to
        compose with.

        **Selection is by EXECUTION, not by an architecture name.** The payload
        directory is named by the BUILD host's ``uname -m``, and a container can
        run a different architecture — so the loop asks each candidate to state
        its version and takes the first that answers, which needs no
        architecture map here or on the host and skips a wrong-arch binary
        instead of picking it and dying at ``Exec format error``. With no mount
        at all the glob does not expand, the guard rejects the literal path, and
        these lines are inert.
        """
        return [
            "# Grove ships a static iptables for images that provide none (#293) —",
            "# a FALLBACK only: an image with its own keeps it, because that binary",
            "# matches the netfilter backend the rest of that image was built for.",
            "if ! command -v iptables >/dev/null 2>&1; then",
            f"  for dir in {CONTAINER_NETFILTER_BIN}/*; do",
            '    [ -x "$dir/iptables" ] || continue',
            # `--version` rather than a name comparison: this is also the
            # wrong-architecture test, and only running it can answer that.
            '    "$dir/iptables" --version >/dev/null 2>&1 || continue',
            '    PATH="$dir:$PATH"',
            "    export PATH",
            '    echo "grove: using Grove\'s bundled iptables from $dir" >&2',
            "    break",
            "  done",
            "fi",
            "",
        ]

    def _preflight_lines(self) -> list[str]:
        """Refuse with a diagnosis when the image lacks the firewall's tools.

        Fails rather than skips, deliberately: ``fail_closed`` is the contract
        the user asked for, and an agent running unbounded inside a container
        the user believes is firewalled is the one outcome worse than a failed
        start. What changes is only that the refusal NAMES the missing binary,
        the image's role in it, and the one-line way out. ``grove doctor``
        cannot check this for them — it inspects the host, and this is a
        property of the project's container image.

        This is the RESIDUAL arm rather than the common one: Grove's own
        bundle (:meth:`_payload_lines`) covers ``iptables``/``ip6tables``, so
        what reaches here is an image missing ``ip`` (iproute2), a container
        on an architecture the host could not build for, or a host where the
        build itself could not run. The message names the binary either way,
        which is the fact the remedy turns on.
        """
        message = (
            f"grove: egress mode '{self.mode}' needs '$bin', which this container image does "
            "not provide and Grove has no bundled copy of for this container's architecture. "
            "Install it in the image (Debian/Ubuntu: apt-get install -y "
            "iptables iproute2), or opt out with container.egress.mode = 'open'."
        )
        return [
            "# Probed before anything is touched. The IMAGE has to provide these;",
            "# `grove doctor` inspects the host and cannot see inside it.",
            f"for bin in {' '.join(self.REQUIRED_BINARIES)}; do",
            '  command -v "$bin" >/dev/null 2>&1 && continue',
            f'  echo "{message}" >&2',
            "  exit 1",
            "done",
            "",
        ]

    def _external_interface_lines(self) -> list[str]:
        """Resolve the interface egress actually LEAVES by, or refuse.

        The nested arm needs to tell "this packet is leaving the container" from
        "this packet is moving between two of the container's own bridges", and
        the only honest discriminator is the interface carrying the default
        route. Refusing when it cannot be determined is the same fail-closed
        rule the binary probe follows: not knowing where the boundary IS makes
        it unenforceable, which is an inability to filter, not an absence of
        anything to filter.
        """
        return [
            "ext=$(ip route show default 2>/dev/null | awk '{print $5}' | head -1)",
            'if [ -z "$ext" ]; then',
            '  echo "grove: cannot determine the external interface; refusing to leave'
            ' nested egress unfiltered" >&2',
            "  exit 1",
            "fi",
            "",
        ]

    def _nested_lines(self) -> list[str]:
        """Apply the same policy to a NESTED daemon's containers.

        A docker-in-docker workspace **routes** its nested containers' traffic
        instead of originating it, so none of it traverses OUTPUT and the entire
        allowlist missed it: one `docker run alpine curl …` reached anything.
        Measured side by side in one container — workspace `1.1.1.1:443`
        BLOCKED, nested `1.1.1.1:443` OPEN — and live rather than latent, since
        Grove's own devcontainer runs a nested daemon.

        **Why `DOCKER-USER` and not `FORWARD`.** Docker jumps to `DOCKER-USER`
        from the top of FORWARD, ahead of its own ACCEPTs, and treats the chain
        as the operator's. Verified on Docker 29.6.1 rather than trusted: rules
        placed there BEFORE `dockerd` first starts survive its startup, and
        `dockerd` adds the jump itself. That is what makes this immune to the
        ordering war — Grove's `postStartCommand` and the nested daemon's start
        can happen in either order. Owning FORWARD directly would lose that race
        (`dockerd` inserts its rules above ours) and flushing it after the fact
        would tear out the nested daemon's own networking.

        **Why not simply `-P FORWARD DROP`.** Nested container-to-container
        traffic is forwarded too, and it never leaves this container — it is
        inside the blast radius by construction, not egress. A blanket drop
        would break nested compose stacks and the nested daemon's embedded DNS
        while bounding nothing extra, so the rule keys off the external
        interface: anything not leaving by it RETURNs untouched.

        `RETURN`, not `ACCEPT`: this is a pre-chain, and allowed traffic must
        fall through to docker's own rules rather than short-circuit them.
        """
        return [
            "",
            "# Nested containers (#262): a docker-in-docker workspace ROUTES their",
            "# traffic rather than originating it, so it never reaches OUTPUT and the",
            "# allowlist above does not see it. DOCKER-USER is where docker guarantees",
            "# us a seat ahead of its own ACCEPTs, and it is never flushed by dockerd.",
            "iptables -N DOCKER-USER 2>/dev/null || true",
            "iptables -F DOCKER-USER",
            "iptables -A DOCKER-USER -m state --state ESTABLISHED,RELATED -j RETURN",
            "# Not leaving by the external interface = not leaving this container:",
            "# nested container-to-container and the nested daemon's embedded DNS.",
            "# Denying it would break the nested daemon rather than bound it.",
            'iptables -A DOCKER-USER ! -o "$ext" -j RETURN',
            "",
        ]

    def _nested_jump_lines(self) -> list[str]:
        """Make FORWARD reach `DOCKER-USER` even when no nested daemon ever runs.

        `dockerd` installs the jump when it starts, so the common case needs
        nothing. Adding it when absent costs one rule and removes a footgun: a
        reader comparing the chains should never find a populated policy chain
        that nothing jumps to, and concluding "the rules are there" from that
        is exactly the kind of half-truth this whole arm exists to kill.
        """
        return [
            'if ! iptables -S FORWARD | grep -q -- "-j DOCKER-USER"; then',
            "  iptables -A FORWARD -j DOCKER-USER",
            "fi",
        ]

    @staticmethod
    def _assert_rule_lines(*, dump: str, needle: str, failure: str) -> list[str]:
        """Fail closed unless *needle* appears in the *dump* of a chain.

        **Never `iptables -S … | head -1 | grep -q`,** which is what this
        replaces. Both `head -1` and `grep -q` exit at their first match and
        close the pipe, `iptables` dies of SIGPIPE (141), and `set -o pipefail`
        promotes that to the pipeline's status — so the assertion reported
        "egress firewall did not apply" and failed the container start while the
        policy was demonstrably applied. Measured exactly that: exit 141 with
        `-P OUTPUT DROP` sitting in the very output being tested, on a ruleset
        large enough to fill the pipe buffer — which is every real workspace.

        A self-check that fails on a correct firewall is as bad as one that
        passes on a broken one: both teach the operator to distrust it. Command
        substitution reads to EOF and `case` is a shell builtin, so there is no
        second process to kill mid-write.
        """
        return [
            f'rules="$({dump})"',
            'case "$rules" in',
            f'  *"{needle}"*) ;;',
            f'  *) echo "{failure}" >&2; exit 1 ;;',
            "esac",
        ]

    def _ipv6_lines(self) -> list[str]:
        """Deny every IPv6 destination — the symmetric arm of the v4 policy.

        **Denial, not a mirrored allowlist.** Allowlisting v6 means resolving
        AAAA alongside A for every allowed name and carrying a second rule set
        that no configuration currently exercises: docker's bridge has no
        working v6 route by default, so the mirrored arm would be untested
        machinery guarding a path nothing uses. Denial is fail-closed by
        construction and matches what the runtime actually provides. If a
        deployment genuinely needs v6 egress, THIS method is where the
        allowlist grows — the derived host/CIDR sets above are family-agnostic
        already.

        **DROP is the boundary; REJECT is why the boundary is not also a
        latency bug.** A dropped SYN blackholes, so a dual-stack client that
        tries v6 first waits out its full connect timeout before Happy Eyeballs
        falls back to v4 (measured on this host: one `curl` ate 15s where
        `curl -4` returned in 60ms). An ICMPv6 rejection fails `connect()`
        immediately, so the v4 attempt starts now. It is a courtesy, not the
        control — the policy below denies with or without it — hence
        best-effort against an image whose kernel lacks the REJECT target.
        """
        return [
            "",
            "# IPv6 (#260): denied outright. Everything above is an IPv4 policy, so",
            "# without this arm any v6-reachable destination bypasses the boundary",
            "# entirely — silently, since the v4 rules stay present and correct.",
            f"if [ -e {self.IPV6_STACK_PROBE} ]; then",
            "  ip6tables -F OUTPUT",
            "  ip6tables -P OUTPUT ACCEPT",
            "  ip6tables -A OUTPUT -o lo -j ACCEPT",
            "  # Link-local unicast + multicast: NDP. Dropping neighbor discovery",
            "  # would not tighten anything (it never leaves the segment) and makes",
            "  # the local network misbehave in ways that read as Grove bugs.",
            "  ip6tables -A OUTPUT -d fe80::/10 -j ACCEPT",
            "  ip6tables -A OUTPUT -d ff02::/16 -j ACCEPT",
            "  ip6tables -A OUTPUT -j REJECT --reject-with adm-prohibited 2>/dev/null ||",
            '    echo "grove: ip6tables REJECT unavailable; v6 attempts will time out'
            ' rather than fail fast" >&2',
            "  ip6tables -P OUTPUT DROP",
            *(
                f"  {line}"
                for line in self._assert_rule_lines(
                    dump="ip6tables -S OUTPUT",
                    needle="-P OUTPUT DROP",
                    failure="grove: ipv6 egress firewall did not apply",
                )
            ),
            "else",
            '  echo "grove: kernel reports no IPv6 stack; nothing to filter" >&2',
            "fi",
            "",
        ]

    def firewall_script(self) -> str:
        """The in-container firewall script, or ``""`` for `open`.

        Deterministic for a given policy (sorted nothing, ordered as derived) so
        it is unit-testable and diffable. Structure: flush, allow loopback and
        established, allow the container's OWN networks (its compose stack is
        reachable by design — and it is per-workspace, never a shared
        cross-workspace network), allow the resolved destinations, then default
        DROP and verify — and finally the IPv6 arm (:meth:`_ipv6_lines`), which
        runs LAST so the v4 policy is fully applied and proven before a second
        address family can fail the start.
        """
        if self.mode == "open":
            return ""
        lines = [
            "#!/usr/bin/env bash",
            "# Generated by Grove (container egress policy). Do not edit — it is",
            "# rewritten from config on every workspace start.",
            "set -euo pipefail",
            "",
            # Grove's own bundle first, so the probe below sees what the policy
            # will actually run with.
            *self._payload_lines(),
            # Probe BEFORE touching anything. Without this the first `iptables`
            # call dies at `command not found` under `set -e`, the
            # postStartCommand exits 127, and the user is told only that
            # `.grove-egress.sh: line 6: iptables: command not found` — a
            # diagnosis that names neither the cause nor the way out, on an
            # image that never claimed to ship a firewall.
            *self._preflight_lines(),
            *self._external_interface_lines(),
            "iptables -F OUTPUT",
            "iptables -P OUTPUT ACCEPT",
            "iptables -A OUTPUT -o lo -j ACCEPT",
            "iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT",
            *self._nested_lines(),
            "# One destination, both chains. Emitted once so the policy the agent",
            "# runs under and the policy its nested containers run under cannot",
            "# drift — which is exactly how the nested half came to be missing.",
            "allow() {",
            "  # Never emit a rule for an address nothing can be sent to (#265).",
            "  # Pi-hole and AdGuard answer a blocked name with 0.0.0.0 by",
            "  # default, which would turn an ordinary allowlist entry into a",
            "  # rule that reads as an allow and permits nothing. Loud, because",
            "  # a silently-skipped entry is how the operator ends up debugging",
            "  # the wrong layer.",
            '  case "${1%%/*}" in',
            "    0.0.0.0|'')",
            '      echo "grove: skipping unusable address ${1} (resolver returned'
            ' the unspecified address)" >&2',
            "      return 0",
            "      ;;",
            "  esac",
            '  iptables -A OUTPUT -d "$1" -j ACCEPT',
            '  iptables -A DOCKER-USER -d "$1" -j RETURN',
            "}",
            "",
            "# The workspace's own networks: its stack services are reachable by",
            "# design. Derived from the container's own interfaces, so no host",
            "# range is ever guessed at.",
            "for net in $(ip -o -f inet addr show scope global | awk '{print $4}'); do",
            '  allow "$net"',
            "done",
            "",
        ]
        if self.mode == "deny":
            lines += [
                "# mode=deny: loopback and the workspace network only — DNS included.",
            ]
        else:
            lines += [
                "# DNS stays open: name resolution is required for every allowed",
                "# hostname below. A documented ceiling (DNS tunneling), not an oversight.",
                "iptables -A OUTPUT -p udp --dport 53 -j ACCEPT",
                "iptables -A OUTPUT -p tcp --dport 53 -j ACCEPT",
                "iptables -A DOCKER-USER -p udp --dport 53 -j RETURN",
                "iptables -A DOCKER-USER -p tcp --dport 53 -j RETURN",
                "",
            ]
            lines += [f'allow "{cidr}"' for cidr in self.cidrs]
            if self.hosts:
                lines += [
                    "",
                    "# Hostnames resolve to addresses once, here. A name whose address",
                    "# changes mid-session stops matching until the next start.",
                    f"for host in {' '.join(shlex.quote(h) for h in self.hosts)}; do",
                    "  addrs=$(getent ahostsv4 \"$host\" | awk '{print $1}' | sort -u)",
                    "  # A name that resolves to NOTHING contributes no rule, and until",
                    "  # this line it did so in silence — `allow` is loud about an",
                    "  # unusable 0.0.0.0, but an unresolvable name never reaches it, so",
                    "  # the entry simply vanished from the firewall on every start.",
                    "  # Measured live: `host.docker.internal` returns zero addresses in",
                    "  # a plain bridge-network container, so a Grove-plane allowlist",
                    "  # entry was being dropped with nothing said. Same invariant as",
                    "  # the 0.0.0.0 skip: never drop a rule quietly.",
                    '  [ -n "$addrs" ] || echo "grove: egress allowlist skipping'
                    " $host — it resolves to no IPv4 address from inside this"
                    ' container; anything reached by that name will be blocked" >&2',
                    "  for addr in $addrs; do",
                    '    allow "$addr"',
                    "  done",
                    "done",
                ]
        lines += [
            "",
            "iptables -P OUTPUT DROP",
            "# The nested chain has no policy of its own (user chains cannot have",
            "# one), so its terminal DROP is the deny — and it is scoped to the",
            "# external interface, since everything else already RETURNed above.",
            'iptables -A DOCKER-USER -o "$ext" -j DROP',
            *self._nested_jump_lines(),
            "",
            "# Fail closed: prove the policy is real rather than trusting that the",
            "# commands above returned 0. A start with an inert ruleset would be a",
            "# boundary the user believes in and does not have.",
            *self._assert_rule_lines(
                dump="iptables -S OUTPUT",
                needle="-P OUTPUT DROP",
                failure="grove: egress firewall did not apply",
            ),
            # Structural, because the runtime canary below CANNOT cover this
            # arm: probing it for real means starting a nested container, which
            # needs a nested daemon that may not have started yet and an image
            # pulled through the network we just firewalled.
            *self._assert_rule_lines(
                dump="iptables -S DOCKER-USER",
                needle="-j DROP",
                failure="grove: nested-container egress policy did not apply",
            ),
            *self._assert_rule_lines(
                dump="iptables -S FORWARD",
                needle="-j DOCKER-USER",
                failure="grove: nested-container egress policy is not reachable from FORWARD",
            ),
            f"probe={shlex.quote(self.VERIFY_BLOCKED)}",
            "# Scope, stated because it was read as broader than it is: this proves",
            "# the ORIGINATING namespace only. It ran green for months while a",
            "# nested container reached this very address freely (#262) — the",
            "# nested arm is asserted structurally above, not by this probe.",
            'if timeout 3 bash -c "</dev/tcp/${probe%/*}/${probe##*/}" 2>/dev/null; then',
            '  echo "grove: egress firewall inert (${probe} still reachable)" >&2',
            "  exit 1",
            "fi",
            # No v6 counterpart to the reachability probe: with no v6 route
            # (today's runtime) an unfirewalled container fails the probe too,
            # so it would prove nothing while costing every start the timeout.
            # The policy-line check inside the arm is the assertion that bites.
            *self._ipv6_lines(),
        ]
        return "\n".join(lines)

    @staticmethod
    def remote_host(url: str) -> str:
        """The host of a git remote URL, for both URL and `scp`-like forms."""
        value = url.strip()
        if not value:
            return ""
        if "://" in value:
            return urlsplit(value).hostname or ""
        # `git@host:org/repo.git` — scp syntax, which urlsplit does not parse.
        if ":" in value and "/" not in value.split(":", 1)[0]:
            authority = value.split(":", 1)[0]
            return authority.split("@")[-1]
        return ""

    @staticmethod
    def _is_address(value: str) -> bool:
        """Whether *value* is a literal address/CIDR rather than a hostname."""
        if "/" in value:
            return True
        head = value.split(".", 1)[0]
        return head.isdigit()


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    """Per-container caps, applied post-hoc with ``docker update``.

    ONE application point, on every runtime. The devcontainer CLI creates the
    container — one image or a compose stack, it makes no difference — and
    exposes no resource knobs, so an update after ``up`` is the one dependable
    place. ``up`` reports the workspace service's container id for a stack too,
    so the compose path needs nothing of its own: a ``deploy.resources``
    compose fragment would be a second application point for the same
    guarantee, and is deliberately not built.

    Empty is the identity: no configured field means no flag at all, so an
    unconfigured workspace runs exactly as it does today.
    """

    memory: str = ""
    cpus: str = ""
    pids: int = 0

    @classmethod
    def from_config(cls, cfg: ResourcesConfig) -> ResourceLimits:
        """Read the caps off the resolved cascade."""
        return cls(memory=cfg.memory.strip(), cpus=cfg.cpus.strip(), pids=max(cfg.pids, 0))

    @property
    def empty(self) -> bool:
        """Whether this policy caps nothing (the default)."""
        return not (self.memory or self.cpus or self.pids)

    def docker_update_argv(self, container: str, *, docker_bin: str = "docker") -> tuple[str, ...]:
        """`docker update` argv for the agent container, or ``()`` when uncapped.

        Post-`up` on purpose: the devcontainer CLI owns creation and exposes no
        resource knobs, so an update is the one dependable application point.

        **`--memory` is always paired with `--memory-swap`, or the memory cap
        never applies on ANY runtime.** Docker refuses ``--memory`` alone on a
        container whose memory-swap limit is still unlimited — *"Memory limit
        should be smaller than already set memoryswap limit, update the
        memoryswap at the same time"* — which is every container the CLI has
        just created. The failure is invisible because the caller is
        best-effort by design: it logs one line and carries on, so
        `container.resources.memory` would silently cap nothing while `cpus`
        and `pids` (which take no such pairing) worked. The two are set EQUAL, which
        is docker's way of spelling "no swap beyond the cap" — the right
        reading for a cap whose purpose is to bound a runaway agent, since
        swap-backed overshoot is the thrash the limit exists to prevent.
        """
        if self.empty or not container:
            return ()
        argv: list[str] = [docker_bin, "update"]
        if self.memory:
            argv += ["--memory", self.memory, "--memory-swap", self.memory]
        if self.cpus:
            argv += ["--cpus", self.cpus]
        if self.pids:
            argv += ["--pids-limit", str(self.pids)]
        argv.append(container)
        return tuple(argv)
