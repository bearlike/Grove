"""Pydantic config models, on-disk cascade loader, and schema export.

The model is the contract: every layer (defaults → user → project → project-local
→ env → CLI) deep-merges into a dict, then Pydantic validates once at the
single boundary. Unknown fields raise — caught typos beat silent acceptance.

`${repo}` / `${repo_name}` are stored verbatim in saved configs and expanded
only when consumed (inside `WorkspaceManager.create()`), so the same global
config can serve every repo without re-validation per invocation.
"""

from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, ClassVar, Literal
from urllib.parse import urlparse

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic.fields import FieldInfo

from grove.core import paths
from grove.core.errors import ConfigError

_FROZEN = ConfigDict(extra="forbid", validate_default=True, frozen=True)
_MUTABLE = ConfigDict(extra="forbid", validate_default=True)


# ─── nested submodels ───────────────────────────────────────────────────────


class WorktreeConfig(BaseModel):
    """Where worktrees live and how branches are named."""

    model_config = _FROZEN

    root_template: str = "${repo}/.worktrees"
    """Template for the worktree parent dir; supports ${repo}, ${repo_name}, ~."""

    branch_prefix: str = "grove/"
    """Prefix prepended to every auto-created branch."""


# Which AgentAdapter introspects an agent. Module-level alias so the persisted
# WorkspaceState.agent_kind (workspace.py) shares one source of truth with the
# config-side AgentSpec.kind — workspace already imports from config, so this
# direction has no cycle.
AgentKind = Literal["claude_code", "codex", "generic", "mewbo"]


class AgentSpec(BaseModel):
    """One selectable agent in the new-workspace picker."""

    model_config = _FROZEN

    name: str
    """Identifier used as the merge key when cascading agent lists across layers."""

    command: str
    """Shell command sent to the agent tmux window via send-keys."""

    kind: AgentKind = "generic"
    """Which `AgentAdapter` introspects this agent's session for the Activity
    Dashboard. `claude_code` enables transcript-based activity tracking — live
    status, human-turn / reply counts, the session's self-generated title — and
    lets Grove mint a deterministic `--session-id` at launch. `codex` reads the
    same transcript signals from Codex CLI rollout files, but the id is
    server-internal (no launch flag) so its session is adopted via discovery,
    not minted. `mewbo` introspects a remote orchestrator over REST. `generic`
    (the default) launches the command but tracks nothing: a plain shell, or any
    tool with no known transcript format. Mechanism, not policy — declare it per
    agent and it cascades like every other field."""

    env: dict[str, str] = Field(default_factory=dict)
    """Extra env vars *exported* into the agent's tmux window before launch."""

    models: tuple[str, ...] = ()
    """Curated model ids to OFFER for this agent in the create-form picker — a
    display/override seam, never a validated allowlist (any id is still
    forwarded verbatim on create; the provider boundary). Empty (the default)
    falls through to the adapter's live discovery: Codex reads ``codex debug
    models``, Claude Code offers its stable ``sonnet``/``opus``/``haiku``
    aliases, a remote/shell agent offers nothing. Set it to pin, restrict,
    reorder, or add gateway/custom ids — it cascades and merges by field like
    every other AgentSpec knob (mechanism, not policy). The engine
    (``agents.resolve_models``) caps the offered list at ten."""

    env_unset: tuple[str, ...] = ()
    """Env vars *cleared* in the agent's tmux window before ``env`` is applied.

    The hermetic half of the launch env: a tmux pane inherits the
    tmux server's environment, which inherited the daemon's, so an ambient value
    (a profile selector like ``CLAUDE_CONFIG_DIR``) silently leaks daemon → server
    → pane → agent. Listing a var here ``unset``s it at the pane boundary, so the
    agent starts from a known base and ``env`` — or, when ``env`` is silent, the
    tool's own default — decides instead of whatever the daemon happened to carry.
    Unset runs first, so a key present in both ``env_unset`` and ``env`` ends up
    exported. Pure mechanism, not policy: the launcher just clears whatever vars
    the config names — no var name is hard-coded anywhere — and a future container
    launcher applies the same ``env`` / ``env_unset`` set at create time."""

    description: str = ""

    tools_offline: bool = False
    """Launch this agent with network-facing tools disallowed: Claude
    Code drops ``WebFetch``/``WebSearch``, Codex flips its sandbox to
    workspace-write with networking off. Mechanism, not policy — a deployment
    that wants a hermetic/offline agent profile sets this per `AgentSpec`
    rather than Grove hard-coding a tool list; the adapter owns the actual
    flag shape (`AgentAdapter.offline_decoration`), same provider-boundary
    split as `model_decoration`. `generic`/`mewbo` have no local tool gate, so
    it's a no-op for those kinds."""


class InitScriptConfig(BaseModel):
    """Optional setup script run in its own tmux window before the agent starts."""

    model_config = _FROZEN

    EXCLUSIVE_FIELDS: ClassVar[tuple[str, ...]] = ("inline", "path")
    """The script has exactly one source: at most one of these may be set.

    The constraint is declared on the model that owns it, not inside the cascade
    machinery, because two guards read it and must not drift: `ExclusiveGroups`
    resolves the group *across* layers before the merge, and the validator below
    rejects a single layer that sets both.
    """

    enabled: bool = False
    shell: Literal["bash", "sh", "zsh"] = "bash"
    inline: str | None = None
    """Inline shell snippet. Mutually exclusive with `path`."""

    path: str | None = None
    """Repo-relative path to a script file. Mutually exclusive with `inline`."""

    timeout_seconds: int = 300
    fail_fast: bool = True
    """If True, a non-zero exit rolls back the worktree+session+branch."""

    run_on_resume: bool = False

    applies_to: Literal["all", "host", "container"] = "all"
    """Which workspace runtimes this script is for — a filter, not a runtime.

    A host-side setup step is redundant (or actively wrong) when the
    devcontainer's own lifecycle hooks already do that work, and vice versa.
    Deliberately NOT named `runtime`: `workspace.Runtime` is `host|container`
    with no `all` member, and one name for two domains is exactly the confusion
    this codebase warns about. Default `"all"` is the historical behavior, so no
    existing config changes meaning.
    """

    def applies(self, *, is_container: bool) -> bool:
        """Does this script apply to a workspace on this runtime?

        Takes the bare fact, not a `Runtime`: `workspace.py` imports this
        module, so importing the enum back would close a cycle — and the plain
        bool keeps the predicate pure and callable from anywhere. Not-applicable
        is `InitStatus.SKIPPED` at the call sites, the same as `enabled: false`.
        """
        if self.applies_to == "all":
            return True
        return self.applies_to == ("container" if is_container else "host")

    @model_validator(mode="after")
    def _reject_two_script_sources(self) -> InitScriptConfig:
        """Fail load when one layer names both script sources.

        Runs on the MERGED config, where cross-layer collisions have already been
        resolved (`ExclusiveGroups`) — so anything still holding both was authored
        that way in a single layer, a real mistake worth reporting at load time
        instead of at the first `create` in that repo. `tmux.run_init_script`
        keeps its own check for models built without validation.
        """
        provided = [name for name in self.EXCLUSIVE_FIELDS if getattr(self, name) is not None]
        if len(provided) > 1:
            joined = " and ".join(f"'{name}'" for name in provided)
            raise ValueError(
                f"init_script: {joined} are mutually exclusive — set exactly one "
                "(an inline snippet or a path to a script file), not both"
            )
        return self


class TmuxConfig(BaseModel):
    """tmux session/window naming and behavior."""

    model_config = _FROZEN

    session_prefix: str = "grove-"
    init_window_name: str = "init"
    agent_window_name: str = "agent"
    shell_window_name: str = "shell"
    history_limit: int = 50_000

    peek_pane_refresh_seconds: float = 0.25
    """Fast pane-only tick for the peek rail (`tmux capture-pane`).

    Bounded subprocess work; keep low for snappier feel, raise on slow
    machines or when watching a large pane.
    """

    peek_stats_refresh_seconds: float = 3.0
    """Slower full-peek tick (git ahead/behind, diff stats, dirty count).

    These don't change at sub-second granularity; rerunning them on every
    pane tick would burn IO without any user-visible benefit.
    """

    peek_history_lines: int = Field(default=500, ge=1)
    """How many lines of tmux scrollback the pane snapshot captures (`-S -N`).

    The live viewport is only ~40 rows, so without scrollback a long agent
    session previews as just its current screen — earlier output is never
    read. This is the captured/over-the-wire bound, not a viewport: each
    client (TUI rail, dashboard tile, webapp `<pre>`) tails or scrolls within
    it. Generous by default; raise to scroll further back, lower on slow links.
    """

    activity_threshold_seconds: int = Field(default=30, ge=1)
    """Age (seconds) of the last tmux pane_activity before a workspace flips
    Active → Idle. Used by `WorkspaceManager._reconcile_status`. Tighter
    values track real-time work but flicker for agents that pause to think;
    looser values smooth flicker but lag the badge. The original 5s default
    read every thinking/long-tool agent (no pane output for >5s is routine)
    as Idle — and the status blend demotes a WORKING transcript to IDLE on
    that signal, so the flicker surfaced on every dashboard card.
    """

    steer_settle_ms: int = Field(default=200, ge=0)
    """Delay (ms) `tmux.send_text`/`send_keys` wait between typing steered
    text and sending the submitting Enter. The TUI's bracketed paste
    buffers everything landing inside its accumulation window as literal
    text, so an Enter sent immediately after a paste can be coalesced into
    that same window and read as a literal newline rather than a lone
    submitting keypress — only a lone `return` keypress submits. Waiting
    this long first lets the window close before Enter lands. The same
    value also bounds the post-Enter verify-and-retry wait (one settle
    period is enough for the composer to render either the reset prompt or
    the still-pasted text). 0 disables both delays.
    """


class HooksConfig(BaseModel):
    """Grove-managed Claude Code status hooks. On by default.

    When ``enabled``, Grove launches ``claude_code`` agents with
    ``--settings <grove-hooks-settings>`` so a lightweight hook pushes exact
    lifecycle status (``WORKING`` / ``WAITING`` / ``BLOCKED`` / ``IDLE``) into a
    per-session sidecar that the Activity Dashboard prefers over polled status —
    giving precise *blocked-on-a-permission-prompt* that polling can't see, plus
    an immediate daemon refresh over the native http hook instead of
    waiting out the poll tick. On by default now that the sidecar is the
    primary live signal rather than a dormant opt-in sidecar (still a
    mechanism knob, not policy); the user's own ``.claude/settings.json`` is
    never touched, so disabling is just flipping this back to ``false``.
    """

    model_config = _FROZEN

    enabled: bool = True

    daemon_url: str = ""
    """Base URL the hook's native ``http`` handler POSTs each event to.

    Empty (the default) keeps the built-in loopback address, so the rendered
    settings stay byte-identical to a config that never mentions this. It is a
    knob because the address is only correct for an agent sharing the daemon's
    loopback: a runtime launched into its own network namespace — a container —
    reaches the daemon at a different host entirely, and an address a runtime
    cannot resolve is policy that has no business being fixed in code."""


class BriefConfig(BaseModel):
    """The one-paragraph brief a new workspace's agent is handed on its first
    turn, pointing it at Grove's ``working-in-grove`` skill. On by default.

    The brief says where the agent is, that what it reports is published onto
    the workspace's attached tickets, and which skill carries the rules; the
    skill itself carries everything else, so the cost is a few lines once per
    session, once per agent.

    How it arrives depends on what the workspace can carry. A ``claude_code``
    agent on the host is handed it by Grove's status hook, on its first prompt,
    whether or not the workspace was given a task. An agent with no such hook —
    one running in a container — gets it prepended to the workspace's initial
    prompt instead, so it is briefed only when a prompt was given. A remote
    (mewbo) agent is never briefed: it runs on a backend, with no worktree to
    report from.

    This is the *default* for new workspaces. ``grove create --no-brief``
    overrides it per workspace, and the choice is recorded, so flipping this
    later never changes a workspace that already exists.
    """

    model_config = _FROZEN

    enabled: bool = True


AgentShare = Literal["full", "projects", "isolated"]
"""How much of the host agent configuration a containerized agent shares.

Ordered by :data:`SHARE_RANK` — ``isolated`` (tightest) → ``full`` (loosest).
The knob is what makes the container a *blast-radius* boundary rather than a
credential boundary: ``full`` means the agent works in-container exactly as it
does on the host (native sign-in, skills, settings), which is the point of
containerizing an agent that runs with permission prompts off.
"""

SHARE_RANK: dict[AgentShare, int] = {"isolated": 0, "projects": 1, "full": 2}
"""Sharing order, tightest first. THE single definition — the committed-layer
tighten-only rule (:class:`CommittedShareFloor`) and the mount planner
(``core.container_policy``) must agree on what "tighter" means."""

