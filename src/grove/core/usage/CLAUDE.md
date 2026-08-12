# grove.core.usage — the historical audit: tokens, time, cost and quota

> ↑ [grove.core](../CLAUDE.md) · [root](../../../../CLAUDE.md)

The past-tense sibling of `activity.py`. `ActivityService` answers *what is
happening right now* over a ~1 Hz delta; this package answers *what happened,
across every account and project Grove can reach*, over bounded request reads.
Nothing here rides the SSE stream and nothing here re-implements a parser.

Children: [quota](quota/CLAUDE.md) (subscription windows + billing posture).

## What this package is NOT, and why that is the whole design

Every one of these was cheap to build and would have been wrong. Re-open one
only with a measurement.

- **Not a second parser.** The projector consumes the existing
  `AgentMessage`/`ContentBlock`/`TokenUsage` spine through the adapters'
  `read_messages`, behind the existing `TranscriptCache`. Every trap in
  [agents](../agents/CLAUDE.md) — Claude's one-line-per-content-block split,
  Codex's dual records and cumulative token reports, the preamble-before-cwd
  scan — is already solved there, and a fresh `json.loads` loop re-acquires all
  of them silently.
- **Not a second session catalog.** `SessionCatalog` already enumerates
  host-wide and already resolves cwd → repo with the cheap walk-up `.git` stat.
- **Not a live dashboard.** `/activity` and `/events` keep their payload and
  cadence exactly; a historical query that widened them would put unbounded
  analytics on the poll path.
- **Not a transcript renderer.** A session row links to the read-only detail
  route Grove already serves, by its exact `(kind, cwd, id)` coordinate.
- **Not a cloud service, an arbitrary-SQL endpoint, or an LLM interpreter.**

## The cache is a cache — that is a licence, not a caveat

The SQLite file under the state dir is derived wholly from transcripts Grove can
re-read. Three consequences follow, and they are why this subsystem is small:

- **A schema-version mismatch REBUILDS; there are no migrations.** Migration
  code is the most dangerous code in a persistence layer, and here it would
  guard data reproducible in seconds. `_schema.SCHEMA_VERSION` must be bumped on
  any column change — a forgotten bump is a cache serving one shape while the
  code reads another, which surfaces as inexplicably empty cards rather than as
  an error.
- **Deleting the file is always safe**, so retention and purge are ordinary
  operations rather than data loss.
- **No lock discipline from `paths.exclusive_lock`.** SQLite in WAL mode owns
  its own concurrency and there is one writer path. This is the one state file
  in the tree that opts out, and the reason is that it is the one state file
  nothing authors.

## Three honesty rules, encoded in the TYPES

Each was a way for a usage dashboard to be confidently wrong, so each is refused
in [contracts/usage.py](../contracts/CLAUDE.md) rather than left to a client.

- **Token classes never fold.** Fresh input, cache read, cache creation,
  reasoning and output stay apart, every one nullable. Cache reads cost roughly
  an order of magnitude less than fresh input, so a summed `tokens_in` hides the
  only number that moves a bill. **An absent count is NULL, never 0** —
  `SUM` over all-NULL yields NULL, which the wire renders as *not reported*.
- **A duration is THREE numbers and a confidence — one interval set, two
  reducers.** `elapsed_span_ms` (birth → last event) is real, cheap, and not how
  long anyone worked; a UI showing only it reports an overnight session as
  sixteen hours. The other two come from the same generation/tool intervals,
  which span the root agent AND every sub-agent thread: `active_ms` **merges**
  them (a wall clock, concurrency counted once) and `execution_ms` **adds** them
  (a labour total). `_intervals.ActiveIntervals` is that one pure reduction,
  shared by the projector and the range-bounded query so they cannot drift.
  **Grove shipped the sum under the union's name**: measured 2026-08-11 on one
  real fleet session (2997 messages, 1849 sidechain across 13 sub-agent
  threads), it published **6.93 h of "active" work inside a 4.56 h lifespan** —
  concurrency factor 2.22x, a duration longer than the session containing it.
  `active_ms <= elapsed_span_ms` and `active_ms <= execution_ms` are the
  invariant; if the first ever breaks again, the union has become a sum.
