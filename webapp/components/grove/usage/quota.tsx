"use client";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import type { BillingAccountView, SubscriptionTier, UsageQuotasView } from "@/lib/grove/api";
import { AgentMark } from "@/components/grove/agent-mark";
import { CardShell } from "@/components/grove/card";
import { RelativeTime } from "@/components/grove/relative-time";
import { cn } from "@/lib/utils";
import { SectionFailure, UsageSection } from "./section";
import { humanize, money } from "./format";
import { accountStatusTone } from "./tokens";
import { SUPPLEMENTARY_RULE, WindowMeters } from "./window-meter";

/**
 * One grid for all four states, so nothing below this row moves as the quota
 * probe resolves.
 *
 * **The second column starts at `lg`, not `md`, and that is a measurement.** At
 * a 768px viewport the rail is already expanded (it is a `Sheet` only below
 * `md`), so `md:grid-cols-2` left each card **226px** wide — narrow enough that
 * a meter's own "25% used · 75% left" truncated against "resets in 4d 22h". No
 * amount of truncation discipline rescues a container that small; the fix is to
 * stop creating it. At `lg` the same card is ~366px, which is the width this
 * card's rows were designed and measured against.
 */
const ACCOUNTS = "grid gap-4 lg:grid-cols-2 xl:grid-cols-3";

/**
 * Subscription capacity, one card per billing account.
 *
 * **Percentages are the primary representation, not the fallback.** Both real
 * providers report `used_percent`/`remaining_percent` and leave
 * `used`/`limit`/`unit` null — a subscription window has no unit to count. The
 * previous gate required all three, so every live window fell through to "not
 * measured" and the page reported no capacity at all while the daemon was
 * returning six perfectly good windows.
 *
 * **An account is not a provider.** Two Claude config roots are two accounts
 * even when they share every project; merging them is how a page reports
 * headroom while one plan is actually exhausted.
 */
export function UsageQuota({
  quotas,
  failed,
  onRetry,
  retrying,
}: {
  quotas: UsageQuotasView | undefined;
  failed: boolean;
  onRetry?: () => void;
  retrying?: boolean;
}): React.ReactNode {
  // Both nothing-states stay ON the grid and inside a card, for the reason the
  // headline row does: as bare paragraphs they were a single muted line where
  // two 208px cards had been, so the rest of the page jumped when the probe
  // answered — and the failure was indistinguishable from the perfectly normal
  // "no profile configured" beside it.
  if (failed) {
    return (
      <div className={ACCOUNTS}>
        <CardShell className="col-span-full p-4" data-testid="usage-quota-failed">
          <SectionFailure
            detail="Quota could not be read. The totals above are unaffected — quota collection fails on its own."
            onRetry={onRetry}
            retrying={retrying}
          />
        </CardShell>
      </div>
    );
  }

  if (!quotas) {
    return (
      <div className={ACCOUNTS} aria-hidden>
        {Array.from({ length: 2 }, (_, index) => (
          <Skeleton key={index} className="h-52 w-full" />
        ))}
      </div>
    );
  }

  if (quotas.accounts.length === 0) {
    return (
      <div className={ACCOUNTS}>
        <CardShell className="col-span-full p-4" data-testid="usage-quota-empty">
          <p className="text-sm text-content-tertiary">
            No subscription profile is configured, so no window was read.
          </p>
        </CardShell>
      </div>
    );
  }

  return (
    <section className={ACCOUNTS} aria-label="Subscription windows" data-testid="usage-quota">
      {quotas.accounts.map((account) => (
        <AccountCard key={account.account_id} account={account} />
      ))}
    </section>
  );
}

/**
 * One account, and the difference between "no usage known" and "this probe
 * failed".
 *
 * A failed refresh is NOT unknown usage. When the provider rate-limits the
 * probe, the windows read minutes ago are still the best answer anyone has, so
 * they stay on screen with the time they were observed, and the failure drops to
 * a secondary note under them. Blanking the card on a failed refresh throws away
 * a good reading and reports a healthy plan as unknowable.
 */
