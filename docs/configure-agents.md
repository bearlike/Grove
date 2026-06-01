# Agents

An *agent* in Grove is a named command. The TUI's create modal lists every
agent the cascade has resolved. The one you pick gets sent to the `agent`
tmux window via `send-keys`. Anything that runs in a terminal is supported,
including Claude Code, Aider, Cursor's CLI, Gemini, an OpenAI shim, or a
plain shell.

## Agent spec fields

| Field | Type | Required | Meaning |
|---|---|---|---|
| `name`        | string | yes | Identifier in the picker. Also the merge key when the cascade combines agent lists. |
| `command`     | string | yes | Shell command sent to the agent window. Quoted args are fine; environment variables expand at run time. |
| `description` | string | no  | One-line label shown beside the name in the picker. |
| `env`         | object | no  | Extra environment variables exported in the agent's tmux window. |

## Defaults

Out of the box Grove ships with two agents:

| Name | Command | Notes |
|---|---|---|
| `claude` | `claude` | Anthropic Claude Code, when installed on `$PATH`. |
| `shell`  | `$SHELL` | A plain interactive shell. Useful for testing and for workspaces that do not need an agent. |

The most common addition is Aider:

```json
{
  "agents": [
    { "name": "aider", "command": "aider --model sonnet", "description": "Aider with Sonnet" }
  ]
}
```

## Adding a custom agent

Drop the entry into your project config. Grove merges it with the defaults
by `name`, so the list extends without redefining `claude` or `shell`:

```json
{
  "agents": [
    { "name": "aider",  "command": "aider --model sonnet" },
    { "name": "cursor", "command": "cursor-agent",         "description": "Cursor's CLI agent" },
    { "name": "gpt",    "command": "openai-agent --model gpt-4o-mini", "env": { "OPENAI_API_KEY": "${OPENAI_API_KEY}" } }
  ]
}
```

After saving, restart `grove`. The new agents show up in the create modal,
and their commands run in the workspace's `agent` window.

## Why `agents` merges by `name`

Most config lists *replace* across cascade layers. When the user layer
declares a list, it wins wholesale. That is not the desired behavior for
agents. A team's project config should pin the agreed-upon registry, and
an individual should still be able to add a personal entry without forking
the team's list.

The merge-by-name rule resolves that. Matching `name` entries replace, and
new names append in overlay order. The user can drop `cursor` into
`config.local.json` without disturbing the project's `claude` or `aider`
entries:

```json
// .grove/config.local.json (your machine, never committed)
{
  "agents": [
    { "name": "cursor", "command": "cursor-agent" }
  ]
}
```

## Running without an agent

Pick `shell` from the create modal. Grove still creates the worktree, runs
the init script, and opens the tmux session. The agent window runs an
interactive shell instead of an LLM client.

## See also

- [Project setup](configure-project.md): where the agent list lives.
- [Configuration cascade](features-cascade.md): the merge-by-name rule in context.