EgressMode = Literal["allowlist", "open", "deny"]
"""Network posture for a containerized agent: derived allowlist (default), no
firewall at all, or nothing but the workspace's own network."""


def _default_agent_plane() -> dict[AgentKind, tuple[str, ...]]:
    """Per-kind agent-plane endpoints, keyed like ``ProxyConfig.upstreams``."""
    return {
        "claude_code": ("api.anthropic.com", "console.anthropic.com", "statsig.anthropic.com"),
        "codex": ("api.openai.com", "auth.openai.com", "chatgpt.com"),
    }


class RangeSource(BaseModel):
    """A provider that publishes its own address ranges as JSON.

    Mechanism, not a GitHub special case: any endpoint returning a JSON object
    whose named keys are arrays of CIDR strings is expressible here, so the next
    provider that publishes ranges costs a config entry rather than a branch.

    ``keys`` is explicit rather than "take everything" because a provider
    publishes ranges Grove has no business allowing — GitHub's payload also
    carries `actions`, `packages` and `codespaces`, which are not what a
    workspace's `git fetch` needs.
    """

    model_config = _FROZEN

    url: str
    """Where the JSON lives. Fetched from the HOST at provision time, where the
    network is unrestricted — never from inside the container the firewall is
    about to constrain."""

    keys: tuple[str, ...] = ()
    """Which top-level keys hold the CIDR arrays. Empty means every key whose
    value is an array of strings."""


def _default_range_sources() -> tuple[RangeSource, ...]:
    """GitHub, because it is where the measured damage is.

    `git` and `web` cover clone/fetch over HTTPS and the redirects that follow;
    `api` covers `gh`. Deliberately not `actions`/`packages`/`codespaces` — a
    workspace does not need GitHub's whole estate to fetch its own repository.
    """
    return (RangeSource(url="https://api.github.com/meta", keys=("git", "web", "api")),)


def _default_package_plane() -> tuple[str, ...]:
    """Package indexes and registries ordinary dev work reaches for."""
    return (
        "registry.npmjs.org",
        "pypi.org",
        "files.pythonhosted.org",
        "github.com",
        "codeload.github.com",
        "objects.githubusercontent.com",
        "raw.githubusercontent.com",
        "proxy.golang.org",
        "index.crates.io",
        "static.crates.io",
        "deb.debian.org",
        "archive.ubuntu.com",
        "registry-1.docker.io",
        "auth.docker.io",
        "ghcr.io",
    )


class ContainerAgentConfig(BaseModel):
    """What the container shares from the host agent configuration.

    Default ``full``: native integration with the host — config, sign-in,
    skills — is a *feature*, delivered through bind mounts, and it is what makes
    a fully-autonomous agent useful rather than a second sign-in chore.
    ``isolated`` restores the credential boundary for an untrusted repository at
    the cost of that sign-in; ``projects`` shares transcripts only.

    **Trust rule (enforced in :class:`CommittedShareFloor`): a committed layer
    may force a tighter value but never raise sharing.** A committed
    ``.grove/config.json`` is untrusted input — a repo that could set
    ``share: full`` would be granting itself the host's credentials.
    """

    model_config = _FROZEN

    share: AgentShare = "full"
    """Sharing level. Read from non-committed layers; a committed layer's value
    applies only when it TIGHTENS what the rest of the cascade resolved."""

    trust: bool = True
    """Whether the seeded agent configuration records the container's workspace
    folder as already trusted, and its committed `.mcp.json` servers as already
    approved.

    On by default because a container workspace is an *unattended* start: the
    agent runs in a directory the tool has never seen, and the tool asks — once,
    interactively — whether that directory can be trusted. Nobody is at the
    terminal to answer, so the workspace simply never begins working. Turning
    this off restores that prompt, which means a containerized agent will not
    start on its own until a human attaches and answers it; the isolation the
    container itself provides is unchanged either way."""


class ContainerTmuxConfig(BaseModel):
    """Whether the agent runs under a tmux INSIDE its container, and whose tmux.

    The multiplexer used to sit on the far side of the namespace boundary from
    the process it multiplexes: the agent ran as ``devcontainer exec … -- claude``
    typed into a HOST pane, so the PTY died with the host client and nothing
    could ever attach back to the surviving in-container process. Running the
    agent under a container-side tmux makes the host pane a *viewport* and the
    in-container tmux the owner of the agent's lifetime.

    Two policy questions live here rather than in code, because both are
    genuinely the operator's:

    * ``prefer_image`` — an image that ships its own tmux is almost always the
      better answer (it matches the distro's terminfo and the user's own
      expectations), but an operator standardizing on one tmux across a fleet
      may want Grove's bundle everywhere.
    * ``payload`` — where the static binary comes from. Empty means Grove's own
      built-and-cached bundle; a path lets an operator supply a vetted build
      (an air-gapped host, a signed artifact, a different tmux version).

    Everything degrades honestly: with no image tmux and no payload, the launch
    composes a plain ``exec`` and the workspace loses persistence, never the
    workspace itself.
    """

    model_config = _FROZEN

    enabled: bool = True
    """Run the agent under a container-side tmux when one is reachable.

    ``False`` restores the bare ``devcontainer exec -- <agent>`` launch: the
    agent still runs, but its terminal dies with the host client and there is
    no reattach path."""

    prefer_image: bool = True
    """Use the image's OWN tmux when the container has one, else Grove's bundle.

    Presence is detected once per provision through the same
    ``devcontainer exec`` road the agent itself takes, so the probe sees the
    remote user's PATH rather than the image's default user's."""

    payload: str = ""
    """Host directory holding ``bin/<arch>/tmux`` plus a shared ``terminfo/``.

    Empty (default) uses Grove's own cache, built on demand. A value is an
    operator-supplied bundle, mounted read-only exactly like the built one and
    never verified beyond "the file is there" — it is the operator's build, and
    Grove never writes into it.

    The per-architecture split is not optional even for a hand-built bundle: the
    binary executes INSIDE the container, so an amd64 build cannot run in an
    arm64 container, and one Grove host routinely serves both (an Apple Silicon
    machine running an amd64 image, or the reverse under emulation). ``<arch>``
    is docker's platform name — ``amd64`` or ``arm64``. Compiled terminfo is
    capability data rather than machine code, so one ``terminfo/`` serves every
    architecture."""

    session: str = "agent"
    """The in-container tmux session name ``new-session -A`` keys on.

    This is a REATTACH IDENTITY, not a display name, which is why it is its own
    field rather than a reuse of ``tmux.agent_window_name``: renaming the host
    window is cosmetic, while renaming this orphans a live agent's session
    behind a newly-created empty one."""

    shell_session: str = "shell"
    """The in-container tmux session an interactive shell attaches to.

    Its own field for the same reason :attr:`session` is one — a reattach
    identity, not a display name — and separate FROM it because the shell and
    the agent are two sessions on one in-container server: sharing a name would
    drop a user into the agent's own pane. ``grove shell`` and the attach
    layout's shell window both key on this, which is what makes them the same
    persistent shell rather than two."""

    term_fallback: str = "xterm-256color"
    """``TERM`` to retry an attach with when tmux refuses the client's own.

    Empty DISABLES the retry — one field rather than a flag plus a value,
    because a bool and a string can express "enabled with no TERM", which means
    nothing.

    Needed structurally, not defensively: an attach with a ``TERM`` no terminfo
    database has is a hard refusal (``missing or unsuitable terminal``), and
    several real terminals (``wezterm``, ``contour``, ``wayst`` depending on the
    distro) ship entries no ncurses release packages, so no bundle can ever
    cover everyone. Verified through a real pty with a real curses app that a
    substituted ``TERM`` renders, resizes on SIGWINCH, detaches and reattaches
    correctly — tmux and ncurses cannot tell a substituted value from a native
    one. Grove SAYS so on stderr when it substitutes; a silent one would leave a
    user debugging their own terminal's colours."""


class ContainerDecorConfig(BaseModel):
    """Whether the container gets Grove's own tmux chrome and statusline.

    Grove bind-mounts a small read-only asset bundle into every containerized
    workspace at ``/grove/decor``: a tmux config and a Claude Code statusline
    script. Both exist because a person attached to a container workspace
    otherwise has no way to tell they are in one — the user's own statusline
    script is never shared into the container, and the in-container tmux has
    no config at all — and because a status bar inside a container must
    report the CONTAINER's own cgroup CPU/memory limits, not
    ``/proc/loadavg``, which is not namespaced and answers for the HOST.
    """

    model_config = _FROZEN

    enabled: bool = True
    """Mount and compose the bundle at all. ``False`` means no mount and
    nothing composed — the container gets bare default tmux chrome and no
    Grove statusline."""

    statusline: bool = True
    """Compose the statusline into the agent's settings. Independent of
    ``tmux_conf`` so an operator can take one without the other."""

    tmux_conf: bool = True
    """Pass Grove's tmux config to the in-container tmux."""

    payload: str = ""
    """Host directory holding the decor assets. Empty (default) uses Grove's
    own bundled copy shipped in the wheel. A value is an operator-supplied
    bundle, mounted read-only exactly like the built-in one and never written
    to — the escape hatch that keeps this mechanism rather than policy: Grove
    ships one sane look, an operator can replace it wholesale."""


class EgressConfig(BaseModel):
    """Where a containerized agent may reach on the network.

    With credentials shared and permission prompts off, egress is the control
    that carries the weight: it bounds where a token can be *sent*. The list is
    **derived, not restated** — the planner (``core.container_policy``) unions
    the agent plane for the workspace's kind, the package plane, the repo's own
    git remotes and the Grove plane, so normal dev work needs zero config.
    ``allow`` is purely additive on top.

    Documented ceilings, carried from the reference implementation this follows:
    UDP/53 stays open (DNS tunneling is not defended against) and name-based
    filtering loses to domain fronting. ``open`` is the supported, un-nagged
    no-firewall path — never a warning, never a refusal.
    """

    model_config = _FROZEN

    mode: EgressMode = "allowlist"
    """``allowlist`` (default) applies the derived firewall; ``open`` applies
    nothing; ``deny`` permits only loopback and the workspace's own network.
    Anything but ``open`` **fails closed**: if the firewall verifiably failed to
    apply, the container start fails. That is config integrity, not a permission
    gate — the agent's own flags are never inspected."""

    allow: tuple[str, ...] = ()
    """Extra destinations (hostnames or CIDRs) added to the derived set."""

    agent_plane: dict[AgentKind, tuple[str, ...]] = Field(default_factory=_default_agent_plane)
    """Per-kind agent endpoints. Provider facts with a cascade override, same
    shape as ``ProxyConfig.upstreams`` — a gateway deployment repoints them
    without touching code."""

    package_plane: tuple[str, ...] = Field(default_factory=_default_package_plane)
    """Package indexes/registries reachable regardless of agent kind."""

    grove_plane: tuple[str, ...] = ("host.docker.internal",)
    """How the container reaches Grove itself (hook ingest, the daemon). The
    connected-container default is what keeps status precise."""

    range_sources: tuple[RangeSource, ...] = Field(default_factory=_default_range_sources)
    """Providers that PUBLISH their address ranges, fetched at provision time.
    The answer to a hostname whose DNS answer outlives its usefulness:
    `github.com` presents one A record with a 42-second TTL drawn from a large
    pool, so an allowlist pinned at container start is authoritative for under
    a minute and a coin flip for the rest of a multi-hour session — measured
    rotating from `140.82.116.3` to `20.29.134.23` inside 80 seconds.

    Additive to the hostname planes above, never a replacement: a source that
    fails to fetch degrades to exactly today's behaviour rather than to nothing.
    Config rather than code so the next provider that publishes ranges needs an
    entry, not a branch."""


class ResourcesConfig(BaseModel):
    """Per-container caps, applied at launch.

    One knob on the policy cascade, deliberately NOT a slice hierarchy: the
    agent container gets them via ``docker update`` after ``up`` (the
    devcontainer CLI owns creation, so post-hoc update is the one dependable
    application point) and compose stack services via ``deploy.resources`` in
    the generated override. Empty/zero means "do not cap" — every field is
    independently optional, so a memory-only policy emits a memory-only update.

    ``hostRequirements`` in a ``devcontainer.json`` is a different question — a
    *declaration* Grove checks and refuses on, never a cap it applies.
    """

    model_config = _FROZEN

    memory: str = ""
    """``docker update --memory`` value (``8g``, ``512m``). Empty = uncapped."""

    cpus: str = ""
    """``docker update --cpus`` value (``4``, ``1.5``). Empty = uncapped.
    A string, not a float: it is passed through verbatim and compose's
    ``deploy.resources.limits.cpus`` is a string there too."""

    pids: int = 0
    """``docker update --pids-limit`` value. ``0`` = uncapped (a fork-bomb cap
    is cheap insurance for an autonomous agent, but it is policy, not default)."""


