/**
 * The global status footer's four sections, as pure functions over wire shapes.
 *
 * Pure for the reason the whole adapter layer is: the footer is one band whose
 * every value comes from a different query, and the interesting parts — which
 * session states are counted, which subscription account survives to the strip,
 * what "two accounts" means when the viewport is narrow — are decisions, not
 * rendering. Deciding them here is what lets them be tested without a daemon,
 * a browser, or a query client.
 *
 * Issue #814.
 */

import type {
  AgentActivityView,
  BillingAccountView,
  ContextWindowView,
  DashboardSnapshotView,
  SubscriptionWindowView,
  UsageQuotasView,
  WhoamiView,
  WorkspaceStateView,
} from "@/lib/grove/api";
import { usedPercent } from "@/components/grove/usage/tokens";

/* ───────────────────────────── 1 · workspace ─────────────────────────────── */

export interface FooterContext {
  /** The project's display name, or null when no project is in scope. */
  readonly project: string | null;
  /**
   * The configured project's path BELOW its repo root, `null` when it is the
   * root itself.
   *
   * This is the `Agents > PA` half of the breadcrumb, and it is a real field
   * rather than a parse of the display name: the daemon publishes `cwd` and
   * `repo_root` per project group, and their difference IS the sub-path. No
   * project on the reference host uses one today, so the mechanism ships
   * unexercised by that host rather than faked.
   */
  readonly subpath: string | null;
  /** The LIVE branch, never the create-time snapshot. Null off a workspace. */
  readonly branch: string | null;
  /**
   * The worktree's own directory name, or null for a root-placed workspace.
   *
   * A root workspace runs IN the repo, so Grove cut no worktree and naming one
   * would claim an isolation that does not exist.
   */
  readonly worktree: string | null;
  /** The workspace's root agent, from its state or engine-primary session. */
  readonly rootAgent?: string | null;
  /** The root agent's latest context window, never a child aggregate. */
  readonly contextWindow?: ContextWindowView | null;
  /**
   * Why there is no window, when the daemon knows.
   *
   * `stale_native_worker` means a reading WAS suppressed rather than never
   * taken — the worker predates the current-format producer and is still
   * emitting the cumulative counters that read as 29.4M against a 1M window.
   * Absent means genuinely unmeasured. The two must not render identically:
   * one has a remedy (respawn) and the other is simply an agent that has not
   * answered yet.
   */
  readonly contextUnavailable?: AgentActivityView["context_unavailable_reason"];
  /** Where the agent runs. A fixed property of the workspace, not a state. */
  readonly runtime?: WorkspaceStateView["runtime"] | null;
  /** This workspace's own uncommitted and branch-delta git facts. */
  readonly git?: GitFacts | null;
}

/**
 * The open workspace's git position, as the rail cards already report it.
 *
 * Three DIFFERENT questions that a single "changes" figure would conflate, and
 * the engine keeps them apart deliberately: `dirty` is what is uncommitted
 * right now, `added`/`removed` is the branch delta since the workspace's
 * creation anchor, and `ahead`/`behind` compares the BASE BRANCH rather than a
 * remote upstream. The footer prints them as three groups for that reason.
 */
export interface GitFacts {
  readonly ahead: number;
  readonly behind: number;
  readonly added: number;
  readonly removed: number;
  readonly dirty: number;
}

/** Whether any of it is worth drawing — an untouched workspace draws none. */
export function hasGitActivity(git: GitFacts | null | undefined): git is GitFacts {
  if (!git) return false;
  return git.ahead > 0 || git.behind > 0 || git.added > 0 || git.removed > 0 || git.dirty > 0;
}

export const EMPTY_CONTEXT: FooterContext = {
  project: null,
  subpath: null,
  branch: null,
  worktree: null,
  rootAgent: null,
  contextWindow: null,
  contextUnavailable: null,
  runtime: null,
  git: null,
};

