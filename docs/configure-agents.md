# Agents

## Choose what runs in each workspace

Grove runs your existing coding agents in isolated workspaces. Claude Code, Codex and OpenCode have native adapters that let you send messages, interrupt work and answer questions from Grove without attaching to their terminal UI.

- **Native session.** Grove starts the agent's protocol process and keeps its control connection open between turns.
- **Headless command.** A standalone command such as `claude -p` runs without an interactive UI, but Grove does not own its control protocol.
- **Interactive terminal.** Grove launches the agent's usual UI in tmux. Other terminal tools, including Aider, Cursor, Gemini and a shell, use this path.

## Native sessions and terminal twins

Choose native mode when you want to control Claude Code, Codex or OpenCode from Grove. Choose a headed terminal when you need the agent's own interactive UI. A standalone headless command suits a scripted task that can finish without further input.

| Decision | Native session | Headed terminal | Standalone headless command |
|---|---|---|---|
| Best for | Managing agents from Grove | Working directly in the agent UI | Running a predefined task |
| Select | `claude`, `codex` or `opencode` | `claude-terminal`, `codex-terminal` or `opencode-terminal` | A command such as `claude -p …` with `native: false` |
| Messages and controls | Agent protocol calls | Terminal input and agent shortcuts | No persistent Grove control channel |
| Questions and approvals | Depends on the adapter below | Respond in the agent UI | Requires the command's own input or approval policy |
| Attach | Read the protocol log | Use the interactive terminal | Read command output |
| Pause and resume | Not supported. Use Respawn for recovery | Supported by workspace lifecycle | Restarts the configured command |
| Main limitation | No interactive agent UI | Terminal steering is not a native protocol | Not a substitute for an interactive session |

- **Native does not mean unattended permission.** The agent's approval and sandbox policy still applies. A pending approval can stop progress until you respond.
- **The Stream tab is a log.** Grove's worker records protocol frames in the tmux pane. Attaching does not open a second agent UI.
- **Recovery is separate from pause.** The worker reconnects after a daemon restart without replacing its running agent. Respawn may recover history when a session ID and transcript were saved. Do not assume every native Codex restart continues its thread. Creating a native workspace with a transcript ID remains unsupported.
- **Choose per workspace.** Use **Session mode** in the web composer, the TUI checkbox, `grove create --native/--terminal`, or `native` on `grove_create_workspace`. The choice is saved with the workspace.
- **Containers need the control connection.** Native mode requires the daemon's mailbox socket to be available inside the container.

```json title=".grove/config.json"
{
  "agents": [
    {"name": "claude-tui", "command": "claude", "kind": "claude_code", "native": false}
  ]
}
```

Mail reaches every live agent, native or interactive, through the channel Grove already steers it with. Mailbox text is untrusted agent data that cannot authorize a tool, a config change, or a bypass of native permissions.

## Native adapters

Three agents have first party native adapters. All three run without the agent's interactive UI, and each page below covers its protocol, its approval support and what it cannot do.

| Capability | [Claude Code](agents-claude-code.md) | [Codex](agents-codex.md) | [OpenCode](agents-opencode.md) |
|---|---|---|---|
| Transport | JSON streaming through `claude -p` | JSON RPC through `codex app-server` | HTTP and events through `opencode serve` |
| Message acknowledgement | Matching replay confirms delivery | Submission is queued, not proof of delivery | The reply itself confirms delivery |
| Model change | Provider validates before success | App server records the setting | Applied per message |
| Questions | Structured `AskUserQuestion` | `request_user_input` requires Codex plan collaboration mode | Answered when OpenCode raises one |
| Tool approvals | Not answered by this adapter | Accept or decline supported command and file changes | Once, always or reject |
| Plan approval | Requires the interactive terminal | No general plan approval control | No general plan approval control |
| Transcript | One file per session | One file per session | One shared database, read only |

## Agent spec fields

