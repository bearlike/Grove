# Configuration reference

## Every field and its default

Every section of `.grove/config.json`, one table each, with a nested block's fields listed under the field that holds it. Generated from Grove's own config model.


## Environment variable overrides

These fields also read a fixed environment variable, so a deployment can set them without a config file. An unset variable does not override, `GROVE_<SECTION>__<FIELD>` still wins, and any string value can reference a variable of your own with `${YOUR_VAR}`. See [the cascade](features-cascade.md).


| Field | Variable |
|---|---|
| `notifications.gotify.server_url` | `GROVE_GOTIFY_API_URL` |
| `tickets.gitea.base_url` | `GROVE_GITEA_BASE_URL` |
| `tls.ca_path` | `GROVE_TLS_CA_PATH` |

## Top-level fields


| Field | Type | Default | Description |
|---|---|---|---|
| `builtin_agents` | `boolean` | `true` | Whether Grove's own agents are offered alongside the ones you declare. Off makes your `agents` list the whole roster. |
| `lifecycle_max_pending` | `integer` | `64` | Maximum accepted lifecycle operations, including lock waiters and running work. |
| `macros` | `object` | unset | Grove commands by name, run as ``/grove:<name>``. A map, so every layer adds or replaces commands by name; none ship built in, because which model is fast is a fact about your fleet. |
| `projects` | `array<string>` | unset | Repo roots kept visible in every picker even with zero workspaces. `~` expands, and a path that is not a git repo is ignored. |
| `projects_root` | `string` | `"~/Projects"` | The directory new projects are created or cloned into, and scanned for existing repositories not yet in `projects`. `~` expands. The web wizard shows it and lets one add change it. |
| `runtimes` | `object` | unset | Remote runtimes by name. A workspace created on one runs as a long-lived container on that platform and dials this daemon; nothing dials in. |

## `activity_admission`

Capacity includes work already handed to consumers.


| Field | Type | Default | Description |
|---|---|---|---|
| `max_bytes` | `integer` | `4194304` |  |
| `max_items` | `integer` | `256` |  |

## `agent_cwds`

Named directories inside the repo an agent may start in. Only the agent session moves, the worktree, branch and init script stay at the root.


| Field | Type | Default | Description |
|---|---|---|---|
| `default` | `string \| null` | unset | The label a create resolves to when it names no directory. Unset starts the agent at the worktree root. |
| `entries` | `object` | unset | Label to repo relative path. The label is what a person picks, the path is what the agent starts in, and insertion order is picker order. |

## `agents` (a list, one entry each)

One selectable agent in the create picker. Anything terminal based works.


| Field | Type | Default | Description |
|---|---|---|---|
| `command` | `string` | **required** | Shell command sent to the agent window. Quoted arguments and `$VAR` expansion work. |
| `description` | `string` | unset | One line label shown in the picker. |
| `env` | `object` | unset | Extra environment variables exported into the agent's window before launch. A `${VAR}` value is resolved from the environment when this agent launches, not when the config loads, so a variable only this agent needs never blocks another command. |
| `env_unset` | `array<string>` | unset | Variable names cleared before `env` is applied, so an ambient value cannot leak into the agent's window. |
| `kind` | `string` | `"generic"` | Which adapter reads this agent's session. `claude_code` and `codex` read transcripts for live state and tokens, `mewbo` reads a remote session over REST, `generic` tracks nothing. |
| `models` | `array<string>` | unset | Model ids offered in the create form picker. A convenience list, never a validated allowlist. Empty uses the adapter's native discovery or maintained fallback catalog. |
| `name` | `string` | **required** | Picker identifier, and the merge key across cascade layers. |
| `native` | `boolean` | `true` | Run the agent as a Grove owned native session instead of its interactive terminal. On by default for Claude Code and Codex, ignored by other kinds, and every create surface can override it per workspace. |
| `native_timeout_seconds` | `number` | `180` | How long a native session may take to answer one protocol exchange: the boot handshake, a submitted prompt's acceptance, a control request such as an interrupt or a model switch. A gateway that queues requests, a slow session-start hook, or a fleet booting at once all land inside this window; past it the exchange is reported as unknown and a boot ends the session. Per agent, because a gateway-backed profile and a local binary have nothing in common here. |
| `tools_offline` | `boolean` | `false` | Launch with network facing tools disallowed. Claude Code drops `WebFetch` and `WebSearch`, Codex turns off sandbox networking. No effect on `generic` or `mewbo`. |

## `artifacts`

Where a workspace's file artifacts may live, beyond its own checkout.


| Field | Type | Default | Description |
|---|---|---|---|
| `roots` | `array<string>` | `["/tmp"]` | Extra directories a file artifact may sit under. ``~`` expands. |

## `auth`

Daemon authentication and pairing limits.


| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `boolean` | `true` | Master switch for daemon authentication. Only in process tests turn it off. |
| `oidc` | see below | | Sign people in with an OpenID Connect provider. |
| `pair_init_per_minute` | `integer` | `5` | Per source rate limit on starting a pairing, which bounds brute force. |
| `pair_poll_per_minute` | `integer` | `60` | Per source rate limit on polling a pairing. The browser polls every two seconds while it waits for approval. |
| `pairing_ttl_seconds` | `integer` | `300` | How long a pairing code stays valid for approval. |
| `session_ttl_seconds` | `integer` | `2592000` | Sliding session lifetime. Every request extends it, so a daily user never re pairs and an idle device ages out. |

### `oidc`


| Field | Type | Default | Description |
|---|---|---|---|
| `audience` | `string` | unset | The ID token audience the daemon accepts. Empty means ``client_id``. |
| `client_id` | `string` | unset | This Grove's client id at the issuer, used by the CLI device flow. |
| `issuer` | `string` | unset | The issuer URL; its ``/.well-known/openid-configuration`` names everything else. |

## `brief`