class EnvSourceConfig(BaseModel):
    """A config section that draws values from a dotenv file or a command.

    The two knobs below are the ONE mechanism Grove has for "give this feature an
    environment it could not read from my own process". They exist because the
    process that consumes them is long-lived: a daemon reads ``os.environ`` once,
    at exec, and nothing a workspace's init script (or a secret manager, or a
    ``docker login``) does afterwards can reach back into it. A file or a command
    is re-read at the moment of use, so a credential that only exists AFTER the
    daemon started is still found.

    Subclassed rather than duplicated — ``container`` and ``tickets`` are two
    consumers of one seam, and every guard around it (``ExclusiveGroups``,
    ``CommittedEnvSource``, ``core.env_source.EnvSource``) reads this one
    definition. A third consumer costs a ``SECTION`` and a base class, never a
    second parser.

    **Never a field holding the secret itself.** A value belongs in the file or
    the command's stdout; config holds only names and paths, which is what keeps
    a committed ``.grove/config.json`` publishable.
    """

    model_config = _FROZEN

    SECTION: ClassVar[str]
    """The config key this section is mounted at (``container``, ``tickets``).

    Declared here so the cross-layer guards and the resolver can name the section
    in their own messages without a caller passing it in — the same reasoning that
    puts ``EXCLUSIVE_FIELDS`` on the model that owns the fields.
    """

    EXCLUSIVE_FIELDS: ClassVar[tuple[str, ...]] = ("env_file", "env_command")
    """This section's environment has exactly one source: at most one of these.

    Declared on the model that owns the fields, not inside the cascade machinery,
    for the same reason as `InitScriptConfig.EXCLUSIVE_FIELDS`: two guards read it
    and must not drift — `ExclusiveGroups` resolves the group *across* layers
    before the merge, and the validator below rejects a single layer setting both.
    """

    env_file: str | None = None
    """Dotenv file whose variables this section draws on.

    Repo-relative or absolute, ``~`` expanded. Mutually exclusive with
    ``env_command``. A configured-but-missing file is a HARD ERROR at the point of
    use — a silently-empty environment is the exact failure this feature exists to
    fix, and a workspace that boots without its credentials burns a whole session
    discovering that. The error is raised at the consumption boundary
    (``core.env_source``), not here, so the cascade can still persist a config for
    a repo whose file is not present yet.

    **A committed layer may only name a path INSIDE the repo** — see
    :class:`CommittedEnvSource`.
    """

    env_command: str | None = None
    """Host command whose stdout is parsed as dotenv.

    Exists so secrets never have to be materialized to disk: any command that
    prints dotenv to stdout works, and Grove knows nothing about any particular
    secret manager — naming one here would be policy in code. Mutually exclusive
    with ``env_file``. Re-run at every consumption (deliberately not cached — a
    cache in a daemon holds secrets for days and serves rotated ones), so it must
    be idempotent and cheap.

    **Never honored from a committed layer** — running it would be remote code
    execution on clone. See :class:`CommittedEnvSource`.
    """

    @staticmethod
    def env_file_is_contained(raw: str) -> bool:
        """Does this ``env_file`` value stay inside the repository?

        The predicate lives on the model that owns the field so the committed-layer
        pre-pass and any future guard read ONE definition — the same reasoning that
        puts `EXCLUSIVE_FIELDS` here. Purely string-level on purpose: the cascade
        resolves long before a repo root or a filesystem is in hand, and a check
        that needed either could not run where it is needed.

        Rejects ``~`` (a home path is not repo-relative however it normalizes), any
        absolute form including the Windows drive/UNC shapes `PurePosixPath` does
        not consider absolute, and any path whose normalized form walks above the
        root.
        """
        candidate = raw.replace("\\", "/")
        if candidate.startswith("~"):
            return False
        if PurePosixPath(candidate).is_absolute():
            return False
        # "C:/secrets.env" is relative to PurePosixPath but absolute where it runs.
        if len(candidate) > 1 and candidate[1] == ":" and candidate[0].isalpha():
            return False
        normalized = os.path.normpath(candidate).replace("\\", "/")
        if normalized.startswith("/"):
            return False
        # Segment compare, not a prefix test: "..secrets.env" is an ordinary name.
        return normalized.split("/", 1)[0] != ".."

    @model_validator(mode="after")
    def _reject_two_env_sources(self) -> EnvSourceConfig:
        """Fail load when one layer names both environment sources.

        Runs on the MERGED config, where cross-layer collisions were already
        resolved (`ExclusiveGroups`), so anything still holding both was authored
        that way in a single layer — a real mistake, and cheaper to report at load
        than at the first use in that repo.
        """
        provided = [name for name in self.EXCLUSIVE_FIELDS if getattr(self, name) is not None]
        if len(provided) > 1:
            joined = " and ".join(f"'{name}'" for name in provided)
            raise ValueError(
                f"{self.SECTION}: {joined} are mutually exclusive — set exactly one "
                "(a dotenv file or a command printing dotenv to stdout), not both"
            )
        return self


class ContainerConfig(EnvSourceConfig):
    """Run the agent inside a container instead of directly on the host.

    **Default-ON, deliberately reversing Grove's earlier "containers are
    strictly opt-in" default.** The container is what lets an agent run fully
    autonomously (relaxed permissions) with its blast radius bounded to the
    workspace, so it is the default new workspaces get; ``--runtime host`` is
    the per-create escape hatch and the persisted ``WorkspaceState.runtime``
    means an existing workspace never moves. Turning it back off is a policy
    choice about isolation, not a bug fix.

    Every value cascades like the rest of the config; a future Podman driver
    reads the SAME submodel behind the same protocol — ``docker_bin`` names the
    CLI, it is not the driver-swap seam.

    The inherited ``env_file`` / ``env_command`` knobs (:class:`EnvSourceConfig`)
    are resolved TWICE per workspace start: once to provision (the values reach
    the project's lifecycle hooks via ``--secrets-file``) and once to launch (they
    reach the agent via the launch env). A missing file or a failing command is
    fatal at create, which is what keeps an agent from booting without its
    credentials.
    """

    model_config = _FROZEN

    SECTION: ClassVar[str] = "container"

    enabled: bool = True
    """The cascade DEFAULT for a new workspace's runtime: ``True`` →
    ``Runtime.CONTAINER``, ``False`` → ``Runtime.HOST``. Only consulted at
    create; ``resume``/``respawn`` read the persisted ``runtime`` so flipping
    this never migrates an existing workspace."""

    up_timeout_seconds: float = 900.0
    """Wall-clock bound on one ``devcontainer up`` (and ``build``). A cold build
    that pulls a base image and installs Features is legitimately minutes long,
    so this is generous — but it MUST stay inside every client's own deadline
    (``GroveClient._LIFECYCLE_TIMEOUT_S``), or the caller gives up on a create
    that then succeeds and leaves an orphaned container with no record."""

    default_config: str = ""
    """Path to the ``devcontainer.json`` Grove passes via ``--config`` for a repo
    that has no ``.devcontainer/`` of its own. Empty (default) uses the
    self-contained config packaged in the wheel. The file must stay
    self-contained — image + features only, no ``build.dockerfile``, no
    ``dockerComposeFile``, no local-path features — because a config outside the
    worktree cannot resolve config-directory-relative paths."""

    docker_bin: str = "docker"
    """The container CLI binary/path (``docker``, or an absolute path / drop-in
    shim). NOT the Podman-driver seam — a different runtime is a different
    ``ContainerDriver`` class, this only points at a docker-compatible CLI."""

    shell: tuple[str, ...] = ("bash", "sh")
    """Interactive shells ``grove shell`` and the attach shell window try, in order.

    A CHAIN rather than one name, because the shell is a property of somebody
    else's image and Grove cannot know it: ``bash`` is absent from Alpine-based
    and distroless-ish images that nonetheless ship ``sh``, while a user whose
    image ships ``zsh`` or ``fish`` wants that one. The first entry that exists
    inside the container is exec'd; if none does, the pane says so and exits
    non-zero rather than silently substituting a shell nobody asked for — a
    baked-in final fallback would be exactly the policy-in-code this config
    exists to avoid.

    Resolution happens INSIDE the container (``command -v``), not here: presence
    is a fact about the image, and probing it from the host would be a second
    ``exec`` answering a question the shell itself answers for free."""

    agent_config: ContainerAgentConfig = Field(default_factory=ContainerAgentConfig)
    """How much of the host agent configuration the container shares."""

    egress: EgressConfig = Field(default_factory=EgressConfig)
    """Where the containerized agent may reach on the network."""

    tmux: ContainerTmuxConfig = Field(default_factory=ContainerTmuxConfig)
    """Whether the agent runs under a tmux inside the container, and whose."""

    resources: ResourcesConfig = Field(default_factory=ResourcesConfig)
    """Per-container CPU / memory / pid caps."""

    decor: ContainerDecorConfig = Field(default_factory=ContainerDecorConfig)
    """Grove's tmux config + statusline bundle, mounted into every container."""


class ChannelsConfig(BaseModel):
    """Grove-managed Claude Code *channel* delivery (research preview).

    A channel is Claude Code's native seam for pushing a message a **running,
    interactive** session acts on (and relaying permission decisions), unlike a
    hook (status push, one way) or steering (raw pane keystrokes). When
    ``enabled``, Grove launches ``claude_code`` agents with ``--channels
    <grove-channel-settings>`` so the agent connects to the Grove channel MCP
    server; the daemon then POSTs queued messages to that server's loopback
    receiver and the agent receives them as ``notifications/claude/channel``.

    Off by default on purpose: channels are an auth-gated Claude Code research
    preview, so this stays a deliberately-flipped mechanism knob (not policy).
    Disabling is just flipping ``enabled`` back — the launch flag disappears and
    the whole path degrades to a no-op, exactly like the hook ``--settings``.
    """

    model_config = _FROZEN

    enabled: bool = False

    allowed_senders: list[str] = Field(default_factory=list)
    """Sender allowlist for inbound channel deliveries. Each entry is a sender
    identifier (a Grove client/device label) permitted to POST a message into a
    running session's channel. **Empty means allow all** — enabling the feature
    is the deliberate opt-in, so a bare enable is permissive; populate this to
    RESTRICT which senders may drive a running agent. Mechanism, not policy: the
    server matches an inbound message's declared sender against this list."""


class PermissionConfig(BaseModel):
    """Grove-hosted Claude Code ``--permission-prompt-tool`` answering.

    A *permission prompt* is the "allow this tool call?" gate a headless / paneless
    session hits with no interactive terminal to answer it. When ``enabled``, Grove
    launches ``claude_code`` agents with a Grove-owned MCP server registered
    (``--mcp-config <grove-permission-mcp>``) plus
    ``--permission-prompt-tool mcp__grove_permission__permission_prompt`` — so
    Claude Code calls that Grove tool instead of blocking on a TTY, and the tool
    answers with allow/deny JSON. It is the native replacement for typing a
    permission answer into the tmux pane (which only works with a human attached).

    Off by default on purpose (mechanism, not policy), and **fail-closed**:
    ``default = "deny"`` means an un-relayed prompt is denied, never silently
    allowed — flipping ``enabled`` on can't widen what an unattended agent may do
    without an explicit ``default: "allow"``. Disabling is just flipping
    ``enabled`` back — the launch flags disappear and the whole path is a no-op,
    exactly like the hook ``--settings`` / channel ``--channels`` appends.
    """

    model_config = _FROZEN

    enabled: bool = False

    default: Literal["allow", "deny"] = "deny"
    """The decision the prompt tool returns while no interactive human/daemon
    relay is wired. ``deny`` is fail-closed (the safe default for an unattended
    agent); ``allow`` is the deliberate permissive opt-in for a trusted sandbox
    (a container workspace whose blast radius is bounded). Mechanism, not policy:
    the tool answers whatever this names, verbatim."""


class AuthConfig(BaseModel):
    """Daemon HTTP authentication knobs.

    The handshake-based pairing flow gates every HTTP entry point on a valid
    bearer token (no loopback bypass; see CLAUDE.md). ``enabled = false`` is
    a test-only escape hatch; production daemons leave it ``true``.
    """

    model_config = _FROZEN

    enabled: bool = True
    """Master switch. ``False`` disables the dep entirely — only used by
    in-process tests that exercise paths unrelated to auth. Production
    must leave this ``True``."""

    session_ttl_seconds: int = Field(default=30 * 24 * 3600, ge=60)
    """Sliding session TTL. Each ``validate()`` extends ``expires_at`` by this
    many seconds, so a daily user never re-pairs; idle for the full window
    means the session ages out and the device must pair again."""

    pairing_ttl_seconds: int = Field(default=300, ge=30)
    """How long a pairing code is valid for approval after creation."""

    pair_init_per_minute: int = Field(default=5, ge=1)
    """Per-source rate limit on ``POST /auth/pair``. Bounds brute-force."""

    pair_poll_per_minute: int = Field(default=60, ge=1)
    """Per-source rate limit on ``GET /auth/pair/{id}``. Generous; the
    browser polls every 2 s during the approval wait."""


