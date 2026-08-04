# Push notifications

## Get told when it matters

Grove's dashboard is pull-only: you have to be looking at it. Push
notifications close that gap and buzz your phone when an agent finishes its
turn, asks you something, or its workspace breaks. Tap one and you land on
that workspace.

Grove runs no second status engine here. The broker reuses the same activity
stream the [Activity Dashboard](features-activity.md) already computes and
watches it for moments worth a buzz.

---

## What fires a notification

Three triggers, each with its own switch, fan out to every enabled channel.

**Agent state** (`on`) fires on the debounced rising edge into a state that
wants you:

| State | Meaning | Default |
|---|---|---|
| `waiting` | The turn finished. The agent may need you. | On |
| `blocked` | The agent is waiting on a permission prompt or a question. | On |
| `error` | The run or init failed. | On |
| `idle` | The agent went quiet. | Off |

**Question** (`on_question`) fires the moment the agent asks something,
independent of state, deduped by question id rather than debounced so it
always gets through. It carries the prompt and options for a lock-screen
decision.

**Workspace lifecycle** (`on_lifecycle`) fires when the workspace itself
changes:

| Event | Meaning | Default |
|---|---|---|
| `error` | The workspace hit an error. | On |
| `orphaned_detected` | Its worktree is gone. | On |
| `offline_detected` | Its tmux session vanished. | On |
| `created`, `killed`, `paused`, `resumed`, `respawned` | Routine lifecycle verbs. | Off |

- Defaults cover the unexpected only. Routine verbs are off but available.
- A fired-worthy session at daemon start is recorded silently rather than
  fired, avoiding a restart storm. Each fire then opens a 30-second
  debounce window that keeps the workspace quiet, so a flapping state does
  not double-ring, and a blocked question inside that window rides it
  instead of firing a duplicate.
- Off by default. Opt in through the [config cascade](features-cascade.md).

---

## Severity: how loudly it lands

Every notification carries a severity: `low`, `normal`, `high`, or `urgent`.
A finished turn is `normal`. A question, a `blocked` state, and a workspace
`error` are `high`. An idle agent or a routine lifecycle verb is `low`.

Each channel maps that onto its own scale:

- **Gotify** maps it to a 0-10 dial via `priorities`. Defaults
  `{low: 2, normal: 5, high: 8, urgent: 9}`: 8 and above is heads-up with
  sound, 4 through 7 vibrates, 1 through 3 is silent.
- **Webhook / ntfy** maps it to ntfy's 1-5 scale via its own `priorities`,
  defaulting to `{low: 2, normal: 3, high: 4, urgent: 5}`.

Re-tune either map without touching code. An unmapped severity falls back
to Gotify's `priority` field.

---

## Channels

Delivery is pluggable across two channels today.

### Gotify

Point Grove at a running [Gotify](https://github.com/gotify/server) server
for OS-level push with no extra app.

- The body is markdown by default (`markdown: true`): a bold agent name and
  reason, the task summary as a quoted line, and, for a question, the
  prompt and every option as its own line.
- It deep-links two ways. Android honors a `click.url` extra and opens the
  workspace on tap. The web client ignores that extra, so the link renders
  as plain markdown in the body too.

### Webhook

- A generic sink: a JSON `POST` to any URL, speaking [ntfy](https://ntfy.sh)'s
  publish format directly (set a `topic`), so any relay can consume it.
- The JSON carries both renderings plus severity, trigger, and any open
  questions with their options and ids.

Secrets never live in config. Each channel holds the *name* of the
environment variable that carries its token, never the token, so a
committed `.grove/config.json` stays safe to publish.

---

## Enabling it

Add a `notifications` block to any config layer. A user scope
`~/.config/grove/config.json` covers every project:

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

Export the tokens in the daemon's environment:

```bash
export GROVE_GOTIFY_TOKEN="your-gotify-app-token"
export GROVE_NTFY_TOKEN="your-ntfy-token"   # only if your topic needs auth
```

`deep_link_base_url` is where your [web dashboard](use-webapp.md) is
reachable. A notification links to `{deep_link_base_url}/w/{id}`, the
workspace page, where you read the transcript and answer the question.

It defaults to `http://localhost:3000`, which fails once you are off that
machine, since a phone resolves `localhost` to itself. Nothing errors. The
tap just goes nowhere.

> [!TIP]
> Set `deep_link_base_url` to an address your phone can resolve, such as
> `https://grove.example.com` or your LAN address. The daemon warns at
> startup if it is left on a loopback address. Set it to `""` for no link.

See the [Configuration Reference](configure-reference.md#notifications) for
every field and its default.

---

## Limitations

- **Delivery rides the daemon, not the TUI.** The broker subscribes to the
  daemon's activity bus in-process, and the daemon stays loopback-only, so
  a TUI-only setup with no daemon gets no push notifications. Delivery is
  best-effort: a down or slow sink logs one line and is skipped, never
  slowing the activity stream.
- **Gotify needs an application token, not a client one.** Use the `Axxx…`
  token from a Gotify *application*, the send-only kind. Grove only sends
  and never reads your Gotify messages back.
- **A Gotify push cannot be retracted.** Gotify gives an application token
  no update, supersede, or delete API. If an agent moves past a question
  before you check your phone, the "needs input" push stays in your tray
  uncorrected.
- **Question notifications need a harness that surfaces the question.**
  Claude Code works today: it never writes a pending question to its
  transcript while on screen, so Grove's status hook, on by default,
  catches it in time. A Codex agent asking through an MCP-bridged question
  tool works the same way. Codex's interactive approval prompts are never
  persisted to its rollout, so Grove has no signal and fires no question
  notification for those.