The one paragraph brief a new agent is handed on its first turn, pointing it at Grove's `working-in-grove` skill.


| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `boolean` | `true` | Whether a new agent receives the brief at all. |
| `instructions` | `string` | unset | Your own text, appended to the brief every agent in this repo receives. |

## `channels`

Grove managed Claude Code channel delivery, a research preview.


| Field | Type | Default | Description |
|---|---|---|---|
| `admission` | see below | | Capacity includes work already handed to consumers. |
| `allowed_senders` | `array<string>` | unset | Sender labels allowed to post into a running session's channel. Empty allows every sender, so populate it to restrict. |
| `enabled` | `boolean` | `false` | Whether Grove delivers channel messages into running Claude Code sessions. |

### `admission`


| Field | Type | Default | Description |
|---|---|---|---|
| `max_bytes` | `integer` | `4194304` |  |
| `max_items` | `integer` | `256` |  |

## `container`

Run the agent inside a container built from the repo's own devcontainer.


| Field | Type | Default | Description |
|---|---|---|---|
| `agent_config` | see below | | What the container shares from your host agent configuration. |
| `decor` | see below | | Whether the container gets Grove's tmux chrome and statusline. |
| `default_config` | `string` | unset | The `devcontainer.json` used for a repo that has none of its own. Empty uses the self contained config packaged with Grove. |
| `docker_bin` | `string` | `"docker"` | The docker compatible CLI Grove shells out to. A name on `PATH` or an absolute path. |
| `egress` | see below | | Where a containerized agent may reach on the network. |
| `enabled` | `boolean` | `true` | Whether Grove keeps this repo's container image warm so a container create starts faster. It does not choose the runtime: a create runs on the host unless it, or a saved `defaults.runtime`, asks for a container. |
| `env_command` | `string \| null` | unset | Host command whose stdout is parsed as dotenv, so a secret never touches disk. Re run at every use and never honored from a committed layer. |
| `env_file` | `string \| null` | unset | Dotenv file this section's variables are read from. Repo relative or absolute, `~` expanded. A configured file that is missing is an error at use. |
| `resources` | see below | | Per container caps, applied at launch. |
| `shell` | `array<string>` | `["bash", "sh"]` | Shells `grove shell` tries inside the container, in order. |
| `tmux` | see below | | Whether the agent runs under a tmux inside its container, and whose tmux. |
| `up_timeout_seconds` | `number` | `900.0` | Seconds one `devcontainer up` or build may take before the create counts as failed. A cold build is legitimately minutes long. |

### `agent_config`


| Field | Type | Default | Description |
|---|---|---|---|
| `share` | `string` | `"full"` | How much of your agent configuration the container sees. `full` mounts sign in, skills and memory, `projects` mounts transcripts only, `isolated` gives the container its own config directory. A committed layer can only tighten it. |
| `trust` | `boolean` | `true` | Seed the workspace folder as already trusted and its committed `.mcp.json` servers as already approved, so the agent never stops on a first run dialog nobody can answer. |

### `decor`


| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `boolean` | `true` | Mount and compose the terminal chrome bundle at all. |
| `payload` | `string` | unset | A host directory of your own decor assets, mounted read only in place of Grove's bundle. |
| `statusline` | `boolean` | `true` | Compose Grove's statusline into the agent's settings. |
| `tmux_conf` | `boolean` | `true` | Pass Grove's tmux config to the in container tmux. |

### `egress`


| Field | Type | Default | Description |
|---|---|---|---|
| `agent_plane` | `object` | unset | Per agent kind provider endpoints. A gateway deployment repoints them here. |
| `allow` | `array<string>` | unset | Extra hostnames or CIDRs added to the derived allowlist. |
| `grove_plane` | `array<string>` | `["host.docker.internal"]` | How the container reaches Grove itself for hook ingest and the daemon. |
| `mode` | `string` | `"allowlist"` | `allowlist` applies the derived firewall, `open` applies nothing, `deny` permits only loopback and the workspace's own network. Anything but `open` fails closed if the firewall cannot be applied. |
| `package_plane` | `array<string>` | unset | Package indexes and registries reachable regardless of agent kind. |
| `range_sources` | `array<object>` | unset | Providers that publish their address ranges as JSON, fetched from the host at provision time, for hosts whose DNS answers outlive their usefulness. |

### `resources`


| Field | Type | Default | Description |
|---|---|---|---|
| `cpus` | `string` | unset | CPU cap such as `4` or `1.5`, as a string. Empty is uncapped. |
| `memory` | `string` | unset | Memory cap such as `8g` or `512m`. Empty is uncapped. |
| `pids` | `integer` | `0` | Process count cap. `0` is uncapped. |

### `tmux`


| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `boolean` | `true` | Run the agent under a tmux inside the container when one is reachable. Off is a bare exec with no persistence. |
| `payload` | `string` | unset | A host directory holding `bin/<arch>/tmux` and a `terminfo/` tree, in place of Grove's bundle. |
| `prefer_image` | `boolean` | `true` | Use the image's own tmux when it has one, otherwise Grove's bundle. |
| `session` | `string` | `"agent"` | The in container tmux session the agent runs in. Renaming it orphans a live agent. |
| `shell_session` | `string` | `"shell"` | The in container tmux session `grove shell` attaches to. |
| `term_fallback` | `string` | `"xterm-256color"` | `TERM` retried when tmux refuses the client's own. Empty disables the retry. |

## `daemon`

Where the daemon listens, and where a workspace runtime reaches it.


| Field | Type | Default | Description |
|---|---|---|---|
| `host` | `string` | `"127.0.0.1"` | Interface `grove daemon serve` binds when no `--host` is given. |
| `mailbox_url` | `string` | unset | The address a workspace runtime that is not on this host's loopback, such as a container, uses to reach this daemon. Empty means only the private mailbox socket (`GROVE_MAILBOX_SOCKET`) can serve one, and a container workspace without either runs its agent in the terminal instead of a native session. |

