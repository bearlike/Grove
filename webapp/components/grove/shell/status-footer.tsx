"use client";

import { FleetSection } from "@/components/grove/shell/footer/fleet-section";
import { GitSection } from "@/components/grove/shell/footer/git-section";
import { SubscriptionsSection } from "@/components/grove/shell/footer/subscriptions-section";
import { SystemSection } from "@/components/grove/shell/footer/system-section";
import { WorkspaceSection } from "@/components/grove/shell/footer/workspace-section";
import { StatusFooterSummary } from "@/components/grove/shell/status-footer-summary";
import type {
  AccountSummary,
  FleetCounts,
  FleetProgress,
  FooterContext,
  SystemFacts,
} from "@/lib/grove/adapters/footer";

/**
 * The global, non-wrapping band for workspace, fleet, quota, and daemon facts.
 *
 * This file is COMPOSITION ONLY. Every section lives in `footer/` and is built
 * from the primitives in `footer/primitives.tsx`, which is where the band's
 * one size, its two divider tiers and its glyph geometry are decided. A
 * section cannot disagree with a sibling about any of those, because it never
 * makes the choice — that is what keeps ten groups consistent without ten
 * authors remembering the rules.
 */
export function StatusFooter({
  context,
  counts,
  accounts,
  system,
  connected,
  progress,
  attention,
}: {
  context: FooterContext;
  counts: FleetCounts;
  accounts: AccountSummary[];
  system: SystemFacts | null;
  connected: boolean;
  progress: FleetProgress;
  attention: number;
}): React.ReactNode {
  return (
    <footer
      // `.status-footer` carries the 24px band (44px on a coarse pointer) at
      // the theme boundary. NOT `h-6`: that is 1.5rem, which renders 19.19px
      // at this app's 80% density root — measured on the deployed page.
      // `text-xs` HERE and nowhere below it: one size for the whole band.
      className="status-footer footer-rule flex shrink-0 min-w-0 items-center overflow-hidden border-t bg-surface-sunken text-xs text-content-secondary whitespace-nowrap"
      data-testid="status-footer"
    >
      {/* TWO COMPOSITIONS, ONE BAND (#815). Narrow viewports cannot fit the
          sections without dropping every word — measured on a 390px phone,
          the sessions counts rendered as `1 0 1`, leaving hue as their only
          carrier. The narrow band shows FEWER values rather than smaller ones,
          and the rest moves into a sheet. Both are rendered and one is hidden,
          because the breakpoint is the viewport's own question and CSS is what
          answers it without a resize listener or a hydration mismatch.

          THE SWITCH IS `lg`, NOT `md`. Cutting over at `md` left 768-1023px
          rendering the sections with their words removed — measured on a
          deployed 820px tablet. A layout may only be shown where it is
          legible, so the two breakpoints are one breakpoint. */}
      <div className="flex h-full min-w-0 flex-1 items-center lg:hidden">
        <StatusFooterSummary
          context={context}
          counts={counts}
          accounts={accounts}
          system={system}
          connected={connected}
        />
      </div>

      {/* PRIMARY LEFT, CONTEXTUAL RIGHT, SLACK BETWEEN (#816). The workspace's
          own facts lead, the fleet and the quota follow, and the system group
          pins right with `ml-auto` putting the surplus BETWEEN them. Stretching
          every section to share the surplus was the other candidate and is
          worse: a value would re-centre inside its own section whenever a
          neighbouring number changed width, in a band whose whole job is to
          stay still and be aimed at. */}
      <div className="hidden h-full min-w-0 flex-1 items-center lg:flex">
        <WorkspaceSection context={context} />
        <GitSection git={context.git} />
        <FleetSection counts={counts} progress={progress} attention={attention} connected={connected} />
        <SubscriptionsSection accounts={accounts} connected={connected} />
        <div className="ml-auto flex h-full shrink-0 items-center">
          <SystemSection system={system} />
        </div>
      </div>
    </footer>
  );
}
