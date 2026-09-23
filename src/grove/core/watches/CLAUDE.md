# grove.core.watches — register a callback, halt, and be woken

> ↑ [grove.core](../CLAUDE.md) · [root](../../../../CLAUDE.md)

One concern: an agent waiting on something slow says what it is waiting for and **stops**, and Grove delivers the outcome when it settles. Wire shapes live in [contracts/watches.py](../contracts/CLAUDE.md); delivery is [`core/mailboxes.py`](../CLAUDE.md) unchanged; the daemon owns the routes and the lifespan.

## The cost model IS the design

A naive implementation gives each waiter a thread, or wakes every few seconds and scans the registry. Both scale CPU with how many agents happen to be waiting, which on a fleet host is exactly backwards — **waiting should be free**.

- **One scheduler for the host**: a single asyncio task over a `heapq` keyed on `next_due`, plus one `asyncio.Event` that register/cancel set to wake it early. No thread, process, timer or sleeping coroutine per watch or per workspace.
- **Nothing periodic.** An empty registry returns `None` from `seconds_until_next()`, so the loop awaits an event rather than a clock and no timer exists to fire. There is no tick, no sweep, no scan — the heap's minimum IS the next wakeup.
- **One shared single-worker pool** (`grove-watch`). A probe must be ONE bounded observation: a single API read, or a command with a timeout. A watcher that called `gh run watch` would move the agent's sleep loop into Grove's one worker, where it stalls every other watch on the host. The deliberate trade is that probes serialize; the remedy if it ever bites is a small FIXED worker count, never one that scales with the fleet.
- **Writes follow state changes, not elapsed time.** `reschedule` no-ops when the due time has not moved — a 6-hour watch at 30 s is 720 ticks, and rewriting the file on each is how a mechanism that should be free becomes a disk-I/O problem.
- **A reload re-arms with JITTER** across each row's own interval, or forty recovered watches fire forty probes in the same instant.

## Ordering rules that survive a crash

- **Persist the outcome BEFORE delivering it.** A crash between the two leaves a durable `fired` row with `receipt=unknown` — recoverable, and visible in `grove watch ls`. Delivering first and crashing loses the outcome entirely: the heap entry is gone and nothing records what the watch concluded. Same "cheap failure over unbounded failure" argument as [`HandoverLog.claim`](../issueops/CLAUDE.md).
- **`unknown` is NEVER retried**, inheriting the mailbox's rule: a second copy of a message whose first copy may have landed is worse than an uncertain one.
- **`rejected` settles `undeliverable`, which is neither success nor failure of the WATCH.** The predicate may well have gone terminal; the recipient had simply stopped being live. Collapsing that into either outcome is this tree's recurring "the degraded answer is indistinguishable from the confident one" bug.
- **Every watch has a deadline, and there is no "wait forever".** `WatchRegistration.deadline` is optional on the wire but `None` means *the default for this predicate*, resolved in the scheduler (the one place that knows "now"): `DEFAULT_DEADLINE` (15 min) for open-ended predicates, and for a timer its own instant plus a minute — capping a timer at the open-ended default would make every long timer expire instead of fire. The default lives in the contract and nowhere else; the CLI sends `None` rather than its own number, or the CLI and MCP drift on how long an agent may be left waiting.
- **The re-check cadence is STORED on the row (`WatchView.every`), not derived.** It was once recomputed as `next_due - created_at`, which grows on every re-arm — a 30 s watch was probed at 30, 60, 120, 240, 480 s, doubling each time, so a long wait learned its subject had settled minutes late. A value that is an input belongs on the record; deriving it from a field the code itself rewrites is how it drifts.
- **A deadline is itself DELIVERED.** An expired watch mails the expiry, so an agent that halted is never left with no signal — the one guarantee a sleep loop structurally cannot make, and the reason halting is safe to recommend.
- **A settled row is RETAINED, not deleted**, pruned by age at scheduler startup. It is the record that this callback already went out, which is what stops a restart from sending it twice; the seven-day retention window leaves cancellation and delivery diagnostics available long after the workspace record is gone.

## Who a watch BELONGS to is its recipient