function AccountCard({ account }: { account: BillingAccountView }): React.ReactNode {
  const healthy = account.status === "ok";
  const windows = account.windows.length > 0;
  const tier = plan(account.subscription);
  const identity = `${humanize(account.provider)} · ${tier ?? humanize(account.billing_mode)}`;

  return (
    // The fleet's mark table, reused rather than re-derived: it already maps a
    // vendor string onto the brand marks, and `provider` (`claude_code`,
    // `codex`) hits the same patterns an agent name does. It rides the card's
    // own icon slot now — this used to hand-compose the mark and a truncating
    // span INTO the title, which is the shape the slot exists for.
    <UsageSection
      icon={<AgentMark agentName={account.provider} />}
      // A NODE, not a string: `SectionCard` truncates its title but sets no
      // `title` attribute, and a truncated profile name with no way back to the
      // full value is data loss (§8). The primitive takes a node precisely so a
      // card can carry its own identity affordance without forking it — the
      // outer span still owns the clipping, this one only owns the tooltip.
      title={<span title={account.label}>{account.label}</span>}
      description={
        // TWO SLOTS, not three clauses. This was `provider · plan · as of
        // <absolute timestamp>` concatenated into one string, which fitted until
        // a plan name pushed it from 310px to 367px in a 360px column and it
        // wrapped. The fix is not a shorter string — it is a row that cannot
        // overflow: the identity yields (`min-w-0 truncate`, full value in
        // `title`) and the age is `shrink-0` with a bounded spelling.
        <span className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-2">
          {/* The plan sits in the metadata line and NOT in a badge beside the
              status, because §6 reserves badge chrome for what can change: a
              subscription tier is a fixed property of the account, the same call
              the Info tab's runtime and placement chips lost.

              It REPLACES the billing mode rather than joining it: the two answer
              one question — how does this account pay — at two granularities,
              and `max 20x` entails `Subscription` while saying more. */}
          <span className="truncate" title={identity}>
            {identity}
          </span>
          {account.observed_at ? (
            // Relative, for the reason the forecast is: "read 3m ago" is
            // understood at a glance and has a bounded width, where an absolute
            // instant is arithmetic AND the longest token on the card. The exact
            // time stays in this element's own `title`.
            <RelativeTime iso={account.observed_at} className="shrink-0 whitespace-nowrap" />
          ) : (
            <span />
          )}
        </span>
      }
      action={
        <Badge variant={accountStatusTone(account.status)}>{humanize(account.status)}</Badge>
      }
      data-testid="usage-quota-card"
      data-account={account.account_id}
      data-status={account.status}
    >
      <div className="flex flex-col gap-3">
        {windows ? (
          <WindowMeters windows={account.windows} />
        ) : (
          // Metadata tier, not body prose: an absent window is a caption
          // about the reading, the same rank as "not measured" everywhere
          // else on this page, not a sentence competing with real data.
          <p className="text-xs text-content-tertiary">
            {healthy
              ? "This provider reports no window, so none is shown."
              : "No earlier reading survived this failure, so no window can be shown."}
          </p>
        )}

        {/* Spend and the refresh's own health are bookkeeping ABOUT the
            reading, not more of it — the same rank shift the tracks-to-
            insights rule marks inside `WindowMeters`, drawn with the SAME
            export rather than a second `border-t` invented here. Grouped
            under one rule instead of two, since a divider per paragraph
            would read as two facts rather than one footnote. */}
        {account.spend || account.detail ? (
          <div
            className={cn(SUPPLEMENTARY_RULE, "flex flex-col gap-1")}
            data-testid="usage-quota-footer"
          >
            {account.spend ? (
              <p className="text-xs text-content-tertiary">
                Spend {money(account.spend.amount, account.spend.currency)} ·{" "}
                {humanize(account.spend.provenance)}
              </p>
            ) : null}
            {/* Last, and quiet: the refresh's own health is a footnote to the
                numbers, not their headline. Above them it reads as though the
                plan were the thing that failed. */}
            {account.detail ? (
              <p className="text-xs text-content-tertiary">
                {windows ? "Last refresh: " : ""}
                {account.detail}
              </p>
            ) : null}
          </div>
        ) : null}
      </div>
    </UsageSection>
  );
}

/**
 * Which paid plan this account is on, or NOTHING AT ALL.
 *
 * The null branch is the whole point: an absent tier renders as no text, no
 * chip and no placeholder. This is the one field on the page a person
 * reconciles against their own bill, so a fabricated or guessed tier is worse
 * than a blank — and the daemon refuses to guess precisely so that the client
 * never has to. "Unknown plan" would be a claim Grove cannot make.
 *
 * Rendered VERBATIM, and deliberately not through `humanize`. `label` is
 * contracted as the provider's own wording, kept beside the slug exactly so a
 * client can show what the vendor calls this plan without expanding,
 * prettifying or translating a name Grove does not own — `prolite` is a real
 * plan nobody here had heard of, and the failure mode is mapping an unfamiliar
 * token onto the nearest familiar one.
 */
function plan(subscription: SubscriptionTier | null | undefined): string | null {
  // Either half can be absent on its own, so this is a fallback and not a
  // preference between two fields that always travel together.
  const name = subscription?.label ?? subscription?.plan;
  if (!name) return null;
  return subscription?.detail ? `${name} ${subscription.detail}` : name;
}