## `defaults`

Your saved answers for a create that does not name one. Every field is optional and an unset one falls through to its normal source.


| Field | Type | Default | Description |
|---|---|---|---|
| `agent` | `string \| null` | unset | Agent a create launches when it names none. Unset uses the first configured agent. |
| `base_ref` | `string \| null` | unset | The branch or ref a new branch starts from. |
| `branch_mode` | `string \| null` | unset | How a create picks its branch. `auto`, `new`, `existing`, `remote` or `root`. Unset is `root`: the current checkout, with no new branch or worktree. |
| `brief` | `boolean \| null` | unset | Whether a new agent receives Grove's first turn brief. Unset falls through to `brief.enabled`. |
| `model` | `string \| null` | unset | Model id sent to the saved `agent`. Another agent, or an unset model, uses the agent's own default. With no saved `agent` it applies to every agent. |
| `profiles` | `object \| null` | unset | Which profile each tool uses on each runtime. A row is `*` (any runtime), a runtime kind (`host`, `container`, `remote`) or a configured resident `runtimes.<name>` block; a column is an agent kind; a cell names an `agents[]` entry of that kind. An omitted profile uses the named remote row, runtime kind, then `*` or a same-kind saved `agent` from the higher layer, then the first roster entry of that kind. Cells merge one by one across config layers and are checked after the final roster is known. |
| `runtime` | `string \| null` | unset | `host` or `container` for a new workspace. Unset runs on the host; enabling containers makes them available without choosing them. |
| `skip_init` | `boolean \| null` | unset | Whether to skip the configured init script. |

## `federation`

Federate several Grove daemons behind one hub.


| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `boolean` | `false` | Turn federation on. A committed project config can never set this. |
| `node` | see below | | This daemon's place in a hub-and-spoke federation. |

### `node`


| Field | Type | Default | Description |
|---|---|---|---|
| `advertise_url` | `string` | unset | The URL the hub dials to forward a verb to this node. Required for a node, since nothing else tells the hub where this daemon listens. |
| `capacity` | `integer \| null` | unset | The most workspaces the hub will place on this node. Unset means no limit; the hub never overcommits a node that declares one. |
| `heartbeat_seconds` | `number` | `15.0` | How often a node tells the hub it is alive. |
| `hub_url` | `string` | unset | The hub's base URL. Required for a node. |
| `join_token_env` | `string` | unset | Name of the environment variable holding a one-time join token minted by `grove node token` on the hub. Only read until the node holds its key. |
| `join_token_ttl_seconds` | `integer` | `3600` | How long a join token minted on a hub stays usable. |
| `labels` | `object` | unset | What this node offers, as ``key: value`` (``residency: eu``, ``gpu: a100``). A create whose selector names labels lands only on a node carrying all of them. |
| `max_clock_skew_seconds` | `number` | `300.0` | How far apart two daemons' clocks may be before one refuses the other's signed bearer. A replay window, not a timeout: nothing waits on it. |
| `name` | `string` | unset | How this node is listed on the hub. Empty uses the host name. |
| `reconnect_ceiling_seconds` | `number` | `30.0` | Longest wait between a node's reconnect attempts; the backoff starts at one second and doubles up to this. |
| `request_timeout_seconds` | `number` | `30.0` | Per request timeout on every daemon-to-daemon call. |
| `role` | `string \| null` | unset | `hub` keeps the node catalog and forwards workspace verbs to their owners; `node` registers with `hub_url` and keeps full authority over its own workspaces. Required once federation is enabled. |
| `unreachable_after_seconds` | `number` | `60.0` | How long the hub waits without a heartbeat before listing a node as unreachable. Unreachable is a state; nothing is killed. |

## `fleet_digest`

How the fleet digest flags a workspace.


| Field | Type | Default | Description |
|---|---|---|---|
| `quiet_minutes` | `number` | `30.0` | A working agent with no reported phase change for this long is flagged quiet. Age alone is never called a failure. |

## `hooks`

Grove managed Claude Code status hooks, for exact push based state.


| Field | Type | Default | Description |
|---|---|---|---|
| `daemon_url` | `string` | unset | Base URL the hook posts each event to. |
| `enabled` | `boolean` | `true` | Launch Claude Code with Grove's managed hooks for exact push based status. |

## `idle`

Suspend a workspace whose agent has been idle for a while; the next message wakes it.


| Field | Type | Default | Description |
|---|---|---|---|
| `cooldown_seconds` | `integer` | `900` | How long an agent must be at rest before its workspace is suspended. |
| `hibernate_after_seconds` | `integer` | `900` | How long a suspended workspace stays suspended before a runtime that can snapshot it releases it entirely; 0 never does. |

## `init_script`

A setup script run in its own tmux window before the agent starts.


| Field | Type | Default | Description |
|---|---|---|---|
| `applies_to` | `string` | `"all"` | Which runtimes the script is for. `all`, `host` or `container`. |
| `enabled` | `boolean` | `false` | Run the init script when a workspace is created. |
| `fail_fast` | `boolean` | `true` | A non zero exit rolls back the worktree, session and branch. Off leaves the workspace in `error` for you to inspect. |
| `inline` | `string \| null` | unset | Inline shell snippet. Mutually exclusive with `path`. |
| `path` | `string \| null` | unset | Repo relative path to a script file. Mutually exclusive with `inline`. |
| `run_on_resume` | `boolean` | `false` | Run the script again when a paused workspace resumes. |
| `shell` | `string` | `"bash"` | Shell the script runs under. |
| `timeout_seconds` | `integer` | `300` | Seconds before the script is killed and counted as failed. |

## `issueops`

Turn issue comment mentions into workspace actions, and mirror progress back onto the ticket.