function basename(path: string): string {
  const parts = path.split("/").filter(Boolean);
  return parts[parts.length - 1] ?? "";
}

/**
 * The context for one open workspace.
 *
 * Reads `activity.branch` — the live branch the engine already derives per
 * tick — and never `state.branch`, which is the create-time identity `resume`
 * rebuilds the worktree from. The two disagree the moment an agent branches,
 * which is ordinary.
 */
export function workspaceContext(
  snapshot: DashboardSnapshotView | undefined,
  workspaceId: string,
): FooterContext | null {
  for (const project of snapshot?.projects ?? []) {
    for (const row of project.workspaces) {
      if (row.state.id !== workspaceId) continue;
      const root = row.state.repo_root;
      const tree = row.state.worktree_path;
      const primary = row.sessions[0];
      return {
        project: project.repo_name,
        subpath: projectSubpath(project.repo_root, project.cwd),
        branch: row.branch || row.state.branch || null,
        // A root workspace's worktree IS the repo root; there is no cut tree.
        worktree: tree && tree !== root ? basename(tree) : null,
        rootAgent: row.state.agent_name || primary?.session?.adapter_kind || null,
        contextWindow: primary?.activity.context ?? null,
        contextUnavailable: primary?.activity.context_unavailable_reason ?? null,
        runtime: row.state.runtime ?? null,
        git: {
          ahead: row.base_ahead,
          behind: row.base_behind,
          added: row.diff_added,
          removed: row.diff_removed,
          dirty: row.dirty_files,
        },
      };
    }
  }
  return null;
}

/** The context when no workspace is open: the selected project, or nothing. */
export function projectContext(
  snapshot: DashboardSnapshotView | undefined,
  selectedCwd: string | null,
): FooterContext {
  if (selectedCwd === null) return EMPTY_CONTEXT;
  const project = (snapshot?.projects ?? []).find((p) => p.cwd === selectedCwd);
  if (!project) return EMPTY_CONTEXT;
  return {
    project: project.repo_name,
    subpath: projectSubpath(project.repo_root, project.cwd),
    branch: null,
    worktree: null,
    rootAgent: null,
    contextWindow: null,
  };
}

/** `cwd` relative to `repo_root`, or null when they are the same directory. */
export function projectSubpath(repoRoot: string, cwd: string): string | null {
  if (cwd === repoRoot) return null;
  const prefix = repoRoot.endsWith("/") ? repoRoot : `${repoRoot}/`;
  if (!cwd.startsWith(prefix)) return null;
  const rest = cwd.slice(prefix.length);
  return rest.length > 0 ? rest : null;
}

/* ────────────────────────────── 2 · sessions ─────────────────────────────── */

export interface FleetCounts {
  readonly working: number;
  readonly idle: number;
  readonly blocked: number;
}

export const EMPTY_COUNTS: FleetCounts = { working: 0, idle: 0, blocked: 0 };

/**
 * Working / idle / blocked over every workspace on the host.
 *
 * Three rules, each of which the approved design states and none of which is
 * the obvious implementation:
 *
 * - **`blocked` is literally `blocked`.** The daemon's own `needs_attention`
 *   folds `waiting`, `blocked` and `error` together, and calling that sum
 *   "blocked" claims a stall for an agent that is only asking a question.
 * - **`idle` is literally `idle`**, never "everything that is not working".
 *   `starting`, `waiting`, `error` and `unknown` are none of these three and
 *   are counted by nothing here rather than swept into the nearest bucket.
 * - **One workspace contributes at most once**, through its primary session.
 *   The engine already promotes a session whose sub-agent fleet is active to
 *   `working`, so counting sub-agents beside it would double what that
 *   promotion exists to fold.
 */
export function fleetCounts(snapshot: DashboardSnapshotView | undefined): FleetCounts {
  let working = 0;
  let idle = 0;
  let blocked = 0;
  for (const project of snapshot?.projects ?? []) {
    for (const row of project.workspaces) {
      switch (row.sessions[0]?.activity.state) {
        case "working":
          working += 1;
          break;
        case "idle":
          idle += 1;
          break;
        case "blocked":
          blocked += 1;
          break;
      }
    }
  }
  return { working, idle, blocked };
}

