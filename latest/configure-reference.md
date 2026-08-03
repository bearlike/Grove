# Configuration reference

This page is generated from Grove's Pydantic model.  Edit `src/grove/core/config.py` and run `make docs` (or push to the default branch, which regenerates in CI) to refresh.


## Environment variable overrides

These fields read from a fixed environment variable when it is set and non-empty, so a deployment can supply the value without editing a config file. An unset or empty variable simply does not override. `GROVE_<SECTION>__<FIELD>` still wins over the name below, and any string value can also reference a variable you choose yourself with `${YOUR_VAR}` — see [the cascade](features-cascade.md).


| Field | Variable |
|---|---|
| `notifications.gotify.server_url` | `GROVE_GOTIFY_API_URL` |
| `tickets.gitea.base_url` | `GROVE_GITEA_BASE_URL` |

## `agents` (list of `AgentSpec`)

One selectable agent in the new-workspace picker.


| Field | Type | Default | Description |
|---|---|---|---|
| `command` | `string` | `**required**` |  |
| `description` | `string` | ``''`` |  |
| `env` | `object` | `(none)` |  |
| `env_unset` | `array<string>` | ``[]`` |  |
| `kind` | `string` | ``'generic'`` |  |
| `models` | `array<string>` | ``[]`` |  |
| `name` | `string` | `**required**` |  |
| `tools_offline` | `boolean` | ``False`` |  |

## `auth`

Daemon HTTP authentication knobs.

The handshake-based pairing flow gates every HTTP entry point on a valid
bearer token (no loopback bypass; see CLAUDE.md). ``enabled = false`` is
a test-only escape hatch; production daemons leave it ``true``.


| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `boolean` | ``True`` |  |
| `pair_init_per_minute` | `integer` | ``5`` |  |
| `pair_poll_per_minute` | `integer` | ``60`` |  |
| `pairing_ttl_seconds` | `integer` | ``300`` |  |
| `session_ttl_seconds` | `integer` | ``2592000`` |  |

## `brief`

The one-paragraph brief a new workspace's agent is handed on its first
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


| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `boolean` | ``True`` |  |

## `builtin_agents`


## `channels`

Grove-managed Claude Code *channel* delivery (research preview).

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


| Field | Type | Default | Description |
|---|---|---|---|
| `allowed_senders` | `array<string>` | `(none)` |  |
| `enabled` | `boolean` | ``False`` |  |

## `container`

Run the agent inside a container instead of directly on the host.

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


