"use client";

import type { ReactNode } from "react";

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

/**
 * A native tooltip for a composer control that is not a `TooltipIconButton`.
 *
 * The trigger is a wrapper SPAN, never the control: `TooltipTrigger asChild`
 * merges its own `data-slot` over its child's, which would rename the
 * attach button and the model and effort triggers out of the slots the theme
 * boundary keys on. Hover and focus both bubble from the control to the span,
 * so the tip still opens from the keyboard.
 *
 * `suppressed` withholds the content while the control's own menu is open, so
 * a popover and its tooltip never stack on one trigger.
 */
export function ControlTip({
  tip,
  suppressed = false,
  children,
}: {
  readonly tip: string;
  readonly suppressed?: boolean;
  readonly children: ReactNode;
}): ReactNode {
  return (
    <TooltipProvider delayDuration={0}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex min-w-0 items-center">{children}</span>
        </TooltipTrigger>
        {suppressed ? null : <TooltipContent side="top">{tip}</TooltipContent>}
      </Tooltip>
    </TooltipProvider>
  );
}
