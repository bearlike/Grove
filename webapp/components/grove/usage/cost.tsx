"use client";

import { CircleDollarSignIcon } from "lucide-react";

import type { UsageSummaryView } from "@/lib/grove/api";
import { UsageSection } from "./section";
import { humanize, money } from "./format";

/**
 * Spend for the indexed range — and, on most hosts, the reason there is none.
 *
 * The absence is stated ONCE, here, with its cause. Cost needs both a price
 * table and per-class token evidence; a store missing either reports
 * `cost_available: false`, and repeating "not measured" in every row of every
 * table below is how the old page turned one honest gap into a page-wide
 * shrug.
 */
export function UsageCost({
  summary,
  failed,
  onRetry,
  retrying,
  className,
}: {
  summary: UsageSummaryView | undefined;
  failed: boolean;
  onRetry?: () => void;
  retrying?: boolean;
  className?: string;
}): React.ReactNode {
  const coverage = summary?.coverage;
  const degraded = coverage?.sources.filter((source) => source.health !== "ok") ?? [];

  return (
    <UsageSection
      icon={<CircleDollarSignIcon />}
      title="Cost"
      description="Spend across every indexed source"
      failed={failed}
      onRetry={onRetry}
      retrying={retrying}
      failure="Cost could not be loaded."
      loading={!summary}
      className={className}
      data-testid="usage-cost"
    >
      {summary?.cost ? (
        <>
          <p className="text-2xl font-semibold text-content-primary">
            {money(summary.cost.amount, summary.cost.currency)}
          </p>
          <p className="text-xs text-content-tertiary">
            {humanize(summary.cost.provenance)}
          </p>
        </>
      ) : (
        <>
          {/* One size down and one tier down from the figure it stands in for,
              and never semibold. It used to render at exactly the size and
              weight of the largest real number on the page, which made the
              absence of data the loudest thing on the screen — the single most
              visible instance of that defect in the app. Weight is for things
              that are there. */}
          <p className="text-lg text-content-tertiary">not measured</p>
          <p className="text-xs text-content-secondary">
            Cost needs a price table and per-class token counts. This store has
            neither for the indexed range
            {degraded.length > 0
              ? `; ${degraded.map((source) => `${source.label} is ${source.health}`).join(", ")}`
              : ""}
            .
          </p>
        </>
      )}
    </UsageSection>
  );
}