| Field | Type | Default | Description |
|---|---|---|---|
| `admission` | see below | | Capacity includes work already handed to consumers. |
| `agent` | `string` | `"claude"` | Which configured agent an issue ops created workspace spawns. Must name an entry in `agents`. |
| `allowed_actors` | `array<string>` | unset | Logins allowed to drive issue ops regardless of repo permission. Write access always suffices, this only adds to it. |
| `assign_bot` | `boolean` | `false` | Assign the tracker's own account to every ticket a live workspace holds, so Grove's work is findable with the tracker's assignee filter. Released when the workspace ends. |
| `deep_link_base_url` | `string` | unset | Your dashboard's base URL. Set, the comment links to `{base}/w/{id}`. Empty omits the link. |
| `enabled` | `boolean` | `false` | Publish the live status comment onto every ticket a workspace works. Off leaves command routing untouched and writes nothing to your tracker. |
| `pickup_backoff_seconds` | `number` | `300.0` | How long a tracker is skipped after it fails or rate limits a poll. The next good poll clears it. |
| `pickup_enabled` | `boolean` | `false` | Treat the assignee field as the work queue. Assign the bot to an open issue and the daemon starts a workspace for it, with no comment and no CI runner. |
| `pickup_interval_seconds` | `number` | `60.0` | How often the pickup poll asks each tracker for its assigned issues. |
| `pickup_max_active` | `integer` | `3` | How many pickup started workspaces may run at once across the host. The rest wait for the next tick. |
| `prompt_template` | `string` | `"You are handling tracker issue #{number}: \"{title}\".\n\nIssue description:\n{body}\n\nThread so far:\n{comments}\n\nHow you were engaged:\n{command_text}\n\nYour mandate:\n\n- Work autonomously through to an OPEN pull request. Read the issue, map the\n  affected components (read the nearest owning CLAUDE.md before editing),\n  implement the change, run the project's gates, open the PR, and reply on the\n  ticket saying what changed and where the PR is.\n- Where the ticket is underspecified, REPLY ON THE TICKET asking for exactly\n  what is missing, and say what you will assume if nobody answers. Do not stall\n  silently, and do not quietly guess at a requirement you could have asked\n  about.\n- Satisfy the stated goals faithfully and safely \u2014 the goals as written, not\n  the larger project you would rather do.\n- Stop and ask rather than take a risky or irreversible action: destroying\n  data, force-pushing a shared branch, or touching anything in production.\n\nIssue link: {url}\n"` | The first prompt a ticket created workspace boots on, with `{title}`, `{body}`, `{number}`, `{url}`, `{comments}` and `{command_text}` filled from the ticket. |
| `trigger` | `string` | `"@grove"` | The mention that must open a comment for Grove to act, matched case insensitively at a word boundary. |
| `update_window_seconds` | `number` | `5.0` | At most one status comment edit per workspace per window, so a burst of activity never trips the forge's rate limit. `0` flushes every change. |

### `admission`


| Field | Type | Default | Description |
|---|---|---|---|
| `max_bytes` | `integer` | `4194304` |  |
| `max_items` | `integer` | `256` |  |

## `mewbo`

Connection settings for `kind: "mewbo"` agents.


| Field | Type | Default | Description |
|---|---|---|---|
| `api_key_env` | `string` | `"MEWBO_API_KEY"` | Name of the environment variable holding the API key, never the key itself. |
| `base_url` | `string` | `"http://127.0.0.1:5125"` | Base URL of the Mewbo REST API. |
| `timeout_seconds` | `number` | `10.0` | Per request HTTP timeout. |

## `models`

How a model id READS in a picker, as distinct from what it costs.


| Field | Type | Default | Description |
|---|---|---|---|
| `catalog_ttl_seconds` | `number` | `300.0` | How long a discovered model list is reused before the agent's own CLI is asked again; 0 asks every time. |
| `display_names` | `object` | unset | Model id to the name a picker prints for it. Exact match only, because a prefix rule would silently name a model the operator never saw. An id with no entry keeps its own spelling rather than being guessed at. |

## `notifications`

Push notifications when an agent needs you. Off by default.


| Field | Type | Default | Description |
|---|---|---|---|
| `debounce_seconds` | `number` | `30.0` | Quiet window per workspace after a state fires, so one attention episode is one buzz. Questions dedupe by id instead. |
| `deep_link_base_url` | `string` | `"http://localhost:3000"` | Where a tap lands, `{base}/w/{id}`. The default is the web app's own local origin, which a phone cannot reach, so set your reachable address or empty for no link. |
| `enabled` | `boolean` | `false` | Send push notifications at all. |
| `gotify` | see below | | The Gotify push channel. |
| `on` | `array<string>` | unset | Agent states that fire a notification. `waiting`, `blocked`, `error` and `idle`. |
| `on_lifecycle` | `array<string>` | unset | Workspace events that fire a notification, such as `error`, `orphaned_detected` and `offline_detected`. Routine verbs are off by default. |
| `on_question` | `boolean` | `true` | Push when the agent asks you something, with the prompt and its options. The one trigger every harness can produce. |
| `waiting_quiet_minutes` | `number` | `15.0` | How long a session must stay settled before a `waiting` push fires, since a finished turn may still have a background command running. `0` fires as soon as every known tracker agrees nothing is left. |
| `webhook` | see below | | A generic JSON webhook channel speaking ntfy's publish format. |

### `gotify`


| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `boolean` | `false` | Deliver through Gotify. |
| `markdown` | `boolean` | `true` | Send the rich markdown body. Turn it off for a client that does not render CommonMark. |
| `priorities` | `object` | unset | Severity to Gotify priority, 0 to 10. The defaults land on Gotify's own Android thresholds, so a question buzzes and a routine pause does not. |
| `priority` | `integer` | `5` | Fallback priority for a severity with no entry in `priorities`. |
| `server_url` | `string` | unset | Your Gotify server. |
| `timeout_seconds` | `number` | `5.0` | Seconds to wait on one push before skipping it. |
| `token_env` | `string` | `"GROVE_GOTIFY_TOKEN"` | Name of the environment variable holding a Gotify application token, never the token. |