| Field | Type | Required | Meaning |
|---|---|---|---|
| `name` | string | yes | Picker name and merge key across configuration layers. |
| `command` | string | yes | Agent command. Grove adapts it for native mode or launches it in the terminal. |
| `kind` | string | no | Adapter selection. Use `claude_code`, `codex`, `opencode`, `mewbo` or `generic`. See [below](#telling-grove-what-kind-of-agent-it-is). |
| `description` | string | no | Short label in the picker. |
| `env` | object | no | Environment variables supplied to the agent. |
| `env_unset` | `array<string>` | no | Variables removed before applying `env`. |
| `models` | `array<string>` | no | Model ids to add, reorder or pin in the picker. |
| `tools_offline` | boolean | no | Disallow network tools where the adapter supports it. |
| `native` | boolean | no | Default to a Grove controlled native session. Supported for Claude Code, Codex and OpenCode. |

## Defaults

Grove ships seven agents, and they always merge into your roster, listed or not. See [Hiding the built-ins](#hiding-the-built-ins) to run a closed set instead.

| Name | Command | Kind | Notes |
|---|---|---|---|
| `claude` | `claude` | `claude_code` | Native Claude Code by default. |
| `claude-terminal` | `claude` | `claude_code` | Headed Claude Code terminal twin. |
| `codex` | `codex` | `codex` | Native Codex by default. |
| `codex-terminal` | `codex` | `codex` | Headed Codex terminal twin. |
| `opencode` | `opencode` | `opencode` | Native OpenCode by default. |
| `opencode-terminal` | `opencode` | `opencode` | Headed OpenCode terminal twin. |
| `shell` | `$SHELL` | `generic` | A plain interactive shell for testing or agent-free work. |

## Adding a custom agent

Drop the entry into your project config. Grove merges it by `name`, so the list extends without redefining `claude` or `shell`.

```json title=".grove/config.json"
{
  "agents": [
    { "name": "aider",  "command": "aider --model sonnet" },
    { "name": "cursor", "command": "cursor-agent",         "description": "Cursor's CLI agent" },
    { "name": "gpt",    "command": "openai-agent --model gpt-4o-mini", "env": { "OPENAI_API_KEY": "${OPENAI_API_KEY}" } }
  ]
}
```

- New names append, matching names merge field by field, so overriding only `claude`'s `command` keeps its `kind`. See [Configuration cascade](features-cascade.md).
- A teammate drops `cursor` into `config.local.json` without disturbing the team's list. Restart `grove` to see it.
- Pick `shell` to run without an agent. Grove still creates the worktree, runs the init script and opens tmux.

## Telling Grove what kind of agent it is

`kind` tells Grove whether it can look inside a session for the [Activity Dashboard](features-activity.md).

- `claude_code`, `codex` and `opencode` support native control and read transcripts for live state, turns, tokens and title.
- `mewbo` reads a remote session over REST, configured in the `mewbo` section. It is a remote adapter, not a local native process.
- `generic`, the default, launches the command without session introspection or native control. Use it for other terminal agents.
- A custom named Claude agent such as `code-agent` must declare `kind: "claude_code"` itself. An omitted `kind` silently defaults to `generic` with no transcript for the dashboard.

```json
{
  "agents": [
    { "name": "code-agent", "command": "claude --model claude-opus-4-5", "kind": "claude_code" }
  ]
}
```

## Models in the create form

The model field offers the selected agent's discovered models, plus **Agent default** and **Custom** for any id you type.

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

`models` pins, reorders or adds the ids your team wants to see. It is never a validated allowlist, and Grove forwards any id verbatim to the provider.

## Hiding the built-ins

Set `builtin_agents: false` and your `agents` list becomes the whole roster, an allowlist.

```json
{
  "builtin_agents": false,
  "agents": [
    { "name": "aider",  "command": "aider --model sonnet" },
    { "name": "cursor", "command": "cursor-agent", "description": "Cursor's CLI agent" }
  ]
}
```

- Name a built in bare, `{ "name": "claude" }`, and merge by name fills in its `command` and `kind`.
- Hiding is a real gate. A hidden agent cannot create a workspace anywhere, and an existing workspace created with it cannot resume or respawn until you add it back.
- `builtin_agents` cascades, so a committed config can restore the roster for one repo. It is not a security boundary.
- Issue ops looks for an agent named `claude`. Without one, point [issue ops](issue-ops.md)'s `agent` at your own.

## See also

- [Claude Code](agents-claude-code.md), [Codex](agents-codex.md), [OpenCode](agents-opencode.md): each native adapter in depth.
- [Project setup](configure-project.md): where the agent list lives.
- [Configuration cascade](features-cascade.md): merge-by-name in context.
- [Agent activity and sessions](features-activity.md): what `kind` unlocks.
- [Container workspaces](features-containers.md#getting-secrets-and-environment-in):
  `env_file`/`env_command` relative to an agent's own `env`.
