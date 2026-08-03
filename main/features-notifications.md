# Push notifications

Grove's dashboard is mobile-first, but it is pull-only: you have to be looking
at it. Push notifications close that gap. Walk away, and when an agent finishes
its turn, asks you something, or its workspace breaks, your phone buzzes. Think
of it as a doorbell for your fleet: you do not stand by the door, it rings you.

A notification is a deep link. Tap it and you land on that workspace.

Grove runs no second status engine for this. The broker that decides when to
ring reuses the same activity stream the [Activity Dashboard](features-activity.md)
already computes; it only watches for the moments in that stream worth a buzz.

---

## What fires a notification

Three independent triggers, each with its own on/off switch, all fan out to
every enabled channel.

**Agent state** (`on`) fires on the debounced rising edge into a state that
wants you:

| State | Meaning | Default |
|---|---|---|
| `waiting` | The turn finished. The agent may need you. | On |
| `blocked` | The agent is waiting on a permission prompt or a question. | On |
| `error` | The run or init failed. | On |
| `idle` | The agent went quiet. | Off |

**Question** (`on_question`) fires the moment the agent asks you something,
independent of the state axis above. It is deduped by question id, not
debounced: a second question five seconds later is a second thing you owe an
answer to, so it always gets through, even inside the state debounce window. A
question notification carries the prompt and its options, so you can often
decide right from the lock screen.

**Workspace lifecycle** (`on_lifecycle`) fires when the workspace itself
changes, not the conversation inside it:

| Event | Meaning | Default |
|---|---|---|
| `error` | The workspace hit an error. | On |
| `orphaned_detected` | Its worktree is gone. | On |
| `offline_detected` | Its tmux session vanished. | On |
| `created`, `killed`, `paused`, `resumed`, `respawned` | Routine lifecycle verbs. | Off |

The default lifecycle set is the *unexpected* half: a workspace that broke or
was interrupted. The routine verbs are available if you want a buzz on every
create or kill too, but a pause you triggered yourself needs no push back to
you.

Two guards keep this to one buzz per real episode. A session or workspace
already in a fired-worthy state when the daemon starts is recorded silently
rather than fired, so a restart never triggers a storm of stale notifications.
And after a state or lifecycle fire, that workspace stays quiet for a short
debounce window (30 seconds by default), so a `waiting` that flaps back to
`working` and finishes again on a tool round-trip does not ring twice. A
question, notified while blocked, keeps that debounce window quiet behind it
too, so you get the rich question push instead of a second, plainer "blocked"
push for the same moment.

Off by default. You opt in through the [config cascade](features-cascade.md),
the same as every other Grove policy.

---

## Severity: how loudly it lands

Every notification carries a severity, `low` / `normal` / `high` / `urgent`,
independent of which channel delivers it. A finished turn is `normal`. A
question, a `blocked` state, and a workspace `error` are all `high`. An idle
agent or a routine lifecycle verb is `low`.

Severity is channel-agnostic on purpose: the event decides how much it
matters, and each channel maps that onto its own native scale in its own
config, so a pending question can buzz your phone while a routine pause stays
silent.

- **Gotify** maps severity to its 0-10 priority dial via `priorities`. The
  defaults, `{low: 2, normal: 5, high: 8, urgent: 9}`, are chosen to hit the
  Android client's real bins: 8 and above is a heads-up notification with
  sound, 4 through 7 vibrates, 1 through 3 is silent. That is what makes a
  pending question buzz your phone while a routine pause does not.
- **Webhook / ntfy** maps severity to ntfy's 1-5 scale via its own
  `priorities`, defaulting to `{low: 2, normal: 3, high: 4, urgent: 5}`.

Re-tune either map in config without touching code; an unmapped severity falls
back to Gotify's `priority` field.

---

## Channels

Delivery is pluggable. The broker fans one notification out to every enabled
channel. Today there are two.

### Gotify

If you already run a [Gotify](https://github.com/gotify/server) server, point
Grove at it and you get OS-level push on your phone with no extra app to
install.

The notification body is markdown by default (`markdown: true`), and both the
Android and web Gotify clients render it: a bold agent name and reason, the
task summary as a quoted line, and, for a question, the prompt and every
option as its own line. A pending question reads as a small, legible card
rather than a wall of text.

The notification also deep-links. On Android, tapping it opens the workspace
directly, because Gotify's Android client honors a `click.url` extra. The web
client ignores that extra, which is why the same link is also rendered as
plain markdown inside the body: one link, two ways to reach it.

### Webhook

The generic sink: a JSON `POST` to any URL. It speaks [ntfy](https://ntfy.sh)'s
JSON-publish format directly (set a `topic`), and any home-automation or chat
relay can consume the same body. The JSON carries both the plain and markdown
renderings plus the structured facts behind them, severity, trigger, and any
open questions with their options and ids, so a relay can render its own view
or even answer a question programmatically.

Secrets never live in config for either channel. Each channel's config holds
the *name* of the environment variable that carries its token, never the
token itself, so a committed `.grove/config.json` stays safe to publish.

---

## Enabling it

Add a `notifications` block to any config layer. A user-scope
`~/.config/grove/config.json` covers every project:

```json
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

Then export the tokens in the daemon's environment:

```bash
export GROVE_GOTIFY_TOKEN="your-gotify-app-token"
export GROVE_NTFY_TOKEN="your-ntfy-token"   # only if your topic needs auth
```

`deep_link_base_url` is where your [web dashboard](use-webapp.md) is reachable.
A notification links to `{deep_link_base_url}/w/{id}`, the workspace page, where
you can read the transcript and answer the question. That link is the entire
point of the push: the buzz tells you something happened, the tap takes you to
it.

It defaults to `http://localhost:3000`, the dashboard's own local address. That
is right when you tap the notification on the same machine, and useless when you
tap it on your phone, because your phone resolves `localhost` to itself. Nothing
errors. The push arrives, the tap simply goes nowhere.

> [!TIP]
> Set `deep_link_base_url` to an address your phone can actually resolve, such as
> `https://grove.example.com` or your machine's LAN address. The daemon logs a
> warning at startup if you leave it on a loopback address, so check the log once
> after enabling notifications. Set it to `""` to render no link at all.

See the [Configuration Reference](configure-reference.md#notifications) for
every field and its default.

---

## Limitations

- **Delivery rides the daemon, not the TUI.** The broker lives in the daemon
  and subscribes to its activity bus in-process; the daemon stays
  loopback-only, exactly as before. A TUI-only setup with no daemon running
  gets no push notifications at all. Delivery itself is best-effort: a sink
  that is down or slow logs one line and is skipped, and never slows or
  breaks the activity stream.
- **Gotify needs an application token, not a client one.** Use the `Axxx…`
  token from a Gotify *application*, the send-only kind. Grove only ever
  sends; it never reads your Gotify messages back.
- **A Gotify push cannot be retracted.** Gotify has no update or supersede
  API, and an application token cannot delete a message either. If an agent
  moves past a question before you look at your phone, the "needs input" push
  stays in your notification tray; it does not get cleared or corrected.
- **Question notifications need a harness that actually surfaces the
  question.** Claude Code works today: it never writes a pending question to
  its transcript while it is on screen, so Grove's status hook, on by default,
  is what catches it in time. A Codex agent asking through an MCP-bridged
  question tool works the same way. Codex's own interactive approval prompts
  are different: they are never persisted to its rollout at all, so Grove has
  no signal to detect them, and no question notification fires for those.