/**
 * The ONE count a narrow band has room for: the most severe non-zero one.
 *
 * Severity, never position. A summary that showed the first of a list could
 * print `0 idle` while an agent sat blocked — the one way a summarized strip
 * can actively mislead, and the same reasoning `hiddenNeedsAttention` applies
 * to the account list. The word travels with the number for §4.7's reason: the
 * narrow band is exactly where the desktop's `lg:` label is absent, so a bare
 * coloured digit would be carrying its meaning in hue alone.
 */
export type SessionSummary = {
  readonly state: "blocked" | "working" | "idle" | "empty";
  readonly count: number;
};

export function sessionSummary(counts: FleetCounts): SessionSummary {
  if (counts.blocked > 0) return { state: "blocked", count: counts.blocked };
  if (counts.working > 0) return { state: "working", count: counts.working };
  if (counts.idle > 0) return { state: "idle", count: counts.idle };
  return { state: "empty", count: 0 };
}

/**
 * How far the fleet's REPORTED ticket claims have got, and how many are silent.
 *
 * `coverage` is the load-bearing half. A phase claim exists only where an agent
 * wrote one, so an average over the claims alone says nothing about the tickets
 * nobody has touched — and on this host most attached tickets are already
 * `done` while their workspaces are mid-flight, which is exactly how a single
 * percentage comes to read 96% over a fleet that is 60% through its work.
 * Publishing the denominator beside the figure is what keeps it honest; a
 * surface that prints the percentage alone is misreading this type.
 *
 * Progress is `index / (total - 1)` per claim, the same arithmetic
 * `ticketRollup` applies per workspace — this is the fleet-wide sibling, not a
 * second opinion about what a phase is worth.
 */
export interface FleetProgress {
  /** Mean progress across claims that exist, 0–1. Null when none do. */
  readonly fraction: number | null;
  /** Tickets carrying a claim. */
  readonly reported: number;
  /** Tickets attached across the fleet, claimed or not. */
  readonly tickets: number;
}

export const EMPTY_PROGRESS: FleetProgress = { fraction: null, reported: 0, tickets: 0 };

export function fleetProgress(snapshot: DashboardSnapshotView | undefined): FleetProgress {
  let tickets = 0;
  let reported = 0;
  let sum = 0;

  for (const project of snapshot?.projects ?? []) {
    for (const row of project.workspaces) {
      tickets += row.state.ticket_refs?.length ?? 0;
      // A per-ticket claim carries its INDEX and no total: the ramp's length is
      // the workspace's own `phase.total`, exactly as `ticketPhases` joins it.
      // Reading a `total` off the claim compiles against `any` and is silently
      // `undefined` — the typecheck caught that, which is why this is spelled
      // out rather than inlined.
      const steps = (row.phase?.total ?? 0) - 1;
      for (const claim of row.phase?.tickets ?? []) {
        reported += 1;
        // `index / (total - 1)`, so `handoff` is exactly 1 and `scope` exactly
        // 0 — the same arithmetic `ticketRollup` applies per workspace. A
        // one-phase vocabulary has no denominator and counts as complete.
        sum += steps > 0 ? Math.min(1, Math.max(0, claim.index / steps)) : 1;
      }
    }
  }
  return { fraction: reported > 0 ? sum / reported : null, reported, tickets };
}