class MewboConfig(BaseModel):
    """Connection settings for the Mewbo orchestrator (``kind: "mewbo"`` agents).

    The ``mewbo`` adapter reads these to reach the Mewbo API: a workspace of
    that kind mints its session on the orchestrator rather than in a local
    process, so the connection details are config rather than discovery.
    """

    model_config = _FROZEN

    base_url: str = "http://127.0.0.1:5125"
    """Base URL of the Mewbo REST API."""

    api_key_env: str = "MEWBO_API_KEY"
    """NAME of the environment variable holding the API key — never the key
    itself. Committed project config must stay secret-free (the repo and its
    config examples are published); the key lives only in the consuming
    process's environment."""

    timeout_seconds: float = 10.0
    """Per-request HTTP timeout for Mewbo API calls."""


class GiteaTicketConfig(BaseModel):
    """Gitea Issues provider settings (``provider: "gitea"``).

    Secret-free like every config layer: ``token_env`` is the NAME of the
    environment variable holding the API token, never the token itself, so a
    committed ``.grove/config.json`` stays publishable. ``base_url`` is the
    instance root (the provider appends ``/api/v1``), and it also reads from
    ``GROVE_GITEA_BASE_URL``: ``token_env`` already lets an operator instrument
    the token, so leaving the endpoint config-only would mean a deployment could
    move the credential and not the server it authenticates against — which is
    how a token ends up pointed at the wrong host. ``branch_prefix`` is an
    optional extra keyword prepended when formatting a branch (e.g. ``"gtea-"``
    → ``gtea-123-slug``); empty means the bare numeric form ``123-slug``. The
    parser recognizes the bare numeric leading segment, the built-in keywords,
    and this configured prefix — mechanism, not policy.
    """

    model_config = _FROZEN

    enabled: bool = False
    base_url: str = Field(
        default="https://gitea.com", json_schema_extra={"x-env-var": "GROVE_GITEA_BASE_URL"}
    )
    owner: str | None = None
    repo: str | None = None
    token_env: str = "GROVE_GITEA_TOKEN"
    branch_prefix: str = ""


class GitHubTicketConfig(BaseModel):
    """GitHub Issues provider settings (``provider: "github"``).

    ``base_url`` defaults to the public REST API; point it at a GitHub
    Enterprise ``/api/v3`` root to use Enterprise. Same secret-free
    ``token_env`` + optional ``branch_prefix`` contract as the Gitea provider.
    """

    model_config = _FROZEN

    enabled: bool = False
    base_url: str = "https://api.github.com"
    owner: str | None = None
    repo: str | None = None
    token_env: str = "GROVE_GITHUB_TOKEN"
    branch_prefix: str = ""


class LinearTicketConfig(BaseModel):
    """Linear provider settings (``provider: "linear"``).

    Linear keys are alphanumeric (``ENG-123``), so there is no numeric
    ``branch_prefix`` — the team key IS the discriminator. ``team_key`` scopes
    both branch parsing (only ``{team_key}-N`` keys are claimed) and the
    ``get_ticket`` lookup; leave it unset to match any uppercase key on parse.
    """

    model_config = _FROZEN

    enabled: bool = False
    base_url: str = "https://api.linear.app/graphql"
    team_key: str | None = None
    token_env: str = "GROVE_LINEAR_TOKEN"


class TicketsConfig(EnvSourceConfig):
    """External ticket-tracker integration, one submodel per MVP provider.

    Each provider is independently ``enabled`` and configured. All three stay
    off by default (mechanism, not policy): a repo opts in by enabling the
    tracker its branches reference. Credentials never live here — only the NAME
    of the env var holding each token, and optionally where to READ that env var
    from (the inherited ``env_file`` / ``env_command``).

    **Resolution order for one provider's token, evaluated at the moment of use
    and never at construction:**

    1. ``tickets.env_command``'s stdout, or ``tickets.env_file``'s contents,
       parsed as dotenv and looked up by that provider's ``token_env`` name.
       The source is re-read on every resolution, so a credential an init script
       (or a secret manager, or a login) produces AFTER the daemon started is
       picked up without a restart.
    2. the consuming process's own environment (``os.environ``), same name.
    3. nothing — the provider reads ``configured: false`` and every network call
       raises rather than sending an unauthenticated request.

    One source serves all three providers because it is keyed by env-var NAME:
    a repo whose ``.grove/config.local.json`` sets
    ``"env_command": "my-secrets export grove"`` gets Gitea's, GitHub's and
    Linear's tokens from one resolution. Per-project configuration is the plain
    cascade (user → committed ``.grove/config.json`` → machine-local
    ``.grove/config.local.json``), which this section has always had.
    """

    model_config = _FROZEN

    SECTION: ClassVar[str] = "tickets"

    gitea: GiteaTicketConfig = Field(default_factory=GiteaTicketConfig)
    github: GitHubTicketConfig = Field(default_factory=GitHubTicketConfig)
    linear: LinearTicketConfig = Field(default_factory=LinearTicketConfig)


# The built-in initial prompt an issue-ops-created workspace boots on. Placeholders
# (``{title} {body} {number} {url} {command_text}``) are filled at the engine
# boundary via ``str.format_map`` with a missing-key-tolerant map, so a
# user-overridden template that references an unknown name renders it literally
# rather than crashing the router. Mechanism, not policy: every deployment can
# override the whole string via ``issueops.prompt_template`` and it cascades like
# any other config value — this is only the sensible default.
_DEFAULT_ISSUEOPS_PROMPT = """\
You are handling tracker issue #{number}: "{title}".

Issue description:
{body}

Thread so far:
{comments}

How you were engaged:
{command_text}

Your mandate:

- Work autonomously through to an OPEN pull request. Read the issue, map the
  affected components (read the nearest owning CLAUDE.md before editing),
  implement the change, run the project's gates, open the PR, and reply on the
  ticket saying what changed and where the PR is.
- Where the ticket is underspecified, REPLY ON THE TICKET asking for exactly
  what is missing, and say what you will assume if nobody answers. Do not stall
  silently, and do not quietly guess at a requirement you could have asked
  about.
- Satisfy the stated goals faithfully and safely — the goals as written, not
  the larger project you would rather do.
- Stop and ask rather than take a risky or irreversible action: destroying
  data, force-pushing a shared branch, or touching anything in production.

Issue link: {url}
"""


class IssueOpsConfig(BaseModel):
    """Turn issue-comment mentions into workspace actions, and mirror progress back.

    One submodel, two faces. INBOUND: a commenter mentions the ``trigger``
    token as the first word of an issue comment; the forwarder (a stateless CI
    action) POSTs the event to the daemon, and the engine parses the grammar,
    enforces the permission policy, and routes to a workspace verb. OUTBOUND:
    when ``enabled``, the daemon runs a status publisher that mirrors each
    workspace's progress onto its ticket as one live sticky comment. Every knob
    here is mechanism, not policy — the trigger word, who may drive it, the boot
    prompt, and the mirror's cadence are all data the deployment owns, never baked
    into the engine.

    ``enabled`` gates ONLY the outbound status mirror. The inbound command routing
    has no on/off flag of its own — its real opt-in is installing the CI workflow
    AND enabling the matching ticket provider (``tickets.<provider>``) with the
    repo's ``owner``/``repo`` (with neither, no event ever reaches the engine and
    no repo resolves for one that does). So a deployment can route commands without
    the status mirror, mirror without routing, or run both.
    """

    model_config = _FROZEN

    enabled: bool = False
    """Master switch for the OUTBOUND live status comment — the sticky
    per-workspace comment the publisher mirrors onto its ticket. Off by default;
    the daemon builds and binds the publisher only when this is ``True``.
    Independent of inbound command routing, which opts in via the CI workflow plus
    an enabled ticket provider (see the class docstring)."""

    trigger: str = "@grove"
    """The mention token that must OPEN a comment (word-boundary, first token) for
    the engine to act. A mention-style ``@``-prefixed token by default — a bare
    ``/`` prefix collides with the forge's own markdown slash commands. Compared
    case-insensitively; everything after it is the command (a fixed verb or free
    prompt text)."""

    allowed_actors: list[str] = Field(default_factory=list)
    """The opt-in WIDENING knob on the permission policy. By default a command is
    honored only when the forwarder asserts the commenter has repo write access;
    listing a login here lets that user drive issue-ops REGARDLESS of their repo
    permission (a trusted bot account, an external collaborator). Empty (default)
    keeps the strict write-access-only policy. Matched case-insensitively. Never a
    NARROWING knob — write access always suffices; this only adds to it."""

    agent: str = "claude"
    """Which configured agent an issue-ops-created workspace spawns. Must name an
    entry in ``agents``; ``create`` raises (and the engine replies) if it doesn't.
    Defaults to the built-in ``claude`` agent — override per deployment to route
    issue work to a different tool. Mechanism, not policy."""

    prompt_template: str = _DEFAULT_ISSUEOPS_PROMPT
    """The initial prompt a newly-created workspace boots on, with ``{title}``,
    ``{body}``, ``{number}``, ``{url}``, ``{comments}`` and ``{command_text}``
    placeholders filled from the ticket. ``{comments}`` renders the whole comment
    thread in order, so the agent starts with the discussion rather than spending
    turns fetching it. One template serves both ways a workspace is started from a
    ticket — an ``@grove`` comment and an assignee pickup — and ``{command_text}``
    says which, so a deployment tunes the agent's marching orders in one place.
    Cascades like all config; the built-in default states the autonomous
    issue→PR mandate."""

    update_window_seconds: float = Field(default=5.0, ge=0)
    """Coalescing window for the OUTBOUND status comment — at most one
    comment PATCH per workspace per window. Forges apply secondary rate limits to
    same-comment edit storms, so the publisher folds every render-relevant change
    into per-workspace state and flushes the merged result once the window
    elapses. ``0`` flushes every change (no coalescing)."""

    deep_link_base_url: str = ""
    """The webapp base (e.g. ``https://grove.example.com``); a status comment
    deep-links to ``{base}/w/{id}``. Reuses the ``notifications`` deep-link
    convention verbatim. Empty (default) omits the link."""

    assign_bot: bool = False
    """Mark what Grove is working ON THE TRACKER: assign the ticket provider's own
    account — whoever the configured token authenticates as — to every ticket a
    live workspace holds, so every Grove-managed issue is findable with the
    tracker's own assignee filter by people who never open Grove. Off by default:
    it writes to somebody else's tracker. Assigning needs repo write, which
    commenting does not; where the token cannot assign, the refusal is logged
    naming the ticket and never fails the workspace. Grove never unassigns on
    completion — the assignment IS the record of who did the work."""

    pickup_enabled: bool = False
    """The assignee AS the inbound work queue: the daemon polls for open issues
    assigned to the provider's own account that no live workspace holds and that
    Grove has never been handed before, and starts a workspace for each. A human
    assigns the bot; a workspace appears. Off by default — this spawns real
    agents. Needs no CI runner, which is what makes inbound automation reachable
    on a deployment that cannot host one."""

    pickup_interval_seconds: float = Field(default=60.0, ge=5)
    """How often the pickup poll asks each configured tracker for its assigned
    issues. A standing load on someone else's API, so it is a knob rather than a
    constant; a provider that errors or rate-limits is backed off for
    ``pickup_backoff_seconds`` regardless of this cadence."""

    pickup_max_active: int = Field(default=3, ge=1)
    """How many pickup-started workspaces may be working at once, across every
    repo on the host. Assigning the bot to forty issues must not spawn forty
    agents: each tick counts the assigned tickets that already have a live
    workspace and starts at most enough to reach this ceiling. The rest are
    DEFERRED to the next tick and named in the log — never silently dropped."""

    pickup_backoff_seconds: float = Field(default=300.0, ge=0)
    """How long one tracker is skipped after it fails or rate-limits a poll.
    Applies to the whole provider, not one ticket, because a 403 or a 429 is a
    statement about the credential or the budget rather than about the issue that
    happened to be asked for. Backoff does not compound — the next successful
    poll clears it."""