### `webhook`


| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `boolean` | `false` | Deliver through the webhook. |
| `priorities` | `object` | unset | Severity to priority on ntfy's 1 to 5 scale. Bounded, since ntfy rejects a value outside it. |
| `timeout_seconds` | `number` | `5.0` | Seconds to wait on one POST before skipping it. |
| `token_env` | `string` | unset | Name of the environment variable holding a bearer token, if the URL needs one. |
| `topic` | `string` | unset | ntfy topic, included only when set. |
| `url` | `string` | unset | The URL to POST to. |

## `panels` (a list, one entry each)

One embeddable view served by a service in this workspace's compose stack.


| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `string` | **required** | URL safe identifier, unique within `panels`. |
| `path` | `string` | `"/"` | Initial path and optional query string for the iframe. |
| `port` | `integer` | **required** | Port the service exposes on its compose network. |
| `service` | `string` | **required** | Docker Compose service name inside this workspace's stack. |
| `title` | `string` | **required** | Tab label. |

## `permission`

Grove hosted answering of Claude Code's permission prompts.


| Field | Type | Default | Description |
|---|---|---|---|
| `default` | `string` | `"deny"` | The answer while no human relay is wired. `deny` fails closed for an unattended agent, `allow` is the deliberate opt in for a bounded sandbox. |
| `enabled` | `boolean` | `false` | Let Grove answer Claude Code's permission prompts through its prompt tool. |

## `proxy`

A loopback proxy in front of the LLM gateway for wire level capture.


| Field | Type | Default | Description |
|---|---|---|---|
| `base_url_env` | `object` | unset | Per agent kind name of the variable that points that runtime at the proxy, such as `ANTHROPIC_BASE_URL` for Claude Code. |
| `enabled` | `boolean` | `false` | Route agent traffic through Grove's loopback proxy for wire level capture. Off leaves launch env and traffic untouched. |
| `host` | `string` | `"127.0.0.1"` | Loopback interface the proxy listens on. Never a routable address, since it relays provider credentials. |
| `log_bodies` | `boolean` | `false` | Capture request bodies into the telemetry event. Off by default because a body can hold prompt content. Response bodies and headers are never captured. |
| `max_body_bytes` | `integer` | `8192` | Cap on a captured request body. |
| `port` | `integer` | `8788` | Port the proxy listens on. |
| `upstreams` | `object` | unset | Per agent kind real upstream base URL. A kind absent here is not proxied. |

## `reaper`

The daemon's reconciliation of leaked host resources against workspace records.


| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `boolean` | `true` | Report what would be removed and remove nothing. ``grove vacuum --apply`` removes regardless. |
| `enabled` | `boolean` | `true` | Run the reaper in the daemon at all. |
| `interval_seconds` | `integer` | `600` | How often the daemon reconciles. Each pass is one socket listing and one ``/proc`` scan. |

## `scheduling`

Where a federation hub places this project's new workspaces.


| Field | Type | Default | Description |
|---|---|---|---|
| `require` | `object` | unset | Node labels a new workspace for this project requires, used when the create names no ``selector`` of its own. A committed project config may set it, so a repository can say "EU residency only" without every caller repeating it. |

## `slack`

Start and steer workspaces from Slack by mentioning the Grove app.


| Field | Type | Default | Description |
|---|---|---|---|
| `allowed_channels` | `array<string>` | unset | Slack conversation IDs Grove works in: channels (`C…`), private channels (`G…`) and direct messages (`D…`). Empty allows none: a mention from an allowed user gets a reply naming the conversation's ID so you can add it. `"*"` allows every conversation the app is in. |
| `allowed_users` | `array<string>` | unset | Slack member IDs (`U…`) allowed to start and steer workspaces. Empty allows nobody: a mention gets a reply naming the sender's ID so you can add it. `"*"` allows every member who can post in an allowed channel. |
| `app_token_env` | `string` | `"GROVE_SLACK_APP_TOKEN"` | Name of the variable holding the app level token (`xapp-…`) with the `connections:write` scope, never the token. |
| `bot_token_env` | `string` | `"GROVE_SLACK_BOT_TOKEN"` | Name of the variable holding the app's bot token (`xoxb-…`), never the token. |
| `channel_projects` | `object` | unset | Slack channel ID to the path of the project its mentions work in. `~` expands. A mention can pick another project named here or in `default_project` with `!project <name>`, and no other. |
| `context_messages` | `integer` | `20` | How many earlier channel messages a top level mention reads as context. A mention inside a thread always reads the whole thread instead. |
| `deep_link_base_url` | `string` | unset | Your dashboard's base URL. Set, Grove's first thread reply links to `{base}/w/{id}`. Empty omits that reply. |
| `default_project` | `string` | unset | Path of the project a mention works in when its channel has no entry in `channel_projects`. `~` expands. Empty refuses such mentions with a reply. |
| `enabled` | `boolean` | `false` | Connect to Slack when the daemon starts. |
| `env_command` | `string \| null` | unset | Host command whose stdout is parsed as dotenv, so a secret never touches disk. Re run at every use and never honored from a committed layer. |
| `env_file` | `string \| null` | unset | Dotenv file this section's variables are read from. Repo relative or absolute, `~` expanded. A configured file that is missing is an error at use. |
| `max_attachment_bytes` | `integer` | `10000000` | Largest Slack file copied into the workspace. Larger files are named in the prompt with their Slack link instead. `0` copies none. |
| `prompt_template` | `string` | `"You were started from a Slack conversation in #{channel}.\n\n{author} asked:\n{command_text}\n\nThe conversation so far, oldest first:\n{thread}\n\nSlack link: {permalink}\n\nHow this session works:\n\n- Every reply you finish is posted back into that Slack thread, so write each\n  final reply for the people reading it there: lead with the answer, keep it\n  short, and say what you changed and where.\n- People may reply in the thread while you work; their messages arrive here as\n  follow-up instructions.\n- Where the request is underspecified, ask in your reply and say what you will\n  assume if nobody answers. Do not stall silently.\n- Stop and ask rather than take a risky or irreversible action: destroying\n  data, force-pushing a shared branch, or touching anything in production.\n"` | The first prompt a Slack started workspace boots on, with `{command_text}`, `{author}`, `{channel}`, `{thread}` and `{permalink}` filled from Slack. |
| `working_reaction` | `string` | `"hourglass_flowing_sand"` | Emoji name Grove keeps on the message it is working on, removed when the agent's reply is posted. Name a custom animated emoji (such as `loading`) for a spinner. |
| `workspace` | see below | | Launch options for the workspace a Slack mention creates. |

