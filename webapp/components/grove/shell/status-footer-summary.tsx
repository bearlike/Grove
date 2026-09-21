"use client";

import {
  ChevronRightIcon,
  CircleAlertIcon,
  CircleDashedIcon,
  CirclePlayIcon,
  FolderGit2Icon,
  FolderTreeIcon,
  GaugeIcon,
  GaugeCircleIcon,
  GitBranchIcon,
  OctagonAlertIcon,
  TagIcon,
  TimerIcon,
  WifiOffIcon,
  type LucideIcon,
} from "lucide-react";

import { AgentMark } from "@/components/grove/agent-mark";
import { QUOTA_TEXT_TONE } from "@/components/grove/shell/footer/quota-tone";
import { Seam } from "@/components/grove/shell/footer/primitives";
import {
  accountTierLabel,
  percentLabel,
  quotaTone,
  quotaUrgent,
  quotaSummary,
  sessionSummary,
  type AccountSummary,
  type FleetCounts,
  type FooterContext,
  type QuotaSummary,
  type SessionSummary,
  type SystemFacts,
} from "@/lib/grove/adapters/footer";
import { formatContextWindow } from "@/lib/grove/adapters/context";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

/**
 * The phone band: three icon-led values on ONE baseline, opening a detail sheet.
 *
 * A narrow viewport shows FEWER values, never the same values with their words
 * removed — which is what the first cut did, rendering `1 0 1` beside three
 * small glyphs because every `Count` label sits behind `lg:inline`. That made
 * hue the only carrier of which digit was which, the failure §4.7 names.
 *
 * So each slot keeps its word and the row keeps a single line: the summary is a
 * REDUCTION IN CONTENT, not in height or type size. The band's 44px coarse
 * minimum is untouched.
 */

/** One icon-led value. `title` is never the only carrier — the word is present. */
function SummaryValue({
  Icon,
  children,
  tone = "text-content-secondary",
  className = "",
  wrap = false,
}: {
  Icon: LucideIcon;
  children: React.ReactNode;
  tone?: string;
  className?: string;
  wrap?: boolean;
}): React.ReactNode {
  return (
    <span className={`inline-flex min-w-0 items-center gap-1 ${tone} ${className}`}>
      <Icon aria-hidden className="size-[1em] shrink-0 align-[-0.125em]" />
      <span className={wrap ? "min-w-0 whitespace-normal" : "min-w-0 truncate"}>{children}</span>
    </span>
  );
}

/** The sessions slot's glyph, tone and wording — severity already decided. */
const SESSION_PRESENTATION: Record<
  SessionSummary["state"],
  { Icon: LucideIcon; tone: string; word: string }
> = {
  // `destructive`, matching AGENT_TONE.blocked, exactly as the wide band does.
  blocked: { Icon: OctagonAlertIcon, tone: "text-destructive", word: "blocked" },
  working: { Icon: CirclePlayIcon, tone: "text-success", word: "working" },
  idle: { Icon: CircleDashedIcon, tone: "text-content-secondary", word: "idle" },
  empty: { Icon: CircleDashedIcon, tone: "text-content-tertiary", word: "sessions" },
};

/**
 * The quota slot's wording.
 *
 * `at limit` and `near limit` are words rather than percentages because the
 * phone shows no account summaries at all — this is the only thing that can
 * report an exhausted quota, and a bare count would imply the set was fine.
 */
const QUOTA_PRESENTATION: Record<
  QuotaSummary["state"],
  { tone: string; word: (n: number) => string }
> = {
  exhausted: { tone: "text-destructive", word: (n) => `${n} at limit` },
  near: { tone: "text-warning", word: (n) => `${n} near limit` },
  stale: { tone: "text-warning", word: (n) => `${n} stale` },
  count: {
    tone: "text-content-secondary",
    word: (n) => `${n} account${n === 1 ? "" : "s"}`,
  },
  empty: { tone: "text-content-tertiary", word: () => "no accounts" },
};

