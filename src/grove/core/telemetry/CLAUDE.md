# grove.core.telemetry — one shape on the wire, whoever produced it

> ↑ [grove.core](../CLAUDE.md) · [root](../../../../CLAUDE.md)

Grove is an OpenTelemetry **gateway**, not a passenger. Everything a workspace
emits — Grove's own transcript replay, the live context tier, and a harness's
own OTLP re-exported through the receiver — is spelled from one vocabulary so a
fleet of mixed harnesses lands as one shape. This file owns the collection,
export and ingestion decisions; the LAUNCH boundary that decides whether an
agent exports at all (`_launch_spec`, `TelemetryConfig.reserve`) stays in
[grove.core](../CLAUDE.md), because that is manager and config territory.

**The three tiers, and why each exists.** Tier 1 stamps identity into the
agent's launch environment (`otel_resource.py`) — frozen at process start, by
construction. Tier 2 is the live context span on the activity bus
(`trace_forwarder.py`), carrying everything a frozen resource attribute cannot:
the phase, the branch actually created, tickets attached later. Tier 3 is the
transcript replay (`trace.py`), the only tier that can reconstruct a session
that already happened. The OTLP receiver (`receiver.py`) is now **mounted** in
the daemon, default OFF behind `telemetry.receiver.enabled`.

**Mounting it turned out to be one `app.mount(...)` and two things that fail by
looking like success.** A mounted sub-app's own lifespan does NOT run —
Starlette drives only the outermost app's — so `OtlpIngest.aclose()` is driven
from the daemon's own lifespan; and because `submit()` self-starts, a mount
missing that drain works perfectly right up until shutdown, where it silently
drops whatever was buffered. It also needed a new `_aclose_best_effort`, because
the existing helper takes a *sync* callable and handing it a coroutine-returning
`aclose` builds the coroutine and discards it unawaited — which drains nothing
and raises nothing. Separately, `receiver.py` imports `opentelemetry-proto` at
MODULE scope (unlike `trace.py`'s lazy imports), so the import is deferred inside
the enabled branch: a module-level one breaks a lean `[daemon]` install — which
omits the `telemetry` extra — at `import app`, with the receiver switched off.
**Both bugs are invisible to a test that only asserts the route answers.**

The mount is left unauthenticated on purpose: the daemon's loopback-only bind is
the same load-bearing constraint that defers auth on every other route, and the
agents posting here are local processes holding no daemon session token.

**The measurement this subsystem was reshaped by (2026-08-11).** Against the
live LangFuse instance, 300 traces: **266 (89%) carried neither input nor
output**, and 48 of 50 sampled carried no tags at all. By producer — Grove's own
context traces 119 (all blank), `claude_code.*` native OTel 105 (all blank), the
Stop-hook exporter 30 (none blank), Grove's replay 9 (all blank, from a daemon
running code older than the fix). A session view is the only way into that
instance, so a blank row is work that was recorded and cannot be found.

- **Historical telemetry is append-only even though agent sessions resume.** A
  completed human turn is the trace boundary and `langfuse.session.id` groups
  those traces into the native agent session. Generations parent tools; Claude
  sub-agent roots parent to their spawning tool, **recursively and with no
  depth bound** — the walk consumes each spawning call id once, which is what
  makes it terminate on a malformed sidecar, and visits ids in SORTED order
  because every downstream span id is derived from them and set iteration
  would make two replays of one transcript disagree. Trace-wide filter
  attributes propagate to every observation. Live context changes are
  zero-width revision spans that all share ONE per-session context trace;
  deriving the TRACE id from the revision instead minted a whole single-span
  trace per attribute change (8 of 14 traces in one live session), so the
  revision belongs in the span id and never the trace id. A restatement-only
  key must stay out of the change GATE while still being emitted — a phase
  file re-saved with an unchanged phase moved a timestamp and bought a
  revision for nothing. Deterministic ids support reconciliation; they do not
  make OTLP itself idempotent.
- **One vocabulary, written twice, defined once (`telemetry/semconv.py`).** Every
  span carries both the OpenTelemetry GenAI convention's keys and the vendor's,
  because portable and readable are different keys *today*: the convention
  specifies `gen_ai.input.messages`, LangFuse maps `gen_ai.prompt`, and picking
  one buys either lock-in or a blank UI. **The convention strings are literals
  with a test asserting them equal to the SDK's**, not an import: `opentelemetry`
  is the optional `telemetry` extra and `trace.py` imports it lazily precisely so
  `grove.core` stays importable without it, so a module-scope import of the
  pre-stable `semconv._incubating` would undo that for every consumer of the
  vocabulary — and that path is one the OTel project has said will move. CI has
  the extra, so drift fails there. **A `grove.*` key that identifies a workspace
  must be emitted by BOTH this module and `otel_resource.compose_resource_attributes`**
  (asserted by test): the agent's own spans and Grove's describe one workspace,
  and a filter that finds half of it is worse than one that finds neither,
  because the half looks complete.