### `workspace`


| Field | Type | Default | Description |
|---|---|---|---|
| `agent` | `string \| null` | unset | Agent the workspace runs. Must name an entry in `agents`. A mention can override it with `!agent <name>`. |
| `base_ref` | `string \| null` | unset | The branch or ref an `auto` branch starts from. Unset starts from `HEAD`. |
| `branch_mode` | `string` | `"auto"` | `auto` gives each session a new branch and worktree. `root` runs it in the project's current checkout, which sessions then share. |
| `brief` | `boolean \| null` | unset | Whether the agent receives Grove's first turn brief. |
| `effort` | `string \| null` | unset | Reasoning effort sent to the agent. Must be a level the agent lists. |
| `model` | `string \| null` | unset | Model id sent to the agent. |
| `native` | `boolean \| null` | unset | Whether the agent runs as a Grove owned native session instead of its interactive terminal. |
| `remote_runtime` | `string \| null` | unset | Which `runtimes.<name>` block runs a `remote` workspace. |
| `runtime` | `string \| null` | unset | Where the agent runs. |
| `skip_init` | `boolean \| null` | unset | Whether to skip the configured init script. |

## `speech`

Dictation into the composer and read-aloud of an answer, on every workspace.


| Field | Type | Default | Description |
|---|---|---|---|
| `api_key_env` | `string` | `"OPENAI_API_KEY"` | Name of the environment variable holding the API key, never the key itself. |
| `base_url` | `string` | `"https://api.openai.com/v1"` | OpenAI-compatible API base, for example a gateway's ``/v1``. |
| `stt_model` | `string` | `"whisper-1"` | Transcription model, passed verbatim. |
| `tts_model` | `string` | `"tts-1"` | Speech model, passed verbatim. |
| `voice` | `string` | `"alloy"` | Voice name for read-aloud, passed verbatim. |

## `telemetry`

Langfuse credentials and OpenTelemetry passthrough. The three credential fields hold variable names, never secrets, so committed config stays publishable.


| Field | Type | Default | Description |
|---|---|---|---|
| `backfill` | see below | | Consent and profile selection for exporting historical sessions. |
| `content_owner` | `object` | unset | Which side emits each runtime's prompt and response content, per agent kind, so a turn is never recorded twice or not at all. |
| `enabled` | `boolean` | `false` | Export Grove's own spans to the configured backend. |
| `env_command` | `string \| null` | unset | Host command whose stdout is parsed as dotenv, so a secret never touches disk. Re run at every use and never honored from a committed layer. |
| `env_file` | `string \| null` | unset | Dotenv file this section's variables are read from. Repo relative or absolute, `~` expanded. A configured file that is missing is an error at use. |
| `export` | see below | | Capacity and retry limits for acknowledged span export. |
| `feedback_reasons` | `array<string>` | `["Unnecessary actions", "Unnecessary testing", "Wasted time", "Ran slow commands without need", "Cluttered the context", "Missed the goal"]` | What a thumbs-down in the webapp may name, recorded as the categorical ``user-feedback-reason`` score in Langfuse. The defaults name ways a turn cost more steps, time or context than its outcome needed. Keep the Langfuse score config's categories in step when you change this list: the daemon only accepts reasons listed here, and Langfuse only renders reasons listed there. |
| `host_env` | `string` | `"LANGFUSE_HOST"` | Name of the variable holding the Langfuse host, never the URL itself. Rename it here if your secret store exports a different name. |
| `passthrough_kinds` | `array<string>` | `["claude_code", "codex"]` | Agent kinds that receive the derived telemetry environment at launch. `mewbo` runs server side and `generic` has nothing to instrument, so both are out by default. |
| `positive_feedback_reasons` | `array<string>` | `["Reached the goal directly", "No wasted steps", "Tested only what mattered", "Clear explanation", "Stayed in scope"]` | What a thumbs-up in the webapp may name, recorded as the categorical ``user-feedback-praise`` score in Langfuse. The defaults name what made a turn efficient, the mirror image of ``feedback_reasons``. The same rule applies: keep the Langfuse score config's categories identical to this list. |
| `public_key_env` | `string` | `"LANGFUSE_PUBLIC_KEY"` | Name of the variable holding the Langfuse public key. |
| `receiver` | see below | | The daemon's own OTLP endpoint for a harness's native exporter. |
| `reserved_env` | `array<string>` | `["OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_HEADERS", "OTEL_EXPORTER_OTLP_PROTOCOL", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_HEADERS", "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "OTEL_EXPORTER_OTLP_METRICS_HEADERS", "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL", "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "OTEL_EXPORTER_OTLP_LOGS_HEADERS", "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL", "OTEL_TRACES_EXPORTER", "OTEL_METRICS_EXPORTER", "OTEL_LOGS_EXPORTER"]` | OTLP exporter variables Grove owns for every agent it launches, so an agent's own exporter cannot split one session into two traces. A displaced value is logged by name. |
| `secret_key_env` | `string` | `"LANGFUSE_SECRET_KEY"` | Name of the variable holding the Langfuse secret key, never the secret itself. |

### `backfill`


