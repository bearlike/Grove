import { Children, Fragment, isValidElement } from "react";
import { cn } from "@/lib/utils";

/**
 * The middot-separated meta row ("pipes → middots").
 * THE one home for the quiet, inline `a · b · c` metadata texture used on cards,
 * the sidebar, the stat row, and the detail chrome — defined once so the
 * separator, spacing, and muted tone can never drift across surfaces.
 *
 * Auto-interleaves a `·` between each *rendered* child (falsy children — the
 * common `cond && <x/>` pattern — are dropped first, so a hidden slot never
 * leaves a dangling separator). Callers just list the spans; they can't forget
 * a divider or double one up.
 */
export function MetaRow({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  // Children.toArray already drops null/undefined/booleans; `.filter(Boolean)`
  // additionally removes empty strings so a hidden slot leaves no stray middot.
  const items = Children.toArray(children).filter(Boolean);
  return (
    <div
      className={cn(
        "flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs text-muted-foreground",
        className,
      )}
    >
      {items.map((child, i) => (
        <Fragment key={isValidElement(child) && child.key != null ? child.key : i}>
          {i > 0 && (
            <span aria-hidden className="select-none text-muted-foreground/40">
              ·
            </span>
          )}
          {child}
        </Fragment>
      ))}
    </div>
  );
}
