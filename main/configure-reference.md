# Configuration reference

This page is generated from Grove's Pydantic model.  Edit `src/grove/core/config.py` and run `make docs` (or push to the default branch, which regenerates in CI) to refresh.


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

## `channels`

Grove-managed Claude Code *channel* delivery (#182; research preview).

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

Run the agent inside a container instead of directly on the host (#65).

Off by default (mechanism, not policy): a workspace opts in and its
``DockerExecLaunchBackend`` builds/starts the container, bind-mounts the
worktree at ``workspace_mount``, and runs the assembled agent command via
``docker exec`` inside it. Every value cascades like the rest of the config;
a future Podman driver reads the SAME submodel behind the same protocol —
``docker_bin`` names the CLI, it is not the driver-swap seam.


| Field | Type | Default | Description |
|---|---|---|---|
| `build_context` | `string` | ``'.'`` |  |
| `docker_bin` | `string` | ``'docker'`` |  |
| `dockerfile` | `string` | ``''`` |  |
| `enabled` | `boolean` | ``False`` |  |
| `exec_user` | `string` | ``''`` |  |
| `image` | `string` | ``''`` |  |
| `mounts` | `array<string>` | ``[]`` |  |
| `network` | `string` | ``''`` |  |
| `run_args` | `array<string>` | ``[]`` |  |
| `workspace_mount` | `string` | ``'/workspace'`` |  |

## `hooks`

Grove-managed Claude Code status hooks (#18; on by default since #171).

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


| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `boolean` | ``True`` |  |

## `init_script`

Optional setup script run in its own tmux window before the agent starts.


| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `boolean` | ``False`` |  |
| `fail_fast` | `boolean` | ``True`` |  |
| `inline` | `string \| null` | ``None`` |  |
| `path` | `string \| null` | ``None`` |  |
| `run_on_resume` | `boolean` | ``False`` |  |
| `shell` | `string` | ``'bash'`` |  |
| `timeout_seconds` | `integer` | ``300`` |  |

## `issueops`

Turn issue-comment mentions into workspace actions (#196) + mirror progress back (#197).

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


| Field | Type | Default | Description |
|---|---|---|---|
| `agent` | `string` | ``'claude'`` |  |
| `allowed_actors` | `array<string>` | `(none)` |  |
| `deep_link_base_url` | `string` | ``''`` |  |
| `enabled` | `boolean` | ``False`` |  |
| `prompt_template` | `string` | ``'You are handling tracker issue #{number}: "{title}".\n\nIssue description:\n{body}\n\nThe human triggered you with this comment:\n{command_text}\n\nDrive it to a merged PR autonomously: read the issue and map the affected\ncomponents (read the nearest owning CLAUDE.md before editing), implement the\nchange, run the project\'s gates, open a PR that closes the issue, and reply on\nthe issue with what changed and a link to the PR. Ask only about genuinely\nambiguous decisions; bias to action.\n\nIssue link: {url}\n'`` |  |
| `trigger` | `string` | ``'@grove'`` |  |
| `update_window_seconds` | `number` | ``5.0`` |  |

## `mewbo`

Connection settings for the Mewbo orchestrator (``kind: "mewbo"`` agents).

#35 registers the kind with a stub adapter; the REST-backed implementation
(#36) reads these to reach the Mewbo API.


| Field | Type | Default | Description |
|---|---|---|---|
| `api_key_env` | `string` | ``'MEWBO_API_KEY'`` |  |
| `base_url` | `string` | ``'http://127.0.0.1:5125'`` |  |
| `timeout_seconds` | `number` | ``10.0`` |  |

## `notifications`

Push notifications on agent-state edges (#70). Off by default.

The broker fires a debounced rising edge into one of the ``on`` states —
``waiting`` (turn finished), ``blocked`` (awaiting input), ``error`` — and
fans out to every enabled channel. ``deep_link_base_url`` is the webapp base
(e.g. ``https://grove.example.com``); a notification deep-links to
``{base}/w/{id}`` so tapping it opens that workspace. Mechanism, not policy:
every value cascades like the rest of the config.


| Field | Type | Default | Description |
|---|---|---|---|
| `debounce_seconds` | `number` | ``30.0`` |  |
| `deep_link_base_url` | `string` | ``''`` |  |
| `enabled` | `boolean` | ``False`` |  |
| `gotify` | `object` | `(none)` | Gotify push channel (#70's first channel).  ``server_url`` is the Gotify base (e.g. ``https://gotify.example.com``); ``token_env`` is the NAME of the env var holding the application token, never the token itself — committed config stays secret-free, exactly like ``mewbo.api_key_env``. |
| `on` | `array<string>` | `(none)` |  |
| `webhook` | `object` | `(none)` | Generic JSON webhook channel — the "mechanism, not policy" sink.  POSTs the notification as JSON to ``url``. ntfy's JSON-publish API works directly: set ``topic`` and point ``url`` at the ntfy base. ``token_env`` (a NAME, never the secret) adds a ``Bearer`` header when set. |

## `permission`

Grove-hosted Claude Code ``--permission-prompt-tool`` answering (#172).

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

Loopback LLM-gateway passthrough proxy for wire-truth capture (issue #177).

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

LangFuse credentials + OpenTelemetry passthrough knobs (issue #176).

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
of the env var holding each token.


| Field | Type | Default | Description |
|---|---|---|---|
| `gitea` | `object` | `(none)` | Gitea Issues provider settings (``provider: "gitea"``).  Secret-free like every config layer: ``token_env`` is the NAME of the environment variable holding the API token, never the token itself, so a committed ``.grove/config.json`` stays publishable. ``base_url`` is the instance root (the provider appends ``/api/v1``). ``branch_prefix`` is an optional extra keyword prepended when formatting a branch (e.g. ``"gtea-"`` → ``gtea-123-slug``); empty means the bare numeric form ``123-slug``. The parser recognizes the bare numeric leading segment, the built-in keywords, and this configured prefix — mechanism, not policy. |
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
