# grove.core.notifications — push on agent-state edges (#70)

> ↑ [grove.core](../CLAUDE.md) · [root](../../../../CLAUDE.md)

Turns the existing activity stream into push notifications. One nameable job:
*deliver a notification when an agent crosses into a state that wants the human*.
The broker decides; channels deliver. Off by default (`cfg.notifications`).

Three atomic types, one concern each — no free functions, no module-level
factories. `Notification` (the event, owns its own shape), `NotificationChannel`
(the ABC contract), `NotificationBroker` (the edge engine + the one factory).

- **Reuse the activity bus; add NO status computation (the KISS rule).** The
  broker subscribes via `bind(ActivityService.subscribe)` exactly like `_SseHub`
  and reads each session's already-blended `AgentActivityState`. A notification
  is the *rising edge* into a notifiable state — never a new status engine. `bind`
  takes the `subscribe` callable, not the service, so the broker depends only on
  the bus shape and tests drive it with a bare stub.
- **`NotificationBroker.from_config(cfg.notifications)` is the single factory** —
  returns the broker or `None` (disabled / no channel). It is the one place
  config coerces to runtime: channel objects built from `_CHANNEL_TYPES` (the
  `(config-attr, channel-class)` registry — adding email/Slack/Teams is one tuple
  entry plus a config field plus the channel class, nothing else), and the `on`
  strings coerced to the state enum. There are deliberately no standalone
  `build_channels` / `resolve_notify_states` helpers — that logic belongs to the
  broker that owns it, not to root-level functions.
- **`evaluate` is pure; `dispatch` is the I/O edge — the split is the whole
  design.** `evaluate(delta)` folds one delta against per-session last-state +
  per-workspace debounce under a lock and returns `Notification`s, fully testable
  with a fake clock and hand-built deltas (which use the real engine IR, so a
  contract drift fails to compile). `dispatch` fans out, each channel in the
  best-effort guard.
- **The bus callback must never block or break the poll.** `_on_delta` runs
  `evaluate` synchronously on the emitting (executor) thread, then `submit`s
  `dispatch` to a single-worker `ThreadPoolExecutor` — channel HTTP never touches
  the activity path; a dead/slow sink degrades one delivery and is logged, never
  re-raised. (A `ThreadPoolExecutor`, not a hand-rolled queue+thread+sentinel:
  `shutdown(wait=True)` drains on close for free — less code, same semantics.)
- **Two storm guards, both load-bearing.** First observation of a session seeds
  its state *without firing* (else every already-finished workspace buzzes on
  daemon restart). After a fire the workspace is debounced for a window (else a
  `waiting`→`working`→`waiting` tool round-trip rings twice). Edge + debounce =
  one buzz per genuine attention episode.
- **`Notification` owns its own shape — construction, phrasing, rendering.**
  `from_activity` builds it from the activity row (the only place that knows
  `repo_name`/`deep_link` derivation); `REASONS` maps state→English AND *is* the
  notifiable set (a state absent from it can never fire — the broker intersects
  against `frozenset(Notification.REASONS)`, so adding a notifiable state is a
  one-line edit); `title()`/`body()` render once so channels stay dumb formatters.
- **`NotificationChannel` is an ABC, not a Protocol — the contract for every
  future sink.** One abstract action, `deliver(notification)`; `close()` a
  concrete overridable no-op (stateless sinks inherit it; HTTP ones override —
  `# noqa: B027` marks the empty body deliberate). The rule for divergent
  capabilities (attachments, threading): add the method here with a base body
  that `raise NotImplementedError`, sinks override what they support — keep "what
  a channel can do" in one readable contract, never capability flags at call
  sites. Today `deliver` + a rich `Notification` is the honest complete surface;
  resist speculative methods (YAGNI) until a real second action appears.
- **Channels mirror `MewboClient`'s boundary exactly.** Config holds the env-var
  *name* of each token (never the secret — committed config stays publishable);
  `httpx.MockTransport` is the test seam; every httpx failure narrows to a typed
  `NotificationError` subclass. Gotify deep-links via the
  `client::notification.click` extra; the webhook speaks ntfy's JSON-publish
  shape (`topic`/`click`/`tags`) so it doubles as a generic sink.
- **Config can't import the agents enum (cycle: agents → registry → adapters →
  config).** `NotifyTransition` is a `Literal` mirroring the `AgentActivityState`
  values; `from_config` is the single edge that coerces it back to the enum.
- **Web Push (VAPID/PWA) is the next channel (#75)** — it needs the webapp
  service-worker + subscription surface first, then drops in as one more
  `_CHANNEL_TYPES` entry with no broker change.
