# grove.core.usage.quota — what remains, per billing account

> ↑ [usage](../CLAUDE.md) · [root](../../../../../CLAUDE.md)

The sibling seam to [agents](../../agents/CLAUDE.md): an `AgentAdapter` reads what
a tool already did, a `QuotaProvider` reads what a tool's account has left. One
provider per coding tool, one `QuotaCollector` over all of them, and an account
minted per *profile root* rather than per tool.

The package's whole difficulty is that reading quota is itself a metered act.
Everything below follows from that one sentence.

## The two providers are not two implementations of one thing

They have opposite cost shapes, and every rule here exists because of the
expensive one.

| | claude_code | codex |
|---|---|---|
| evidence | `provider_endpoint` — an OAuth GET | `rollout` — a file the CLI already wrote |
| costs | a metered request per read | a bounded tail read |
| can be refused | yes (401/403/429) | no |

**A provider that reads a file can never rate-limit you, so `codex` needs none
of the machinery and proves nothing about whether it works.** When quota broke
on this host, `.codex` sat at `status=ok` beside a `.claude` reporting nothing —
the asymmetry is the diagnosis, not a coincidence, and any test that exercises
only the local arm is testing the case that was never at risk.

**That asymmetry is DECLARED, not re-derived per rule: `QuotaProvider.metered`.**
Every caching decision keys off it, and it defaults to `True` so a provider
added without thinking inherits the careful treatment. The TTL is the first
consumer — it is a rate-limit budget, so it governs only the providers that
spend one, and an unmetered read (~3 ms of rollout tail on a real profile)
happens on every call. **The cool-off is deliberately NOT gated the same way:**
it fires on what a provider actually RETURNED, which is the one signal that can
change without anyone re-declaring the cost shape. Before the flag existed the
TTL was generalized to both, so every step taken to protect a rate limiter was
silently paid for in staleness by the provider with no limiter to protect —
exactly what this section forbids, hiding inside a shared knob.

## Which plan an account is on is LOCAL evidence — for BOTH providers

The asymmetry above inverts here, and that is the whole finding: the expensive
provider answers this one for free. Verified on the reference host on
**2026-08-11** (Claude Code 2.1.227, codex-cli 0.147.0):

| | where the plan is written | what it says |
|---|---|---|
| claude_code | the `claudeAiOauth` block of `.credentials.json` — the file `_read_credential` already opens | `subscriptionType` (`max`) + `rateLimitTier` (`default_claude_max_20x`) |
| codex | the same `rate_limits` block the windows come out of | `plan_type` — `plus` / `team` / `prolite`, and `null` on 47,764 of 53,286 records |

**No probe was spent to learn this, and none should be.** The metered endpoint
may or may not also state a plan; it does not matter, because a free file
already does. **Before adding a field to a metered read, check whether the
credential store the provider opens anyway already holds it** — that is the same
question the ledger answers for readings, one layer earlier.

Four rules follow, and each is a way this field could be confidently wrong:

- **Absent means "could not tell". There is no unknown-plan string and no
  default.** This is the one number a person reconciles against their own bill,
  so a blank field costs nothing and a wrong one costs trust in the page. Grove
  never infers a plan from a window size or a token limit.
- **A plan slug crosses unchanged; Grove holds no vocabulary of vendor plan
  names.** `prolite` was not a plan anybody here had heard of, and it is what
  the account is on. Mapping an unfamiliar token onto the nearest familiar one
  is the failure mode, not a nicety.
- **The plan is read from the block the WINDOWS came from.** This host's Codex
  corpus runs `plus` → `team` → `prolite` across six months, and 5,584 of its
  11,105 window-bearing blocks name no plan at all — so scanning back for the
  newest block that *does* name one reports a subscription the account left in
  March, with a current percentage beside it. The percentage is what would make
  it believable.
- **Claude's sub-tier is anchored on the plan's own name** (`max` +
  `default_claude_max_20x` → `20x`), never on a table of Anthropic tier strings.
  A tier that does not contain the plan contributes nothing.

A refusal carries the tier too (`QuotaProvider.failure(subscription=…)`): the
plan never depended on the endpoint, so blanking it exactly when the endpoint
says "not yet" hides the one answer Grove could still give — which is precisely
the state the 2026-08-10 incident left the page in.

## The incident this package was reshaped by (2026-08-10)

Measured on the reference host, and worth keeping because both halves were
invisible from the code:

- 10:33 — `.claude` returns three healthy windows.
- 11:39, 11:45 — the daemon is restarted twice (ordinary Grove updates).
- 12:47 — `GET /usage/quotas` reports `status=rate_limited`, `windows=[]`,
  `observed_at=null`, `stale_seconds=null`. The 10:33 reading is gone from the
  wire **and** from `quota_snapshots` in the usage DB.

Two root causes, and neither is "there was no cache" — a TTL, a retry floor and
a last-known-good slot all already existed:

