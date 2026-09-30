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
- **A `grove.*` attribute is STORED and UNFINDABLE, so anything a consumer must
  SELECT on is written a second time, flat (`shell.py`, `semconv.filterable_identity`).**
  LangFuse files every attribute it does not recognise under `metadata.attributes`
  and every resource attribute under `metadata.resourceAttributes`, and its own
  docs state that only top-level `metadata` keys are filterable — so the whole
  identity vocabulary above was correct, present and matched nothing, which is
  the worst of the three states because the data is visibly there. The escape is
  the documented `langfuse.observation.metadata.<key>` prefix. **Dots are folded
  to underscores** on the way through: the vendor's filterable examples are all
  flat identifiers and a dotted key is exactly the shape its nesting rules use,
  so `grove.tool.category` risks re-creating the nesting the prefix exists to
  escape. The flat name is DERIVED from the attribute it mirrors, never a second
  hand-written table. The flat set is deliberately a third, smaller projection
  beside attributes and tags: attributes are the complete record, tags are what
  a human clicks, metadata is what an automated rule filters a cohort on.
- **A shell call is the one tool call worth normalizing across harnesses, and it
  is normalized in `SpanRecord.tool` so BOTH tiers get it from one place.**
  Claude spells it `Bash`/`command`, Codex spells it `exec_command`/`cmd`, and
  the native OTLP spells it `full_command` on the span with `bash_command` on a
  `tool.output` event (both measured in the checked-in capture). An evaluator is
  one rule with one variable mapping and cannot hold a per-harness case, so
  `telemetry/shell.py` publishes one category, one metadata block and stable
  `input.command` / `output.content` envelopes. **Classification is by exact
  provider tool NAME** (`agents/shell.py`, shared with the usage audit so the
  two cannot disagree) — never by substring, never by sniffing the payload, and
  never converting an arbitrary tool into a shell observation. The category is
  therefore also the EXCLUSION filter: an external hook exporting the same
  transcript beside Grove carries no category at all, so selecting on it is
  structurally safe without disabling anybody's hook.
- **Result evidence and process outcome are TWO keys, and success is asserted
  from a recorded exit code alone.** `empty` + `succeeded` is the case that
  forces the split: a command that printed nothing and exited zero is a
  complete, successful call, and one enum would have to drop one of those facts.
  The absence of a tool-error flag is never evidence — Codex records no
  structural flag on a shell result at all, so a published `false` there reads
  as "this worked" to any consumer that does not know which harness wrote it,
  and only `true` is ever emitted. `redacted` exists for the same reason at the
  other end: a content policy nulls the TEXT of a call that plainly finished, so
  folding it onto `pending` reported a redacting fleet as permanently
  mid-command. **Whether a result LANDED cannot be read off whether its text is
  present** — the caller holds the record and must say (`resolved`).
- **Clipping an ENCODED payload produces a document that is a valid attribute
  and invalid JSON, and nothing on the wire admits it.** `json.dumps(x)[:CAP]`
  cuts mid-token. Fitting now happens on the PAYLOAD before encoding, optional
  keys are shed before the graded text is cut, and the flag rides inside the
  payload (it only ever flips `false` → `true`, which shortens the encoding, so
  a payload measured as fitting cannot stop fitting when marked). **The search
  for the surviving prefix is exact rather than arithmetic**: JSON escaping is
  not length-preserving, so a budget from the unescaped length overflows on
  precisely the multi-line output the cap exists to carry. `ATTR_TEXT_CAP` is
  Grove's own conservative ceiling, not a vendor limit — LangFuse documents a
  5 MB request ceiling and no per-attribute limit — and it stays because a
  receiver rejecting one oversized span drops the batch it rode in.
- **Changing a span's attributes changes `TraceManifest.fingerprint`, which is a
  HISTORY decision, not a schema one.** `telemetry_backfill` compares that hash
  against a completed checkpoint and reports `degraded` on a mismatch: it never
  re-exports and never deletes. So a canonical-shape change applies to sessions
  replayed from here on and leaves history exactly as it was — which is the
  correct default, and the reason no reindex is needed. Do not "fix" the
  mismatch by clearing checkpoints.
