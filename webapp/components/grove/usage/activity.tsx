"use client";

import { useLayoutEffect, useRef } from "react";
import { ChartColumnIcon } from "lucide-react";

import type { UsageActivityView } from "@/lib/grove/api";
import { ActivityHeatmap } from "./activity-heatmap";
import { AbbreviatedNumber } from "./abbreviated-number";
import { UsageSection } from "./section";

/**
 * Tokens per day — a rolling year of density, in one glance.
 *
 * A calendar answers "when was this thing busy" deterministically, which is the
 * question here; a time-series chart trades that for a magnitude curve nobody
 * asked for. The green ramp comes from `./activity-heatmap`, a declared port of
 * the vendored heat-graph — see its header for the two deltas and why a prop
 * could not carry them.
 *
 * The calendar derives its own range from the points it is handed, which is why
 * the 365-day window is applied to the QUERY (see `usage-audit`) and not here.
 *
 * This series is MEASURED even where `summary.tokens` is null — the daily
 * rollup carries counts the whole-store aggregate does not, so this card, not
 * the headline row, is where the page says how many tokens were spent.
 */
export function UsageActivity({
  activity,
  failed,
  onRetry,
  retrying,
  className,
}: {
  activity: UsageActivityView | undefined;
  failed: boolean;
  onRetry?: () => void;
  retrying?: boolean;
  className?: string;
}): React.ReactNode {
  const points =
    activity?.buckets.map((bucket) => ({ date: bucket.day, count: bucket.value })) ?? [];
  const measured = activity !== undefined && points.length > 0 && activity.total > 0;
  const scrollRef = useRef<HTMLDivElement>(null);
  const initialScroll = useRef(false);

  // A calendar runs oldest-to-newest, but the current work is the question this
  // card opens to answer. The point count changes when its initial data lands,
  // after the scroll box itself first mounts. Do this once: a later refetch must
  // not take control back from someone reading older activity.
  useLayoutEffect(() => {
    const scroll = scrollRef.current;
    if (scroll && !initialScroll.current) {
      scroll.scrollLeft = scroll.scrollWidth;
      initialScroll.current = true;
    }
  }, [points.length]);

  return (
    <UsageSection
      icon={<ChartColumnIcon />}
      title="Token activity"
      description="Daily tokens, last 365 days"
      failed={failed}
      onRetry={onRetry}
      retrying={retrying}
      failure="Token activity could not be loaded."
      loading={!activity}
      className={className}
      // The figure rides in the header's action slot rather than stacking above
      // the calendar: `CardAction` spans both header rows, so the total shares
      // height with the title instead of adding a row of its own.
      action={
        measured ? (
          // NEUTRAL, not `text-success`. A token total is a quantity, not an
          // outcome — nothing about a large number here is good or bad, and a
          // state colour gated on nothing is an identity hue pretending. It
          // also failed the redundancy rule outright: a green figure with no
          // word beside it carries its meaning in hue alone.
          <p className="text-2xl font-semibold text-content-primary">
            <AbbreviatedNumber value={activity.total} />
            <span className="ms-1.5 text-sm font-normal text-content-tertiary">tokens</span>
          </p>
        ) : null
      }
      data-testid="usage-activity"
    >
      {measured ? (
        /*
          A dense grid of solid cells reads as touching its container at any
          padding a sentence of text would call generous, so this gets its own
          gutter beyond the card body's ambient `p-3` — `min-w-0` again on the
          OUTER wrapper for the same reason as the scroller: it too is a flex
          child of `CardContent` and would otherwise refuse to shrink below its
          content, which is exactly the bug the inner `min-w-0` exists to avoid
          one layer down.

          The padding lives OUTSIDE the scroll boundary, never on it — a
          trailing edge of padding placed ON an `overflow-x-auto` element is
          the classic case a browser is inconsistent about clipping once the
          content scrolls, where a wrapper that never scrolls has no such
          question to answer.

          A 365-day calendar has an intrinsic size, so it gets a scroll boundary
          rather than a licence to stretch its card in either axis.

          `min-w-0` is the load-bearing class horizontally: a flex/grid child
          defaults to `min-width: auto` and refuses to shrink below its content,
          which is precisely how the calendar pushed its own card wider than the
          page.

          The calendar FILLS the card and the height is bounded anyway, because
          the two are controlled in different places: this layer owns the width
          (fill what the row gives us, scroll below the legible minimum), while
          the cell's own maximum size — delta 4 in `./activity-heatmap` — is
          what stops seven `aspect-square` rows growing tall on a wide monitor.
          Fixing the WIDTH here instead would bound the height just as well and
          did, but it left a third of the card empty at 1920.

          `min-w-[56rem]` is the floor, not the size: below it the calendar
          scrolls horizontally like GitHub's rather than shrinking to
          illegible dots, so the range stays a true 365 days at every width and
          every cell keeps its hover tooltip.
        */
        <div className="min-w-0 p-3">
          <div ref={scrollRef} className="min-w-0 overflow-x-auto">
            <div className="min-w-[56rem]">
              <ActivityHeatmap data={points} />
            </div>
          </div>
        </div>
      ) : (
        <p className="text-sm text-content-tertiary">
          Not measured: no indexed source reported token counts for this range.
        </p>
      )}
    </UsageSection>
  );
}