- **Money is a decimal string carrying its provenance.** Binary floats cannot
  hold ordinary decimal money exactly and these are summed over thousands of
  sessions. A subscription session's token cost is `estimated` and hypothetical
  — never presented as billed cash — and a model with no configured price is
  `unknown`, never `$0`.

Coverage rides every aggregate for the same reason: without it, a page rendered
from a half-indexed store is indistinguishable from a complete one, and the
operator's conclusion is wrong for a reason nothing on screen can reveal.

## Day buckets belong to the READER's timezone

**A `day` column is deliberately not stored.** A calendar day depends on the
zone the reader asked for, so a stored one answers every request in whatever
zone the indexer happened to run in. Buckets come from `_store.day_boundaries`,
which generates exact per-day epoch boundaries with `zoneinfo` and joins them in
SQL. A fixed hour offset — the obvious shortcut — is wrong for events in the
first or last hour of a day across a DST transition, in exactly the direction
nobody checks. Both the web graph and the TUI heatmap consume the same helper,
which is what makes "the two surfaces agree for the same tz and metric" a
property of the code rather than a test that happens to pass.

Timestamps are stored as epoch INTEGERs, not ISO text: every query over them is
arithmetic, and text dates turn each into a string operation whose index only
helps lexicographically.

## Incremental ingest — persist the pre-read fingerprint

Keyed by `(path, inode, size, mtime_ns)` with a byte `cursor`. Any growth or
replacement triggers a complete adapter-spine reread and atomic session
replacement; usage code never parses a JSONL delta itself. The stored cursor is
the complete-line byte boundary for cache evidence and future adapter-owned
incremental work. A vanished member prunes that session's derived rows.

**Persist the fingerprint captured immediately before the adapter read.** The
post-read stat is only a vanish check. If a writer appends after the adapter has
finished reading but before projection commits, blessing that later size as
ingested permanently skips unread bytes; retaining the pre-read size guarantees
the next refresh sees a mismatch and replaces the session from the adapter
spine. A retention-policy change force-rebuilds unchanged files once, so
relaxing the window can restore rows previously pruned by policy.

Idempotency is *replace, not append*: re-ingesting a file deletes its rows by
`(session_id, source_id)` before inserting. Verified by the invariant test that
deleting the whole database and refreshing reproduces identical rows.

## Shell time is attributed to the LEADING command, and the label says so

`usage_events.target` names the file a tool touched — and a shell call touches
none, so the column was NULL for every `Bash`/`exec_command` row and "which
processes eat the agent's time" had no evidence behind it. It now carries the
normalized **leading top-level executable**, filled at projection time by
`_command.py::LeadingCommand` and ranked by `insights.py::BashCommandRanking`.
Every choice below came out of a census of 112,300 real commands from this
host's two corpora; re-open one only with a new measurement.

- **The attribution rule is forced by arithmetic, not taste.** Crediting every
  executable in a call inflates attributed time to **3.05x** true wall clock
  (a second estimator over 93,376 correlated Claude calls put it at 4.0x —
  different arithmetic, same verdict), so the whole call's duration goes to the
  leading command alone. It is the only reducer whose sum equals the clock, and
  it does: **747,432 s attributed plus 3,139 s unattributed against 750,571 s
  of measured wall clock, exactly.** Make that equality the regression test for
  any change here. The consequence is that
  `find … | xargs pylint` is credited to `find`, which is why the number is
  labelled *"time in shell calls led by X"* and never *"time spent in X"*. The
  bias is directionally right (producers dominate cost) and, unlike the
  alternative, it is a bias a reader can be told about.
- **Two counters ride the wire because without them the ranking is a lie.**
  A duration at the harness's ~600 s timeout ceiling says when the tool gave up
  (`censored_calls`, threshold in `usage.commands.censored_at_ms`), and ~1-3%
  of calls are `run_in_background`, where the result returns a handle at once
  so long work is biased DOWNWARD (`background_calls`). Both are per-row, both
  unconditional. `unattributed_calls`/`_ms` is the third: a line that is only
  builtins, only a loop header, or that no road could read is 0.6% of the
  corpus and is REPORTED, never dropped into the ranked rows' denominator.
