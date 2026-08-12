# grove.core.notifications — push on workspace edges

> ↑ [grove.core](../CLAUDE.md) · [root](../../../../CLAUDE.md)

Turns the existing activity stream into push notifications: *deliver a
notification when a workspace crosses into a state that wants the human*. The
broker decides; channels deliver. Off by default (`cfg.notifications`).

One concern per type, no free functions and no module-level factories:
`WorkspaceIdentity`, `NotificationReason` (the phrase + the urgency, together),
`Notification`, `NotificationChannel` (the ABC contract), `NotificationBroker`
(the edge engine + the one factory).

## The engine seam

- **Reuse the activity bus; add NO status computation.** The broker subscribes
  via `bind(ActivityService.subscribe)` exactly like `_SseHub` and reads each
  session's already-blended `AgentActivityState`. A notification is an *edge* in
  what already flows past it — never a new status engine. `bind` takes the
  `subscribe` callable, not the service, so the broker depends only on the bus
  shape and tests drive it with a bare stub.
- **`evaluate` is pure; `dispatch` is the I/O edge.** `evaluate(delta)` folds one
  delta against the memory under a lock and returns `Notification`s, fully
  testable with a fake clock and hand-built deltas (which use the real engine IR,
  so a contract drift fails to compile). `dispatch` fans out, each channel in the
  best-effort guard.
- **The bus callback must never block or break the poll.** `_on_delta` runs
  `evaluate` synchronously on the emitting (executor) thread, then `submit`s
  `dispatch` to a single-worker `ThreadPoolExecutor` — channel HTTP never touches
  the activity path; a dead or slow sink degrades one delivery and is logged,
  never re-raised. Not a hand-rolled queue+thread+sentinel: `shutdown(wait=True)`
  drains on close for free.
- **`NotificationBroker.from_config(cfg.notifications)` is the single factory**,
  returning the broker or `None` (disabled / no channel), and the one place
  config coerces to runtime. Channels are built from `_CHANNEL_TYPES`, the
  `(config-attr, channel-class)` registry — **adding a channel is one tuple
  entry, one config field and the channel class, nothing else**.
- **The broker lives in the daemon only** (`daemon/app.py` lifespan). The TUI runs
  its own in-process `ActivityService` and never builds one: push wants a
  long-lived process. A TUI-only user gets no notifications — a deliberate
  boundary, not an oversight.

## The three triggers

Each trigger has its own detector, its own storm guard and its own config
switch; all three produce the same `Notification`, so channels stay dumb.

- **Agent-state edge** (`_state_edge`, `cfg.on`) — the *rising edge* into a
  notifiable state (WAITING = turn finished, BLOCKED, ERROR, IDLE).
- **Question edge** (`_question_edge`, `cfg.on_question`) — **the harness-parity
  seam, and the reason the subsystem is not Claude-only.** A question is a
  *content* signal (`AgentActivity.questions`, the provider-neutral
  `AgentQuestion` every adapter normalizes its native ask-the-human tool onto),
  whereas BLOCKED is a *state* only some adapters can ever reach — Codex's
  approval prompts are interactive and never persisted to its rollout, so
  `codex.py` has no BLOCKED-producing code path at all. Firing on the question
  itself means **any** harness that surfaces one gets the push, with the prompt
  and options in the body, without a line of per-provider code.
- **Lifecycle edge** (`_evaluate_lifecycle`, `cfg.on_lifecycle`) — the
  `workspace_changed` deltas: a create that failed at `init_script`, a tmux
  session that vanished under a running agent, a worktree deleted underneath a
  workspace. This is the "the work was *interrupted*" arm, as opposed to "the
  work *finished*". The default set is exactly the unexpected half; the routine
  verbs are phrased and available but off — a user-initiated pause needs no push
  back to the user who initiated it.

**Storm guards, each matched to its trigger:**

- First observation of a session **seeds silently** (state *and* open-question
  ids), or every already-finished workspace and every already-open question
  buzzes on daemon restart.