1. **Last-known-good lived only in process memory.** A restart erased it, and
   the failing read that followed had nothing to fall back to. `_store_quota_snapshots`
   then wiped the durable copy too, because it clears the table and re-inserts
   from `account.windows`, which a failure leaves empty. **The persisted table
   was never a second memory; it was a mirror of the first one's mistake.**
2. **A failure that never expires was retried on a fixed floor, forever.** A
   120 s floor against a limit nobody has lifted is a request every 120 s for as
   long as it lasts — which is precisely what a rate limiter reads as continuing
   to knock.

**The general lesson: a cache whose lifetime is the process is not a cache of
anything a restart can destroy.** The question to ask of any "we keep the last
good value" claim is *how long does the thing holding it live, relative to the
thing that erases it* — here, the daemon restarts more often than a weekly quota
window rolls.

## Durability is the design, not an optimization

`_state.py` owns both halves and splits them on the side-effect line:
`QuotaProbeState` is the entire "may I probe, and what do I show" decision as
pure clock-injected data; `QuotaStateFile` is the edge that makes it outlive the
process.

- **The ledger is `paths.quota_state_path()`, deliberately NOT `usage.sqlite3`.**
  That file's licence is that deleting it is always safe because it is derived
  wholly from transcripts — and a quota reading is the one fact in this subtree
  nothing can reconstruct. Once a provider stops answering, the last reading
  Grove saw is the only one that will ever exist. It would also mean a schema
  bump rebuilding 180 MB of derived data to add a quota column.
- **It is a shared probe budget, not just a memory.** The daemon, the TUI's usage
  screen and every `grove usage` run each build their own `UsageService`, so
  before the ledger each was an independent, uncoordinated stream of requests to
  the same endpoint on behalf of the same account. Now three processes reading
  inside one TTL window cost one request.
- **A merge, never a replace.** Those processes legitimately resolve different
  `usage.quota.profiles` (a repo-scoped cascade differs from the user one), so a
  narrower run must not delete history for accounts it was never asked about.
  The whole read-modify-write is under `paths.exclusive_lock`, for the reason
  the workspace store documents: atomic publication is a rename, so two writers
  otherwise clobber each other's readings.
- **Both directions are best-effort.** An unreadable ledger degrades to "no
  history", which is exactly the state that shipped before it existed. A quota
  page must never be the thing that fails a daemon start.
- **It holds no credential**, which is what makes writing it to disk admissible
  at all: account ids, operator labels, percentages, window durations and
  timestamps — the same facts the wire already carries.

## "Good" is about the payload, never the status word

`QuotaProbeState.carries_reading` decides what is worth keeping, and it asks
whether the answer measured anything (`windows`, or `spend` for an API-key
account) rather than whether its status was `ok`.

**Codex reports `stale` WITH the newest numbers Grove has** when its recorded
window has rolled over. Keying last-known-good on the status word files that
newer reading as a failure and answers with the *older* one — this package's own
bug, inverted, and it was live before the rule changed. The same rule is why a
windowless `ok` cannot be remembered: falling back to it would render `stale`
with empty windows, i.e. exactly the useless answer the fallback exists to
prevent. Neither real provider can emit one, which is why **a fixture that does
describes a program Grove does not run** — and that fixture is what hid the rule.

## Backing off

- **`Retry-After` is the one header read out of a refusal**, because the
  alternative is Grove deciding for itself how soon to knock at a door that just
  said "not yet". `parse_retry_after` handles **both** forms RFC 9110 allows —
  delta-seconds and an HTTP-date — since a client that handles only the first
  silently ignores every server that sends the second, and an ignored
  instruction is indistinguishable from one that was never sent.
- **A provider asking for LONGER wins; asking for less does not — and that is
  not a hypothetical.** Measured against the live endpoint on **2026-08-10**, a
  429 from `api.anthropic.com/api/oauth/usage` carries `retry-after: 0`. The
  header is present and meaningless, so a client that honours it literally
  answers a rate limit by retrying immediately, forever — **strictly worse than
  having read no header at all.** Read the instruction, then take the longer of
  it and your own schedule: the header bounds when the limit lifts, never how
  often you should ask.
- **The schedule doubles from `retry_floor_seconds` up to `retry_max_seconds`**
  (120 s → 1 h by default). Only outcomes that cannot clear on their own earn a
  cool-off: `auth_expired` and `rate_limited` mean *nothing changes until a human
  signs in or a window elapses*, while `unreachable` clears by itself and is
  bounded by the ordinary TTL. It rides on the view's own `retry_after` field
  rather than a second return channel — the field is already on the wire and
  means the same thing at both ends.
- **The cool-off outranks `force`.** A floor a caller can bypass is not a floor,
  and the explicit-refresh button is exactly what a frustrated operator presses
  repeatedly at an account that is already limited.
