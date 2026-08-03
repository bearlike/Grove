---
name: configuring-grove
description: Use when a user wants to set up or change Grove's configuration, for a host/user or for a project. Covers the global user config, the committed project config, machine-local overrides, agents, init scripts, containers, tmux and peek tuning, the TUI theme, the daemon auth, notifications, and ticket providers (Gitea, GitHub, Linear) with their credential resolution and the issue-ops status mirror and assignee queue, plus the layered cascade, the exact file locations, the schema with defaults, and a required verification step against the user's installed version.
---

# Configuring Grove

[Grove](https://github.com/bearlike/Grove) is a terminal workspace manager for AI
coding agents. Each Grove workspace is one git worktree plus one tmux session
running an agent, scoped to the repository it is launched from. This skill helps
you configure Grove correctly for a user, both at the host/user level and per
project.

Your job: find out what the user wants, decide which config layer it belongs in,
write valid JSON, and **verify it against the version of Grove they actually have
installed**. Do not guess. Grove validates strictly and rejects unknown keys, so
a wrong field is a hard error, not a silent no-op.

## What people use this for

Common requests, and how to handle them:

- **"Set this repo up so every workspace is ready to code."** Configure a project
  `init_script` (committed in `<repo>/.grove/config.json`) that installs
  dependencies and prepares the tree, for example `uv sync`, `npm ci`, or
  `make bootstrap`. It runs in its own tmux window before the agent starts.
- **"Handle my secrets properly."** Never put secret values in the committed
  project config; it is meant to be shared. Reference environment variables
  instead (the user sets them in their shell or a gitignored `.env`), keep
  machine-specific values in the gitignored project-local config
  (`<repo>/.grove/config.local.json`), and let the init script wire secrets up,
  for example `cp .env.example .env`. `agents[].env` and init scripts can read
  env vars; keep the literal secrets out of anything committed.
- **"Make test, deploy, or scratch directories for each workspace."** Put the
  `mkdir`, fixture seeding, mock-data setup, or deploy scaffolding in the
  `init_script`, so every new worktree starts in the right shape.
- **"Bring my MCP servers into each workspace."** A Grove workspace is a real git
  worktree, so any file committed at the repo root is already present inside it.
    - **Claude Code** reads project MCP servers from a committed `.mcp.json` at
      the repo root, so committing that file gives every workspace and teammate
      the same servers. Reference secret tokens via env vars there; do not
      hardcode them.
    - **Codex** reads `~/.codex/config.toml` (global, so it already applies to
      every workspace) or a project `.codex/config.toml` with
      `[mcp_servers.<name>]` tables. Commit the project file, or have the init
      script copy or symlink the user's config into the new worktree.
    - For any MCP config that is gitignored or lives only in the home directory,
      the init script is the place to copy or symlink it into the workspace.

## 1. The configuration model

Grove merges layers in this order. The last layer to set a key wins.

1. Built-in defaults (inside Grove).
2. **User config**, global, one per user.
3. **Project config**, one per repo, committed.
4. **Project-local config**, one per repo, gitignored, machine-specific.
5. **Declared per-field env vars.** A few fields name their own variable in the
   schema (`x-env-var`, e.g. `GROVE_GITEA_BASE_URL`). A convenience alias, so it
   sits below the schema-path form, which states the exact field it fills.
6. **Schema-path environment variables**, `GROVE_<SECTION>__<FIELD>`.
7. **CLI overrides**, highest priority.

After the merge, any `${VAR}` inside a string value is resolved against the same
environment, then the result is validated exactly like a literal. An unset or
empty `${VAR}` is a hard error naming the variable and the dotted path — writing
the reference *is* the opt-in. `$${VAR}` escapes it; `${repo}` / `${repo_name}`
are reserved for per-repo expansion.

File locations:

| Layer | Path |
|---|---|
| User | `${user_config_dir}/grove/config.json`. Linux: `~/.config/grove/config.json`. macOS: `~/Library/Application Support/grove/config.json`. Windows: `%APPDATA%\grove\config.json`. |
| Project | `<repo>/.grove/config.json` (commit this) |
| Project-local | `<repo>/.grove/config.local.json` (gitignore this) |

Run `grove debug` on the user's machine to print the exact resolved paths.

## 2. The schema is the source of truth

Every config file is one JSON object validated against a single Pydantic model,
`GroveConfig`, configured with `extra = "forbid"`. An unknown or misspelled key
is a hard error, never silently ignored.

Two authoritative sources. Prefer them over this document if they ever disagree:

- The user's **installed** version: `grove config schema --stdout` prints the JSON
  Schema their binary accepts. This is what you validate against.
- The **latest published** schema:
  <https://bearlike.github.io/Grove/latest/grove.schema.json>, rendered as a
  field-by-field reference at
  <https://bearlike.github.io/Grove/latest/configure-reference/>.

Add a `"$schema"` key to any file you write so the user's editor autocompletes
and validates it. `grove config init` also writes a local schema to
`${user_config_dir}/grove/config.schema.json`, which a project file can reference
with a relative path.

## 3. The sections

Defaults are shown. Every key is optional; omit a key to keep its default. The
top-level keys the installed version accepts are whatever
`grove config schema --stdout` lists — read it rather than trusting this list to
be complete.

Two top-level scalars have no section of their own:

- `builtin_agents` (bool, default `true`). `false` filters the roster down to
  the agents your own layers declare, hiding the built-ins below.
- `projects` (list of strings, default `[]`). Repo (or subdirectory) paths that
  appear on the cross-project surfaces even with zero workspaces. A user-level
  concern; `grove config add-project` edits it for you.

### `worktree`
- `root_template` (string, default `"${repo}/.worktrees"`). Parent directory for
  worktrees. Supports `${repo}`, `${repo_name}`, and `~`, expanded per repo at
  use time, not when the file is saved.
- `branch_prefix` (string, default `"grove/"`). Prepended to auto-created branch
  names.

### `agents` (list of objects)
Each entry is one selectable agent in the create-workspace picker:
- `name` (string, required). Identifier, and the **merge key** across layers.
- `command` (string, required). Shell command sent into the agent's tmux window,
  for example `"claude"`, `"aider"`, `"codex"`, or `"$SHELL"`.
- `kind` (`"claude_code" | "codex" | "generic" | "mewbo"`, default `"generic"`).
  Which adapter introspects this agent's session for the **Activity Dashboard**.
  `claude_code` reads Claude Code transcripts (live status, human-turn / reply
  counts, the session's self-generated title) and lets Grove mint a
  deterministic `--session-id` at launch; `codex` reads the same signals from
  Codex CLI rollout files, but its session is adopted by discovery rather than
  minted; `mewbo` introspects a remote orchestrator over REST; `generic`
  launches the command but tracks nothing.
- `env` (object of string to string, default `{}`). Extra env vars exported in
  that window.
- `env_unset` (list of strings, default `[]`). Env vars *cleared* in that window
  before `env` is applied, so an ambient value the daemon happened to carry (a
  profile selector like `CLAUDE_CONFIG_DIR`) cannot leak into the agent. Unset
  runs first, so a name in both ends up exported.
- `models` (list of strings, default `[]`). Model ids to OFFER in the create
  form — a display override, never an allowlist (any id given at create is
  forwarded verbatim). Empty falls through to the adapter's own discovery.
- `tools_offline` (bool, default `false`). Launch with network-facing tools
  disallowed (Claude Code drops `WebFetch`/`WebSearch`, Codex flips its sandbox
  to networking-off). A no-op for `generic`/`mewbo`.
- `description` (string, default `""`).

Built-in defaults: `claude` (command `claude`, `kind` `claude_code`), `codex`
(command `codex`, `kind` `codex`) and `shell` (command `$SHELL`, `kind`
`generic`).

### `init_script`
Optional setup run in its own tmux window before the agent starts:
- `enabled` (bool, default `false`)
- `shell` (`"bash" | "sh" | "zsh"`, default `"bash"`)
- `inline` (string or null). Inline snippet. Mutually exclusive with `path`.
- `path` (string or null). Repo-relative script file. Mutually exclusive with
  `inline`.
- `timeout_seconds` (int, default `300`)
- `fail_fast` (bool, default `true`). A non-zero exit rolls back the worktree,
  session, and branch.
- `run_on_resume` (bool, default `false`)
- `applies_to` (`"all" | "host" | "container"`, default `"all"`). Scopes the
  script to host or container workspaces, matched against what the workspace
  actually became (a fallback-to-host workspace counts as `host`). Use this
  when a project's devcontainer lifecycle hooks already cover setup that
  would otherwise run twice. Excluded workspaces report the init step as
  skipped rather than failing.

### `hooks`
Grove-managed Claude Code **status hooks** for the Activity Dashboard:
- `enabled` (bool, default `true`). Grove launches `claude_code` agents with
  `--settings <grove-managed-file>` so a lightweight hook pushes exact
  lifecycle status (`WORKING` / `WAITING` / `BLOCKED` / `IDLE`) into a per-session
  sidecar the dashboard prefers over polled status — giving precise
  *blocked-on-a-permission-prompt* that polling can't see, plus it surfaces
  sessions you started by hand in a Grove worktree. Grove writes only its own
  settings file and never touches your `.claude/settings.json`, so turning this
  to `false` fully uninstalls.
- `daemon_url` (string, default `""`). Base URL the hook POSTs each event to.
  Empty keeps the built-in loopback address; set it only for a runtime that
  reaches the daemon at a different host.

### `container`
Runs the agent inside a container instead of directly on the host. `runtime`
(`"host"` | `"container"`, chosen per workspace at create — `--runtime` /
the TUI create Select / the webapp composer's runtime picker / the MCP
`runtime` param) cascades to this section's `enabled` when omitted:
**What the container runs is the project's own `.devcontainer/`, not this
section.** Grove provisions through the `@devcontainers/cli`, so the image,
Dockerfile, features, mounts and lifecycle hooks all live in
`devcontainer.json`; this section configures how Grove *drives* that.

- `enabled` (bool, default `true`). The cascade default for `runtime` when a
  create doesn't pass one explicitly. `false` makes new workspaces host ones.
- `default_config` (string, default `""`). Path to the `devcontainer.json`
  Grove passes for a repo with no `.devcontainer/` of its own — empty uses the
  self-contained config packaged with Grove. Graduate a project off the
  default with `grove init devcontainer`, which scaffolds
  `.devcontainer/devcontainer.json` from that same packaged config.
- `up_timeout_seconds` (float, default `900.0`). Wall-clock bound on one
  `devcontainer up`. A cold build that pulls a base image and installs features
  is legitimately minutes long.
- `docker_bin` (string, default `"docker"`). The container CLI binary/path.
  Not the runtime-swap seam — it must be docker-compatible.
- `shell` (list of strings, default `["bash", "sh"]`). Shells `grove shell` and
  the attach shell window try, in order, resolved *inside* the container. None
  present → the pane says so and exits non-zero; there is no baked-in fallback.
- `env_file` / `env_command` (string or null, mutually exclusive). A dotenv file
  or a command printing dotenv to stdout, resolved twice per start — once for
  the project's lifecycle hooks (`--secrets-file`), once for the agent's launch
  env. A missing file or a failing command is fatal at create. A **committed**
  layer may never set `env_command` (that would be RCE on clone) and its
  `env_file` must stay inside the repo.
- `agent_config.share` (`"full" | "projects" | "isolated"`, default `"full"`).
  How much of the host agent configuration the container shares by bind mount —
  `full` means native sign-in, skills and settings work in-container;
  `isolated` restores the credential boundary at the cost of a second sign-in.
  A committed layer may only *tighten* this, never raise it.
- `agent_config.trust` (bool, default `true`). Seed the agent's config so the
  workspace folder and its committed `.mcp.json` servers count as already
  approved. `false` restores the interactive trust prompt — which nobody is
  there to answer, so an unattended workspace simply never starts.
- `egress.mode` (`"allowlist" | "open" | "deny"`, default `"allowlist"`).
  `allowlist` applies a firewall **derived** from the agent's own endpoints, the
  package registries, the repo's git remotes and the Grove plane, so normal work
  needs no config. Anything but `open` fails closed: a firewall that verifiably
  did not apply fails the container start.
- `egress.allow` (list of strings, default `[]`). Extra hostnames or CIDRs,
  purely additive. `agent_plane`, `package_plane`, `grove_plane` and
  `range_sources` override the derived sets themselves — read them from the
  schema before touching them.
- `resources.memory` / `.cpus` (strings, default `""`) and `.pids` (int,
  default `0`). Per-container caps applied after `up`. Empty/zero = uncapped.
- `tmux.enabled` (bool, default `true`). Run the agent under a tmux **inside**
  the container, which is what makes it persistent and re-attachable; `false`
  restores a bare exec whose terminal dies with the client. `prefer_image`
  (default `true`) uses the image's own tmux when it has one, else Grove's
  bundle; `payload` (default `""`) points at an operator-supplied bundle.
  `session` / `shell_session` (`"agent"` / `"shell"`) are **reattach
  identities** — renaming one orphans a live session. `term_fallback`
  (default `"xterm-256color"`) is the `TERM` an attach retries with when tmux
  refuses the client's own; empty disables the retry.
- `decor.enabled` (bool, default `true`). Mount Grove's read-only tmux config +
  statusline bundle, so a person attached to a container can tell they are in
  one and the status bar reports the *container's* cgroup limits rather than the
  host's. `statusline` and `tmux_conf` (both `true`) take one without the other;
  `payload` (default `""`) replaces the bundle wholesale.

`runtime` is a create-time-only fact: once a workspace exists, changing
this section never moves it between host and container. If a workspace
wanted a container and the runtime was unavailable at create, it falls
back to host and every surface renders a persistent warning naming the
reason; fix the runtime and run `grove respawn` to promote it, branch and
worktree preserved. See [Container Workspaces](../../../docs/features-containers.md)
for the full model.

### `tickets` and `issueops`

Two sections, one story: `tickets` is **who Grove is** on a tracker, `issueops`
is **what it does there**. Read the field lists from
`grove config schema --stdout`; what follows is only what a schema cannot tell
you.

`tickets` has one submodel per provider — `gitea`, `github`, `linear` — each
independently `enabled`, plus a shared credential source. **All three are off
until a repo opts in.** A provider needs its `enabled`, where its instance lives,
which repo it speaks for, and the **NAME** of an env var holding its token. A
token value never appears in any config layer.

**The token is resolved at the moment of use, in this order**, which is the part
worth understanding because it decides where you put things:

1. `tickets.env_command`'s stdout or `tickets.env_file`'s contents, parsed as
   dotenv, looked up by that provider's `token_env`;
2. the consuming process's own environment;
3. nothing — the provider reads unconfigured and every call raises rather than
   sending an unauthenticated request.

One source serves all three providers because it is keyed by env-var NAME, so a
single `env_command` pointed at a secret manager can feed Gitea, GitHub and
Linear at once. Resolving per lookup is deliberate: a credential that appears
*after* the daemon started — written by an init script, refreshed by a login — is
picked up without a restart.

**`issueops.enabled` gates only the OUTBOUND status mirror**, the live sticky
comment Grove maintains on each ticket a workspace names. Inbound work arrives by
two independent roads, each with its own opt-in:

- **Comment commands** (`@grove <verb>`) have no flag of their own — the opt-in
  is a CI workflow that forwards the event **plus** an enabled provider for that
  repo.
- **The assignee queue** (`issueops.pickup_enabled`, off by default) is the
  daemon polling each tracker for open issues assigned to the provider's own
  account and starting a workspace for each. It needs no CI runner, which is
  what makes inbound automation reachable where you cannot host one.
  `pickup_max_active` (default `3`) caps how many pickup-started workspaces run
  at once, host-wide; the rest defer to the next tick. Pair it with
  `assign_bot` (off by default), which assigns the bot to every ticket a live
  workspace holds so Grove's work is findable with the tracker's own filter.

So a deployment can mirror without routing, route without mirroring, or run both.

#### Traps

- **A daemon serves many repos, so put per-repo identity in a per-repo layer.**
  `owner`/`repo` differ per repository; the credential source usually does not.
- **Never commit the instance URL of a private tracker.** The project config is
  shared; a self-hosted endpoint belongs in the project-local layer or in an env
  var. Only Gitea declares its own (`GROVE_GITEA_BASE_URL`) — the schema-path
  form (`GROVE_TICKETS__GITHUB__BASE_URL`) always works.
- **The token's identity is the identity every Grove comment is authored as.**
  Point it at a bot account, not a human's personal token. Forges set comment
  authorship at creation and never change it on edit, so a comment first written
  under the wrong token stays wrong for its whole life — fixing it means deleting
  the comment so the publisher recreates it.
- **Attaching a ticket performs no network call**, by design, so `grove tickets
  attach` succeeding proves the ref parsed and a provider claimed it — never that
  the token works. And publish failures are swallowed so a tracker outage cannot
  break the activity poll. A misconfigured provider therefore looks like
  *silence*, not an error. Verify deliberately; see step 4 below.
- **Changing which source a token comes from may need `grove daemon` restarted**,
  even though the token's *value* refreshes live. If a config edit reads correct
  in `grove config show` and the behaviour has not moved, restart before
  debugging further.

### `tmux`
- `session_prefix` (default `"grove-"`), `init_window_name` (`"init"`),
  `agent_window_name` (`"agent"`), `shell_window_name` (`"shell"`),
  `history_limit` (int, `50000`).
- `peek_pane_refresh_seconds` (float, `0.25`). Fast pane-mirror tick for the peek
  rail.
- `peek_stats_refresh_seconds` (float, `3.0`). Slower git-stats tick.
- `peek_history_lines` (int, minimum `1`, default `500`). How many lines of tmux
  scrollback a pane snapshot captures. Each client tails or scrolls within it.
- `activity_threshold_seconds` (int, minimum `1`, default `30`). Seconds of pane
  quiet before a workspace flips from Active to Idle. (Was `5`, which read every
  thinking/long-tool agent as Idle.)
- `steer_settle_ms` (int, minimum `0`, default `200`). Delay between typing
  steered text and sending the submitting Enter, so a TUI's bracketed-paste
  window closes first and the Enter submits instead of reading as a newline.
  `0` disables it.

### `ui`
- `theme` (string, default `"auto"`). `auto`, `dark`, or `light`, or a custom name
  registered from `${user_config_dir}/grove/themes/*.toml`. UI only; the engine
  ignores it, so it belongs in the **user** config, not a shared project config.
- `keybindings` (object, default `{}`).

### `auth`
Daemon HTTP auth. Only relevant when running `grove daemon serve` or the web
dashboard. Leave the defaults unless there is a clear reason:
- `enabled` (bool, `true`). Keep `true` in production.
- `session_ttl_seconds` (int, minimum `60`, default `2592000`, which is 30 days,
  sliding on each use).
- `pairing_ttl_seconds` (int, minimum `30`, default `300`).
- `pair_init_per_minute` (int, minimum `1`, default `5`).
- `pair_poll_per_minute` (int, minimum `1`, default `60`).

### `brief`
- `enabled` (bool, default `true`). Hand a new workspace's agent a
  one-paragraph brief on its first turn, pointing it at Grove's
  `working-in-grove` skill. Resolved at create and persisted, so flipping it
  never re-decides for a workspace that already exists; `grove create --no-brief`
  overrides per workspace.

### `notifications`
Push on workspace edges, off by default. `enabled` (bool, `false`) is the master
switch; three independent triggers fan out to every enabled channel — `on` (a
debounced rising edge into `waiting`/`blocked`/`error`), `on_question` (bool,
`true`, deduped by question id rather than debounced) and `on_lifecycle` (the
workspace broke, was orphaned, or went offline). `debounce_seconds` (float,
`30.0`) is the per-workspace quiet window; questions are exempt. Channels are
`gotify` and `webhook` submodels, each off until enabled and each holding a
`token_env` NAME, never a token.

**`deep_link_base_url` defaults to `http://localhost:3000`, which is the
feature's quietest failure.** A push lands on your phone, you tap it, the phone
resolves `localhost` to itself and nothing errors. Point it at an origin the
device can actually reach, or set it empty to render no link.

### `telemetry`
LangFuse / OpenTelemetry passthrough, off by default. `host_env`,
`public_key_env` and `secret_key_env` are env-var NAMES (never literals);
`passthrough_kinds` (default `["claude_code", "codex"]`) picks which agents get
the derived env at launch. The exporter dependencies live in the `telemetry`
extra, which `.[all]` does **not** include.

### `proxy`
Loopback LLM-gateway passthrough for wire-truth capture, off by default. Binds
`host`/`port` (`127.0.0.1:8788`); `upstreams` and `base_url_env` are per-agent-kind
maps, and a kind absent from them is not proxied. `log_bodies` (default `false`)
opts into capturing request bodies — prompt content — capped by
`max_body_bytes`; response bodies and headers are never captured.

### `channels` and `permission`
Two `claude_code` launch integrations, both off by default and both no-ops when
disabled. `channels.enabled` registers Grove's channel MCP server so the daemon
can push a message a *running* session acts on; `allowed_senders` empty means
allow all, so populate it to restrict. `permission.enabled` registers Grove's
`--permission-prompt-tool` so an unattended session answers permission prompts
instead of blocking on a TTY; `permission.default` (`"deny"`) is fail-closed —
enabling the feature cannot widen what an agent may do without an explicit
`"allow"`.

### `mewbo`
Connection settings for the Mewbo orchestrator, read by `kind: "mewbo"` agents:
`base_url`, `api_key_env` (a NAME, never the key) and `timeout_seconds`.

## 4. Which layer for what

- **Preferences that follow the user everywhere** (theme, your own extra agents):
  user config.
- **Team standards for one repo** (agent roster, worktree layout, the init script
  that prepares the project): project config. Commit it.
- **One machine's quirk for a repo** (a local path, a personal override of a
  shared value): project-local config. Gitignore it.
- **One-off or scripted**: an env var. The format is
  `GROVE_<SECTION>__<FIELD>=value`, a double underscore between nesting levels and
  lowercase field names, for example `GROVE_UI__THEME=dark` or
  `GROVE_TMUX__ACTIVITY_THRESHOLD_SECONDS=10`.

## 5. Merge rules to respect

- Deep merge, last layer wins per key.
- **`agents` merges by `name`, field by field.** An entry whose `name` matches an
  existing one **refines it** (your fields win, the base entry's other fields are
  kept); a new `name` is **appended**. So overriding just the `claude` agent's
  `command` keeps its `kind: "claude_code"` — you do not have to restate it, and
  the dashboard keeps tracking. Every other list replaces wholesale.
- **Mutually exclusive fields resolve across layers before the merge, not
  field by field.** `init_script`'s `inline`/`path` and the `env_file`/
  `env_command` pairs are alternatives: the highest layer that *mentions* either
  member wins the whole group and the lower layers' members are dropped. An
  explicit `"path": null` therefore deliberately clears a lower layer's script.
- Unknown keys in a config **file** raise. Validate before you tell the user it
  is done. The `GROVE_*` environment is the one carve-out — the namespace is
  shared, so a var whose first segment is not a real section is ignored (it only
  warns when it carries the `__` separator).

## 6. Examples

User config (`~/.config/grove/config.json`): a personal theme and an extra agent.

```json
{
  "$schema": "./config.schema.json",
  "ui": { "theme": "dark" },
  "agents": [
    { "name": "aider", "command": "aider", "description": "Aider pair-programmer" }
  ]
}
```

Project config (`<repo>/.grove/config.json`): a shared standard, committed.

```json
{
  "worktree": { "root_template": "${repo}/.worktrees", "branch_prefix": "feat/" },
  "agents": [
    { "name": "claude", "command": "claude --model sonnet" }
  ],
  "init_script": {
    "enabled": true,
    "shell": "bash",
    "inline": "uv sync && cp .env.example .env",
    "timeout_seconds": 600
  }
}
```

## 7. Verify before you finish (required)

Grove never silently ignores bad config, so prove that it loaded.

1. `grove config schema --stdout`. Confirm every key and type you wrote exists in
   the **installed** version's schema. If a field you want is missing, the user's
   Grove is older than this skill. Have them upgrade (`uv tool upgrade grove`, or
   `pipx upgrade grove`) before relying on it.
2. Write the file, then `grove config show`. This prints the merged effective
   config. Confirm your values appear in the right place. A `ConfigError` here
   means a typo or an unknown key; fix it.
3. `grove debug`. Confirm `config_loaded: true` and check which paths were read.

Report back exactly which file you changed, which layer it is, and what
`grove config show` now reports.

## Related skills

`using-grove` drives a fleet from outside and `working-in-grove` is for the agent
inside a workspace — both are runtime concerns rather than configuration.
`reinstalling-grove` covers an update that did not take.

## Quick command reference

- `grove config init [-f]`. Scaffold `<repo>/.grove/config.json` and write the
  user schema.
- `grove config show`. Print the merged effective config as JSON.
- `grove config schema [--stdout]`. Write or print the JSON Schema for the
  installed version.
- `grove debug`. Print resolved paths and whether config loaded.