- **…but "unseen session" is the wrong test for that, and using it cost every
  brand-new workspace its first question push.** The storm is a property of the
  BROKER (it restarted), not of a session — and a session opened by `create` is
  unseen for exactly the same reason a pre-restart one is. So an agent that read
  its task and immediately asked something seeded silently and never fired,
  which is the case the human most needs. `_is_cold` gates the question seed on
  a broker **warm-up window** opened at the first fold; after it, an unseen
  session is genuinely new and its open questions are owed. **A window is right
  here even though the dedupe next door refuses one**: dedupe is exact because
  "same question id" is exact, whereas a restart burst has no exact marker in
  the data and is inherently a span of startup time. Generalizable: when a guard
  defends against a lifecycle event, key it on that lifecycle, not on a proxy
  that merely correlates with it.
- A state edge is **debounced per workspace** (a WAITING→WORKING→WAITING tool
  round-trip must ring once, not twice).
- A question is **deduped by question id and deliberately NOT debounced.** Time
  is the wrong guard: two questions 5 seconds apart are two answers the agent is
  blocked on, and swallowing the second strands it while the human believes they
  are done. Re-asking the *same* id can never re-fire, so the guard is exact
  rather than temporal — strictly stronger than a window.
- **A question outranks the state edge behind it** — they are the same attention
  episode (an agent is BLOCKED *because of* the question) and the question push
  is strictly richer, so it wins and stamps the debounce that keeps the state
  edge quiet. Without this you buzz twice for one event.
- **…but suppressing an edge means RECORDING it, not out-voting it.** Every
  detector *folds its own memory* on every tick, so `_evaluate_session` runs
  **both** `_state_edge` and `_question_edge` and only then picks the winner. An
  early `return` on the question would skip `_state_edge` and leave that
  session's last-seen state stale at WORKING — so the WORKING→BLOCKED edge stays
  *pending* and rings a second, redundant bare "needs your input" push the
  instant the debounce window lapses. **A short-circuit that skips a stateful
  detector is a latent duplicate notification**; a fourth trigger must be run for
  its memory and chosen afterwards.
- The identity cache (`_identity`) exists because **a lifecycle delta carries no
  activity row** — only an id and the manager's `detail`. The broker caches the
  `WorkspaceIdentity` it learns from activity deltas;
  `WorkspaceIdentity.unresolved` is the honest fallback for a workspace that
  broke *before* it ever reported activity (a create that died at
  `worktree_add`), and it still deep-links.

## The event and the contract

- **`Notification` owns its own shape** — three classmethod constructors, one per
  trigger, and three renderers: `title()`, `body()` (plain), `markdown()` (rich).
  `REASONS` / `LIFECYCLE` map state/event → `NotificationReason` AND *are* the
  notifiable sets (the broker intersects its config against them), so adding a
  notifiable edge is a one-line table edit with no other coupling.
- **An ERROR push renders `error_detail`, never the task line.** `current_task`
  is *what the agent was doing*, which next to the word "error" is actively
  misleading; `error_detail` is *why it broke*. Gated on the edge's own state,
  not on `error_detail` merely being non-empty — a recovered session can still
  carry a stale detail, and a finished turn must never read as a failure.
- **Severity is the abstraction that keeps channels dumb.** The *event* decides
  how much it matters (`low`/`normal`/`high`/`urgent`); each *channel* maps that
  onto its own native scale **in config** (Gotify's 0-10, ntfy's 1-5). Without
  this axis every sink re-implements "is this important?" keyed off the state
  enum — four sinks, four drifting policies, a hard-coded number in a branch.
  Pairing the phrase and the urgency in one `NotificationReason` entry is what
  stops the two tables drifting apart.
- **`NotificationChannel` is an ABC, not a Protocol — the contract for every
  future sink.** One abstract action, `deliver(notification)`; `close()` a
  concrete overridable no-op (`# noqa: B027` marks the empty body deliberate).
  For divergent capabilities (attachments, threading): add the method here with a
  base body that raises `NotImplementedError` and let sinks override what they
  support — never capability flags at call sites. A sink renders what it can and
  ignores the rest; it never re-derives a phrase, a priority or a URL.
