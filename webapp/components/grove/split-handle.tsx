"use client";

import type { ComponentProps, ReactNode } from "react";

import { ResizableHandle } from "@/components/ui/resizable";
import { cn } from "@/lib/utils";

/**
 * The one divider between two panes, everywhere the app splits.
 *
 * NO `withHandle`. That prop draws a 12x16 bordered block with a grip icon
 * parked in the middle of the divider: a hairline is what the divider should
 * LOOK like, and a grip is a 90s affordance for a control that already tells
 * you what it does by sitting between two panes.
 *
 * Thin to look at, easy to grab: the full-height `w-px` border stays, and
 * `after:w-3` widens the INVISIBLE hit area from 4px to 12px. The `::after`
 * belongs to the separator for hit-testing, which is also why plain
 * `hover:`/`active:` fire from anywhere in that band rather than only on the
 * 1px line. `hover`/`active`/`focus-visible` and not a library state
 * attribute: react-resizable-panels documents exactly `data-separator`,
 * `data-disabled`, `role` and ARIA on this element — there is no drag-state
 * hook to bind to, and CSS `:active` already holds for the whole pointer drag.
 */
export function SplitHandle({
  className,
  ...props
}: ComponentProps<typeof ResizableHandle>): ReactNode {
  return (
    <ResizableHandle
      className={cn(
        "h-full transition-colors after:w-3 hover:bg-ring focus-visible:bg-ring active:bg-ring",
        className,
      )}
      {...props}
    />
  );
}