| Field | Type | Default | Description |
|---|---|---|---|
| `content` | `string` | `"none"` | How much content a backfilled trace carries. `none` exports structure only. |
| `enabled` | `boolean` | `false` | Allow `grove usage backfill --telemetry` to export historical sessions at all. |
| `profiles` | `object` | unset | Provider config roots whose transcripts may be exported, such as `~/.codex`. |

### `export`


| Field | Type | Default | Description |
|---|---|---|---|
| `admission` | see below | | Capacity includes work already handed to consumers. |
| `batch_size` | `integer` | `512` |  |
| `max_retries` | `integer` | `0` |  |
| `max_retry_backoff_seconds` | `number` | `30.0` |  |
| `retry_backoff_seconds` | `number` | `1.0` |  |

### `export.admission`


| Field | Type | Default | Description |
|---|---|---|---|
| `max_bytes` | `integer` | `4194304` |  |
| `max_items` | `integer` | `256` |  |

### `receiver`


| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `boolean` | `false` | Mount the receiver on the daemon. Off serves no endpoint. |
| `path` | `string` | `"/otlp"` | Where the receiver is mounted under the daemon's address. Point a harness's `OTEL_EXPORTER_OTLP_ENDPOINT` at it. |
| `queue_capacity` | `integer` | `256` | Export batches held before the receiver starts shedding. |
| `workers` | `integer` | `2` | Threads that transform accepted batches off the daemon's event loop. |

## `tickets`

Ticket tracker integration, one block per provider.


| Field | Type | Default | Description |
|---|---|---|---|
| `env_command` | `string \| null` | unset | Host command whose stdout is parsed as dotenv, so a secret never touches disk. Re run at every use and never honored from a committed layer. |
| `env_file` | `string \| null` | unset | Dotenv file this section's variables are read from. Repo relative or absolute, `~` expanded. A configured file that is missing is an error at use. |
| `gitea` | see below | | Gitea Issues provider settings. |
| `github` | see below | | GitHub Issues provider settings. |
| `linear` | see below | | Linear provider settings. |

### `gitea`


| Field | Type | Default | Description |
|---|---|---|---|
| `base_url` | `string` | `"https://gitea.com"` | Your Gitea instance. |
| `branch_prefix` | `string` | unset | A branch prefix that marks a bare number as a Gitea ticket. |
| `enabled` | `boolean` | `false` | Turn the provider on. |
| `owner` | `string \| null` | unset | Repository owner. |
| `repo` | `string \| null` | unset | Repository name. |
| `token_env` | `string` | `"GROVE_GITEA_TOKEN"` | Name of the environment variable holding the API token, never the token. |

### `github`


| Field | Type | Default | Description |
|---|---|---|---|
| `base_url` | `string` | `"https://api.github.com"` | The GitHub API root. Change it for GitHub Enterprise. |
| `branch_prefix` | `string` | unset | A branch prefix that marks a bare number as a GitHub ticket. |
| `enabled` | `boolean` | `false` | Turn the provider on. |
| `owner` | `string \| null` | unset | Repository owner. |
| `repo` | `string \| null` | unset | Repository name. |
| `token_env` | `string` | `"GROVE_GITHUB_TOKEN"` | Name of the environment variable holding the API token, never the token. |

### `linear`


| Field | Type | Default | Description |
|---|---|---|---|
| `base_url` | `string` | `"https://api.linear.app/graphql"` | The Linear API root. |
| `enabled` | `boolean` | `false` | Turn the provider on. |
| `team_key` | `string \| null` | unset | The team key that prefixes your issue ids, such as `ENG`. |
| `token_env` | `string` | `"GROVE_LINEAR_TOKEN"` | Name of the environment variable holding the API token, never the token. |

## `tls`

Extra certificate authority roots for Grove's outbound TLS clients.


| Field | Type | Default | Description |
|---|---|---|---|
| `ca_path` | `string` | unset | A PEM bundle or OpenSSL hashed CA directory trusted alongside the operating system's roots. |

## `tmux`

tmux session naming and refresh cadences.


| Field | Type | Default | Description |
|---|---|---|---|
| `activity_threshold_seconds` | `integer` | `30` | Seconds of pane silence before a workspace flips from active to idle. Too low and a thinking agent flickers, too high and the badge lags. |
| `agent_window_name` | `string` | `"agent"` | Window the agent runs in. |
| `detached_size` | `string` | `"200x50"` | Size, as `<columns>x<rows>`, a session is created at while nothing is attached. |
| `history_limit` | `integer` | `50000` | Scrollback lines kept per pane. |
| `init_window_name` | `string` | `"init"` | Window the init script runs in. |
| `peek_history_lines` | `integer` | `500` | Scrollback lines the pane snapshot captures. |
| `peek_pane_refresh_seconds` | `number` | `0.25` | How often the peek rail refreshes the pane. |
| `peek_stats_refresh_seconds` | `number` | `3.0` | How often the peek rail refreshes git counts and diff stats. |
| `session_prefix` | `string` | `"grove-"` | Prefix on every Grove tmux session name. |
| `shell_window_name` | `string` | `"shell"` | Window that holds a plain shell. |
| `steer_settle_ms` | `integer` | `200` | Milliseconds between pasting steered text and sending Enter, so the agent's TUI treats Enter as a submit rather than part of the paste. `0` disables the wait. |

## `transcript_cache`

Process-wide retention budgets per filesystem agent adapter.


| Field | Type | Default | Description |
|---|---|---|---|
| `max_retained_bytes` | `integer` | `268435456` | Maximum estimated retained fold bytes per adapter. |
| `max_source_bytes` | `integer \| null` | `268435456` | Additional source-byte limit; null disables this secondary limit. |
| `memo_max_bytes` | `integer` | `67108864` | Maximum estimated bytes retained by derived read products per adapter. |
| `memo_max_entries` | `integer` | `512` | Maximum retained derived products, including empty answers. |

## `triggers`

