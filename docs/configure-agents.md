# Agents

## Add and override agents

An *agent* in Grove is a named command. The TUI's create modal lists
every agent the cascade has resolved, and sends your pick to the `agent`
tmux window via `send-keys`. Anything terminal-based works: Claude Code,
Aider, Cursor's CLI, Gemini, or a plain shell.

## Agent spec fields

| Field | Type | Required | Meaning |
|---|---|---|---|
| `name`        | string | yes | Picker identifier, and merge key across cascade layers. |
| `command`     | string | yes | Shell command sent to the agent window. Quoted args and env expansion work. |
| `kind`        | string | no  | Session adapter: `claude_code`, `codex`, `mewbo`, or `generic` (default). See [below](#telling-grove-what-kind-of-agent-it-is). |
| `description` | string | no  | One-line picker label. |
| `env`         | object | no  | Extra environment variables for the tmux window. |
| `env_unset`   | `array<string>` | no  | Variable names cleared before `env` is applied, so an ambient value (like a `CLAUDE_CONFIG_DIR` inherited from the daemon) cannot leak into the agent's window. |
| `models`      | `array<string>` | no  | Curated model ids for the create-form picker, overriding auto-discovery. |
| `tools_offline` | boolean | no | Launch with network-facing tools disallowed (Claude Code drops `WebFetch`/`WebSearch`, Codex disables sandbox networking). No effect on `generic`/`mewbo`. |

## Models in the create form

The model field is a picker for the agent you selected. It offers that
agent's discovered models, plus **Agent default** and **Custom**. Pick
**Custom** to enter any model id yourself.

Operators curate the picker with `AgentSpec.models`. This is a convenience
list for people opening the form, never a validated allowlist. Grove forwards
any selected or custom id verbatim to the provider.

```json title=".grove/config.json"
{
  "agents": [
    {
      "name": "claude",
      "models": ["sonnet", "opus", "my-gateway-model"]
    }
  ]
}
```

An empty `models` list keeps the agent's normal discovery. A configured list
can pin, reorder, or add the ids your team wants to see, but it cannot prevent
a custom id from being sent.

## Defaults

Grove ships three agents:

| Name | Command | Kind | Notes |
|---|---|---|---|
| `claude` | `claude` | `claude_code` | Claude Code, when on `$PATH`. |
| `codex`  | `codex`  | `codex` | OpenAI Codex CLI, when on `$PATH`. |
| `shell`  | `$SHELL` | `generic` | A plain interactive shell, for testing or agent-free work. |

These always merge into your roster, listed or not. See [Hiding the
built-ins](#hiding-the-built-ins) to run a closed set instead.

## Adding a custom agent

Drop the entry into your project config. Grove merges it by `name`, so
the list extends without redefining `claude` or `shell`. The most common
addition is Aider:

```json title=".grove/config.json"
{
  "agents": [
    { "name": "aider",  "command": "aider --model sonnet" },
    { "name": "cursor", "command": "cursor-agent",         "description": "Cursor's CLI agent" },
    { "name": "gpt",    "command": "openai-agent --model gpt-4o-mini", "env": { "OPENAI_API_KEY": "${OPENAI_API_KEY}" } }
  ]
}
```

After saving, restart `grove`. The new agents show up in the create
modal and run in the workspace's `agent` window.

## Telling Grove what kind of agent it is

`kind` tells Grove whether it can look inside a session for the
[Activity Dashboard](features-activity.md):

- `claude_code`: Claude Code or a compatible format. Grove reads the
  transcript for live state (working, waiting, blocked), turns, tokens,
  and title, and hands it a session id at launch to track what it started.
- `codex`: OpenAI Codex CLI. Grove reads its rollout files
  (`~/.codex/sessions`, or `$CODEX_HOME`) for the same state, turns, and
  tokens. Codex mints its own id with no flag to set one, so Grove finds
  it on disk by working directory instead.
- `mewbo`: a remote Mewbo session over its REST API, created at launch,
  anchored to the worktree, status read from the API, not a local file.
  `command` still runs in the agent window, but typically just `$SHELL`,
  since the session lives server side. The `mewbo` section holds
  `base_url`, `api_key_env` (NAME of the env var holding your key), and
  `timeout_seconds`.
- `generic` (default): Grove launches the command and tracks nothing.

`kind` cascades like any field: an agent declared in one repo's
`.grove/config.json` stays scoped there, still resolving its adapter, but
absent elsewhere.

## Custom-named Claude agents: declare `kind` explicitly

Give a Claude Code agent a custom name (`"claude-opus"`, `"code-agent"`)
and declare `kind: "claude_code"` in that entry. Merge-by-name does NOT
inherit the built-in `claude` agent's kind. A name-only match inherits
only what the overlay provides. Omit `kind` and it silently defaults to
`generic`: no session id, no transcript for the Activity Dashboard.

```json
{
  "agents": [
    { "name": "code-agent", "command": "claude --model claude-opus-4-5", "kind": "claude_code" }
  ]
}
```

Overriding only `command` on `claude` is safe: `kind` carries over. The
risk is a new name never in the base list.

## Why `agents` merges by `name`

Most config lists *replace* across cascade layers: a user-layer list wins
wholesale. Agents differ: a project config pins the agreed registry, and
an individual should still add a personal entry unforked.

- New names append in overlay order.
- Matching names merge **field by field**: the overlay's fields win, the
  base fills every gap. Override just `claude`'s `command` and its
  `kind` stays intact.

That lets the user drop `cursor` into `config.local.json` without
disturbing `claude` or `aider`:

```json title=".grove/config.local.json"
{
  "agents": [
    { "name": "cursor", "command": "cursor-agent" }
  ]
}
```

## Hiding the built-ins

Merge-by-name is why leaving `claude`, `codex`, or `shell` out of your
`agents` list does not drop them. Set `builtin_agents: false` (default
`true`) and your config becomes the whole roster, an allowlist:

```json
{
  "builtin_agents": false,
  "agents": [
    { "name": "aider",  "command": "aider --model sonnet" },
    { "name": "cursor", "command": "cursor-agent", "description": "Cursor's CLI agent" }
  ]
}
```

The picker now shows only `aider` and `cursor`. `claude`, `codex` and `shell`
went unnamed, with nothing left to merge into. Want Claude Code back?
Name it bare, `{ "name": "claude" }`: merge-by-name fills in `command`
and `kind` from the built-in. A future fourth stays hidden until named
too, unlike a per-agent "disabled" flag, which would need a new entry
every release.

Hiding an agent is a real gate: it cannot create a workspace from the
CLI, MCP, or web UI. Consequences worth knowing:

- **Existing workspaces are not grandfathered in.** Hide the agent a
  workspace was created with and it can no longer resume or respawn. Add
  it back or kill the workspace. Dashboard tracking is unaffected.
- **An empty roster is not an error.** No agents of your own gets an
  empty picker, not a block.
- **A project config can put the built-ins back**, since `builtin_agents`
  cascades: a committed `.grove/config.json` setting it `true` restores
  the roster even where you turned it off elsewhere. Not a security
  boundary: a project config can already run any command it wants.
- **`grove config init` writes a `claude` entry**, opting Claude Code
  back in. Remove it for a closed roster.
- **Issue ops looks for an agent named `claude` by default.** Without one,
  issue-ops-created workspaces fail loudly, Grove replying on the ticket
  that the agent is unknown. Point [issue ops](issue-ops.md)'s `agent` at
  your own instead.

`builtin_agents` cascades: user config for everywhere, project config for
one repo, or `GROVE_BUILTIN_AGENTS=false grove` for one shell.

## Running without an agent

Pick `shell` from the create modal. Grove still creates the worktree,
runs the init script, and opens the tmux session, just with a shell in
the agent window instead of an LLM client.

## Exact status with hooks

By default Grove derives Claude Code state from its transcript: accurate,
but slightly behind. For exact, push-based status, opt in to Grove's
managed hooks:

```json
{ "hooks": { "enabled": true } }
```

With hooks on, Grove launches `claude_code` agents with an extra
`--settings` file, and a hook reports each lifecycle change (working,
waiting, blocked, idle) as it happens: polling can only tell you the
agent went quiet, the hook can tell you it is blocked on a permission
prompt. Your `.claude/settings.json` stays untouched, and turning it off
just flips the flag back.

## See also

- [Project setup](configure-project.md): where the agent list lives.
- [Configuration cascade](features-cascade.md): merge-by-name in context.
- [Agent activity and sessions](features-activity.md): what `kind` unlocks.
- [Container workspaces](features-containers.md#getting-secrets-and-environment-in):
  `env_file`/`env_command` relative to an agent's own `env`.
