---
name: configuring-grove
description: Use when a user wants to set up or change Grove's configuration, for a host/user or for a project. Covers the global user config, the committed project config, machine-local overrides, agents, init scripts, tmux and peek tuning, the TUI theme, and the daemon auth, plus the six-layer cascade, the exact file locations, the full schema with defaults, and a required verification step against the user's installed version.
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

Grove merges up to six layers. The last layer to set a key wins.

1. Built-in defaults (inside Grove).
2. **User config**, global, one per user.
3. **Project config**, one per repo, committed.
4. **Project-local config**, one per repo, gitignored, machine-specific.
5. **Environment variables**, `GROVE_*`.
6. **CLI overrides**, highest priority.

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
  <https://bearlike.github.io/Grove/latest/grove.schema.json>

Add a `"$schema"` key to any file you write so the user's editor autocompletes
and validates it. `grove config init` also writes a local schema to
`${user_config_dir}/grove/config.schema.json`, which a project file can reference
with a relative path.

## 3. The sections

Defaults are shown. Every key is optional; omit a key to keep its default.

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
- `env` (object of string to string, default `{}`). Extra env vars exported in
  that window.
- `description` (string, default `""`).

Built-in defaults: `claude` (command `claude`) and `shell` (command `$SHELL`).

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

### `tmux`
- `session_prefix` (default `"grove-"`), `init_window_name` (`"init"`),
  `agent_window_name` (`"agent"`), `shell_window_name` (`"shell"`),
  `history_limit` (int, `50000`).
- `peek_pane_refresh_seconds` (float, `0.25`). Fast pane-mirror tick for the peek
  rail.
- `peek_stats_refresh_seconds` (float, `3.0`). Slower git-stats tick.
- `activity_threshold_seconds` (int, minimum `1`, default `5`). Seconds of pane
  quiet before a workspace flips from Active to Idle.

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
- **`agents` merges by `name`.** An entry whose `name` matches an existing one
  **replaces** it; a new `name` is **appended**. So a project can override the
  shared `claude` agent's command without redefining the others. Every other list
  replaces wholesale.
- Unknown keys raise. Validate before you tell the user it is done.

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

## Quick command reference

- `grove config init [-f]`. Scaffold `<repo>/.grove/config.json` and write the
  user schema.
- `grove config show`. Print the merged effective config as JSON.
- `grove config schema [--stdout]`. Write or print the JSON Schema for the
  installed version.
- `grove debug`. Print resolved paths and whether config loaded.