- **A cool-off that does not survive a restart is not a cool-off either, and on
  this host that was the larger hole of the two.** The daemon was restarted three
  times on the day of the incident; each restart previously discarded the floor
  and probed on the next page load. Persisting `retry_not_before` in the ledger
  is what makes a backoff a property of the ACCOUNT rather than of a process
  that happens to still be running. **Whenever a rate-limit defence is stored in
  memory, count how often the process dies compared with how long the limit
  lasts** — if the process is shorter-lived, the defence does not exist.

## Coalescing needs a counter, not a clock

Two callers arriving together both carry `force`, so serializing them on the
collector's lock is not enough — the second would bypass the TTL in turn. A
caller records the account's completed-probe count *before* queueing on the
lock, and a probe that landed while it waited is its answer too.

**A timestamp cannot serve as that token**: under an injected clock two calls
share one instant, and in production two probes can share a millisecond. The
counter is per process and deliberately not persisted — it answers "did someone
here just do this", where the ledger's `fetched_at` answers the cross-process
question.

## Grove is a CO-TENANT on that limiter, and measuring said so (2026-08-11)

The endpoint refusing does NOT mean Grove is the one knocking, and the second
time this was reported the answer was that it is not. Measured on the reference
host over a 420-second window, with no probe of the endpoint spent to learn it:

| prober | probes in 420 s | interval |
|---|---|---|
| the host's Claude Code statusline poller | 5 | 63–127 s |
| Grove's daemon | 0 | — |

The statusline forces a refresh of the same `/api/oauth/usage`, on the same
personal credential, whenever its own 60-second cache is stale — per render,
per session, with no cross-process coalescing and no cool-off. Grove's whole
visible history for the preceding day is six `rate_limited` probes.

Three things follow, and the third is the general one:

- **Grove's read-triggered probing is bounded by the ledger, and the bound
  holds.** `fetched_at` is persisted, so N processes and every restart inside
  one window cost one request. That is also why **jitter buys nothing here** —
  the herd a jittered schedule would break up is already collapsed by a shared
  timestamp, and the alignment jitter defends against is between PROCESSES that
  share no state.
- **A window-scaled TTL is arithmetically the wrong direction.** The reference
  account's shortest window is five hours and both providers round usage to
  whole percent, so one reporting step is `18000/100` = 180 s — *shorter* than
  the fixed default it would have replaced. Scaling to the measured window
  would make Grove probe more, not less.
- **When a limit is per-ACCOUNT, your own rate is not the whole question.**
  Count every client on the host holding that credential before tuning your own,
  or you will spend a redesign lowering a rate that was never the binding one.

## Counting your own requests has to survive the default log level

`QuotaProbeState.probe_count` is monotonic and lives on the ledger, because the
per-probe log line cannot answer the question on an ordinary install: it is a
`logger.debug` and Grove's default sink is `WARNING`, so a *healthy* probe
leaves no trace anywhere and the rate is reconstructable only from failures.
Twenty-four hours of journal on the reference host held six quota lines, all of
them backoffs. **Instrumentation whose visibility depends on a level nobody
sets is instrumentation that does not exist** — put the census in the durable
artifact the feature already writes. Two writers in one instant can lose an
increment to the merge, so it reads as a floor; the TTL makes that overlap rare
and undercounting is the safe direction for a number used to decide whether
Grove is the culprit.

## One log line per PROBE, never per render

There was no log of an upstream request at all, which is why the rate limit
could not be attributed after the fact — nothing on the host could say how often
Grove had contacted the endpoint. The line is edge-triggered on an actual probe:
a line per served snapshot would be noise proportional to page loads, and page
loads are not the thing anyone needs to count.

## What has NOT been done, and what would have to change

- **`UsageService.quotas()` still triggers collection from a READ, and the
  2026-08-11 measurement is the reason it still does.** With the TTL, the ledger
  and the cool-off, N readers in a window cost one request, and the daemon was
  measured probing zero times across a window in which the endpoint was refusing
  — so the read path is demonstrably not the hazard, and a scheduler would cost
  a daemon timer plus a story for the hosts that run no daemon (a CLI-only host
  would then never refresh at all). Reconsider it against a MEASURED read rate,
  or if quota ever grows a third, chattier provider.
- **`force` still bypasses the TTL entirely.** Only the cool-off outranks it,
  and a cool-off exists only after a failure, so a human pressing an
  explicit-refresh button repeatedly on a healthy account is one probe per
  press. Unmeasured, and there is no honest floor to reuse: `retry_floor_seconds`
  answers a different question, and giving it a second meaning is how one field
  starts answering two.
- **`_store_quota_snapshots`'s `DELETE FROM quota_snapshots` is untouched.** It
  no longer destroys anything, because a failing account now *renders* with its
  last-known-good windows — but the destructive shape is still there and would
  bite again the moment something renders windowless. That file is
  [`usage/service.py`](../service.py), not this package.
- **Two simultaneous probes from two PROCESSES are still possible.** The lock
  covers the ledger's read-modify-write, not the request; holding a file lock
  across a 10 s HTTP call would let a stalled CLI block the daemon's page. The
  measured problem was sequential (restart → probe), and `fetched_at` answers
  that completely.