- **Channels mirror `MewboClient`'s boundary exactly.** Config holds the env-var
  *name* of each token, never the secret (committed config stays publishable);
  `httpx.MockTransport` is the test seam; every httpx failure narrows to a typed
  `NotificationError` subclass.
- **Config can't import the agents enum (cycle: agents → registry → adapters →
  config).** `NotifyTransition` / `NotifyLifecycle` / `NotifySeverity` are
  `Literal`s mirroring the engine's values; `from_config` is the single edge that
  coerces them back.

## The deep link IS the feature (and its quietest failure)

- **A push without a reachable tap target is worth nothing.** The link is
  `{deep_link_base_url}/w/{id}` — the webapp's workspace route, where the human
  can read the transcript *and answer the question*.
- **`deep_link_base_url` defaults to `http://localhost:3000`** (the webapp's own
  `next start` origin). An empty default renders **no link at all**, i.e. the
  feature is dead by default.
- **But loopback is a silent trap: a phone resolves `localhost` to *itself*.** The
  push lands, the tap dies, nothing errors, no log. So the *config model answers
  this about itself* — `NotificationsConfig.deep_link_is_loopback` (pure,
  `urlparse`, no I/O) — and `from_config` logs one warning at construction. A LAN
  address is deliberately NOT loopback: a phone on the same network can follow it.
  Validate at the point of definition, act at the edge — the failure is otherwise
  undiscoverable except on a lock screen.

## The rising edge is a BAND, not a transition

`_is_rising_edge` fires on crossing **into** the notifiable set (`cfg.on`) from
**outside** it, and a first sighting seeds without firing. So `BLOCKED → WAITING`
never fires under the default set: both are notifiable, and the human is already
being asked for. The agent must pass back through WORKING (which is exactly what
happens in life: you answer, it resumes, it finishes).

Consequence for any harness driving the broker: a scripted
`WORKING → BLOCKED → WAITING → ERROR` sequence produces **2** pushes, not 4, and
that is correct. Interleave the working state between episodes or the harness
will "lose" notifications that were never owed.

## WAITING is not "done" — the quiet-window push

**The spam was one specific edge, found from the code, not guessed:**
`_state_edge`'s rising edge into WAITING (`_DEFAULT_NOTIFY_ON` includes
`"waiting"`), which fires once per attention episode already — the complaint
"a push on every turn" is what that edge is SUPPOSED to do when a session
genuinely alternates WORKING↔WAITING every debounce window, because a
transcript's WAITING means only "the top-level turn stopped generating," not
"the task is finished." An async Task/Agent spawn, an in-session hook fleet
worker, or a queued follow-up message can all still be running behind it.