`GET /watches?workspace=<id>` (what the webapp's per-workspace card reads) filters on `recipient.workspace_id`, never on a `command` predicate's `workspace_id` and never on who made the HTTP call. An orchestrator registering `--for` a worker, or a command that runs in one tree and calls back to another, are both ordinary — the watch appears where its callback LANDS, because that is the session it can wake. The filter lives in `WatchScheduler.list` so the CLI, MCP and HTTP share one rule; a malformed id is a 422, never an empty list that reads as "no watches here". **The recipient also owns lifecycle:** reconciliation cancels every pending kind (CI, command, timer, ticket) when that workspace is paused, killed/missing, or otherwise non-`RUNNING`; attachment changes only add or remove the standing ticket rows. Lifecycle callbacks arrive from a worker while observations resume on the scheduler loop, so `cancel` and post-probe transition share one scheduler lock: cancellation that wins keeps its row cancelled and sends no stale mail.

## `Watcher[P]` is the whole extension story

A watcher answers exactly one question — *has this settled?* — and holds no scheduling, persistence, delivery or retry logic. Adding a predicate is a subclass plus one registry entry; it cannot change when Grove wakes up, what it writes, or how a callback is delivered.

- **It is GENERIC over its predicate variant, and that was not the first design.** Originally every watcher took the whole `WatchPredicate` union and re-narrowed it to its own helpers — boilerplate a type checker cannot verify is complete and a reader cannot tell is correct. Two independent implementations hit the identical wall, which is the signal a shared seam is missing rather than two files needing a fix. Now the union-narrowing happens once in the base `evaluate` and subclasses implement `observe(predicate: P, now)`.
- **Returning `None` is the load-bearing case, and it covers "I cannot tell".** A forge that timed out, a rate limit, a container that is gone — all answer `None`, never a failed outcome. "I could not tell" and "it failed" are answers a recipient acts on differently, and collapsing them is how a transient network error comes to read as a red build.
- **A wrong-kind predicate RAISES rather than answering `None`.** That is a routing bug in the registry, and a silent "not yet" would leave the watch pending forever while looking perfectly healthy.
- **A missing watcher is a real state, not a raise.** A durable row may name a predicate whose watcher was removed; it settles as unwatchable instead of taking down every tick behind it.

## Two rules the predicates paid for

- **`ci` keys on an immutable head SHA, never a branch name** — a force-push makes a DIFFERENT commit with the same name, and a watch following the name answers about work nobody asked about. Terminal requires at least one check to have APPEARED and none still running: the window right after a push legitimately has no checks, and reading that as success is the failure mode the whole predicate exists to avoid.
- **`command` runs in the workspace that REGISTERED it**, inside its container when that is where the agent lives. The daemon cannot claim to hold "the same environment the agent had". A timeout must TERMINATE the child, not merely stop awaiting it — and the guard is only real if the output reader's join is bounded too, which is the one guard here that survived its first mutation.

## Testing it

**`prime()` is split from `start()` because "ready" and "running" are different states.** Conflating them makes the loop untestable: a test driving `tick()` while the background task also drains gives two consumers of one heap, each stealing entries (measured: 26 of 50). Tests `prime()` and drive `tick()` with a fake clock; only a test genuinely about the background loop calls `start()`.

**An idle-cost assertion goes vacuous by default and three of eight did here.** `asyncio.wait_for` blocks on REAL time, so in a sub-second test the loop never runs and "zero evaluations" passes against a scheduler that scans every row. Assert on the SCHEDULE (`seconds_until_next()` is `None`) rather than on counted work, drive `tick()` explicitly, and mutate the guard to confirm it bites.

## Standing ticket watches (#837)

A watch an agent HALTS on is a *wait*: it settles once, has a deadline, and mails its expiry. Tracking an attached issue or PR is a *subscription*: nobody halts on it, and it lives as long as the workspace. `TicketPredicate` is the one standing predicate. It has no deadline (`expires_at` is `None`), and its watcher answers `StillWatching(predicate, outcome)`: the scheduler persists the new baseline (`WatchLog.carry_on`) BEFORE mailing, so a crash loses at most one notification and never repeats one. A failed or rejected send leaves the watch pending, because only the workspace's lifecycle ends it.

- **Nobody registers these by hand.** `TicketSubscriptions` reconciles a workspace's watches against its RECORD (one per attached ref while the persisted status is `RUNNING`, none otherwise) and is idempotent, so a lost edge or a restart is repaired by the next reconcile. It rides the ACTIVITY BUS, not the manager's events, because `grove tickets attach` usually runs in ANOTHER process and reaches the daemon only through the store-file watcher. That is also why `WorkspaceActivity.fingerprint` now carries the attached refs: attaching moved no other member, so no delta was ever published for it.
- **Grove's own writes are excluded by what is COMPARED, not by a filter after the fact.** `TicketSnapshot` holds only fields a person changes (title, state, draft, a body digest taken after `splice_footer` strips the badge region, a comment count). It leaves out assignees (Grove assigns itself) and the last-updated time (a sticky edit moves it). A new comment counts only without either issue-ops marker AND not by `viewer_login()`. The markers alone must be enough, because a tracker that cannot report its login still must not echo the sticky comment. That is the one guard that survived its first mutation, and it has its own test.
- **One read attempt per ticket per minute, however many workspaces or consumers.** The daemon shares ONE `tickets.reads.TicketReads` between the watcher and sticky publisher. It coalesces concurrent same-ticket misses and holds even failures for the full interval: without both guards, a publisher flush raced the watch or retried a failing forge faster than a minute. Fresh entries remain until expiry rather than being LRU-evicted under a busy fleet; see [tickets](../tickets/CLAUDE.md) for the memory trade-off. The provider read is `read_state`: one GET whose payload already carries body and comment count, and the thread is listed only when the count rises.
- **The first check records the baseline silently.** Its guard is correct but does not survive mutation, and that is structural rather than a gap: with no previous snapshot there is nothing to diff, so the test fixture cannot observe the difference.
