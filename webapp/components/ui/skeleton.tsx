import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * The ONE loading placeholder (design-direction.md §5, brand.md §5) — a muted
 * base with a subtle terracotta shimmer sweep (~5% opacity), the single
 * sanctioned place terracotta *moves* across a whole surface because a skeleton
 * is transient by construction. The sweep is motion-safe only: under
 * `prefers-reduced-motion` the gradient image drops out entirely and it renders
 * as a static muted block (no pulse, no motion). Reused verbatim wherever data
 * loads (rail, transcript, peek, grid) so every loading state reads as one system.
 */
export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-md bg-muted bg-no-repeat",
        // The terracotta band + its animation exist only when motion is allowed;
        // reduced-motion users get the bare `bg-muted` block above.
        "motion-safe:animate-grove-shimmer motion-safe:bg-[length:200%_100%] motion-safe:bg-[linear-gradient(90deg,transparent,hsl(var(--primary)/0.06),transparent)]",
        className,
      )}
      {...props}
    />
  );
}
