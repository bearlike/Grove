# Project setup

## Config the whole team shares

A Grove repository carries a `.grove/` directory of one or two JSON files:
worktree layout, agents, init scripts, tmux behavior, UI preferences.

## Why per-repo config

A global config pins nothing team-wide. A committed, PR-reviewed
`.grove/config.json` ships one default set to every contributor.

## Cascade in one paragraph

Layers resolve last wins, built-in defaults to CLI flags. Lists replace
wholesale, `agents` merges by `name`. See [Configuration
cascade](features-cascade.md).

## Three files you may touch

| Layer | Path | Purpose |
|---|---|---|
| **Project** | `<repo>/.grove/config.json` | Team baseline. Commit this. |
| **Project-local** | `<repo>/.grove/config.local.json` | Per-machine overrides. Gitignored. |
| **User** | `${user_config_dir}/grove/config.json` | Per-user defaults for every repo. |

`${user_config_dir}` follows `platformdirs`: XDG on Linux, `%APPDATA%` on
Windows, `~/Library/Application Support` on macOS.

## Known projects (keep empty repos visible)

Grove learns which projects exist from its workspaces. A zero-workspace
repo drops out of the webapp new-workspace dialog and TUI switcher, so
declare it in user config.

```json title="${user_config_dir}/grove/config.json"
{
  "projects": [
    "~/code/my-app",
    "~/code/another-project"
  ]
}
```

- `projects` takes repo-root paths, `~` expanded, unioned with tracked
  repos, deduped by canonical path.
- A missing or non-git path is ignored silently, never breaking config load.

## Worked example

```json title=".grove/config.json"
{
  "$schema": "~/.config/grove/config.schema.json",
  "worktree": {
    "root_template": "${repo}/.worktrees",
    "branch_prefix": "feat/"
  },
  "agents": [
    { "name": "claude", "command": "claude",                "description": "Anthropic Claude Code" },
    { "name": "aider",  "command": "aider --model sonnet",  "description": "Aider with Sonnet" },
    { "name": "shell",  "command": "$SHELL",                "description": "Plain shell" }
  ],
  "init_script": {
    "enabled": true,
    "shell": "bash",
    "inline": "uv sync && cp ../.envrc .envrc",
    "timeout_seconds": 120,
    "fail_fast": true,
    "run_on_resume": false
  },
  "tmux": {
    "session_prefix": "grove-",
    "agent_window_name": "agent",
    "shell_window_name": "shell",
    "history_limit": 50000
  },
  "ui": {
    "theme": "auto"
  }
}
```

`${repo}` and `${repo_name}` inside string values expand at *consume* time,
not validate time: one config serves every repo.

## IDE autocomplete via JSON Schema

Grove writes a JSON Schema so editors autocomplete keys and enum values
inline. `grove config init` writes it. `grove config schema` rewrites on
demand.

```bash
grove config schema     # writes ${user_config_dir}/grove/config.schema.json
```

Point `"$schema"` at it by any path: VS Code, JetBrains, Helix and Neovim
(`coc.nvim` or `lspconfig`) pick it up automatically:

```json
{
  "$schema": "~/.config/grove/config.schema.json",
  "worktree": { "branch_prefix": "feat/" }
}
```

## Per-repo cascade applies everywhere

The daemon, TUI and web dashboard each resolve a repo's cascade
independently, user through project to project-local.

- A project-scoped agent appears in that repo's create picker everywhere.
- A project-enabled `init_script` runs for every workspace there, from
  CLI, TUI or MCP.
- No daemon restart needed: the next create picks up the cascade.

## See also

- [Agents](configure-agents.md): adding an agent, the merge-by-name rule.
- [Init scripts](configure-init-scripts.md): automating per-workspace setup.
- [Configuration reference](configure-reference.md): every field, auto-generated.
- [Configuration cascade](features-cascade.md): the layers' philosophy.