| Field | Type | Default | Description |
|---|---|---|---|
| `agent_config` | `object` | `(none)` | What the container shares from the host agent configuration.  Default ``full``: native integration with the host — config, sign-in, skills — is a *feature*, delivered through bind mounts, and it is what makes a fully-autonomous agent useful rather than a second sign-in chore. ``isolated`` restores the credential boundary for an untrusted repository at the cost of that sign-in; ``projects`` shares transcripts only.  **Trust rule (enforced in :class:`CommittedShareFloor`): a committed layer may force a tighter value but never raise sharing.** A committed ``.grove/config.json`` is untrusted input — a repo that could set ``share: full`` would be granting itself the host's credentials. |
| `decor` | `object` | `(none)` | Whether the container gets Grove's own tmux chrome and statusline.  Grove bind-mounts a small read-only asset bundle into every containerized workspace at ``/grove/decor``: a tmux config and a Claude Code statusline script. Both exist because a person attached to a container workspace otherwise has no way to tell they are in one — the user's own statusline script is never shared into the container, and the in-container tmux has no config at all — and because a status bar inside a container must report the CONTAINER's own cgroup CPU/memory limits, not ``/proc/loadavg``, which is not namespaced and answers for the HOST. |
| `default_config` | `string` | ``''`` |  |
| `docker_bin` | `string` | ``'docker'`` |  |
| `egress` | `object` | `(none)` | Where a containerized agent may reach on the network.  With credentials shared and permission prompts off, egress is the control that carries the weight: it bounds where a token can be *sent*. The list is **derived, not restated** — the planner (``core.container_policy``) unions the agent plane for the workspace's kind, the package plane, the repo's own git remotes and the Grove plane, so normal dev work needs zero config. ``allow`` is purely additive on top.  Documented ceilings, carried from the reference implementation this follows: UDP/53 stays open (DNS tunneling is not defended against) and name-based filtering loses to domain fronting. ``open`` is the supported, un-nagged no-firewall path — never a warning, never a refusal. |
| `enabled` | `boolean` | ``True`` |  |
| `env_command` | `string \| null` | ``None`` |  |
| `env_file` | `string \| null` | ``None`` |  |
| `resources` | `object` | `(none)` | Per-container caps, applied at launch.  One knob on the policy cascade, deliberately NOT a slice hierarchy: the agent container gets them via ``docker update`` after ``up`` (the devcontainer CLI owns creation, so post-hoc update is the one dependable application point) and compose stack services via ``deploy.resources`` in the generated override. Empty/zero means "do not cap" — every field is independently optional, so a memory-only policy emits a memory-only update.  ``hostRequirements`` in a ``devcontainer.json`` is a different question — a *declaration* Grove checks and refuses on, never a cap it applies. |
| `shell` | `array<string>` | ``['bash', 'sh']`` |  |
| `tmux` | `object` | `(none)` | Whether the agent runs under a tmux INSIDE its container, and whose tmux.  The multiplexer used to sit on the far side of the namespace boundary from the process it multiplexes: the agent ran as ``devcontainer exec … -- claude`` typed into a HOST pane, so the PTY died with the host client and nothing could ever attach back to the surviving in-container process. Running the agent under a container-side tmux makes the host pane a *viewport* and the in-container tmux the owner of the agent's lifetime.  Two policy questions live here rather than in code, because both are genuinely the operator's:  * ``prefer_image`` — an image that ships its own tmux is almost always the   better answer (it matches the distro's terminfo and the user's own   expectations), but an operator standardizing on one tmux across a fleet   may want Grove's bundle everywhere. * ``payload`` — where the static binary comes from. Empty means Grove's own   built-and-cached bundle; a path lets an operator supply a vetted build   (an air-gapped host, a signed artifact, a different tmux version).  Everything degrades honestly: with no image tmux and no payload, the launch composes a plain ``exec`` and the workspace loses persistence, never the workspace itself. |
| `up_timeout_seconds` | `number` | ``900.0`` |  |

## `hooks`

Grove-managed Claude Code status hooks. On by default.

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


| Field | Type | Default | Description |
|---|---|---|---|
| `daemon_url` | `string` | ``''`` |  |
| `enabled` | `boolean` | ``True`` |  |

## `init_script`

Optional setup script run in its own tmux window before the agent starts.


| Field | Type | Default | Description |
|---|---|---|---|
| `applies_to` | `string` | ``'all'`` |  |
| `enabled` | `boolean` | ``False`` |  |
| `fail_fast` | `boolean` | ``True`` |  |
| `inline` | `string \| null` | ``None`` |  |
| `path` | `string \| null` | ``None`` |  |
| `run_on_resume` | `boolean` | ``False`` |  |
| `shell` | `string` | ``'bash'`` |  |
| `timeout_seconds` | `integer` | ``300`` |  |

## `issueops`

Turn issue-comment mentions into workspace actions, and mirror progress back.

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