# Which agent-state edges may fire a push. String values mirror
# ``AgentActivityState`` (waiting/blocked/error/idle) — kept a Literal here, not
# the imported enum, so ``config.py`` never imports ``grove.core.agents`` (which
# would cycle: agents → registry → adapters → config). The notifications
# subpackage coerces these strings back to the enum at its construction edge.
NotifyTransition = Literal["waiting", "blocked", "error", "idle"]
_DEFAULT_NOTIFY_ON: list[NotifyTransition] = ["waiting", "blocked", "error"]

# Workspace lifecycle events that may fire a push. Mirrors ``WorkspaceEvent.kind``
# (manager.py's ``EventKindStr``) for the subset the notifier phrases — same
# no-import discipline as ``NotifyTransition``. The default set is the
# *unexpected* half: a workspace that broke or was interrupted. The routine verbs
# (created/killed/paused/…) are available but off — a user-initiated pause needs
# no push back to the user who initiated it.
NotifyLifecycle = Literal[
    "error",
    "orphaned_detected",
    "offline_detected",
    "created",
    "killed",
    "paused",
    "resumed",
    "respawned",
]
_DEFAULT_NOTIFY_LIFECYCLE: list[NotifyLifecycle] = [
    "error",
    "offline_detected",
    "orphaned_detected",
]

# The channel-agnostic urgency a notification carries, mirroring
# ``notifications.channel.NotificationSeverity`` (config cannot import the
# notifications package — that package imports *this* one). Each channel maps
# these onto its own native scale below, in config: the mapping is policy, and
# policy does not belong in a branch.
NotifySeverity = Literal["low", "normal", "high", "urgent"]

# Gotify's dial is 0-10 and its Android client bins it: >=8 heads-up + sound,
# 4-7 vibrate, 1-3 silent, <=0 minimized. ntfy's is 1-5 (3 = default). The
# defaults below are chosen against those real thresholds so a pending question
# actually buzzes the phone and a routine pause never does.
_DEFAULT_GOTIFY_PRIORITIES: dict[NotifySeverity, int] = {
    "low": 2,
    "normal": 5,
    "high": 8,
    "urgent": 9,
}
_DEFAULT_NTFY_PRIORITIES: dict[NotifySeverity, int] = {
    "low": 2,
    "normal": 3,
    "high": 4,
    "urgent": 5,
}


class GotifyChannelConfig(BaseModel):
    """Gotify push channel.

    ``server_url`` is the Gotify base (e.g. ``https://gotify.example.com``), and
    it also reads from ``GROVE_GOTIFY_API_URL`` so a deployment can point every
    repo at its own server without editing a file. ``token_env`` is the NAME of
    the env var holding the *application* token (Gotify's ``Axxx…``, the
    send-only kind), never the token itself — committed config stays secret-free,
    exactly like ``mewbo.api_key_env``.
    """

    model_config = _FROZEN

    enabled: bool = False
    server_url: str = Field(default="", json_schema_extra={"x-env-var": "GROVE_GOTIFY_API_URL"})
    token_env: str = "GROVE_GOTIFY_TOKEN"
    priority: int = Field(default=5, ge=0, le=10)
    """Fallback priority for a severity with no entry in ``priorities``."""

    priorities: dict[NotifySeverity, Annotated[int, Field(ge=0, le=10)]] = Field(
        default_factory=lambda: dict(_DEFAULT_GOTIFY_PRIORITIES)
    )
    """Severity → Gotify priority, bounded like ``priority`` itself. The defaults
    hit Gotify's own Android thresholds: ``<=0`` min, ``1-3`` low (silent),
    ``4-7`` default (vibrates), ``>=8`` high (heads-up + sound). So a pending
    question buzzes and a routine pause does not."""

    markdown: bool = True
    """Send the rich body with ``client::display.contentType: text/markdown``.
    Both official clients render CommonMark; turn it off for a client that does
    not."""

    timeout_seconds: float = Field(default=5.0, gt=0)


class WebhookChannelConfig(BaseModel):
    """Generic JSON webhook channel — the "mechanism, not policy" sink.

    POSTs the notification as JSON to ``url``. ntfy's JSON-publish API works
    directly: set ``topic`` and point ``url`` at the ntfy base. ``token_env`` (a
    NAME, never the secret) adds a ``Bearer`` header when set.
    """

    model_config = _FROZEN

    enabled: bool = False
    url: str = ""
    token_env: str = ""
    topic: str = ""
    """ntfy topic; included in the JSON body only when set (ntfy requires it)."""

    priorities: dict[NotifySeverity, Annotated[int, Field(ge=1, le=5)]] = Field(
        default_factory=lambda: dict(_DEFAULT_NTFY_PRIORITIES)
    )
    """Severity → priority on ntfy's 1-5 scale (5 = max, 3 = default), and bounded
    to it: ntfy **rejects** an out-of-range priority outright, so an unvalidated
    value here would silently drop the notification at the far end. A sink that
    reads the JSON itself can ignore the field — the raw ``severity`` string is in
    the body too."""

    timeout_seconds: float = Field(default=5.0, gt=0)


class NotificationsConfig(BaseModel):
    """Push notifications on workspace edges. Off by default.

    Three independent triggers, each with its own switch, all fanning out to
    every enabled channel:

    - ``on`` — a debounced rising edge into an agent state that wants the human:
      ``waiting`` (turn finished), ``blocked`` (awaiting input), ``error``.
    - ``on_question`` — the agent posted a question. Deduped by question id, not
      debounced: a second question inside the quiet window is a second thing the
      human must answer, and it is the one push that must never be dropped.
    - ``on_lifecycle`` — the workspace itself changed (it broke, it was
      orphaned, its session vanished).

    ``deep_link_base_url`` is the webapp base (e.g. ``https://grove.example.com``);
    a notification deep-links to ``{base}/w/{id}`` so tapping it opens that
    workspace. Mechanism, not policy: every value cascades like the rest of the
    config.
    """

    model_config = _FROZEN

    enabled: bool = False
    on: list[NotifyTransition] = Field(default_factory=lambda: list(_DEFAULT_NOTIFY_ON))
    on_question: bool = True
    """Push when the agent asks the human something. The one trigger every
    harness can produce — a question is a transcript/hook *content* signal, while
    ``blocked`` is a *state* only some adapters ever reach."""

    on_lifecycle: list[NotifyLifecycle] = Field(
        default_factory=lambda: list(_DEFAULT_NOTIFY_LIFECYCLE)
    )
    debounce_seconds: float = Field(default=30.0, ge=0)
    """Per-workspace quiet window after a state fire — one buzz per attention
    episode, not one per WAITING↔WORKING tool round-trip. Questions are exempt
    (they dedupe by id instead)."""

    deep_link_base_url: str = "http://localhost:3000"
    """Where a notification's tap lands: ``{base}/w/{id}``, the webapp's workspace
    route — where you can read the transcript *and answer the question*. Defaults
    to the webapp's own local origin (its `next start` port) because a push whose
    link goes nowhere is a dead end, and a single-host install is the overwhelming
    common case. Point it at your reachable origin (e.g.
    ``https://grove.example.com``) to make the tap work from a phone; set it empty
    to render no link at all."""

    gotify: GotifyChannelConfig = Field(default_factory=GotifyChannelConfig)
    webhook: WebhookChannelConfig = Field(default_factory=WebhookChannelConfig)

    @property
    def deep_link_is_loopback(self) -> bool:
        """Whether the tap target is reachable only from this host.

        The feature's quietest failure: the push lands on your phone, you tap it,
        and the phone resolves ``localhost`` to *itself*. Nothing errors — the tap
        just dies, and the notification is worth nothing. The model can answer this
        about itself (pure, no I/O), so the broker's construction edge says it out
        loud instead of every operator discovering it on their lock screen. A LAN
        address is deliberately NOT loopback: a phone on the same network can
        follow it.
        """
        host = urlparse(self.deep_link_base_url).hostname or ""
        return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


class TelemetryConfig(BaseModel):
    """LangFuse credentials + OpenTelemetry passthrough knobs.

    Secret-free like every other integration submodel (the ``mewbo.api_key_env``
    discipline): the three canonical values are env-var NAMES, never secret
    literals, so committed config stays publishable — the actual host/keys live
    only in the consuming process's environment. Off by default (mechanism, not
    policy); a deployment opts in by setting ``enabled: true`` and pointing the
    three ``*_env`` fields at whatever names its host actually exports (exact
    key names per environment are TBD — this holds regardless of what a given
    host calls them).
    """

    model_config = _FROZEN

    enabled: bool = False

    host_env: str = "LANGFUSE_HOST"
    """NAME of the env var holding the Langfuse host (e.g. a self-hosted or
    ``https://cloud.langfuse.com`` URL) — never the URL itself."""

    public_key_env: str = "LANGFUSE_PUBLIC_KEY"
    """NAME of the env var holding the Langfuse public key."""

    secret_key_env: str = "LANGFUSE_SECRET_KEY"
    """NAME of the env var holding the Langfuse secret key — never the secret
    itself."""

    passthrough_kinds: tuple[AgentKind, ...] = ("claude_code", "codex")
    """Which agent runtimes get the derived telemetry env exported into their
    launch env — an allow-list, mechanism not policy (mirrors
    ``NotificationsConfig.on``'s "which transitions" shape). ``mewbo`` runs
    server-side (no local pane to export into) and a bare ``generic`` shell has
    no instrumentation to feed, so both are excluded by default; set explicitly
    to opt in."""

    def derive_env(self, env: Mapping[str, str]) -> dict[str, str]:
        """Derive the launch-env vars from the canonical three, read out of `env`.

        Pure function over a caller-supplied mapping — never `os.environ`
        directly — so it stays testable and composes with the launch
        boundary's own env resolution (`LaunchSpec.env`); the actual
        `os.environ` read happens at that boundary, not here. Returns BOTH
        derivable shapes at once and lets the launch boundary pick per
        `passthrough_kinds`:

        1. the native Langfuse SDK trio (`LANGFUSE_HOST` / `LANGFUSE_PUBLIC_KEY`
           / `LANGFUSE_SECRET_KEY`) — for a runtime whose own code reads these
           directly;
        2. the generic OTEL exporter pair — `OTEL_EXPORTER_OTLP_ENDPOINT`
           (`{host}/api/public/otel`) and `OTEL_EXPORTER_OTLP_HEADERS`
           (`Authorization=Basic <b64(public:secret)>,x-langfuse-ingestion-version=4`)
           — for a runtime that only speaks OTLP. The header is assembled here
           at call time and never stored — only the three source values are
           config.

        A name that resolves to nothing in `env` is silently omitted (a
        partial credential set derives whatever it can); the OTEL pair needs
        all three source values, so it's only emitted when all resolve.
        Disabled (`enabled=False`) always derives nothing.
        """
        if not self.enabled:
            return {}

        host = env.get(self.host_env)
        public_key = env.get(self.public_key_env)
        secret_key = env.get(self.secret_key_env)

        derived: dict[str, str] = {}
        if host:
            derived["LANGFUSE_HOST"] = host
        if public_key:
            derived["LANGFUSE_PUBLIC_KEY"] = public_key
        if secret_key:
            derived["LANGFUSE_SECRET_KEY"] = secret_key

        if host and public_key and secret_key:
            token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
            derived["OTEL_EXPORTER_OTLP_ENDPOINT"] = f"{host.rstrip('/')}/api/public/otel"
            derived["OTEL_EXPORTER_OTLP_HEADERS"] = (
                f"Authorization=Basic {token},x-langfuse-ingestion-version=4"
            )
        return derived


def _default_proxy_upstreams() -> dict[AgentKind, str]:
    return {"claude_code": "https://api.anthropic.com", "codex": "https://api.openai.com/v1"}


def _default_proxy_base_url_env() -> dict[AgentKind, str]:
    return {"claude_code": "ANTHROPIC_BASE_URL", "codex": "OPENAI_BASE_URL"}