/**
 * The whole fleet in ONE group of short figures — the band's answer to "how is
 * everything going", stated once.
 *
 * Three sections used to answer it three ways: `1 working 0 idle 1 blocked`,
 * then `22 tickets 82% of 11 reported`, then `4 need you` — and `blocked` and
 * `need you` overlap (attention folds blocked in), while the denominator was a
 * sentence in a band whose every other value is a figure. VS Code's status-bar
 * guidance is the rule applied here: *use short text labels*; global items
 * left. Each figure keeps ONE word so §4.7 holds (no count carries its meaning
 * in hue alone), and the coverage that keeps the percentage honest moves to the
 * tooltip — it must still be one hover away, but it is a qualification, not a
 * headline.
 *
 * Zero counts are DROPPED rather than greyed. A band is read at a glance, and
 * `0 idle` is a figure the eye has to reject; a fleet with nothing blocked
 * simply says nothing about blocking.
 */
export interface FleetFigure {
  readonly key: "working" | "idle" | "blocked" | "attention" | "tickets";
  readonly count: number;
  /** The one word beside the number. */
  readonly word: string;
  /** For `tickets` only: the mean progress percentage, or null when unclaimed. */
  readonly percent: number | null;
  /** The qualification a hover reveals. */
  readonly title: string;
}

export function fleetFigures(
  counts: FleetCounts,
  progress: FleetProgress,
  attention: number,
): FleetFigure[] {
  const figures: FleetFigure[] = [];
  if (counts.working > 0) {
    figures.push({ key: "working", count: counts.working, word: "working", percent: null, title: `${counts.working} agents working` });
  }
  if (counts.idle > 0) {
    figures.push({ key: "idle", count: counts.idle, word: "idle", percent: null, title: `${counts.idle} agents idle` });
  }
  if (counts.blocked > 0) {
    figures.push({ key: "blocked", count: counts.blocked, word: "blocked", percent: null, title: `${counts.blocked} agents blocked` });
  }
  // Attention is BROADER than blocked (waiting | blocked | error). Showing it
  // only when it says more than `blocked` already did removes the redundancy
  // the band was reported for without losing the case it exists for: an agent
  // waiting on a question is not blocked, and this is the only figure naming it.
  if (attention > counts.blocked) {
    figures.push({
      key: "attention",
      count: attention,
      word: "need you",
      percent: null,
      title: `${attention} workspaces waiting on a person (asking, blocked or errored)`,
    });
  }
  if (progress.tickets > 0) {
    const pct = progress.fraction === null ? null : Math.round(progress.fraction * 100);
    figures.push({
      key: "tickets",
      count: progress.tickets,
      word: progress.tickets === 1 ? "ticket" : "tickets",
      percent: pct,
      title:
        pct === null
          ? `${progress.tickets} tickets attached; none has reported a phase yet`
          : `${pct}% mean progress across the ${progress.reported} of ${progress.tickets} tickets that reported a phase`,
    });
  }
  return figures;
}

/**
 * How many workspaces are asking for a human, fleet-wide.
 *
 * The daemon's own `needs_attention` — `waiting | blocked | error` folded — so
 * this is deliberately BROADER than `FleetCounts.blocked`, which counts only a
 * literal `blocked`. The two are different questions and the band shows both:
 * one says what is stalled, the other says what wants you.
 */
export function fleetAttention(snapshot: DashboardSnapshotView | undefined): number {
  let count = 0;
  for (const project of snapshot?.projects ?? []) {
    for (const row of project.workspaces) {
      if (row.needs_attention) count += 1;
    }
  }
  return count;
}

/* ─────────────────────────── 3 · subscriptions ───────────────────────────── */

/** One account's compact summary: who, how used, and whether that is measured. */
export interface AccountSummary {
  readonly accountId: string;
  /**
   * The account's full identity — routinely an email address.
   *
   * Belongs in the popover, which is where a reader goes to tell two accounts
   * apart. It is NOT what the strip prints: the band is permanently visible,
   * including in every screenshare and screenshot, and an email is both the
   * longest string here and the one nobody chose to publish.
   */
  readonly label: string;
  /**
   * The short name for the STRIP — the plan, not the person.
   *
   * `Claude max 20x` says everything a headroom reading needs and stays the
   * same width whoever is signed in. Falls back to the full label only when a
   * provider publishes no plan, because a row with no name at all is worse
   * than a long one.
   */
  readonly shortLabel: string;
  readonly provider: BillingAccountView["provider"];
  /** The provider's own percentage for the window nearest its ceiling. */
  readonly percent: number | null;
  /** That window's own label (`5h`, `7d`), never a Grove-invented name. */
  readonly window: string | null;
  readonly status: BillingAccountView["status"];
  /** True when the reading is old enough that the daemon flagged it. */
  readonly stale: boolean;
}

