import { cn } from "@/lib/utils";

/**
 * A small filled status dot — the Devin "attention / unread" mark and the
 * sidebar/status-bar lifecycle indicator (attention is a dot or a thin bar,
 * never a full-card glow). Pure: `tone` is any CSS color
 * (typically a `var(--status-*)` / `var(--agent-*)` token or `var(--ref-info)`
 * for the blue attention dot). Decorative by default (`aria-hidden`); give it a
 * `title` + `role` upstream when it carries the only signal.
 */
export function Dot({
  tone,
  pulse = false,
  className,
}: {
  tone: string;
  pulse?: boolean;
  className?: string;
}) {
  return (
    <span
      aria-hidden
      data-testid="dot"
      className={cn(
        "inline-block size-1.5 shrink-0 rounded-full",
        pulse && "motion-safe:animate-pulse",
        className,
      )}
      style={{ backgroundColor: tone }}
    />
  );
}
