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
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError

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

    The hermetic half of the launch env (issue #82): a tmux pane inherits the
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
    """Launch this agent with network-facing tools disallowed (#148): Claude
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
    text and sending the submitting Enter (#180). The TUI's bracketed paste
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
    """Grove-managed Claude Code status hooks (#18; on by default since #171).

    When ``enabled``, Grove launches ``claude_code`` agents with
    ``--settings <grove-hooks-settings>`` so a lightweight hook pushes exact
    lifecycle status (``WORKING`` / ``WAITING`` / ``BLOCKED`` / ``IDLE``) into a
    per-session sidecar that the Activity Dashboard prefers over polled status —
    giving precise *blocked-on-a-permission-prompt* that polling can't see, plus
    an immediate daemon refresh over the native http hook (#171) instead of
    waiting out the poll tick. On by default now that the sidecar is the
    primary live signal rather than a dormant opt-in sidecar (still a
    mechanism knob, not policy); the user's own ``.claude/settings.json`` is
    never touched, so disabling is just flipping this back to ``false``.
    """

    model_config = _FROZEN

    enabled: bool = True


class ContainerConfig(BaseModel):
    """Run the agent inside a container instead of directly on the host (#65).

    Off by default (mechanism, not policy): a workspace opts in and its
    ``DockerExecLaunchBackend`` builds/starts the container, bind-mounts the
    worktree at ``workspace_mount``, and runs the assembled agent command via
    ``docker exec`` inside it. Every value cascades like the rest of the config;
    a future Podman driver reads the SAME submodel behind the same protocol —
    ``docker_bin`` names the CLI, it is not the driver-swap seam.
    """

    model_config = _FROZEN

    enabled: bool = False
    """Master switch. ``False`` keeps the default host tmux launch untouched."""

    image: str = ""
    """Image the container runs (e.g. ``python:3.12``). When ``dockerfile`` is
    set this doubles as the build tag; otherwise it is pulled as-is. Required
    (non-empty) for a container launch — an empty image is a config error the
    driver surfaces at launch, not here, so the cascade can persist a partial
    config before the image is chosen."""

    dockerfile: str = ""
    """Worktree-relative Dockerfile to build into ``image`` before first run.
    Empty (default) pulls ``image`` as-is — no build step."""

    build_context: str = "."
    """Worktree-relative build context passed to ``docker build`` when
    ``dockerfile`` is set."""

    workspace_mount: str = "/workspace"
    """Absolute path INSIDE the container where the worktree is bind-mounted;
    the agent's container workdir is this plus its ``project_subpath``."""

    exec_user: str = ""
    """``docker exec -u`` value (``uid``/``name``/``uid:gid``). Empty keeps the
    image's default user."""

    mounts: tuple[str, ...] = ()
    """Extra ``-v`` bind specs (``host:container[:opts]``) passed verbatim to
    ``docker run`` — passthrough mechanism, never parsed or validated here."""

    network: str = ""
    """``docker run --network`` value. Empty keeps the engine default network."""

    run_args: tuple[str, ...] = ()
    """Extra args spliced into ``docker run`` before the image (``--gpus``,
    ``--cpus``, an ``--env-file`` …). Pure passthrough — the mechanism seam that
    keeps container knobs out of Grove's own field set."""

    docker_bin: str = "docker"
    """The container CLI binary/path (``docker``, or an absolute path / drop-in
    shim). NOT the Podman-driver seam — a different runtime is a different
    ``ContainerDriver`` class, this only points at a docker-compatible CLI."""


class ChannelsConfig(BaseModel):
    """Grove-managed Claude Code *channel* delivery (#182; research preview).

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
    """Grove-hosted Claude Code ``--permission-prompt-tool`` answering (#172).

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

    #35 registers the kind with a stub adapter; the REST-backed implementation
    (#36) reads these to reach the Mewbo API.
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
    instance root (the provider appends ``/api/v1``). ``branch_prefix`` is an
    optional extra keyword prepended when formatting a branch (e.g. ``"gtea-"``
    → ``gtea-123-slug``); empty means the bare numeric form ``123-slug``. The
    parser recognizes the bare numeric leading segment, the built-in keywords,
    and this configured prefix — mechanism, not policy.
    """

    model_config = _FROZEN

    enabled: bool = False
    base_url: str = "https://gitea.com"
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


class TicketsConfig(BaseModel):
    """External ticket-tracker integration, one submodel per MVP provider.

    Each provider is independently ``enabled`` and configured. All three stay
    off by default (mechanism, not policy): a repo opts in by enabling the
    tracker its branches reference. Credentials never live here — only the NAME
    of the env var holding each token.
    """

    model_config = _FROZEN

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

The human triggered you with this comment:
{command_text}

Drive it to a merged PR autonomously: read the issue and map the affected
components (read the nearest owning CLAUDE.md before editing), implement the
change, run the project's gates, open a PR that closes the issue, and reply on
the issue with what changed and a link to the PR. Ask only about genuinely
ambiguous decisions; bias to action.

Issue link: {url}
"""


class IssueOpsConfig(BaseModel):
    """Turn issue-comment mentions into workspace actions (#196) + mirror progress back (#197).

    One submodel, two faces. INBOUND (#196): a commenter mentions the ``trigger``
    token as the first word of an issue comment; the forwarder (a stateless CI
    action) POSTs the event to the daemon, and the engine parses the grammar,
    enforces the permission policy, and routes to a workspace verb. OUTBOUND
    (#197): when ``enabled``, the daemon runs a status publisher that mirrors each
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
    """Master switch for the OUTBOUND live status comment (#197) — the sticky
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
    ``{body}``, ``{number}``, ``{url}``, and ``{command_text}`` placeholders filled
    from the event. Cascades like all config; the built-in default instructs the
    agent on the autonomous issue→PR loop."""

    update_window_seconds: float = Field(default=5.0, ge=0)
    """Coalescing window for the OUTBOUND status comment (#197) — at most one
    comment PATCH per workspace per window. Forges apply secondary rate limits to
    same-comment edit storms, so the publisher folds every render-relevant change
    into per-workspace state and flushes the merged result once the window
    elapses. ``0`` flushes every change (no coalescing)."""

    deep_link_base_url: str = ""
    """The webapp base (e.g. ``https://grove.example.com``); a status comment
    deep-links to ``{base}/w/{id}``. Reuses the ``notifications`` deep-link
    convention verbatim. Empty (default) omits the link."""


# Which agent-state edges may fire a push. String values mirror
# ``AgentActivityState`` (waiting/blocked/error/idle) — kept a Literal here, not
# the imported enum, so ``config.py`` never imports ``grove.core.agents`` (which
# would cycle: agents → registry → adapters → config). The notifications
# subpackage coerces these strings back to the enum at its construction edge.
NotifyTransition = Literal["waiting", "blocked", "error", "idle"]
_DEFAULT_NOTIFY_ON: list[NotifyTransition] = ["waiting", "blocked", "error"]


class GotifyChannelConfig(BaseModel):
    """Gotify push channel (#70's first channel).

    ``server_url`` is the Gotify base (e.g. ``https://gotify.example.com``);
    ``token_env`` is the NAME of the env var holding the application token, never
    the token itself — committed config stays secret-free, exactly like
    ``mewbo.api_key_env``.
    """

    model_config = _FROZEN

    enabled: bool = False
    server_url: str = ""
    token_env: str = "GROVE_GOTIFY_TOKEN"
    priority: int = Field(default=5, ge=0, le=10)
    """Gotify message priority (0 to 10); ~8+ triggers high-priority delivery."""

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

    timeout_seconds: float = Field(default=5.0, gt=0)


class NotificationsConfig(BaseModel):
    """Push notifications on agent-state edges (#70). Off by default.

    The broker fires a debounced rising edge into one of the ``on`` states —
    ``waiting`` (turn finished), ``blocked`` (awaiting input), ``error`` — and
    fans out to every enabled channel. ``deep_link_base_url`` is the webapp base
    (e.g. ``https://grove.example.com``); a notification deep-links to
    ``{base}/w/{id}`` so tapping it opens that workspace. Mechanism, not policy:
    every value cascades like the rest of the config.
    """

    model_config = _FROZEN

    enabled: bool = False
    on: list[NotifyTransition] = Field(default_factory=lambda: list(_DEFAULT_NOTIFY_ON))
    debounce_seconds: float = Field(default=30.0, ge=0)
    """Per-workspace quiet window after a fire — one buzz per attention episode,
    not one per WAITING↔WORKING tool round-trip."""

    deep_link_base_url: str = ""
    gotify: GotifyChannelConfig = Field(default_factory=GotifyChannelConfig)
    webhook: WebhookChannelConfig = Field(default_factory=WebhookChannelConfig)


class TelemetryConfig(BaseModel):
    """LangFuse credentials + OpenTelemetry passthrough knobs (issue #176).

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
    """Loopback LLM-gateway passthrough proxy for wire-truth capture (issue #177).

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


def _default_agents() -> list[AgentSpec]:
    return [
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
    ]


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
    agents: list[AgentSpec] = Field(default_factory=_default_agents)
    init_script: InitScriptConfig = Field(default_factory=InitScriptConfig)
    tmux: TmuxConfig = Field(default_factory=TmuxConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
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
    project-local JSON → GROVE_* env vars → caller-supplied CLI overrides.
    """
    layers: list[dict[str, Any]] = [
        # The built-in agents are literally layer 0 of the cascade, so the
        # `agents` merge-by-name REFINES them instead of replacing the
        # roster: a config that redeclares `claude` keeps kind="claude_code"
        # (the merge contract `_merge_agents` documents), and a scaffolded
        # project config never silently hides codex/shell (#105). Every
        # other field's default already survives via the Pydantic model.
        {"agents": [a.model_dump(mode="json") for a in _default_agents()]},
    ]

    user_path = paths.user_config_path()
    if user_path.exists():
        layers.append(_read_json(user_path))

    if repo_root is not None:
        project_path = paths.project_config_path(repo_root)
        if project_path.exists():
            layers.append(_read_json(project_path))
        local_path = paths.project_local_config_path(repo_root)
        if local_path.exists():
            layers.append(_read_json(local_path))

    env_layer = _parse_env_overrides(env if env is not None else os.environ)
    if env_layer:
        layers.append(env_layer)

    if cli_overrides:
        layers.append(cli_overrides)

    merged = _deep_merge(*layers) if layers else {}

    try:
        cfg = GroveConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration: {exc}") from exc

    logger.debug("config loaded: {} layers merged", len(layers))
    return cfg


def add_known_project(repo_root: Path, *, target: Path | None = None) -> bool:
    """Append ``repo_root`` to the user config's ``projects`` list (idempotent).

    Read-modify-write on the raw JSON layer (:func:`_read_json`, the same seam
    :func:`load_config` reads) rather than round-tripping through a merged
    ``GroveConfig`` — a full merge would bake every cascade default back into
    the user file. Targets the *user* config by default: ``projects`` is a
    user-level concern (#95's ``known_roots()`` union), so a repo becomes
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
    """
    by_name: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in base:
        if isinstance(item, dict) and "name" in item:
            name = item["name"]
            if name not in by_name:
                order.append(name)
            by_name[name] = item
    for item in overlay:
        if isinstance(item, dict) and "name" in item:
            name = item["name"]
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
    load the moment any of them is exported (#105).
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