/**
 * The window a compact summary should speak for: the one nearest its ceiling.
 *
 * Never a sum. Windows are independent readings of different spans — a session
 * window at 2% and a weekly at 68% are not parts of one budget — so adding them
 * produces a number nobody measured. The tightest is the one that stops you
 * first, which is the only question a one-line summary can answer.
 */
export function tightestWindow(
  windows: readonly SubscriptionWindowView[],
): SubscriptionWindowView | null {
  let best: SubscriptionWindowView | null = null;
  let bestPercent = -1;
  for (const window of windows) {
    const percent = usedPercent(window);
    if (percent === null) continue;
    if (percent > bestPercent) {
      best = window;
      bestPercent = percent;
    }
  }
  // Every window unmeasured: still name one, so the row says WHICH window it
  // could not measure rather than vanishing.
  return best ?? windows[0] ?? null;
}

/**
 * The plan's own words, for the strip: `Claude max 20x`, `Codex pro`.
 *
 * Composed from the provider and the tier the daemon already publishes, never
 * invented and never abbreviated — an account whose provider declared no plan
 * keeps its full label rather than being given a name Grove made up.
 */
export function accountShortLabel(account: BillingAccountView): string {
  const provider = PROVIDER_LABEL[account.provider] ?? account.provider;
  const tier = account.subscription;
  const plan = tier?.label ?? tier?.plan ?? null;
  if (plan === null) return account.label;
  return [provider, plan, tier?.detail].filter(Boolean).join(" ");
}

/** Each provider's own spelling, so the strip does not print a wire enum. */
const PROVIDER_LABEL: Partial<Record<BillingAccountView["provider"], string>> = {
  claude_code: "Claude",
  codex: "Codex",
  opencode: "OpenCode",
  mewbo: "Mewbo",
};

/**
 * The plan WITHOUT its provider word — for a row whose provider is already its
 * brand mark.
 *
 * `Claude max 20x` beside the Claude logo says "Claude" twice, and in a band
 * measured in characters that is the one word cheapest to drop: the mark is
 * the identity (§4.1, a fixed property of the account), so the text carries
 * only the tier. Falls back to the full label for a provider with no plan —
 * that row has no other name.
 */
export function accountTierLabel(account: AccountSummary): string {
  const provider = PROVIDER_LABEL[account.provider];
  if (provider && account.shortLabel.startsWith(`${provider} `)) {
    return account.shortLabel.slice(provider.length + 1);
  }
  return account.shortLabel;
}

/** Summarize one account for the strip. */
export function accountSummary(account: BillingAccountView): AccountSummary {
  const window = tightestWindow(account.windows);
  return {
    accountId: account.account_id,
    label: account.label,
    shortLabel: accountShortLabel(account),
    provider: account.provider,
    percent: window ? usedPercent(window) : null,
    window: window?.label ?? null,
    status: account.status,
    stale: account.status === "stale",
  };
}

