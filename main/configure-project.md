# Project setup

Grove keeps configuration close to the code it serves. Every repository
that uses Grove carries a `.grove/` directory with one or two JSON files
that pin worktree layout, agent registry, init scripts, tmux behavior,
and UI preferences.

## Why per-repo config

A single global config can serve multiple repositories, but it cannot
*pin* anything for the team. Project-scoped configuration solves that.
The `.grove/config.json` file lives in the repo, gets reviewed in pull
requests, and ships the same defaults to every contributor. Personal
overrides land in `.grove/config.local.json`, which is gitignored.

## Cascade in one paragraph

Grove resolves configuration from six layers, applied in order with
last-wins semantics: Pydantic defaults, user JSON, project JSON,
project-local JSON, environment variables, then CLI overrides. Most
lists *replace* wholesale across layers. The `agents` list is the
exception and merges by `name`, so a project can override one entry
without redefining all of them. See [configuration
cascade](features-cascade.md) for the full narrative.

## Three files you may touch

| Layer | Path | Purpose |
|---|---|---|
| **Project** | `<repo>/.grove/config.json` | Team baseline. Commit this. |
| **Project-local** | `<repo>/.grove/config.local.json` | Per-machine overrides. Gitignored. |
| **User** | `${user_config_dir}/grove/config.json` | Per-user defaults applied to every repo. |

`${user_config_dir}` follows `platformdirs`: XDG on Linux, `%APPDATA%` on
Windows, `~/Library/Application Support` on macOS.

## Worked example

```json
{
  "$schema": "../../../home/me/.config/grove/config.schema.json",
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

`${repo}` and `${repo_name}` placeholders inside string values expand at
*consume* time, not validate time, so the same global config can serve
every repo without re-validation.

## IDE autocomplete via JSON Schema

Grove can write a JSON Schema for the config model. Editors then
autocomplete keys and surface enum values inline. `grove config init`
writes the schema. `grove config schema` rewrites it on demand. Reference
the schema from your project config with `"$schema"`:

```bash
grove config schema     # writes ${user_config_dir}/grove/config.schema.json
```

```json
{
  "$schema": "../../../home/me/.config/grove/config.schema.json",
  "worktree": { "branch_prefix": "feat/" }
}
```

The path is yours to set, relative or absolute. Most IDEs (VS Code,
JetBrains, Helix, Neovim with `coc.nvim` or `lspconfig`) pick it up
automatically.

## See also

- [Agents](configure-agents.md): adding a new agent, the merge-by-name rule.
- [Init scripts](configure-init-scripts.md): automating per-workspace setup.
- [Configuration reference](configure-reference.md): every field, auto-generated from the model.
- [Configuration cascade](features-cascade.md): the philosophy behind the six layers.