- **A human rating joins the trace by the turn's `started_at`, never by an id
  the client holds (`feedback.py`, `trace.turn_trace_id`).** The wire turn's
  `started_at` and the replay's turn anchor are the same prompt timestamp
  (measured 25/25 on a live session), so the daemon re-derives the trace id with
  the replay's own derivation, and a client can never annotate a trace the replay
  would not produce. An OPEN turn rates too: its id derives from the anchor
  alone, so it is the id the replay exports once the turn closes. The seed is the LIVE replay's, without `source_id`. A
  backfilled session's traces are seeded with its source and a rating will not
  reach them, which is the right trade for a surface that rates live work.
  **Langfuse refuses an `ANNOTATION` score without its rubric's `configId`**
  (measured, not documented where you would look), so `record` looks the configs
  up by name and checks every one BEFORE the first write. Checking per score
  posted the verdict and then refused the reason, which leaves a half-recorded
  rating. Score ids derive from trace + name (+ reason), so a re-vote overwrites.
  An unticked reason is DELETEd by the same id, and deleting an absent score
  answers 200, so there is no 404 branch. A score names exactly ONE target:
  `traceId` plus `sessionId` is a 400, and the trace already belongs to its
  session. **Verify a score by `GET /api/public/v2/scores/{id}` with the
  derived id, never by listing.** On this self-hosted instance (2026-09-29) the
  list reported `totalItems: 0` for the whole project while GET-by-id returned
  the score, and the CLI's `scores list` targets a v3 path the instance 404s.
  "Nothing listed" there is a reader problem, not a lost write.
- **From 2026-09-16 to 2026-09-29, EVERY span Grove emitted raised inside
  `span.end()`, and nothing said so.** The OpenTelemetry SDK began calling a
  span processor's `_on_ending` hook; `_AcknowledgingSpanProcessor` is
  duck-typed (subclassing `SpanProcessor` would import the SDK at module scope
  and break the lean install), so it did not inherit the hook's no-op.
  `replay()` swallows exceptions by design, so the replay and context tiers
  simply went dark: no `agent-turn` traces, every rating pointing at a trace
  Langfuse never received ("Trace not found"). The local collector had also
  been refusing data under memory pressure that same day, which made the host
  look like the cause. **The discriminator was running the real sink
  in-process against one real turn** — the crash is the first line of output,
  while the logs held nothing. Every processor test had fed `on_end` a bare
  `object()`, so no real span ever passed through the class. The guard now
  drives a real span through the production `sink_from_processor` +
  `_AcknowledgingSpanProcessor` pairing, so the NEXT hook the SDK adds fails
  there instead of in silence. **A best-effort swallow on the only path that
  exercises a seam turns an SDK upgrade into a silent outage; the test for such
  a seam must go through the swallowed path's real objects.**
- **`langfuse.user.id` is the host account, set once per daemon.** Traces
  reached Langfuse with no user, so "whose work is this" had no answer on the
  vendor's own user axis. It is an attribute, never a tag (high cardinality),
  and only Grove's own spans carry it — the tier-1 resource carries no
  `langfuse.*` keys at all.
- **Praise and complaint are two rubrics, and a flipped vote withdraws the old
  verdict's reasons.** `user-feedback-praise` exists beside
  `user-feedback-reason` because a category's meaning must not depend on which
  way the thumb pointed; one rubric answering both could not calibrate a judge.
  `withdrawn` walks BOTH catalogs minus what was just ticked, so a turn rated
  down with a reason and then up does not keep the complaint.
- **A generation span is zero-width and that is the honest answer, so do not
  "fix" it.** A transcript stamps a message once, when it was written, and
  carries no request-start or first-token time; tool spans get real durations
  because a resolving `tool_result` has its own timestamp. Model latency is only
  knowable from a tier that watches the call happen — which is precisely the
  TTFT cost the telemetry reservation makes explicit.