/**
 * Every subscription account, FULLEST FIRST.
 *
 * The strip shows at most two of N, so the question it has to answer is which
 * two — and the useful answer is the ones closest to stopping you. Ordering by
 * the daemon's own list showed whichever two happened to be configured first,
 * so an exhausted account could sit behind `+2 accounts` while two idle ones
 * held the band.
 *
 * **This reverses the previous rule, deliberately, and the cost is real.** That
 * rule was stability: a strip that re-ranks as percentages move can swap which
 * accounts are visible while somebody is reading them. Two things make the
 * trade worth taking now. Quota percentages move on the order of minutes, not
 * frames, so a swap is rare rather than jittery; and each row now leads with
 * its provider's brand mark, so a reader re-finds an account by its logo rather
 * than by its position. Ranking by severity is the same rule `sessionSummary`
 * and `quotaSummary` already follow when they must choose one value to show.
 *
 * The comparison is each account's TIGHTEST window (`tightestWindow`), which is
 * what makes "5h versus 7d" a non-question: neither span outranks the other,
 * the reading nearest its own ceiling wins, because that is the one that stops
 * you first. Unmeasured accounts sort last — an unknown reading is not a low
 * one, and putting them first would bury a real 98%.
 *
 * Ties keep the daemon's order because `Array.prototype.sort` is stable, so two
 * accounts at the same percentage never trade places between polls.
 *
 * API-key accounts are excluded. They report spend rather than a subscription
 * window, and rendering money as a percentage would be a fabricated reading.
 */
export function accountSummaries(quotas: UsageQuotasView | undefined): AccountSummary[] {
  return (quotas?.accounts ?? [])
    .filter((account) => account.billing_mode === "subscription")
    .map(accountSummary)
    // `Array.prototype.sort` is STABLE (ES2019), so equal percentages keep the
    // daemon's order with no tiebreak — an index map-and-unmap around this was
    // written first and was dead code. Mutation caught it: deleting the
    // tiebreak changed no behaviour and failed no test, which is the tell.
    .sort((a, b) => (b.percent ?? -1) - (a.percent ?? -1));
}

/**
 * How close a quota is to stopping you, as a semantic tier.
 *
 * The ONE place this ramp's thresholds live — the strip, the popover and the
 * phone sheet all read it, so 50% cannot mean amber on one surface and calm on
 * another. Three bands rather than the context meter's four: a quota has no
 * "empty and fine" tier worth distinguishing from "plenty left", where a
 * context window does.
 *
 * `warning` is the theme's existing amber (hue 75), not a new token — it is
 * already the value every other at-risk reading in the app uses, and a second
 * yellow would be a second opinion about what at-risk looks like.
 */
export type QuotaTone = "success" | "warning" | "destructive";

export function quotaTone(percent: number | null): QuotaTone | null {
  if (percent === null) return null;
  if (percent >= 80) return "destructive";
  if (percent >= 50) return "warning";
  return "success";
}

/**
 * Whether a reading should pull the eye — amber and above.
 *
 * Separate from the tone because it answers a different question: the tone is
 * what a reading IS, this is whether it is worth interrupting someone over. A
 * green reading is information; an amber one is a thing to act on before it
 * becomes a red one.
 */
export function quotaUrgent(percent: number | null): boolean {
  const tone = quotaTone(percent);
  return tone === "warning" || tone === "destructive";
}

/**
 * A quota reading as the band prints it: a WHOLE percent.
 *
 * DISPLAY ONLY — every threshold still reads the raw number, because rounding
 * before comparing would let 99.6% print `100%` and claim a limit the provider
 * never reported.
 *
 * The decimal is gone because of what the figure is FOR. Nobody acts on the
 * difference between 23.6% and 24% of a weekly quota: the reading says how much
 * headroom is left, and a tenth of a percent is below the resolution of that
 * decision — while costing two characters in the band whose scarcest resource
 * is width, and putting a moving digit in a strip whose job is to stay still.
 * Providers mostly publish whole numbers already; a generic one published
 * `23.63`, which is where the decimal came from. Precision follows the act
 * (design system §3): the usage page is where a reader goes to compare
 * readings, and it keeps its own formatter.
 */
export function percentLabel(percent: number): string {
  return `${Math.round(percent)}`;
}

/** How many account summaries fit inline, before the overflow control. */
export interface AccountFit {
  readonly inline: AccountSummary[];
  readonly hidden: AccountSummary[];
  /** Every account, in one stable order — what the popover lists. */
  readonly all: AccountSummary[];
}