class ProxyConfig(BaseModel):
    """Loopback LLM-gateway passthrough proxy for wire-truth capture.

    Off by default (mechanism, not policy). When a deployment opts in and the
    orchestrator serves the proxy (``grove.core.proxy.ProxyApp``), an agent is
    pointed at it through :meth:`proxy_env` at the launch boundary and every
    provider request/response is forwarded VERBATIM while telemetry (true TTFT,
    token usage, latency) is teed off the stream. Nothing here holds a secret —
    upstreams are public API base URLs and the env-var NAMES that carry the
    proxy address to each runtime; auth flows untouched through the proxy, never
    into config.

    Two per-kind maps do the wiring, keyed by ``AgentKind`` (the map keys are the
    opt-in set, the ``TelemetryConfig.passthrough_kinds`` analogue expressed as
    membership): ``upstreams`` = where the proxy forwards that kind's traffic;
    ``base_url_env`` = the env var whose value :meth:`proxy_env` sets to the proxy
    URL (``claude_code`` reads ``ANTHROPIC_BASE_URL``; ``codex`` reads its
    ``model_providers`` base-url env, ``OPENAI_BASE_URL`` by default). Both
    cascade and merge like every other config value.
    """

    model_config = _FROZEN

    enabled: bool = False
    """Master switch. ``False`` leaves launch env and traffic untouched."""

    host: str = "127.0.0.1"
    """Loopback interface the proxy listens on — never a routable address; the
    proxy relays provider credentials, so it must stay same-host only."""

    port: int = Field(default=8788, ge=1, le=65535)
    """Port the proxy listens on. The value :meth:`proxy_env` hands the agent is
    ``http://{host}:{port}``."""

    upstreams: dict[AgentKind, str] = Field(default_factory=_default_proxy_upstreams)
    """Per-kind real upstream base URL the proxy forwards to (verbatim). The keys
    are the opt-in set; a kind absent here is not proxied."""

    base_url_env: dict[AgentKind, str] = Field(default_factory=_default_proxy_base_url_env)
    """Per-kind NAME of the env var that points that runtime at the proxy — the
    documented gateway seam each provider exposes (Claude:
    ``ANTHROPIC_BASE_URL``; Codex: its ``model_providers`` base-url env). Never a
    URL literal here — :meth:`proxy_env` assembles ``{name: proxy_url}``."""

    log_bodies: bool = False
    """Content-gating: capture the REQUEST body into the telemetry event. Off by
    default — a captured body can hold prompt content, so opting in is deliberate.
    Response bodies are NEVER captured; only their token ``usage`` is extracted.
    Headers (auth included) are never captured or logged regardless."""

    max_body_bytes: int = Field(default=8192, ge=0)
    """Truncation cap (bytes) applied to a captured request body when
    ``log_bodies`` is set — bounds the telemetry payload."""

    def proxy_env(self, kind: AgentKind) -> dict[str, str]:
        """Derive the launch-env that points a ``kind`` agent at the proxy.

        The proxy sibling of ``TelemetryConfig.derive_env``: pure, returns the
        ``{env_var_name: proxy_url}`` the launch boundary merges into an agent's
        env so its provider client dials the loopback proxy instead of the real
        upstream (Claude honors ``ANTHROPIC_BASE_URL``; Codex its
        ``model_providers`` base-url env). Disabled, or a kind with no configured
        ``base_url_env`` entry, derives nothing. The proxy URL is the same
        ``host``/``port`` for every kind — a deployment fronting multiple
        providers on distinct ports overrides at the orchestration seam.
        """
        if not self.enabled:
            return {}
        env_name = self.base_url_env.get(kind)
        if not env_name:
            return {}
        return {env_name: f"http://{self.host}:{self.port}"}


class UIConfig(BaseModel):
    """Client-facing UI knobs. The TUI consumes these; core ignores them."""

    model_config = _FROZEN

    theme: str = "auto"
    """Theme id. `auto`/`dark`/`light` map to the built-in Grove themes;
    any other string selects a user override registered from
    `${user_config_dir}/grove/themes/*.toml`. Validated at app startup
    by `grove.tui.theme.resolve_theme_name` — unknown ids raise
    `ConfigError` then, not here, so the cascade can persist a name even
    before its TOML file exists."""

    keybindings: dict[str, str] = Field(default_factory=dict)


# ─── root model ─────────────────────────────────────────────────────────────


class AgentRoster:
    """Owns how the agent roster is composed: the built-in seed, and the gate on it.

    One owner so the built-in specs exist exactly once — they both seed cascade
    layer 0 (:meth:`seed_layer`) and back ``GroveConfig.agents``'s default. Layer
    0 is what makes the ``agents`` merge-by-name a *refinement* of the built-ins
    instead of a wholesale roster replacement; the direct consequence is that a
    built-in can never be dropped by omission, which is why removing one needs
    the explicit ``builtin_agents`` switch :meth:`allowed` implements.
    """

    BUILTINS: ClassVar[tuple[AgentSpec, ...]] = (
        AgentSpec(
            name="claude",
            command="claude",
            kind="claude_code",
            description="Anthropic Claude Code",
        ),
        AgentSpec(
            name="codex",
            command="codex",
            kind="codex",
            description="OpenAI Codex CLI",
        ),
        AgentSpec(name="shell", command="$SHELL", description="Plain shell"),
    )

    @classmethod
    def seed_layer(cls) -> dict[str, Any]:
        """The built-ins as cascade layer 0 — raw JSON, not models.

        It merges with the raw file layers through `_deep_merge` / `_merge_agents`
        long before Pydantic sees anything, so it must speak the same dict shape
        the on-disk layers do.
        """
        return {"agents": [spec.model_dump(mode="json") for spec in cls.BUILTINS]}

    @staticmethod
    def entry_name(entry: Any) -> str:
        """The ``name`` of one raw ``agents`` entry, or ``ConfigError``.

        The single definition of "is this a usable agent entry", read by both
        pre-validation passes over raw layers (:meth:`names_in` and
        `_merge_agents`). Both used to skip anything that was not a dict with a
        ``name``, which sounds defensive and is not: the merge runs BEFORE
        ``model_validate``, so a skipped entry never reaches Pydantic and
        ``extra="forbid"`` — the mechanism this module's docstring promises will
        catch every typo — never gets a chance to fire for it. A user adding a
        custom agent and forgetting ``name`` got a roster silently missing it and
        a DEBUG line nobody sees, while mistyping a *field* on the same entry
        raised loudly. Same layer, same file, opposite outcomes.

        Raising here restores one boundary for bad config. The message carries
        the offending entry because, unlike Pydantic's, it cannot name a field
        path — the entry has no valid identity to point at.
        """
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise ConfigError(
                f"invalid 'agents' entry {entry!r}: each entry must be an object with a "
                "string 'name' (the key every layer's overrides merge by)"
            )
        return str(entry["name"])

    @classmethod
    def names_in(cls, layers: Iterable[Mapping[str, Any]]) -> frozenset[str]:
        """Every agent ``name`` declared across raw (pre-validation) config layers.

        Reads user JSON before `model_validate` has vetted it, so a malformed
        entry raises here (:meth:`entry_name`) rather than being skipped —
        pre-validation code is the ONLY thing that can report it, since Pydantic
        never sees an entry the merge dropped. A non-list ``agents`` still falls
        through untouched: that one Pydantic does see, and its message points at
        the field far better than anything this pass could say.
        """
        names: set[str] = set()
        for layer in layers:
            entries = layer.get("agents")
            if not isinstance(entries, list):
                continue
            names.update(cls.entry_name(entry) for entry in entries)
        return frozenset(names)

    @staticmethod
    def allowed(specs: Iterable[AgentSpec], *, declared: frozenset[str]) -> list[AgentSpec]:
        """Filter a resolved roster down to what the config layers named.

        The ``builtin_agents: false`` gate. "Declared" alone is the whole rule —
        no built-in-name membership test is needed, because a user-defined agent
        is in ``declared`` by construction, so the only thing this can ever drop
        is a built-in nobody asked for.
        """
        return [spec for spec in specs if spec.name in declared]