- **A trace LangFuse lists with no input and no output is one a human never
  opens, so "has content" is a navigation property, not a nicety.** Measured
  against the live instance on 2026-08-11: **266 of 300 traces (89%) were blank
  on both**, and the single largest producer was Grove's own — 119 context
  traces, every one of them empty, 40% of everything on the page. A session view
  is the only way into that instance, so a blank row is work that was recorded
  and cannot be found. The context span therefore renders a brief (title,
  description, tickets) and a standing report (status, phase, todo counts).
  **Every line of it is Grove's OWN record and not one word is transcript** —
  `current_task` is the near miss, and including it would have made the
  `content_owner` gate conditional on which axis a payload arrived through,
  which is not a gate. A test enumerates the fixture's payloads and asserts each
  is absent, because an absence assertion is only as strong as its list.
- **Identity rides EVERY span, not the trace root, and the reason is the
  consumer's aggregation model.** LangFuse filters and aggregates across
  individual observations rather than only at the root, so a trace-scoped fact
  present on the root alone is one a reader cannot narrow by — their own
  guidance is to propagate. `TraceIdentity` (in `semconv.py`) is the one object
  both the live context tier and the transcript replay spend, which is what
  stopped the replay carrying the session join key and nothing else: the richest
  tree Grove produces was the one nobody could filter by repo, branch or agent.
- **Tags are a PROJECTION of those attributes, never a second vocabulary.**
  `langfuse.trace.tags` is the one surface a human narrows by clicking instead of
  by writing a query, and a tag set assembled independently drifts from the
  attributes it claims to mirror; a test asserts every `prefix:value` tag names a
  fact some attribute states. The tag table is deliberately a SUBSET of the
  attribute table — a worktree path and a free-prose title stay queryable but
  would bury every useful facet in a list nobody can scan. Tags sort, so two
  replays of one session cannot render as visibly different traces.
- **`langfuse.trace.tags` is specified as `string[]`, which makes it the one
  attribute Grove writes that is not a scalar — and widening `AttributeValue`
  without widening `_otlp`'s copy was a latent protobuf crash.** A tuple fell
  through to `string_value`, which protobuf rejects, reachable only from the
  re-export tier and only for a span Grove had stamped tags onto. `apply`
  overwrites in place, so the array arm must CLEAR before filling: a tag list
  that grows by one copy of itself per export is a bug that appears only under
  retry. The two unions are contracted as identical; widen them together.
- **Provenance belongs ON the observation, because the tree walk is exactly what
  a list view cannot do.** Every generation and tool span carries its owning
  agent's id, name and depth (`_SpanOwner`, applied once at the single point
  every descendant leaves the builder). A fleet's tool calls all render as
  siblings in a list, and without an owner on each the only way to tell a
  sub-agent's `Read` from the root's is to open both. Applying it at one exit
  rather than per constructor is deliberate: provenance some records carry and
  others do not reads as a fact about the call rather than as a missed call site.
- **A replay that drops a sub-agent drops CONTENT, not just an edge — and the
  measurement said the edge is often simply not written down.** Census of 2592
  real sidecars, 2026-08-11: 1025 are genuine sub-agents (`spawnDepth >= 1`),
  747 carry a `toolUseId` and attach, **278 carry none — and only 8 of those 278
  carry `parentAgentId` either.** So "correlate the rest by `parentAgentId`"
  recovers eight threads, not the class; there is no second key waiting to be
  read. **Every depth-2 and depth-3 thread attaches**, so nesting was never the
  problem — the whole gap is at depth 1. A thread nothing attaches never becomes
  a span and its messages sit in the transcript as sidechains nobody replays: on
  one real session that silence was **1434 of 2420 messages, 59% of the work**,
  absent from the trace entirely. `_Fleet` splits the two halves at the read;
  the adrift ones are placed in the turn their own start instant falls in,
  parented to the turn root, against the turn's ORIGINAL bounds so an attached
  grandchild widening the root cannot pull in an unrelated thread. They carry
  `grove.agent.attachment` (`spawn_tool` | `turn_window`), because one is
  evidence about causation and the other is a clock, and a reader comparing two
  sub-agents needs to know which they are looking at.
- **A generation span is zero-width and that is the honest answer, so do not
  "fix" it.** A transcript stamps a message once, when it was written, and
  carries no request-start or first-token time; tool spans get real durations
  because a resolving `tool_result` has its own timestamp. Model latency is only
  knowable from a tier that watches the call happen — which is precisely the
  TTFT cost the telemetry reservation makes explicit.
