import { FolderRoot } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { Placement } from "@/lib/grove/types";

/**
 * Placement tag. A workspace usually lives in its own git worktree (the
 * default, common case), so worktree placement renders nothing — silence is
 * the signal that there's nothing unusual to flag. Only `root` placement,
 * where the session runs in the repo root with no isolated worktree, earns a
 * visible badge so a glance distinguishes it.
 *
 * Neutral outline chrome (no status hue): placement is orthogonal to lifecycle
 * status, so it must not read as a status color. Composes shadcn Badge for the
 * shared pill geometry rather than hand-rolling a sibling pill.
 *
 * Test seam: `data-testid="placement-badge"` plus `data-placement` and the
 * visible "root" label. Returns null for worktree, so absence is assertable.
 */
export function PlacementBadge({
  placement,
  size = "md",
  className,
}: {
  placement: Placement;
  size?: "sm" | "md";
  className?: string;
}) {
  if (placement !== "root") return null;
  return (
    <Badge
      variant="outline"
      data-placement={placement}
      data-testid="placement-badge"
      title="Runs in the repo root — no isolated worktree"
      className={cn(
        "gap-1 rounded-full bg-muted/60 font-medium uppercase tracking-wide text-muted-foreground",
        size === "sm" ? "px-2 py-0 text-[10px]" : "text-[11px]",
        className,
      )}
    >
      <FolderRoot aria-hidden className="h-3 w-3 shrink-0" />
      <span>root</span>
    </Badge>
  );
}