class ExclusiveGroups:
    """Owns cross-layer resolution for fields that cannot legally coexist.

    The cascade merges field-by-field, so a lower layer's value survives unless a
    higher layer sets *that same field* — which no layer can do for a mutually
    exclusive pair. A project `.grove/config.json` setting `init_script.path` and
    a machine-local `.grove/config.local.json` setting `init_script.inline` merged
    into a config holding BOTH, and every create in that repo failed and rolled
    back. Each layer was individually valid, so no per-layer validator could see
    it: the merge itself manufactures the invalid combination.

    The rule: the highest-precedence layer that mentions ANY member of a group
    wins the WHOLE group, and every member is stripped from the lower layers
    before `_deep_merge` runs. "Mentions" is key presence, so an explicit
    `"path": null` clears a lower layer's script rather than being a no-op.

    Mechanism only — which fields form a group is declared by the model that owns
    them (`InitScriptConfig.EXCLUSIVE_FIELDS`), never inlined here, and never in
    `_deep_merge`, which stays a policy-free dict merge.
    """

    GROUPS: ClassVar[Mapping[str, tuple[str, ...]]] = {
        "init_script": InitScriptConfig.EXCLUSIVE_FIELDS,
        ContainerConfig.SECTION: ContainerConfig.EXCLUSIVE_FIELDS,
        TicketsConfig.SECTION: TicketsConfig.EXCLUSIVE_FIELDS,
    }

    @classmethod
    def resolve(cls, layers: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Return `layers` (lowest→highest precedence) with losing members stripped.

        Copies every layer it touches: the inputs come from `_read_json` and
        `AgentRoster.seed_layer()` and belong to the caller. Warns only when a
        strip actually drops something — the silent override is what made the
        original bug invisible, but a no-op group must stay quiet.
        """
        resolved = [dict(layer) for layer in layers]
        for section, fields in cls.GROUPS.items():
            winner = cls._winning_layer(resolved, section, fields)
            if winner is None:
                continue
            kept = [name for name in fields if name in resolved[winner][section]]
            for index in range(winner):
                block = resolved[index].get(section)
                if not isinstance(block, dict):
                    continue
                dropped = [name for name in fields if name in block]
                if not dropped:
                    continue
                resolved[index][section] = {k: v for k, v in block.items() if k not in fields}
                logger.warning(
                    "config cascade: {} set in a higher-precedence layer wins the "
                    "mutually-exclusive group in '{}' — dropping {} from a "
                    "lower-precedence layer",
                    ", ".join(f"'{name}'" for name in kept),
                    section,
                    ", ".join(f"'{name}'" for name in dropped),
                )
        return resolved

    @staticmethod
    def _winning_layer(
        layers: Sequence[Mapping[str, Any]], section: str, fields: tuple[str, ...]
    ) -> int | None:
        """Index of the highest-precedence layer naming any member, else None."""
        for index in reversed(range(len(layers))):
            block = layers[index].get(section)
            if isinstance(block, dict) and any(name in block for name in fields):
                return index
        return None


class CommittedShareFloor:
    """Cross-layer resolution of ``container.agent_config.share``.

    The trust boundary the whole containerization epic rests on: **a committed
    layer may REQUEST a capability; only a non-committed layer may GRANT one.**
    A committed ``.grove/config.json`` travels with the repository, so a repo
    that could raise ``share`` would be granting itself the host's real agent
    credentials — while a repo that *lowers* it (an untrusted vendor drop asking
    to be run isolated) is asking for less, which is always safe to honor.

    Why this can't be a field validator or plain precedence: the cascade merges
    field-by-field with the highest layer winning, so the committed project layer
    legitimately outranks the *user* layer beneath it. Direction, not position,
    is the rule here — the same reason ``ExclusiveGroups`` runs as a pre-pass:
    the merge itself manufactures the value no layer is allowed to author. The
    resolution is therefore a pure pre-pass over the raw layers, run immediately
    before ``_deep_merge``: the committed value is stripped, and re-applied as a
    synthetic TOP layer only when it tightens what everything else resolved to.
    Tightening wins outright — a project forcing ``isolated`` must not be undone
    by a user-level ``full`` — which is exactly the asymmetry precedence alone
    cannot express.
    """

    SECTION: ClassVar[str] = "container"
    SUBSECTION: ClassVar[str] = "agent_config"
    FIELD: ClassVar[str] = "share"

    @classmethod
    def resolve(
        cls, layers: Sequence[Mapping[str, Any]], *, committed: frozenset[int]
    ) -> list[dict[str, Any]]:
        """Return ``layers`` (lowest→highest) with the tighten-only rule applied.

        ``committed`` holds the indexes of layers that ship inside the repo.
        Copies every layer it touches — the inputs belong to the caller.
        """
        resolved = [dict(layer) for layer in layers]
        asks: dict[int, AgentShare] = {}
        for index in committed:
            value = cls._share(resolved[index])
            if value is not None:
                asks[index] = value
        if not asks:
            return resolved
        for index in asks:
            cls._strip(resolved, index)
        granted = cls._share_of(resolved) or "full"
        # Tightest ask across the committed layers: several may declare it, and
        # honoring the loosest would let a nested layer relax a stricter one.
        tightest = min(asks.values(), key=lambda value: SHARE_RANK[value])
        if SHARE_RANK[tightest] >= SHARE_RANK[granted]:
            logger.warning(
                "config cascade: committed 'container.agent_config.share' = '{}' ignored — "
                "a committed layer may only tighten sharing (effective: '{}')",
                tightest,
                granted,
            )
            return resolved
        resolved.append({cls.SECTION: {cls.SUBSECTION: {cls.FIELD: tightest}}})
        return resolved

    @classmethod
    def _share(cls, layer: Mapping[str, Any]) -> AgentShare | None:
        """The share value one raw layer declares, if it declares a legal one."""
        section = layer.get(cls.SECTION)
        if not isinstance(section, dict):
            return None
        block = section.get(cls.SUBSECTION)
        if not isinstance(block, dict):
            return None
        value = block.get(cls.FIELD)
        # An illegal literal is left in place for Pydantic to reject with its own
        # message rather than being silently dropped here.
        return value if value in SHARE_RANK else None

    @classmethod
    def _share_of(cls, layers: Sequence[Mapping[str, Any]]) -> AgentShare | None:
        """The value the remaining (non-committed) layers resolve to."""
        for layer in reversed(layers):
            value = cls._share(layer)
            if value is not None:
                return value
        return None

    @classmethod
    def _strip(cls, layers: list[dict[str, Any]], index: int) -> None:
        """Remove the share key from one layer, leaving its siblings intact."""
        section = dict(layers[index][cls.SECTION])
        block = {k: v for k, v in section[cls.SUBSECTION].items() if k != cls.FIELD}
        section[cls.SUBSECTION] = block
        layers[index][cls.SECTION] = section


class CommittedEnvSource:
    """What a committed layer may say about an :class:`EnvSourceConfig`.

    Same rule as :class:`CommittedShareFloor`, applied to a different capability:
    ``.grove/config.json`` is COMMITTED, so it travels with the repository and is
    untrusted input — **a committed layer may REQUEST a capability, never GRANT
    one**. Direction, not precedence: the committed project layer legitimately
    outranks the user layer beneath it, so only a pre-pass over the raw layers can
    express this.

    ``env_command`` from a committed layer is stripped outright. Honoring it
    would run a repository-supplied command on the host the moment someone clones
    and creates a workspace — remote code execution, dressed as configuration.

    ``env_file`` is deliberately treated DIFFERENTLY, and the asymmetry is the
    interesting part. It executes nothing, so it is not RCE — but it does let a
    committed config name an ARBITRARY HOST PATH and ferry its contents into the
    container (or into a request to a tracker), a real exfiltration primitive that
    a committed ``.devcontainer/`` cannot otherwise reach (the container only ever
    sees the worktree). Banning it outright would cost the legitimate case this
    feature is for — a team committing ``"env_file": ".grove/container.env"`` so
    every clone picks the convention up. So the rule is containment, not denial: a
    committed ``env_file`` must be repo-relative and stay inside the repo
    (`EnvSourceConfig.env_file_is_contained`); absolute paths, ``~``, and any
    ``..`` that escapes are stripped. Non-committed layers (user config,
    ``.grove/config.local.json``, env, CLI) may name anything — the operator is
    trusted by definition.

    An explicit ``null`` from a committed layer is left alone in both cases: it
    clears rather than grants, and asking for LESS is always safe to honor.

    Warnings never include the value of ``env_command`` — a command line can carry
    a token. A rejected ``env_file`` path is named, because that is what makes the
    warning actionable.

    Every :class:`EnvSourceConfig` section is covered by the SAME rule, from the
    one list below: the trust question ("what does this let a committed layer
    REACH") is a property of the mechanism, not of the feature consuming it, so a
    new consumer inherits the boundary instead of re-arguing it.
    """

    SECTIONS: ClassVar[tuple[str, ...]] = (ContainerConfig.SECTION, TicketsConfig.SECTION)
    COMMAND_FIELD: ClassVar[str] = "env_command"
    FILE_FIELD: ClassVar[str] = "env_file"

    @classmethod
    def resolve(
        cls, layers: Sequence[Mapping[str, Any]], *, committed: frozenset[int]
    ) -> list[dict[str, Any]]:
        """Return ``layers`` (lowest→highest) with disallowed committed keys gone.

        ``committed`` holds the indexes of layers that ship inside the repository.
        Copies every layer it touches — the inputs belong to the caller.
        """
        resolved = [dict(layer) for layer in layers]
        for index in committed:
            for section in cls.SECTIONS:
                block = resolved[index].get(section)
                if not isinstance(block, dict):
                    continue
                dropped = [
                    name for name in (cls.COMMAND_FIELD, cls.FILE_FIELD) if cls._denied(block, name)
                ]
                if not dropped:
                    continue
                resolved[index][section] = {k: v for k, v in block.items() if k not in dropped}
                for name in dropped:
                    cls._warn(section, name, block[name])
        return resolved

    @classmethod
    def _denied(cls, block: Mapping[str, Any], field: str) -> bool:
        """May a committed layer keep this field's value?

        A missing key or an explicit ``null`` is never denied — neither grants
        anything. Anything that is not a string is left for Pydantic to reject with
        its own message rather than being silently swallowed here.
        """
        value = block.get(field)
        if not isinstance(value, str):
            return False
        if field == cls.COMMAND_FIELD:
            return True
        return not EnvSourceConfig.env_file_is_contained(value)

    @classmethod
    def _warn(cls, section: str, field: str, value: str) -> None:
        """One warning per stripped key, never echoing a command line."""
        if field == cls.COMMAND_FIELD:
            logger.warning(
                "config cascade: committed '{}.env_command' ignored — a committed "
                "layer may not run a host command (set it in your user config or "
                ".grove/config.local.json instead)",
                section,
            )
            return
        logger.warning(
            "config cascade: committed '{}.env_file' = '{}' ignored — a committed "
            "layer may only name a path inside the repository",
            section,
            value,
        )


class EnvReferences:
    """Resolves ``${VAR}`` references in string config values against the env.

    The ``GROVE_<SECTION>__<FIELD>`` layer already lets the environment override
    any field, but the variable NAME is fixed by the schema path. This closes the
    other half: a value may reference a variable of the user's OWN choosing, so a
    config can point at ``${MY_GOTIFY_URL}`` and never hold the value itself. It
    is for non-secret values; a credential still names its variable through the
    dedicated ``*_env`` fields, which this deliberately does not absorb.

    Resolution runs over the MERGED dict, immediately before
    ``GroveConfig.model_validate``, so every field validator sees the resolved
    string and a bad resolved value is rejected exactly like a bad literal.

    **An unset or EMPTY variable is a loud error naming both the variable and the
    dotted config path.** Emptiness reads as absence in the implicit ``GROVE_*``
    override layer, and that rule must not travel here: writing the reference IS
    the opt-in, and the failure it prevents is real — an empty base URL resolves
    to a config that loads fine and then silently sends telemetry nowhere.

    ``$${VAR}`` escapes to a literal ``${VAR}``, and it is the only escape. Text
    that is not this exact shape — a bare ``$``, ``$FOO`` without braces — is
    left completely alone. This is a reference mechanism, not shell
    interpolation.

    ``${repo}`` and ``${repo_name}`` are RESERVED and pass through untouched:
    they belong to :func:`expand_template`, a later expansion against a concrete
    repository, and consuming them here would break every path template using
    them.

    Pure over ``(mapping, env)`` — it never reads ``os.environ`` itself, so the
    caller supplies the environment and a test needs no process patching.
    """

    PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"\$(\$?)\{([A-Za-z_][A-Za-z0-9_]*)\}")
    RESERVED: ClassVar[frozenset[str]] = frozenset({"repo", "repo_name"})

    @classmethod
    def resolve(cls, merged: Mapping[str, Any], env: Mapping[str, str]) -> dict[str, Any]:
        """Return ``merged`` with every reference in a string VALUE resolved.

        Keys are never rewritten — a section named after a variable is a section,
        not a reference.
        """
        return {key: cls._value(value, env, key) for key, value in merged.items()}

    @classmethod
    def _value(cls, value: Any, env: Mapping[str, str], path: str) -> Any:
        """Walk one node; only strings are candidates, lists included by index."""
        if isinstance(value, str):
            return cls._text(value, env, path)
        if isinstance(value, dict):
            return {key: cls._value(item, env, f"{path}.{key}") for key, item in value.items()}
        if isinstance(value, list):
            return [cls._value(item, env, f"{path}[{index}]") for index, item in enumerate(value)]
        return value

    @classmethod
    def _text(cls, text: str, env: Mapping[str, str], path: str) -> str:
        """Substitute every reference in one string, or raise naming the first gap."""

        def substitute(match: re.Match[str]) -> str:
            escaped, name = match.group(1), match.group(2)
            if escaped:
                return "${" + name + "}"
            if name in cls.RESERVED:
                return match.group(0)
            value = env.get(name, "")
            if not value:
                state = "is empty" if name in env else "is not set"
                raise ConfigError(
                    f"config: '{path}' references environment variable '{name}', which {state}"
                )
            return value

        return cls.PATTERN.sub(substitute, text)


class DeclaredEnvVars:
    """A field may DECLARE the one environment variable that overrides it.

    The complement of :class:`EnvReferences`, and the two run in opposite
    directions. A reference is written BY the operator in a config file as the
    value ``${VARIABLE}``, names whatever variable that file chose to defer to,
    and is an error when the variable is unset. A declaration is made BY this
    module on a field the operator may have set to a perfectly good literal, is
    always on, and simply does not apply when its variable is unset — nobody
    asked for it, so silence is the only correct answer, and an empty value is
    how a shell says nothing rather than how an operator blanks a setting.
    **Both behaviours are right for their own mechanism; neither is the other's
    inconsistency.**

    Precedence, lowest to highest: the literal in the config file, a ``${VAR}``
    reference written in that file (it IS the file's value, only resolved), the
    field's declared variable, then ``GROVE_<SECTION>__<FIELD>``. The declared
    variable losing to the schema-path layer is deliberate: the schema-path
    name states the exact field it fills and is unambiguous by construction,
    while a declared name is a convenience alias for one field somebody thought
    worth naming. When an operator has exported both, the one that says what it
    means should win.

    So this is a cascade LAYER, not a model validator — a `mode="before"`
    validator on the submodel would run after the whole merge and beat the
    schema-path layer, which is the precedence inverted. The layer is sparse
    (only variables the environment actually supplies), which is what keeps it
    from mentioning fields no one set.

    The name lives in the field's ``json_schema_extra`` so the declaration
    reaches the exported JSON schema and therefore the published configuration
    reference, rather than being a hand-written ``os.environ`` read that no
    reader outside this file can discover. **The schema is the census** — never
    write a second list of these somewhere else.

    Declared only for NON-secret fields. A credential still travels through the
    dedicated ``*_env`` fields, which name a variable the consumer reads at the
    moment of use so no layer ever holds the secret itself.
    """

    SCHEMA_KEY: ClassVar[str] = "x-env-var"

    @classmethod
    def layer(cls, model: type[BaseModel], env: Mapping[str, str]) -> dict[str, Any]:
        """A sparse cascade layer of every declared override ``env`` supplies.

        Walks the model tree so the annotation is the single source of truth.
        Only a field whose annotation IS a submodel is descended into: a list of
        models (``agents``) is not, because an override applies to a field, and
        a list has no field to name.
        """
        layer: dict[str, Any] = {}
        for name, field in model.model_fields.items():
            annotation = field.annotation
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                nested = cls.layer(annotation, env)
                if nested:
                    layer[name] = nested
                continue
            variable = cls.declared(field)
            if variable and env.get(variable):
                layer[name] = env[variable]
        return layer

    @classmethod
    def declared(cls, field: FieldInfo) -> str | None:
        """The variable one field declares, if it declares a usable one."""
        extra = field.json_schema_extra
        variable = extra.get(cls.SCHEMA_KEY) if isinstance(extra, dict) else None
        return variable if isinstance(variable, str) else None


class GroveConfig(BaseModel):
    """Merged, validated configuration. Built once per `load_config` call."""

    model_config = _MUTABLE

    schema_url: str = Field(default="", alias="$schema")
    # Repo roots to surface as "known projects" even with zero workspaces, so a
    # freshly-added project (or one whose workspaces were all killed) stays
    # visible in the pickers. Mechanism, not policy: a plain list of path strings
    # (``~`` expanded at consume time, like ``expand_template``); the registry
    # unions these into ``known_roots()`` and drops any that aren't an existing
    # git repo. A user-level concern that still cascades like every other field.
    projects: list[str] = Field(default_factory=list)
    worktree: WorktreeConfig = Field(default_factory=WorktreeConfig)
    builtin_agents: bool = True
    """Whether Grove's own agents (``claude``, ``codex``, ``shell``) are offered
    alongside the ones you declare.

    ``true`` (the default) is the standing contract: the built-ins are literally layer
    0 of the cascade, so declaring ``agents`` REFINES them and can never hide one
    by omission. That is right for a first-run install and wrong for an operator
    whose fleet runs its own curated profiles — the stock three still show up in
    every picker with no way to say no. Flip this to ``false`` and the roster is
    exactly what your layers declare; name a built-in (even bare
    ``{"name": "claude"}``) to opt it back in and inherit its full built-in spec
    through the same merge-by-name. Forward-compatible by construction: a built-in
    added in a future Grove release stays hidden too, instead of appearing on
    upgrade.

    Hiding an agent removes it from ``find_agent`` as well, so ``create`` rejects
    it on every surface (CLI/MCP/HTTP) and an existing workspace pinned to it can
    no longer resume/respawn — it gets the typed "no longer present in config"
    error. That blast radius is the point: the roster is one list, and hiding an
    agent is the operator saying nothing should run it anymore."""

    agents: list[AgentSpec] = Field(default_factory=lambda: list(AgentRoster.BUILTINS))
    init_script: InitScriptConfig = Field(default_factory=InitScriptConfig)
    tmux: TmuxConfig = Field(default_factory=TmuxConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    brief: BriefConfig = Field(default_factory=BriefConfig)
    container: ContainerConfig = Field(default_factory=ContainerConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    permission: PermissionConfig = Field(default_factory=PermissionConfig)
    mewbo: MewboConfig = Field(default_factory=MewboConfig)
    tickets: TicketsConfig = Field(default_factory=TicketsConfig)
    issueops: IssueOpsConfig = Field(default_factory=IssueOpsConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)

    def find_agent(self, name: str) -> AgentSpec | None:
        for spec in self.agents:
            if spec.name == name:
                return spec
        return None


# ─── public helpers ─────────────────────────────────────────────────────────


def expand_template(template: str, repo_root: Path) -> Path:
    """Replace ${repo}/${repo_name}/~ in a path template against a concrete repo.

    Done at consume time, not validate time, so the same persisted config
    can serve any repo without re-validation.
    """
    expanded = template.replace("${repo}", str(repo_root)).replace("${repo_name}", repo_root.name)
    return Path(expanded).expanduser()


def load_config(
    repo_root: Path | None,
    cli_overrides: dict[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> GroveConfig:
    """Resolve the full cascade and return a validated GroveConfig.

    Layers (last wins): built-in defaults → user JSON → project JSON →
    project-local JSON → per-field declared env vars (`DeclaredEnvVars`) →
    GROVE_* env vars → caller-supplied CLI overrides.

    Once the layers have merged, any ``${VAR}`` a string value carries is
    resolved against the same environment (`EnvReferences`) — before validation,
    so a resolved value is checked exactly like a literal one.
    """
    env_map = env if env is not None else os.environ
    # Everything the *user* supplied, kept apart from the built-in seed below:
    # `builtin_agents: false` filters the resolved roster down to the names these
    # layers declare, and folding the seed in here would name every built-in and
    # make that gate a no-op.
    overlays: list[dict[str, Any]] = []
    # Which overlays ship INSIDE the repository. Committed layers are untrusted
    # input (they travel with the code), so a capability they may only request —
    # not grant — is resolved by index, not by precedence (`CommittedShareFloor`).
    committed: set[int] = set()

    user_path = paths.user_config_path()
    if user_path.exists():
        overlays.append(_read_json(user_path))

    if repo_root is not None:
        project_path = paths.project_config_path(repo_root)
        if project_path.exists():
            committed.add(len(overlays))
            overlays.append(_read_json(project_path))
        local_path = paths.project_local_config_path(repo_root)
        if local_path.exists():
            overlays.append(_read_json(local_path))

    # Declared per-field variables sit BELOW the schema-path layer: the name a
    # field declares is a convenience alias, the `GROVE_<SECTION>__<FIELD>` name
    # states the exact field it fills (`DeclaredEnvVars`).
    declared_layer = DeclaredEnvVars.layer(GroveConfig, env_map)
    if declared_layer:
        overlays.append(declared_layer)

    env_layer = _parse_env_overrides(env_map)
    if env_layer:
        overlays.append(env_layer)

    if cli_overrides:
        overlays.append(cli_overrides)

    # The built-in agents are literally layer 0 of the cascade, so the `agents`
    # merge-by-name REFINES them instead of replacing the roster: a config that
    # redeclares `claude` keeps kind="claude_code" (the merge contract
    # `_merge_agents` documents), and a scaffolded project config never silently
    # hides codex/shell. Every other field's default already survives via the
    # Pydantic model.
    # Mutually exclusive fields are resolved BEFORE the merge: a field-by-field
    # merge cannot express "my `inline` replaces your `path`", so the two layers
    # would both survive into a combination no layer asked for (`ExclusiveGroups`).
    layers = ExclusiveGroups.resolve([AgentRoster.seed_layer(), *overlays])
    # +1: the seed layer shifts every overlay index by one.
    committed_indexes = frozenset(i + 1 for i in committed)
    layers = CommittedShareFloor.resolve(layers, committed=committed_indexes)
    # Trust runs AFTER exclusivity on purpose, and the ordering has a visible
    # consequence worth stating: the group pass resolves which SOURCE the user
    # chose across layers, then the committed pass strips a choice a committed
    # layer was never allowed to make. So when a committed layer WINS the group
    # with `env_command`, the lower layers' members are already gone and the
    # result is NO env source at all — not a silent fall back to some lower
    # layer's `env_file`. That is the fail-safe outcome: a repo's attempt to
    # claim the slot must not quietly promote the operator's own setting into it.
    layers = CommittedEnvSource.resolve(layers, committed=committed_indexes)
    merged = _deep_merge(*layers)
    # Explicit references resolve LAST, over the merged result rather than per
    # layer: a reference is a property of the value that won, and resolving
    # per layer would pay for values nothing ever reads.
    merged = EnvReferences.resolve(merged, env_map)

    try:
        cfg = GroveConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration: {exc}") from exc

    # `builtin_agents: false` is filtered AFTER the merge, never by dropping the
    # seed layer — dropping it would re-break this twice over: with no layer
    # declaring `agents` at all, Pydantic's `default_factory` puts the built-ins
    # back anyway, and a config that redeclares `{"name": "claude"}` would lose
    # kind="claude_code" and degrade to `generic`, silently killing that
    # workspace's transcript tracking. Layer 0 must keep merging so refinement
    # works; the roster is trimmed afterwards. Post-validation also means the
    # flag is a real coerced bool (a JSON `"false"` string can't read truthy)
    # and `cfg.agents` are real specs — `_MUTABLE` sets no `validate_assignment`,
    # so this assignment doesn't re-run validators.
    if not cfg.builtin_agents:
        cfg.agents = AgentRoster.allowed(cfg.agents, declared=AgentRoster.names_in(overlays))

    logger.debug("config loaded: {} layers merged", len(layers))
    return cfg


def add_known_project(repo_root: Path, *, target: Path | None = None) -> bool:
    """Append ``repo_root`` to the user config's ``projects`` list (idempotent).

    Read-modify-write on the raw JSON layer (:func:`_read_json`, the same seam
    :func:`load_config` reads) rather than round-tripping through a merged
    ``GroveConfig`` — a full merge would bake every cascade default back into
    the user file. Targets the *user* config by default: ``projects`` is a
    user-level concern (part of the ``known_roots()`` union), so a repo becomes
    visible cross-project without needing its own committed
    ``.grove/config.json``. Returns ``False`` (no write) when already listed.
    """
    target = target or paths.user_config_path()
    raw = _read_json(target) if target.exists() else {}
    existing = raw.get("projects", [])
    if not isinstance(existing, list):
        raise ConfigError(f"{target}: 'projects' must be a list")
    resolved = str(repo_root.resolve())
    already_known = {str(Path(p).expanduser().resolve()) for p in existing if isinstance(p, str)}
    if resolved in already_known:
        return False
    raw["projects"] = [*existing, resolved]
    try:
        GroveConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration: {exc}") from exc
    paths.ensure_dir(target.parent)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, target)
    return True


def dump_schema_json() -> str:
    """Return the JSON Schema for `GroveConfig` as a `str`.

    Pure helper — no I/O.  Two callers depend on this identical byte stream
    so they cannot drift: `write_schema` (writes the IDE-autocomplete
    file next to the user config) and `grove config schema --stdout`
    (feeds the docs-build hook that renders `configure-reference.md`).
    Trailing newline keeps shell pipelines clean.
    """
    schema = GroveConfig.model_json_schema()
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def write_schema(target: Path | None = None) -> Path:
    """Write the JSON Schema for GroveConfig next to the user config.

    Users reference it via `"$schema": "./config.schema.json"` for IDE autocomplete.
    Returns the path written to.
    """
    target = target or paths.user_schema_path()
    paths.ensure_dir(target.parent)
    text = dump_schema_json()
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, target)
    return target


def dump_config_json(cfg: GroveConfig) -> str:
    """Pretty-print a config back to JSON (round-trips through model_validate_json)."""
    return cfg.model_dump_json(indent=2, exclude_none=True, by_alias=True)


# ─── internal: merge + env parsing + I/O ────────────────────────────────────


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Expected JSON object at top level of {path}")
    return data


def _deep_merge(*layers: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge, last-wins. The `agents` list merges by `name`."""
    result: dict[str, Any] = {}
    for layer in layers:
        for key, value in layer.items():
            existing = result.get(key)
            if key == "agents" and isinstance(value, list) and isinstance(existing, list):
                result[key] = _merge_agents(existing, value)
            elif isinstance(value, dict) and isinstance(existing, dict):
                result[key] = _deep_merge(existing, value)
            else:
                result[key] = value
    return result


def _merge_agents(base: list[Any], overlay: list[Any]) -> list[dict[str, Any]]:
    """Merge agent lists by `name`, **field-level**; new names appended in overlay order.

    An overlay entry whose `name` matches a base entry refines it field-by-field
    (overlay fields win, base fields fill the gaps) rather than replacing it
    wholesale. That is what lets a user tweak only the `claude` agent's `command`
    while keeping its `kind="claude_code"` — without field-merge, omitting `kind`
    on the override would silently drop the agent back to the `generic` default
    and disable Activity Dashboard tracking. This is the cascade principle applied
    at field granularity: each layer overrides the previous, value by value.

    An entry with no usable `name` raises through `AgentRoster.entry_name` — this
    pass is the last place a bad entry can be reported, because dropping it here
    would keep it away from `model_validate` entirely.
    """
    by_name: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in base:
        name = AgentRoster.entry_name(item)
        if name not in by_name:
            order.append(name)
        by_name[name] = item
    for item in overlay:
        name = AgentRoster.entry_name(item)
        if name not in by_name:
            order.append(name)
            by_name[name] = item
        else:
            by_name[name] = {**by_name[name], **item}
    return [by_name[n] for n in order]


def _parse_env_overrides(env: Mapping[str, str]) -> dict[str, Any]:
    """Parse GROVE_<SECTION>__<FIELD>=value into a nested dict.

    Double-underscore separates nesting depth; field names lowercase.
    Values stay strings; Pydantic coerces on validation.

    Only keys whose top-level segment is a real GroveConfig field are
    consumed. GROVE_* is a shared namespace — GROVE_DEBUG, the provider
    token vars config itself names (GROVE_GITEA_TOKEN, ...), the installer
    knobs — and the strict model would otherwise hard-fail every config
    load the moment any of them is exported.

    That carve-out has a cost worth naming: mistyping a *field*
    (`GROVE_TMUX__SESSON_PREFIX`) raises loudly at validation, while mistyping
    the *section* one segment earlier (`GROVE_TMUXX__SESSION_PREFIX`) is
    indistinguishable from a foreign `GROVE_*` var and disappears. The
    exemption still stands — it must, or any exported `GROVE_*` breaks every
    load — but a var carrying the `__` nesting separator is announcing itself
    as a config override, and nothing else in the shared namespace uses that
    shape. So an unknown section WITH a separator warns rather than whispering
    at DEBUG: the one signal available without giving up the carve-out.
    """
    known_fields = GroveConfig.model_fields.keys()
    result: dict[str, Any] = {}
    for key, raw in env.items():
        if not key.startswith("GROVE_"):
            continue
        suffix = key[len("GROVE_") :]
        if not suffix:
            continue
        parts = [p.lower() for p in suffix.split("__") if p]
        if not parts:
            continue
        if parts[0] not in known_fields:
            if len(parts) > 1:
                logger.warning(
                    "ignoring env var {}: {!r} is not a config section — a GROVE_*__* "
                    "variable that names no section is silently dropped, so this is "
                    "almost certainly a typo",
                    key,
                    parts[0],
                )
            else:
                logger.debug("ignoring non-config env var {}", key)
            continue
        cursor: dict[str, Any] = result
        for part in parts[:-1]:
            nxt = cursor.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[part] = nxt
            cursor = nxt
        cursor[parts[-1]] = raw
    return result
