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

## Set the agent's starting directory

A monorepo can name the repo-relative directories where an agent may start.
The worktree, branch, and init script still stay at the repository root. Only
the agent session moves. Labels make the choice readable in create surfaces,
and `default` applies when a create names no directory.

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

Paths must remain inside the repository. They are deliberately relative, so a
committed project config describes the layout every clone shares. On the CLI,
`grove create --cwd services/api` overrides the default for one workspace.

## Tailor the first-turn brief

`brief.instructions` adds your team's own short instruction after Grove's
first-turn brief. Set a broad default in user config, or a repository-specific
one in `.grove/config.json`. Like other scalar settings, the nearest cascade
layer supplies the text; Grove appends that text to its own brief.

```json title=".grove/config.json"
{
  "brief": {
    "instructions": "Run the focused checks before asking for review.",
    "self_naming": true
  }
}
```

With `self_naming` on, Grove asks an agent in a workspace with no description
to give the workspace a useful title and description. Keep added instructions
short. They are added to the brief that a newly started agent receives.

## Trust a private deployment CA

If Grove must call a private forge, gateway, or collector with a root CA that
the operating system does not know, name a PEM CA bundle or OpenSSL-hashed CA
directory in user config:

```json title="${user_config_dir}/grove/config.json"
{
  "tls": {
    "ca_path": "~/.config/grove/company-ca.pem"
  }
}
```

`GROVE_TLS_CA_PATH` supplies the same value for a deployment. This adds trust
to the operating system roots. It never disables certificate verification or
replaces the normal roots. Grove validates a configured path at startup, so a
missing or unreadable file fails loudly rather than silently losing the
private CA.

## New-workspace defaults

`defaults` pre-fills every create surface: the new-workspace modal, the web
composer, and a bare `grove create`. Every field is optional. A
field set to `null` (or omitted) has no saved value, so Grove uses the
normal source for that answer: `container.enabled` for runtime,
`brief.enabled` for the brief, and the selected agent's own default for
model.

| Field | Meaning |
|---|---|
| `agent` | Agent selected when the form opens. |
| `runtime` | `host` or `container` for the new workspace. |
| `brief` | Whether the agent receives Grove's first-turn brief. |
| `model` | Model id sent to the selected agent. |
| `branch_mode` | How Grove selects the branch source: `auto`, `new`, `existing`, `remote`, or `root`. |
| `base_ref` | The starting branch or ref offered by the form. |
| `skip_init` | Whether to skip the configured init script. |

Title is deliberately not a default. It describes one task, not a reusable
preference. Concrete new-branch and remote names are left out for the same
reason.

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

### Save defaults from the create form

Choose **Save as defaults** in the new-workspace modal after setting up a
workspace. It records the reusable form answers, then asks where they belong:

| Scope | File | Use it for |
|---|---|---|
| **User** | `~/.config/grove/config.json` | Your preference on every project on this machine. |
| **Project** | `.grove/config.json` | A team suggestion committed with the repository. |
| **Project (local)** | `.grove/config.local.json` | Your preference for this repository, without committing it. |

The project files are unavailable when the form was opened without a
repository. Saving to either project scope can have no visible effect for a
field already set in user defaults. That is intentional: for `defaults`, your
machine-level preference wins over a repository you cloned. See
[the defaults exception in the cascade](features-cascade.md#workspace-defaults-put-the-user-first).

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