/** Every fact the wide band shows, plus the ones it drops. One scrolling column. */
function SummaryDetails({
  context,
  counts,
  accounts,
  system,
  connected,
}: {
  context: FooterContext;
  counts: FleetCounts;
  accounts: AccountSummary[];
  system: SystemFacts | null;
  connected: boolean;
}): React.ReactNode {
  const window = formatContextWindow(context.contextWindow?.used, context.contextWindow?.size);

  // `text-sm` ONCE, on the container: the sheet is read at arm's length
  // rather than at a glance, so it earns one step up from the band — and
  // every row inherits it rather than choosing its own.
  return (
    <div className="flex min-h-0 flex-col gap-3 overflow-y-auto px-4 pb-4 text-sm" data-testid="footer-summary-details">
      {context.project || context.branch || context.worktree ? (
        // Headed like every other section below it. Without the heading these
        // three sat above the first `Context` rule as an unlabelled preamble,
        // so the sheet opened on a group that was the only one not named.
        <section className="flex flex-col gap-1" data-testid="footer-summary-workspace">
          <h3 className="text-xs text-content-tertiary">Workspace</h3>
          {context.project ? (
            <SummaryValue Icon={FolderGit2Icon} tone="text-content-primary">
              {context.subpath ? `${context.project} › ${context.subpath}` : context.project}
            </SummaryValue>
          ) : null}
          {context.branch ? (
            <SummaryValue Icon={GitBranchIcon}>
              <span className="font-mono">{context.branch}</span>
            </SummaryValue>
          ) : null}
          {context.worktree ? (
            <SummaryValue Icon={FolderTreeIcon} tone="text-content-tertiary">
              <span className="font-mono">{context.worktree}</span>
            </SummaryValue>
          ) : null}
        </section>
      ) : null}

      {window ? (
        <section className="footer-rule flex flex-col gap-1 border-t pt-3" data-testid="footer-summary-context-window">
          <h3 className="text-xs text-content-tertiary">Context</h3>
          {context.rootAgent ? (
            <span className="inline-flex items-center gap-1.5 whitespace-normal break-words">
              <AgentMark agentName={context.rootAgent} className="size-[1em] shrink-0" />
              {context.rootAgent}
            </span>
          ) : null}
          <SummaryValue Icon={GaugeCircleIcon} tone="text-content-secondary" wrap>
            <span className="tabular-nums">{window.counts} · {window.percent}%</span>
          </SummaryValue>
          <span className="tabular-nums text-content-tertiary whitespace-normal">{window.exactCounts} tokens</span>
        </section>
      ) : context.contextUnavailable === "stale_native_worker" ? (
        // The sheet has room for the remedy itself, where the band has room
        // only to say a remedy exists.
        <section className="footer-rule flex flex-col gap-1 border-t pt-3" data-testid="footer-summary-context-window">
          <h3 className="text-xs text-content-tertiary">Context</h3>
          <SummaryValue Icon={GaugeCircleIcon} tone="text-warning" wrap>
            Not measured — this agent started before the context fix
          </SummaryValue>
          <span className="text-xs text-content-tertiary whitespace-normal">
            Respawn the workspace to start a worker that reports it.
          </span>
        </section>
      ) : null}

      <section className="footer-rule flex flex-col gap-1 border-t pt-3">
        <h3 className="text-xs text-content-tertiary">Sessions</h3>
        {connected ? (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <SummaryValue Icon={CirclePlayIcon} tone="text-success">
              <span className="tabular-nums">{counts.working}</span> working
            </SummaryValue>
            <SummaryValue Icon={CircleDashedIcon}>
              <span className="tabular-nums">{counts.idle}</span> idle
            </SummaryValue>
            <SummaryValue
              Icon={OctagonAlertIcon}
              tone={counts.blocked > 0 ? "text-destructive" : "text-content-tertiary"}
            >
              <span className="tabular-nums">{counts.blocked}</span> blocked
            </SummaryValue>
          </div>
        ) : (
          <SummaryValue Icon={WifiOffIcon} tone="text-warning">
            fleet unavailable
          </SummaryValue>
        )}
      </section>

      <section className="footer-rule flex flex-col gap-1 border-t pt-3">
        <h3 className="text-xs text-content-tertiary">Subscriptions</h3>
        {!connected ? (
          <SummaryValue Icon={CircleAlertIcon} tone="text-warning">
            quota not measured
          </SummaryValue>
        ) : accounts.length === 0 ? (
          <span className="text-content-tertiary">No subscription accounts</span>
        ) : (
          // MARK-ANCHORED, like the band. A border per row was the first
          // repair and it read as clutter: in a column the eye already walks
          // row by row, so the divider is the row rhythm and the anchor is the
          // provider's mark at the left edge. Everything on that line until
          // the next mark is that account.
          accounts.map((account) => (
            <div
              key={account.accountId}
              className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-2"
              data-testid="footer-summary-account"
            >
              {/* This opened sheet still names the plan, not its account holder:
                  context removes no screensharing risk from screen recording. */}
              <span className="inline-flex min-w-0 items-center gap-1.5">
                <AgentMark agentName={account.provider} className="size-[1em] shrink-0" />
                <span className="min-w-0 whitespace-normal">{accountTierLabel(account)}</span>
              </span>
              <span className="shrink-0 tabular-nums text-content-tertiary">
                {account.window ?? "window"}{" "}
                {account.percent === null ? (
                  "not measured"
                ) : (
                  <span
                    className={`${QUOTA_TEXT_TONE[quotaTone(account.percent)!]} ${quotaUrgent(account.percent) ? "quota-urgent" : ""}`}
                  >
                    {percentLabel(account.percent)}%
                  </span>
                )}
                {account.stale ? <span className="text-warning"> stale</span> : null}
              </span>
            </div>
          ))
        )}
      </section>

      {system !== null ? (
        <section className="footer-rule flex flex-wrap items-center gap-x-4 gap-y-1 border-t pt-3">
          <SummaryValue Icon={TagIcon}>
            <span className="tabular-nums">v{system.version}</span>
          </SummaryValue>
          {system.startedAt ? (
            <SummaryValue Icon={TimerIcon} tone="text-content-tertiary">
              <time dateTime={system.startedAt}>
                since {new Date(system.startedAt).toLocaleString()}
              </time>
            </SummaryValue>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

/**
 * The narrow band. One row, one control, one sheet.
 *
 * The whole row opens the sheet rather than each slot owning its own target:
 * three adjacent controls in a 44px band are three ways to miss, and every one
 * of them opens the same surface anyway.
 */
export function StatusFooterSummary({
  context,
  counts,
  accounts,
  system,
  connected,
}: {
  context: FooterContext;
  counts: FleetCounts;
  accounts: AccountSummary[];
  system: SystemFacts | null;
  connected: boolean;
}): React.ReactNode {
  const sessions = sessionSummary(counts);
  const quota = quotaSummary(accounts);
  const session = SESSION_PRESENTATION[sessions.state];
  const quotaLook = QUOTA_PRESENTATION[quota.state];

  return (
    <Sheet>
      {/* Not `SheetTrigger asChild` over a composed row: the trigger IS the row,
          and a bare button keeps one accessible name over the whole band. */}
      <SheetContent
        side="bottom"
        className="max-h-[70dvh] gap-0 pb-[env(safe-area-inset-bottom)]"
        data-testid="footer-summary-sheet"
      >
        <SheetHeader className="pb-2">
          <SheetTitle>Status</SheetTitle>
          <SheetDescription className="sr-only">
            Workspace, sessions, subscriptions and daemon facts
          </SheetDescription>
        </SheetHeader>
        <SummaryDetails
          context={context}
          counts={counts}
          accounts={accounts}
          system={system}
          connected={connected}
        />
      </SheetContent>

      {/* The whole band is one target, at the coarse-pointer minimum: three
          adjacent controls in a 44px row are three ways to miss, and each
          would open this same surface anyway. */}
      <SheetTrigger
        // `gap-0` with each slot padded: the seams need equal air on both
        // sides, which a container gap stacked on top of would break.
        className="flex h-full w-full min-w-0 items-center gap-0 px-3 text-left"
        aria-label="Show full status"
        data-testid="footer-summary-trigger"
      >
        {context.project ? (
          <SummaryValue Icon={FolderGit2Icon} className="shrink pr-2">
            {context.project}
          </SummaryValue>
        ) : null}

        {/* The phone row's three slots are three different subjects — where you
            are, what the fleet is doing, how much quota is left — and a gap
            alone left them reading as one sentence. */}
        {context.project ? <Seam /> : null}

        {connected ? (
          <SummaryValue Icon={session.Icon} tone={session.tone} className="shrink-0 px-2">
            {sessions.state === "empty" ? (
              "no sessions"
            ) : (
              <>
                <span className="tabular-nums">{sessions.count}</span> {session.word}
              </>
            )}
          </SummaryValue>
        ) : (
          <SummaryValue Icon={WifiOffIcon} tone="text-warning" className="shrink-0 px-2">
            offline
          </SummaryValue>
        )}

        {connected ? (
          <>
            <Seam />
            <SummaryValue Icon={GaugeIcon} tone={quotaLook.tone} className="shrink-0 px-2">
              {quotaLook.word(quota.count)}
            </SummaryValue>
          </>
        ) : null}

        <ChevronRightIcon
          aria-hidden
          className="ml-auto size-4 shrink-0 text-content-tertiary"
        />
      </SheetTrigger>
    </Sheet>
  );
}
