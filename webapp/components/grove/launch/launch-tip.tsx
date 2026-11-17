"use client";

import type { ReactNode } from "react";

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

/**
 * A hover tooltip around a control that must keep its own `data-slot`.
 *
 * `TooltipTrigger asChild` merges its slot over the child's, which would hide
 * the picker trigger and send button from the composer theme's slot rules, so
 * the trigger is a wrapper span instead.
 */
export function LaunchTip({
  label,
  suppressed = false,
  children,
}: {
  readonly label: string;
  /** Withhold the tip while the control's own menu is open, or the two stack. */
  readonly suppressed?: boolean;
  readonly children: ReactNode;
}): ReactNode {
  return (
    <TooltipProvider delayDuration={0}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex min-w-0">{children}</span>
        </TooltipTrigger>
        {suppressed ? null : <TooltipContent side="top">{label}</TooltipContent>}
      </Tooltip>
    </TooltipProvider>
  );
}
