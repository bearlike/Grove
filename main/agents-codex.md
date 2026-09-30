# Codex

## Control a Codex thread from Grove

Grove starts `codex app-server --stdio`, initializes its JSON RPC connection and creates a thread. The app server owns every turn inside that thread, and Grove drives it from the web dashboard, the TUI or MCP.

- **A message either starts a turn or steers one.** Sending while Codex is idle starts a turn. Sending while it is busy steers the turn already running.
- **Steering names the turn it expects.** If the turn moved on, Codex rejects the request rather than applying your input to work you were not looking at.
- **Interrupt targets the current turn.** With no turn running there is nothing to cancel, so Grove tells you instead of sending a request that means nothing.
- **Approvals come to you.** Grove answers supported command and file change approvals with accept or decline.

## What Codex does not offer

- **Delivery is queued, not confirmed.** Codex acknowledges submission, which is weaker evidence than Claude Code's replay. Treat a sent message as handed over rather than read.
- **A model change is recorded, not validated.** The app server accepts the setting before checking it, so an unknown model surfaces on the next turn rather than at the moment you switch.
- **Approval breadth is narrow.** Session wide grants, cancellation and separate permission grant requests are unsupported. There is no general plan approval control.
- **Questions need plan mode.** `request_user_input` is only available in Codex plan collaboration mode.

## Set the approval policy deliberately

Codex app server policy comes from Codex configuration, not from a terminal shortcut. A flag such as `--yolo` on the command line does not configure thread approvals.

Set `approval_policy` and `sandbox_mode` in Codex's own configuration, keeping the permissions your task actually requires.

## Configuration

```json title=".grove/config.json"
{
  "agents": [
    { "name": "codex-fast", "command": "codex --model gpt-5.3-codex-spark", "kind": "codex" }
  ]
}
```

Codex mints its own thread identifier, so Grove discovers the session rather than naming it at launch.

## See also

- [Agents](configure-agents.md): choosing between native, headed and headless.
- [Claude Code](agents-claude-code.md) and [OpenCode](agents-opencode.md): the other native adapters.
- [Agent activity and sessions](features-activity.md): what the transcript unlocks.
