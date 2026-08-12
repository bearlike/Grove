"use client";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { NOT_MEASURED, abbreviate, exact } from "./format";

/**
 * A count, shortened, with the exact figure one hover away.
 *
 * The tooltip wiring lives HERE and nowhere else. Every number on this page is
 * a candidate for abbreviation, and a page that pairs `Tooltip` with a
 * formatter at thirty call sites is thirty chances to forget one — which reads
 * to the user as "some numbers are hoverable".
 *
 * Below a thousand the abbreviation IS the exact value, so no tooltip is
 * mounted: a tooltip that repeats its trigger is noise, and a `tabIndex` on it
 * costs a keyboard user a stop for nothing.
 *
 * THE ABSENCE BRANCH NEUTRALISES WEIGHT AND TIER, and deliberately does not
 * touch size. This is one of the two routes by which "not measured" ended up as
 * the loudest thing on the page: it inherits its caller, and its callers run
 * from a `text-2xl font-semibold` display figure down to an inline table cell,
 * so a null in a stat tile rendered at 20px/600 — bigger and bolder than any
 * real number beside it. Weight and tier are safe to fix here because an
 * absence is never bold and never primary at ANY size. The size step-down is
 * the caller's, because only the caller knows what size the missing value
 * would have been.
 */
export function AbbreviatedNumber({
  value,
  className,
}: {
  value: number | null | undefined;
  className?: string;
}): React.ReactNode {
  const short = abbreviate(value);

  if (short === NOT_MEASURED) {
    return (
      <span className={cn("font-normal text-content-tertiary", className)}>{NOT_MEASURED}</span>
    );
  }

  const full = exact(value);
  if (short === full) {
    return <span className={cn("tabular-nums", className)}>{short}</span>;
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span tabIndex={0} className={cn("tabular-nums", className)}>
          {short}
        </span>
      </TooltipTrigger>
      <TooltipContent>{full}</TooltipContent>
    </Tooltip>
  );
}