- **A count is only half a measurement; the fourth counter carries the other
  half.** `error_calls` shipped divided by `calls`, and **Codex records no
  structural tool-error flag at all** — re-measured 2026-08-11, its 31,373 real
  `function_call_output` records carry exactly `{type, call_id, output}` and
  nothing error-shaped, against Claude filling `is_error` on 1,067 of 49,505
  `tool_result` blocks. So every Codex call inflated the denominator and could
  only ever add 0 to the numerator: each row's published failure rate was
  deflated by that row's OWN silent share, which varies per command, so no
  single caveat could describe it and nothing on screen could reveal it.
  `error_reportable_calls` is the honest denominator, and a row where it is 0 is
  *unmeasurable*, not clean — a client must render that as "not measured" and a
  genuine zero over a capable population as `0%`, which is the
  unmeasured-is-never-zero rule pointed in BOTH directions at one column.
  **The capability is the ADAPTER's to declare** (`AgentAdapter.reports_tool_errors`,
  the `reports_queue` shape) and `insights.ERROR_REPORTING_PROVIDERS` derives the
  provider set from `all_adapters()`; a provider-name list in this module's SQL
  would be policy in code, correct only until a harness changes and with nothing
  to fail. **Generalizable: before publishing a rate, ask whether every member of
  the denominator could have contributed to the numerator** — a population that
  structurally cannot report the event is not evidence that the event did not
  happen. It needed **no `SCHEMA_VERSION` bump**: `sessions.provider` was already
  stored and no column's meaning moved.
- **Naive splitting is not fit to ship, and the `shlex` middle road is the
  worst of the three.** 59.6% of real commands hold a pipe, 56.0% an
  `&&`/`||`/`;`, 27.0% are multi-line and **34.3% carry an operator character
  inside a quoted span**. Hand-checked on 100 stratified-random commands: naive
  first-token ~31% correct, a careful `shlex` segmentation ~79% **and never
  signalling failure**, `bashlex` effectively 100% wherever it parses and loud
  otherwise. A road that cannot say "I could not read this" makes its 21%
  indistinguishable from its 79%.
