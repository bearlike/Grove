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
  provisioning: "var(--status-provisioning)",
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
  // The glyph pulses for every status where something is HAPPENING right now —
  // an agent working, or a container being built. PROVISIONING is the whole
  // reason the pulse matters: a still glyph is what made the old OFFLINE read
  // as dead and got the build killed.
  const isActive =
    status === "active" || status === "running" || status === "provisioning";
  const color = STATUS_CSS_VAR[status];
  return (
    <Badge
      variant="outline"
      data-status={status}
      data-testid="status-badge"
      className={cn(
        "gap-1 rounded-full border-transparent bg-muted/40 font-medium uppercase tracking-[0.08em] text-muted-foreground",
        size === "sm" ? "px-1.5 py-0 text-[10px]" : "px-2 text-[10px]",
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