**Grove already computed three "still working" signals before this feature
existed, and none needed a new subsystem:** `AgentActivity.active_subagents`
(transcript-derived, an Agent/Task `tool_use` spawned but not yet returned),
`WorkspaceActivity.fleet` (the hook's live per-agent push, sees a worker before
its own transcript file exists), and `WorkspaceActivity.queue` (the harness's
own steer queue, `WorkspaceQueueView`'s live count). `NotificationBroker._is_busy`
is their OR; while true, WAITING is fed to the edge detector AS WORKING
(`_effective_state`), so the rising edge simply hasn't happened — no second
notion of "state" exists anywhere else to keep in sync.

**None of the three can see a backgrounded shell command**
(`Bash ... run_in_background`): the tool call's own result returns
immediately, so there is no open call left to count. `cfg.waiting_quiet_minutes`
(default 15) is the user's own proposed fallback for exactly that gap — once
every known tracker agrees nothing is left, the push is *parked*
(`_pending_quiet`) rather than fired, and only dispatches once that much real
wall-clock time has passed with **nothing un-settling it** (a resumed
WORKING, a BLOCKED/ERROR, or a fresh question all cancel the parked entry —
see the cancellation clause in `_state_edge` and the pop in `_question_edge`).
`waiting_quiet_minutes: 0` disables the wait and restores instant-fire, with
the busy-gate still applied — it is a genuinely independent knob, not a
special case of the other.

**The three idempotence properties all fall out of the existing cold-seed
rule, not new bookkeeping:** `_last_state[session_id]` is `None` on the very
first post-restart observation regardless of the session's real state, and
`_is_rising_edge` refuses to fire (or queue) when `previous is None` — so a
workspace already WAITING/settled before the broker (re)started can never
enter `_pending_quiet` from that first observation, no matter how long it had
already been quiet. Only a genuine transition seen AFTER the broker came up
can queue one, which is also what makes "fires once" hold: the entry is
popped on dispatch and only a fresh WORKING→settled cycle re-arms it.

**The delayed dispatch needed a real timer, and that is the one place this
diverges from `evaluate`'s purity contract.** `evaluate()` only runs when a
delta arrives, and the whole point of the quiet window is to catch a
workspace that produces **no further deltas at all** — nothing on the bus can
ever wake the broker to check one. `due_quiet(now=...)` stays pure (mirrors
`evaluate`, testable with a fake clock, called from `_state_edge`'s memory
mutation without spawning anything); the one genuinely new I/O is
`bind()`'s self-rescheduling `Timer`, armed lazily by `_on_delta` only while
`_pending_quiet` is non-empty and disarmed by `_quiet_tick` the moment it
drains — an idle fleet costs one dict check per delta and no thread at all.

## Gotify: the facts worth not re-deriving

Observed against the server's OpenAPI spec and the Android client's Kotlin
source, then confirmed end-to-end against a live server — the official docs
under-specify most of this.

- **`POST /message` is the entire integration.** Auth via `X-Gotify-Key`,
  `Authorization: Bearer`, or `?token=`. Body: `message` (required, markdown
  allowed), `title`, `priority`, `extras`. Errors 400/401/403. **No rate limiting
  exists anywhere in the server** — none to design around.
- **Exactly four `extras` keys are honored, and only one of them by the web
  client:** `client::display.contentType` (`text/markdown` — Android renders via
  Markwon/CommonMark, web via react-markdown/GFM; **HTML is never rendered**, so
  never emit any), `client::notification.click.url` (**Android only** — the web
  client has no code reading it, which is *why* `markdown()` also renders the deep
  link inline: one link, two ways to reach it), `client::notification.bigImageUrl`
  (Android only), and `android::action.onReceive.intentUrl` (fires *on receipt*,
  not on tap, behind a user prompt — **not** an "open on click" mechanism; do not
  reach for it).
- **Priority is a real dial, not decoration.** The Android client bins it: `<=0`
  min, `1-3` low/silent, `4-7` default (vibrate), `>=8` high (heads-up + sound).
  The web client only tints the message's left border. Our defaults are chosen
  against those bins, so "question" (8) actually wakes the phone and "paused" (2)
  never does.
- **An application token (`Axxx…`) can only send — PROVEN, not inferred.** A live
  `DELETE /message/{id}` with the app token returns **401**; `POST /application`
  and image upload require a *client* token (`Cxxx…`); and **there is no PUT/PATCH
  on messages at all** — no update, no supersede. So "retract the needs-input push
  once the agent moves on" is *not implementable* with a send-only token, and we
  do not fake it. It would need a client token plus tracking the message id the
  POST returns.
- **The POST echoes the stored `Message` back** (id, appid, priority, extras,
  message) — the cheapest ground truth there is. Assert against it before
  believing any future claim about this API.
- **One app token = one Gotify application**, and the clients group messages by
  application — so a dedicated "Grove" application is the grouping mechanism;
  there is nothing to build.
- **Depend on no Gotify SDK.** The only real PyPI package is Alpha-status and
  wraps this one endpoint over the `httpx` we already have; direct `httpx.post` is
  fewer lines than the import, and it is the same boundary discipline as
  `core/mewbo.py`. Do not re-open this.

## The webhook sink

Carries both renderings (`message` plain, `markdown` rich) plus the structured
facts (`severity`, `trigger`, and the open `questions` with their ids and
options) so a relay can render — or eventually *answer* — rather than re-parse
prose. `priority` is ntfy's own 1-5 integer, mapped from `severity` through
config: never put a state string in an integer field.
