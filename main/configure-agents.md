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
| `kind`        | string | no  | Which adapter introspects this agent's sessions: `claude_code` or `generic` (default). See [below](#telling-grove-what-kind-of-agent-it-is). |
| `description` | string | no  | One-line label shown beside the name in the picker. |
| `env`         | object | no  | Extra environment variables exported in the agent's tmux window. |

## Defaults

Out of the box Grove ships with two agents:

| Name | Command | Kind | Notes |
|---|---|---|---|
| `claude` | `claude` | `claude_code` | Anthropic Claude Code, when installed on `$PATH`. |
| `shell`  | `$SHELL` | `generic` | A plain interactive shell. Useful for testing and for workspaces that do not need an agent. |

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

## Telling Grove what kind of agent it is

The `kind` field tells Grove whether it can look inside the agent's
sessions for the [Activity Dashboard](features-activity.md).

- `claude_code` means the agent is Claude Code or speaks its session
  format. Grove reads the transcript and derives live state (working,
  waiting, blocked), turn and token counts, and the session title. It
  also hands the agent a session id at launch, so session history knows
  which sessions Grove started.
- `generic` (the default) means Grove launches the command and tracks
  nothing beyond terminal output. The right choice for a plain shell or
  any tool with no transcript Grove understands.

A `kind` is mechanism, not policy. Declare it per agent and it cascades
like every other field. An agent declared only in one repo's
`.grove/config.json` stays scoped to that repo; it never shows up in
other repos' create menus, and the dashboard still resolves its adapter.

## Why `agents` merges by `name`

Most config lists *replace* across cascade layers. When the user layer
declares a list, it wins wholesale. That is not the desired behavior for
agents. A team's project config should pin the agreed-upon registry, and
an individual should still be able to add a personal entry without forking
the team's list.

The merge-by-name rule resolves that. New names append in overlay order.
Matching names merge **field by field**: the overlay's fields win, and the
base entry fills every gap. So overriding just the `claude` agent's
`command` keeps its `kind: "claude_code"` intact. You change one field,
you keep the rest. The user can drop `cursor` into `config.local.json`
without disturbing the project's `claude` or `aider` entries:

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

## Exact status with hooks

By default Grove derives a Claude Code agent's state by reading its
transcript. That is accurate but slightly behind. For exact, push-based
status, opt in to Grove's managed hooks:

```json
{ "hooks": { "enabled": true } }
```

With hooks on, Grove launches `claude_code` agents with an extra
`--settings` file. A lightweight hook inside the agent then reports each
lifecycle change (working, waiting, blocked, idle) the moment it happens.
The practical win is precision: polling can tell you the agent went
quiet; the hook can tell you it is blocked on a permission prompt. Your
own `.claude/settings.json` is never modified, and turning the feature
off is just flipping the flag back to `false`.

## See also

- [Project setup](configure-project.md): where the agent list lives.
- [Configuration cascade](features-cascade.md): the merge-by-name rule in context.
- [Agent activity and sessions](features-activity.md): what declaring a `kind` unlocks.