Instructions the Grove daemon puts into a working agent's session on its own.


| Field | Type | Default | Description |
|---|---|---|---|
| `check_seconds` | `integer` | `60` | How often the daemon checks each running workspace. Idle waiting costs nothing; each check reads one workspace's activity row. |
| `enabled` | `boolean` | `true` | Run triggers at all. Off stops every rule, the first-turn brief included. |
| `rules` | `array<object>` | unset | The rules, merged by ``name`` across layers. Grove's own four are layer 0. |

## `ui`

TUI preferences.


| Field | Type | Default | Description |
|---|---|---|---|
| `keybindings` | `object` | unset | Key overrides for the TUI. |
| `theme` | `string` | `"auto"` | `auto`, `dark`, `light`, or the name of a theme file under `${user_config_dir}/grove/themes/`. |

## `usage`

The usage audit. Indexing, pricing, quota and insights.


| Field | Type | Default | Description |
|---|---|---|---|
| `busy_timeout_ms` | `integer` | `5000` | How long a writer waits on the SQLite lock, since the daemon, the TUI and a backfill can all write at once. `0` raises immediately. |
| `commands` | see below | | How a shell tool call's time is attributed to the command that led it. |
| `enabled` | `boolean` | `true` | Index transcripts into the usage audit at all. |
| `insights` | see below | | Thresholds for the usage audit's detectors. |
| `max_breakdown_rows` | `integer` | `50` | Rows per breakdown dimension before the long tail is dropped. The response says when it truncated. |
| `max_sessions_per_page` | `integer` | `100` | Upper bound on one page of the session table. |
| `pricing` | see below | | Model prices used to estimate cost from token counts. |
| `quota` | see below | | How subscription windows are collected per account. |
| `retention_days` | `integer \| null` | unset | Drop indexed events older than this on refresh. Unset keeps everything. |

### `commands`


| Field | Type | Default | Description |
|---|---|---|---|
| `aliases` | `object` | unset | Executable names folded onto one row. |
| `basename` | `boolean` | `true` | Fold an absolute path to its final component, so `/usr/bin/git` and `git` are one row. |
| `censored_at_ms` | `integer` | `600000` | A duration at or above this is reported as censored rather than as cost, since it marks the harness's own timeout rather than the command's length. `0` disables it. |
| `version_suffix_pattern` | `string` | `"(?<=[A-Za-z])\\d+(?:\\.\\d+)+$"` | Trailing version fragment stripped from a name, so `python3.12` counts as `python`. |

### `insights`


| Field | Type | Default | Description |
|---|---|---|---|
| `concentration_share` | `number` | `0.5` | Share of tokens or time one project, account or model must hold before concentration is named. |
| `edit_churn_edits` | `integer` | `4` | Edits to one file in a session before it is called churn. |
| `enabled` | `boolean` | `true` | Run the detectors at all. |
| `min_occurrences` | `integer` | `3` | How often a pattern must recur before it is reported. |
| `retry_window_seconds` | `integer` | `300` | Repeats of the same failing call inside this window count as one retry loop. |
| `slow_operation_ms` | `integer` | `60000` | A generation or tool call above this is a slow operation candidate. |

### `pricing`


| Field | Type | Default | Description |
|---|---|---|---|
| `aliases` | `object` | unset | Model id to the id it is priced as. Explicit only, Grove never infers one from a prefix. |
| `cache_ttl_seconds` | `number` | `3600.0` | Maximum age of a fetched price snapshot before it is refreshed. |
| `currency` | `string` | `"USD"` | ISO 4217 code the prices are quoted in. |
| `models` | `object` | unset | Manual prices by model id, exact match first then longest prefix. They override fetched entries. |
| `sources` | `array<object>` | unset | Authenticated pricing endpoints. Empty disables remote pricing, and a higher layer replaces the list. |

### `quota`


| Field | Type | Default | Description |
|---|---|---|---|
| `burn_over_percent` | `number` | `100.0` | Projected usage at reset above which a window reads `over`, on course to run out before it resets. |
| `burn_tight_percent` | `number` | `85.0` | Projected usage at reset above which a window reads `tight`. Grove extrapolates the pace so far and contacts no provider. |
| `enabled` | `boolean` | `true` | Collect quota for the selected `profiles`. |
| `gateway` | see below | | One aggregate quota endpoint holding subscriptions from several vendors. |
| `labels` | `object` | unset | Account id to display name, so two profiles are distinguishable without Grove storing an email. |
| `profiles` | `object` | unset | Subscription profiles whose quota may be collected and displayed. |
| `retry_floor_seconds` | `integer` | `120` | Wait after the first auth or rate limit failure, doubling per failure, while the last good reading keeps rendering as stale. |
| `retry_max_seconds` | `integer` | `3600` | Ceiling on that doubling. |
| `timeout_seconds` | `number` | `10.0` | Bound on each credential read and provider request. |
| `ttl_seconds` | `integer` | `900` | How long a good reading is served before the provider is asked again. |
| `window_seconds` | `object` | unset | Window label to its length, for providers that publish a reset instant but never the window's length. |

### `quota.gateway`


| Field | Type | Default | Description |
|---|---|---|---|
| `base_url` | `string` | unset | The gateway endpoint fetched once per quota refresh. |
| `token_env` | `string` | `"GROVE_QUOTA_GATEWAY_TOKEN"` | Name of the environment variable holding the gateway bearer token. |

## `worktree`

Where worktrees live and how branches are named.


| Field | Type | Default | Description |
|---|---|---|---|
| `branch_prefix` | `string` | `"grove/"` | Prefix on every auto created branch. |
| `push_token_env` | `string` | unset | Name of the environment variable holding a token that can push the workspace branch from a remote workload, never the token itself. Empty hands over none. |
| `root_template` | `string` | `"${repo}/.worktrees"` | Where worktrees live. Supports `${repo}`, `${repo_name}` and `~`. |