| Field | Type | Default | Description |
|---|---|---|---|
| `agent` | `string` | ``'claude'`` |  |
| `allowed_actors` | `array<string>` | `(none)` |  |
| `assign_bot` | `boolean` | ``False`` |  |
| `deep_link_base_url` | `string` | ``''`` |  |
| `enabled` | `boolean` | ``False`` |  |
| `pickup_backoff_seconds` | `number` | ``300.0`` |  |
| `pickup_enabled` | `boolean` | ``False`` |  |
| `pickup_interval_seconds` | `number` | ``60.0`` |  |
| `pickup_max_active` | `integer` | ``3`` |  |
| `prompt_template` | `string` | ``'You are handling tracker issue #{number}: "{title}".\n\nIssue description:\n{body}\n\nThread so far:\n{comments}\n\nHow you were engaged:\n{command_text}\n\nYour mandate:\n\n- Work autonomously through to an OPEN pull request. Read the issue, map the\n  affected components (read the nearest owning CLAUDE.md before editing),\n  implement the change, run the project\'s gates, open the PR, and reply on the\n  ticket saying what changed and where the PR is.\n- Where the ticket is underspecified, REPLY ON THE TICKET asking for exactly\n  what is missing, and say what you will assume if nobody answers. Do not stall\n  silently, and do not quietly guess at a requirement you could have asked\n  about.\n- Satisfy the stated goals faithfully and safely — the goals as written, not\n  the larger project you would rather do.\n- Stop and ask rather than take a risky or irreversible action: destroying\n  data, force-pushing a shared branch, or touching anything in production.\n\nIssue link: {url}\n'`` |  |
| `trigger` | `string` | ``'@grove'`` |  |
| `update_window_seconds` | `number` | ``5.0`` |  |

## `mewbo`

Connection settings for the Mewbo orchestrator (``kind: "mewbo"`` agents).

The ``mewbo`` adapter reads these to reach the Mewbo API: a workspace of
that kind mints its session on the orchestrator rather than in a local
process, so the connection details are config rather than discovery.


| Field | Type | Default | Description |
|---|---|---|---|
| `api_key_env` | `string` | ``'MEWBO_API_KEY'`` |  |
| `base_url` | `string` | ``'http://127.0.0.1:5125'`` |  |
| `timeout_seconds` | `number` | ``10.0`` |  |

## `notifications`

Push notifications on workspace edges. Off by default.

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


| Field | Type | Default | Description |
|---|---|---|---|
| `debounce_seconds` | `number` | ``30.0`` |  |
| `deep_link_base_url` | `string` | ``'http://localhost:3000'`` |  |
| `enabled` | `boolean` | ``False`` |  |
| `gotify` | `object` | `(none)` | Gotify push channel.  ``server_url`` is the Gotify base (e.g. ``https://gotify.example.com``), and it also reads from ``GROVE_GOTIFY_API_URL`` so a deployment can point every repo at its own server without editing a file. ``token_env`` is the NAME of the env var holding the *application* token (Gotify's ``Axxx…``, the send-only kind), never the token itself — committed config stays secret-free, exactly like ``mewbo.api_key_env``. |
| `on` | `array<string>` | `(none)` |  |
| `on_lifecycle` | `array<string>` | `(none)` |  |
| `on_question` | `boolean` | ``True`` |  |
| `webhook` | `object` | `(none)` | Generic JSON webhook channel — the "mechanism, not policy" sink.  POSTs the notification as JSON to ``url``. ntfy's JSON-publish API works directly: set ``topic`` and point ``url`` at the ntfy base. ``token_env`` (a NAME, never the secret) adds a ``Bearer`` header when set. |

## `permission`

Grove-hosted Claude Code ``--permission-prompt-tool`` answering.

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


| Field | Type | Default | Description |
|---|---|---|---|
| `default` | `string` | ``'deny'`` |  |
| `enabled` | `boolean` | ``False`` |  |

## `projects`


## `proxy`

Loopback LLM-gateway passthrough proxy for wire-truth capture.

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


| Field | Type | Default | Description |
|---|---|---|---|
| `base_url_env` | `object` | `(none)` |  |
| `enabled` | `boolean` | ``False`` |  |
| `host` | `string` | ``'127.0.0.1'`` |  |
| `log_bodies` | `boolean` | ``False`` |  |
| `max_body_bytes` | `integer` | ``8192`` |  |
| `port` | `integer` | ``8788`` |  |
| `upstreams` | `object` | `(none)` |  |

## `telemetry`

LangFuse credentials + OpenTelemetry passthrough knobs.

Secret-free like every other integration submodel (the ``mewbo.api_key_env``
discipline): the three canonical values are env-var NAMES, never secret
literals, so committed config stays publishable — the actual host/keys live
only in the consuming process's environment. Off by default (mechanism, not
policy); a deployment opts in by setting ``enabled: true`` and pointing the
three ``*_env`` fields at whatever names its host actually exports (exact
key names per environment are TBD — this holds regardless of what a given
host calls them).


| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `boolean` | ``False`` |  |
| `host_env` | `string` | ``'LANGFUSE_HOST'`` |  |
| `passthrough_kinds` | `array<string>` | ``['claude_code', 'codex']`` |  |
| `public_key_env` | `string` | ``'LANGFUSE_PUBLIC_KEY'`` |  |
| `secret_key_env` | `string` | ``'LANGFUSE_SECRET_KEY'`` |  |

## `tickets`

External ticket-tracker integration, one submodel per MVP provider.

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


| Field | Type | Default | Description |
|---|---|---|---|
| `env_command` | `string \| null` | ``None`` |  |
| `env_file` | `string \| null` | ``None`` |  |
| `gitea` | `object` | `(none)` | Gitea Issues provider settings (``provider: "gitea"``).  Secret-free like every config layer: ``token_env`` is the NAME of the environment variable holding the API token, never the token itself, so a committed ``.grove/config.json`` stays publishable. ``base_url`` is the instance root (the provider appends ``/api/v1``), and it also reads from ``GROVE_GITEA_BASE_URL``: ``token_env`` already lets an operator instrument the token, so leaving the endpoint config-only would mean a deployment could move the credential and not the server it authenticates against — which is how a token ends up pointed at the wrong host. ``branch_prefix`` is an optional extra keyword prepended when formatting a branch (e.g. ``"gtea-"`` → ``gtea-123-slug``); empty means the bare numeric form ``123-slug``. The parser recognizes the bare numeric leading segment, the built-in keywords, and this configured prefix — mechanism, not policy. |
| `github` | `object` | `(none)` | GitHub Issues provider settings (``provider: "github"``).  ``base_url`` defaults to the public REST API; point it at a GitHub Enterprise ``/api/v3`` root to use Enterprise. Same secret-free ``token_env`` + optional ``branch_prefix`` contract as the Gitea provider. |
| `linear` | `object` | `(none)` | Linear provider settings (``provider: "linear"``).  Linear keys are alphanumeric (``ENG-123``), so there is no numeric ``branch_prefix`` — the team key IS the discriminator. ``team_key`` scopes both branch parsing (only ``{team_key}-N`` keys are claimed) and the ``get_ticket`` lookup; leave it unset to match any uppercase key on parse. |

## `tmux`

tmux session/window naming and behavior.


| Field | Type | Default | Description |
|---|---|---|---|
| `activity_threshold_seconds` | `integer` | ``30`` |  |
| `agent_window_name` | `string` | ``'agent'`` |  |
| `history_limit` | `integer` | ``50000`` |  |
| `init_window_name` | `string` | ``'init'`` |  |
| `peek_history_lines` | `integer` | ``500`` |  |
| `peek_pane_refresh_seconds` | `number` | ``0.25`` |  |
| `peek_stats_refresh_seconds` | `number` | ``3.0`` |  |
| `session_prefix` | `string` | ``'grove-'`` |  |
| `shell_window_name` | `string` | ``'shell'`` |  |
| `steer_settle_ms` | `integer` | ``200`` |  |

## `ui`

Client-facing UI knobs. The TUI consumes these; core ignores them.


| Field | Type | Default | Description |
|---|---|---|---|
| `keybindings` | `object` | `(none)` |  |
| `theme` | `string` | ``'auto'`` |  |

## `worktree`

Where worktrees live and how branches are named.


| Field | Type | Default | Description |
|---|---|---|---|
| `branch_prefix` | `string` | ``'grove/'`` |  |
| `root_template` | `string` | ``'${repo}/.worktrees'`` |  |
