# Project setup

## Share a starting point for every workspace

A Grove repository carries a `.grove/` directory of one or two JSON files, and a committed, PR reviewed `.grove/config.json` ships one default set to every contributor.

## Three files you may touch

| Layer | Path | Purpose |
|---|---|---|
| **Project** | `<repo>/.grove/config.json` | Team baseline. Commit this. |
| **Project-local** | `<repo>/.grove/config.local.json` | Per-machine overrides. Gitignored. |
| **User** | `${user_config_dir}/grove/config.json` | Per-user defaults for every repo. |

- Layers resolve last wins and `agents` merges by `name`. See [Configuration cascade](features-cascade.md).
- Every surface resolves a repo's cascade on its own, so a project scoped agent or init script applies everywhere with no daemon restart.
- `${user_config_dir}` follows `platformdirs`. `${repo}` and `${repo_name}` expand at consume time, so one config serves every repo.

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

## New-workspace defaults

`defaults` pre-fills every create surface. The new workspace modal, the web composer, and a bare `grove create`.

| Field | Meaning |
|---|---|
| `agent` | Agent selected when the form opens. |
| `runtime` | `host` or `container` for the new workspace. |
| `brief` | Whether the agent receives Grove's first-turn brief. |
| `model` | Model id sent to the selected agent. |
| `branch_mode` | How Grove selects the branch source: `auto`, `new`, `existing`, `remote`, or `root`. |
| `base_ref` | The starting branch or ref offered by the form. |
| `skip_init` | Whether to skip the configured init script. |

- Every field is optional, and an omitted one falls back to its normal source. Title is not a default, since it describes one task.
- **Save as defaults** in the new workspace modal records the form's answers to **User**, **Project** or **Project (local)** scope.
- For `defaults` your user preference wins over a repository you cloned. See [the defaults exception in the cascade](features-cascade.md#workspace-defaults-put-the-user-first).

```json title=".grove/config.local.json"
{
  "defaults": {
    "agent": "claude",
    "runtime": "container",
    "brief": true,
    "model": "sonnet",
    "branch_mode": "auto",
    "base_ref": "main",
    "skip_init": false
  }
}
```

## Set the agent's starting directory

A monorepo can name the repo relative directories an agent may start in. Only the agent session moves. The worktree, branch and init script stay at the repository root.

```json title=".grove/config.json"
{
  "agent_cwds": {
    "entries": {
      "web": "apps/web",
      "api": "services/api"
    },
    "default": "web"
  }
}
```

Labels make the choice readable in every create surface, paths stay relative and inside the repository, and `grove create --cwd services/api` overrides the default for one workspace.

## Tailor the first-turn brief

`brief.instructions` adds your team's own short instruction after Grove's first turn brief.

```json title=".grove/config.json"
{
  "brief": {
    "instructions": "Run the focused checks before asking for review.",
    "self_naming": true
  }
}
```

The nearest cascade layer supplies the text. With `self_naming` on, Grove asks an agent in a workspace with no description to give it a title and description.

## Known projects

Grove learns which projects exist from its workspaces, so a repo with zero workspaces drops out of the create pickers. Declare it in user config to keep it visible.

```json title="${user_config_dir}/grove/config.json"
{
  "projects": [
    "~/code/my-app",
    "~/code/another-project"
  ]
}
```

Paths take `~`, union with tracked repos, and a missing one is ignored silently.

## Trust a private deployment CA

If Grove must call a private forge, gateway or collector signed by a root CA the operating system does not know, name a PEM bundle or OpenSSL hashed CA directory in user config.

```json title="${user_config_dir}/grove/config.json"
{
  "tls": {
    "ca_path": "~/.config/grove/company-ca.pem"
  }
}
```

`GROVE_TLS_CA_PATH` supplies the same value for a deployment. It adds trust to the operating system roots, never disables verification, and a missing path fails loudly at startup.

## IDE autocomplete via JSON Schema

`grove config init` writes a JSON Schema and `grove config schema` rewrites it on demand, so editors autocomplete keys and enum values.

```bash
grove config schema     # writes ${user_config_dir}/grove/config.schema.json
```

Point `"$schema"` at it by any path and VS Code, JetBrains, Helix and Neovim pick it up.

## See also

- [Agents](configure-agents.md): adding an agent, the merge-by-name rule.
- [Init scripts](configure-init-scripts.md): automating per-workspace setup.
- [Configuration reference](configure-reference.md): every field, auto-generated.
- [Configuration cascade](features-cascade.md): the layers' philosophy.
