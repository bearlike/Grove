# Push notifications

Grove's dashboard is mobile-first, but it is pull-only: you have to be looking
at it. Push notifications close that gap. Walk away, and when an agent finishes
its turn or stops to ask you something, your phone buzzes. Think of it as a
doorbell for your fleet: you do not stand by the door, it rings you.

A notification is a deep link. Tap it and you land on that workspace.

## What fires a notification

Grove does not run a second status engine for this. It reuses the same activity
stream the [Activity Dashboard](features-activity.md) already computes. A
notification is a debounced edge: the moment an agent session crosses *into* a
state that wants you.

| State | Meaning | Default |
|---|---|---|
| `waiting` | The turn finished. The agent may need you. | On |
| `blocked` | The agent is waiting on a permission prompt or a question. | On |
| `error` | The run or init failed. | On |
| `idle` | The agent went quiet. | Off |

Two guards keep it to one buzz per real episode, not a stream of them. A session
already finished when the daemon starts is recorded silently rather than fired
(no boot storm), and after a notification that workspace stays quiet for a short
debounce window, so a `waiting` that flaps back to `working` and finishes again
on a tool round-trip does not ring twice.

Off by default. You opt in through the [config cascade](features-cascade.md),
the same as every other Grove policy.

## Channels

Delivery is pluggable. The broker fans one notification out to every enabled
channel. Today there are two; each carries the deep link so a tap opens the
workspace.

- **Gotify** is the first channel. If you already run a
  [Gotify](https://github.com/gotify/server) server, point Grove at it and you
  get OS-level push on your phone with no extra app to install.
- **Webhook** is the generic sink: a JSON `POST` to any URL. It speaks
  [ntfy](https://ntfy.sh)'s JSON-publish format directly (set a `topic`), and
  any home-automation or chat relay can consume the same body.

Secrets never live in config. Each channel's config holds the *name* of the
environment variable that carries its token, never the token itself, so a
committed `.grove/config.json` stays safe to publish.

## Enabling it

Add a `notifications` block to any config layer. A user-scope
`~/.config/grove/config.json` covers every project:

```json
{
  "notifications": {
    "enabled": true,
    "deep_link_base_url": "https://grove.example.com",
    "on": ["waiting", "blocked", "error"],
    "gotify": {
      "enabled": true,
      "server_url": "https://gotify.example.com",
      "token_env": "GROVE_GOTIFY_TOKEN",
      "priority": 5
    },
    "webhook": {
      "enabled": true,
      "url": "https://ntfy.sh",
      "topic": "grove-alerts",
      "token_env": "GROVE_NTFY_TOKEN"
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
A notification links to `{deep_link_base_url}/w/{id}`, so set it to the URL you
open on your phone. Leave it blank and notifications still fire, just without the
tap-through link.

See the [Configuration Reference](configure-reference.md#notifications) for every
field and its default.

## Where it runs

The broker lives in the daemon and subscribes to the activity bus in-process.
The daemon stays loopback-only, exactly as before. It talks to Gotify or your
webhook over plain outbound HTTP, best-effort: a sink that is down or slow logs
one line and is skipped, and never slows or breaks the activity stream.