/**
 * At most `capacity` accounts inline, the rest reachable through overflow.
 *
 * `MAX_INLINE_ACCOUNTS` is a CEILING rather than a target: two accounts is the
 * most the strip will ever show even on a wide screen, because a status bar
 * that grows with an account list stops being a status bar. A narrow viewport
 * passes a smaller capacity and the remainder moves into the same control, so
 * there is one overflow mechanism rather than a wrapping mode.
 *
 * The popover lists EVERY account including the inline ones, so a reader who
 * opens it sees one complete list rather than a remainder they have to
 * mentally add to the strip.
 */
export const MAX_INLINE_ACCOUNTS = 2;

export function fitAccounts(
  accounts: readonly AccountSummary[],
  capacity: number = MAX_INLINE_ACCOUNTS,
): AccountFit {
  const inlineCount = Math.max(0, Math.min(capacity, MAX_INLINE_ACCOUNTS, accounts.length));
  // One account hidden behind a "+1" control costs a reader a click to see
  // what the strip had room for; show it instead.
  const shown = accounts.length - inlineCount === 1 && inlineCount < MAX_INLINE_ACCOUNTS
    ? inlineCount + 1
    : inlineCount;
  return {
    inline: accounts.slice(0, shown),
    hidden: accounts.slice(shown),
    all: [...accounts],
  };
}

/**
 * Whether any HIDDEN account is in a state a reader would act on.
 *
 * The overflow control must disclose this. Showing two healthy accounts while
 * an exhausted one sits behind a `+2` implies the whole set is fine, which is
 * the one way a summarized strip can actively mislead.
 */
export function hiddenNeedsAttention(hidden: readonly AccountSummary[]): boolean {
  return hidden.some(
    (account) =>
      account.status !== "ok" || (account.percent !== null && account.percent >= 100),
  );
}

/**
 * The ONE account fact a narrow band has room for.
 *
 * Same severity rule as `sessionSummary`, over the whole account list rather
 * than the two the wide strip happens to show — a phone shows no account
 * summaries at all, so this is the only thing that can report an exhausted
 * quota, and reporting the count alone would imply the set was fine.
 *
 * The thresholds are `quotaTone`'s, deliberately not re-derived: 100% is
 * exhausted and 80% is close, and two copies of that table is how one surface
 * comes to call 80% a warning while another still calls it fine.
 */
export type QuotaSummary = {
  readonly state: "exhausted" | "near" | "stale" | "count" | "empty";
  readonly count: number;
};

export function quotaSummary(accounts: readonly AccountSummary[]): QuotaSummary {
  const exhausted = accounts.filter((a) => a.percent !== null && a.percent >= 100).length;
  if (exhausted > 0) return { state: "exhausted", count: exhausted };
  const near = accounts.filter((a) => a.percent !== null && a.percent >= 80).length;
  if (near > 0) return { state: "near", count: near };
  const stale = accounts.filter((a) => a.stale).length;
  if (stale > 0) return { state: "stale", count: stale };
  if (accounts.length > 0) return { state: "count", count: accounts.length };
  return { state: "empty", count: 0 };
}

/* ─────────────────────────────── 4 · system ──────────────────────────────── */

export interface SystemFacts {
  readonly version: string;
  /** The daemon's start instant, for a clock the browser advances itself. */
  readonly startedAt: string | null;
  readonly updateAvailable: boolean;
  readonly latestVersion: string | null;
  /** The daemon's code on disk has moved past what it booted with. */
  readonly restartRequired: boolean;
}

export function systemFacts(identity: WhoamiView | undefined): SystemFacts | null {
  if (!identity) return null;
  return {
    version: identity.version,
    startedAt: identity.started_at ?? null,
    updateAvailable: Boolean(identity.update_available && identity.latest_version),
    latestVersion: identity.latest_version ?? null,
    // An older daemon omits the field, and "cannot tell" must not prompt.
    restartRequired: identity.restart_required === true,
  };
}