- **A heredoc goes STRAIGHT to the fallback, and the fallback strips heredoc
  BODIES first.** bashlex cannot parse a quoted delimiter (`<<'EOF'` is 99.2%
  of this corpus's style), so 99.0% of heredoc commands raise there — trying
  first buys nothing. And a body is prose: a markdown table is a wall of `|`,
  which an operator scan reads as pipes. Measured, the distinct-executable
  count was **51,622 before body stripping and 4,003 after**. **If this number
  ever comes back five-figure, the heredoc stripping is broken** — the shipped
  parser reports **254 distinct executables over 112,300 commands**.
- **A fabricated executable is the failure mode, not a missing one**, so three
  guards exist and each was added against a counted artifact: `[` as a builtin
  (763 fake rows), a per-prefix flag grammar for `xargs -I{}` / `env -u VAR`
  (several hundred each — a blanket "skip one word" is what produces them), and
  a plausibility refusal for `$AAPT`, `*"status":"failed"*`, `#` and `-z`.
  Every one of those is now honestly `unattributed`.
- **bashlex's grammar is a fact about bash; only NORMALIZATION is config.**
  `usage.commands` owns basename folding, the version-suffix pattern and the
  alias map, per the mechanism-not-policy rule. The version pattern requires a
  DOT (`python3.12` → `python`) because a bare trailing-digit strip invents
  tools that do not exist (`base64` → `base`, `bzip2` → `bzip`); cross-version
  folding is an alias entry instead. `npm`/`npx`, `rg`/`grep` and `uv`/`python`
  are deliberately NOT folded — **`uv run python` is credited to `uv` because
  uv's own resolution is real overhead, and folding it reports that time as if
  the code had been running.**
- **The watchdog bounds pure-Python work and NOTHING ELSE — say so rather than
  implying a guarantee.** bashlex is a pure-Python yacc parser with no input
  bound, reported to hang hard enough to need `kill -9`, and it runs on a
  daemon executor thread. `sys.settrace` on the calling thread with a
  coarse-grained wall-clock deadline is the only mechanism that stops it in
  place: a signal alarm is main-thread-only and a worker thread bounds the
  CALLER while the hung thread keeps a core for the life of the process. It
  does **not** bound a hang inside a single C-level call (a catastrophic regex
  backtrack executes no Python event); `MAX_LENGTH` is all that stands there.
  **The hang did not reproduce**: raw, unguarded bashlex over all 112,300
  commands completed in 85.8 s with zero stalls, so the guard is defence in
  depth against an input this host has not got.
- **The guard is also the cost.** Measured: 343 us raw per parse against
  ~1.6 ms traced, i.e. a full cache REBUILD pays ~8 minutes of parsing it did
  not before. Tolerable only because a refresh re-projects changed sessions
  only and is user-triggered, never on the ~1 Hz tick — **check that is still
  true before putting anything else this expensive in `_event_rows`.** A memo
  was measured and rejected: 101,158 of 112,300 real commands are distinct.
- **Version 4 of the schema adds NO column.** It changes what `target` and
  `attrs_json` MEAN for shell rows, and an unbumped cache would serve the card
  an empty range — indistinguishable from a quiet week. **Bump
  `SCHEMA_VERSION` for a change in a column's meaning, not only its type.**

## Attribution has five dimensions and they do not collapse

provider · source (provider + profile root) · account · project · session. **A
coding tool kind is not an account:** two Claude `CLAUDE_CONFIG_DIR` roots and
two Codex `CODEX_HOME` roots are four accounts even when they share every
project. `account_id` is an opaque local hash of provider + resolved root —
**never an email, never anything copied out of a credential store**, because a
primary key made of a credential leaks the credential into every dump, log and
wire view that ever touches it.

Roots are ENUMERATED from config and persisted `TranscriptContext` records, not
discovered by crawling. A recursive walk of every historical `agent-config/`
directory would index workspaces that no longer exist and grow without bound.

## Privacy is a default, and a test

By default the database holds ids, opaque account ids, cwd/repo display facts,
timestamps, model ids, tool names, failure categories, paths already represented
as metadata, token counts, durations, costs and quota snapshots. It holds no
prompt, no response, no reasoning text or encrypted reasoning, no tool-result
body, no request body, no auth header, no token or cookie. The transcripts
remain the source of truth for all of that, behind the existing read-only
drill-in.

## Session lessons

- **The projector's only transcript input is the adapter message spine.** The
  adapter's `read_messages` call already passes through `TranscriptCache` and
  owns Claude split-block merging plus Codex dual-record/cumulative-token
  handling; a usage implementation must never open JSONL or decode provider
  records itself. Session rows may use the adapter's activity projection for
  provider totals that are intentionally absent from individual messages.
- **Refresh is a replace operation at the `(session_id, source_id)` seam.** A
  changed transcript removes that session's derived rows before inserting the
  new projection, while unchanged fingerprints skip the adapter read. The
  fingerprint is the SET returned by `locate_transcripts` (primary plus
  sub-agent files), not only the catalog's primary path; a vanished member
  invalidates the session and a vanished primary prunes its stale rows. This is
  what makes deleting the cache and rebuilding equivalent to an incremental
  refresh without accumulating duplicate events.
- **Derived active time includes only timestamp-supported loop intervals.** A
  user/tool record followed by an assistant generation and a tool call followed
  by its correlated result are active evidence; the assistant-final → next-user
  gap is human wait and is excluded. Generation/tool durations carry `derived`
  provenance, while unsupported intervals stay null.
- **A provider total is authoritative for headline totals, not a token class.**
  Codex's cumulative input/output belongs in `provider_total`; it must not be
  relabelled fresh input. Class breakdowns remain nullable and separate.
- **A range cost is all-or-unknown.** Price every included session from current
  config at query time; if any row lacks a unique priced model or token evidence,
  return null and mark cost unavailable for that selection. A partial sum or a
  priced model with all-null counts is not `$0` and is not a complete total.
  Every class with a non-zero configured rate must be measured: output-only
  Codex evidence cannot silently price missing input as zero. Refresh sortable
  cache columns when startup pricing changes so cost order and display agree.
- **Every grouped aggregate is complete-or-null, not merely `SUM`-shaped.**
  SQLite ignores null inputs to `SUM`, so provider/account/project/model rows
  must compare each measurement's count with the group count before summing;
  otherwise one measured session plus one unknown session becomes a confident
  partial total. This applies to token classes, provider totals and active time.
- **Temporal aggregates follow event timestamps, not session endings.** A
  session crossing midnight contributes timestamped generation/tool evidence
  to each corresponding day; range listings use event existence rather than
  `last_event_at`. Aggregate token totals are all-or-null when a contributing
  session has no token evidence. Codex's cumulative total can only attach to its
  final normalized generation because the adapter exposes no per-message usage;
  never invent an intra-session split.
- **A CALENDAR BUCKET HAS NO NULL, so complete-or-null degrades there into
  complete-or-ABSENT — which is a positive false claim, not a withheld one.**
  `activity` skipped any day where one contributing session lacked the
  measurement, and a heatmap draws a withheld day exactly like a day nobody
  worked. Measured: on the documentation demo corpus the tokens metric lit
  **4 of 24 active days** while `sessions` and `tool_calls` lit all 24; on the
  reference host's real cache `active_minutes` deleted **3 of 31** days, one of
  them holding **1099.5 measurable minutes across 22 of its 23 sessions**. So
  `_measured_sum` publishes the sum a day can support and withholds only a day
  that HAS sessions and measured none of them — an empty day is a genuine zero
  and keeps its bucket. That is `ticketRollup`'s rule from
  [webapp](../../../../webapp/CLAUDE.md) at a second call site: an
  understatement can only ever understate, while an exclusion lets every day
  that remains overstate its share of the total. **`cost` keeps all-or-nothing
  precisely BECAUSE it has the third state** (`coverage.cost_available`), and
  that is the discriminator for any future metric — a value with nowhere to say
  *unknown* must never answer by disappearing. `series.py` already gets this
  right for the same data by returning `None` for the cell, which a client
  renders as unmeasured rather than as idle; the divergence is the wire shape,
  not the policy.
- **Model metrics require single-model attribution.** Switched-model sessions
  remain visible in the audit table, but whole-session totals must not be copied
  into every model row or concentration cohort.
- **Activity discovery and subscription quota selection are intentionally
  different.** Historical activity projects every normalized transcript Grove
  can reach. Quota may touch live credentials and provider endpoints, so only
  provider/profile roots listed in `usage.quota.profiles` are collected or
  displayed. Do not reintroduce ambient, roster, or workspace quota discovery.
  Keep selection in the config cascade; clients render the frozen response and
  do not maintain a second preference store. `accounts()` must not cache a local
  unsupported description as a fresh network result; retry floors key off the
  original failure through stale fallback, and an unknown stale age stays null.
- **Local projection and external telemetry have separate durability and consent.**
  `usage.sqlite3` is disposable and refresh atomically replaces adapter-derived
  session/event rows; summary, activity, breakdowns and findings are bounded SQL
  over that projection, never transcript parsing at request time. Historical
  telemetry writes require `telemetry.backfill.enabled` plus exact provider
  roots in `telemetry.backfill.profiles`, still obey `telemetry.content_owner`,
  and checkpoint accepted immutable traces in `telemetry-exports.sqlite3`.
  A checkpoint is keyed by a credential-safe digest of host + public project
  key as well as trace id; changing Langfuse projects must not inherit another
  destination's acceptance history. Remote observation-id reconciliation is
  mandatory before and after writes because deterministic OTel ids are not a
  deduplication guarantee. Collector acceptance is its own durable `submitted`
  state: Langfuse indexing may lag ingestion by many minutes, so an immediate
  404 means "await visibility," not "send again." During the retry grace,
  reruns probe submitted traces without re-emitting; only a complete remote id
  set promotes the row to `completed`.
  `grove usage backfill` refreshes local state by default; `--telemetry --yes`
  is the explicit external-write edge, while `--dry-run` writes neither store.
  Reconcile persisted quota rows against `accounts()` during service startup:
  deselection must make every endpoint's coverage honest before `/quotas` or an
  explicit refresh happens, while still preserving selected accounts' last read.
- **Every aggregate and detector shares `session_filter_sql`.** Exact model
  matching goes through `json_each`, project matches the resolved root or cwd,
  and day bounds are computed in the requested zone. A shape-valid but
  impossible calendar day degrades to the remaining filters instead of raising.
- **The daemon's page limit is a safety clamp, not a closure-bound OpenAPI
  constraint.** FastAPI/Pydantic resolves annotations from module globals;
  request-specific config belongs in the handler body, where it can clamp a
  valid positive value without making `build_app().openapi()` depend on a
  closure variable.
- **A row must be internally coherent before it is large, and a decoration
  added to a breakdown must survive `breakdown()`'s own fork.** Model latency
  shipped with both halves of that wrong. Its read omitted
  `json_array_length(s.models)=1`, so a row built from single-model sessions
  (the restriction that keeps a session-level token SUM from being attributed
  to one of several models) was decorated with an average taken over *every*
  session that touched the model — measured, `claude-sonnet-5` published
  8429 ms over 41,648 calls beside "24 sessions", whose own calls support
  6532 ms over 121. The defence that an event carries its own exact `model` so
  nothing is guessed is true and beside the point: the question is not whether
  the average is computable, it is **which population every column on the row
  describes**. And the merge lived inside `_model_breakdown`, which only serves
  the untimed route — `breakdown()` forks on `_has_temporal_filter` first — so
  any date bound silently dropped the column to `avg_ms=None`, contracted as
  *never measured*, i.e. the degraded answer wearing the honest one's clothes
  again. Both are pinned by mutation-checked tests. **Generalizable: when you
  decorate one dimension's rows, decorate them at the routing seam, not inside
  one of the branches.**

- **Prior art was read rather than re-derived, and two facts came back worth
  keeping.** `cclens` fingerprints on `(mtime, size)` — an explicitly cheap
  change detector, not a content hash — and guarantees idempotency by replacing
  a source's rows rather than appending; both are adopted here. `ccusage` keeps
  provider-reported cost ahead of a price-table estimate and treats its pricing
  snapshot as overridable config; both are adopted, and the built-in price table
  is deliberately empty because a shipped one goes stale into confident wrong
  money.
- **The detectors get their thresholds from config, never from literals**, for
  the reason the root file gives about policy in code: a threshold baked into a
  query is a judgement nobody can disagree with, and every one of these
  judgements is workload-specific.
- **A quota window's burn rate is DERIVED AT READ TIME and costs no provider
  request, which is the only reason it may exist at all.** `quota/_projection.py`
  is pure arithmetic over a reading Grove already holds — used percentage,
  window bounds, injected clock — attached in `UsageQuery.quotas`, persisted
  nowhere. **A projection is a statement about *now* against a reading taken
  earlier, so storing one serves an answer that ages while its inputs do not**;
  that is also why `quota_snapshots` needed no column and no schema bump.
  - **Half the shipped providers cannot be projected, and the reason is a
    missing field rather than a bug.** A window start is `resets_at −
    window_seconds`, and the Claude endpoint reports **no duration at all**, so
    every Claude window is honestly `unknown` while Codex (which sends
    `window_seconds`) projects. Verified live on 2026-08-11: three Claude
    windows unknown, the Codex weekly at 25% used / 28.5% elapsed → `tight`,
    87.6% projected. **Filling the gap means an operator-supplied duration in
    config, never a built-in "5h"/"7d"** — those boundaries have moved once
    already, and a guessed denominator produces a confident projection off a
    number nobody published.
  - **A minimum elapsed fraction is not caution, it is the reporting
    granularity.** Both providers round usage to whole percentage points, and
    dividing by elapsed magnifies that step by `100 / elapsed` — so below
    `MIN_ELAPSED_PERCENT` (10) a single rounding step walks the verdict across a
    threshold on its own. The elapsed fraction still rides, because it is
    measured rather than extrapolated and it is what tells a client *why* the
    verdict is unknown. Same split for `tokens_used`: a measurement does not
    share an extrapolation's gate.
  - **`tokens_used` reuses `_event_groups`, so a window inherits every temporal
    rule already settled** — event timestamps rather than session endings, and
    per-session completeness, which is what keeps Codex's one cumulative report
    per session from reading as a partial total. Measured on the reference host
    (182 MB cache, ~15k events in a weekly window): **36 ms per window that has
    bounds**, against 0.5 ms for the `coverage()` the same call already paid. A
    per-request seam at that price, never the ~1 Hz tick — and it scales with
    events inside the window, so several duration-bearing windows multiply it.

## SQLite mechanics: what a migration audit ruled out, and the two gaps it found

A 2026-08-11 evaluation on the real cache (188 MB, 612k events) REJECTED
migrating off SQLite: readers are not starved by a concurrent writer (summary
p50 4772 ms idle vs 5381 ms during a refresh), writes cost only 1.53 s of a
30.4 s refresh, and transcript parsing (22.4 s) dominates. Two genuine gaps
survived that verdict and are now closed:

- **`PRAGMA busy_timeout` was simply missing.** `_connect_prepared` set WAL,
  `synchronous=NORMAL` and `temp_store=MEMORY` but left SQLite's own busy
  timeout at its default of 0, so a real cross-process write COLLISION raised
  `database is locked` immediately instead of waiting. Up to three processes
  write this file — the daemon, the TUI's Usage screen (`refresh(force=False)`
  on open) and `grove usage backfill` — and "zero locked errors in 30 days of
  daemon journal" never covered the other two, whose failures land in their
  own stdout. `UsageConfig.busy_timeout_ms` (default 5000, `0` restores
  SQLite's immediate-raise behavior) threads through `UsageStore(db_path,
  busy_timeout_ms=...)` → `_connect_prepared`, per the root rule that a
  threshold baked into code is a judgement nobody can disagree with.
- **`_event_intervals`'s Python-side merge is where a wide window's Python
  time actually goes, and the fix is row-shape, not algorithm.** For a week-
  wide window the raw SQL is ~1.06 s while the full `summary()` call is ~5.1 s;
  `EXPLAIN QUERY PLAN` shows proper covering-index searches, so it is not a
  missing index. The gap is a Python loop over up to ~330k EVENT rows (not
  session rows — `_event_groups`'s own per-row `dict(row)` conversion is
  per-SESSION and negligible by comparison) doing named `sqlite3.Row` access
  four times per row, which re-scans the cursor description by string on
  every call. `UsageStore.query_tuples` swaps `row_factory` to `None` for one
  call (safe under `self.lock` — one connection, one lock) and returns plain
  tuples the caller destructures positionally; `_event_intervals` and
  `series.py`'s twin `_intervals` both moved onto it. Measured on the real
  cache: a 90k-row week-wide window went from ~0.30 s to ~0.23 s (~22%); a
  389k-row all-time window from ~1.3 s to ~1.0-1.2 s. The reduction itself —
  sort, merge, `ActiveIntervals.of`/`.union_ms`/`.sum_ms` — is untouched, so
  `active_ms`/`execution_ms` are provably identical before/after (verified by
  monkey-patching the old row-shape back in and diffing `summary()` output).
  **A gaps-and-islands SQL rewrite that pushes the merge itself into SQLite
  was considered and deliberately NOT done** — the union/sum reducer already
  lives in exactly one place (`_intervals.py`, shared by ingest and query) and
  a second implementation in SQL is how the two drift; re-open only if the
  row-shape fix stops being enough.
- **A per-bucket query over a calendar range is O(days) ROUND TRIPS — and the
  round trips turned out not to be where the time went, which is the more
  useful half of the finding.** `activity` looped `day_boundaries` and issued a
  fresh `_event_groups` + `_event_intervals` pair per day (`cost` a second pair,
  inside `_event_cost`), so a year-wide window was ~730-1460 executes over the
  same corpus; on the reference host one request cost **30.7 s** for `tokens`
  and **74.3 s** for `cost`, against 11.2 s for the `summary` beside it that is
  not per-day, and the page fires several concurrently. The day is a `GROUP BY`
  column now: `day_calendar_sql` carries the whole spine in as a `VALUES` CTE —
  **the boundaries must be VALUES, because a day is a local-midnight span in the
  reader's zone and a tz-naive `date(ts)` would silently re-bucket every event
  for every non-UTC caller while the query got faster** — and `series.py`, which
  was already built this way, now shares that helper and the join string rather
  than keeping a second copy of both.
  - **Whether the round trips were the WORK depends on the plan, and a facet
    filter flips the answer** — which is why one measurement of this would have
    been worse than none. Unfiltered, `EXPLAIN QUERY PLAN` gives the per-day and
    the grouped form the identical plan (an `ix_events_ts` range search per
    bucket, then a covering probe into `sessions`), so deleting 730 executes
    bought **7%** (3.97 s → 3.71 s on a 1000-session / 1.34M-event corpus). Add
    `provider` + `project` and the same deletion bought **4.6x** (16.06 s →
    3.50 s), because the per-day form starts driving from the session indexes
    and re-walks each matching session's events once per day. **Read the plan
    before assuming the loop is the cost — and measure the FILTERED shape too,
    because the cheap case is the one that hides the blow-up.**
  - **What bought the rest was that four of the five metrics were paying for a
    column none of them reads.** `active_ms` is the one value no aggregate can
    produce, so it needs the read that walks every EVENT (594,960 rows, 1.51 s
    including the merge) beside the per-session aggregate (1,301 rows, 1.82 s) —
    and `_event_groups` computed it unconditionally for every caller. Gating it
    on the metric took `tokens`/`sessions`/`tool_calls` to **~1.9 s** and `cost`
    to **2.5 s**; `active_minutes` legitimately pays both and stays at **3.7 s**,
    which is the documented Python-side merge above and not a new problem.
    **The tell is a shared helper that computes whatever its widest caller
    needs.** The withheld value is an **absent key, never `None`** — `None` is
    contracted as *never timed*, so a default would let a metric that forgot to
    ask publish a day of real work as unmeasured.
  - **The user-visible number is the LAST response, not the first.** The usage
    page fires all five metrics at once and every read serializes on
    `UsageStore.lock` (one connection, one lock), so the page waits for their
    SUM: measured on the planted 1.34M-event capture corpus, **28.6 s before and
    12.6 s after**. That is the whole reason this reads as a blank card rather
    than as a slow one — the client gives up and renders its empty branch, which
    is indistinguishable from *no tokens were measured*. **When a surface fans
    out over a shared lock, budget against the serialized total.**
- **`_workspace_id(ref)` used to re-walk the whole fleet PER SESSION inside
  the refresh loop** — `RepoRegistry.get(root)` → `manager.list()` per known
  root, i.e. live git/tmux reconciliation, profiled at 394 `manager.list()`
  calls / 3.098 s cumulative for 79 sessions (~10% of refresh wall time)
  re-deriving a value that is static per session. `UsageProjector._workspace_index()`
  builds the whole `(agent_session_id, adapter_kind) -> workspace_id` map
  ONCE, lazily, on the first ref that actually needs replacing in a refresh
  (not eagerly for every refresh — an all-unchanged refresh still costs
  nothing extra), and every later ref in that same refresh does a dict
  lookup. This is a loop-placement fix, not a cache: the mapping is a local
  variable scoped to one `refresh()` call, never persisted or reused across
  refreshes, because a stale session→workspace mapping would silently
  misattribute a session moved or reassigned between refreshes.
