import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { WorkspaceStatus } from "@/lib/grove/types";
import { statusGlyph, statusLabel } from "@/lib/grove/status-tokens";

const STATUS_CSS_VAR: Record<WorkspaceStatus, string> = {
  active: "var(--status-active)",
  running: "var(--status-running)",
  idle: "var(--status-idle)",
  offline: "var(--status-offline)",
  paused: "var(--status-paused)",
  orphaned: "var(--status-orphaned)",
  error: "var(--status-error)",
};

/**
 * Status pill. Composes shadcn Badge so the rounded-full geometry,
 * border, and typography come from the design system rather than
 * being hand-rolled. The status hue lights only the leading glyph,
 * keeping label contrast at AA across both themes.
 *
 * Test seam: the badge element exposes `data-status` and `data-testid`,
 * its `style` attribute carries the status CSS var, and the first child
 * is the (potentially pulsing) glyph element.
 */
export function StatusBadge({
  status,
  size = "md",
  className,
}: {
  status: WorkspaceStatus;
  size?: "sm" | "md";
  className?: string;
}) {
  const isActive = status === "active" || status === "running";
  const color = STATUS_CSS_VAR[status];
  return (
    <Badge
      variant="outline"
      data-status={status}
      data-testid="status-badge"
      className={cn(
        "gap-1.5 rounded-full bg-muted/60 font-medium uppercase tracking-wide text-foreground",
        size === "sm" ? "px-2 py-0 text-[10px]" : "text-[11px]",
        className,
      )}
      style={{ ["--status-c" as string]: color }}
    >
      <span
        aria-hidden
        className={cn(
          "font-mono text-[var(--status-c)] leading-none",
          isActive && "animate-grove-pulse motion-reduce:animate-none",
        )}
      >
        {statusGlyph(status)}
      </span>
      <span>{statusLabel(status)}</span>
    </Badge>
  );
}
