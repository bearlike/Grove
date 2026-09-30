# Push notifications

## Know when your agents need attention

Grove's dashboard is pull only. You have to be looking at it, and push notifications close that gap.

## Get told when it matters

- Your phone buzzes when an agent finishes its turn, asks you something, or its workspace breaks.
- Tap one and you land on that workspace.
- Grove runs no second status engine. The broker watches the same activity stream the [Activity Dashboard](features-activity.md) already computes for moments worth a buzz.
- You choose which triggers reach each channel, and everything is off until you turn it on.

## What fires a notification

Each trigger has its own switch.

| State | Meaning | Default |
|---|---|---|
| `waiting` | The turn finished. The agent may need you. | On |
| `blocked` | The agent is waiting on a permission prompt or a question. | On |
| `error` | The run or init failed. | On |
| `idle` | The agent went quiet. | Off |

| Event | Meaning | Default |
|---|---|---|
| `error` | The workspace hit an error. | On |
| `orphaned_detected` | Its worktree is gone. | On |
| `offline_detected` | Its tmux session vanished. | On |
| `created`, `killed`, `paused`, `resumed`, `respawned` | Routine lifecycle verbs. | Off |

- `on` lists the agent states that alert, and `on_lifecycle` lists the workspace events. Routine verbs are off by default.
- `on_question` adds the prompt and its options to the alert, so you can answer from the tray.
- A session already waiting when the daemon starts is recorded without an alert, and a 30 second window stops one state from buzzing twice.
- Enable it in any layer of the [config cascade](features-cascade.md).

## Severity: how loudly it lands

Every notification carries `low`, `normal`, `high`, or `urgent` severity.

- A finished turn is `normal`.
- A question, `blocked` state, or workspace `error` is `high`.
- An idle agent or routine lifecycle event is `low`.
- Gotify maps severity through `priorities` from 0 to 10.
- Webhook maps severity through `priorities` on ntfy's scale from 1 to 5.

## Channels

Delivery is pluggable across two channels today, and secrets never live in config. Each channel holds the name of the environment variable that carries its token, so a committed `.grove/config.json` stays safe to publish.

<div class="ms-grid ms-grid--3">
  <div class="ms-card">
    <span class="ms-card__title">Gotify</span>
    <span class="ms-card__body">Point Grove at a running <a href="https://github.com/gotify/server">Gotify</a> server for OS level push with no extra app. The body is markdown with the agent, the reason, the task and every question option on its own line, and Android opens the workspace on tap.</span>
  </div>
  <div class="ms-card">
    <span class="ms-card__title">Webhook</span>
    <span class="ms-card__body">A JSON POST to any URL, speaking <a href="https://ntfy.sh">ntfy</a>'s publish format directly, so any relay can consume it. The JSON carries both renderings plus severity, trigger and any open questions with their ids.</span>
  </div>
</div>

## Limitations

- The daemon sends them, so a TUI only setup without the daemon receives no alerts. A down or slow channel is skipped, never retried.
- Gotify needs an application token rather than a client token. Grove only sends messages.
- A Gotify push cannot be retracted. A resolved question can remain in your tray.
- Question alerts need a harness that surfaces the question. Claude Code and Codex questions through an MCP tool work. Codex interactive approval prompts produce no alert.

## Enabling it

Add `notifications` to any configuration layer.

```json title="~/.config/grove/config.json"
{
  "notifications": {
    "enabled": true,
    "deep_link_base_url": "https://grove.example.com",
    "on": ["waiting", "blocked", "error"],
    "on_question": true,
    "on_lifecycle": ["error", "offline_detected", "orphaned_detected"],
    "gotify": {
      "enabled": true,
      "server_url": "https://gotify.example.com",
      "token_env": "GROVE_GOTIFY_TOKEN",
      "markdown": true,
      "priorities": { "low": 2, "normal": 5, "high": 8, "urgent": 9 }
    },
    "webhook": {
      "enabled": true,
      "url": "https://ntfy.sh",
      "topic": "grove-alerts",
      "token_env": "GROVE_NTFY_TOKEN",
      "priorities": { "low": 2, "normal": 3, "high": 4, "urgent": 5 }
    }
  }
}
```

Export the tokens in the daemon's environment.

```bash
export GROVE_GOTIFY_TOKEN="your-gotify-app-token"
export GROVE_NTFY_TOKEN="your-ntfy-token"   # only if your topic needs auth
```

- `deep_link_base_url` is where your [web dashboard](use-webapp.md) is reachable. Its default is `http://localhost:3000`, which a phone resolves to itself, so set an address your phone can reach, or `""` for no link.
- The [Configuration Reference](configure-reference.md#notifications) lists every field and default.
