"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * The composer's bordered `rounded-full` pill trigger geometry — the shared
 * chrome behind the Agent ▾ / Model ▾ menus (and any future toolbar pill). One
 * home so the border, height, focus ring, and quiet hover never drift across the
 * pickers. Deliberately NOT the `--primary` Button: in the composer view, the
 * send button is the single high-contrast affordance; every selector is quiet
 * outline chrome. Forwards its ref so radix's `DropdownMenuTrigger asChild` can
 * own it.
 */
export const PillTrigger = React.forwardRef<
  HTMLButtonElement,
  React.ButtonHTMLAttributes<HTMLButtonElement>
>(({ className, type = "button", ...props }, ref) => (
  <button
    ref={ref}
    type={type}
    className={cn(
      "inline-flex h-7 max-w-48 items-center gap-1.5 rounded-full border border-border bg-card px-3 text-xs font-medium text-foreground",
      "transition-colors duration-200 hover:bg-accent hover:text-accent-foreground",
      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
      "disabled:pointer-events-none disabled:opacity-50",
      className,
    )}
    {...props}
  />
));
PillTrigger.displayName = "PillTrigger";
