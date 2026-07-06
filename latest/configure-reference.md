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

## `hooks`

Opt-in Grove-managed Claude Code status hooks (#18).

When ``enabled``, Grove launches ``claude_code`` agents with
``--settings <grove-hooks-settings>`` so a lightweight hook pushes exact
lifecycle status (``WORKING`` / ``WAITING`` / ``BLOCKED`` / ``IDLE``) into a
per-session sidecar that the Activity Dashboard prefers over polled status —
giving precise *blocked-on-a-permission-prompt* that polling can't see. Off
by default (mechanism, not policy); the user's own ``.claude/settings.json``
is never touched, so uninstalling is just flipping this back to ``false``.


| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `boolean` | ``False`` |  |

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

## `projects`


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
