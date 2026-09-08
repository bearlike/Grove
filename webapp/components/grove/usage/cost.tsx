"use client";

import { CircleDollarSignIcon } from "lucide-react";

import type { MoneyView, UsageSummaryView } from "@/lib/grove/api";
import { UsageSection } from "./section";
import { humanize, money } from "./format";

/** Renders a row price honestly: a measured money value or an explicit unknown. */
export function UsageCostFigure({
  cost,
}: {
  cost: MoneyView | null | undefined;
}): React.ReactNode {
  if (!cost) return <span className="text-content-tertiary">unknown</span>;
  return (
    <span className="tabular-nums">
      {money(cost.amount, cost.currency)}
      <span className="ms-1 text-xs text-content-tertiary">{humanize(cost.provenance)}</span>
    </span>
  );
}

/** Spend for the indexed range, including a priced subtotal where coverage is partial. */
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
  const completeCost = summary?.cost;
  const breakdown = summary?.cost_breakdown;
  const knownCost = completeCost ? null : breakdown?.known_cost;
  const totalSessions = breakdown?.total_sessions ?? summary?.sessions ?? 0;

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
      {completeCost ? (
        <>
          <p className="text-2xl font-semibold text-content-primary">
            {money(completeCost.amount, completeCost.currency)}
          </p>
          <p className="text-xs text-content-tertiary">
            Complete estimate · {humanize(completeCost.provenance)}
          </p>
        </>
      ) : knownCost ? (
        <>
          <p className="text-2xl font-semibold text-content-primary">
            {money(knownCost.amount, knownCost.currency)}
          </p>
          <p className="text-xs text-content-tertiary">
            Known partial · {humanize(knownCost.provenance)} · {breakdown?.priced_sessions ?? 0} of {totalSessions} sessions priced
          </p>
        </>
      ) : totalSessions === 0 ? (
        <>
          <p className="text-lg text-content-tertiary">no sessions</p>
          <p className="text-xs text-content-secondary">No sessions match this selection.</p>
        </>
      ) : (
        <>
          <p className="text-lg text-content-tertiary">unavailable</p>
          <p className="text-xs text-content-secondary">Cost is unavailable for this selection.</p>
        </>
      )}
    </UsageSection>
  );
}
